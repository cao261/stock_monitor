"""alert_rules 表：自选股对应的告警规则。"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.watchlist import Watchlist


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        Index("ix_alert_rules_stock_id", "stock_id"),
        Index("ix_alert_rules_signal_type", "signal_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("watchlist.id", ondelete="CASCADE"), nullable=False
    )
    # 告警信号类型，例如 volume_spike / price_breakout / ma_cross
    signal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 触发阈值（具体含义由 signal_type 决定）
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    # 比较方向：">", "<", ">=", "<=", "=="
    comparison: Mapped[str] = mapped_column(String(4), nullable=False, default=">")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    stock: Mapped["Watchlist"] = relationship(back_populates="alert_rules", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AlertRule id={self.id} stock_id={self.stock_id} "
            f"signal={self.signal_type!r} threshold={self.threshold}>"
        )
