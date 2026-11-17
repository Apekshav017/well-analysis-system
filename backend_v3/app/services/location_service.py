"""
services/location_service.py

Location hierarchy queries for the cascading dropdowns and the village search.
"""

import logging

from sqlalchemy.orm import Session

from app.db.repository.location_repository import LocationRepository
from app.schemas.location import DistrictResponse, StateResponse, TalukaResponse, VillageResponse, VillageSearchItem

logger = logging.getLogger(__name__)


class LocationService:
    """Location hierarchy business logic."""

    @staticmethod
    def get_states(db: Session) -> list[StateResponse]:
        return [StateResponse.model_validate(s) for s in LocationRepository.get_all_states(db)]

    @staticmethod
    def get_districts(db: Session, state_id: int) -> list[DistrictResponse]:
        return [DistrictResponse.model_validate(d) for d in LocationRepository.get_districts_by_state(db, state_id)]

    @staticmethod
    def get_talukas(db: Session, district_id: int) -> list[TalukaResponse]:
        return [TalukaResponse.model_validate(t) for t in LocationRepository.get_talukas_by_district(db, district_id)]

    @staticmethod
    def get_villages(db: Session, taluka_id: int) -> list[VillageResponse]:
        return [VillageResponse.model_validate(v) for v in LocationRepository.get_villages_by_taluka(db, taluka_id)]

    @staticmethod
    def get_village_details(db: Session, village_id: int) -> VillageResponse | None:
        village = LocationRepository.get_village_by_id(db, village_id)
        return VillageResponse.model_validate(village) if village else None

    @staticmethod
    def search_villages(db: Session, text: str, limit: int = 15) -> list[VillageSearchItem]:
        return [VillageSearchItem(**row) for row in LocationRepository.search_villages(db, text, limit)]
