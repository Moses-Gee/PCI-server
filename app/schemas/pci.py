from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime


class PCIRequest(BaseModel):
    section_id: UUID


class PCIResponse(BaseModel):
    section_id: UUID
    final_pci: float
    rating: str
    deduct_values: List[float]
    cdv: float
    calculated_at: datetime


class PCIHistoryResponse(PCIResponse):
    id: UUID
    created_at: datetime
    updated_at: Optional[datetime]
