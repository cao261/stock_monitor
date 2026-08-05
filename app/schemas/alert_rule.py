"""alert_rule 的 Pydantic 模型（骨架阶段先建好，便于后续扩展 /alert_rules 路由）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


_ALLOWED_COMPARISON = {">", "<", ">=", "<=", "=="}


class AlertRuleBase(BaseModel):
    signal_type: str = Field(..., min_length=1, max_length=32)
    threshold: float
    comparison: str = Field(default=">")
    enabled: bool = Field(default=True)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("comparison")
    @classmethod
    def _check_comparison(cls, v: str) -> str:
        if v not in _ALLOWED_COMPARISON:
            raise ValueError("comparison 必须是 >, <, >=, <=, == 之一")
        return v


class AlertRuleRead(AlertRuleBase):
    id: int
    stock_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
