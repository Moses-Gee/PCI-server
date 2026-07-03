# ─────────────────────────────────────────────────────────────────────────────
# ASTM D6433-07 severity thresholds
# ─────────────────────────────────────────────────────────────────────────────

# Longitudinal / Transverse cracking (X1.14.2) - crack width in mm
LINEAR_LOW_MAX_MM    = 10.0     # < 10 mm  → Low
LINEAR_MED_MAX_MM    = 75.0     # 10–75 mm → Medium; ≥ 75 mm → High

# Pothole diameter ranges in mm (Table X1.1 converted from inches)
_PH_DIAM_BREAKS = [100.0, 200.0, 450.0, 750.0]   # mm boundaries
# Pothole depth ranges in mm (Table X1.1)
_PH_DEPTH_BREAKS = [13.0, 25.0, 50.0]             # mm boundaries

# ASTM Table X1.1 - severity matrix [depth_band][diam_band]
#   depth_band: 0 = 13–25 mm, 1 = 25–50 mm, 2 = >50 mm
#   diam_band:  0 = 100–200, 1 = 200–450, 2 = 450–750
_PH_SEVERITY_TABLE = [
    ["low",    "low",    "medium"],   # depth 13–25 mm
    ["low",    "medium", "high"],     # depth 25–50 mm
    ["medium", "medium", "high"],     # depth > 50 mm
]

# Alligator cracking topology thresholds (from classifier → severity proxy)
# L: low branch density, no or few loops
# M: moderate network (branch_density moderate OR 1–2 loops)
# H: dense network (many loops, high branch density)
_ALIG_BRANCH_LOW  = 0.008   # branch_density < this → Low
_ALIG_BRANCH_HIGH = 0.025   # branch_density > this → High


class Config:
    # MODEL_TYPE = "yolov8n-seg.pt"
    classes = ["Crack", "pothole"]

    img_size = 640
    # device = "cuda" if torch.cuda.is_available() else "cpu"

    IOU_THRESHOLD = 0.1
    CONF_THRESHOLD = 0.1


seg_cfg = Config()