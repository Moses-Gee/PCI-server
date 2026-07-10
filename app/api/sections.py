from fastapi import APIRouter, Body, Depends, HTTPException, status
import io
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Sequence, select
from typing import List
from uuid import UUID
from sqlalchemy.orm import selectinload
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.pci_history import PCIHistory
from app.models.sample_unit import SampleUnit
from app.models.section import Section
from app.models.network import Network
from app.models.user import User
from app.schemas.pci import PCIHistoryResponse, PCIResponse
from app.schemas.sample_unit import SampleUnitResponse
from app.schemas.section import (
    SectionCreate,
    SectionUpdate,
    SectionResponse,
    SectionWithSUsResponse,
)
from app.core.pci import get_pci_calculator
from app.services.image_service import delete_images_for_ids
from app.services.pci.pci_utilities import groupAndCalcDensity
from app.services.reports.pdf_generator import generate_pci_report

# from app.services.pci.pci_utilities import groupAndCalcDensity

router = APIRouter(prefix="/sections", tags=["Sections"])


@router.get("/", response_model=List[SectionResponse])
async def get_all_sections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Section).order_by(Section.created_at.desc()))
    return result.scalars().all()


@router.get("/{section_id}", response_model=SectionResponse)
async def get_section(
    section_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # stmt = (
    #     select(Section)
    #     .where(Section.id == section_id)
    #     .options(
    #         selectinload(Section.sample_units).selectinload(SampleUnit.detections),
    #         selectinload(Section.sample_units).selectinload(SampleUnit.images),
    #     )
    # )
    # result = await db.execute(stmt)
    # section = result.scalar_one_or_none()
    # if not section:
    #     raise HTTPException(status_code=404, detail="Section not found")
    # Verify section exists
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    return section


@router.post("/", response_model=SectionResponse, status_code=status.HTTP_201_CREATED)
async def create_section(
    section: SectionCreate, network_id: UUID, db: AsyncSession = Depends(get_db)
):
    print(section)
    # Verify network exists
    network = await db.get(Network, network_id)
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    # Calculate area (m²)
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
    area = update.length * update.width
    setattr(section, "area", area)
    setattr(section, "is_calculated", False)
    await db.commit()
    await db.refresh(section)
    return section


@router.delete("/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(section_id: UUID, db: AsyncSession = Depends(get_db)):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Collect sample unit IDs before cascade fires
    stmt = select(SampleUnit.id).where(SampleUnit.section_id == section_id)
    result = await db.execute(stmt)
    sample_unit_ids = result.scalars().all()

    # Clean Cloudinary + image DB rows
    await delete_images_for_ids(db, sample_unit_ids)
    await db.delete(section)
    network = await db.get(Network, section.network_id)
    if network.total_sections > 0:
        network.total_sections -= 1
    await db.commit()


@router.get("/{section_id}/sample-units", response_model=List[SampleUnitResponse])
async def get_section_sample_units(
    section_id: UUID,
    db: AsyncSession = Depends(get_db),
    # skip: int = 0,
    # limit: int = 20,
):
    # Verify section exists
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    stmt = (
        select(SampleUnit)
        .where(SampleUnit.section_id == section_id)
        .options(
            selectinload(SampleUnit.detections),
            selectinload(SampleUnit.images),
        )
        .order_by(SampleUnit.created_at.desc())
        # .offset(skip)
        # .limit(limit)
    )
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{section_id}/calc_pci", response_model=PCIHistoryResponse)
async def calc_section_pci(section_id: UUID, db: AsyncSession = Depends(get_db)):
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")
    if section.sample_unit_count < 5:
        raise HTTPException(status_code=400, detail="Not enough Sample Units")
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

    group_with_density = groupAndCalcDensity(predictions, section.area)
    pci_result = get_pci_calculator().compute_pci(group_with_density)

    section.latest_pci = pci_result["final_pci"]
    section.latest_rating = pci_result["condition_rating"]
    section.is_calculated = True
    await db.commit()

    pci_history = PCIHistory(
        section_id=section_id,
        final_pci=pci_result["final_pci"],
        condition_rating=pci_result["condition_rating"],
        max_cdv=pci_result["max_cdv"],
        tdv_start=pci_result["tdv_start"],
        deduct_values=pci_result["deduct_values"],
        observations=pci_result["observations"],
        all_cdvs=pci_result["all_cdvs"],
        all_tdvs=pci_result["all_tdvs"],
    )

    db.add(pci_history)
    await db.commit()
    await db.refresh(pci_history)
    return pci_history


@router.post("/{section_id}/report")
async def generate_report(
    section_id: UUID,
    report_name: str = Body(...),
    include_options: list[str] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Load section
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Load network name
    network = await db.get(Network, section.network_id)
    # Load latest PCI history
    pci_stmt = (
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.desc())
        .limit(1)
    )
    pci_result = await db.execute(pci_stmt)
    pci_history = pci_result.scalar_one_or_none()

    if not pci_history:
        raise HTTPException(
            status_code=400,
            detail="No PCI calculation found. Please calculate PCI first.",
        )
    # Load sample units with detections
    su_stmt = (
        select(SampleUnit)
        .where(SampleUnit.section_id == section_id)
        .options(selectinload(SampleUnit.detections))
        .order_by(SampleUnit.created_at.desc())
    )
    su_result = await db.execute(su_stmt)
    sample_units = su_result.scalars().all()

    # Generate PDF
    pdf_bytes = generate_pci_report(
        report_name=report_name,
        network_name=network.name if network else "Unknown",
        section=section,
        pci_result=pci_history,
        sample_units=sample_units,
        include_options=include_options,
    )

    filename = f"{report_name.replace(' ', '_')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{section_id}/pci_history", response_model=List[PCIHistoryResponse])
async def get_pci_history(section_id: UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PCIHistory)
        .where(PCIHistory.section_id == section_id)
        .order_by(PCIHistory.created_at.desc())
    )
    return result.scalars().all()
