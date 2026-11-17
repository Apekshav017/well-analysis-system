"""
services/imagery_paths.py

Where imagery files live on disk. The master mosaic is the picture used for detection by village name.
"""

from pathlib import Path

from app.core.config import get_settings

settings = get_settings()
BACKEND_ROOT = Path(__file__).resolve().parents[2]
MASTER_FILE_ID = "master-mosaic"


def resolve(path: Path) -> Path:
    """Relative paths in .env are relative to the Backend folder."""
    return path if path.is_absolute() else (BACKEND_ROOT / path).resolve()


def master_path() -> Path:
    return resolve(Path(settings.MASTER_IMAGE_PATH))


def path_of(storage_uri: str | None, stored_filename: str | None) -> Path:
    if storage_uri:
        return Path(storage_uri)
    if stored_filename:
        return settings.UPLOAD_DIR / stored_filename
    raise ValueError("The file record has no stored file name.")
