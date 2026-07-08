"""
bbox_model.py  (updated)
=========================
Unchanged public API:
    infer_image_bbox_model(image, px_per_mm, on_progress)
    → (detections, records, annotated)

What changed
------------
• DistressRecord now carries every new metric from width_estimator.
• `detections` list entries carry every new key too.
• A printed/returned `summary` dict groups totals by class + severity.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import time

import cv2

from app.core.models import BBOX_MODEL, BBOX_MODEL_PATH
from app.services.yolo_bbox.width_estimator import CrackWidthEstimator
from app.services.yolo_bbox.config_bbox import CLASS_NAMES, bbox_cfg

# ─────────────────────────────────────────────────────────────────────────────
# DistressRecord  (extended)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DistressRecord:
    """One distress instance — every metric computed by the pipeline."""

    # identity
    class_name: str
    severity: str  # "low" | "medium" | "high" | "n/a"
    severity_label: str = ""  # human-readable ASTM label

    # geometry (mm)
    width_mm: float = 0.0
    length_mm: float = 0.0
    area_mm2: float = 0.0
    perimeter_mm: float = 0.0

    # detection
    confidence: float = 0.0
    bbox: List[int] = field(default_factory=list)  # [x1,y1,x2,y2]
    bbox_dim: List[int] = field(default_factory=list)  # [w_px, h_px]
    bbox_area_mm2: float = 0.0

    # topology (crack / alligator)
    branch_density: float = 0.0
    loop_count: int = 0
    fill_ratio: float = 0.0
    shape_complexity: float = 0.0
    orientation_deg: float = 0.0
    aspect_ratio: float = 1.0
    crack_category_confidence: int = 0

    # texture (patching)
    texture_cv: float = 0.0

    # pothole
    pothole_equiv_diameter_mm: Optional[float] = None
    pothole_depth_est_mm: Optional[float] = None
    pothole_count: Optional[int] = None

    # ASTM measurement unit
    astm_quantity: float = 0.0
    astm_unit: str = ""

    # full topology dict (for downstream use / JSON export)
    metrics: Dict = field(default_factory=dict)
    severity_metrics: Dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Inference
# ─────────────────────────────────────────────────────────────────────────────


def infer_image_bbox_model(
    image,
    px_per_mm: float,
    on_progress: Optional[Callable[[str, str], None]] = None,
    sample_unit_area_mm2: float = 0.0,
    depth_mm: Optional[float] = None,  # real depth sensor
):
    """
    Run the full pipeline on a single BGR numpy image.

    Parameters
    ----------
    image                : BGR numpy array
    px_per_mm            : pixels per mm (camera calibration)
    on_progress          : optional callback(step, detail)
    sample_unit_area_mm2 : pavement sample-unit area in mm²
                           (used for patching severity)
    depth_mm             : real rut / pothole depth from sensor (mm);
                           if None → empirical proxy is used

    Returns
    -------
    detections : List[Dict]       — one dict per detection, all metrics
    records    : List[DistressRecord]
    annotated  : np.ndarray       — BGR image with YOLO boxes drawn
    """

    def progress(step: str, detail: str = ""):
        if on_progress:
            on_progress(step, detail)

    if image is None:
        raise ValueError("image must not be None")
    if not BBOX_MODEL_PATH:
        raise RuntimeError("BBOX_MODEL_PATH does not exist")
    if BBOX_MODEL is None:
        raise RuntimeError("BBOX_MODEL not loaded at worker startup")

    est = CrackWidthEstimator(
        px_per_mm=px_per_mm,
        sample_unit_area_mm2=sample_unit_area_mm2,
        depth_mm=depth_mm,
    )

    t0 = time.time()
    image_bgr = image
    h, w = image_bgr.shape[:2]

    # ── Stage 1: YOLO detection ──────────────────────────────────────────────
    progress("detecting", "Running YOLO detection...")
    yolo_results = BBOX_MODEL.predict(
        source=image_bgr,
        imgsz=bbox_cfg.img_size,
        conf=bbox_cfg.CONF_THRESHOLD,
        iou=bbox_cfg.IOU_THRESHOLD,
    )
    annotated = yolo_results[0].plot()

    detections: List[Dict] = []
    records: List[DistressRecord] = []

    if not (yolo_results and yolo_results[0].boxes is not None):
        progress("complete", "No detections found")
        return detections, records, annotated

    boxes = yolo_results[0].boxes
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    clsids = boxes.cls.cpu().numpy().astype(int)
    total = len(xyxy)

    progress("analysing", f"Analysing {total} detection(s)...")

    for i in range(total):
        x1, y1, x2, y2 = map(int, xyxy[i])
        conf_val = float(confs[i])
        cls_id = int(clsids[i])
        cls_name = CLASS_NAMES[cls_id] if cls_id < len(CLASS_NAMES) else "unknown"

        progress("analysing", f"Processing {i + 1}/{total}: {cls_name}")

        # ── Width / topology / severity ──────────────────────────────────────
        crop = image_bgr[max(0, y1) : min(h, y2), max(0, x1) : min(w, x2)]
        wr = est.estimate(crop, class_name=cls_name)

        # Flatten into detection dict (all keys)
        det_dict = {
            # identity
            "class_id": cls_id,
            "class_name": cls_name,
            "confidence": conf_val,
            "bbox": [x1, y1, x2, y2],
            "bbox_dim": [x2 - x1, y2 - y1],
            # geometry
            "width_mm": wr["width_mm"],
            "length_mm": wr["length_mm"],
            "area_mm2": wr["area_mm2"],
            "perimeter_mm": wr["perimeter_mm"],
            "bbox_area_mm2": wr["bbox_area_mm2"],
            # severity
            "severity": wr["severity"],
            "severity_label": wr["severity_label"],
            # topology
            "branch_density": wr["branch_density"],
            "loop_count": wr["loop_count"],
            "fill_ratio": wr["fill_ratio"],
            "shape_complexity": wr["shape_complexity"],
            "orientation_deg": wr["orientation_deg"],
            "aspect_ratio": wr["aspect_ratio"],
            "texture_cv": wr["texture_cv"],
            # pothole / rutting extras
            "pothole_equiv_diameter_mm": wr["equiv_diameter_mm"],
            "pothole_depth_est_mm": wr["depth_est_mm"],
            # ASTM
            "astm_quantity": wr["astm_quantity"],
            "astm_unit": wr["astm_unit"],
            # full dicts
            "metrics": wr["metrics"],
            "severity_metrics": wr["severity_metrics"],
        }
        detections.append(det_dict)

        records.append(
            DistressRecord(
                class_name=cls_name,
                severity=wr["severity"],
                severity_label=wr["severity_label"],
                width_mm=wr["width_mm"],
                length_mm=wr["length_mm"],
                area_mm2=wr["area_mm2"],
                perimeter_mm=wr["perimeter_mm"],
                confidence=conf_val,
                bbox=[x1, y1, x2, y2],
                bbox_dim=[x2 - x1, y2 - y1],
                bbox_area_mm2=wr["bbox_area_mm2"],
                branch_density=wr["branch_density"],
                loop_count=wr["loop_count"],
                fill_ratio=wr["fill_ratio"],
                shape_complexity=wr["shape_complexity"],
                orientation_deg=wr["orientation_deg"],
                aspect_ratio=wr["aspect_ratio"],
                texture_cv=wr["texture_cv"],
                pothole_equiv_diameter_mm=wr["equiv_diameter_mm"],
                pothole_depth_est_mm=wr["depth_est_mm"],
                astm_quantity=wr["astm_quantity"],
                astm_unit=wr["astm_unit"],
                metrics=wr["metrics"],
                severity_metrics=wr["severity_metrics"],
            )
        )

    elapsed = time.time() - t0
    progress(
        "uploading",
        f"Done — {len(records)} distress(es) in {elapsed:.2f}s",
    )
    return detections, records, annotated
