"""
services/settings_service.py

System settings the administrator controls. Everyone can read them; only accounts with the
settings.manage permission can change them. Missing keys fall back to DEFAULTS.
"""

from app.core.exceptions import ValidationException
from app.db.repository import settings_repository

DEFAULTS = {
    "confidence_thresholds": {"high": 0.75, "medium": 0.50},
    "detection_defaults": {"confidence": 0.25, "tile_size": 640, "normalize": "none"},
    "learning": {"enabled": True, "min_confidence": 0.70, "auto_train_samples": 300},
}
TILE_SIZES = (256, 384, 512, 640, 768, 1024)
STATUS_KEY = "learning_status"  # written by the learning service only


def _clean(key: str, value: dict) -> dict:
    try:
        if key == "confidence_thresholds":
            high, medium = float(value["high"]), float(value["medium"])
            if not 0.0 < medium < high <= 1.0:
                raise ValueError
            return {"high": round(high, 3), "medium": round(medium, 3)}
        if key == "detection_defaults":
            conf, tile, norm = float(value["confidence"]), int(value["tile_size"]), str(value["normalize"])
            if not (0.01 <= conf <= 0.99 and tile in TILE_SIZES and norm in ("none", "stretch")):
                raise ValueError
            return {"confidence": round(conf, 3), "tile_size": tile, "normalize": norm}
        if key == "learning":
            enabled, minc = bool(value["enabled"]), float(value["min_confidence"])
            auto = int(value["auto_train_samples"])
            if not (0.05 <= minc <= 0.99 and auto >= 0):
                raise ValueError
            return {"enabled": enabled, "min_confidence": round(minc, 3), "auto_train_samples": auto}
    except (KeyError, TypeError, ValueError):
        raise ValidationException("The setting value is not valid. Check the numbers and choices.")
    raise ValidationException("Unknown setting.")


def get_all(db) -> dict:
    stored = settings_repository.get_all(db)
    out = {}
    for key, default in DEFAULTS.items():
        value = stored.get(key)
        out[key] = {**default, **value} if isinstance(value, dict) else dict(default)
    return out


def get_value(db, key: str) -> dict:
    return get_all(db)[key]


def set_value(db, key: str, value: dict, user_id: int) -> dict:
    if key not in DEFAULTS:
        raise ValidationException("Unknown setting.")
    clean = _clean(key, value)
    settings_repository.upsert(db, key, clean, user_id)
    return {**DEFAULTS[key], **clean}


def get_status(db) -> dict | None:
    return settings_repository.get_all(db).get(STATUS_KEY)


def set_status(db, status: dict) -> None:
    settings_repository.upsert(db, STATUS_KEY, status, None)
