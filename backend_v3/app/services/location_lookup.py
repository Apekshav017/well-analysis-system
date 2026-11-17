"""
services/location_lookup.py

Which village, taluka and district a point lies in, using the village boundaries in the database.
Boundaries are read once and kept for a few minutes, so a detection run does not re-read them for every well.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass

from shapely.geometry import Point, shape
from shapely.prepared import prep

from app.db.models.district import District
from app.db.models.taluka import Taluka
from app.db.models.village import Village

logger = logging.getLogger(__name__)
CACHE_SECONDS = 300
_lock = threading.Lock()
_cache: dict = {"at": None, "areas": []}  # None: never loaded yet (monotonic time can be small after a boot)


@dataclass(frozen=True)
class Area:
    village: str
    taluka: str
    district: str
    bbox: tuple
    geom: object  # prepared shapely geometry


def to_shape(value):
    """GeoJSON text or dict (Feature, FeatureCollection or geometry) -> shapely Polygon / MultiPolygon, or None."""
    try:
        obj = json.loads(value) if isinstance(value, str) else value
        if not isinstance(obj, dict):
            return None
        if obj.get("type") == "FeatureCollection":
            obj = obj["features"][0]
        if obj.get("type") == "Feature":
            obj = obj["geometry"]
        if obj.get("type") not in ("Polygon", "MultiPolygon"):
            return None
        geom = shape(obj)
        return None if geom.is_empty else geom
    except Exception:  # any malformed boundary is treated as "no boundary"
        return None


def _load(db) -> list[Area]:
    rows = (
        db.query(Village.name, Taluka.name, District.name, Village.geom_geojson)
        .join(Taluka, Village.taluka_id == Taluka.id)
        .join(District, Village.district_id == District.id)
        .filter(Village.geom_geojson.isnot(None))
        .all()
    )
    areas = []
    for village, taluka, district, text in rows:
        geom = to_shape(text)
        if geom is None:
            logger.warning("Village %s has an unreadable boundary and is skipped", village)
            continue
        areas.append(Area(village, taluka, district, geom.bounds, prep(geom)))
    return areas


def get_areas(db, force: bool = False) -> list[Area]:
    with _lock:
        if force or _cache["at"] is None or time.monotonic() - _cache["at"] > CACHE_SECONDS:
            _cache["areas"] = _load(db)
            _cache["at"] = time.monotonic()
        return _cache["areas"]


def locate(areas: list[Area], lon: float, lat: float) -> tuple[str | None, str | None, str | None]:
    """(village, taluka, district) of the boundary that contains the point, or (None, None, None)."""
    point = Point(lon, lat)
    for area in areas:
        minx, miny, maxx, maxy = area.bbox
        if minx <= lon <= maxx and miny <= lat <= maxy and area.geom.contains(point):
            return area.village, area.taluka, area.district
    return None, None, None
