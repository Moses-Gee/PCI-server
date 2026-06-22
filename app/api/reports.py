from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.core.database import get_db
from app.schemas.report import ReportRequest, ReportResponse
from app.services.report_generator import generate_report_task

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("/generate", response_model=ReportResponse)
async def generate_report(
    request: ReportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Start background report generation
    report_id = f"rpt_{UUID.uuid4().hex[:8]}"
    background_tasks.add_task(
        generate_report_task,
        request.section_id,
        request.report_name,
        request.options,
        report_id,
    )
    return {"message": "Report generation started", "report_id": report_id}
