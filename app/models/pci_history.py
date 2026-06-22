from sqlalchemy import Column, Float, Integer, UUID, ForeignKey, JSON, String
from sqlalchemy.orm import relationship
from .base import BaseModel


class PCIHistory(BaseModel):
    __tablename__ = "pci_history"

    section_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sections.id", ondelete="CASCADE"),
        nullable=False,
    )
    final_pci = Column(Float, nullable=False)
    rating = Column(String, nullable=False)
    deduct_values = Column(JSON, nullable=True)
    cdv = Column(Float, nullable=True)

    section = relationship("Section", back_populates="pci_history")
