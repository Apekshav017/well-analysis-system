"""
schemas/known_well.py

Reference wells (officer surveys and administrator-marked wells) as a GeoJSON FeatureCollection,
and the body used to add one.
"""

from typing import Any

from pydantic import BaseModel, Field


class KnownWellProperties(BaseModel):
    id: int
    well_identifier: str | None = None
    source: str
    district: str | None = None
    taluka: str | None = None
    village: str | None = None
    status: str | None = None
    well_type: str | None = None
    notes: str | None = None
    created_by: int | None = None


class KnownWellFeature(BaseModel):
    type: str = "Feature"
    geometry: dict[str, Any] = Field(description='{"type": "Point", "coordinates": [longitude, latitude]}')
    properties: KnownWellProperties


class KnownWellCollection(BaseModel):
    type: str = "FeatureCollection"
    count: int = Field(ge=0)
    features: list[KnownWellFeature]


class KnownWellCreate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    well_type: str | None = Field(default="Open well", max_length=50)
    village: str | None = Field(default=None, max_length=255, description="Empty: found from the boundaries.")
    taluka: str | None = Field(default=None, max_length=255)
    district: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=1000)
