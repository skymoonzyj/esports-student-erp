"""One database-backed appearance shared by authenticated and anonymous users."""

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class GlobalAppearance(Base):
    __tablename__ = "global_appearance"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_global_appearance_singleton"),
        CheckConstraint("theme IN ('clear', 'command')", name="ck_global_appearance_theme"),
        CheckConstraint("revision >= 1", name="ck_global_appearance_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme: Mapped[str] = mapped_column(String(16), nullable=False, server_default="clear")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
