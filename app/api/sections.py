from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.models.sample_unit import SampleUnit
from app.models.section import Section
from app.models.network import Network
from app.schemas.section import (
    SectionCreate,
    SectionUpdate,
    SectionResponse,
    SectionWithSUsResponse,
)

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


@router.get("/network/{network_id}", response_model=List[SectionResponse])
async def get_sections_by_network(network_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Section)
        .where(Section.network_id == network_id)
        .order_by(Section.chainage_start)
    )
    return result.scalars().all()


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
    db_section = Section(**section.dict(), network_id=network_id, area=area)
    db.add(db_section)
    # Increment total sections on network
    network.total_sections += 1
    await db.commit()
    await db.refresh(db_section)
    return db_section


# GET, PUT, DELETE for a single section
