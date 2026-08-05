"""自选股 + 实时行情联调的响应模型。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WatchlistQuote(BaseModel):
    """单条自选股 + 最新行情快照合并后的数据。"""

    id: int = Field(..., description="watchlist 主键")
    ts_code: str
    name: str | None = Field(None, description="用户在 watchlist 中登记的名称")
    name_from_market: str | None = Field(None, description="市场返回的最新名称")
    industry: str | None = None
    is_active: bool = True
    in_cache: bool = Field(..., description="是否命中内存缓存（False 时下方行情字段为 null）")

    # 以下字段 in_cache=False 时全部为 null
    price: float | None = None
    open: float | None = None
    prev_close: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    amount: float | None = None
    change_pct: float | None = None
    quote_date: str | None = None
    quote_time: str | None = None
    updated_at: str | None = None

    model_config = ConfigDict(from_attributes=True)
