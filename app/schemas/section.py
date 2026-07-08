from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from app.schemas.sample_unit import SampleUnitResponse


class SectionBase(BaseModel):
    name: str
    description: Optional[str] = None
    coordinates: List[float] = Field(..., min_items=2, max_items=2)
    chainage_start: float
    chainage_end: float
    width: float
    length: float
    pixel_to_mm_factor: float


class SectionCreate(SectionBase):
    pass


class SectionUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    coordinates: Optional[List[float]] = None
    chainage_start: Optional[float] = None
    chainage_end: Optional[float] = None
    width: Optional[float] = None
    length: Optional[float] = None
    pixel_to_mm_factor: Optional[float] = None


class SectionResponse(SectionBase):
    id: UUID
    network_id: UUID
    area: float
    sample_unit_count: int
    latest_pci: Optional[float] = None
    latest_rating: Optional[str] = None
    is_calculated: bool
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class SectionWithSUsResponse(SectionResponse):
    sample_units: List[SampleUnitResponse] = []
