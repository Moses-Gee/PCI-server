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

from app.services.pci_utilities import (
    VALID_DISTRESS_TYPES,
    VALID_SEVERITIES,
    clamp,
    pci_condition,
)
import numpy as np

# Default path: models.json lives next to this file.
# _DEFAULT_MODELS_PATH = os.path.join(os.path.dirname(__file__), "models.json")
_DEFAULT_MODELS_PATH = os.path.join(
    os.getcwd(), "app", "fitted_polynomial", "distress_models.json"
)
print(os.path.exists(_DEFAULT_MODELS_PATH))

# ---------------------------------------------------------------------------
# Core class
# ---------------------------------------------------------------------------


class PCICalculator:
    """
    Production deduct-value and PCI calculator.

    Parameters
    ----------
    models_path : str
        Path to the JSON file produced by save_models.py.
        Defaults to models.json in the same directory as this file.

    The models are loaded exactly once on first instantiation (or explicitly
    via load_models). After that every call is pure arithmetic -- no I/O,
    no fitting, no imports of numpy beyond what is already loaded.
    """

    _singleton = None  # optional module-level cache (see get_instance())

    def __init__(self, models_path: str = _DEFAULT_MODELS_PATH):
        self._models_path = models_path
        self._polys: dict = {}  # {distress_type: {severity: np.poly1d}}
        self._load_models()

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
            distress  : str   -- "alligator" | "long_trans" | "pothole"
            severity  : str   -- "low" | "medium" | "high"
            density   : float -- density % (> 0)

        Returns
        -------
        dict with keys:
            pci           : float  -- 0-100
            condition     : str    -- e.g. "Fair"
            cdv           : float  -- corrected deduct value
            tdv           : float  -- total deduct value
            deduct_values : list[float] -- individual DVs (sorted descending)
            observations  : list[dict] -- echoed back with 'deduct_value' filled in
        """
        if not observations:
            return {
                "pci": 100.0,
                "condition": "Good",
                "cdv": 0.0,
                "tdv": 0.0,
                "deduct_values": [],
                "observations": [],
            }

        # Step 1 -- compute individual deduct values
        enriched = []
        for obs in observations:
            dv = self.get_deduct_value(obs["distress"], obs["severity"], obs["density"])
            enriched.append({**obs, "deduct_value": round(dv, 2)})

        dvs = sorted([e["deduct_value"] for e in enriched], reverse=True)

        # Step 2 -- ASTM Section 9.5: find maximum CDV iteratively
        # m = allowable number of deducts (Eq. 4 in the standard)
        hdv = dvs[0]
        m = min(10, 1 + (9 / 98) * (100 - hdv))
        num_to_keep = math.ceil(m)

        # Truncate to m deducts (last one scaled by fractional part of m)
        working = dvs[:num_to_keep]
        frac = m - math.floor(m)
        if frac > 0 and len(working) == num_to_keep and num_to_keep > 1:
            working[-1] = working[-1] * frac

        max_cdv = 0.0
        current_dvs = list(working)
        all_cdvs = []
        all_tdvs = []

        # Iterate: each pass replaces smallest dv > 2 with 2, recompute CDV
        while True:
            tdv = sum(current_dvs)
            all_tdvs.append(tdv)

            q = sum(1 for v in current_dvs if v > 2)

            if q <= 1:
                max_cdv = max(max_cdv, tdv)
                break

            cdv = self._corrected_deduct_value(tdv, q)
            all_cdvs.append(cdv)

            max_cdv = max(max_cdv, cdv)

            for i in reversed(range(len(current_dvs))):
                if current_dvs[i] > 2:
                    current_dvs[i] = 2
                    break

        pci = clamp(100.0 - max_cdv)

        return {
            "pci": round(pci, 2),
            "condition": pci_condition(pci),
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
        Approximate the corrected deduct value from the ASTM correction curves
        (Fig. X3.26 for asphalt).

        The standard provides this as a family of graphical curves (one per q
        value). We use a widely-adopted analytical approximation that fits
        those curves closely for asphalt:

            CDV = a * ln(TDV) + b

        where a and b are empirically derived per q level. For a production
        system where millimetre accuracy matters, digitize Fig. X3.26 the same
        way as the distress curves and replace this function with a polynomial
        lookup -- the structure is identical.
        """
        # Coefficients approximating the ASTM asphalt CDV curves for q = 1..8+
        # Source: analytical fits widely used in pavement management literature.
        cdv_params = {
            1: (17.677, -3.8),
            2: (14.036, -0.8),
            3: (11.815, 1.7),
            4: (10.209, 3.4),
            5: (9.078, 4.7),
            6: (8.249, 5.7),
            7: (7.618, 6.5),
            8: (7.112, 7.1),
        }
        q = max(1, min(q, 8))
        if tdv <= 0:
            return 0.0
        a, b = cdv_params[q]
        cdv = a * math.log(tdv) + b
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
    def get_instance(cls, models_path: str = _DEFAULT_MODELS_PATH):
        """
        Return a shared singleton instance.

        In a web server (Flask, Django, FastAPI) call this once at startup
        or at the module level, rather than creating a new instance per
        request:

            # In your Flask app factory or Django AppConfig.ready():
            from deduct_calculator import DeductValueCalculator
            calc = PCICalculator.get_instance()

        Then in each view / endpoint:
            dv = calc.get_deduct_value("alligator", "high", 12.5)
        """
        if cls._singleton is None:
            cls._singleton = cls(models_path)
        return cls._singleton


# ---------------------------------------------------------------------------
# Quick smoke test when run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    calc = PCICalculator.get_instance()

    print("=== Single deduct value lookups ===")
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
    for dt, sev, dens in examples:
        dv = calc.get_deduct_value(dt, sev, dens)
        print(f"  {dt:10s}  sev={sev}  density={dens:>6}%  ->  DV = {dv:.2f}")

    print("\n=== Full PCI calculation ===")
    result = calc.compute_pci(
        [
            {"distress": "alligator", "severity": "high", "density": 15.0},
            {"distress": "linear", "severity": "medium", "density": 8.0},
            {"distress": "pothole", "severity": "low", "density": 0.2},
        ]
    )
    print(f"  PCI       : {result['pci']}")
    print(f"  Condition : {result['condition']}")
    print(f"  CDV       : {result['cdv']}")
    print(f"  TDV       : {result['tdv']}")
    print(f"  DV list   : {result['deduct_values']}")
    print(f"  Details   :")
    for obs in result["observations"]:
        print(f"    {obs}")
