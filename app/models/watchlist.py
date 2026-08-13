"""watchlist 表：自选股清单（含持仓 / 交易备忘）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, func
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

    # ===== 持仓 / 交易备忘（v1.1 增量）=====
    # 买入成本价（元/股）。null = 只观察、未持仓
    cost_price: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # 持仓数量（股）。null = 只观察、未持仓
    position: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # 交易逻辑备忘（买入理由、止损位、目标位等）
    trade_note: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)

    # ===== 止盈止损（v1.2 增量）=====
    # 止盈目标价（元/股）：现价 >= target_win 触发 is_take_profit 信号
    target_win: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    # 止损/防守价（元/股）：现价 <= target_loss 触发 is_stop_loss 信号
    target_loss: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

    # ===== v2.7 网格动态追踪（增量）=====
    # 上次网格加/减仓的基准价（元/股）。
    # 算法：grid_distance = (现价 - last_grid_price) / last_grid_price * 100
    #       如果 grid_distance <= -grid_step_pct → 触发 is_grid_buy（加仓机会）
    #       如果 grid_distance >=  grid_step_pct → 触发 is_grid_sell（减仓机会）
    # 空值时 fallback 使用 cost_price（首次建仓还没建网格交易也能算）
    last_grid_price: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)

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
