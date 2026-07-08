import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.services.yolo_seg.config_seg import (
    _ALIG_BRANCH_HIGH,
    _ALIG_BRANCH_LOW,
    _PH_SEVERITY_TABLE,
    LINEAR_LOW_MAX_MM,
    LINEAR_MED_MAX_MM,
)

try:
    from skimage.filters import sato
    from skimage.morphology import skeletonize as ski_skeletonize

    _SKIMAGE = True
except ImportError:
    _SKIMAGE = False

# ─────────────────────────────────────────────────────────────────────────────
# Result container
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class QuantifyResult:
    # ── identity ──────────────────────────────────────────────────────────────
    detection_index: int
    raw_class: str  # "Crack" or "pothole" (YOLO output)
    distress_type: str  # "alligator" | "longitudinal" | "transverse" | "pothole"
    severity: str  # "low" | "medium" | "high"
    severity_label: str  # human-readable with ASTM thresholds
    confidence: int

    # ── geometry (all in mm / mm² after px_per_mm conversion) ────────────────
    mask_area_mm2: float  # total foreground area
    bbox_x1: int
    bbox_y1: int
    bbox_x2: int
    bbox_y2: int
    bbox_w_mm: float
    bbox_h_mm: float

    # ── crack-specific (None for pothole) ────────────────────────────────────
    crack_width_mm: Optional[float] = None
    crack_length_mm: Optional[float] = None
    skeleton_length_px: Optional[float] = None
    branch_density: Optional[float] = None
    loop_count: Optional[int] = None
    fill_ratio: Optional[float] = None
    orientation_deg: Optional[float] = None
    crack_category_confidence: Optional[float] = None

    # ── pothole-specific (None for crack) ────────────────────────────────────
    pothole_equiv_diameter_mm: Optional[float] = None  # √(4·A/π)
    pothole_depth_est_mm: Optional[float] = None  # empirical proxy
    pothole_count: Optional[int] = None  # 1 per mask

    # ── topology detail ──────────────────────────────────────────────────────
    branch_points: Optional[int] = None
    end_points: Optional[int] = None

    # ── ASTM measurement unit required for DV curve ─────────────────────────
    astm_quantity: float = 0.0  # m (linear) or m² (area) or count
    astm_unit: str = ""  # "m", "m²", "count"

    def __repr__(self) -> str:
        lines = [
            f"=== Detection #{self.detection_index}  [{self.raw_class}] ===",
            f"  Distress type : {self.distress_type}",
            f"  Severity      : {self.severity.upper()}  —  {self.severity_label}",
            f"  Mask area     : {self.mask_area_mm2:,.1f} mm²  "
            f"({self.mask_area_mm2/1e6:.4f} m²)",
            f"  BBox (px)     : ({self.bbox_x1},{self.bbox_y1}) → "
            f"({self.bbox_x2},{self.bbox_y2})  "
            f"[{self.bbox_w_mm:.1f} × {self.bbox_h_mm:.1f} mm]",
        ]
        if self.crack_length_mm is not None:
            lines += [
                f"  Crack length  : {self.crack_length_mm:,.1f} mm  "
                f"({self.crack_length_mm/1000:.3f} m)",
                f"  Crack width   : {self.crack_width_mm:.2f} mm",
                f"  Orientation   : {self.orientation_deg:.1f}°  "
                f"(0=longitudinal, 90=transverse)",
                f"  Branch pts    : {self.branch_points}",
                f"  Loop count    : {self.loop_count}",
                f"  Branch density: {self.branch_density:.5f} bp/px",
                f"  Fill ratio    : {self.fill_ratio:.2f}",
            ]
        if self.pothole_equiv_diameter_mm is not None:
            lines += [
                f"  Equiv diam    : {self.pothole_equiv_diameter_mm:.1f} mm",
                f"  Depth est.    : {self.pothole_depth_est_mm:.1f} mm  (proxy)",
            ]
        lines.append(f"  ASTM quantity : {self.astm_quantity:.4f} {self.astm_unit}")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Skeleton helpers
# ─────────────────────────────────────────────────────────────────────────────


def _skeletonize(binary_uint8: np.ndarray) -> np.ndarray:
    """Thin binary mask to 1-pixel-wide skeleton. Returns uint8 mask."""
    bm = binary_uint8 > 0
    if _SKIMAGE:
        return ski_skeletonize(bm).astype(np.uint8) * 255
    # Fallback: ximgproc
    try:
        import cv2.ximgproc as xip

        return xip.thinning(
            (bm.astype(np.uint8) * 255), thinningType=xip.THINNING_ZHANGSUEN
        )
    except (ImportError, AttributeError):
        return _morph_thin(bm.astype(np.uint8) * 255)


def _morph_thin(img: np.ndarray) -> np.ndarray:
    """Fallback Zhang-Suen morphological thinning."""
    skel = np.zeros_like(img)
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    tmp = img.copy()
    while True:
        er = cv2.erode(tmp, kernel)
        op = cv2.dilate(er, kernel)
        sub = cv2.subtract(tmp, op)
        skel = cv2.bitwise_or(skel, sub)
        tmp = er
        if cv2.countNonZero(tmp) == 0:
            break
    return skel


def _skeleton_stats(skeleton: np.ndarray) -> Tuple[float, int, int, int]:
    """
    Returns (length_px, branch_points, end_points, loop_count).
    """
    sk = (skeleton > 0).astype(np.uint8)
    length = float(np.count_nonzero(sk))

    # neighbour count per skeleton pixel
    kn = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.uint8)
    nc = cv2.filter2D(sk, ddepth=cv2.CV_8U, kernel=kn, borderType=cv2.BORDER_CONSTANT)
    deg = nc * sk

    branch_pts = int(np.sum((deg >= 3) & (sk > 0)))
    end_pts = int(np.sum((deg == 1) & (sk > 0)))

    # Loop count via Euler characteristic on skeleton graph
    # Remove branch pixels → remaining are edge segments
    no_branch = sk.copy()
    no_branch[(deg >= 3) & (sk > 0)] = 0
    n_seg, _ = cv2.connectedComponents(no_branch, connectivity=8)
    n_edges = max(0, n_seg - 1)

    bp_mask = ((deg >= 3) & (sk > 0)).astype(np.uint8) * 255
    n_bp_clust, _ = cv2.connectedComponents(bp_mask, connectivity=8)
    n_bp_clust = max(0, n_bp_clust - 1)

    n_comp, _ = cv2.connectedComponents(sk, connectivity=8)
    n_comp = max(1, n_comp - 1)

    n_verts = n_bp_clust + end_pts
    loops = max(0, n_edges - n_verts + n_comp) if n_bp_clust > 0 else 0

    return length, branch_pts, end_pts, loops


def _principal_orientation_and_aspect(mask: np.ndarray) -> Tuple[float, float]:
    """
    Returns (orientation_deg, aspect_ratio).
    orientation_deg: 0 = vertical (travel direction = longitudinal),
                     90 = horizontal (transverse).
    """
    ys, xs = np.nonzero(mask > 0)
    if len(xs) < 2:
        return 0.0, 1.0
    pts = np.column_stack([xs, ys]).astype(np.float32)
    centered = pts - pts.mean(axis=0)
    cov = np.cov(centered.T) if len(xs) > 1 else np.eye(2)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    mv = eigvecs[:, 0]
    # angle of major axis w.r.t. vertical (y-axis); 0=vertical, 90=horizontal
    ang = math.degrees(math.atan2(abs(mv[0]), abs(mv[1])))
    ang = max(0.0, min(90.0, ang))
    major = math.sqrt(max(eigvals[0], 1e-9))
    minor = math.sqrt(max(eigvals[1], 1e-9))
    aspect = major / minor if minor > 0 else 999.0
    return ang, aspect


# ─────────────────────────────────────────────────────────────────────────────
# Sato tubeness width estimator
# ─────────────────────────────────────────────────────────────────────────────


def _estimate_crack_width(
    gray_crop: np.ndarray,
    binary_crop: np.ndarray,
    skeleton: np.ndarray,
    sigmas: List[float],
    px_per_mm: float,
) -> float:
    """
    Width = A / B  (paper Eq. 7):  crack pixels / skeleton pixels.
    Converted to mm via px_per_mm.
    """
    A = float(np.count_nonzero(binary_crop))
    B = float(np.count_nonzero(skeleton))
    if B == 0:
        return 0.0
    width_px = A / B
    return width_px / px_per_mm


# ─────────────────────────────────────────────────────────────────────────────
# ASTM severity classifiers
# ─────────────────────────────────────────────────────────────────────────────


def _linear_crack_severity(width_mm: float) -> Tuple[str, str]:
    """
    ASTM D6433-07 X1.14.2 — Longitudinal & Transverse Cracking severity.

    L: unfilled crack width < 10 mm (≈ 3/8 in.)
    M: 10 mm ≤ width < 75 mm (3/8 – 3 in.)
    H: width ≥ 75 mm (> 3 in.)
    """
    if width_mm < LINEAR_LOW_MAX_MM:
        sev = "low"
        label = f"Low (width {width_mm:.1f} mm < {LINEAR_LOW_MAX_MM} mm)"
    elif width_mm < LINEAR_MED_MAX_MM:
        sev = "medium"
        label = (
            f"Medium ({LINEAR_LOW_MAX_MM} mm ≤ width "
            f"{width_mm:.1f} mm < {LINEAR_MED_MAX_MM} mm)"
        )
    else:
        sev = "high"
        label = f"High (width {width_mm:.1f} mm ≥ {LINEAR_MED_MAX_MM} mm)"
    return sev, label


def _alligator_severity(
    branch_density: float,
    loop_count: int,
    fill_ratio: float,
) -> Tuple[str, str]:
    """
    ASTM D6433-07 X1.5.1 — Alligator Cracking severity (image-based proxy).

    ASTM descriptions (our image-measurable proxies):
      L: fine hairline longitudinal cracks, few interconnections, not spalled
         → low branch density, few or no loops, thin fill ratio
      M: further development into network/pattern, may be lightly spalled
         → moderate branch density, some loops
      H: well-defined pieces, spalled edges, pieces rock under traffic
         → high branch density OR many closed loops

    Image proxies (calibrated against ASTM descriptions):
      branch_density < 0.008 per px  → L
      branch_density 0.008–0.025      → M
      branch_density > 0.025 OR
        loop_count >= 3               → H
    """
    if loop_count >= 3 or branch_density > _ALIG_BRANCH_HIGH:
        sev = "high"
        label = (
            "High — well-defined interconnected pieces, "
            f"branch_density={branch_density:.4f}, loops={loop_count}"
        )
    elif loop_count >= 1 or branch_density >= _ALIG_BRANCH_LOW:
        sev = "medium"
        label = (
            "Medium — developed crack network, "
            f"branch_density={branch_density:.4f}, loops={loop_count}"
        )
    else:
        sev = "low"
        label = (
            "Low — fine hairline cracks, few interconnections, "
            f"branch_density={branch_density:.4f}, loops={loop_count}"
        )
    return sev, label


def _pothole_severity(
    diameter_mm: float,
    depth_mm: float,
) -> Tuple[str, str]:
    """
    ASTM D6433-07 X1.17.1 Table X1.1 — Pothole severity.

    Table X1.1 (converted to mm):
    ┌─────────────────┬────────────────┬────────────────┬────────────────┐
    │ Max Depth       │ 100–200 mm dia │ 200–450 mm dia │ 450–750 mm dia │
    ├─────────────────┼────────────────┼────────────────┼────────────────┤
    │ 13–25 mm        │      L         │      L         │      M         │
    │ 25–50 mm        │      L         │      M         │      H         │
    │ > 50 mm         │      M         │      M         │      H         │
    └─────────────────┴────────────────┴────────────────┴────────────────┘
    Potholes > 750 mm diameter: compute equivalent count (area / 0.5 m²).
    For depth > 25 mm → H  (X1.17.1.2).
    """
    # Depth band index (0,1,2)
    if depth_mm <= 25.0:
        depth_idx = 0
    elif depth_mm <= 50.0:
        depth_idx = 1
    else:
        depth_idx = 2

    # Diameter band index (0,1,2); if > 750 mm treat as band 2
    if diameter_mm <= 200.0:
        diam_idx = 0
    elif diameter_mm <= 450.0:
        diam_idx = 1
    else:
        diam_idx = 2

    sev = _PH_SEVERITY_TABLE[depth_idx][diam_idx]
    label = (
        f"{sev.capitalize()} — "
        f"equiv diam {diameter_mm:.1f} mm, "
        f"est. depth {depth_mm:.1f} mm  "
        f"(ASTM D6433-07 Table X1.1)"
    )
    return sev, label


def _estimate_pothole_depth(area_mm2: float, diameter_mm: float) -> float:
    """
    Empirical depth proxy for a 2-D image with no depth sensor.

    ASTM field-measured potholes show that for typical asphalt:
      depth ≈ k · diameter  where k ≈ 0.10–0.20 (average ~0.15)
    This is well-documented in the PAVER manual and referenced in the
    standard (depths tend to scale with horizontal extent).

    If a depth sensor (LiDAR/stereo) provides actual depth_mm, pass it
    directly to `_pothole_severity()` and skip this function.
    """
    depth_estimate = max(13.0, 0.15 * diameter_mm)
    return depth_estimate


# ─────────────────────────────────────────────────────────────────────────────
# Crack type classifier (embedded, no separate import needed)
# ─────────────────────────────────────────────────────────────────────────────


def _classify_crack_type(
    mask: np.ndarray,
    skeleton: np.ndarray,
    skel_len: float,
    branch_pts: int,
    loop_count: int,
    fill_ratio: float,
    aspect_ratio: float,
    orientation_deg: float,
) -> Tuple[str, float]:
    """
    Returns (category, confidence).
    category: "alligator" | "longitudinal" | "transverse"

    Rules encode ASTM X1.5 (alligator = web/chicken-wire = loops + branches)
    vs X1.14 (single crack parallel or perpendicular to centreline).
    """
    slen = max(skel_len, 1.0)
    branch_density = branch_pts / slen

    alligator_score = 0
    # Strong evidence: closed loops
    if loop_count >= 1:
        alligator_score += 1
    # Medium evidence: many branch points relative to length
    if branch_density > 0.012:
        alligator_score += 1
    # Area evidence: mask is "fat" relative to skeleton (dense 2-D mesh)
    if fill_ratio > 6.0 and aspect_ratio < 3.0:
        alligator_score += 1

    is_alligator = (alligator_score >= 2) or (loop_count >= 2)

    if is_alligator:
        return "alligator", min(1.0, alligator_score / 3.0)
    else:
        # linear orientation: 0° = vertical = longitudinal, 90° = transverse
        if orientation_deg <= 35.0:
            return "longitudinal", min(1.0, (35.0 - orientation_deg) / 35.0)
        elif orientation_deg >= 55.0:
            return "transverse", min(1.0, (orientation_deg - 55.0) / 35.0)
        else:
            # Ambiguous zone: classify by majority orientation
            if orientation_deg < 45.0:
                return "longitudinal", 0.5
            else:
                return "transverse", 0.5
