"""
pci_calculator.py  --  PRODUCTION MODULE

The only file your application needs to import.

It loads pre-fitted polynomial coefficients from models.json at startup
(once, on first use) and then answers deduct-value queries instantly
with no fitting, no heavy dependencies, and no digitized data required.

Deployment patterns
-------------------
Web server  : create one instance at module level, share it across requests.
CLI tool    : create one instance per process invocation (fast, < 1ms).
Lambda/FaaS : create at module level so it's reused across warm invocations.
Django/Flask: attach to app context or use a module-level singleton (see below).
"""

import json
import math
import os

from app.services.pci.pci_utilities import (
    VALID_DISTRESS_TYPES,
    VALID_SEVERITIES,
    clamp,
    pci_condition,
)
import numpy as np

# normalized_classes = {
#     "alligator": [
#         "alligator crack",
#         "Alligator crack",
#         "alligator cracking",
#         "alligator",
#     ],
#     "linear": [
#         "Longitudinal Crack",
#         "Transverse Crack",
#         "longitudinal cracking",
#         "transverse cracking",
#         "Longitudinal",
#         "Transverse",
#     ],
#     "pothole": ["Pothole"],
#     "edge_crack": ["edge cracking", "edge crack"],
#     "patching": ["patching"],
#     "rutting": ["rutting"],
# }

# VALID_DISTRESS_TYPES = list(normalized_classes.keys())

# # VALID_DISTRESS_TYPES = {"alligator", "long_trans", "pothole"}
# VALID_SEVERITIES = ["low", "medium", "high"]

# PCI_RATING_TABLE = [
#     (86, 100, "Good"),
#     (71, 85, "Satisfactory"),
#     (56, 70, "Fair"),
#     (41, 55, "Poor"),
#     (26, 40, "Very Poor"),
#     (11, 25, "Serious"),
#     (0, 10, "Failed"),
# ]

# def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
#     return max(lo, min(hi, value))


# def pci_condition(pci: float) -> str:
#     for lo, hi, label in PCI_RATING_TABLE:
#         if lo <= pci <= hi:
#             return label
#     return "Unknown"

_DEFAULT_DISTRESS_MODELS_PATH = os.path.join(
    os.getcwd(), "app", "fitted_polynomial", "distress_models.json"
)
_DEFAULT_CDV_MODELS_PATH = os.path.join(
    os.getcwd(), "app", "fitted_polynomial", "cdvs_model.json"
)
# print(os.path.exists(_DEFAULT_MODELS_PATH))

# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class PCICalculator:
    """
    Production deduct-value and PCI calculator.
    The models are loaded exactly once on first instantiation (or explicitly
    via load_models). After that every call is pure arithmetic -- no I/O,
    no fitting, no imports of numpy beyond what is already loaded.
    """

    _singleton = None  # optional module-level cache (see get_instance())

    def __init__(
        self,
        models_path: str = _DEFAULT_DISTRESS_MODELS_PATH,
        cdv_models_path: str = _DEFAULT_CDV_MODELS_PATH,
    ):
        self._models_path = models_path
        self._cdv_models_path = cdv_models_path
        self._polys: dict = {}  # {distress_type: {severity: np.poly1d}}
        self._cdv_polys: dict = {}  # {q: np.poly1d}
        self._load_models()
        self._load_cdv_models()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_models(self):
        """Load polynomial coefficients from JSON and reconstruct poly1d objects."""
        if not os.path.exists(self._models_path):
            raise FileNotFoundError(
                f"Model file not found: {self._models_path}\n"
                f"Run save_models.py once to generate it."
            )
        with open(self._models_path) as f:
            raw = json.load(f)

        for distress_type, severities in raw.items():
            self._polys[distress_type] = {}
            for sev, data in severities.items():
                self._polys[distress_type][sev] = np.poly1d(data["coefficients"])

    def _load_cdv_models(self):
        """Load digitized CDV correction curves (one polynomial per q)."""
        if not os.path.exists(self._cdv_models_path):
            raise FileNotFoundError(
                f"CDV model file not found: {self._cdv_models_path}"
            )
        with open(self._cdv_models_path) as f:
            raw = json.load(f)

        self._cdv_polys = {
            int(q_str): np.poly1d(data["coefficients"]) for q_str, data in raw.items()
        }
        self._max_q = max(self._cdv_polys.keys())

    # ------------------------------------------------------------------
    # Core query
    # ------------------------------------------------------------------

    def get_deduct_value(
        self,
        distress_type: str,
        severity: str,
        density: float,
    ) -> float:
        """
        Return the deduct value for one distress observation.

        Parameters
        ----------
        distress_type : "alligator" | "long_trans" | "pothole"
        severity      : "L" | "M" | "H"
        density       : distress density as a percentage (must be > 0)

        Returns
        -------
        float  in the range [0, 100]
        """
        self._validate(distress_type, severity, density)
        poly = self._polys[distress_type][severity]
        raw = float(poly(math.log10(density)))
        return clamp(raw)

    # ------------------------------------------------------------------
    # Full PCI calculation (ASTM D6433 Section 9)
    # ------------------------------------------------------------------

    def compute_pci(self, observations: list[dict]) -> dict:
        """
        Compute the Pavement Condition Index for a sample unit.

        Parameters
        ----------
        observations : list of dicts, each with keys:
            distress_type  : str   -- "alligator" | "linear" | "pothole"
            severity  : str   -- "low" | "medium" | "high"
            density   : float -- density % (> 0)
        """
        if not observations:
            return {
                "final_pci": 100.0,
                "condition_rating": "Good",
                "max_cdv": 0.0,
                "tdv_start": 0.0,
                "deduct_values": [],
                "observations": [],
                "all_cdvs": [],
                "all_tdvs": [],
            }

        # Step 1 -- compute individual deduct values
        enriched = []
        for obs in observations:
            dv = self.get_deduct_value(
                obs["distress_type"], obs["severity"], obs["density"]
            )
            enriched.append({**obs, "deduct_value": round(dv, 2)})

        dvs = sorted([e["deduct_value"] for e in enriched], reverse=True)

        # Step 2 -- ASTM Section 9.5: find maximum CDV iteratively
        # m = allowable number of deducts (Eq. 4 in the standard)
        hdv = dvs[0]
        m = min(10, 1 + (9 / 98) * (100 - hdv))
        num_to_keep = math.ceil(m)
        # print("num_to_keep", num_to_keep)

        # Truncate to m deducts (last one scaled by fractional part of m)
        working = dvs[:num_to_keep]
        # print("working", working)
        frac = m - math.floor(m)
        if frac > 0 and len(working) == num_to_keep and num_to_keep > 1:
            working[-1] = working[-1] * frac

        max_cdv = 0.0
        current_dvs = list(working)
        # print("current_dvs", current_dvs)
        all_cdvs = []
        all_tdvs = []

        # Iterate: each pass replaces smallest dv > 2 with 2, recompute CDV
        while True:
            tdv = sum(current_dvs)
            print("tdv", tdv)
            all_tdvs.append(tdv)

            q = sum(1 for v in current_dvs if v > 2)

            if q <= 1:
                max_cdv = max(max_cdv, tdv)
                break

            cdv = self._corrected_deduct_value(tdv, q)
            # print("q", q, "cdv", cdv)
            all_cdvs.append(cdv)

            max_cdv = max(max_cdv, cdv)

            for i in reversed(range(len(current_dvs))):
                if current_dvs[i] > 2:
                    current_dvs[i] = 2
                    print("current_dvs", current_dvs)
                    break

        pci = clamp(100.0 - max_cdv)

        return {
            "final_pci": round(pci, 2),
            "condition_rating": pci_condition(pci),
            "max_cdv": round(max_cdv, 2),
            "tdv_start": round(sum(dvs), 2),
            "deduct_values": [round(v, 2) for v in dvs],
            "observations": enriched,
            "all_cdvs": [round(cdv, 2) for cdv in all_cdvs],
            "all_tdvs": [round(tdv, 2) for tdv in all_tdvs],
        }

    # ------------------------------------------------------------------
    # CDV correction (ASTM Fig. X3.26 -- asphalt correction curves)
    # ------------------------------------------------------------------

    def _corrected_deduct_value(self, tdv: float, q: int) -> float:
        """
        Corrected deduct value from digitized ASTM Fig. X3.26 curves.

        Each q level (1..7, or however many you digitized) has its own
        fitted polynomial in TDV. q values above the max digitized curve
        are clamped to the highest available curve, matching ASTM practice
        of treating q >= 8 the same as the q=8 curve.
        """
        if tdv <= 0:
            return 0.0
        q_clamped = max(1, min(q, self._max_q))
        poly = self._cdv_polys[q_clamped]
        cdv = float(poly(tdv))
        return clamp(cdv)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate(self, distress_type: str, severity: str, density: float):
        if distress_type not in VALID_DISTRESS_TYPES:
            raise ValueError(
                f"distress_type must be one of {VALID_DISTRESS_TYPES}, "
                f"got '{distress_type}'"
            )
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"severity must be one of {VALID_SEVERITIES}, got '{severity}'"
            )
        if density <= 0:
            raise ValueError(f"density must be > 0 (log-scale x-axis), got {density}")

    # ------------------------------------------------------------------
    # Singleton helper -- use this in web servers / Django / Flask
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(
        cls,
        models_path: str = _DEFAULT_DISTRESS_MODELS_PATH,
        cdv_models_path: str = _DEFAULT_CDV_MODELS_PATH,
    ):
        """
        Return a shared singleton instance.

        In a web server (Flask, Django, FastAPI) call this once at startup
        or at the module level, rather than creating a new instance per
        request:
        """
        if cls._singleton is None:
            cls._singleton = cls(models_path, cdv_models_path)
        return cls._singleton


# ---------------------------------------------------------------------------
# Quick smoke test when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    calc = PCICalculator.get_instance()

    # print("=== Single deduct value lookups ===")
    examples = [
        ("alligator", "low", 2.0),
        ("alligator", "medium", 25.0),
        ("alligator", "high", 60.0),
        ("linear", "low", 5.0),
        ("linear", "medium", 20.0),
        ("linear", "high", 50.0),
        ("pothole", "low", 0.05),
        ("pothole", "medium", 0.5),
        ("pothole", "high", 3.0),
    ]
    # for dt, sev, dens in examples:
    #     dv = calc.get_deduct_value(dt, sev, dens)
    #     print(f"  {dt:10s}  sev={sev}  density={dens:>6}%  ->  DV = {dv:.2f}")

    print("\n=== Full PCI calculation ===")
    result = calc.compute_pci(
        [
            {
                "distress_type": "pothole",
                "severity": "low",
                "density": 0.3604,
                "count": 9,
                "deduct_value": 40.83,
            },
            {
                "distress_type": "pothole",
                "severity": "medium",
                "density": 0.0801,
                "count": 2,
                "deduct_value": 31.07,
            },
            {
                "distress_type": "linear",
                "severity": "low",
                "density": 0.04,
                "count": 1,
                "deduct_value": 4.26,
            },
            {
                "distress_type": "alligator",
                "severity": "low",
                "density": 0.04,
                "count": 1,
                "deduct_value": 6.89,
            },
        ]
    )
    print(f"  PCI       : {result['final_pci']}")
    print(f"  Condition : {result['condition_rating']}")
    # print(f"  CDV       : {result['cdv']}")
    # print(f"  TDV       : {result['tdv']}")
    # print(f"  DV list   : {result['deduct_values']}")
    # print(f"  Details   :")
    # for obs in result["observations"]:
    #     print(f"    {obs}")
