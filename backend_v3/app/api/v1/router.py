"""
api/v1/router.py

Aggregates every v1 endpoint module into one APIRouter. Each module is added explicitly here.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    auth,
    gee,
    health,
    known_wells,
    learning,
    locations,
    models,
    settings,
    statistics,
    upload,
    well,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(settings.router)
api_v1_router.include_router(learning.router)
api_v1_router.include_router(locations.router)
api_v1_router.include_router(upload.router)
api_v1_router.include_router(statistics.router)
api_v1_router.include_router(models.router)
api_v1_router.include_router(gee.router)
api_v1_router.include_router(well.router, prefix="/wells")
api_v1_router.include_router(known_wells.router)
