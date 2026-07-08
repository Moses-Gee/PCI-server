from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from uuid import UUID
from collections import defaultdict
from typing import List
from datetime import datetime

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.models.network import Network
from app.models.section import Section
from app.models.sample_unit import SampleUnit
from app.models.detection_result import DetectionResult
from app.models.pci_history import PCIHistory
from app.schemas.dashboard import (
    DashboardStats,
    PCIDistributionItem,
    DistressDistributionItem,
    RecentSampleUnit,
    GeoJSONResponse,
    GeoJSONFeature,
)

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _pci_color(pci: float) -> str:
    if pci >= 85:
        return "#22c55e"
    if pci >= 70:
        return "#3b82f6"
    if pci >= 55:
        return "#f59e0b"
    if pci >= 40:
        return "#f97316"
    return "#ef4444"


async def _get_user_network_ids(db: AsyncSession, user_id) -> list:
    result = await db.execute(select(Network.id).where(Network.user_id == user_id))
    return result.scalars().all()


async def _get_user_section_ids(db: AsyncSession, network_ids: list) -> list:
    if not network_ids:
        return []
    result = await db.execute(
        select(Section.id).where(Section.network_id.in_(network_ids))
    )
    return result.scalars().all()


async def _latest_pci_for_section(db: AsyncSession, section_id) -> PCIHistory | None:
    result = await db.execute(
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    total_networks = len(network_ids)
    total_sections = len(section_ids)

    total_sus = 0
    if section_ids:
        total_sus = (
            await db.execute(
                select(func.count(SampleUnit.id)).where(
                    SampleUnit.section_id.in_(section_ids)
                )
            )
        ).scalar_one() or 0

    # Latest PCI per section
    pci_values = []
    rating_map: dict[str, str] = {}  # section_id → condition_rating
    for sid in section_ids:
        h = await _latest_pci_for_section(db, sid)
        if h:
            pci_values.append(h.final_pci)
            rating_map[str(sid)] = h.condition_rating

    avg_pci = round(sum(pci_values) / len(pci_values), 2) if pci_values else 0.0

    critical_sections = sum(1 for p in pci_values if p < 55)
    analyzed_sections = sum(1 for p in pci_values if p > 0)

    # analyzed_sections = 0
    # if section_ids:
    #     analyzed_sections = (
    #         await db.execute(
    #             select(func.count(SampleUnit.section_id.distinct())).where(
    #                 SampleUnit.section_id.in_(section_ids)
    #             )
    #         )
    #     ).scalar_one() or 0

    return DashboardStats(
        total_networks=total_networks,
        total_sections=total_sections,
        total_sample_units=total_sus,
        avg_pci=avg_pci,
        critical_sections=critical_sections,
        analyzed_sections=analyzed_sections,
    )


@router.get("/pci-distribution", response_model=List[PCIDistributionItem])
async def get_pci_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    if not section_ids:
        return []

    rating_counts: dict[str, int] = defaultdict(int)
    for sid in section_ids:
        h = await _latest_pci_for_section(db, sid)
        if h:
            rating_counts[h.condition_rating] += 1

    order = ["Good", "Satisfactory", "Fair", "Poor", "Very Poor", "Serious", "Failed"]
    return [
        PCIDistributionItem(rating=r, count=rating_counts.get(r, 0))
        for r in order
        if rating_counts.get(r, 0) > 0
    ]


@router.get("/distress-distribution", response_model=List[DistressDistributionItem])
async def get_global_distress_distribution(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Global distress distribution across all user sections."""
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    if not section_ids:
        return []

    stmt = (
        select(DetectionResult.normalized_class, func.count().label("count"))
        .join(SampleUnit, DetectionResult.sample_unit_id == SampleUnit.id)
        .where(SampleUnit.section_id.in_(section_ids))
        .where(DetectionResult.normalized_class.isnot(None))
        .group_by(DetectionResult.normalized_class)
        .order_by(desc("count"))
    )
    result = await db.execute(stmt)
    return [DistressDistributionItem(type=row[0], count=row[1]) for row in result.all()]


@router.get("/recent-sample-units", response_model=List[RecentSampleUnit])
async def get_recent_sample_units(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    if not section_ids:
        return []

    stmt = (
        select(SampleUnit, Section.name.label("section_name"))
        .join(Section, Section.id == SampleUnit.section_id)
        .where(SampleUnit.section_id.in_(section_ids))
        .order_by(desc(SampleUnit.created_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    recent = []
    for sample, section_name in rows:
        det_count = (
            await db.execute(
                select(func.count(DetectionResult.id)).where(
                    DetectionResult.sample_unit_id == sample.id
                )
            )
        ).scalar_one() or 0

        if det_count > 0:
            status = "Processed"
        elif sample.inference_status == "processing":
            status = "Processing"
        else:
            status = "Pending"

        recent.append(
            RecentSampleUnit(
                id=sample.id,
                name=sample.name,
                section=section_name,
                date=sample.created_at,
                status=status,
            )
        )
    return recent


@router.get("/geojson", response_model=GeoJSONResponse)
async def get_geojson(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    if not section_ids:
        return GeoJSONResponse(features=[])

    stmt = select(Section).where(Section.id.in_(section_ids))
    sections = (await db.execute(stmt)).scalars().all()

    features = []
    for sec in sections:
        if not sec.coordinates or len(sec.coordinates) < 2:
            continue

        h = await _latest_pci_for_section(db, sec.id)
        pci = h.final_pci if h else None
        rating = h.condition_rating if h else "Not Assessed"

        lat, lng = sec.coordinates[0], sec.coordinates[1]
        features.append(
            GeoJSONFeature(
                geometry={"type": "Point", "coordinates": [lng, lat]},
                properties={
                    "id": str(sec.id),
                    "name": sec.name,
                    "pci": pci,
                    "rating": rating,
                    "color": _pci_color(pci) if pci is not None else "#9ca3af",
                },
            )
        )

    return GeoJSONResponse(features=features)


# ── Section-specific analytics ────────────────────────────────────────────────


@router.get("/pci-trend/{section_id}")
async def get_pci_trend(
    section_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify section belongs to user
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    network = await db.get(Network, section.network_id)
    if not network or network.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    stmt = (
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.asc())
    )
    history = (await db.execute(stmt)).scalars().all()

    return [
        {
            "date": h.created_at.isoformat(),
            "pci": h.final_pci,
            "condition_rating": h.condition_rating,
            "max_cdv": h.max_cdv,
        }
        for h in history
    ]


@router.get("/distress-distribution/{section_id}")
async def get_section_distress_distribution(
    section_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify ownership
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    network = await db.get(Network, section.network_id)
    if not network or network.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    stmt = (
        select(DetectionResult)
        .join(SampleUnit, DetectionResult.sample_unit_id == SampleUnit.id)
        .where(SampleUnit.section_id == section_id)
    )
    detections = (await db.execute(stmt)).scalars().all()

    type_counts: dict[str, int] = defaultdict(int)
    severity_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"low": 0, "medium": 0, "high": 0}
    )

    for d in detections:
        key = d.normalized_class or "Unknown"
        # key = d.distress_type or d.normalized_class or "Unknown"
        type_counts[key] += 1
        sev = (d.severity or "low").lower()
        if sev in ("low", "medium", "high"):
            severity_counts[key][sev] += 1

    # Include manual sample unit entries
    manual_sus = (
        (
            await db.execute(
                select(SampleUnit).where(
                    SampleUnit.section_id == section_id,
                    SampleUnit.normalized_class.isnot(None),
                )
            )
        )
        .scalars()
        .all()
    )

    for su in manual_sus:
        has_det = (
            await db.execute(
                select(DetectionResult.id)
                .where(DetectionResult.sample_unit_id == su.id)
                .limit(1)
            )
        ).scalar_one_or_none()

        if not has_det:
            key = su.distress_type or "Unknown"
            type_counts[key] += 1
            sev = (su.severity or "low").lower()
            if sev in ("low", "medium", "high"):
                severity_counts[key][sev] += 1

    return {
        "type_distribution": [
            {"distress_type": k, "count": v}
            for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
        ],
        "severity_distribution": [
            {
                "distress_type": k,
                "low": v["low"],
                "medium": v["medium"],
                "high": v["high"],
            }
            for k, v in severity_counts.items()
        ],
    }


@router.get("/network-summary")
async def get_network_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    network_ids = await _get_user_network_ids(db, current_user.id)
    section_ids = await _get_user_section_ids(db, network_ids)

    total_sus = 0
    if section_ids:
        total_sus = (
            await db.execute(
                select(func.count(SampleUnit.id)).where(
                    SampleUnit.section_id.in_(section_ids)
                )
            )
        ).scalar_one() or 0

    pci_values = []
    for sid in section_ids:
        h = await _latest_pci_for_section(db, sid)
        if h:
            pci_values.append(h.final_pci)

    avg_pci = round(sum(pci_values) / len(pci_values), 1) if pci_values else None

    return {
        "total_networks": len(network_ids),
        "total_sections": len(section_ids),
        "total_sample_units": total_sus,
        "average_pci": avg_pci,
        "sections_assessed": len(pci_values),
    }
