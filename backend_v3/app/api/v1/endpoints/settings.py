"""
api/v1/endpoints/settings.py

System settings. Everyone reads them; only accounts with settings.manage can change them.
"""

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.permissions import require
from app.db.models.user import User
from app.db.session import get_db
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("")
def read_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return settings_service.get_all(db)


@router.put("/{key}")
def write_setting(key: str, value: dict = Body(..., embed=True), db: Session = Depends(get_db),
                  user: User = Depends(require("settings.manage"))):
    return {key: settings_service.set_value(db, key, value, user.id)}
