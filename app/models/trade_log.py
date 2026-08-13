"""trade_log 表：自选股的真实交割单记录（v3.0）。

设计原则：
- **不可变**：每一笔真实成交都对应一行记录，不允许修改/删除（财务数据完整性）
- **真实成交**：通过 /api/watchlist/{id}/trade 接口写入，**不**让用户直接 PATCH
- **财务基石**：realized_pnl 是已实现盈亏，浮盈浮亏不算；这个字段喂给 LLM 做复盘

字段说明：
- action: 'BUY' / 'SELL'
  - 严格 enum，本应用 SQLAlchemy Enum，但 SQLite 兼容性差 → 用 String(8) + 业务层校验
- volume: 永远存正数（绝对值），方向由 action 决定
- realized_pnl: 仅 SELL 时可能非零（本次卖出的盈亏）；BUY 固定 0
- created_at: 用户操作时间，默认 now（不允许后补，因为是真实交易时间）
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TradeLog(Base):
    __tablename__ = "trade_log"
    __table_args__ = (
        # 按 ts_code + 时间倒序查某只票的所有交割单
        Index("ix_trade_log_ts_code_created", "ts_code", "created_at"),
        # 查今日所有交割（喂给 LLM 复盘）
        Index("ix_trade_log_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 冗余存 ts_code（不存 FK，因为 watchlist 可能被删，但交割单要永留）
    ts_code: Mapped[str] = mapped_column(String(16), nullable=False)
    # 'BUY' / 'SELL'
    action: Mapped[str] = mapped_column(String(8), nullable=False)
    # 成交价
    price: Mapped[float] = mapped_column(Float, nullable=False)
    # 数量（永远正数）
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    # 已实现盈亏：BUY 固定 0；SELL = (price - cost_price) * volume
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # 交易时间（用户操作瞬间，由 server_default 取数据库时间）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TradeLog id={self.id} {self.action} {self.ts_code} "
            f"{self.volume}股@{self.price} pnl={self.realized_pnl}>"
        )
