"""
api/v1/endpoints/locations.py

Location hierarchy for the cascading dropdowns, village search by name, and point lookup.
Village search is at /search/villages, so it can never be read as /villages/{taluka_id}.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.location import (
    DistrictResponse,
    LocateResult,
    StateResponse,
    TalukaResponse,
    VillageResponse,
    VillageSearchItem,
)
from app.services import location_lookup
from app.services.location_service import LocationService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/locations", tags=["Locations"])


@router.get("/states", response_model=list[StateResponse])
def get_states(db: Session = Depends(get_db)):
    """Fetch all states."""
    try:
        return LocationService.get_states(db)
    except Exception as e:
        logger.error("Error fetching states: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/districts/{state_id}", response_model=list[DistrictResponse])
def get_districts(state_id: int, db: Session = Depends(get_db)):
    """Fetch districts in a state."""
    try:
        return LocationService.get_districts(db, state_id)
    except Exception as e:
        logger.error("Error fetching districts: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search/villages", response_model=list[VillageSearchItem])
def search_villages(q: str = Query(..., min_length=2, max_length=80),
                    limit: int = Query(15, ge=1, le=50), db: Session = Depends(get_db)):
    """Villages whose name contains the text. has_boundary tells whether detection by village name works."""
    try:
        return LocationService.search_villages(db, q, limit)
    except Exception as e:
        logger.error("Error searching villages: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/talukas/{district_id}", response_model=list[TalukaResponse])
def get_talukas(district_id: int, db: Session = Depends(get_db)):
    """Fetch talukas in a district."""
    try:
        return LocationService.get_talukas(db, district_id)
    except Exception as e:
        logger.error("Error fetching talukas: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/villages/{taluka_id}", response_model=list[VillageResponse])
def get_villages(taluka_id: int, db: Session = Depends(get_db)):
    """Fetch villages in a taluka."""
    try:
        return LocationService.get_villages(db, taluka_id)
    except Exception as e:
        logger.error("Error fetching villages: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/village/{village_id}", response_model=VillageResponse)
def get_village_details(village_id: int, db: Session = Depends(get_db)):
    """Fetch one village with its boundary (geom_geojson)."""
    try:
        village = LocationService.get_village_details(db, village_id)
        if not village:
            raise HTTPException(status_code=404, detail="Village not found")
        return village
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching village: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/locate", response_model=LocateResult)
def locate_point(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                 db: Session = Depends(get_db)):
    """Which village, taluka and district a point lies in, from the stored village boundaries."""
    village, taluka, district = location_lookup.locate(location_lookup.get_areas(db), lon, lat)
    return LocateResult(found=village is not None, village=village, taluka=taluka, district=district)
