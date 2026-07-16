from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, List

class DashboardStats(BaseModel):
    total_networks: int
    total_sections: int
    total_sample_units: int
    avg_pci: float
    critical_sections: int
    analyzed_sections: int
    latest_section_id: Optional[UUID]

class PCIDistributionItem(BaseModel):
    rating: str
    count: int

class DistressDistributionItem(BaseModel):
    type: str
    count: int

class RecentSampleUnit(BaseModel):
    id: UUID
    name: str
    section_name: str
    section_area: float
    date: datetime
    status: str  # Processed, Pending, Processing

class GeoJSONFeature(BaseModel):
    type: str = "Feature"
    geometry: dict
    properties: dict

class GeoJSONResponse(BaseModel):
    type: str = "FeatureCollection"
    features: List[GeoJSONFeature]