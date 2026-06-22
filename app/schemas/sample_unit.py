from pydantic import BaseModel, Field
from typing import Optional, List, Any
from uuid import UUID
from datetime import datetime
from app.schemas.detection_result import DetectionResultResponse as DetectionResultSchema  # avoid name clash


class DistressInput(BaseModel):
    distress_type: str
    severity: str  # L, M, H
    quantity: float


class SampleUnitBase(BaseModel):
    name: str
    area: Optional[float] = None
    is_random: bool = True
    distress_inputs: Optional[List[DistressInput]] = None
    gps_coords: Optional[List[float]] = None
    pixel_to_mm_factor: Optional[float] = None
    distress_type: Optional[str] = None
    severity: Optional[str] = None
    pothole_depth: Optional[float] = None
    note: Optional[str] = None


class SampleUnitCreate(SampleUnitBase):
    image_file: bytes  # we'll handle via multipart form


class SampleUnitUpdate(BaseModel):
    name: Optional[str] = None
    area: Optional[float] = None
    is_random: Optional[bool] = None
    distress_inputs: Optional[List[DistressInput]] = None
    gps_coords: Optional[List[float]] = None
    pixel_to_mm_factor: Optional[float] = None
    distress_type: Optional[str] = None
    severity: Optional[str] = None
    pothole_depth: Optional[float] = None
    note: Optional[str] = None


class SampleUnitResponse(SampleUnitBase):
    id: UUID
    section_id: UUID
    original_image: Optional[str] = None
    predicted_image: Optional[str] = None
    detections: List[DetectionResultSchema] = []
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
