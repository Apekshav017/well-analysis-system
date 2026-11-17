"""
api/v1/endpoints/known_wells.py

Reference wells as GeoJSON (any signed-in user). Administrators can add wells the detection missed,
and remove the wells they added. Officer-survey wells cannot be removed here.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.permissions import require
from app.core.exceptions import AuthorizationException, ResourceNotFoundException, ValidationException
from app.db.models.user import User
from app.db.repository import known_well_repository
from app.db.repository.known_well_repository import MANUAL_SOURCE
from app.db.session import get_db
from app.schemas.known_well import KnownWellCollection, KnownWellCreate
from app.services import location_lookup

router = APIRouter(prefix="/known-wells", tags=["Known Wells"])


def _parse_bbox(raw: str | None):
    """'minLon,minLat,maxLon,maxLat' -> four floats, or None when not given."""
    if raw is None:
        return None
    try:
        minx, miny, maxx, maxy = (float(v) for v in raw.split(","))
    except ValueError as e:
        raise ValidationException("bbox must be four numbers: minLon,minLat,maxLon,maxLat") from e
    if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
        raise ValidationException("bbox is out of range, or the minimum is above the maximum")
    return minx, miny, maxx, maxy


@router.get("", response_model=KnownWellCollection)
def list_known_wells(
    village: str | None = Query(None, max_length=255),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = known_well_repository.list_points(db, village=village, bbox=_parse_bbox(bbox), limit=limit)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "id": r["id"],
                "well_identifier": r["well_identifier"],
                "source": r["source"],
                "district": r["district"],
                "taluka": r["taluka"],
                "village": r["village"],
                "status": r["status"],
                "well_type": r["well_type"],
                "notes": r["notes"],
                "created_by": r["created_by"],
            },
        }
        for r in rows
    ]
    return {"type": "FeatureCollection", "count": len(features), "features": features}


@router.post("", status_code=status.HTTP_201_CREATED)
def add_known_well(body: KnownWellCreate, db: Session = Depends(get_db),
                   user: User = Depends(require("wells.manual"))):
    village = (body.village or "").strip() or None
    taluka = (body.taluka or "").strip() or None
    district = (body.district or "").strip() or None
    if not (village and taluka and district):
        found_village, found_taluka, found_district = location_lookup.locate(
            location_lookup.get_areas(db), body.longitude, body.latitude)
        village, taluka, district = village or found_village, taluka or found_taluka, district or found_district
    new_id = known_well_repository.insert_manual(
        db, lon=body.longitude, lat=body.latitude, well_type=body.well_type, village=village,
        taluka=taluka, district=district, notes=(body.notes or "").strip() or None, user_id=user.id,
    )
    return {"id": new_id, "message": "Well saved.", "village": village, "taluka": taluka, "district": district}


@router.delete("/{well_id}")
def remove_known_well(well_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require("wells.manual"))):
    source = known_well_repository.source_of(db, well_id)
    if source is None:
        raise ResourceNotFoundException("Well not found.")
    if source != MANUAL_SOURCE:
        raise AuthorizationException("Only wells added by an administrator can be removed here.")
    known_well_repository.delete_by_id(db, well_id)
    return {"message": "Well removed.", "id": well_id}
