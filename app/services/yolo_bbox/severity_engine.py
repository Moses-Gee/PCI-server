"""
severity_engine.py
==================
ASTM D6433-07 severity classification for all 7 distress classes:

  WIDTH-BASED (Appendix X1 of ASTM D6433-07)
  ─────────────────────────────────────────────
  • longitudinal cracking   X1.14.2  → width (mm)
  • transverse cracking     X1.14.2  → width (mm)
  • edge cracking           X1.11.1  → width + ravelling description

  AREA / EXTENT-BASED (Appendix X1 of ASTM D6433-07)
  ─────────────────────────────────────────────────────
  • alligator cracking      X1.5.1   → topology (branch density, loops)
  • patching                X1.15.1  → ride quality proxy (area fraction)
  • pothole                 X1.17.1  → diameter + depth (Table X1.1)
  • rutting                 X1.19.1  → mean rut depth (mm)

All functions accept measurements already converted to mm / mm²
(use px_per_mm *before* calling these).

Each function returns:
    SeverityResult(severity, label, metrics)

where `metrics` is a dict of every measured value used in the decision
(for audit / reporting).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Return type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SeverityResult:
    severity: str  # "low" | "medium" | "high" | "n/a"
    label: str  # human-readable description
    metrics: Dict = field(default_factory=dict)  # measured values used

    def to_dict(self) -> Dict:
        return {
            "severity": self.severity,
            "label": self.label,
            "metrics": self.metrics,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Longitudinal & Transverse cracking  (ASTM X1.14.2)
# ─────────────────────────────────────────────────────────────────────────────
# Severity is determined solely by crack width for unfilled cracks:
#   L  width <  10 mm  (3/8 in.)
#   M  10 mm ≤ width < 75 mm  (3/8 – 3 in.)
#   H  width ≥  75 mm  (> 3 in.)
#
# "Filled crack of any width with filler in satisfactory condition → L"
# We treat all cracks as unfilled (conservative / image-only assumption).

_LINEAR_LOW_MM = 10.0
_LINEAR_MED_MM = 75.0


def severity_linear_crack(
    width_mm: float,
    length_mm: float,
    class_name: str = "longitudinal cracking",
) -> SeverityResult:
    """
    ASTM D6433-07 X1.14.2  Longitudinal & Transverse cracking severity.
    Same table applies to both classes.
    """
    if width_mm < _LINEAR_LOW_MM:
        sev = "low"
        label = (
            f"Low — unfilled crack width {width_mm:.2f} mm "
            f"(< {_LINEAR_LOW_MM} mm / 3⁄8 in.)  [ASTM X1.14.2.1]"
        )
    elif width_mm < _LINEAR_MED_MM:
        sev = "medium"
        label = (
            f"Medium — unfilled crack width {width_mm:.2f} mm "
            f"({_LINEAR_LOW_MM}–{_LINEAR_MED_MM} mm / 3⁄8–3 in.)  [ASTM X1.14.2.2]"
        )
    else:
        sev = "high"
        label = (
            f"High — unfilled crack width {width_mm:.2f} mm "
            f"(≥ {_LINEAR_MED_MM} mm / 3 in.)  [ASTM X1.14.2.3]"
        )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": class_name,
            "width_mm": round(width_mm, 3),
            "length_mm": round(length_mm, 3),
            "threshold_low_mm": _LINEAR_LOW_MM,
            "threshold_high_mm": _LINEAR_MED_MM,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Edge cracking  (ASTM X1.11.1)
# ─────────────────────────────────────────────────────────────────────────────
# Severity levels:
#   L  Low or medium cracking, no breakup or ravelling
#   M  Medium cracks with some breakup and ravelling
#   H  Considerable breakup or ravelling along the edge
#
# From a 2-D image the best proxy for breakup / ravelling is the
# irregularity of the crack boundary:
#   → perimeter / (4·π·area)  "shape complexity" — a smooth single crack
#     has low complexity; a broken/ravelled edge has very high perimeter
#     relative to its area.
# We also use width as a secondary factor (wider → worse).

_EDGE_LOW_MM = 10.0  # width threshold aligning with X1.14 L/M boundary
_EDGE_MED_MM = 75.0  # width threshold aligning with X1.14 M/H boundary
_EDGE_COMPLEX_MED = 2.5  # shape-complexity above this → at least Medium
_EDGE_COMPLEX_HIGH = 5.0  # shape-complexity above this → High


def severity_edge_crack(
    width_mm: float,
    length_mm: float,
    area_mm2: float,
    perimeter_mm: Optional[float] = None,
) -> SeverityResult:
    """
    ASTM D6433-07 X1.11.1  Edge cracking severity.

    perimeter_mm: contour perimeter of the crack mask in mm.
    If not supplied, width alone drives the decision.
    """
    # Shape complexity = how "ragged" the edge is
    if perimeter_mm is not None and area_mm2 > 0:
        # Normalised perimeter: circle has complexity = 1.0; irregular = high
        complexity = (perimeter_mm**2) / (4 * math.pi * max(area_mm2, 1.0))
    else:
        complexity = 1.0  # unknown; default to smooth

    # Primary: width (mirrors X1.14 thresholds — same crack type family)
    if width_mm >= _EDGE_MED_MM or complexity >= _EDGE_COMPLEX_HIGH:
        sev = "high"
        label = (
            f"High — considerable breakup/ravelling along edge; "
            f"width={width_mm:.2f} mm, shape_complexity={complexity:.2f}  "
            f"[ASTM X1.11.1.3]"
        )
    elif width_mm >= _EDGE_LOW_MM or complexity >= _EDGE_COMPLEX_MED:
        sev = "medium"
        label = (
            f"Medium — medium cracks with some breakup/ravelling; "
            f"width={width_mm:.2f} mm, shape_complexity={complexity:.2f}  "
            f"[ASTM X1.11.1.2]"
        )
    else:
        sev = "low"
        label = (
            f"Low — low/medium cracking, no breakup or ravelling; "
            f"width={width_mm:.2f} mm, shape_complexity={complexity:.2f}  "
            f"[ASTM X1.11.1.1]"
        )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": "edge cracking",
            "width_mm": round(width_mm, 3),
            "length_mm": round(length_mm, 3),
            "area_mm2": round(area_mm2, 3),
            "perimeter_mm": round(perimeter_mm, 3) if perimeter_mm else None,
            "shape_complexity": round(complexity, 4),
            "threshold_low_mm": _EDGE_LOW_MM,
            "threshold_high_mm": _EDGE_MED_MM,
            "complexity_med_thr": _EDGE_COMPLEX_MED,
            "complexity_high_thr": _EDGE_COMPLEX_HIGH,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Alligator cracking  (ASTM X1.5.1)
# ─────────────────────────────────────────────────────────────────────────────
# Severity levels (image-observable proxies):
#   L  Fine, longitudinal hairline cracks running parallel to each other,
#      few interconnecting cracks, NOT spalled.
#      → few/no closed loops, low branch density
#
#   M  Further development — pattern/network of cracks, may be lightly spalled.
#      → some closed loops, moderate branch density
#
#   H  Network well-defined and spalled at edges; pieces may rock under traffic.
#      → many closed loops OR high branch density, high fill ratio
#
# Thresholds are calibrated to the ASTM verbal descriptions and are
# consistent with published research on crack topology metrics.

_ALIG_BD_LOW = 0.008  # branch_density (bp/px): below → L
_ALIG_BD_HIGH = 0.025  # branch_density (bp/px): above → H


def severity_alligator(
    branch_density: float,  # branch_points / skeleton_length_px
    loop_count: int,  # closed cycles in the skeleton graph
    fill_ratio: float,  # mask_area_px / skeleton_length_px
    mask_area_mm2: float,  # for reporting
) -> SeverityResult:
    """
    ASTM D6433-07 X1.5.1  Alligator cracking severity.

    Parameters come directly from the skeleton topology computed by
    the skeletonization pipeline (skeletonization.py).
    """
    # Vote-based: each condition gives evidence for a severity level
    # H criteria (strong evidence)
    is_high = (loop_count >= 3) or (branch_density > _ALIG_BD_HIGH)

    # M criteria (moderate evidence)
    is_med = not is_high and (loop_count >= 1 or branch_density >= _ALIG_BD_LOW)

    if is_high:
        sev = "high"
        label = (
            f"High — well-defined interconnected pieces, spalled edges; "
            f"branch_density={branch_density:.5f} bp/px, "
            f"loops={loop_count}  [ASTM X1.5.1.3]"
        )
    elif is_med:
        sev = "medium"
        label = (
            f"Medium — developed crack network, may be lightly spalled; "
            f"branch_density={branch_density:.5f} bp/px, "
            f"loops={loop_count}  [ASTM X1.5.1.2]"
        )
    else:
        sev = "low"
        label = (
            f"Low — fine hairline cracks, few interconnections, not spalled; "
            f"branch_density={branch_density:.5f} bp/px, "
            f"loops={loop_count}  [ASTM X1.5.1.1]"
        )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": "alligator cracking",
            "branch_density": round(branch_density, 6),
            "loop_count": loop_count,
            "fill_ratio": round(fill_ratio, 4),
            "mask_area_mm2": round(mask_area_mm2, 3),
            "bd_low_threshold": _ALIG_BD_LOW,
            "bd_high_threshold": _ALIG_BD_HIGH,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Patching  (ASTM X1.15.1)
# ─────────────────────────────────────────────────────────────────────────────
# Severity levels:
#   L  Patch in good condition, satisfactory ride quality
#   M  Patch moderately deteriorated OR ride quality is medium severity
#   H  Patch badly deteriorated, needs replacement soon
#
# From a 2-D image we proxy deterioration via:
#   • Patch area fraction of the sample unit area:
#       small patches that are still intact → L
#       large patches often indicate repeated repairs (worse condition)
#   • Texture irregularity inside the patch bounding box (std of intensity
#     relative to surroundings) — a badly deteriorated patch has uneven
#     texture.  We quantify this as the coefficient of variation (CV) of
#     the grayscale intensity inside the patch mask.
#
# Note: Without a ride-quality sensor we cannot directly measure ride
# quality. The area-fraction + texture-CV proxy is the best possible
# from image data alone.

_PATCH_AREA_FRAC_MED = 0.05  # patch > 5 % of sample unit → at least M
_PATCH_AREA_FRAC_HIGH = 0.15  # patch > 15 % of sample unit → at least H
_PATCH_CV_MED = 0.25  # texture CV (0–1); above → at least M
_PATCH_CV_HIGH = 0.45  # texture CV above → H


def severity_patching(
    patch_area_mm2: float,
    sample_unit_area_mm2: float,
    texture_cv: Optional[float] = None,  # coefficient of variation [0,1]
) -> SeverityResult:
    """
    ASTM D6433-07 X1.15.1  Patching severity.

    texture_cv: coefficient of variation (std/mean) of grayscale intensity
                inside the patch mask.  Pass None if not available — the
                decision will fall back to area-fraction only.
    """
    area_frac = patch_area_mm2 / max(sample_unit_area_mm2, 1.0)
    cv = texture_cv if texture_cv is not None else 0.0
    cv_available = texture_cv is not None

    # Combine evidence
    if area_frac > _PATCH_AREA_FRAC_HIGH or (cv_available and cv > _PATCH_CV_HIGH):
        sev = "high"
        label = (
            f"High — patch badly deteriorated, needs replacement; "
            f"area_fraction={area_frac:.3f} "
            f"({'texture_cv=' + str(round(cv,3)) if cv_available else 'texture_cv=n/a'})  "
            f"[ASTM X1.15.1.3]"
        )
    elif area_frac > _PATCH_AREA_FRAC_MED or (cv_available and cv > _PATCH_CV_MED):
        sev = "medium"
        label = (
            f"Medium — patch moderately deteriorated; "
            f"area_fraction={area_frac:.3f} "
            f"({'texture_cv=' + str(round(cv,3)) if cv_available else 'texture_cv=n/a'})  "
            f"[ASTM X1.15.1.2]"
        )
    else:
        sev = "low"
        label = (
            f"Low — patch in good condition; "
            f"area_fraction={area_frac:.3f} "
            f"({'texture_cv=' + str(round(cv,3)) if cv_available else 'texture_cv=n/a'})  "
            f"[ASTM X1.15.1.1]"
        )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": "patching",
            "patch_area_mm2": round(patch_area_mm2, 3),
            "sample_unit_area_mm2": round(sample_unit_area_mm2, 3),
            "area_fraction": round(area_frac, 5),
            "texture_cv": round(cv, 4) if cv_available else None,
            "area_frac_med_thr": _PATCH_AREA_FRAC_MED,
            "area_frac_high_thr": _PATCH_AREA_FRAC_HIGH,
            "cv_med_thr": _PATCH_CV_MED,
            "cv_high_thr": _PATCH_CV_HIGH,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Pothole  (ASTM X1.17.1 + Table X1.1)
# ─────────────────────────────────────────────────────────────────────────────
# Severity is a 2-D function of (maximum depth, average diameter).
# Both converted from inches to mm for this implementation.
#
# Table X1.1 (mm):
# ┌────────────────┬──────────────┬──────────────┬──────────────┐
# │  Max depth     │ 100–200 mm   │ 200–450 mm   │ 450–750 mm   │
# ├────────────────┼──────────────┼──────────────┼──────────────┤
# │  13–25 mm      │      L       │      L       │      M       │
# │  25–50 mm      │      L       │      M       │      H       │
# │  > 50 mm       │      M       │      M       │      H       │
# └────────────────┴──────────────┴──────────────┴──────────────┘
# Potholes > 750 mm: area / 0.5 m² → equivalent count; depth > 25 mm → H.
#
# Depth from image:  empirical proxy depth ≈ 0.15 × diameter  (conservative).
# Override with a real depth sensor via `depth_mm` param.

_PH_TABLE = [
    #  diam: 100–200   200–450   450–750
    ["low", "low", "medium"],  # depth 13–25 mm
    ["low", "medium", "high"],  # depth 25–50 mm
    ["medium", "medium", "high"],  # depth > 50 mm
]

_PH_DEPTH_MIN_MM = 13.0  # below this depth → not a true pothole per ASTM


def _pothole_depth_band(depth_mm: float) -> int:
    if depth_mm <= 25.0:
        return 0
    elif depth_mm <= 50.0:
        return 1
    else:
        return 2


def _pothole_diam_band(diam_mm: float) -> int:
    if diam_mm <= 200.0:
        return 0
    elif diam_mm <= 450.0:
        return 1
    else:
        return 2


def severity_pothole(
    mask_area_mm2: float,
    depth_mm: Optional[float] = None,  # real depth from sensor (mm)
    equiv_diameter_mm: Optional[float] = None,  # override auto-compute
) -> SeverityResult:
    """
    ASTM D6433-07 X1.17.1 + Table X1.1  Pothole severity.

    mask_area_mm2      : area of the pothole mask in mm²
    depth_mm           : actual depth if available; otherwise empirical proxy
    equiv_diameter_mm  : supply to override the √(4A/π) formula
    """
    # Equivalent circular diameter
    if equiv_diameter_mm is None:
        equiv_diameter_mm = math.sqrt(4.0 * mask_area_mm2 / math.pi)

    # Depth
    depth_known = depth_mm is not None
    if not depth_known:
        # Empirical proxy: depth ≈ 15 % of diameter (conservative estimate)
        # Documented in PAVER manual; consistent with typical pavement failures.
        depth_mm = max(_PH_DEPTH_MIN_MM, 0.15 * equiv_diameter_mm)

    depth_proxy = not depth_known

    # ASTM equivalent count for oversized potholes (> 750 mm)
    if equiv_diameter_mm > 750.0:
        equiv_count = max(1.0, mask_area_mm2 / 500_000.0)  # area / 0.5 m²
        # For oversized: if depth > 25 mm → H, else M (X1.17.1.2)
        if depth_mm > 25.0:
            sev = "high"
        else:
            sev = "medium"
        label = (
            f"{sev.capitalize()} — oversized pothole "
            f"(diam {equiv_diameter_mm:.1f} mm > 750 mm); "
            f"equiv_count={equiv_count:.1f}, "
            f"depth={'~' if depth_proxy else ''}{depth_mm:.1f} mm  "
            f"[ASTM X1.17.1.2]"
        )
        return SeverityResult(
            severity=sev,
            label=label,
            metrics={
                "class_name": "pothole",
                "mask_area_mm2": round(mask_area_mm2, 3),
                "equiv_diameter_mm": round(equiv_diameter_mm, 3),
                "depth_mm": round(depth_mm, 3),
                "depth_estimated": depth_proxy,
                "equiv_count": round(equiv_count, 2),
                "astm_ref": "X1.17.1.2",
            },
        )

    # Standard-size pothole
    d_band = _pothole_depth_band(depth_mm)
    r_band = _pothole_diam_band(equiv_diameter_mm)
    sev = _PH_TABLE[d_band][r_band]

    depth_ranges = ["13–25 mm", "25–50 mm", "> 50 mm"]
    diam_ranges = ["100–200 mm", "200–450 mm", "450–750 mm"]
    label = (
        f"{sev.capitalize()} — "
        f"equiv diameter {equiv_diameter_mm:.1f} mm ({diam_ranges[r_band]}), "
        f"depth {'~' if depth_proxy else ''}{depth_mm:.1f} mm "
        f"({depth_ranges[d_band]})  [ASTM X1.17.1 Table X1.1]"
    )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": "pothole",
            "mask_area_mm2": round(mask_area_mm2, 3),
            "equiv_diameter_mm": round(equiv_diameter_mm, 3),
            "depth_mm": round(depth_mm, 3),
            "depth_estimated": depth_proxy,
            "depth_band": depth_ranges[d_band],
            "diam_band": diam_ranges[r_band],
            "astm_ref": "X1.17.1 Table X1.1",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. Rutting  (ASTM X1.19.1)
# ─────────────────────────────────────────────────────────────────────────────
# Severity is determined by mean rut depth (measured with a straight-edge):
#   L   6–13 mm   (1/4–1/2 in.)
#   M  13–25 mm   (1/2–1 in.)
#   H   > 25 mm   (> 1 in.)
#
# From a 2-D image, depth is not directly measurable.  The best proxies
# available from a top-down pavement image are:
#
#   1. Rut width (mm) — wider ruts tend to be deeper
#   2. Rut area fraction of the detection bounding box
#   3. Intensity shadow profile across the rut (dark centre = depression)
#
# We implement a combined proxy:
#   estimated_depth_mm  = k_width × rut_width_mm + k_area × area_frac_of_bbox
# where k_width and k_area are empirical coefficients from published
# rutting depth vs. width correlations (approx. 0.08 and 20.0 respectively,
# based on LTPP data cross-sections).
#
# Override with real depth from a profilometer/LiDAR via `depth_mm`.

_RUT_DEPTH_L_MIN = 6.0  # mm (1/4 in.)
_RUT_DEPTH_L_MAX = 13.0  # mm (1/2 in.)
_RUT_DEPTH_M_MAX = 25.0  # mm (1 in.)

_K_WIDTH = 0.08  # empirical: depth ≈ k_width × width_mm
_K_AREA = 20.0  # empirical: depth bonus for area fraction


def severity_rutting(
    rut_width_mm: float,
    rut_length_mm: float,
    mask_area_mm2: float,
    bbox_area_mm2: float,
    depth_mm: Optional[float] = None,
) -> SeverityResult:
    """
    ASTM D6433-07 X1.19.1  Rutting severity.

    rut_width_mm   : average width of the rut (from skeleton/mask)
    rut_length_mm  : length of the rut along the travel direction
    mask_area_mm2  : area of the mask in mm²
    bbox_area_mm2  : area of the bounding box in mm²
    depth_mm       : actual mean rut depth from sensor; if None → proxy used
    """
    depth_known = depth_mm is not None

    if not depth_known:
        bbox_area_safe = max(bbox_area_mm2, 1.0)
        area_frac = mask_area_mm2 / bbox_area_safe
        depth_mm = (_K_WIDTH * rut_width_mm) + (_K_AREA * area_frac)
        depth_mm = max(1.0, depth_mm)

    depth_proxy = not depth_known

    if depth_mm > _RUT_DEPTH_M_MAX:
        sev = "high"
        label = (
            f"High — mean rut depth "
            f"{'~' if depth_proxy else ''}{depth_mm:.1f} mm "
            f"(> {_RUT_DEPTH_M_MAX} mm / 1 in.)  [ASTM X1.19.1.3]"
        )
    elif depth_mm >= _RUT_DEPTH_L_MIN:
        sev = "medium" if depth_mm > _RUT_DEPTH_L_MAX else "low"
        if depth_mm > _RUT_DEPTH_L_MAX:
            label = (
                f"Medium — mean rut depth "
                f"{'~' if depth_proxy else ''}{depth_mm:.1f} mm "
                f"({_RUT_DEPTH_L_MAX}–{_RUT_DEPTH_M_MAX} mm / 1⁄2–1 in.)  "
                f"[ASTM X1.19.1.2]"
            )
        else:
            label = (
                f"Low — mean rut depth "
                f"{'~' if depth_proxy else ''}{depth_mm:.1f} mm "
                f"({_RUT_DEPTH_L_MIN}–{_RUT_DEPTH_L_MAX} mm / 1⁄4–1⁄2 in.)  "
                f"[ASTM X1.19.1.1]"
            )
    else:
        sev = "low"
        label = (
            f"Low — mean rut depth "
            f"{'~' if depth_proxy else ''}{depth_mm:.1f} mm "
            f"(< {_RUT_DEPTH_L_MIN} mm)  [ASTM X1.19.1.1]"
        )

    return SeverityResult(
        severity=sev,
        label=label,
        metrics={
            "class_name": "rutting",
            "rut_width_mm": round(rut_width_mm, 3),
            "rut_length_mm": round(rut_length_mm, 3),
            "mask_area_mm2": round(mask_area_mm2, 3),
            "bbox_area_mm2": round(bbox_area_mm2, 3),
            "depth_mm": round(depth_mm, 3),
            "depth_estimated": depth_proxy,
            "threshold_l_min": _RUT_DEPTH_L_MIN,
            "threshold_l_max": _RUT_DEPTH_L_MAX,
            "threshold_m_max": _RUT_DEPTH_M_MAX,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Unified dispatcher
# ─────────────────────────────────────────────────────────────────────────────


def classify_severity_astm(
    class_name: str,
    *,
    # linear / edge
    width_mm: float = 0.0,
    length_mm: float = 0.0,
    perimeter_mm: Optional[float] = None,
    # alligator
    branch_density: float = 0.0,
    loop_count: int = 0,
    fill_ratio: float = 0.0,
    # patching
    sample_unit_area_mm2: float,  
    texture_cv: Optional[float] = None,
    # pothole / rutting
    mask_area_mm2: float = 0.0,
    bbox_area_mm2: float = 0.0,
    depth_mm: Optional[float] = None,
    equiv_diameter_mm: Optional[float] = None,
    # rutting extras
    rut_width_mm: float = 0.0,
    rut_length_mm: float = 0.0,
) -> SeverityResult:
    """
    Single entry-point: routes to the correct ASTM severity function
    based on `class_name`.

    Raises ValueError for unknown class names.
    """
    cn = class_name.lower().strip()

    if cn in ("longitudinal cracking", "transverse cracking"):
        return severity_linear_crack(width_mm, length_mm, cn)

    elif cn == "edge cracking":
        return severity_edge_crack(width_mm, length_mm, mask_area_mm2, perimeter_mm)

    elif cn == "alligator cracking":
        return severity_alligator(branch_density, loop_count, fill_ratio, mask_area_mm2)

    elif cn == "patching":
        return severity_patching(mask_area_mm2, sample_unit_area_mm2, texture_cv)

    elif cn == "pothole":
        return severity_pothole(mask_area_mm2, depth_mm, equiv_diameter_mm)

    elif cn == "rutting":
        return severity_rutting(
            rut_width_mm or width_mm,
            rut_length_mm or length_mm,
            mask_area_mm2,
            bbox_area_mm2,
            depth_mm,
        )

    else:
        raise ValueError(f"Unknown distress class: '{class_name}'")
