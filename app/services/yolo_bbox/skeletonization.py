"""
skeletonization.py  (updated)
==============================
Adds topology metrics required by severity_engine.py on top of the
existing Sato + morphology + skeleton pipeline.

New exports
-----------
  SkeletonMetrics          dataclass with every topology measurement
  compute_skeleton_metrics(binary_mask, skeleton, px_per_mm) → SkeletonMetrics
  compute_perimeter(binary_mask, px_per_mm)                  → float (mm)
  compute_texture_cv(gray_crop, binary_mask)                 → float [0,1]

Unchanged exports (kept for backwards compatibility)
-----------------------------------------------------
  sato_tubeness, morphological_refine, skeletonize,
  compute_width, classify_severity,
  SEVERITY_LABELS, SEVERITY_LOW_MAX, SEVERITY_MEDIUM_MAX
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import math

import cv2
import numpy as np
from skimage.filters import sato
from skimage.morphology import skeletonize as ski_skeletonize

from app.services.yolo_bbox.config_bbox import bbox_cfg

# ─────────────────────────────────────────────────────────────────────────────
# Sato Tubeness
# ─────────────────────────────────────────────────────────────────────────────

def _sato_skimage(gray: np.ndarray, sigmas: List[float]) -> np.ndarray:
    gray_f = gray.astype(np.float32) / 255.0
    result = sato(gray_f, sigmas=sigmas, black_ridges=True)
    if result.max() > 0:
        result = result / result.max()
    return result.astype(np.float32)


def _sato_opencv(gray: np.ndarray, sigmas: List[float]) -> np.ndarray:
    gray_f = gray.astype(np.float32) / 255.0
    max_response = np.zeros_like(gray_f)
    for sigma in sigmas:
        ksize = max(3, int(6 * sigma + 1) | 1)
        blurred = cv2.GaussianBlur(gray_f, (ksize, ksize), sigma)
        Lxx = cv2.Sobel(blurred, cv2.CV_32F, 2, 0, ksize=3)
        Lyy = cv2.Sobel(blurred, cv2.CV_32F, 0, 2, ksize=3)
        Lxy = cv2.Sobel(blurred, cv2.CV_32F, 1, 1, ksize=3)
        trace = Lxx + Lyy
        det   = Lxx * Lyy - Lxy ** 2
        disc  = np.sqrt(np.maximum((trace ** 2 / 4) - det, 0))
        lambda1 = trace / 2 + disc
        R = (sigma ** 2) * np.maximum(lambda1, 0)
        max_response = np.maximum(max_response, R)
    if max_response.max() > 0:
        max_response = max_response / max_response.max()
    return max_response.astype(np.float32)


def sato_tubeness(
    gray: np.ndarray,
    sigmas: Optional[List[float]] = None,
) -> np.ndarray:
    sigmas = sigmas or [1, 2, 3, 4, 5, 6, 8, 10]
    return _sato_skimage(gray, sigmas)


# ─────────────────────────────────────────────────────────────────────────────
# Morphological refinement  (paper Eq. 4–6)
# ─────────────────────────────────────────────────────────────────────────────

def morphological_refine(
    binary: np.ndarray,
    dilation_k: int = 3,
    erosion_k: int  = 3,
    opening_k: int  = 3,
) -> np.ndarray:
    d_kern = cv2.getStructuringElement(cv2.MORPH_RECT, (dilation_k, dilation_k))
    e_kern = cv2.getStructuringElement(cv2.MORPH_RECT, (erosion_k,  erosion_k))
    o_kern = cv2.getStructuringElement(cv2.MORPH_RECT, (opening_k,  opening_k))
    out = cv2.dilate(binary, d_kern, iterations=1)
    out = cv2.erode(out,    e_kern, iterations=1)
    out = cv2.morphologyEx(out, cv2.MORPH_OPEN, o_kern)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Skeletonization
# ─────────────────────────────────────────────────────────────────────────────

def skeletonize(binary: np.ndarray) -> np.ndarray:
    """Parallel iterative thinning → 1-pixel-wide skeleton (uint8, 255=on)."""
    bool_mask = binary > 0
    skel = ski_skeletonize(bool_mask)
    return skel.astype(np.uint8) * 255


def _manual_thin(binary: np.ndarray) -> np.ndarray:
    """Pure-NumPy Zhang-Suen thinning fallback."""
    img  = (binary > 0).astype(np.uint8)
    prev = np.zeros_like(img)
    while not np.array_equal(img, prev):
        prev = img.copy()
        for iteration in range(2):
            marked = np.zeros_like(img)
            for y in range(1, img.shape[0] - 1):
                for x in range(1, img.shape[1] - 1):
                    p2=img[y-1,x]; p3=img[y-1,x+1]; p4=img[y,x+1]
                    p5=img[y+1,x+1]; p6=img[y+1,x]; p7=img[y+1,x-1]
                    p8=img[y,x-1]; p9=img[y-1,x-1]
                    s = (int(p2==0 and p3==1)+int(p3==0 and p4==1)+
                         int(p4==0 and p5==1)+int(p5==0 and p6==1)+
                         int(p6==0 and p7==1)+int(p7==0 and p8==1)+
                         int(p8==0 and p9==1)+int(p9==0 and p2==1))
                    n = p2+p3+p4+p5+p6+p7+p8+p9
                    if img[y,x] and 2<=n<=6 and s==1:
                        if iteration==0 and not(p2 and p4 and p6):
                            marked[y,x]=1
                        elif iteration==1 and not(p2 and p4 and p8):
                            marked[y,x]=1
            img[marked==1]=0
    return (img*255).astype(np.uint8)


# ─────────────────────────────────────────────────────────────────────────────
# Width / length / area  (paper Eq. 7)
# ─────────────────────────────────────────────────────────────────────────────

def compute_width(
    binary_mask: np.ndarray,
    skeleton:    np.ndarray,
    px_per_mm:   float = 1.0,
) -> Tuple[float, float, float]:
    """
    W = A / B  (paper Eq. 7)
    Returns (width_mm, length_mm, area_mm2).
    """
    A = float(np.count_nonzero(binary_mask))
    B = float(np.count_nonzero(skeleton))
    if B == 0:
        return 0.0, 0.0, 0.0
    width_px  = A / B
    width_mm  = width_px  / px_per_mm
    length_mm = B         / px_per_mm
    area_mm2  = A         / (px_per_mm ** 2)
    return width_mm, length_mm, area_mm2


# ─────────────────────────────────────────────────────────────────────────────
# NEW: Topology metrics for severity classification
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkeletonMetrics:
    """All topology measurements derived from a binary mask + its skeleton."""

    # geometry (mm units)
    width_mm:    float
    length_mm:   float
    area_mm2:    float
    perimeter_mm: float

    # raw pixel counts
    skeleton_length_px: float
    mask_area_px:       float

    # topology
    branch_points:  int
    end_points:     int
    loop_count:     int

    # derived ratios
    branch_density: float   # branch_points / skeleton_length_px
    fill_ratio:     float   # mask_area_px  / skeleton_length_px
    shape_complexity: float  # perimeter² / (4π·area_mm2) — isoperimetric ratio

    # orientation
    orientation_deg: float  # 0 = vertical/longitudinal, 90 = horizontal/transverse
    aspect_ratio:    float  # major / minor PCA eigenvalue ratio

    def to_dict(self) -> Dict:
        return {k: round(v, 5) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


def _neighbour_degree_map(sk: np.ndarray) -> np.ndarray:
    """Count 8-connected skeleton neighbours per skeleton pixel."""
    kn = np.array([[1,1,1],[1,0,1],[1,1,1]], dtype=np.uint8)
    nc = cv2.filter2D(sk, ddepth=cv2.CV_8U, kernel=kn,
                      borderType=cv2.BORDER_CONSTANT)
    return nc * sk


def _count_topology(skeleton: np.ndarray) -> Tuple[int, int, int]:
    """
    Returns (branch_points, end_points, loop_count).

    Loop count via Euler characteristic:
        loops = edges - vertices + connected_components
    """
    sk = (skeleton > 0).astype(np.uint8)
    if sk.sum() == 0:
        return 0, 0, 0

    deg = _neighbour_degree_map(sk)
    branch_pts = int(np.sum((deg >= 3) & (sk > 0)))
    end_pts    = int(np.sum((deg == 1) & (sk > 0)))

    # Split skeleton into edge-segments by removing branch pixels
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
    return branch_pts, end_pts, loops


def _orientation_and_aspect(mask: np.ndarray) -> Tuple[float, float]:
    """
    PCA-based principal orientation and aspect ratio of the mask.
    orientation_deg: 0 = vertical (longitudinal), 90 = horizontal (transverse).
    """
    ys, xs = np.nonzero(mask > 0)
    if len(xs) < 2:
        return 0.0, 1.0
    pts = np.column_stack([xs, ys]).astype(np.float32)
    centered = pts - pts.mean(axis=0)
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    mv  = eigvecs[:, 0]
    ang = math.degrees(math.atan2(abs(mv[0]), abs(mv[1])))  # 0=vertical
    ang = max(0.0, min(90.0, ang))
    major = math.sqrt(max(eigvals[0], 1e-9))
    minor = math.sqrt(max(eigvals[1], 1e-9))
    aspect = major / minor if minor > 0 else 999.0
    return ang, aspect


def compute_perimeter(binary_mask: np.ndarray, px_per_mm: float = 1.0) -> float:
    """
    Contour perimeter of the binary mask converted to mm.
    Returns 0.0 if no contour found.
    """
    contours, _ = cv2.findContours(
        (binary_mask > 0).astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_NONE,
    )
    if not contours:
        return 0.0
    total_perimeter_px = sum(cv2.arcLength(c, closed=True) for c in contours)
    return total_perimeter_px / px_per_mm


def compute_texture_cv(
    gray_crop: np.ndarray,
    binary_mask: np.ndarray,
) -> float:
    """
    Coefficient of variation (std / mean) of grayscale intensity INSIDE
    the mask.  Returns 0.0 if mask is empty or mean is zero.

    Used as a patching deterioration proxy: a well-maintained patch has
    uniform texture (low CV); a deteriorated patch is rough (high CV).
    """
    if gray_crop is None or gray_crop.size == 0:
        return 0.0
    mask = (binary_mask > 0)
    if not mask.any():
        return 0.0
    pixels = gray_crop[mask].astype(np.float32)
    mean = pixels.mean()
    if mean < 1e-6:
        return 0.0
    return float(pixels.std() / mean)


def compute_skeleton_metrics(
    binary_mask: np.ndarray,
    skeleton:    np.ndarray,
    px_per_mm:   float = 1.0,
    gray_crop:   Optional[np.ndarray] = None,
) -> SkeletonMetrics:
    """
    Compute every topology/geometry metric from a binary mask and its
    pre-computed skeleton.

    Parameters
    ----------
    binary_mask : uint8 H×W (0=background, >0=distress)
    skeleton    : uint8 H×W skeleton from skeletonize()
    px_per_mm   : camera calibration
    gray_crop   : optional grayscale crop — needed for texture_cv (patching)

    Returns
    -------
    SkeletonMetrics
    """
    width_mm, length_mm, area_mm2 = compute_width(binary_mask, skeleton, px_per_mm)

    mask_area_px   = float(np.count_nonzero(binary_mask))
    skel_len_px    = float(np.count_nonzero(skeleton))
    skel_safe      = max(skel_len_px, 1.0)

    branch_pts, end_pts, loop_count = _count_topology(skeleton)
    branch_density  = branch_pts / skel_safe
    fill_ratio      = mask_area_px / skel_safe

    perimeter_mm    = compute_perimeter(binary_mask, px_per_mm)
    area_safe       = max(area_mm2, 1.0)
    shape_complexity = (perimeter_mm ** 2) / (4 * math.pi * area_safe)

    orientation_deg, aspect_ratio = _orientation_and_aspect(binary_mask)

    return SkeletonMetrics(
        width_mm         = width_mm,
        length_mm        = length_mm,
        area_mm2         = area_mm2,
        perimeter_mm     = perimeter_mm,
        skeleton_length_px = skel_len_px,
        mask_area_px     = mask_area_px,
        branch_points    = branch_pts,
        end_points       = end_pts,
        loop_count       = loop_count,
        branch_density   = branch_density,
        fill_ratio       = fill_ratio,
        shape_complexity = shape_complexity,
        orientation_deg  = orientation_deg,
        aspect_ratio     = aspect_ratio,
    )
