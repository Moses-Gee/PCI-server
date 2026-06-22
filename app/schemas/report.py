from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID


class ReportOptions(BaseModel):
    include_pci: bool = True
    include_distress_summary: bool = True
    include_sample_unit_details: bool = True
    include_map: bool = True
    include_recommendations: bool = True


class ReportRequest(BaseModel):
    section_id: UUID
    report_name: str
    options: ReportOptions


class ReportResponse(BaseModel):
    message: str
    report_id: str
