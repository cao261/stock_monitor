"""A 股情绪与信号计算引擎。

两个核心能力：

* ``calculate_market_sentiment`` —— 全市场情绪打分（0~100）
* ``check_signals`` —— 单只股票的量比信号判断（放量突破 / 缩量企稳）

依赖 ``market_fetcher`` 的内存缓存（``all_stocks_cache`` / ``history_cache``），
**不直接读 DB**，保持纯计算。
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

import market_fetcher as mf

logger = logging.getLogger("analyzer")

# A 股交易时段（自然分钟数）
MORNING_START = time(9, 30)
MORNING_END = time(11, 30)
AFTERNOON_START = time(13, 0)
AFTERNOON_END = time(15, 0)
TRADING_MINUTES_PER_DAY = 240  # 9:30-11:30 + 13:00-15:00

# 涨跌停阈值（科创板 / 创业板 20%，北交所 30%，这里用最严的 9.8% 粗算主板）
LIMIT_UP_THRESHOLD = 9.8
LIMIT_DOWN_THRESHOLD = -9.8

# 信号阈值（按用户需求）
VOLUME_BREAKOUT_RATIO = 2.5
VOLUME_BREAKOUT_CHANGE_PCT = 3.0
SHRINKING_PULLBACK_RATIO = 0.8
SHRINKING_CHANGE_PCT_LO = -1.0
SHRINKING_CHANGE_PCT_HI = 1.0


# ====================== 工具：交易分钟 ======================
def _now_time() -> time:
    return datetime.now().time()


def _is_trading_time(now: time | None = None) -> bool:
    now = now or _now_time()
    return (MORNING_START <= now <= MORNING_END) or (AFTERNOON_START <= now <= AFTERNOON_END)


def trading_minutes_elapsed(now: time | None = None) -> int:
    """当前已开盘多少分钟（午休时段不计入）。非交易时段返回 0。"""
    now = now or _now_time()
    minutes = 0
    # 上午段
    if now >= MORNING_END:
        minutes += 120
    elif now >= MORNING_START:
        minutes += (now.hour * 60 + now.minute) - (9 * 60 + 30)
    # 下午段
    if now >= AFTERNOON_END:
        minutes += 120
    elif now >= AFTERNOON_START:
        minutes += (now.hour * 60 + now.minute) - (13 * 60)
    return max(0, minutes)


# ====================== 功能 A：全市场情绪 ======================
def calculate_market_sentiment() -> dict[str, Any]:
    """遍历 ``all_stocks_cache``，统计涨/跌/涨停/跌停家数并打分。

    评分模型：

    * 基础分 50
    * 基准浮动 = (上涨家数比 - 0.5) * 100
    * 打板溢价 = (涨停家数 - 跌停家数) * 0.1
    * 最终分 = clamp(基础 + 浮动 + 溢价, 0, 100)
    """
    all_stocks = mf.get_all_stocks()
    up_count = down_count = flat_count = 0
    limit_up = limit_down = 0

    for _code, data in all_stocks.items():
        chg = data.get("change_pct", 0.0) or 0.0
        if chg > 0:
            up_count += 1
        elif chg < 0:
            down_count += 1
        else:
            flat_count += 1
        if chg >= LIMIT_UP_THRESHOLD:
            limit_up += 1
        if chg <= LIMIT_DOWN_THRESHOLD:
            limit_down += 1

    decided = up_count + down_count
    up_ratio = up_count / decided if decided > 0 else 0.5

    base_score = 50.0
    swing_score = (up_ratio - 0.5) * 100.0
    limit_premium = (limit_up - limit_down) * 0.1
    final = max(0.0, min(100.0, base_score + swing_score + limit_premium))

    return {
        "total_stocks": len(all_stocks),
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "up_ratio": round(up_ratio, 4),
        "base_score": base_score,
        "swing_score": round(swing_score, 2),
        "limit_premium": round(limit_premium, 2),
        "score": round(final, 2),
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ====================== 功能 B：单只股票信号 ======================
def check_signals(
    code: str,
    current: dict[str, Any],
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对单只股票判断两个信号。

    量比公式（与通达信、同花顺一致）::

        量比 = 实时总成交量 / (过去 5 日均量 / 240 * 当前已开盘分钟数)

    当 5 日均量为 0（无历史数据）或非交易时段时，量比按 0 处理，**不会**触发放量信号。
    """
    avg_vol_5d = float((history or {}).get("avg_volume_5d") or 0.0)
    minutes = trading_minutes_elapsed()

    cur_vol = int(current.get("volume") or 0)
    chg = float(current.get("change_pct") or 0.0)
    price = float(current.get("price") or 0.0)
    open_p = float(current.get("open") or 0.0)

    # 预期成交量 = 5日均量 / 240 * 当前已开盘分钟数（股）
    expected = (avg_vol_5d / TRADING_MINUTES_PER_DAY) * minutes if avg_vol_5d > 0 else 0.0
    vol_ratio = (cur_vol / expected) if expected > 0 else 0.0

    is_volume_breakout = (
        (vol_ratio > VOLUME_BREAKOUT_RATIO)
        and (chg > VOLUME_BREAKOUT_CHANGE_PCT)
        and (price > open_p > 0)
    )
    is_shrinking_pullback = (
        (SHRINKING_CHANGE_PCT_LO <= chg <= SHRINKING_CHANGE_PCT_HI)
        and (vol_ratio < SHRINKING_PULLBACK_RATIO)
        and (vol_ratio > 0)  # 必须有有效量比（避免非交易时段误判）
    )

    return {
        "code": code,
        "name": current.get("name"),
        "trading_minutes": minutes,
        "is_trading_time": _is_trading_time(),
        "volume_ratio": round(vol_ratio, 3),
        "current": {
            "price": price,
            "open": open_p,
            "prev_close": float(current.get("prev_close") or 0.0),
            "change_pct": chg,
            "volume": cur_vol,
        },
        "history": {
            "avg_volume_5d": avg_vol_5d,
            "expected_volume_so_far": round(expected, 0),
        },
        "signals": {
            "is_volume_breakout": is_volume_breakout,
            "is_shrinking_pullback": is_shrinking_pullback,
        },
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
    }
