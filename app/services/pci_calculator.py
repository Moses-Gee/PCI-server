from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.sample_unit import SampleUnit
from app.models.detection_result import DetectionResult
from uuid import UUID


async def calculate_pci_for_section(section_id: UUID, db: AsyncSession):
    # Fetch all sample units for this section
    sample_units = await db.execute(
        select(SampleUnit).where(SampleUnit.section_id == section_id)
    )
    sample_units = sample_units.scalars().all()

    if not sample_units:
        # No data: return a default PCI (e.g., 100)
        return {
            "final_pci": 100,
            "rating": "Good",
            "deduct_values": [],
            "cdv": 0,
        }

    # Aggregate distress quantities across all sample units
    total_deductions = 0
    for su in sample_units:
        detections = await db.execute(
            select(DetectionResult).where(DetectionResult.sample_unit_id == su.id)
        )
        detections = detections.scalars().all()
        for d in detections:
            # Simple rule: each detection adds a deduction based on severity and quantity
            severity_factor = {"L": 5, "M": 15, "H": 30}[d.severity]
            total_deductions += severity_factor * d.quantity

    # Cap deductions at 100
    total_deductions = min(total_deductions, 100)
    pci = max(0, 100 - total_deductions)

    # Determine rating
    if pci >= 85:
        rating = "Good"
    elif pci >= 70:
        rating = "Satisfactory"
    elif pci >= 55:
        rating = "Poor"
    elif pci >= 40:
        rating = "Very Poor"
    else:
        rating = "Failed"

    # Build deduct values (mock)
    deduct_values = [
        round(total_deductions * 0.4, 2),
        round(total_deductions * 0.3, 2),
        round(total_deductions * 0.2, 2),
    ]
    cdv = sum(deduct_values) * 0.7

    return {
        "final_pci": round(pci, 2),
        "rating": rating,
        "deduct_values": deduct_values,
        "cdv": round(cdv, 2),
    }
