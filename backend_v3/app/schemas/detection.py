"""
schemas/detection.py

Request and reply shapes for detection runs.
An area is a GeoJSON boundary: a searched village or place, a drawn area, or a located circle.
An empty setting means "the system default" (set by the administrator).

WHO IMPORTS THIS FILE: api/v1/endpoints/well.py, services/detection_job_service.py
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DetectionRequest(BaseModel):
    image_id: str | None = Field(default=None, max_length=255, description="An image already on the server.")
    use_master: bool = Field(default=False, description="Use the master image (no upload).")
    boundary: dict | None = Field(default=None, description="GeoJSON Polygon or MultiPolygon: the area to scan.")
    area_label: str | None = Field(default=None, max_length=255, description="Name of the area, shown in run lists.")
    district_id: int | None = Field(default=None, gt=0)
    taluka_id: int | None = Field(default=None, gt=0)
    village_id: int | None = Field(default=None, gt=0)
    tile_size: int | None = Field(default=None, ge=256, le=1024)      # administrators only
    normalize: Literal["none", "stretch"] | None = None               # administrators only
    confidence: float | None = Field(default=None, ge=0.01, le=0.99)  # administrators only


class DetectionJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    image_id: str
    user_id: int | None = None
    owner_name: str | None = None
    area_label: str | None = None
    model_version: str | None = None
    district: str | None = None
    taluka: str | None = None
    village: str | None = None
    params: dict | None = None
    tiles_total: int | None = None
    tiles_done: int = 0
    total_detections: int = 0
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
