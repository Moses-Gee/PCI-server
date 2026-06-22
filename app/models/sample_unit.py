from sqlalchemy import Column, String, Float, Integer, UUID, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from .base import BaseModel


class SampleUnit(BaseModel):
    __tablename__ = "sample_units"

    section_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String, nullable=False)
    area = Column(Float, nullable=True)
    is_random = Column(Boolean, default=True)
    distress_inputs = Column(JSON, nullable=True)  # manual override
    gps_coords = Column(JSON, nullable=True)
    pixel_to_mm_factor = Column(Float, nullable=True)  # override

    # Image paths
    original_image = Column(String, nullable=True)
    predicted_image = Column(String, nullable=True)

    # User‑provided fields
    distress_type = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    pothole_depth = Column(Float, nullable=True)
    note = Column(String, nullable=True)

    section = relationship("Section", back_populates="sample_units")
    detections = relationship(
        "DetectionResult", back_populates="sample_unit", cascade="all, delete-orphan"
    )
