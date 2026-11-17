"""
db/repository/settings_repository.py

Data access for the app_settings table.
"""

from sqlalchemy.orm import Session

from app.db.models.app_setting import AppSetting


def get_all(db: Session) -> dict:
    return {row.key: row.value for row in db.query(AppSetting).all()}


def upsert(db: Session, key: str, value: dict, user_id: int | None) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=user_id))
    else:
        row.value = value  # assign a new object so the change is saved
        row.updated_by = user_id
    db.commit()
