"""
db/repository/detection_repository.py

Every read and write for detection runs and detected wells. Personal views pass a user_id;
universal views pass None. Only completed runs show their wells.
"""

from collections.abc import Iterator

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.orm import Session

from app.db.models.detected_wells import DetectedWell
from app.db.models.detection_job import DetectionJob
from app.db.models.user import User

INSERT_BATCH_SIZE = 1000
STALE_MESSAGE = "Interrupted: the server restarted before this run finished."


def create_job(db: Session, job: DetectionJob) -> DetectionJob:
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> DetectionJob | None:
    return db.get(DetectionJob, job_id)


def owner_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return (user.full_name or user.email) if user else None


def list_jobs(db: Session, limit: int = 20, user_id: int | None = None) -> list[tuple[DetectionJob, str | None]]:
    stmt = (
        select(DetectionJob, func.coalesce(User.full_name, User.email))
        .outerjoin(User, User.id == DetectionJob.user_id)
        .order_by(DetectionJob.created_at.desc())
        .limit(limit)
    )
    if user_id is not None:
        stmt = stmt.where(DetectionJob.user_id == user_id)
    return [(job, name) for job, name in db.execute(stmt).all()]


def update_job(db: Session, job: DetectionJob, **fields) -> None:
    for name, value in fields.items():
        setattr(job, name, value)
    db.commit()


def fail_stale_jobs(db: Session) -> int:
    """Runs left pending or running by an earlier server process can never finish."""
    result = db.execute(
        update(DetectionJob)
        .where(DetectionJob.status.in_(["pending", "running"]))
        .values(status="failed", error_message=STALE_MESSAGE, completed_at=func.now())
    )
    db.commit()
    return result.rowcount or 0


def count_wells(db: Session, job_id: str) -> int:
    return int(db.execute(select(func.count(DetectedWell.id)).where(DetectedWell.job_id == job_id)).scalar() or 0)


def delete_job(db: Session, job: DetectionJob) -> None:
    """The database removes the run's wells as well (ON DELETE CASCADE)."""
    db.delete(job)
    db.commit()


def insert_wells(db: Session, job_id: str, wells: list[dict]) -> int:
    """Adds the wells of one run. Does not commit: the caller decides when."""
    for start in range(0, len(wells), INSERT_BATCH_SIZE):
        chunk = wells[start:start + INSERT_BATCH_SIZE]
        db.execute(
            insert(DetectedWell.__table__).values([
                {
                    "job_id": job_id,
                    # ST_MakePoint takes (longitude, latitude). The order matters.
                    "geom": func.ST_SetSRID(func.ST_MakePoint(w["longitude"], w["latitude"]), 4326),
                    "confidence": w["confidence"],
                    "class_name": w["class_name"],
                    "px_x1": w["px_x1"], "px_y1": w["px_y1"], "px_x2": w["px_x2"], "px_y2": w["px_y2"],
                    "village": w.get("village"), "taluka": w.get("taluka"), "district": w.get("district"),
                }
                for w in chunk
            ])
        )
    return len(wells)


def well_owner(db: Session, well_id: int) -> tuple[str, int | None] | None:
    row = db.execute(
        select(DetectedWell.job_id, DetectionJob.user_id)
        .join(DetectionJob, DetectionJob.id == DetectedWell.job_id)
        .where(DetectedWell.id == well_id)
    ).first()
    return (row.job_id, row.user_id) if row else None


def delete_well(db: Session, well_id: int) -> bool:
    result = db.execute(delete(DetectedWell).where(DetectedWell.id == well_id))
    db.commit()
    return (result.rowcount or 0) > 0


def _wells_statement(*, job_id=None, bbox=None, district_id=None, taluka_id=None, village_id=None,
                     user_id=None, min_confidence=None):
    """The one query behind every well listing: wells from COMPLETED runs only."""
    stmt = (
        select(
            DetectedWell.id,
            DetectedWell.job_id,
            func.ST_Y(DetectedWell.geom).label("latitude"),
            func.ST_X(DetectedWell.geom).label("longitude"),
            DetectedWell.confidence,
            DetectedWell.class_name,
            DetectedWell.px_x1, DetectedWell.px_y1, DetectedWell.px_x2, DetectedWell.px_y2,
            func.coalesce(DetectedWell.village, DetectionJob.village).label("village"),
            func.coalesce(DetectedWell.taluka, DetectionJob.taluka).label("taluka"),
            func.coalesce(DetectedWell.district, DetectionJob.district).label("district"),
            DetectionJob.completed_at.label("detected_on"),
            DetectionJob.user_id,
            func.coalesce(User.full_name, User.email).label("owner_name"),
        )
        .select_from(DetectedWell)
        .join(DetectionJob, DetectionJob.id == DetectedWell.job_id)
        .outerjoin(User, User.id == DetectionJob.user_id)
        .where(DetectionJob.status == "completed")
    )
    if job_id:
        stmt = stmt.where(DetectedWell.job_id == job_id)
    if user_id is not None:
        stmt = stmt.where(DetectionJob.user_id == user_id)
    if district_id is not None:
        stmt = stmt.where(DetectionJob.district_id == district_id)
    if taluka_id is not None:
        stmt = stmt.where(DetectionJob.taluka_id == taluka_id)
    if village_id is not None:
        stmt = stmt.where(DetectionJob.village_id == village_id)
    if min_confidence is not None:
        stmt = stmt.where(DetectedWell.confidence >= min_confidence)
    if bbox:
        min_lon, min_lat, max_lon, max_lat = bbox
        envelope = func.ST_MakeEnvelope(min_lon, min_lat, max_lon, max_lat, 4326)
        stmt = stmt.where(func.ST_Intersects(DetectedWell.geom, envelope))
    return stmt


def list_wells(db: Session, job_id: str | None = None, bbox: tuple | None = None, limit: int = 5000,
               district_id: int | None = None, taluka_id: int | None = None, village_id: int | None = None,
               user_id: int | None = None, min_confidence: float | None = None) -> list[dict]:
    stmt = _wells_statement(
        job_id=job_id, bbox=bbox, district_id=district_id, taluka_id=taluka_id,
        village_id=village_id, user_id=user_id, min_confidence=min_confidence,
    ).order_by(DetectedWell.id).limit(limit)
    return [dict(row._mapping) for row in db.execute(stmt)]


def iter_wells(db: Session, *, job_id: str | None = None, district_id: int | None = None,
               taluka_id: int | None = None, village_id: int | None = None, user_id: int | None = None,
               min_confidence: float | None = None, batch: int = 2000) -> Iterator[dict]:
    """The same wells with no row limit, read in batches so memory stays flat (used by the CSV export)."""
    stmt = _wells_statement(
        job_id=job_id, district_id=district_id, taluka_id=taluka_id, village_id=village_id,
        user_id=user_id, min_confidence=min_confidence,
    ).order_by(DetectedWell.id).execution_options(yield_per=batch)
    for row in db.execute(stmt):
        yield dict(row._mapping)
