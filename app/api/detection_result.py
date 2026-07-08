from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.api import updateSectionCalcStatus
from app.core.database import get_db
from app.models.detection_result import DetectionResult
from app.models.sample_unit import SampleUnit
from app.schemas.detection_result import DetectionResultResponse, DetectionResultUpdate
from app.services.pci.pci_utilities import normalizeClass

router = APIRouter(prefix="/detections", tags=["Detections"])


@router.delete("/{detection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_detection_result(
    detection_id: UUID, db: AsyncSession = Depends(get_db)
):
    detection = await db.get(DetectionResult, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")

    await db.delete(detection)
    await db.commit()
    sample = await db.get(SampleUnit, detection.sample_unit_id)
    await updateSectionCalcStatus(db=db, section_id=sample.section_id)


@router.patch("/{detection_id}", response_model=DetectionResultResponse)
async def update_detection_result(
    detection_id: UUID,
    update: DetectionResultUpdate,
    db: AsyncSession = Depends(get_db),
):
    detection = await db.get(DetectionResult, detection_id)
    if not detection:
        raise HTTPException(status_code=404, detail="Detection not found")

    update_data = {}
    if update.distress_type is not None:
        update_data["distress_type"] = update.distress_type
        normalized_class = normalizeClass(update.distress_type)
        update_data["normalized_class"] = normalized_class
    if update.severity is not None:
        update_data["severity"] = update.severity
    update_data["edited"] = True

    for key, value in update_data.items():
        setattr(detection, key, value)

    await db.commit()
    await db.refresh(detection)
    sample = await db.get(SampleUnit, detection.sample_unit_id)
    await updateSectionCalcStatus(db=db, section_id=sample.section_id)
    return detection
