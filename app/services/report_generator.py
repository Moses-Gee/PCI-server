import os
import json
from uuid import UUID
from datetime import datetime


async def generate_report_task(
    section_id: UUID, report_name: str, options: dict, report_id: str
):
    # In real app, generate PDF/Excel and save to file
    # For demo, just write a JSON summary
    os.makedirs("./reports", exist_ok=True)
    report_data = {
        "report_id": report_id,
        "report_name": report_name,
        "section_id": str(section_id),
        "generated_at": datetime.utcnow().isoformat(),
        "options": options,
        "data": "Mock report data",
    }
    with open(f"./reports/{report_id}.json", "w") as f:
        json.dump(report_data, f)
