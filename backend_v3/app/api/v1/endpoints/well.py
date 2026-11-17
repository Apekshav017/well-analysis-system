"""
api/v1/endpoints/well.py

Detection runs and detected wells. Mounted at /api/v1/wells.

  POST   /wells/detect                start a run (village name, an existing image, or an upload) -> 202
  GET    /wells/jobs                  runs: personal (default) or universal (scope=all)
  GET    /wells/jobs/{job_id}         one run: status, progress, counts
  POST   /wells/jobs/{job_id}/abort   stop a running run (its owner, or an administrator)
  DELETE /wells/jobs/{job_id}         delete a finished run and its wells (its owner, or an administrator)
  GET    /wells/export                wells as CSV, no row limit
  GET    /wells                       wells: personal (default) or universal, optional confidence floor
  DELETE /wells/{well_id}             delete one well (its owner, or an administrator)

WHO IMPORTS THIS FILE: api/v1/router.py
"""

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.permissions import can, require
from app.core.exceptions import AuthorizationException, ResourceNotFoundException, ValidationException
from app.db.models.user import User
from app.db.repository import detection_repository
from app.db.session import get_db
from app.gis.bbox import parse_bbox
from app.schemas.detection import DetectionJobOut, DetectionRequest
from app.schemas.well import WellOut
from app.services import detection_job_service, well_export_service
from app.services.detection_job_service import DetectionInputError

router = APIRouter(tags=["Wells"])
ADVANCED_FIELDS = ("confidence", "tile_size", "normalize")
SCOPE_PATTERN = "^(mine|all)$"


def _job_out(job, owner: str | None) -> DetectionJobOut:
    out = DetectionJobOut.model_validate(job)
    out.owner_name = owner
    params = dict(out.params or {})
    out.area_label = params.get("area_label") or ", ".join(
        part for part in (job.village, job.taluka, job.district) if part) or None
    params.pop("boundary", None)  # the outline is large; run lists do not need it
    out.params = params or None
    return out


def _scope_user(user: User, scope: str) -> int | None:
    """Personal views show the account's own runs; universal views (scope=all) show everyone's."""
    return user.id if scope == "mine" else None


def _job_or_404(db: Session, job_id: str):
    job = detection_repository.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return job


def _may_change(user: User, owner_id: int | None, any_permission: str) -> None:
    if owner_id != user.id and not can(user, any_permission):
        raise AuthorizationException("You can only change your own detection runs.")


@router.post("/detect", response_model=DetectionJobOut, status_code=status.HTTP_202_ACCEPTED)
def start_detection(body: DetectionRequest, background: BackgroundTasks, db: Session = Depends(get_db),
                    user: User = Depends(require("detect.own"))):
    if any(getattr(body, field) is not None for field in ADVANCED_FIELDS) and not can(user, "detect.advanced"):
        raise AuthorizationException("Only administrators can change the confidence, tile size or colour stretch.")
    if not body.use_master and not body.image_id:
        raise ValidationException("Choose an image, upload one, or detect by village name.")
    if not detection_job_service.try_claim():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Another detection or a learning round is running. Try again in a moment.")
    try:
        job = detection_job_service.create_job(db, user_id=user.id, body=body)
    except DetectionInputError as exc:
        detection_job_service.release_claim()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception:
        detection_job_service.release_claim()
        raise
    background.add_task(detection_job_service.run_job, job.id)
    return _job_out(job, user.full_name or user.email)


@router.get("/jobs", response_model=list[DetectionJobOut])
def list_jobs(limit: int = Query(20, ge=1, le=200), scope: str = Query("mine", pattern=SCOPE_PATTERN),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = detection_repository.list_jobs(db, limit=limit, user_id=_scope_user(user, scope))
    return [_job_out(job, name) for job, name in rows]


@router.get("/jobs/{job_id}", response_model=DetectionJobOut)
def get_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    job = _job_or_404(db, job_id)
    return _job_out(job, detection_repository.owner_name(db, job.user_id))


@router.post("/jobs/{job_id}/abort", response_model=DetectionJobOut)
def abort_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(require("jobs.abort.own"))):
    job = _job_or_404(db, job_id)
    _may_change(user, job.user_id, "jobs.abort.any")
    if job.status == "pending":
        detection_job_service.request_abort(job.id)
        detection_repository.update_job(db, job, status="cancelled",
                                        error_message=detection_job_service.STOPPED_MESSAGE,
                                        completed_at=datetime.now(timezone.utc))
    elif job.status == "running":
        detection_job_service.request_abort(job.id)  # the scan stops at its next tile
    else:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This detection has already finished.")
    db.refresh(job)
    return _job_out(job, detection_repository.owner_name(db, job.user_id))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(require("results.delete.own"))):
    job = _job_or_404(db, job_id)
    _may_change(user, job.user_id, "results.delete.any")
    if job.status in ("pending", "running"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Stop this detection before deleting its results.")
    removed = detection_repository.count_wells(db, job.id)
    detection_repository.delete_job(db, job)
    return {"message": "Results deleted.", "job_id": job_id, "wells_removed": removed}


@router.get("/export")
def export_wells_csv(
    job_id: str | None = Query(None, max_length=36),
    district_id: int | None = Query(None, gt=0),
    taluka_id: int | None = Query(None, gt=0),
    village_id: int | None = Query(None, gt=0),
    scope: str = Query("mine", pattern=SCOPE_PATTERN),
    min_confidence: float | None = Query(None, ge=0, le=1),
    user: User = Depends(require("export")),
):
    """Download wells as CSV. Choose a run or an area: an export of everything is refused."""
    if job_id is None and district_id is None and taluka_id is None and village_id is None:
        raise ValidationException("Choose a job, district, taluka or village to export.")
    rows = well_export_service.stream_wells_csv(
        job_id=job_id, district_id=district_id, taluka_id=taluka_id, village_id=village_id,
        user_id=_scope_user(user, scope), min_confidence=min_confidence,
    )
    return StreamingResponse(rows, media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="wells.csv"'})


@router.get("", response_model=list[WellOut])
def list_wells(
    job_id: str | None = Query(None, max_length=36),
    district_id: int | None = Query(None, gt=0),
    taluka_id: int | None = Query(None, gt=0),
    village_id: int | None = Query(None, gt=0),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    min_confidence: float | None = Query(None, ge=0, le=1),
    scope: str = Query("mine", pattern=SCOPE_PATTERN),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return detection_repository.list_wells(
        db, job_id=job_id, bbox=parse_bbox(bbox), limit=limit, district_id=district_id,
        taluka_id=taluka_id, village_id=village_id, user_id=_scope_user(user, scope),
        min_confidence=min_confidence,
    )


@router.delete("/{well_id}")
def delete_well(well_id: int, db: Session = Depends(get_db), user: User = Depends(require("results.delete.own"))):
    owner = detection_repository.well_owner(db, well_id)
    if owner is None:
        raise ResourceNotFoundException("Well not found.")
    _may_change(user, owner[1], "results.delete.any")
    detection_repository.delete_well(db, well_id)
    return {"message": "Well deleted.", "id": well_id}
