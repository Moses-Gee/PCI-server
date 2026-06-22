from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from uuid import UUID
from datetime import datetime
from typing import List

from app.core.database import get_db
from app.models.section import Section
from app.models.sample_unit import SampleUnit
from app.models.detection_result import DetectionResult
from app.models.pci_history import PCIHistory
from app.schemas.pci import PCIResponse, PCIHistoryResponse
from app.services.pci_calculator import calculate_pci_for_section

router = APIRouter(prefix="/pci", tags=["PCI"])


@router.get("/section/{section_id}", response_model=PCIResponse)
async def get_latest_pci(section_id: UUID, db: AsyncSession = Depends(get_db)):
    # Check if we have a cached PCI in history
    result = await db.execute(
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.desc())
        .limit(1)
    )
    history = result.scalar_one_or_none()
    if history:
        return PCIResponse(
            section_id=history.section_id,
            final_pci=history.final_pci,
            rating=history.rating,
            deduct_values=history.deduct_values,
            cdv=history.cdv,
            calculated_at=history.created_at,
        )

    # Otherwise compute fresh (and store)
    pci_result = await calculate_pci_for_section(section_id, db)

    # Save to history
    pci_history = PCIHistory(
        section_id=section_id,
        final_pci=pci_result["final_pci"],
        rating=pci_result["rating"],
        deduct_values=pci_result["deduct_values"],
        cdv=pci_result["cdv"],
    )
    db.add(pci_history)
    await db.commit()

    return PCIResponse(
        section_id=section_id,
        final_pci=pci_result["final_pci"],
        rating=pci_result["rating"],
        deduct_values=pci_result["deduct_values"],
        cdv=pci_result["cdv"],
        calculated_at=datetime.utcnow(),
    )


@router.get("/section/{section_id}/history", response_model=List[PCIHistoryResponse])
async def get_pci_history(section_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.desc())
    )
    return result.scalars().all()
