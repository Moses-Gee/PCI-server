import uuid
from datetime import datetime
from .base import BaseModel
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.orm import relationship


class User(BaseModel):
    __tablename__ = "users"

    email = Column(String, unique=True, nullable=False, index=True)
    password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)

    networks = relationship(
        "Network",
        back_populates="user",
        cascade="all, delete-orphan",
    )
