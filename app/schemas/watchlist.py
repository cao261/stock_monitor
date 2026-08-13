"""watchlist 的 Pydantic 模型。"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 复用 market_fetcher 的代码归一化规则（5/6→sh, 0/3→sz, 4/8/9→bj）
# 用延迟 import 避免循环依赖（market_fetcher 又会通过 router 拉 db 间接拉 schema）
def _normalize_ts_code(raw: str) -> str:
    """把 6 位纯数字代码归一化为带 sh/sz/bj 前缀的标准形式。"""
    from market_fetcher import _normalize_code
    return _normalize_code(raw)


# Tushare 风格代码：交易所前缀 + 6 位数字
_TS_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$", re.IGNORECASE)
# 6 位纯数字代码
_BARE_CODE_RE = re.compile(r"^\d{6}$")
# 前缀 → 交易所的对应关系（用于交叉校验）
_PREFIX_TO_EXCHANGE = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


class WatchlistBase(BaseModel):
    ts_code: str = Field(
        ..., min_length=6, max_length=16,
        description="形如 sh600000 / sz000001 / bj920000，或 6 位纯数字（自动补前缀）",
    )
    name: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=8, description="SH / SZ / BJ")
    market: str | None = Field(default=None, max_length=16)
    industry: str | None = Field(default=None, max_length=64)
    is_active: bool = Field(default=True)

    # ===== 持仓 / 交易备忘（v1.1 增量）=====
    cost_price: float | None = Field(
        default=None, ge=0, description="买入成本价（元/股），未持仓留空"
    )
    position: int | None = Field(
        default=None, ge=0, description="持仓数量（股），未持仓留空"
    )
    trade_note: str | None = Field(
        default=None, max_length=500, description="交易逻辑备忘"
    )

    # ===== 止盈止损（v1.2 增量）=====
    target_win: float | None = Field(
        default=None, ge=0, description="止盈目标价（元/股），触发后弹通知"
    )
    target_loss: float | None = Field(
        default=None, ge=0, description="止损/防守价（元/股），触发后弹通知"
    )

    # ===== v2.7 网格动态追踪 =====
    # 上次网格加/减仓的基准价。
    # 算法：grid_distance = (现价 - last_grid_price) / last_grid_price * 100
    # 留空时 fallback 用 cost_price 作为初始基准
    last_grid_price: float | None = Field(
        default=None, ge=0,
        description="上次网格加/减仓基准价；空时 fallback 用 cost_price",
    )

    @field_validator("ts_code", mode="before")
    @classmethod
    def _check_ts_code(cls, v) -> str:
        """6 位纯数字 → 自动归一化为带前缀；带前缀的 → 校验格式。"""
        if v is None:
            raise ValueError("ts_code 不能为空")
        raw = str(v).strip().lower()
        if not raw:
            raise ValueError("ts_code 不能为空")
        # 6 位纯数字 → 调归一化
        if _BARE_CODE_RE.match(raw):
            return _normalize_ts_code(raw)
        # 带前缀的 → 校验格式
        if not _TS_CODE_RE.match(raw):
            raise ValueError(
                "ts_code 必须是 6 位纯数字 或 形如 sh600000 / sz000001 / bj920000"
            )
        return raw

    @field_validator("exchange", mode="before")
    @classmethod
    def _check_exchange(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_norm = str(v).strip().upper()
        if v_norm not in {"SH", "SZ", "BJ"}:
            raise ValueError("exchange 必须是 SH / SZ / BJ 之一")
        return v_norm

    @model_validator(mode="after")
    def _check_exchange_consistency(self) -> "WatchlistBase":
        """exchange 字段必须与 ts_code 前缀一致。
        1) 传了 exchange → 交叉校验，不一致 422
        2) 没传 exchange → 从 ts_code 前缀自动补全（用户友好）
        """
        prefix = self.ts_code[:2].lower()
        expected = _PREFIX_TO_EXCHANGE.get(prefix)
        if not expected:
            return self
        if not self.exchange:
            # 自动补全
            object.__setattr__(self, "exchange", expected)
            return self
        if self.exchange != expected:
            raise ValueError(
                f"ts_code 前缀 {prefix.upper()} 与 exchange={self.exchange} 不一致"
                f"（{prefix} 开头的代码应该属于 {expected}）"
            )
        return self


class WatchlistCreate(WatchlistBase):
    """创建自选股：ts_code 必填，其余可选。"""


class WatchlistUpdate(BaseModel):
    """更新自选股：所有字段可选，便于部分更新。"""

    name: str | None = Field(default=None, max_length=64)
    exchange: str | None = Field(default=None, max_length=8)
    market: str | None = Field(default=None, max_length=16)
    industry: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    # 持仓字段：null 表示"不更新这个字段"，传值（含 null）会同步到 DB
    # （注意区分"没传"和"传了 None"——下面用 model_fields_set / model_dump 的策略）
    cost_price: float | None = Field(default=None, ge=0)
    position: int | None = Field(default=None, ge=0)
    trade_note: str | None = Field(default=None, max_length=500)
    # v1.2: 止盈止损
    target_win: float | None = Field(default=None, ge=0)
    target_loss: float | None = Field(default=None, ge=0)
    # v2.7: 网格基准价
    last_grid_price: float | None = Field(default=None, ge=0)

    @field_validator("exchange")
    @classmethod
    def _check_exchange(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_norm = str(v).strip().upper()
        if v_norm not in {"SH", "SZ", "BJ"}:
            raise ValueError("exchange 必须是 SH / SZ / BJ 之一")
        return v_norm


class WatchlistRead(WatchlistBase):
    """响应模型：包含数据库生成字段。"""

    id: int
    created_at: datetime
    updated_at: datetime
    # 关联的告警规则数量（按需可在 router 中填充）
    alert_rules_count: int | None = None

    model_config = ConfigDict(from_attributes=True)
