from sqlalchemy import Column, String, Float, Integer, UUID, ForeignKey, JSON, Boolean
from sqlalchemy.orm import relationship
from .base import BaseModel
import uuid


class Section(BaseModel):
    __tablename__ = "sections"

    network_id = Column(
        UUID(as_uuid=True),
        ForeignKey("networks.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    coordinates = Column(JSON, nullable=False)  # [lat, lng]
    chainage_start = Column(Float, nullable=True)
    chainage_end = Column(Float, nullable=True)
    width = Column(Float, nullable=False)
    length = Column(Float, nullable=False)  # km
    pixel_to_mm_factor = Column(Float, nullable=False)
    area = Column(Float, nullable=False)  # m²

    sample_unit_count = Column(Integer, server_default='0', default=0)

    latest_pci = Column(Float, nullable=True)
    latest_rating = Column(String, nullable=True)
    is_calculated = Column(Boolean, default=False)

    network = relationship("Network", back_populates="sections")
    sample_units = relationship(
        "SampleUnit", back_populates="section", cascade="all, delete-orphan"
    )
    pci_history = relationship(
        "PCIHistory", back_populates="section", cascade="all, delete-orphan"
    )
