import math
from typing import Dict, List, Optional

import numpy as np

from app.services.yolo_seg.helpers import (
    QuantifyResult,
    _alligator_severity,
    _classify_crack_type,
    _estimate_pothole_depth,
    _linear_crack_severity,
    _pothole_severity,
    _principal_orientation_and_aspect,
    _skeleton_stats,
    _skeletonize,
)


class DistressQuantifier:
    """
    Quantifies YOLO segmentation masks for classes ["Crack", "pothole"].

    Parameters
    ----------
    px_per_mm : float
        Camera calibration: how many pixels correspond to 1 mm in the world.
        Paper default: 1.0  (camera resolves 1 px = 1 mm on the pavement).
        Change this to match your camera setup.
    sato_sigmas : list of float
        Multi-scale σ values for Sato Tubeness filter (crack segmentation
        refinement). If skimage is unavailable, Sato is skipped.
    depth_sensor_fn : callable, optional
        If provided, called as depth_sensor_fn(mask) → depth_mm per pothole.
        If None, the empirical 0.15·diameter proxy is used.
    """

    def __init__(
        self,
        px_per_mm: float = 1.0,
        sato_sigmas: Optional[List[float]] = None,
        depth_sensor_fn=None,
    ):
        if px_per_mm <= 0:
            raise ValueError(f"px_per_mm must be > 0, got {px_per_mm}")
        self.px_per_mm = px_per_mm
        self.mm_per_px = 1.0 / px_per_mm
        self.sato_sigmas = sato_sigmas or [1, 2, 3, 4, 5, 6, 8, 10]
        self.depth_sensor_fn = depth_sensor_fn

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def quantify_all(
        self,
        image_bgr: np.ndarray,
        detections: List[Dict],
    ) -> List[QuantifyResult]:
        """
        Quantify every detection.

        Parameters
        ----------
        image_bgr  : H×W×3 BGR image (as read by cv2.imread)
        detections : list of dicts, each with:
            {
              "class_name" : "Crack" or "pothole"  (case-insensitive),
              "mask"       : np.ndarray uint8 H×W  (0=background, 255=distress),
              "bbox"       : [x1, y1, x2, y2]  (optional, computed if absent),
              "confidence" : float              (optional YOLO detection conf)
            }

        Returns
        -------
        list of QuantifyResult, one per detection
        """
        results = []
        for idx, det in enumerate(detections):
            cls = det["class_name"].lower().strip()
            mask = det["mask"].astype(np.uint8)
            mask = np.where(mask > 0, 255, 0).astype(np.uint8)

            if "bbox" in det and det["bbox"] is not None:
                x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
            else:
                ys, xs = np.nonzero(mask)
                if len(xs) == 0:
                    continue
                x1, y1, x2, y2 = xs.min(), ys.min(), xs.max(), ys.max()

            if cls in ("crack", "cracks"):
                result = self._quantify_crack(
                    idx, mask, image_bgr, x1, y1, x2, y2, det["confidence"]
                )
            elif cls in ("pothole", "potholes", "pot hole"):
                result = self._quantify_pothole(
                    idx, mask, x1, y1, x2, y2, det["confidence"]
                )
            else:
                # Unknown class: skip gracefully
                continue

            results.append(result)
        return results

    def quantify_single(
        self,
        image_bgr: np.ndarray,
        class_name: str,
        mask: np.ndarray,
        bbox: Optional[List[int]] = None,
        detection_index: int = 0,
    ) -> QuantifyResult:
        """Convenience wrapper for a single detection."""
        det = {
            "class_name": class_name,
            "mask": mask,
            "bbox": bbox,
        }
        results = self.quantify_all(image_bgr, [det])
        if results:
            results[0].detection_index = detection_index
            return results[0]
        raise ValueError("Empty mask or unknown class.")

    # ─────────────────────────────────────────────────────────────────
    # Crack quantification
    # ─────────────────────────────────────────────────────────────────

    def _quantify_crack(
        self,
        idx: int,
        mask: np.ndarray,
        image_bgr: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        confidence: int,
    ) -> QuantifyResult:

        mm = self.mm_per_px  # shorthand

        # Bounding box dimensions in mm
        bbox_w_mm = (x2 - x1) * mm
        bbox_h_mm = (y2 - y1) * mm

        # Mask area
        mask_area_px = float(np.count_nonzero(mask))
        mask_area_mm2 = mask_area_px * (mm**2)

        # ── Skeletonize ──────────────────────────────────────────────
        skeleton = _skeletonize(mask)
        skel_len_px, branch_pts, end_pts, loop_count = _skeleton_stats(skeleton)

        # ── Width = A/B (paper Eq.7) then convert ────────────────────
        skel_safe = max(skel_len_px, 1.0)
        width_px = mask_area_px / skel_safe
        width_mm = width_px * mm

        # ── Length in mm ─────────────────────────────────────────────
        length_mm = skel_len_px * mm

        # ── Orientation & aspect ──────────────────────────────────────
        orientation_deg, aspect_ratio = _principal_orientation_and_aspect(mask)

        # ── Fill ratio ────────────────────────────────────────────────
        fill_ratio = mask_area_px / skel_safe
        branch_density = branch_pts / skel_safe

        # ── Classify crack type ───────────────────────────────────────
        category, conf = _classify_crack_type(
            mask,
            skeleton,
            skel_len_px,
            branch_pts,
            loop_count,
            fill_ratio,
            aspect_ratio,
            orientation_deg,
        )

        # ── Severity ──────────────────────────────────────────────────
        if category == "alligator":
            severity, severity_label = _alligator_severity(
                branch_density, loop_count, fill_ratio
            )
            # ASTM unit for alligator = m²
            astm_quantity = mask_area_mm2 / 1e6
            astm_unit = "m²"
        else:
            severity, severity_label = _linear_crack_severity(width_mm)
            # ASTM unit for longitudinal/transverse = m (length)
            astm_quantity = length_mm / 1000.0
            astm_unit = "m"

        return QuantifyResult(
            detection_index=idx,
            raw_class="Crack",
            distress_type=category,
            severity=severity,
            severity_label=severity_label,
            mask_area_mm2=mask_area_mm2,
            bbox_x1=x1,
            bbox_y1=y1,
            bbox_x2=x2,
            bbox_y2=y2,
            bbox_w_mm=bbox_w_mm,
            bbox_h_mm=bbox_h_mm,
            crack_width_mm=width_mm,
            crack_length_mm=length_mm,
            skeleton_length_px=skel_len_px,
            branch_density=branch_density,
            loop_count=loop_count,
            fill_ratio=fill_ratio,
            orientation_deg=orientation_deg,
            crack_category_confidence=conf,
            branch_points=branch_pts,
            end_points=end_pts,
            astm_quantity=astm_quantity,
            astm_unit=astm_unit,
            confidence=confidence,
        )

    # ─────────────────────────────────────────────────────────────────
    # Pothole quantification
    # ─────────────────────────────────────────────────────────────────

    def _quantify_pothole(
        self,
        idx: int,
        mask: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        confidence: int,
    ) -> QuantifyResult:

        mm = self.mm_per_px

        bbox_w_mm = (x2 - x1) * mm
        bbox_h_mm = (y2 - y1) * mm

        mask_area_px = float(np.count_nonzero(mask))
        mask_area_mm2 = mask_area_px * (mm**2)

        # ── Equivalent circular diameter  d = √(4A/π) ────────────────
        # This is the standard field conversion (ASTM X1.17.1.2):
        # "area in sq ft divided by 0.5 m² → equiv number of holes"
        # We reverse: find the diameter of a circle with the same area.
        equiv_diam_mm = math.sqrt(4.0 * mask_area_mm2 / math.pi)

        # ── Depth estimate ────────────────────────────────────────────
        if self.depth_sensor_fn is not None:
            depth_mm = float(self.depth_sensor_fn(mask))
        else:
            depth_mm = _estimate_pothole_depth(mask_area_mm2, equiv_diam_mm)

        # ── Severity (ASTM Table X1.1) ────────────────────────────────
        severity, severity_label = _pothole_severity(equiv_diam_mm, depth_mm)

        # ── ASTM unit: count (number of potholes) ────────────────────
        # For potholes > 750 mm diam, ASTM says divide area by 0.5 m²
        astm_unit = "count"
        if equiv_diam_mm > 750.0:
            astm_quantity = max(1.0, mask_area_mm2 / 500_000.0)
        else:
            astm_quantity = 1.0

        return QuantifyResult(
            detection_index=idx,
            raw_class="pothole",
            distress_type="pothole",
            severity=severity,
            severity_label=severity_label,
            mask_area_mm2=mask_area_mm2,
            bbox_x1=x1,
            bbox_y1=y1,
            bbox_x2=x2,
            bbox_y2=y2,
            bbox_w_mm=bbox_w_mm,
            bbox_h_mm=bbox_h_mm,
            pothole_equiv_diameter_mm=equiv_diam_mm,
            pothole_depth_est_mm=depth_mm,
            pothole_count=int(astm_quantity),
            astm_quantity=astm_quantity,
            astm_unit=astm_unit,
            confidence=confidence,
        )
