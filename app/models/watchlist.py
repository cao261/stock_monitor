"""watchlist 表：自选股清单。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        Index("ix_watchlist_ts_code", "ts_code", unique=True),
        Index("ix_watchlist_exchange", "exchange"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Tushare 风格的代码，例如 sh600000 / sz000001 / bj920000
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    # 股票简称（可后续从行情接口同步，允许为空以便先录入代码）
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 交易所代码：SH / SZ / BJ
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 市场板块：主板 / 创业板 / 科创板 / 北交所
    market: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 所属行业
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 是否仍在监控
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 反向引用：一支自选股可以挂多条告警规则
    alert_rules: Mapped[list["AlertRule"]] = relationship(  # noqa: F821
        back_populates="stock",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Watchlist id={self.id} ts_code={self.ts_code!r}>"
