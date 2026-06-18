"""Base model with common fields."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class BaseModel(Base, TimestampMixin):
    __abstract__ = True

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
