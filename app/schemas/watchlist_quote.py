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

    # ===== 持仓 / 交易备忘（v1.1 增量）=====
    cost_price: float | None = Field(None, description="买入成本价")
    position: int | None = Field(None, description="持仓数量（股）")
    trade_note: str | None = Field(None, description="交易逻辑备忘")

    # 派生：盈亏与收益率（缺失字段或 cache miss 时为 null）
    # floating_pnl = (price - cost_price) * position
    # return_rate = (price - cost_price) / cost_price * 100  （百分比）
    floating_pnl: float | None = Field(None, description="浮动盈亏（元）")
    return_rate: float | None = Field(None, description="收益率（%）")

    model_config = ConfigDict(from_attributes=True)
