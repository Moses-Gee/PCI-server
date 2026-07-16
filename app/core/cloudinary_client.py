import cloudinary
import cloudinary.uploader
import cloudinary.api
import filetype
import logging
from dataclasses import dataclass
from datetime import datetime
import numpy as np
import cv2

from app.core.config import settings

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
}

MAX_FILE_SIZE_BYTES = 2.5 * 1024 * 1024  # 2.5 MB


def _configure_cloudinary():
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


# Configure once at import time
_configure_cloudinary()


@dataclass
class CloudinaryUploadResult:
    public_id: str  # Cloudinary's unique identifier (used for deletion)
    public_url: str  # https secure URL
    asset_id: str  # Cloudinary internal asset ID
    format: str  # e.g. "jpg", "png"
    width: int
    height: int
    size_bytes: int
    mime_type: str
    original_filename: str



MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}  # adjust as needed

def _validate_file(file_bytes: bytes) -> str:
    """Validates size and MIME type. Returns detected mime_type."""
    size = len(file_bytes)
    if size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File too large: {size / (1024*1024):.1f}MB — "
            f"max is {MAX_FILE_SIZE_BYTES / (1024*1024):.1f}MB"
        )

    kind = filetype.guess(file_bytes)
    if kind is None:
        mime_type = "application/octet-stream"
    else:
        mime_type = kind.mime

    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"File type '{mime_type}' is not allowed. "
            f"Accepted: {', '.join(ALLOWED_MIME_TYPES)}"
        )
    return mime_type


def _build_public_id(original_filename: str, folder: str) -> str:
    """
    Builds a structured Cloudinary public_id.
    e.g. pci-app/originals/2025/06/20250615_abc123
    Cloudinary appends the format extension automatically.
    """
    stem = (
        original_filename.rsplit(".", 1)[0]
        if "." in original_filename
        else original_filename
    )
    date_prefix = datetime.utcnow().strftime("%Y/%m")
    import uuid

    unique = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    return f"pci-app/{folder}/{date_prefix}/{unique}_{stem}"


# ── Async upload (FastAPI context) ────────────────────────────────────────────


async def upload_image_to_cloudinary(
    file,  # FastAPI UploadFile
    folder: str = "originals",
) -> CloudinaryUploadResult:
    """
    Validates and uploads an image to Cloudinary.
    Uses asyncio executor to avoid blocking the event loop.
    """
    import asyncio

    file_bytes = await file.read()
    mime_type = _validate_file(file_bytes)
    public_id = _build_public_id(file.filename or "upload", folder)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _upload_bytes_sync(
            file_bytes, public_id, mime_type, file.filename or "upload"
        ),
    )

    await file.seek(0)
    return result


def _upload_bytes_sync(
    file_bytes: bytes,
    public_id: str,
    mime_type: str,
    original_filename: str,
) -> CloudinaryUploadResult:
    """Core sync upload — called from both async (via executor) and Celery."""
    import io

    try:
        response = cloudinary.uploader.upload(
            io.BytesIO(file_bytes),
            public_id=public_id,
            resource_type="image",
            overwrite=False,
            context={"original_filename": original_filename},
        )
    except Exception as e:
        logger.exception(f"Cloudinary upload failed for public_id={public_id}")
        raise RuntimeError(f"Cloudinary upload failed: {e}")

    logger.info(
        f"Uploaded {public_id} ({len(file_bytes)} bytes) → {response['secure_url']}"
    )

    return CloudinaryUploadResult(
        public_id=response["public_id"],
        public_url=response["secure_url"],
        asset_id=response.get("asset_id", ""),
        format=response.get("format", ""),
        width=response.get("width", 0),
        height=response.get("height", 0),
        size_bytes=response.get("bytes", len(file_bytes)),
        mime_type=mime_type,
        original_filename=original_filename,
    )


# ── Sync upload for Celery (annotated images) ─────────────────────────────────


def upload_numpy_image_to_cloudinary_sync(
    image_array: np.ndarray,
    original_filename: str,
    folder: str = "predicted",
) -> CloudinaryUploadResult:
    """
    Encodes a cv2/numpy image array and uploads to Cloudinary.
    Use inside Celery tasks.
    """
    _, buffer = cv2.imencode(".jpg", image_array)
    file_bytes = buffer.tobytes()
    public_id = _build_public_id(f"{original_filename}_predicted", folder)

    return _upload_bytes_sync(
        file_bytes=file_bytes,
        public_id=public_id,
        mime_type="image/jpeg",
        original_filename=original_filename,
    )


# ── Deletion ──────────────────────────────────────────────────────────────────


async def delete_image_from_cloudinary(public_id: str) -> None:
    """Async single delete. Use in FastAPI endpoints."""
    import asyncio

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete_sync, [public_id])


async def delete_images_from_cloudinary(public_ids: list[str]) -> None:
    """Async batch delete. Use in FastAPI endpoints."""
    import asyncio

    if not public_ids:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _delete_sync, public_ids)


def delete_images_from_cloudinary_sync(public_ids: list[str]) -> None:
    """Sync batch delete. Use inside Celery tasks."""
    _delete_sync(public_ids)


def _delete_sync(public_ids: list[str]) -> None:
    """
    Cloudinary delete_resources accepts up to 100 public_ids at once.
    Chunks automatically if needed.
    """
    if not public_ids:
        return

    chunk_size = 100
    for i in range(0, len(public_ids), chunk_size):
        chunk = public_ids[i : i + chunk_size]
        try:
            result = cloudinary.api.delete_resources(chunk, resource_type="image")
            logger.info(f"Deleted {len(chunk)} Cloudinary resources")
            # Log anything that failed within the batch
            failed = {
                k: v for k, v in result.get("deleted", {}).items() if v != "deleted"
            }
            if failed:
                logger.warning(f"Some Cloudinary deletes failed: {failed}")
        except Exception as e:
            logger.exception(f"Cloudinary batch delete failed: {e}")
            raise RuntimeError(f"Cloudinary delete failed: {e}")
