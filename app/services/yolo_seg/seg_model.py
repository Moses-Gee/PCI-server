"""
seg_model.py
============
Segmentation-model inference pipeline.

DistressRecord is identical to the one in bbox_model.py so that
yolo_tasks.py can consume records from either model with the same code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np

from app.core import models
from app.services.yolo_seg.config_seg import seg_cfg
from app.services.yolo_seg.main_quantifier import DistressQuantifier

# ─────────────────────────────────────────────────────────────────────────────
# DistressRecord  — identical schema to bbox_model.DistressRecord
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DistressRecord:
    """
    One distress instance.  Every field mirrors bbox_model.DistressRecord
    so yolo_tasks.py can use a single code-path for both models.
    """

    # ── identity ──────────────────────────────────────────────────────────────
    class_name: str
    severity: str  # "low" | "medium" | "high" | "n/a"
    severity_label: str = ""  # human-readable ASTM label

    # ── geometry (mm) ─────────────────────────────────────────────────────────
    width_mm: float = 0.0
    length_mm: float = 0.0
    area_mm2: float = 0.0
    perimeter_mm: float = 0.0

    # ── detection ─────────────────────────────────────────────────────────────
    confidence: float = 0.0
    bbox: List[int] = field(default_factory=list)  # [x1,y1,x2,y2]
    bbox_dim: List[float] = field(default_factory=list)  # [w_mm, h_mm]
    bbox_area_mm2: float = 0.0

    # ── topology (crack / alligator) ──────────────────────────────────────────
    branch_density: float = 0.0
    loop_count: int = 0
    fill_ratio: float = 0.0
    shape_complexity: float = 0.0
    orientation_deg: float = 0.0
    aspect_ratio: float = 1.0

    # ── texture (patching proxy — always 0.0 for seg model) ──────────────────
    texture_cv: float = 0.0

    # ── crack classification ──────────────────────────────────────────────────
    crack_category_confidence: Optional[float] = None

    # ── pothole ───────────────────────────────────────────────────────────────
    pothole_equiv_diameter_mm: Optional[float] = None
    pothole_depth_est_mm: Optional[float] = None
    pothole_count: Optional[int] = None

    # ── ASTM measurement unit ─────────────────────────────────────────────────
    astm_quantity: float = 0.0
    astm_unit: str = ""

    # ── full metric dicts (for DB / JSON export) ──────────────────────────────
    metrics: Dict = field(default_factory=dict)
    severity_metrics: Dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# YOLO mask extraction
# ─────────────────────────────────────────────────────────────────────────────


def extract_detections_from_yolo(
    yolo_result,
    image_h: int,
    image_w: int,
    progress: Callable[[str, str], None],
) -> List[Dict]:
    """
    Convert a YOLOv8-seg result into the list-of-dicts format that
    DistressQuantifier.quantify_all() expects.
    """
    detections: List[Dict] = []

    if yolo_result.masks is None:
        return detections

    class_names = yolo_result.names  # {0: "Crack", 1: "pothole"}
    masks_data = yolo_result.masks.data  # tensor  [N, Hm, Wm]
    boxes = yolo_result.boxes
    total = len(masks_data)

    progress("analysing", f"Analysing {total} detection(s)...")

    for i in range(total):
        # Resize mask from model resolution → original image resolution
        mask_tensor = masks_data[i].cpu().numpy()
        mask_full = cv2.resize(
            (mask_tensor * 255).astype(np.uint8),
            (image_w, image_h),
            interpolation=cv2.INTER_NEAREST,
        )

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


# ─────────────────────────────────────────────────────────────────────────────
# Inference entry-point
# ─────────────────────────────────────────────────────────────────────────────


def infer_image_seg_model(
    image,
    px_per_mm: float,
    on_progress: Optional[Callable[[str, str], None]] = None,
):
    """
    Run the full segmentation + quantification pipeline on a BGR image.

    Parameters
    ----------
    image       : BGR numpy array
    px_per_mm   : pixels per mm  (camera calibration)
    on_progress : optional callback(step: str, detail: str)

    Returns
    -------
    detections : List[Dict]          — raw per-detection data
    records    : List[DistressRecord]
    annotated  : np.ndarray          — BGR image with masks drawn
    """

    def progress(step: str, detail: str = ""):
        if on_progress:
            on_progress(step, detail)

    if image is None:
        raise ValueError("image must not be None")
    if models.SEG_MODEL is None:
        raise RuntimeError("SEG_MODEL not loaded at worker startup")

    H, W = image.shape[:2]

    # ── Stage 1: YOLO segmentation ───────────────────────────────────────────
    progress("detecting", "Running YOLO segmentation...")
    yolo_results = models.SEG_MODEL.predict(
        source=image,
        imgsz=seg_cfg.img_size,
        conf=seg_cfg.CONF_THRESHOLD,
        iou=seg_cfg.IOU_THRESHOLD,
    )
    yolo_result = yolo_results[0]
    annotated = yolo_result.plot()

    # ── Stage 2: Extract masks ───────────────────────────────────────────────
    detections = extract_detections_from_yolo(yolo_result, H, W, progress=progress)

    if not detections:
        progress("complete", "No detections found")
        return [], [], annotated

    # ── Stage 3: Quantify ────────────────────────────────────────────────────
    progress("analysing", "Running distress quantification...")
    dq = DistressQuantifier(px_per_mm=px_per_mm)
    results = dq.quantify_all(image, detections)

    # ── Stage 4: Map QuantifyResult → DistressRecord ─────────────────────────
    records: List[DistressRecord] = []

    for r in results:
        # ── geometry shared by both crack and pothole ─────────────────────────
        bbox_area_mm2 = r.bbox_w_mm * r.bbox_h_mm

        # ── crack-specific fields (None for pothole → default 0.0) ────────────
        width_mm = r.crack_width_mm or 0.0
        length_mm = r.crack_length_mm or 0.0
        branch_density = r.branch_density or 0.0
        loop_count = r.loop_count or 0
        fill_ratio = r.fill_ratio or 0.0
        orientation_deg = r.orientation_deg or 0.0

        # ── severity_metrics dict for DB storage ──────────────────────────────
        severity_metrics: Dict = {
            "severity_label": r.severity_label,
        }
        if r.crack_length_mm is not None:
            severity_metrics.update(
                {
                    "crack_width_mm": width_mm,
                    "crack_length_mm": length_mm,
                    "branch_density": branch_density,
                    "loop_count": loop_count,
                    "fill_ratio": fill_ratio,
                    "orientation_deg": orientation_deg,
                    "crack_category_confidence": r.crack_category_confidence,
                }
            )
        if r.pothole_equiv_diameter_mm is not None:
            severity_metrics.update(
                {
                    "equiv_diameter_mm": r.pothole_equiv_diameter_mm,
                    "depth_est_mm": r.pothole_depth_est_mm,
                }
            )

        # ── full topology metrics dict ────────────────────────────────────────
        metrics: Dict = {
            "skeleton_length_px": r.skeleton_length_px,
            "branch_points": r.branch_points,
            "end_points": r.end_points,
            "mask_area_mm2": r.mask_area_mm2,
            "bbox_w_mm": r.bbox_w_mm,
            "bbox_h_mm": r.bbox_h_mm,
        }

        records.append(
            DistressRecord(
                # identity
                class_name=r.distress_type,
                severity=r.severity,
                severity_label=r.severity_label,
                # geometry
                width_mm=width_mm,
                length_mm=length_mm,
                area_mm2=r.mask_area_mm2,
                perimeter_mm=0.0,  # not computed in seg pipeline
                # detection
                confidence=r.confidence,
                bbox=[r.bbox_x1, r.bbox_y1, r.bbox_x2, r.bbox_y2],
                bbox_dim=[r.bbox_w_mm, r.bbox_h_mm],
                bbox_area_mm2=bbox_area_mm2,
                # topology
                branch_density=branch_density,
                loop_count=loop_count,
                fill_ratio=fill_ratio,
                shape_complexity=0.0,  # not computed in seg pipeline
                orientation_deg=orientation_deg,
                aspect_ratio=1.0,  # not stored in QuantifyResult
                # crack classification
                crack_category_confidence=r.crack_category_confidence,
                # pothole
                pothole_equiv_diameter_mm=r.pothole_equiv_diameter_mm,
                pothole_depth_est_mm=r.pothole_depth_est_mm,
                pothole_count=r.pothole_count,
                # ASTM
                astm_quantity=r.astm_quantity,
                astm_unit=r.astm_unit,
                # dicts
                metrics=metrics,
                severity_metrics=severity_metrics,
            )
        )

    progress("uploading", f"Uploading results ({len(records)} distress(es) found)...")
    return detections, records, annotated
