"""watchlist 的 Pydantic 模型。"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Tushare 风格代码：交易所前缀 + 6 位数字
_TS_CODE_RE = re.compile(r"^(sh|sz|bj)\d{6}$", re.IGNORECASE)


class WatchlistBase(BaseModel):
    ts_code: str = Field(..., min_length=8, max_length=16, description="如 sh600000")
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

    @field_validator("ts_code")
    @classmethod
    def _check_ts_code(cls, v: str) -> str:
        v_norm = v.strip().lower()
        if not _TS_CODE_RE.match(v_norm):
            raise ValueError("ts_code 必须形如 sh600000 / sz000001 / bj920000")
        return v_norm

    @field_validator("exchange")
    @classmethod
    def _check_exchange(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_norm = v.strip().upper()
        if v_norm not in {"SH", "SZ", "BJ"}:
            raise ValueError("exchange 必须是 SH / SZ / BJ 之一")
        return v_norm


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

    @field_validator("exchange")
    @classmethod
    def _check_exchange(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v_norm = v.strip().upper()
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
