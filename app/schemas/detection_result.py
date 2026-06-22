from pydantic import BaseModel
from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime


class DetectionResultBase(BaseModel):
    distress_type: str
    severity: str
    quantity: float
    confidence: Optional[float] = None
    metrics: Optional[Dict[str, float]] = None


class DetectionResultCreate(DetectionResultBase):
    pass


class DetectionResultResponse(DetectionResultBase):
    id: UUID
    sample_unit_id: UUID
    created_at: datetime
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
