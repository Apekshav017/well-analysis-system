"""
db/models/app_setting.py

System-wide settings the administrator controls (one row per key).
"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, func

from app.db.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AppSetting(key={self.key})>"
