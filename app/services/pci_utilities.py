normalized_classes = {"alligator": ["alligator crack"], "linear": [""], "pothole": [""]}

VALID_DISTRESS_TYPES = list(normalized_classes.keys())

# VALID_DISTRESS_TYPES = {"alligator", "long_trans", "pothole"}
VALID_SEVERITIES = ["low", "medium", "high"]


def normalizeClass(distress_type: str):
    for key, vals in normalized_classes.items():
        if distress_type.lower() in vals:
            return key
        else:
            return None


normalized_class = normalizeClass("Alligator Crack")
print(normalized_class)


# ---------------------------------------------------------------------------
# PCI condition rating table (ASTM D6433 Table 2 / Fig. 1)
# ---------------------------------------------------------------------------
PCI_RATING_TABLE = [
    (86, 100, "Good"),
    (71, 85, "Satisfactory"),
    (56, 70, "Fair"),
    (41, 55, "Poor"),
    (26, 40, "Very Poor"),
    (11, 25, "Serious"),
    (0, 10, "Failed"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def pci_condition(pci: float) -> str:
    for lo, hi, label in PCI_RATING_TABLE:
        if lo <= pci <= hi:
            return label
    return "Unknown"


def groupAndCalcDensity(predictions, section_area):
    # Count occurrences of each (distress_type, severity) combination
    count_map = {}
    for prediction in predictions:
        distress_type = prediction.get("distress_type")
        severity = prediction.get("severity")
        if distress_type not in VALID_DISTRESS_TYPES:
            print(f"Distress type: {distress_type} not valid")
            continue
        key = (distress_type, severity)
        count_map[key] = count_map.get(key, 0) + 1

    # Build the grouped result with the required keys
    grouped_predictions = []
    for (distress_type, severity), count in count_map.items():
        density = (count * 100) / section_area
        density = round(density, 4)
        grouped_predictions.append(
            {
                "distress": distress_type,
                "severity": severity,
                "count": count,
                "density": density,
            }
        )
        # df = pd.DataFrame()

    return grouped_predictions
