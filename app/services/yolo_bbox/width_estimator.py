"""
width_estimator.py  (updated)
==============================
Drop-in replacement for the original.  All existing call-sites work
unchanged because the returned dict has the same keys.

What changed
------------
• Calls `compute_skeleton_metrics()` to get topology (branch_density,
  loop_count, fill_ratio, perimeter_mm, texture_cv, …).

• Calls `classify_severity_astm()` (severity_engine.py) instead of the
  old `classify_severity()`, so every class now returns a proper ASTM
  severity instead of "n/a".

• Adds new keys to the returned dict:
    "metrics"          → SkeletonMetrics.to_dict()  (full topology)
    "astm_quantity"    → float  (m or m²  for DV curve lookup)
    "astm_unit"        → "m" | "m²" | "count"
    "equiv_diameter_mm"→ float  (potholes only, else None)
    "depth_est_mm"     → float  (potholes / rutting only, else None)

Old keys still present (unchanged):
    width_mm, length_mm, area_mm2, severity, severity_label,
    binary_mask, skeleton, sato_response, class_name
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional
import cv2
import numpy as np

from app.services.yolo_bbox.skeletonization import (
    # SEVERITY_LABELS,
    SkeletonMetrics,
    compute_perimeter,
    compute_skeleton_metrics,
    compute_texture_cv,
    morphological_refine,
    sato_tubeness,
    skeletonize,
)
from app.services.yolo_bbox.severity_engine import classify_severity_astm

# Default sample-unit area used when the caller doesn't supply it
# (paper: 3.5 m lane-width × 10 m length = 35 m² = 35 000 000 mm²)
_DEFAULT_SAMPLE_UNIT_MM2 = 35_000_000.0


class CrackWidthEstimator:
    """
    End-to-end distress measurement pipeline.

    Parameters
    ----------
    px_per_mm            : camera calibration (paper default 1.0)
    sigmas               : Sato Tubeness σ scales
    dilation_k           : morphological dilation kernel size
    erosion_k            : morphological erosion kernel size
    opening_k            : morphological opening kernel size
    threshold            : Sato response binarisation threshold
    sample_unit_area_mm2 : total area of the pavement sample unit in mm²
                           (used for patching area-fraction severity)
    depth_mm             : optional real rut/pothole depth from sensor (mm)
    """

    def __init__(
        self,
        px_per_mm: float = 1.0,
        sigmas: Optional[List[float]] = None,
        dilation_k: int = 3,
        erosion_k: int = 3,
        opening_k: int = 3,
        threshold: float = 0.1,
        sample_unit_area_mm2: float = _DEFAULT_SAMPLE_UNIT_MM2,
        depth_mm: Optional[float] = None,
    ):
        self.px_per_mm = px_per_mm
        self.sigmas = sigmas or [1, 2, 3, 4, 5, 6, 8, 10]
        self.dilation_k = dilation_k
        self.erosion_k = erosion_k
        self.opening_k = opening_k
        self.threshold = threshold
        self.sample_unit_area_mm2 = sample_unit_area_mm2
        self.depth_mm = depth_mm  # real depth sensor, if available

    # ──────────────────────────────────────────────────────────────────────────
    def estimate(
        self,
        crop_bgr: np.ndarray,
        class_name: str,
    ) -> Dict:
        """
        Run the full measurement + severity pipeline on a BGR crop.

        Parameters
        ----------
        crop_bgr   : BGR numpy array cropped to the detection bounding box
        class_name : one of the 7 ASTM distress classes (case-insensitive)

        Returns
        -------
        dict — all original keys plus new keys (see module docstring)
        """
        if crop_bgr is None or crop_bgr.size == 0:
            return self._empty_result(class_name)

        # ── Step 1: Grayscale ────────────────────────────────────────────────
        gray = (
            cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
            if crop_bgr.ndim == 3
            else crop_bgr.copy()
        )

        # ── Step 2: Sato Tubeness filter (paper Eq. 1–3) ────────────────────
        sato_resp = sato_tubeness(gray, self.sigmas)

        # ── Step 3: Binarise ─────────────────────────────────────────────────
        binary = (sato_resp > self.threshold).astype(np.uint8) * 255

        # ── Step 4: Morphological refinement (paper Eq. 4–6) ────────────────
        binary_refined = morphological_refine(
            binary, self.dilation_k, self.erosion_k, self.opening_k
        )

        # ── Step 5: Skeletonize (paper Section 3.2.2) ───────────────────────
        skeleton = skeletonize(binary_refined)

        # ── Step 6: Compute all metrics ──────────────────────────────────────
        sm: SkeletonMetrics = compute_skeleton_metrics(
            binary_refined, skeleton, self.px_per_mm, gray_crop=gray
        )
        texture_cv = compute_texture_cv(gray, binary_refined)

        # ── Step 7: ASTM severity (full, per class) ──────────────────────────
        cn = class_name.lower().strip()
        bbox_h, bbox_w = crop_bgr.shape[:2]
        bbox_area_mm2 = (bbox_w * bbox_h) / (self.px_per_mm**2)

        sev_result = classify_severity_astm(
            cn,
            # linear / edge
            width_mm=sm.width_mm,
            length_mm=sm.length_mm,
            perimeter_mm=sm.perimeter_mm,
            # alligator
            branch_density=sm.branch_density,
            loop_count=sm.loop_count,
            fill_ratio=sm.fill_ratio,
            # patching
            sample_unit_area_mm2=self.sample_unit_area_mm2,
            texture_cv=texture_cv,
            # pothole / rutting
            mask_area_mm2=sm.area_mm2,
            bbox_area_mm2=bbox_area_mm2,
            depth_mm=self.depth_mm,
            # rutting width proxy
            rut_width_mm=sm.width_mm,
            rut_length_mm=sm.length_mm,
        )

        # ── Step 8: ASTM measurement quantity (for DV curve lookup) ──────────
        astm_quantity, astm_unit = self._astm_quantity(cn, sm)

        # ── Step 9: Pothole extras ────────────────────────────────────────────
        equiv_diam_mm = None
        depth_est_mm = None
        if cn == "pothole":
            equiv_diam_mm = math.sqrt(4.0 * sm.area_mm2 / math.pi)
            depth_est_mm = (
                self.depth_mm
                if self.depth_mm is not None
                else max(13.0, 0.15 * equiv_diam_mm)
            )
        elif cn == "rutting":
            depth_est_mm = sev_result.metrics.get("depth_mm")

        return {
            # ── original keys (unchanged) ─────────────────────────────────
            "width_mm": sm.width_mm,
            "length_mm": sm.length_mm,
            "area_mm2": sm.area_mm2,
            "severity": sev_result.severity,
            "severity_label": sev_result.label,
            "binary_mask": binary_refined,
            "skeleton": skeleton,
            "sato_response": sato_resp,
            "class_name": class_name,
            # ── new keys ──────────────────────────────────────────────────
            "metrics": sm.to_dict(),  # full topology dict
            "severity_metrics": sev_result.metrics,  # values that drove severity
            "astm_quantity": astm_quantity,
            "astm_unit": astm_unit,
            "perimeter_mm": sm.perimeter_mm,
            "branch_density": sm.branch_density,
            "loop_count": sm.loop_count,
            "fill_ratio": sm.fill_ratio,
            "shape_complexity": sm.shape_complexity,
            "orientation_deg": sm.orientation_deg,
            "aspect_ratio": sm.aspect_ratio,
            "texture_cv": texture_cv,
            "equiv_diameter_mm": equiv_diam_mm,
            "depth_est_mm": depth_est_mm,
            "bbox_area_mm2": bbox_area_mm2,
        }

    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _astm_quantity(cn: str, sm: SkeletonMetrics) -> tuple[float, str]:
        """
        Return (quantity, unit) for the ASTM DV-curve lookup.

        ASTM D6433-07 measurement units per distress type:
          alligator cracking  → m²  (area)
          longitudinal crack  → m   (length)
          transverse crack    → m   (length)
          edge cracking       → m   (length)
          patching            → m²  (area)
          pothole             → count
          rutting             → m²  (area)
        """
        if cn in ("longitudinal cracking", "transverse cracking", "edge cracking"):
            return sm.length_mm / 1000.0, "m"
        elif cn in ("alligator cracking", "patching", "rutting"):
            return sm.area_mm2 / 1_000_000.0, "m²"
        elif cn == "pothole":
            equiv_diam = math.sqrt(4.0 * sm.area_mm2 / math.pi)
            count = max(1.0, sm.area_mm2 / 500_000.0) if equiv_diam > 750 else 1.0
            return count, "count"
        return 0.0, "n/a"

    # ──────────────────────────────────────────────────────────────────────────
    def _empty_result(self, class_name: str) -> Dict:
        return {
            "width_mm": 0.0,
            "length_mm": 0.0,
            "area_mm2": 0.0,
            "severity": "n/a",
            "severity_label": "N/A — empty crop",
            "binary_mask": np.zeros((1, 1), dtype=np.uint8),
            "skeleton": np.zeros((1, 1), dtype=np.uint8),
            "sato_response": np.zeros((1, 1), dtype=np.float32),
            "class_name": class_name,
            "metrics": {},
            "severity_metrics": {},
            "astm_quantity": 0.0,
            "astm_unit": "n/a",
            "perimeter_mm": 0.0,
            "branch_density": 0.0,
            "loop_count": 0,
            "fill_ratio": 0.0,
            "shape_complexity": 0.0,
            "orientation_deg": 0.0,
            "aspect_ratio": 1.0,
            "texture_cv": 0.0,
            "equiv_diameter_mm": None,
            "depth_est_mm": None,
            "bbox_area_mm2": 0.0,
        }
