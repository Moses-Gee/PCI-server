from sqlalchemy import Column, String, Float, JSON, Integer
from sqlalchemy.orm import relationship
from .base import BaseModel


class Network(BaseModel):
    __tablename__ = "networks"

    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    coordinates = Column(JSON, nullable=False)  # [lat, lng]
    total_sections = Column(Integer, default=0)

    sections = relationship(
        "Section", back_populates="network", cascade="all, delete-orphan"
    )
