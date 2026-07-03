from collections.abc import Callable
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import cv2
import numpy as np
from app.services.yolo_seg.config_seg import seg_cfg
from app.services.yolo_seg.main_quantifier import DistressQuantifier
from app.core.models import SEG_MODEL, SEG_MODEL_PATH


class DistressRecord:
    """One distress instance detected in a sample unit."""

    class_name: str
    severity: str  # "low" | "medium" | "high" | "n/a"
    width_mm: float = 0.0  # for linear cracks
    length_mm: float = 0.0  # for linear cracks
    area_mm2: float = 0.0  # for area distresses
    confidence: float = 0.0
    crack_category_confidence: Optional[float] = None
    bbox: List[int] = field(default_factory=list)
    bbox_dim: List[int] = field(default_factory=list)
    pothole_equiv_diameter_mm: Optional[float] = None  # √(4·A/π)
    pothole_depth_est_mm: Optional[float] = None  # empirical proxy
    pothole_count: Optional[int] = None  # 1 per mask


def extract_detections_from_yolo(
    yolo_result,
    image_h: int,
    image_w: int,
    progress,  # (step, detail))
) -> List[Dict]:

    detections = []
    if yolo_result.masks is None:
        return detections

    class_names = yolo_result.names  # {0: "Crack", 1: "pothole"}
    masks_xy = yolo_result.masks.xy  # list of polygon arrays
    masks_data = yolo_result.masks.data  # tensor [N, H, W]
    boxes = yolo_result.boxes
    total = len(masks_data)

    progress("analysing", f"Analysing {total} detection(s)...")

    for i in range(len(masks_data)):
        # Full-resolution binary mask
        mask_tensor = masks_data[i].cpu().numpy()
        # Resize from model resolution to original image resolution
        mask_full = cv2.resize(
            (mask_tensor * 255).astype(np.uint8),
            (image_w, image_h),
            interpolation=cv2.INTER_NEAREST,
        )
        # BBox
        xyxy = boxes.xyxy[i].cpu().numpy().astype(int).tolist()
        conf = float(boxes.conf[i].cpu().numpy())
        cls_id = int(boxes.cls[i].cpu().numpy())
        cls_name = class_names[cls_id]

        progress("analysing", f"Processing detection {i + 1}/{total}: {cls_name}")

        detections.append(
            {
                "class_name": cls_name,
                "mask": mask_full,
                "bbox": xyxy,
                "confidence": conf,
            }
        )

    return detections


def infer_image_seg_model(
    image,
    px_per_mm: float,
    on_progress: Optional[Callable[[str, str], None]] = None,  # (step, detail))
):
    """
    Run the full pipeline on a single BGR numpy image.
    on_progress is an optional callback fired at each stage.
    """

    def progress(step: str, detail: str = ""):
        if on_progress:
            on_progress(step, detail)

    if image is None:
        raise ValueError("Could not load image")
    if not SEG_MODEL_PATH:
        raise RuntimeError("BBOX_MODEL PATH does not exist")
    if SEG_MODEL is None:
        raise RuntimeError("BBOX_MODEL not loaded at worker startup")

        # ── Stage 1: YOLO detection ───────────────────────────────────────────────
    progress("detecting", "Running YOLO detection...")
    H, W = image.shape[:2]
    # ── Phase I: YOLOv8 Detection ─────────────────────────────────
    yolo_results = SEG_MODEL.predict(
        source=image,
        imgsz=seg_cfg.img_size,
        conf=seg_cfg.CONF_THRESHOLD,
        iou=seg_cfg.IOU_THRESHOLD,
        # device=bbox_cfg,
    )
    # print(yolo_results[0])
    annotated = yolo_results[0].plot()

    yolo_result = yolo_results[0]

    # ── Extract masks ─────────────────────────────────────────────────
    detections = extract_detections_from_yolo(yolo_result, H, W, progress=progress)
    print(f"[INFO] Detections found: {len(detections)}")

    if not detections:
        print("[INFO] No distress detected.")
        progress("complete", "No detections found")
        return detections, records, annotated

    # ── Quantify ──────────────────────────────────────────────────────
    dq = DistressQuantifier(px_per_mm=px_per_mm)
    results = dq.quantify_all(image, detections)
    records: List[DistressRecord] = []

    for result in results:
        records.append(
            DistressRecord(
                class_name=result.distress_type,
                severity=result.severity,
                width_mm=result.crack_width_mm,
                length_mm=result.crack_length_mm,
                area_mm2=result.mask_area_mm2,
                confidence=result.confidence,
                bbox=[result.bbox_x1, result.bbox_y1, result.bbox_x2, result.bbox_y2],
                crack_category_confidence=result.crack_category_confidence,
                bbox_dim=[result.bbox_w_mm, result.bbox_h_mm],
                pothole_equiv_diameter_mm=result.pothole_equiv_diameter_mm,
                pothole_depth_est_mm=result.pothole_depth_est_mm,
                pothole_count=result.pothole_count,
            )
        )
    progress("uploading", f"Uploading results ({len(records)} distresses found)...")
    return detections, records, annotated
