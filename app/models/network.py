from sqlalchemy import Column, String, Float, JSON, Integer, UUID, ForeignKey
from sqlalchemy.orm import relationship
from .base import BaseModel


class Network(BaseModel):
    __tablename__ = "networks"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    start_coordinates = Column(JSON, nullable=False)  # [lat, lng]
    end_coordinates = Column(JSON, nullable=False)  # [lat, lng]
    total_sections = Column(Integer, default=0)

    user = relationship("User", back_populates="networks")

    sections = relationship(
        "Section", back_populates="network", cascade="all, delete-orphan"
    )

