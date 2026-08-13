"""trade_log 表的 Pydantic 模型（v3.0）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# action 枚举（业务层校验，避免 Pydantic/SQLite enum 兼容问题）
TradeAction = Literal["BUY", "SELL"]


class TradeLogRead(BaseModel):
    """单条交割单（响应）。"""

    id: int
    ts_code: str
    action: TradeAction
    price: float
    volume: int = Field(..., description="绝对值数量")
    realized_pnl: float = Field(0.0, description="本次卖出的已实现盈亏（买入为 0）")
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TradeRequest(BaseModel):
    """POST /api/watchlist/{id}/trade 的请求体。"""

    price: float = Field(..., gt=0, description="成交价（> 0）")
    # 带符号：正=买入，负=卖出，0=无效
    volume: int = Field(..., description="带符号数量：正数买入，负数卖出")

    @field_validator("volume")
    @classmethod
    def _check_volume_not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("volume 不能为 0（正数买入，负数卖出）")
        return v


class TradeResponse(BaseModel):
    """POST /api/watchlist/{id}/trade 的响应：操作结果 + 新仓位状态。"""

    trade_id: int = Field(..., description="新写入 trade_log 的 id")
    ts_code: str
    action: TradeAction
    trade_price: float
    trade_volume: int = Field(..., description="绝对值数量")
    realized_pnl: float = Field(..., description="本次操作的已实现盈亏（买入=0）")

    # 成交后该票的持仓状态
    new_position: int = Field(..., description="成交后剩余持仓")
    new_cost_price: float = Field(..., description="成交后新成本价")
    new_last_grid_price: float = Field(..., description="成交后新网格基准价（=本次成交价）")

    model_config = ConfigDict(from_attributes=True)
