"""
services/detection_job_service.py

One detection run, from the request to the saved wells.
  1. create_job() checks the imagery and the area, and saves a 'pending' run.
     The area is a boundary: a searched village or place, a drawn or located area, or a village whose
     boundary is stored in the database. It is scanned inside the master image (no upload needed), or
     inside an image already on the server. Only the window of the image that covers the boundary is read.
  2. run_job() runs in the background: it tiles the window, runs the detector, removes duplicate boxes,
     converts them to longitude / latitude, keeps the wells inside the boundary, names the village,
     taluka and district each well lies in, and saves them.
  3. request_abort() asks a running scan to stop. It stops at the next tile and nothing is saved.
Only one run at a time: the detector and the GPU are shared.

WHO IMPORTS THIS FILE: api/v1/endpoints/well.py
"""

import logging
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import rasterio
from rasterio.crs import CRS
from rasterio.errors import WindowError
from rasterio.transform import xy
from rasterio.warp import transform as transform_points
from rasterio.warp import transform_bounds
from rasterio.windows import Window, from_bounds
from shapely import affinity
from shapely.geometry import Point, mapping
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.detection_job import DetectionJob
from app.db.models.district import District
from app.db.models.satellite_image import SatelliteImage
from app.db.models.taluka import Taluka
from app.db.models.village import Village
from app.db.repository import detection_repository
from app.db.session import SessionLocal
from app.ml.bridge import load_model, predict_tile
from app.services import imagery_paths, learning_service, location_lookup, run_lock
from app.services.imagery_paths import MASTER_FILE_ID
from app.services.settings_service import get_value
from app.services.tiled_detection import DetectionCancelled, detect_raster, read_info

logger = logging.getLogger(__name__)
settings = get_settings()

MODEL_IMGSZ = 640  # the size the detector was trained at; the tile size is separate
FAILED_MESSAGE = "Detection failed. The details are in the server log."
STOPPED_MESSAGE = "Stopped by the user. Nothing was saved for this run."
MAX_BOUNDARY_POINTS = 50_000  # a larger outline is too detailed to use
_abort_requests: set[str] = set()


class DetectionInputError(Exception):
    """The file or the area cannot be used. The message is safe to show the user."""


def try_claim() -> bool:
    return run_lock.try_claim()


def release_claim() -> None:
    run_lock.release_claim()


def request_abort(job_id: str) -> None:
    _abort_requests.add(job_id)


def _abort_requested(job_id: str) -> bool:
    return job_id in _abort_requests


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _picture_path(image: SatelliteImage) -> Path:
    try:
        path = imagery_paths.path_of(image.storage_uri, image.stored_filename)
    except ValueError as exc:
        raise DetectionInputError(str(exc)) from exc
    if not path.is_file():
        raise DetectionInputError("The file is missing from the server storage.")
    return path


def master_image(db: Session) -> SatelliteImage:
    """The master mosaic as a stored image record. Created the first time it is needed."""
    path = imagery_paths.master_path()
    if not path.is_file():
        raise DetectionInputError("The master image is not on the server. Place it at the path set in MASTER_IMAGE_PATH.")
    row = db.get(SatelliteImage, MASTER_FILE_ID)
    if row is None:
        with rasterio.open(path) as src:
            info = read_info(src)
        row = SatelliteImage(
            file_id=MASTER_FILE_ID, original_filename=path.name, stored_filename=path.name,
            file_size_bytes=path.stat().st_size, detected_format="GeoTIFF",
            image_width=info["width"], image_height=info["height"], band_count=info["bands"],
            crs=info["crs"], spatial_resolution_m=None, bounds=None,
            image_metadata={"dtype": info["dtype"], "master": True}, storage_uri=str(path),
            status="stored", created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    elif row.storage_uri != str(path):
        row.storage_uri = str(path)
        db.commit()
    return row


def _count_points(coords) -> int:
    """Number of coordinate pairs in a nested GeoJSON coordinate list."""
    if not isinstance(coords, (list, tuple)):
        return 0
    if coords and isinstance(coords[0], (int, float)):
        return 1
    return sum(_count_points(part) for part in coords)


def area_km2(geom: BaseGeometry) -> float:
    """Approximate area in square kilometres (degrees scaled at the boundary's mid latitude)."""
    _, miny, _, maxy = geom.bounds
    mid_lat = math.radians((miny + maxy) / 2)
    scaled = affinity.scale(geom, xfact=111.32 * math.cos(mid_lat), yfact=110.57, origin=(0, 0))
    return float(scaled.area)


def clean_boundary(value: dict) -> BaseGeometry:
    """Checks an area sent by the client. Returns a clean polygon, or raises DetectionInputError."""
    if _count_points(value.get("coordinates")) > MAX_BOUNDARY_POINTS:
        raise DetectionInputError("The area outline has too many points. Use a simpler outline.")
    geom = location_lookup.to_shape(value)
    if geom is None:
        raise DetectionInputError("The area must be a polygon (GeoJSON Polygon or MultiPolygon).")
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise DetectionInputError("The area is empty.")
    minx, miny, maxx, maxy = geom.bounds
    if minx < -180 or maxx > 180 or miny < -90 or maxy > 90:
        raise DetectionInputError("The area has coordinates outside the world.")
    size = area_km2(geom)
    if size > settings.DETECTION_MAX_AREA_KM2:
        raise DetectionInputError(
            f"This area is {size:,.0f} km2. Choose an area up to {settings.DETECTION_MAX_AREA_KM2:,.0f} km2, "
            "for example one village, taluka or town."
        )
    return geom


def village_window(picture: Path, bbox: tuple) -> tuple[int, int, int, int]:
    """Pixel window (col, row, width, height) of the picture that covers a longitude / latitude box."""
    left, bottom, right, top = bbox
    with rasterio.open(picture) as src:
        if src.crs is None:
            raise DetectionInputError(
                "This image has no map coordinates, so an area cannot be used with it. Use a georeferenced GeoTIFF."
            )
        if src.crs.to_epsg() != 4326:
            left, bottom, right, top = transform_bounds(CRS.from_epsg(4326), src.crs, left, bottom, right, top)
        try:
            win = from_bounds(left, bottom, right, top, transform=src.transform).intersection(
                Window(0, 0, src.width, src.height))
        except WindowError as exc:
            raise DetectionInputError("This area lies outside the image.") from exc
    col, row = max(0, math.floor(win.col_off)), max(0, math.floor(win.row_off))
    width, height = math.ceil(win.width), math.ceil(win.height)
    if width <= 0 or height <= 0:
        raise DetectionInputError("This area lies outside the image.")
    return int(col), int(row), int(width), int(height)


def _check_area(db: Session, district_id, taluka_id, village_id):
    """Returns (district, taluka, village). All None when no area is given."""
    if district_id is None:
        if taluka_id is not None or village_id is not None:
            raise DetectionInputError("Choose a district before a taluka or village.")
        return None, None, None
    district = db.get(District, district_id)
    if district is None:
        raise DetectionInputError("District not found.")
    taluka = None
    if taluka_id is not None:
        taluka = db.get(Taluka, taluka_id)
        if taluka is None or taluka.district_id != district.id:
            raise DetectionInputError("That taluka does not belong to the chosen district.")
    village = None
    if village_id is not None:
        village = db.get(Village, village_id)
        if village is None or village.district_id != district.id:
            raise DetectionInputError("That village does not belong to the chosen district.")
        if taluka is not None and village.taluka_id != taluka.id:
            raise DetectionInputError("That village does not belong to the chosen taluka.")
    return district, taluka, village


def create_job(db: Session, *, user_id: int, body) -> DetectionJob:
    """Checks the request and saves a 'pending' run. The caller must already hold the run lock."""
    defaults = get_value(db, "detection_defaults")
    confidence = defaults["confidence"] if body.confidence is None else body.confidence
    tile_size = body.tile_size or defaults["tile_size"]
    normalize = body.normalize or defaults["normalize"]

    boundary = clean_boundary(body.boundary) if body.boundary is not None else None
    district_id, taluka_id, village_id = body.district_id, body.taluka_id, body.village_id

    if body.use_master:
        image = master_image(db)
        picture = _picture_path(image)
        if boundary is None:
            # Detection by a village whose boundary is stored in the database.
            if village_id is None:
                raise DetectionInputError("Choose an area: search a village or a place, or draw one on the map.")
            village_row = db.get(Village, village_id)
            if village_row is None:
                raise DetectionInputError("Village not found.")
            boundary = location_lookup.to_shape(village_row.geom_geojson)
            if boundary is None:
                raise DetectionInputError("This village has no boundary stored yet. Search a place or draw the area instead.")
            district_id = district_id or village_row.district_id
            taluka_id = taluka_id or village_row.taluka_id
    else:
        image = (db.query(SatelliteImage).filter(SatelliteImage.file_id == body.image_id).first()
                 if body.image_id else None)
        if image is None:
            raise DetectionInputError("File not found. Upload it first.")
        picture = _picture_path(image)

    window = village_window(picture, boundary.bounds) if boundary is not None else None

    try:
        with rasterio.open(picture) as src:
            info = read_info(src)
    except Exception as exc:
        raise DetectionInputError(
            "This file cannot be read as a picture. Use GeoTIFF, JPEG, PNG or JPEG 2000."
        ) from exc

    district, taluka, village = _check_area(db, district_id, taluka_id, village_id)
    label = (body.area_label or "").strip() or None
    params = {
        "mode": "area" if boundary is not None else "image",
        "tile_size": tile_size,
        "overlap": settings.DETECTION_OVERLAP,
        "normalize": normalize,
        "confidence": confidence,
        "nms_iou": settings.DETECTION_NMS_IOU,
        "imgsz": MODEL_IMGSZ,
        "image": info,
        "window": list(window) if window else None,
        "area_label": label,
        "boundary": mapping(boundary) if boundary is not None else None,
    }
    detection_repository.fail_stale_jobs(db)
    job = DetectionJob(
        id=str(uuid.uuid4()),
        user_id=user_id,
        image_id=image.file_id,
        district_id=district.id if district else None,
        taluka_id=taluka.id if taluka else None,
        village_id=village.id if village else None,
        district=district.name if district else None,
        taluka=taluka.name if taluka else None,
        village=village.name if village else None,
        model_version=Path(settings.MODEL_WEIGHTS_PATH).name,
        params=params,
        status="pending",
        tiles_done=0,
    )
    return detection_repository.create_job(db, job)


def _to_wells(picture: str, detections: list[dict], boundary, areas: list, job: DetectionJob) -> list[dict]:
    """Box centres -> longitude / latitude, kept inside the boundary, each named by the village it lies in."""
    if not detections:
        return []
    with rasterio.open(picture) as src:
        if src.crs is None:
            raise DetectionInputError(
                "The image has no map coordinates, so the wells cannot be placed on a map. Use a georeferenced GeoTIFF."
            )
        cols = [(d["x1"] + d["x2"]) / 2 for d in detections]
        rows = [(d["y1"] + d["y2"]) / 2 for d in detections]
        xs, ys = xy(src.transform, rows, cols)
        lons, lats = transform_points(src.crs, "EPSG:4326", xs, ys)
    fallback = (job.village, job.taluka, job.district)
    out = []
    for d, lon, lat in zip(detections, lons, lats):
        lon, lat = float(lon), float(lat)
        if boundary is not None and not boundary.contains(Point(lon, lat)):
            continue
        village, taluka, district = location_lookup.locate(areas, lon, lat)
        out.append({
            "longitude": lon,
            "latitude": lat,
            "confidence": float(d["confidence"]),
            "class_name": str(d.get("class_name", "well")),
            "px_x1": int(round(d["x1"])), "px_y1": int(round(d["y1"])),
            "px_x2": int(round(d["x2"])), "px_y2": int(round(d["y2"])),
            "village": village or fallback[0],
            "taluka": taluka or fallback[1],
            "district": district or fallback[2],
        })
    return out


def _execute(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = detection_repository.get_job(db, job_id)
        if job is None or job.status != "pending":
            return  # stopped before it started
        params = dict(job.params or {})
        image = db.get(SatelliteImage, job.image_id)
        if image is None:
            raise DetectionInputError("The file record is no longer in the database.")
        picture = _picture_path(image)

        boundary = None
        if params.get("boundary"):
            boundary = location_lookup.to_shape(params["boundary"])
        elif params.get("mode") == "village" and job.village_id:
            # Runs made before the area was stored: the village boundary from the database.
            village = db.get(Village, job.village_id)
            boundary = location_lookup.to_shape(village.geom_geojson) if village else None
            if boundary is None:
                raise DetectionInputError("The village boundary is no longer available.")
        boundary_prep = prep(boundary) if boundary is not None else None

        detection_repository.update_job(db, job, status="running", started_at=_now())
        load_model()
        areas = location_lookup.get_areas(db)

        def predict(rgb):
            return predict_tile(rgb, conf=params["confidence"], imgsz=params["imgsz"])

        def on_progress(done, total):
            detection_repository.update_job(db, job, tiles_done=done, tiles_total=total)

        window = tuple(params["window"]) if params.get("window") else None
        detections, stats = detect_raster(
            str(picture), predict, tile_size=params["tile_size"], overlap=params["overlap"],
            normalize=params["normalize"], nms_iou=params["nms_iou"], progress=on_progress,
            window=window, should_stop=lambda: _abort_requested(job_id),
        )
        if _abort_requested(job_id):
            raise DetectionCancelled("Stopped by the user.")

        wells = _to_wells(str(picture), detections, boundary_prep, areas, job)
        stats["wells_in_boundary"] = len(wells) if boundary is not None else None
        params["stats"] = stats
        detection_repository.update_job(db, job, params=params, tiles_total=stats["tiles"])
        detection_repository.insert_wells(db, job.id, wells)
        detection_repository.update_job(
            db, job, status="completed", tiles_done=stats["tiles"],
            total_detections=len(wells), completed_at=_now(),
        )
        logger.info("Detection job %s completed: %d wells", job_id, len(wells))
    except DetectionCancelled:
        db.rollback()
        job = detection_repository.get_job(db, job_id)
        if job is not None:
            detection_repository.update_job(db, job, status="cancelled", error_message=STOPPED_MESSAGE,
                                            completed_at=_now())
        logger.info("Detection job %s was stopped by the user", job_id)
    except DetectionInputError as exc:
        logger.warning("Detection job %s stopped: %s", job_id, exc)
        _mark_failed(db, job_id, str(exc))
    except Exception as exc:
        logger.exception("Detection job %s failed", job_id)
        _mark_failed(db, job_id, _failure_text(exc))
    finally:
        _abort_requests.discard(job_id)
        db.close()


def _failure_text(exc: Exception) -> str:
    if settings.DEBUG:
        return f"{FAILED_MESSAGE} Reason: {type(exc).__name__}: {str(exc)[:300]}"
    return FAILED_MESSAGE


def _mark_failed(db: Session, job_id: str, message: str) -> None:
    """Roll back first: the session may hold a half-finished change."""
    try:
        db.rollback()
        job = detection_repository.get_job(db, job_id)
        if job is not None:
            detection_repository.update_job(db, job, status="failed", error_message=message, completed_at=_now())
    except Exception:
        logger.exception("Could not record the failure of job %s", job_id)


def run_job(job_id: str) -> None:
    """Background task. Never raises. Always releases the run lock, then checks whether a learning round is due."""
    try:
        _execute(job_id)
    finally:
        run_lock.release_claim()
    try:
        learning_service.schedule_auto_training()
    except Exception:
        logger.exception("Could not check the learning schedule")
