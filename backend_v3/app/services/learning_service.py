"""
services/learning_service.py

Learning from detections.

- Every completed detection keeps its wells whose confidence is above the learning threshold (default 70 %).
  These are pseudo-labels: the detector's own boxes.
- Wells the administrator marks by hand (known wells, source admin-manual) are verified labels.
- A round starts from the version in use, trains briefly on the samples, and checks the result on
  verified wells only. The new version replaces the current one when it is not clearly worse there.
  With no verified wells there is no independent check: the new version is saved but not used,
  unless an administrator forces activation.
"""

import logging
import random
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import rowcol
from rasterio.windows import Window
from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.db.models.detected_wells import DetectedWell
from app.db.models.detection_job import DetectionJob
from app.db.models.known_wells import KnownWell
from app.db.models.satellite_image import SatelliteImage
from app.db.repository.known_well_repository import MANUAL_SOURCE
from app.db.session import SessionLocal
from app.ml import bridge
from app.services import imagery_paths, run_lock, settings_service
from app.services.tiled_detection import to_rgb_uint8

logger = logging.getLogger(__name__)
settings = get_settings()

MIN_PSEUDO_AUTO = 20
MIN_PSEUDO_MANUAL = 5
MAX_PSEUDO_PER_ROUND = 2000
MIN_VERIFIED_FOR_CHECK = 6
VERIFY_IMAGES_MIN = 3
MAP50_TOLERANCE = 0.01


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pending_select(min_conf: float):
    return (
        select(DetectedWell.id, DetectedWell.px_x1, DetectedWell.px_y1, DetectedWell.px_x2, DetectedWell.px_y2,
               SatelliteImage.storage_uri, SatelliteImage.stored_filename)
        .select_from(DetectedWell)
        .join(DetectionJob, DetectionJob.id == DetectedWell.job_id)
        .join(SatelliteImage, SatelliteImage.file_id == DetectionJob.image_id)
        .where(DetectionJob.status == "completed", DetectedWell.confidence > min_conf,
               DetectedWell.learned_at.is_(None), DetectedWell.px_x1.isnot(None))
    )


def pending_count(db, min_conf: float) -> int:
    stmt = (
        select(func.count(DetectedWell.id)).select_from(DetectedWell)
        .join(DetectionJob, DetectionJob.id == DetectedWell.job_id)
        .where(DetectionJob.status == "completed", DetectedWell.confidence > min_conf,
               DetectedWell.learned_at.is_(None), DetectedWell.px_x1.isnot(None))
    )
    return int(db.execute(stmt).scalar() or 0)


def verified_points(db) -> list[tuple[float, float]]:
    rows = db.execute(
        select(func.ST_X(KnownWell.geom), func.ST_Y(KnownWell.geom)).where(KnownWell.source == MANUAL_SOURCE)
    ).all()
    return [(float(x), float(y)) for x, y in rows]


def _write_sample(src, cx: float, cy: float, box: tuple, crop: int, img_path: Path, lbl_path: Path) -> bool:
    """One square crop centred on the well, with its box as a YOLO label. Box is in full-picture pixels."""
    x0 = int(round(cx - crop / 2))
    y0 = int(round(cy - crop / 2))
    bands = list(range(1, min(3, src.count) + 1))
    arr = src.read(indexes=bands, window=Window(x0, y0, crop, crop), boundless=True, fill_value=0)
    x1, y1, x2, y2 = box
    bx1, by1 = max(0.0, x1 - x0), max(0.0, y1 - y0)
    bx2, by2 = min(float(crop), x2 - x0), min(float(crop), y2 - y0)
    w, h = bx2 - bx1, by2 - by1
    if w < 2 or h < 2:
        return False
    Image.fromarray(to_rgb_uint8(arr)).save(img_path, quality=95)
    lbl_path.write_text(
        f"0 {(bx1 + bx2) / 2 / crop:.6f} {(by1 + by2) / 2 / crop:.6f} {w / crop:.6f} {h / crop:.6f}\n",
        encoding="utf-8",
    )
    return True


def _emit(root: Path, split: str, items: list, crop: int) -> int:
    """items: (picture path, centre x, centre y, box). Returns how many samples were written."""
    written = 0
    counter = 0
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item[0], []).append(item)
    for path, group in grouped.items():
        try:
            src = rasterio.open(path)
        except Exception:
            logger.warning("Skipping samples from an unreadable picture: %s", path)
            continue
        with src:
            for _, cx, cy, box in group:
                counter += 1
                name = f"{split}_{counter:06d}"
                if _write_sample(src, cx, cy, box, crop, root / "images" / split / f"{name}.jpg",
                                 root / "labels" / split / f"{name}.txt"):
                    written += 1
    return written


def _verified_items(master: Path, points: list, default_size: float) -> list:
    items = []
    if not points or not master.is_file():
        return items
    with rasterio.open(master) as src:
        if src.crs is None or src.crs.to_epsg() != 4326:
            logger.warning("The master picture is not in degrees; verified wells are not used for learning")
            return items
        half = default_size / 2
        for lon, lat in points:
            rows, cols = rowcol(src.transform, [lon], [lat])
            r, c = int(rows[0]), int(cols[0])
            if 0 <= r < src.height and 0 <= c < src.width:
                items.append((str(master), float(c), float(r), (c - half, r - half, c + half, r + half)))
    return items


def _write_yaml(path: Path, root: Path, val_dir: str) -> None:
    path.write_text(
        f'path: "{root.as_posix()}"\ntrain: images/train\nval: images/{val_dir}\nnames:\n  0: well\n',
        encoding="utf-8",
    )


def _metrics(weights: Path, data_yaml: Path, project: Path) -> dict:
    from ultralytics import YOLO

    result = YOLO(str(weights)).val(
        data=str(data_yaml), imgsz=settings.LEARNING_CROP_SIZE, batch=8, workers=0, plots=False,
        verbose=False, split="val", project=str(project), name="verify", exist_ok=True,
    )
    return {
        "precision": round(float(result.box.mp), 4),
        "recall": round(float(result.box.mr), 4),
        "map50": round(float(result.box.map50), 4),
    }


def learning_root() -> Path:
    return imagery_paths.resolve(Path(settings.LEARNING_DIR))


def latest_candidate() -> Path | None:
    candidates = sorted((learning_root() / "candidates").glob("*.pt"))
    return candidates[-1] if candidates else None


def _activate(new_weights: Path) -> None:
    """Keeps the version in use as a backup, then puts the new one in its place."""
    current = bridge.weights_path()
    versions = learning_root() / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    if current.is_file():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shutil.copy2(current, versions / f"{stamp}_previous.pt")
    staging = current.with_name(current.stem + ".incoming.pt")
    shutil.copy2(new_weights, staging)
    staging.replace(current)
    bridge.reload_model()


def _finish(db, trigger: str, started: str, result: str, message: str, extra: dict | None = None) -> None:
    status = {"trigger": trigger, "started_at": started, "finished_at": _now().isoformat(),
              "result": result, "message": message}
    status.update(extra or {})
    settings_service.set_status(db, status)
    logger.info("Learning round (%s): %s - %s", trigger, result, message)


def _train(db, trigger: str, activate: bool) -> None:
    cfg = settings_service.get_value(db, "learning")
    crop = settings.LEARNING_CROP_SIZE
    started = _now().isoformat()
    settings_service.set_status(db, {"trigger": trigger, "result": "running", "started_at": started,
                                     "message": "Preparing samples."})

    rows = db.execute(
        _pending_select(cfg["min_confidence"]).order_by(DetectedWell.confidence.desc()).limit(MAX_PSEUDO_PER_ROUND)
    ).all()
    needed = MIN_PSEUDO_AUTO if trigger == "auto" else MIN_PSEUDO_MANUAL
    if len(rows) < needed:
        _finish(db, trigger, started, "skipped", f"Only {len(rows)} new samples. At least {needed} are needed.")
        return

    ids = [r.id for r in rows]
    pseudo, sizes = [], []
    for r in rows:
        box = (float(r.px_x1), float(r.px_y1), float(r.px_x2), float(r.px_y2))
        path = str(imagery_paths.path_of(r.storage_uri, r.stored_filename))
        pseudo.append((path, (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, box))
        sizes.append(max(box[2] - box[0], box[3] - box[1]))
    default_size = float(np.clip(np.median(sizes), 8, 48))

    verified = _verified_items(imagery_paths.master_path(), verified_points(db), default_size)
    rng = random.Random(7)
    rng.shuffle(pseudo)
    rng.shuffle(verified)
    n_val = max(1, len(pseudo) // 10)
    pseudo_val, pseudo_train = pseudo[:n_val], pseudo[n_val:]
    if len(verified) >= MIN_VERIFIED_FOR_CHECK:
        n_check = max(VERIFY_IMAGES_MIN, len(verified) // 3)
        check_items, verified_train = verified[:n_check], verified[n_check:]
    else:
        check_items, verified_train = [], verified

    run_dir = learning_root() / f"round_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    dataset = run_dir / "dataset"
    for split in ("train", "val", "verify"):
        (dataset / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset / "labels" / split).mkdir(parents=True, exist_ok=True)
    written_train = _emit(dataset, "train", pseudo_train + verified_train, crop)
    written_val = _emit(dataset, "val", pseudo_val, crop)
    written_check = _emit(dataset, "verify", check_items, crop)
    if written_train == 0 or written_val == 0:
        _finish(db, trigger, started, "skipped", "The new samples were too small to use. Nothing was changed.")
        return

    train_yaml, check_yaml = dataset / "train.yaml", dataset / "check.yaml"
    _write_yaml(train_yaml, dataset, "val")
    _write_yaml(check_yaml, dataset, "verify")

    from ultralytics import YOLO

    current = bridge.weights_path()
    YOLO(str(current)).train(
        data=str(train_yaml), epochs=settings.LEARNING_EPOCHS, imgsz=crop, batch=8, lr0=0.001, freeze=10,
        patience=3, workers=0, plots=False, verbose=False, project=str(run_dir / "runs"), name="train",
        exist_ok=True,
    )
    best = run_dir / "runs" / "train" / "weights" / "best.pt"
    if not best.is_file():
        best = run_dir / "runs" / "train" / "weights" / "last.pt"

    can_check = written_check >= VERIFY_IMAGES_MIN
    before = after = None
    if can_check:
        before = _metrics(current, check_yaml, run_dir)
        after = _metrics(best, check_yaml, run_dir)
    if activate:
        decide = True
    elif can_check:
        decide = after["map50"] >= before["map50"] - MAP50_TOLERANCE
    else:
        decide = False

    candidates = learning_root() / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, candidates / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.pt")

    if decide:
        _activate(best)
        result = "activated"
        if can_check:
            message = f"The new version is in use. Check on verified wells: mAP50 {after['map50']} (was {before['map50']})."
        else:
            message = "The new version is in use. It was forced by an administrator without a check on verified wells."
    elif can_check:
        result = "kept"
        message = (f"The new version was not better on the verified wells (mAP50 {after['map50']} against "
                   f"{before['map50']}), so the current version was kept. The new version is saved as a candidate.")
    else:
        result = "candidate"
        message = ("The new version is saved but not in use: there are too few verified wells to check it. "
                   "Mark more wells by hand, or activate the saved version after review.")

    if ids:
        db.execute(update(DetectedWell).where(DetectedWell.id.in_(ids)).values(learned_at=_now()))
        db.commit()
    _finish(db, trigger, started, result, message, {
        "samples_pseudo": len(pseudo), "samples_verified": len(verified),
        "checked_on": written_check, "before": before, "after": after,
    })


def _run_claimed(trigger: str, activate: bool) -> None:
    db = SessionLocal()
    try:
        _train(db, trigger, activate)
    except Exception as exc:
        logger.exception("Learning round failed")
        try:
            db.rollback()
            settings_service.set_status(db, {
                "trigger": trigger, "result": "failed", "finished_at": _now().isoformat(),
                "message": f"The learning round failed: {str(exc)[:300]}",
            })
        except Exception:
            logger.exception("Could not record the failed learning round")
    finally:
        db.close()
        run_lock.release_claim()


def request_training(trigger: str, activate: bool = False) -> bool:
    """Starts a round in the background. False when the machine is busy with a detection or another round."""
    if not run_lock.try_claim():
        return False
    threading.Thread(target=_run_claimed, args=(trigger, activate), name="learning-round", daemon=True).start()
    return True


def schedule_auto_training() -> None:
    """Called after every detection run: starts a round when enough new samples have built up."""
    db = SessionLocal()
    try:
        cfg = settings_service.get_value(db, "learning")
        if not cfg["enabled"] or cfg["auto_train_samples"] <= 0:
            return
        if pending_count(db, cfg["min_confidence"]) < cfg["auto_train_samples"]:
            return
    finally:
        db.close()
    request_training("auto")


def status(db) -> dict:
    cfg = settings_service.get_value(db, "learning")
    return {
        "enabled": cfg["enabled"],
        "min_confidence": cfg["min_confidence"],
        "auto_train_samples": cfg["auto_train_samples"],
        "pending_samples": pending_count(db, cfg["min_confidence"]),
        "verified_wells": len(verified_points(db)),
        "busy": run_lock.is_busy(),
        "candidate_available": latest_candidate() is not None,
        "last_round": settings_service.get_status(db),
    }


def activate_latest_candidate(db) -> str:
    candidate = latest_candidate()
    if candidate is None:
        raise ValidationException("There is no saved version to activate.")
    _activate(candidate)
    previous = settings_service.get_status(db) or {}
    settings_service.set_status(db, {**previous, "result": "activated", "finished_at": _now().isoformat(),
                                     "message": f"Saved version {candidate.name} was activated by an administrator."})
    return candidate.name
