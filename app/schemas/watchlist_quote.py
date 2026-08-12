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

    # ===== 止盈止损（v1.2 增量）=====
    target_win: float | None = Field(None, description="止盈目标价")
    target_loss: float | None = Field(None, description="止损/防守价")

    # 派生：盈亏与收益率（缺失字段或 cache miss 时为 null）
    # floating_pnl = (price - cost_price) * position
    # return_rate = (price - cost_price) / cost_price * 100  （百分比）
    floating_pnl: float | None = Field(None, description="浮动盈亏（元）")
    return_rate: float | None = Field(None, description="收益率（%）")

    # ===== v2.6: trade_note 智能解析结果 =====
    # 用户没设 target_* 但 trade_note 写了"跌破 1620"这种带数字的策略时，
    # 后端自动从 note 里挖出价位作为兜底。signals 扫描会拿 eff_target_* 当价格线。
    note_extracted_target_win: float | None = Field(
        None, description="从 trade_note 文本里自动识别的止盈价（用户未设时兜底用）"
    )
    note_extracted_target_loss: float | None = Field(
        None, description="从 trade_note 文本里自动识别的止损价（用户未设时兜底用）"
    )
    eff_target_win: float | None = Field(
        None, description="实际生效的止盈价（用户值优先，note 提取值兜底）"
    )
    eff_target_loss: float | None = Field(
        None, description="实际生效的止损价（用户值优先，note 提取值兜底）"
    )
    note_has_rule: bool = Field(False, description="trade_note 里是否有任何纪律（数字或语义）")
    note_semantic_rules: list[str] = Field(
        default_factory=list,
        description="trade_note 里命中的纯语义规则关键词（'网格策略'/'次日不连板' 等）",
    )
    # ===== v2.6.2: 自动判字段（让前端不用手算）=====
    # note_target_broken: 当前价 <= eff_target_loss（trade_note 里的止损位已被破）
    # note_target_reached: 当前价 >= eff_target_win（trade_note 里的止盈位已到）
    # 这俩跟 is_stop_loss / is_take_profit 不同 —— 这俩只看 eff_target_* 跟当前价的关系
    # 不依赖 is_take_profit 信号是否触发。给用户 / LLM 一个"硬性命中"的判断。
    note_target_broken: bool = Field(
        False, description="当前价 ≤ eff_target_loss：trade_note 里的止损位已破"
    )
    note_target_reached: bool = Field(
        False, description="当前价 ≥ eff_target_win：trade_note 里的止盈位已到"
    )

    model_config = ConfigDict(from_attributes=True)
