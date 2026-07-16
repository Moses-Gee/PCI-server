"""
ASTM D6433 PCI Report Generator
Replicates the exact calculation worksheets from the standard:
- Fig. 4: Condition Survey Data Sheet (distress inventory)
- Fig. 6: PCI Calculation Sheet (CDV iteration table)
- Detection images with annotation results
- Map section (placeholder / static map)
"""

import io
import requests
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import mm, inch
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    PageBreak,
    Image as RLImage,
    KeepTogether,
)
from reportlab.pdfgen import canvas
from reportlab.platypus.flowables import Flowable
from io import BytesIO
import math

# ── Page dimensions ───────────────────────────────────────────────────────────
W, H = letter  # 8.5 × 11 inches — matches ASTM sheets

# ── Colour palette (matches ASTM forms exactly) ───────────────────────────────
C_BLACK = colors.black
C_WHITE = colors.white
C_LGRAY = colors.HexColor("#e8e8e8")
C_MGRAY = colors.HexColor("#b0b0b0")
C_DGRAY = colors.HexColor("#404040")
C_HEADER = colors.HexColor("#1a1a2e")  # dark navy — form header
C_STRIPE = colors.HexColor("#f5f5f5")  # alternating row
C_HIGHLIGHT = colors.HexColor("#fff9c4")  # yellow for max CDV row

PCI_COLOR = {
    "Good": colors.HexColor("#27ae60"),
    "Satisfactory": colors.HexColor("#52be80"),
    "Fair": colors.HexColor("#f39c12"),
    "Poor": colors.HexColor("#e67e22"),
    "Very Poor": colors.HexColor("#e74c3c"),
    "Serious": colors.HexColor("#c0392b"),
    "Failed": colors.HexColor("#7b241c"),
}


# ── Typography helpers ────────────────────────────────────────────────────────
def ps(name, **kw):
    return ParagraphStyle(name, **kw)


STYLE = {
    "form_title": ps(
        "ft",
        fontSize=11,
        fontName="Helvetica-Bold",
        textColor=C_WHITE,
        alignment=TA_CENTER,
    ),
    "form_sub": ps(
        "fs", fontSize=8, fontName="Helvetica", textColor=C_WHITE, alignment=TA_CENTER
    ),
    "label": ps("lb", fontSize=7, fontName="Helvetica-Bold", textColor=C_DGRAY),
    "value": ps("vl", fontSize=8, fontName="Helvetica", textColor=C_BLACK),
    "th": ps(
        "th",
        fontSize=7,
        fontName="Helvetica-Bold",
        textColor=C_WHITE,
        alignment=TA_CENTER,
    ),
    "td": ps(
        "td", fontSize=7, fontName="Helvetica", textColor=C_BLACK, alignment=TA_CENTER
    ),
    "td_left": ps(
        "tdl", fontSize=7, fontName="Helvetica", textColor=C_BLACK, alignment=TA_LEFT
    ),
    "section": ps(
        "sc",
        fontSize=9,
        fontName="Helvetica-Bold",
        textColor=C_DGRAY,
        spaceBefore=6,
        spaceAfter=2,
    ),
    "normal": ps("nm", fontSize=8, fontName="Helvetica", textColor=C_BLACK),
    "small": ps("sm", fontSize=6.5, fontName="Helvetica", textColor=C_DGRAY),
    "pci_big": ps(
        "pb",
        fontSize=48,
        fontName="Helvetica-Bold",
        textColor=C_BLACK,
        alignment=TA_CENTER,
    ),
    "pci_label": ps(
        "pl", fontSize=8, fontName="Helvetica", textColor=C_DGRAY, alignment=TA_CENTER
    ),
    "rec_head": ps("rh", fontSize=9, fontName="Helvetica-Bold", textColor=C_BLACK),
    "rec_body": ps("rb", fontSize=8, fontName="Helvetica", textColor=C_BLACK),
}


# ── Thin line flowable ────────────────────────────────────────────────────────
def rule(width="100%", thickness=0.5, color=C_MGRAY):
    return HRFlowable(
        width=width, thickness=thickness, color=color, spaceAfter=2, spaceBefore=2
    )


# ── Helper: bold field row used in header boxes ───────────────────────────────
def field_row(label, value, lw=55 * mm, vw=80 * mm):
    row = [[Paragraph(label, STYLE["label"]), Paragraph(str(value), STYLE["value"])]]
    t = Table(row, colWidths=[lw, vw])
    t.setStyle(
        TableStyle(
            [
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    return t


# ─────────────────────────────────────────────────────────────────────────────
# PCI Calculation Iteration (replicates ASTM Fig. 6)
# ─────────────────────────────────────────────────────────────────────────────
def compute_cdv_iterations(deduct_values: list) -> dict:
    """
    Simulate the ASTM CDV iteration process.
    Returns: {
        'iterations': [{'tdv': float, 'q': int, 'cdv': float}],
        'max_cdv': float,
        'max_cdv_index': int
    }
    Uses a simplified correction curve (linear interpolation based on Fig. X4.15 / X4.20)
    """
    # Sort descending
    dvs = sorted(deduct_values, reverse=True)
    if not dvs:
        return {"iterations": [], "max_cdv": 0, "max_cdv_index": -1}

    # Calculate m
    hdv = dvs[0]
    m = min(10, 1 + (9 / 98) * (100 - hdv))
    # Keep only m largest (including fractional part)
    if len(dvs) > m:
        # fractional part
        frac = m - int(m)
        if frac > 0:
            # keep int(m) full values + fractional of the next
            keep = int(m) + (1 if frac > 0 else 0)
            selected = dvs[:keep]
            # reduce the last value by fraction
            if len(selected) > int(m):
                selected[-1] *= frac
        else:
            selected = dvs[: int(m)]
    else:
        selected = dvs[:]

    iterations = []
    working = selected[:]
    max_cdv = 0
    max_idx = 0
    idx = 0

    while True:
        # Compute TDV
        tdv = sum(working)
        # q = number of deducts > 2.0
        q = sum(1 for v in working if v > 2.0)
        # Compute CDV using approximate correction curve (ASTM Fig. X4.15 / X4.20)
        cdv = _approx_cdv(tdv, q, pavement_type="ac")
        if cdv > max_cdv:
            max_cdv = cdv
            max_idx = idx
        iterations.append({"tdv": tdv, "q": q, "cdv": cdv})
        # Reduce the smallest deduct > 2.0 to 2.0
        reduced = False
        for i in range(len(working) - 1, -1, -1):
            if working[i] > 2.0:
                working[i] = 2.0
                reduced = True
                break
        if not reduced or q <= 1:
            break
        idx += 1

    # If no iterations (should not happen), add one
    if not iterations:
        tdv = sum(dvs)
        q = sum(1 for v in dvs if v > 2.0)
        cdv = _approx_cdv(tdv, q, "ac")
        iterations.append({"tdv": tdv, "q": q, "cdv": cdv})
        max_cdv = cdv
        max_idx = 0

    return {
        "iterations": iterations,
        "max_cdv": max_cdv,
        "max_cdv_index": max_idx,
        "initial_dvs": dvs,
    }


def _approx_cdv(tdv: float, q: int, pavement_type: str = "ac") -> float:
    """
    Approximate CDV from TDV and q using simplified curves.
    For AC: uses Fig. X4.15 (Flexible Pavements).
    For PCC: uses Fig. X4.20 (Jointed Concrete Pavements).
    """
    # Simple piecewise linear approximation (based on typical curves)
    if q <= 1:
        return tdv
    # For q >= 2, CDV = TDV * factor, where factor depends on q and TDV
    # We'll use a simplified approach
    if pavement_type == "ac":
        if q >= 2:
            if tdv <= 50:
                factor = 0.85 - 0.15 * (tdv / 50)
            else:
                factor = 0.7 - 0.2 * ((tdv - 50) / 50)
            factor = max(0.4, min(0.85, factor))
        else:
            factor = 1.0
    else:  # PCC
        if q >= 2:
            if tdv <= 60:
                factor = 0.8 - 0.15 * (tdv / 60)
            else:
                factor = 0.65 - 0.15 * ((tdv - 60) / 40)
            factor = max(0.4, min(0.8, factor))
        else:
            factor = 1.0
    return round(tdv * factor, 1)


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 1 — Cover / Summary
# ─────────────────────────────────────────────────────────────────────────────
def _cover_page(report_name, network_name, section, pci_result, include_options):
    story = []

    # ── Top banner ────────────────────────────────────────────────────────────
    banner_data = [
        [
            Paragraph(
                "PAVEMENT CONDITION INDEX (PCI) INSPECTION REPORT", STYLE["form_title"]
            )
        ],
        [
            Paragraph(
                "Standard Practice for Roads and Parking Lots — ASTM D6433",
                STYLE["form_sub"],
            )
        ],
    ]
    banner = Table(banner_data, colWidths=[W - 2 * inch])
    banner.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (0, 0), 10),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
            ]
        )
    )
    story.append(banner)
    story.append(Spacer(1, 6 * mm))

    # ── Identity block ────────────────────────────────────────────────────────
    computed = getattr(pci_result, "computed_at", datetime.utcnow())
    if isinstance(computed, str):
        computed = datetime.fromisoformat(computed)

    id_data = [
        [
            Paragraph("REPORT NAME:", STYLE["label"]),
            Paragraph(report_name, STYLE["value"]),
            Paragraph("NETWORK:", STYLE["label"]),
            Paragraph(network_name, STYLE["value"]),
        ],
        [
            Paragraph("SECTION:", STYLE["label"]),
            Paragraph(section.name, STYLE["value"]),
            Paragraph("DATE:", STYLE["label"]),
            Paragraph(computed.strftime("%Y-%m-%d"), STYLE["value"]),
        ],
        [
            Paragraph("AREA (m²):", STYLE["label"]),
            Paragraph(f"{section.area:.2f}", STYLE["value"]),
            Paragraph("COMPUTED BY:", STYLE["label"]),
            Paragraph("PCI Management System", STYLE["value"]),
        ],
    ]
    id_table = Table(id_data, colWidths=[38 * mm, 60 * mm, 30 * mm, 65 * mm])
    id_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_LGRAY),
                ("BACKGROUND", (0, 0), (-1, -1), C_STRIPE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(id_table)
    story.append(Spacer(1, 5 * mm))

    # ── PCI Score + Rating Scale side by side ────────────────────────────────
    if "PCI Score" in include_options:
        pci = pci_result.final_pci
        rating = pci_result.condition_rating
        pci_color = PCI_COLOR.get(rating, C_DGRAY)

        # --- Left: Score block (reduced font) ---
        score_style = ParagraphStyle(
            "sc",
            fontSize=24,
            fontName="Helvetica-Bold",
            spaceAfter=10,
            textColor=pci_color,
            alignment=TA_CENTER,
        )
        rating_style = ParagraphStyle(
            "rs",
            fontSize=10,
            fontName="Helvetica-Bold",
            spaceBefore=10,
            textColor=pci_color,
            alignment=TA_CENTER,
        )
        score_block = Table(
            [
                [Paragraph(f"{pci:.1f}", score_style)],
                [Paragraph(rating, rating_style)],
                [Paragraph("Pavement Condition Index", STYLE["pci_label"])],
            ],
            colWidths=[45 * mm],
        )
        score_block.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 2, pci_color),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )

        # --- Right: ASTM Fig.1 Rating Scale (compact) ---
        scale = [
            ("100-85", "Good", "#27ae60"),
            ("85-70", "Satisfactory", "#52be80"),
            ("70-55", "Fair", "#f39c12"),
            ("55-40", "Poor", "#e67e22"),
            ("40-25", "Very Poor", "#e74c3c"),
            ("25-10", "Serious", "#c0392b"),
            ("10-0", "Failed", "#7b241c"),
        ]
        # Smaller fonts for the scale bar
        scale_style = ParagraphStyle(
            "ss", fontSize=5, textColor=C_WHITE, alignment=TA_CENTER
        )
        bar_cells = [
            [Paragraph(f"<b>{lbl}</b><br/>{rng}", scale_style) for rng, lbl, _ in scale]
        ]
        bar_w = (W - 2 * inch - 45 * mm - 5 * mm) / 7  # remaining width for 7 blocks
        if bar_w < 10 * mm:
            bar_w = 10 * mm  # fallback
        scale_bar = Table(bar_cells, colWidths=[bar_w] * 7, rowHeights=[16 * mm])
        scale_bar.setStyle(
            TableStyle(
                [
                    *[
                        ("BACKGROUND", (i, 0), (i, 0), colors.HexColor(scale[i][2]))
                        for i in range(7)
                    ],
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                    ("INNERGRID", (0, 0), (-1, -1), 0.2, C_WHITE),
                ]
            )
        )

        # --- Key metrics under the scale bar (reduced font) ---
        metric_style = ParagraphStyle(
            "ms",
            fontSize=6,
            fontName="Helvetica",
            textColor=C_DGRAY,
            alignment=TA_CENTER,
        )
        metrics_data = [
            [
                Paragraph("Max CDV", metric_style),
                Paragraph("TDV Start", metric_style),
                Paragraph("Total DVs", metric_style),
                Paragraph("Observations", metric_style),
            ],
            [
                Paragraph(f"{pci_result.max_cdv:.1f}", metric_style),
                Paragraph(f"{pci_result.tdv_start:.1f}", metric_style),
                Paragraph(str(len(pci_result.deduct_values)), metric_style),
                Paragraph(str(len(pci_result.observations)), metric_style),
            ],
        ]
        bar_w2 = bar_w * 7 / 4  # distribute evenly
        metrics_t = Table(metrics_data, colWidths=[bar_w2] * 4)
        metrics_t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), C_LGRAY),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, C_LGRAY),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )

        # Combine right column: scale bar + spacing + metrics
        right_col = Table(
            [[scale_bar], [Spacer(1, 2 * mm)], [metrics_t]],
            colWidths=[bar_w * 7],
        )
        right_col.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ]
            )
        )

        # Put left and right together
        combined = Table(
            [[score_block, Spacer(5 * mm, 1), right_col]],
            colWidths=[45 * mm, 5 * mm, bar_w * 7],
        )
        combined.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(combined)
        story.append(Spacer(1, 5 * mm))

    # ── Recommendations ───────────────────────────────────────────────────────
    if "Recommendations" in include_options:
        story.append(rule())
        story.append(Paragraph("MAINTENANCE RECOMMENDATIONS", STYLE["section"]))
        action, detail = _get_recommendation(pci_result.final_pci)
        rec_data = [
            [Paragraph(f"Recommended Action: {action}", STYLE["rec_head"])],
            [Paragraph(detail, STYLE["rec_body"])],
        ]
        rec_t = Table(rec_data, colWidths=[W - 2 * inch])
        rec_t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), C_STRIPE),
                    ("BOX", (0, 0), (-1, -1), 0.8, C_MGRAY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(rec_t)

    return story


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 2 — Condition Survey Data Sheet  (ASTM Fig. 4)
# ─────────────────────────────────────────────────────────────────────────────
def _astm_survey_sheet(section, pci_result, computed_at):
    """Replicates ASTM D6433 Fig. 4 — Condition Survey Data Sheet."""
    story = []
    story.append(PageBreak())

    # ── Form header ───────────────────────────────────────────────────────────
    header_data = [
        [
            Paragraph(
                "ASPHALT SURFACED ROADS AND PARKING LOTS\nCONDITION SURVEY DATA SHEET FOR SAMPLE UNIT",
                ParagraphStyle(
                    "hdr",
                    fontSize=9,
                    fontName="Helvetica-Bold",
                    textColor=C_WHITE,
                    alignment=TA_CENTER,
                ),
            ),
        ]
    ]
    header_t = Table(header_data, colWidths=[W - 2 * inch])
    header_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(header_t)

    # ── Identity row ──────────────────────────────────────────────────────────
    id_row = [
        [
            Paragraph(f"SECTION:  {section.name}", STYLE["label"]),
            Paragraph(f"AREA:  {section.area:.2f} m²", STYLE["label"]),
            Paragraph(f"DATE:  {computed_at.strftime('%Y-%m-%d')}", STYLE["label"]),
        ]
    ]
    id_t = Table(id_row, colWidths=[(W - 2 * inch) / 3] * 3)
    id_t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, C_LGRAY),
                ("BACKGROUND", (0, 0), (-1, -1), C_STRIPE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(id_t)
    story.append(Spacer(1, 3 * mm))

    # ── Distress inventory table ──────────────────────────────────────────────
    col_w = [
        W - 2 * inch - 30 * mm - 30 * mm - 30 * mm,  # Distress / Severity
        30 * mm,
        30 * mm,
        30 * mm,
    ]  # Total, Density%, Deduct Value

    header_row = [
        Paragraph("DISTRESS TYPE / SEVERITY", STYLE["th"]),
        Paragraph("TOTAL\nQUANTITY", STYLE["th"]),
        Paragraph("DENSITY\n(%)", STYLE["th"]),
        Paragraph("DEDUCT\nVALUE", STYLE["th"]),
    ]
    rows = [header_row]

    # Use observations from pci_result
    for i, obs in enumerate(pci_result.observations):
        dtype = obs.get("distress_type", "")
        sev = obs.get("severity", "")
        count = obs.get("count", 0)
        density = obs.get("density", 0)
        dv = obs.get("deduct_value", 0)
        bg = C_STRIPE if i % 2 == 0 else C_WHITE
        rows.append(
            [
                Paragraph(f"{dtype}  —  {sev.capitalize()}", STYLE["td_left"]),
                Paragraph(str(count), STYLE["td"]),
                Paragraph(f"{density:.4f}", STYLE["td"]),
                Paragraph(f"{dv:.1f}", STYLE["td"]),
            ]
        )

    # Pad to at least 12 rows
    while len(rows) < 13:
        rows.append([Paragraph("", STYLE["td"])] * 4)

    survey_t = Table(rows, colWidths=col_w, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("GRID", (0, 0), (-1, -1), 0.4, C_MGRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), C_STRIPE))
    survey_t.setStyle(TableStyle(style_cmds))
    story.append(survey_t)
    story.append(Spacer(1, 3 * mm))

    # ── Deduct values sorted list ─────────────────────────────────────────────
    dvs = sorted(pci_result.deduct_values, reverse=True)
    story.append(Paragraph("DEDUCT VALUES (descending order):", STYLE["label"]))
    dv_chips = "   ".join([f"{v:.1f}" for v in dvs])
    story.append(
        Paragraph(
            dv_chips,
            ParagraphStyle(
                "dvc", fontSize=9, fontName="Helvetica-Bold", textColor=C_DGRAY
            ),
        )
    )
    story.append(Spacer(1, 2 * mm))

    # ── m value ───────────────────────────────────────────────────────────────
    hdv = dvs[0] if dvs else 0
    m = min(10, 1 + (9 / 98) * (100 - hdv))
    story.append(
        Paragraph(
            f"Allowable number of deducts (m) = 1 + (9/98)(100 − HDV) = "
            f"1 + (9/98)(100 − {hdv:.1f}) = <b>{m:.1f}</b>",
            STYLE["small"],
        )
    )

    return story


# ─────────────────────────────────────────────────────────────────────────────
# PAGE 3 — PCI Calculation Sheet  (ASTM Fig. 6)
# ─────────────────────────────────────────────────────────────────────────────
def _astm_calculation_sheet(pci_result):
    """Replicates ASTM D6433 Fig. 6 — Corrected PCI Value calculation."""
    story = []
    story.append(PageBreak())

    # ── Header ───────────────────────────────────────────────────────────────
    header_data = [
        [
            Paragraph(
                "CALCULATION OF CORRECTED PCI VALUE — FLEXIBLE PAVEMENT",
                ParagraphStyle(
                    "hdr",
                    fontSize=9,
                    fontName="Helvetica-Bold",
                    textColor=C_WHITE,
                    alignment=TA_CENTER,
                ),
            ),
        ]
    ]
    header_t = Table(header_data, colWidths=[W - 2 * inch])
    header_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(header_t)
    story.append(Spacer(1, 3 * mm))

    # ── Prepare data ────────────────────────────────────────────────────────
    dvs = sorted(pci_result.deduct_values, reverse=True)
    # If pci_result has all_cdvs/all_tdvs, use them; otherwise compute
    if hasattr(pci_result, "all_cdvs") and pci_result.all_cdvs:
        all_tdvs = getattr(pci_result, "all_tdvs", [])
        all_cdvs = pci_result.all_cdvs
        if len(all_cdvs) != len(all_tdvs):
            # fallback to recompute
            iter_data = compute_cdv_iterations(dvs)
            all_tdvs = [it["tdv"] for it in iter_data["iterations"]]
            all_cdvs = [it["cdv"] for it in iter_data["iterations"]]
            max_idx = iter_data["max_cdv_index"]
        else:
            max_idx = all_cdvs.index(max(all_cdvs)) if all_cdvs else 0
    else:
        iter_data = compute_cdv_iterations(dvs)
        all_tdvs = [it["tdv"] for it in iter_data["iterations"]]
        all_cdvs = [it["cdv"] for it in iter_data["iterations"]]
        max_idx = iter_data["max_cdv_index"]

    # ── Build iteration rows ────────────────────────────────────────────────
    max_dv_cols = min(len(dvs), 10)  # maximum 10 deduct columns
    # Header
    dv_headers = [Paragraph(f"DV{i+1}", STYLE["th"]) for i in range(max_dv_cols)]
    header_row = (
        [Paragraph("#", STYLE["th"])]
        + dv_headers
        + [
            Paragraph("TOTAL", STYLE["th"]),
            Paragraph("q", STYLE["th"]),
            Paragraph("CDV", STYLE["th"]),
        ]
    )
    rows = [header_row]

    # Simulate reduction process
    working = list(dvs[:max_dv_cols])
    for i in range(len(all_cdvs)):
        # Build row: iteration number, current DVs, TDV, q, CDV
        dv_cells = [
            Paragraph(f"{v:.1f}" if v > 0 else "", STYLE["td"]) for v in working
        ]
        # Pad to max_dv_cols
        while len(dv_cells) < max_dv_cols:
            dv_cells.append(Paragraph("", STYLE["td"]))

        q = sum(1 for v in working if v > 2.0)
        row = (
            [Paragraph(str(i + 1), STYLE["td"])]
            + dv_cells
            + [
                Paragraph(f"{all_tdvs[i]:.1f}", STYLE["td"]),
                Paragraph(str(q), STYLE["td"]),
                Paragraph(f"{all_cdvs[i]:.1f}", STYLE["td"]),
            ]
        )
        rows.append(row)

        # Reduce for next iteration (except last)
        if i < len(all_cdvs) - 1:
            for j in range(len(working) - 1, -1, -1):
                if working[j] > 2.0:
                    working[j] = 2.0
                    break

    # Pad to at least 10 rows (like the blank ASTM sheet)
    num_cols = 1 + max_dv_cols + 3
    while len(rows) < 11:
        rows.append([Paragraph("", STYLE["td"])] * num_cols)

    # ── Column widths ────────────────────────────────────────────────────────
    avail = W - 2 * inch
    fixed = 12 * mm + 22 * mm + 12 * mm + 22 * mm  # #, TOTAL, q, CDV
    dv_w = (avail - fixed) / max_dv_cols if max_dv_cols > 0 else 20 * mm
    col_widths = [12 * mm] + [dv_w] * max_dv_cols + [22 * mm, 12 * mm, 22 * mm]

    # ── Table styling ────────────────────────────────────────────────────────
    style_cmds = [
        # Header background
        ("BACKGROUND", (0, 0), (-1, 0), C_HEADER),
        ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        # Grid: both inner and outer
        ("GRID", (0, 0), (-1, -1), 0.5, C_MGRAY),
        ("BOX", (0, 0), (-1, -1), 0.8, C_DGRAY),
        # Cell padding
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # Highlight max CDV row (index + 1 because header is row 0)
    highlight_row = max_idx + 1
    style_cmds.append(
        ("BACKGROUND", (0, highlight_row), (-1, highlight_row), C_HIGHLIGHT)
    )
    style_cmds.append(
        ("FONTNAME", (0, highlight_row), (-1, highlight_row), "Helvetica-Bold")
    )

    # Alternate row shading for data rows (optional, but ASTM shows plain)
    for i in range(2, len(rows)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), C_STRIPE))

    # Build table
    calc_t = Table(rows, colWidths=col_widths, repeatRows=1)
    calc_t.setStyle(TableStyle(style_cmds))
    story.append(calc_t)
    story.append(Spacer(1, 4 * mm))

    # ── PCI summary box ──────────────────────────────────────────────────────
    pci_color = PCI_COLOR.get(pci_result.condition_rating, C_DGRAY)
    summary_data = [
        [
            Paragraph("Max CDV  =", STYLE["label"]),
            Paragraph(f"{pci_result.max_cdv:.1f}", STYLE["value"]),
            Paragraph("", STYLE["label"]),
            Paragraph("", STYLE["label"]),
        ],
        [
            Paragraph("PCI  =  100 − Max CDV  =", STYLE["label"]),
            Paragraph(
                f"{pci_result.final_pci:.1f}",
                ParagraphStyle(
                    "pv", fontSize=14, fontName="Helvetica-Bold", textColor=pci_color
                ),
            ),
            Paragraph("Rating  =", STYLE["label"]),
            Paragraph(
                pci_result.condition_rating,
                ParagraphStyle(
                    "rv", fontSize=12, fontName="Helvetica-Bold", textColor=pci_color
                ),
            ),
        ],
    ]
    summary_t = Table(summary_data, colWidths=[60 * mm, 30 * mm, 30 * mm, 50 * mm])
    summary_t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1.5, pci_color),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("BACKGROUND", (0, 0), (-1, -1), C_STRIPE),
            ]
        )
    )
    story.append(KeepTogether([summary_t]))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            "★ Highlighted row = maximum CDV used for PCI calculation", STYLE["small"]
        )
    )

    return story


# ─────────────────────────────────────────────────────────────────────────────
# Detection images page
# ─────────────────────────────────────────────────────────────────────────────
def _download_image(url: str, max_w: float, max_h: float):
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        buf = BytesIO(resp.content)
        img = RLImage(buf)
        iw, ih = img.imageWidth, img.imageHeight
        scale = min(max_w / iw, max_h / ih, 1.0)
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        return img
    except Exception:
        return None


def _detection_images_page(sample_units, include_options):
    if "Detection Images" not in include_options:
        return []

    story = []
    story.append(PageBreak())

    header_data = [
        [
            Paragraph(
                "DETECTION IMAGES AND AI ANALYSIS RESULTS",
                ParagraphStyle(
                    "hdr",
                    fontSize=9,
                    fontName="Helvetica-Bold",
                    textColor=C_WHITE,
                    alignment=TA_CENTER,
                ),
            ),
        ]
    ]
    header_t = Table(header_data, colWidths=[W - 2 * inch])
    header_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(header_t)
    story.append(Spacer(1, 4 * mm))

    avail_w = W - 2 * inch
    img_w = (avail_w - 6 * mm) / 2
    img_h = 70 * mm

    for su in sample_units:
        if not hasattr(su, "images") or not su.images:
            continue

        orig_url = next((img.public_url for img in su.images if img.is_original), None)
        pred_url = next((img.public_url for img in su.images if img.is_annotated), None)

        story.append(
            Paragraph(
                f"Sample Unit: {su.name}  |  Status: {su.inference_status}  |  "
                f"Pixel/mm: {su.pixel_to_mm_factor or 'N/A'}",
                STYLE["section"],
            )
        )

        orig_img = _download_image(orig_url, img_w, img_h) if orig_url else None
        pred_img = _download_image(pred_url, img_w, img_h) if pred_url else None

        orig_cell = orig_img or Paragraph(
            "Original image not available", STYLE["small"]
        )
        pred_cell = pred_img or Paragraph(
            "Annotated image not available", STYLE["small"]
        )

        img_row = Table(
            [
                [
                    Paragraph("Original Image", STYLE["label"]),
                    Paragraph("AI Annotated Image", STYLE["label"]),
                ],
                [orig_cell, pred_cell],
            ],
            colWidths=[img_w + 3 * mm, img_w + 3 * mm],
        )
        img_row.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                    ("INNERGRID", (0, 0), (-1, -1), 0.3, C_LGRAY),
                    ("BACKGROUND", (0, 0), (-1, 0), C_STRIPE),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(img_row)
        story.append(Spacer(1, 2 * mm))

        # Detection results table
        if hasattr(su, "detections") and su.detections:
            det_header = [
                Paragraph("Distress Type", STYLE["th"]),
                Paragraph("Severity", STYLE["th"]),
                Paragraph("Confidence", STYLE["th"]),
                Paragraph("Area (mm²)", STYLE["th"]),
                Paragraph("Width (mm)", STYLE["th"]),
                Paragraph("Length (mm)", STYLE["th"]),
            ]
            det_rows = [det_header]
            for i, d in enumerate(su.detections):
                m = d.metrics or {}
                bg = C_STRIPE if i % 2 == 0 else C_WHITE
                det_rows.append(
                    [
                        Paragraph(d.distress_type or "", STYLE["td_left"]),
                        Paragraph((d.severity or "").capitalize(), STYLE["td"]),
                        Paragraph(f"{(d.confidence or 0)*100:.0f}%", STYLE["td"]),
                        Paragraph(f"{m.get('area',0):.2f}", STYLE["td"]),
                        Paragraph(f"{m.get('avg_width',0):.2f}", STYLE["td"]),
                        Paragraph(f"{m.get('length',0):.2f}", STYLE["td"]),
                    ]
                )
            cw = avail_w / 6
            det_t = Table(det_rows, colWidths=[cw] * 6, repeatRows=1)
            det_style = [
                ("BACKGROUND", (0, 0), (-1, 0), C_DGRAY),
                ("TEXTCOLOR", (0, 0), (-1, 0), C_WHITE),
                ("GRID", (0, 0), (-1, -1), 0.3, C_MGRAY),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 6.5),
            ]
            for i in range(1, len(det_rows)):
                if i % 2 == 0:
                    det_style.append(("BACKGROUND", (0, i), (-1, i), C_STRIPE))
            det_t.setStyle(TableStyle(det_style))
            story.append(det_t)
        else:
            note = (
                f"No AI detections — manual entry: {su.distress_type or 'N/A'} / {su.severity or 'N/A'}"
                if su.distress_type
                else "No detections or manual data."
            )
            story.append(Paragraph(note, STYLE["small"]))

        story.append(Spacer(1, 5 * mm))

    return story


# ─────────────────────────────────────────────────────────────────────────────
# Sample unit details (distress inventory per unit)
# ─────────────────────────────────────────────────────────────────────────────
def _sample_unit_details_page(sample_units, include_options):
    if "Sample Unit Details" not in include_options:
        return []

    story = []
    story.append(PageBreak())

    header_data = [
        [
            Paragraph(
                "SAMPLE UNIT DISTRESS INVENTORY",
                ParagraphStyle(
                    "hdr",
                    fontSize=9,
                    fontName="Helvetica-Bold",
                    textColor=C_WHITE,
                    alignment=TA_CENTER,
                ),
            ),
        ]
    ]
    header_t = Table(header_data, colWidths=[W - 2 * inch])
    header_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(header_t)
    story.append(Spacer(1, 4 * mm))

    for su in sample_units:
        story.append(
            Paragraph(
                f"Sample Unit: <b>{su.name}</b>   |   "
                f"px/mm: {su.pixel_to_mm_factor or 'N/A'}   |   "
                f"Status: {su.inference_status}",
                STYLE["section"],
            )
        )

        if su.detections:
            det_header = [
                Paragraph("Distress Type", STYLE["th"]),
                Paragraph("Severity", STYLE["th"]),
                Paragraph("Qty", STYLE["th"]),
                Paragraph("Confidence", STYLE["th"]),
                Paragraph("Area mm²", STYLE["th"]),
                Paragraph("Width mm", STYLE["th"]),
                Paragraph("Length mm", STYLE["th"]),
                Paragraph("Perimeter mm", STYLE["th"]),
            ]
            det_rows = [det_header]
            for i, d in enumerate(su.detections):
                m = d.metrics or {}
                det_rows.append(
                    [
                        Paragraph(d.distress_type or "", STYLE["td_left"]),
                        Paragraph((d.severity or "").capitalize(), STYLE["td"]),
                        Paragraph(str(d.quantity or 0), STYLE["td"]),
                        Paragraph(f"{(d.confidence or 0)*100:.0f}%", STYLE["td"]),
                        Paragraph(f"{m.get('area',0):.2f}", STYLE["td"]),
                        Paragraph(f"{m.get('avg_width',0):.2f}", STYLE["td"]),
                        Paragraph(f"{m.get('length',0):.2f}", STYLE["td"]),
                        Paragraph(f"{m.get('perimeter',0):.2f}", STYLE["td"]),
                    ]
                )
            avail_w = W - 2 * inch
            cw = [
                avail_w * 0.22,
                avail_w * 0.10,
                avail_w * 0.07,
                avail_w * 0.10,
                avail_w * 0.12,
                avail_w * 0.12,
                avail_w * 0.12,
                avail_w * 0.15,
            ]
            det_t = Table(det_rows, colWidths=cw, repeatRows=1)
            det_style = [
                ("BACKGROUND", (0, 0), (-1, 0), C_DGRAY),
                ("GRID", (0, 0), (-1, -1), 0.3, C_MGRAY),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("FONTSIZE", (0, 0), (-1, -1), 6.5),
            ]
            for i in range(1, len(det_rows)):
                if i % 2 == 0:
                    det_style.append(("BACKGROUND", (0, i), (-1, i), C_STRIPE))
            det_t.setStyle(TableStyle(det_style))
            story.append(det_t)
        else:
            story.append(
                Paragraph(
                    f"Manual entry — Distress: {su.distress_type or 'N/A'} | "
                    f"Severity: {su.severity or 'N/A'}",
                    STYLE["small"],
                )
            )
        story.append(Spacer(1, 4 * mm))

    return story


# ─────────────────────────────────────────────────────────────────────────────
# Map page (static placeholder)
# ─────────────────────────────────────────────────────────────────────────────
def _map_page(section, include_options):
    if "Map Preview" not in include_options:
        return []

    story = []
    story.append(PageBreak())

    header_data = [
        [
            Paragraph(
                "SECTION LOCATION MAP",
                ParagraphStyle(
                    "hdr",
                    fontSize=9,
                    fontName="Helvetica-Bold",
                    textColor=C_WHITE,
                    alignment=TA_CENTER,
                ),
            ),
        ]
    ]
    header_t = Table(header_data, colWidths=[W - 2 * inch])
    header_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), C_HEADER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(header_t)
    story.append(Spacer(1, 4 * mm))

    coords = getattr(section, "start_coordinates", None)
    if coords and len(coords) >= 2:
        lat, lng = coords[0], coords[1]
        # OpenStreetMap static tile
        map_url = (
            f"https://staticmap.openstreetmap.de/staticmap.php"
            f"?center={lat},{lng}&zoom=16&size=600x400"
            f"&markers={lat},{lng},red-pushpin"
        )
        map_img = _download_image(map_url, W - 2 * inch, 120 * mm)
        if map_img:
            story.append(map_img)
        else:
            # Fallback: coordinate info box
            coord_box = Table(
                [
                    [
                        Paragraph(
                            f"Section: {section.name}<br/>"
                            f"Coordinates: {lat:.6f}°N, {lng:.6f}°E<br/>"
                            f"Area: {section.area:.2f} m²<br/>"
                            f"<br/>Map preview unavailable — coordinates recorded for GIS integration.",
                            STYLE["normal"],
                        )
                    ]
                ],
                colWidths=[W - 2 * inch],
            )
            coord_box.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, C_MGRAY),
                        ("BACKGROUND", (0, 0), (-1, -1), C_STRIPE),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("TOPPADDING", (0, 0), (-1, -1), 40),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 40),
                    ]
                )
            )
            story.append(coord_box)
    else:
        story.append(
            Paragraph(
                "No coordinates stored for this section. "
                "Add coordinates to the section record to enable map preview.",
                STYLE["normal"],
            )
        )

    # Section details table
    details = [
        ["Section Name", section.name],
        ["Network Area", f"{section.area:.2f} m²"],
        [
            "Coordinates",
            (
                f"{coords[0]:.6f}°N, {coords[1]:.6f}°E"
                if coords and len(coords) >= 2
                else "Not recorded"
            ),
        ],
        ["Length", getattr(section, "length", "N/A")],
        ["Width", getattr(section, "width", "N/A")],
    ]
    det_rows = [
        [Paragraph(str(k), STYLE["label"]), Paragraph(str(v), STYLE["value"])]
        for k, v in details
    ]
    detail_t = Table(det_rows, colWidths=[50 * mm, W - 2 * inch - 50 * mm])
    detail_t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, C_MGRAY),
                ("BACKGROUND", (0, 0), (0, -1), C_STRIPE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(detail_t)
    return story


# ─────────────────────────────────────────────────────────────────────────────
# Recommendations helper
# ─────────────────────────────────────────────────────────────────────────────
def _get_recommendation(pci: float):
    recs = [
        (
            85,
            100,
            "Routine Maintenance",
            "Crack sealing, minor patching. Pavement is in good condition — no structural intervention required.",
        ),
        (
            70,
            85,
            "Preventive Maintenance",
            "Surface treatment, fog seal, or thin overlay. Address early signs of deterioration before structural damage occurs.",
        ),
        (
            55,
            70,
            "Minor Rehabilitation",
            "Mill and overlay, surface recycling. Address drainage issues. Monitor for progressive structural deterioration.",
        ),
        (
            40,
            55,
            "Major Rehabilitation",
            "Structural overlay or partial reconstruction of severely distressed areas. Engineering assessment recommended.",
        ),
        (
            25,
            40,
            "Major Rehabilitation / Reconstruction",
            "Full-depth reclamation or reconstruction may be required. Seek professional structural analysis.",
        ),
        (
            10,
            25,
            "Reconstruction",
            "Complete reconstruction. Pavement has reached end of serviceable life. Immediate engineering assessment required.",
        ),
        (
            0,
            10,
            "Emergency Reconstruction",
            "Pavement has failed. Immediate action required — section may be impassable or hazardous.",
        ),
    ]
    for lo, hi, action, detail in recs:
        if lo <= pci <= hi:
            return action, detail
    return "Assessment Required", "Consult a qualified pavement engineer."


# ─────────────────────────────────────────────────────────────────────────────
# Page numbering canvas
# ─────────────────────────────────────────────────────────────────────────────
class _PageNumCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(num_pages)
            super().showPage()
        super().save()

    def _draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 6)
        self.setFillColor(C_MGRAY)
        # Determine current page number
        current_dict = dict(
            [
                (k, v)
                for k, v in self.__dict__.items()
                if k in self._saved_page_states[0]
            ]
        )
        try:
            idx = self._saved_page_states.index(current_dict)
            page_num = idx + 1
        except ValueError:
            page_num = self._pageNumber

        self.setStrokeColor(C_MGRAY)
        self.setLineWidth(0.3)
        self.line(inch, 0.55 * inch, W - inch, 0.55 * inch)
        self.drawCentredString(
            W / 2,
            0.4 * inch,
            f"PCI Management System — ASTM D6433 | Page {page_num} of {page_count}",
        )
        self.drawString(inch, 0.4 * inch, datetime.utcnow().strftime("%Y-%m-%d"))
        self.drawRightString(W - inch, 0.4 * inch, "Method: ASTM D6433-07")
        self.restoreState()


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────
def generate_pci_report(
    report_name: str,
    network_name: str,
    section,
    pci_result,
    sample_units: list,
    include_options: list,
) -> bytes:
    """
    Generate ASTM D6433-faithful PCI report.

    Args:
        report_name: Name of the report.
        network_name: Name of the network.
        section: Section object with attributes: name, area, coordinates, etc.
        pci_result: Object with attributes:
            final_pci, condition_rating, max_cdv, tdv_start, deduct_values,
            observations (list of dict with keys: distress_type, severity, count, density, deduct_value),
            all_cdvs (optional), all_tdvs (optional), computed_at (optional).
        sample_units: List of sample unit objects with images and detections.
        include_options: List of strings: "PCI Score", "Distress Summary", "Sample Unit Details",
                         "Detection Images", "Map Preview", "Recommendations".

    Returns:
        bytes: The PDF document as a bytes object.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=report_name,
        author="PCI Management System",
    )

    computed_at = getattr(pci_result, "computed_at", datetime.utcnow())
    if isinstance(computed_at, str):
        computed_at = datetime.fromisoformat(computed_at)

    story = []

    # Page 1 — Cover / Summary
    story += _cover_page(
        report_name, network_name, section, pci_result, include_options
    )

    # Page 2 — ASTM Condition Survey Data Sheet (Fig. 4)
    if "Distress Summary" in include_options:
        story += _astm_survey_sheet(section, pci_result, computed_at)

    # Page 3 — ASTM PCI Calculation Sheet (Fig. 6)
    if "Distress Summary" in include_options:
        story += _astm_calculation_sheet(pci_result)

    # Additional pages
    if "Detection Images" in include_options:
        story += _detection_images_page(sample_units, include_options)

    if "Sample Unit Details" in include_options:
        story += _sample_unit_details_page(sample_units, include_options)

    if "Map Preview" in include_options:
        story += _map_page(section, include_options)

    doc.build(story, canvasmaker=_PageNumCanvas)
    buf.seek(0)
    return buf.read()
