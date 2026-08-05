"""行情数据相关的 Pydantic 模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StockSnapshot(BaseModel):
    """单只股票的实时快照。"""

    code: str = Field(..., description="带 sh/sz/bj 前缀")
    name: str
    open: float
    prev_close: float
    price: float
    high: float
    low: float
    volume: int = Field(..., description="成交量（股）")
    amount: float = Field(..., description="成交额（元）")
    change_pct: float = Field(..., description="涨跌幅 %")
    quote_date: str | None = None
    quote_time: str | None = None
    updated_at: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MarketMeta(BaseModel):
    """fetcher 元信息。"""

    code_count: int = 0
    code_refreshed_at: str | None = None
    last_fetch_at: str | None = None
    last_fetch_count: int = 0
    history_size: int = 0
    history_with_data: int = 0

    model_config = ConfigDict(from_attributes=True)
