"""
db/repository/location_repository.py

Data access layer for the location hierarchy (State -> District -> Taluka -> Village).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.district import District
from app.db.models.state import State
from app.db.models.taluka import Taluka
from app.db.models.village import Village


class LocationRepository:
    """Encapsulates location queries."""

    @staticmethod
    def get_all_states(db: Session) -> list[State]:
        return db.query(State).order_by(State.name).all()

    @staticmethod
    def get_districts_by_state(db: Session, state_id: int) -> list[District]:
        return db.query(District).filter(District.state_id == state_id).order_by(District.name).all()

    @staticmethod
    def get_talukas_by_district(db: Session, district_id: int) -> list[Taluka]:
        return db.query(Taluka).filter(Taluka.district_id == district_id).order_by(Taluka.name).all()

    @staticmethod
    def get_villages_by_taluka(db: Session, taluka_id: int) -> list[Village]:
        return db.query(Village).filter(Village.taluka_id == taluka_id).order_by(Village.name).all()

    @staticmethod
    def get_village_by_id(db: Session, village_id: int) -> Village | None:
        return db.query(Village).filter(Village.id == village_id).first()

    @staticmethod
    def search_villages(db: Session, text: str, limit: int = 15) -> list[dict]:
        """Villages whose name contains the text, with their taluka and district."""
        pattern = f"%{text.strip()}%"
        stmt = (
            select(
                Village.id,
                Village.name,
                Taluka.id.label("taluka_id"),
                Taluka.name.label("taluka"),
                District.id.label("district_id"),
                District.name.label("district"),
                Village.geom_geojson.isnot(None).label("has_boundary"),
            )
            .select_from(Village)
            .join(Taluka, Village.taluka_id == Taluka.id)
            .join(District, Village.district_id == District.id)
            .where(Village.name.ilike(pattern))
            .order_by(Village.name)
            .limit(limit)
        )
        return [dict(row._mapping) for row in db.execute(stmt)]
