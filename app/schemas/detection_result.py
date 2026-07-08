from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime


class DetectionResultBase(BaseModel):
    distress_type: str
    severity: str
    severity_label: Optional[str] = None
    quantity: float
    confidence: Optional[float] = None
    metrics: Optional[Dict[str, Any]] = None
    normalized_class: Optional[str] = None
    edited: bool


class DetectionResultCreate(DetectionResultBase):
    pass


class DetectionResultUpdate(BaseModel):
    distress_type: str
    severity: str


class DetectionResultResponse(DetectionResultBase):
    id: UUID
    sample_unit_id: UUID
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
