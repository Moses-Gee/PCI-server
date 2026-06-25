from sqlalchemy import Column, String, Float, UUID, ForeignKey, JSON
from sqlalchemy.orm import relationship
from .base import BaseModel


class DetectionResult(BaseModel):
    __tablename__ = "detection_results"

    sample_unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sample_units.id", ondelete="CASCADE"),
        nullable=False,
    )
    distress_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)  # L, M, H
    quantity = Column(Float, nullable=False)  # count or area
    confidence = Column(Float, nullable=True)
    metrics = Column(JSON, nullable=True)  # {avg_width, length, area, perimeter}
    normalized_class = Column(String, nullable=False)

    sample_unit = relationship("SampleUnit", back_populates="detections")
