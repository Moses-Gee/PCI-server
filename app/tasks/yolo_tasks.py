from collections import Counter
import logging
from pathlib import Path
from app.core.celery_app import celery_app
from app.core.cloudinary_client import upload_numpy_image_to_cloudinary_sync
from app.services.image_service import save_image_record_sync
from app.core.inference_events import (
    publish_processing,
    publish_done,
    publish_failed,
    publish_inference_event,
)
from app.services.yolo_seg.seg_model import infer_image_seg_model

logger = logging.getLogger(__name__)


def _mark_failed(sample_unit_id: str):
    """Helper to mark a sample unit as failed in a fresh DB session."""
    from app.core.database import SyncSessionLocal
    from app.models.sample_unit import SampleUnit

    try:
        with SyncSessionLocal() as db:
            sample = db.get(SampleUnit, sample_unit_id)
            if sample:
                sample.inference_status = "failed"
                db.commit()
    except Exception:
        logger.exception(f"Could not mark {sample_unit_id} as failed")


def _build_metrics_payload(record) -> dict:
    """
    Build the `metrics` JSON payload stored in DetectionResult.
    Works for DistressRecord from EITHER bbox_model or seg_model because
    both dataclasses now share the same field names.
    """
    return {
        # geometry
        "avg_width": record.width_mm,
        "length": record.length_mm,
        "area": record.area_mm2,
        "perimeter": record.perimeter_mm,
        "bbox_area_mm2": record.bbox_area_mm2,
        # topology
        "branch_density": record.branch_density,
        "loop_count": record.loop_count,
        "fill_ratio": record.fill_ratio,
        "shape_complexity": record.shape_complexity,
        "orientation_deg": record.orientation_deg,
        "aspect_ratio": record.aspect_ratio,
        "texture_cv": record.texture_cv,
        # crack classification
        "crack_category_confidence": record.crack_category_confidence,
        # pothole
        "pothole_equiv_diameter_mm": record.pothole_equiv_diameter_mm,
        "pothole_depth_est_mm": record.pothole_depth_est_mm,
        "pothole_count": record.pothole_count,
        # bbox
        "bbox_dim": record.bbox_dim,
        # ASTM
        "astm_quantity": record.astm_quantity,
        "astm_unit": record.astm_unit,
        # full dicts
        "severity_metrics": record.severity_metrics,
        "skel_metrics": record.metrics,
    }


@celery_app.task(
    bind=True,
    max_retries=3,
    time_limit=180,  # hard kill after 3 min
    soft_time_limit=150,  # graceful signal at 2.5 min
    name="tasks.run_yolo_inference",
)
def run_yolo_inference(self, sample_unit_id: str, model_to_use: str):
    """
    Celery runs in a separate process — must use a sync DB session,
    not the async one FastAPI uses.
    """
    from app.core.celery_app import celery_app
    from app.core.database import SyncSessionLocal  # see note below
    from app.models.sample_unit import SampleUnit
    from app.models.detection_result import DetectionResult
    from app.services.pci.pci_utilities import normalizeClass
    from app.services.yolo_bbox.bbox_model import infer_image_bbox_model
    import cv2
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.image import Image
    import requests
    import numpy as np

    logger.info(f"Starting inference for sample_unit_id={sample_unit_id}")

    try:
        with SyncSessionLocal() as db:
            stmt = (
                select(SampleUnit)
                .where(SampleUnit.id == sample_unit_id)
                .options(selectinload(SampleUnit.section))  # 👈 this loads the section
            )
            result = db.execute(stmt)
            sample = result.scalar_one_or_none()

            if not sample:
                return {"status": "error", "detail": "Sample unit not found"}

            # Get original image record
            original = db.execute(
                select(Image).where(
                    Image.sample_unit_id == sample.id,
                    Image.is_original == True,
                )
            ).scalar_one_or_none()

            if not original:
                _mark_failed(sample_unit_id)
                publish_failed(sample_unit_id, "No original image found")
                return {"status": "error", "detail": "No original image record"}

            # Mark as processing
            sample.inference_status = "processing"
            db.commit()
            publish_processing(sample_unit_id, "started", "Downloading image...")

            # Download image bytes from Cloudinry public URL
            response = requests.get(original.public_url, timeout=30)
            response.raise_for_status()
            image_array = np.frombuffer(response.content, np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

            if image is None:
                raise ValueError("Could not decode image from Cloudinry")

            # ── Run YOLO with progress callback ───────────────────────────────
            def on_progress(step: str, detail: str):
                publish_processing(sample_unit_id, step, detail)

            # Run YOLO (blocking is fine here — we're in a worker process)
            if model_to_use == "Segmentation":
                detections, records, annotated = infer_image_seg_model(
                    image=image,
                    px_per_mm=sample.pixel_to_mm_factor,
                    on_progress=on_progress,
                )
            else:
                detections, records, annotated = infer_image_bbox_model(
                    image=image,
                    px_per_mm=sample.pixel_to_mm_factor,
                    on_progress=on_progress,
                    sample_unit_area_mm2=sample.section.area * 1_000_000,
                )

            if not records:
                print(detections)
                print(records)
                print("No detections")
                sample.inference_status = "done"
                db.commit()
                publish_done(sample_unit_id, detection_count=0)
                return {"status": "done", "sample_unit_id": sample_unit_id}

            # ── Upload annotated image ────────────────────────────────────────
            publish_processing(sample_unit_id, "uploading", "Saving annotated image...")
            annotated_result = upload_numpy_image_to_cloudinary_sync(
                image_array=annotated,
                original_filename=original.original_filename,
                folder="predicted",
            )

            save_image_record_sync(
                db,
                sample_unit_id=sample.id,
                result=annotated_result,
                is_original=False,
                is_annotated=True,
            )

            # ── Persist detections ────────────────────────────────────────────
            publish_processing(
                sample_unit_id, "saving", "Saving detections to database..."
            )
            # Count occurrences of each class across all records
            class_counts = Counter(r.class_name for r in records)

            # Persist detections
            for record in records:
                det = DetectionResult(
                    sample_unit_id=sample.id,
                    distress_type=record.class_name,
                    severity=record.severity,
                    severity_label=record.severity_label,
                    quantity=class_counts[record.class_name],
                    confidence=record.confidence,
                    normalized_class=normalizeClass(record.class_name),
                    metrics=_build_metrics_payload(record),
                )
                db.add(det)

            # sample.predicted_image = annotated_path
            sample.inference_status = "done"
            db.commit()

            publish_done(sample_unit_id, detection_count=len(records))
            logger.info(
                f"Inference done for {sample_unit_id} — {len(records)} detections"
            )
            return {"status": "done", "sample_unit_id": sample_unit_id}

    except Exception as exc:
        logger.exception(f"Inference failed for {sample_unit_id}: {exc}")
        publish_failed(sample_unit_id, str(exc))

        if self.request.retries >= self.max_retries:
            # All retries exhausted
            logger.error(f"All retries exhausted for {sample_unit_id}")
            _mark_failed(sample_unit_id)
            return {"status": "failed", "detail": str(exc)}

        # Exponential backoff: 2s, 4s, 8s
        raise self.retry(exc=exc, countdown=2**self.request.retries)
