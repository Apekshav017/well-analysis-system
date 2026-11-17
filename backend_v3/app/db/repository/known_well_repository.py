"""
db/repository/known_well_repository.py

Reference wells: officer surveys, and wells the administrator marked by hand (source MANUAL_SOURCE).
PostGIS gives longitude (ST_X) and latitude (ST_Y) directly.
"""

import uuid

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.db.models.known_wells import KnownWell

MANUAL_SOURCE = "admin-manual"
BBox = tuple[float, float, float, float]  # minLon, minLat, maxLon, maxLat


def list_points(db: Session, village: str | None = None, bbox: BBox | None = None, limit: int = 5000) -> list[dict]:
    stmt = select(
        KnownWell.id,
        KnownWell.well_identifier,
        KnownWell.source,
        KnownWell.district,
        KnownWell.taluka,
        KnownWell.village,
        KnownWell.status,
        KnownWell.well_type,
        KnownWell.notes,
        KnownWell.created_by,
        func.ST_X(KnownWell.geom).label("lon"),
        func.ST_Y(KnownWell.geom).label("lat"),
    )
    if village:
        stmt = stmt.where(func.lower(KnownWell.village) == village.strip().lower())
    if bbox:
        minx, miny, maxx, maxy = bbox
        envelope = func.ST_MakeEnvelope(minx, miny, maxx, maxy, 4326)
        stmt = stmt.where(func.ST_Intersects(KnownWell.geom, envelope))
    stmt = stmt.order_by(KnownWell.id).limit(limit)
    return [dict(row._mapping) for row in db.execute(stmt)]


def insert_manual(db: Session, *, lon: float, lat: float, well_type: str | None, village: str | None,
                  taluka: str | None, district: str | None, notes: str | None, user_id: int) -> int:
    stmt = (
        insert(KnownWell.__table__)
        .values(
            well_identifier=f"MAN-{uuid.uuid4().hex[:10].upper()}",
            source=MANUAL_SOURCE,
            source_reference="Marked by administrator",
            geom=func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326),
            well_type=well_type or "Open well",
            status="Verified",
            district=district,
            taluka=taluka,
            village=village,
            notes=notes,
            created_by=user_id,
        )
        .returning(KnownWell.__table__.c.id)
    )
    new_id = db.execute(stmt).scalar_one()
    db.commit()
    return int(new_id)


def source_of(db: Session, well_id: int) -> str | None:
    return db.execute(select(KnownWell.source).where(KnownWell.id == well_id)).scalar()


def delete_by_id(db: Session, well_id: int) -> None:
    db.execute(delete(KnownWell).where(KnownWell.id == well_id))
    db.commit()
