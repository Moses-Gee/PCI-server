import uuid

from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
import os
import shutil
from datetime import datetime

from app.core.database import get_db
from app.core.config import settings
from app.models.sample_unit import SampleUnit
from app.models.section import Section
from app.schemas.sample_unit import (
    SampleUnitCreate,
    SampleUnitUpdate,
    SampleUnitResponse,
)
from app.services.yolo_simulator import simulate_yolo_processing

router = APIRouter(prefix="/sample-units", tags=["Sample Units"])


@router.get("/section/{section_id}", response_model=List[SampleUnitResponse])
async def get_sample_units_by_section(
    section_id: UUID, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(SampleUnit).where(SampleUnit.section_id == section_id)
    )
    return result.scalars().all()


@router.post(
    "/", response_model=SampleUnitResponse, status_code=status.HTTP_201_CREATED
)
async def create_sample_unit(
    section_id: UUID = Form(...),
    name: str = Form(...),
    area: float = Form(None),
    is_random: bool = Form(True),
    distress_type: str = Form(None),
    severity: str = Form(None),
    pothole_depth: float = Form(None),
    note: str = Form(None),
    pixel_to_mm_factor: float = Form(None),
    image_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Verify section exists
    section = await db.get(Section, section_id)
    if not section:
        raise HTTPException(status_code=404, detail="Section not found")

    # Save image
    upload_dir = settings.UPLOAD_DIR
    os.makedirs(upload_dir, exist_ok=True)
    file_ext = os.path.splitext(image_file.filename)[1]
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex}{file_ext}"
    filepath = os.path.join(upload_dir, filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(image_file.file, buffer)

    # Create sample unit
    db_sample = SampleUnit(
        section_id=section_id,
        name=name,
        area=area,
        is_random=is_random,
        distress_type=distress_type,
        severity=severity,
        pothole_depth=pothole_depth,
        note=note,
        pixel_to_mm_factor=pixel_to_mm_factor or section.pixel_to_mm_factor,
        original_image=filepath,
    )
    db.add(db_sample)
    await db.commit()
    await db.refresh(db_sample)

    # Simulate YOLO processing (background task)
    # We'll call a service that updates detections asynchronously
    # For simplicity we'll do it in the same request, but for production use Celery
    # We'll just simulate with a short delay.
    await simulate_yolo_processing(db_sample.id, db)

    # Reload to include detections
    await db.refresh(db_sample)
    return db_sample


@router.patch("/{sample_unit_id}", response_model=SampleUnitResponse)
async def update_sample_unit(
    sample_unit_id: UUID, update: SampleUnitUpdate, db: AsyncSession = Depends(get_db)
):
    sample = await db.get(SampleUnit, sample_unit_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Sample unit not found")
    for key, value in update.dict(exclude_unset=True).items():
        setattr(sample, key, value)
    await db.commit()
    await db.refresh(sample)
    return sample


@router.delete("/{sample_unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sample_unit(sample_unit_id: UUID, db: AsyncSession = Depends(get_db)):
    sample = await db.get(SampleUnit, sample_unit_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Sample unit not found")
    # Optionally delete image files
    await db.delete(sample)
    await db.commit()
