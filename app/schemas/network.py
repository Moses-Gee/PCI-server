from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

from app.schemas.section import SectionResponse


class NetworkBase(BaseModel):
    name: str
    description: Optional[str] = None
    start_coordinates: List[float] = Field(..., min_items=2, max_items=2)
    end_coordinates: List[float] = Field(..., min_items=2, max_items=2)


class NetworkCreate(NetworkBase):
    pass


class NetworkUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    start_coordinates: Optional[List[float]] = None
    end_coordinates: Optional[List[float]] = None


class NetworkResponse(NetworkBase):
    id: UUID
    total_sections: int
    created_at: datetime
    updated_at: Optional[datetime]
    sections: List[SectionResponse]

    class Config:
        from_attributes = True
class NetworkWithSectionsResponse(NetworkResponse):
    sections: List[SectionResponse] = []