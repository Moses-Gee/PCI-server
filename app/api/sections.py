from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Sequence, select
from typing import List
from uuid import UUID
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.sample_unit import SampleUnit
from app.models.section import Section
from app.models.network import Network
from app.schemas.pci import PCIResponse
from app.schemas.section import (
    SectionCreate,
    SectionUpdate,
    SectionResponse,
    SectionWithSUsResponse,
)
from app.services.pci_utilities import groupAndCalcDensity

router = APIRouter(prefix="/sections", tags=["Sections"])


@router.get("/", response_model=List[SectionResponse])
async def get_all_sections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Section).order_by(Section.created_at.desc()))
    return result.scalars().all()


@router.get("/{section_id}", response_model=SectionWithSUsResponse)
async def get_section(section_id: UUID, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Section)
        .where(Section.id == section_id)
        .options(selectinload(Section.sample_units).selectinload(SampleUnit.detections))
    )
    result = await db.execute(stmt)
    section = result.scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    return section


# @router.get("/network/{network_id}", response_model=List[SectionResponse])
# async def get_sections_by_network(network_id: UUID, db: AsyncSession = Depends(get_db)):
#     result = await db.execute(
#         select(Section)
#         .where(Section.network_id == network_id)
#         .order_by(Section.chainage_start)
#     )
#     return result.scalars().all()


@router.post("/", response_model=SectionResponse, status_code=status.HTTP_201_CREATED)
async def create_section(
    section: SectionCreate, network_id: UUID, db: AsyncSession = Depends(get_db)
):
    print(section)
    # Verify network exists
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    # Calculate area (m²) = length (km) * width (m) * 1000
    area = section.length * section.width
    db_section = Section(**section.model_dump(), network_id=network_id, area=area)
    db.add(db_section)
    # Increment total sections on network
    network.total_sections += 1
    await db.commit()
    await db.refresh(db_section)
    return db_section


@router.patch("/{section_id}", response_model=SectionResponse)
async def update_section(
    section_id: UUID, update: SectionUpdate, db: AsyncSession = Depends(get_db)
):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(section, key, value)
    await db.commit()
    await db.refresh(section)
    return section


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(section_id: UUID, db: AsyncSession = Depends(get_db)):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    # Optionally delete image files
    await db.delete(section)
    await db.commit()


# GET, PUT, DELETE for a single section


@router.get("/{section_id}/calc_pci", response_model=List[PCIResponse])
async def calc_section_pci(section_id: UUID, db: AsyncSession = Depends(get_db)):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    stmt = (
        select(SampleUnit)
        .where(SampleUnit.section_id == section.id)
        .options(selectinload(SampleUnit.detections))  # Eager load detections
        .order_by(SampleUnit.created_at.desc())
    )
    result = await db.execute(stmt)
    sample_units = result.scalars().all()
    predictions = []
    for sample_unit in sample_units:
        detections = sample_unit.detections
        if len(detections) > 0:
            for detection in detections:
                predictions.append(
                    {
                        "distress_type": detection.normalized_class,
                        "severity": detection.severity,
                    }
                )
        else:
            if (
                sample_unit.distress_type is None
                or sample_unit.normalized_class is None
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Found No Distress type for {sample_unit.name} Sample Unit. This could be as a result of no prediction from the seg and box models. Please manually select a distress type by updating this sample to continue",
                )
            if sample_unit.severity is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Found No Severity level for {sample_unit.name} Sample Unit. This could be as a result of no prediction from the seg and box models. Please manually select a distress type by updating this sample to continue",
                )
            predictions.append(
                {
                    "distress_type": sample_unit.normalized_class,
                    "severity": sample_unit.severity,
                }
            )

    groupWithDensity = groupAndCalcDensity(predictions, section.area)
    return groupWithDensity
