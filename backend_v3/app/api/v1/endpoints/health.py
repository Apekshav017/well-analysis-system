"""
api/v1/endpoints/health.py

GET /api/v1/health: app status, database status (503 if the database is unreachable) and the API version.
The frontend reads the version and warns when an older backend is running.
"""

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

API_VERSION = "3"
logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except SQLAlchemyError:
        logger.exception("Health check: database unreachable")
        db_status = "unavailable"

    healthy = db_status == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "app": settings.APP_NAME,
            "env": settings.ENV,
            "database": db_status,
            "version": API_VERSION,
        },
    )
