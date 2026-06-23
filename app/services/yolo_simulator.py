from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.sample_unit import SampleUnit
from app.models.detection_result import DetectionResult
from uuid import UUID
import random


async def simulate_yolo_processing(sample_unit_id: UUID, db: AsyncSession):
    # Simulate AI processing by creating dummy detections
    # In real app, you'd call a Celery task that runs YOLO
    sample = await db.get(SampleUnit, sample_unit_id)
    if not sample:
        return

    # Clear existing detections
    await db.execute(
        select(DetectionResult).where(DetectionResult.sample_unit_id == sample_unit_id)
    )

    # Generate 1-3 random detections
    distress_types = [
        "Pothole",
        "Alligator Crack",
        "Longitudinal Crack",
        "Transverse Crack",
        "Rutting",
    ]
    severities = ["low", "medium", "high"]
    num = random.randint(1, 3)
    for _ in range(num):
        det = DetectionResult(
            sample_unit_id=sample_unit_id,
            distress_type=random.choice(distress_types),
            severity=random.choice(severities),
            quantity=round(random.uniform(0.1, 2.0), 2),
            confidence=round(random.uniform(0.7, 0.99), 2),
            metrics={
                "avg_width": round(random.uniform(0.01, 0.5), 3),
                "length": round(random.uniform(0.5, 3.0), 2),
                "area": round(random.uniform(0.05, 1.5), 2),
                "perimeter": round(random.uniform(1.0, 8.0), 2),
            },
        )
        db.add(det)
    await db.commit()

    # Optionally generate a predicted image (copy original or create overlay)
    # For demo, we'll just set predicted_image to same as original (or None)
    # In real app, YOLO would save an overlay image.
    sample.predicted_image = sample.original_image  # placeholder
    await db.commit()
