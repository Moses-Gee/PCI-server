import logging
from collections import Counter

logger = logging.getLogger(__name__)


def _mark_failed(sample_unit_id: str):
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
    return {
        "avg_width":   record.width_mm,
        "length":      record.length_mm,
        "area":        record.area_mm2,
        "perimeter":   record.perimeter_mm,
        "bbox_area_mm2": record.bbox_area_mm2,
        "branch_density": record.branch_density,
        "loop_count":  record.loop_count,
        "fill_ratio":  record.fill_ratio,
        "shape_complexity": record.shape_complexity,
        "orientation_deg": record.orientation_deg,
        "aspect_ratio": record.aspect_ratio,
        "texture_cv":  record.texture_cv,
        "crack_category_confidence": record.crack_category_confidence,
        "pothole_equiv_diameter_mm": record.pothole_equiv_diameter_mm,
        "pothole_depth_est_mm": record.pothole_depth_est_mm,
        "pothole_count": record.pothole_count,
        "bbox_dim":    record.bbox_dim,
        "astm_quantity": record.astm_quantity,
        "astm_unit":   record.astm_unit,
        "severity_metrics": record.severity_metrics,
        "skel_metrics": record.metrics,
    }


def run_yolo_inference(sample_unit_id: str, model_to_use: str):
    """
    Plain Python function — runs in a background thread via run_in_background().
    No Celery, no Redis. Uses sync DB session (same as before).
    """
    from app.core.database import SyncSessionLocal
    from app.models.sample_unit import SampleUnit
    from app.models.detection_result import DetectionResult
    from app.models.image import Image
    from app.services.pci.pci_utilities import normalizeClass
    from app.services.yolo_bbox.bbox_model import infer_image_bbox_model
    from app.services.yolo_seg.seg_model import infer_image_seg_model
    from app.core.cloudinary_client import upload_numpy_image_to_cloudinary_sync
    from app.services.image_service import save_image_record_sync
    from app.core.inference_events import (
        publish_processing, publish_done, publish_failed,
    )
    from app.core import models as M
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    import cv2
    import numpy as np
    import requests

    logger.info(f"Starting inference for {sample_unit_id} model={model_to_use}")

    try:
        with SyncSessionLocal() as db:
            # ── Load sample ───────────────────────────────────────────────────
            stmt = (
                select(SampleUnit)
                .where(SampleUnit.id == sample_unit_id)
                .options(selectinload(SampleUnit.section))
            )
            sample = db.execute(stmt).scalar_one_or_none()
            if not sample:
                return

            # ── Original image ────────────────────────────────────────────────
            original = db.execute(
                select(Image).where(
                    Image.sample_unit_id == sample.id,
                    Image.is_original == True,
                )
            ).scalar_one_or_none()

            if not original:
                _mark_failed(sample_unit_id)
                publish_failed(sample_unit_id, "No original image found")
                return

            # ── Mark processing ───────────────────────────────────────────────
            sample.inference_status = "processing"
            db.commit()
            publish_processing(sample_unit_id, "started", "Downloading image...")

            # ── Download from Cloudinary ──────────────────────────────────────
            response = requests.get(original.public_url, timeout=30)
            response.raise_for_status()
            image_array = np.frombuffer(response.content, np.uint8)
            image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("Could not decode image")

            # ── Check models loaded ───────────────────────────────────────────
            if model_to_use == "Segmentation" and M.SEG_MODEL is None:
                raise RuntimeError("SEG_MODEL not loaded")
            if model_to_use != "Segmentation" and M.BBOX_MODEL is None:
                raise RuntimeError("BBOX_MODEL not loaded")

            # ── Progress callback ─────────────────────────────────────────────
            def on_progress(step: str, detail: str):
                publish_processing(sample_unit_id, step, detail)

            # ── Run inference ─────────────────────────────────────────────────
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

            # ── No detections ─────────────────────────────────────────────────
            if not records:
                sample.inference_status = "done"
                db.commit()
                publish_done(sample_unit_id, detection_count=0)
                return

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
            publish_processing(sample_unit_id, "saving", "Saving detections...")
            class_counts = Counter(r.class_name for r in records)
            for record in records:
                db.add(DetectionResult(
                    sample_unit_id=sample.id,
                    distress_type=record.class_name,
                    severity=record.severity,
                    severity_label=record.severity_label,
                    quantity=class_counts[record.class_name],
                    confidence=record.confidence,
                    normalized_class=normalizeClass(record.class_name),
                    metrics=_build_metrics_payload(record),
                ))

            sample.inference_status = "done"
            db.commit()
            publish_done(sample_unit_id, detection_count=len(records))
            logger.info(f"Inference done for {sample_unit_id} — {len(records)} detections")

    except Exception as exc:
        logger.exception(f"Inference failed for {sample_unit_id}: {exc}")
        publish_failed(sample_unit_id, str(exc))
        _mark_failed(sample_unit_id)