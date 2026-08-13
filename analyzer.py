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
    *,
    target_win: float | None = None,
    target_loss: float | None = None,
    position: int | None = None,
    entry_price_min: float | None = None,
    entry_price_max: float | None = None,
) -> dict[str, Any]:
    """对单只股票判断 5 个信号（量比 ×2 + 止盈/止损 ×2 + v4.0 建仓机会 ×1）。

    量比公式（与通达信、同花顺一致）::

        量比 = 实时总成交量 / (过去 5 日均量 / 240 * 当前已开盘分钟数)

    当 5 日均量为 0（无历史数据）或非交易时段时，量比按 0 处理，**不会**触发放量信号。

    止盈止损（v1.2）：
      - target_win  设置 + 现价 >= target_win + **有持仓 (position>0)**  → is_take_profit
      - target_loss 设置 + 现价 <= target_loss + **有持仓 (position>0)** → is_stop_loss
      - v3.1: 空仓时强制 is_take_profit / is_stop_loss = False（幽灵告警修复）
        量比信号 (is_volume_breakout / is_shrinking_pullback) 不受持仓状态影响
      - 都触发时 `trade_message` 给出双行提示文案

    v4.0 建仓机会（领航员前瞻信号）：
      - 空仓 (position is None or <= 0) + entry_price_min / entry_price_max 都已设置
        + 现价落入 [min, max] 区间内 → is_entry_opportunity = True
      - 前端金色高亮 + 桌面弹窗（每日每只股票最多 1 次）
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
        and (vol_ratio > 0)
    )

    # ===== v3.1: 空仓强校验 =====
    # 空仓（position is None or <= 0）→ 止盈/止损信号全 False（避免幽灵告警）
    # 量比信号（放量/缩量）仍可触发（用于"找买点"）
    is_empty_position = (position is None) or (int(position) <= 0)

    # ===== 止盈/止损（v1.2 + v3.1 空仓校验）=====
    is_take_profit = bool(
        (not is_empty_position)
        and target_win and target_win > 0
        and price > 0 and price >= target_win
    )
    is_stop_loss = bool(
        (not is_empty_position)
        and target_loss and target_loss > 0
        and price > 0 and price <= target_loss
    )

    # ===== v4.0: 建仓机会（空仓 + 现价落入 [min, max] 区间）=====
    is_entry_opportunity = bool(
        is_empty_position
        and entry_price_min and entry_price_min > 0
        and entry_price_max and entry_price_max > 0
        and price > 0
        and float(entry_price_min) <= price <= float(entry_price_max)
    )

    # 触发文案（双行，去桌面通知的 body 用）
    trade_messages: list[str] = []
    if is_take_profit and target_win is not None:
        trade_messages.append(f"到达止盈线 {target_win:.2f}，注意减仓")
    if is_stop_loss and target_loss is not None:
        trade_messages.append(f"触发止损线 {target_loss:.2f}，注意防守")
    if is_entry_opportunity and entry_price_min is not None and entry_price_max is not None:
        trade_messages.append(
            f"到达理想建仓区间 [{entry_price_min:.2f}, {entry_price_max:.2f}]，可考虑分批建仓"
        )
    trade_message = "\n".join(trade_messages) if trade_messages else None

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
            "is_take_profit": is_take_profit,
            "is_stop_loss": is_stop_loss,
            "is_entry_opportunity": is_entry_opportunity,  # v4.0
        },
        "trade_message": trade_message,
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ====================== 功能 C：网格动态追踪（v2.7，v3.1 加空仓强校验）======================
def check_grid_signals(
    price: float | None,
    last_grid_price: float | None,
    cost_price: float | None,
    grid_step_pct: float | None,
    position: int | None = None,
) -> dict[str, Any]:
    """根据"现价 vs 基准价"判断网格加减仓信号。

    算法（与用户拍板的方案一致）：
        grid_reference = last_grid_price or cost_price
        if grid_reference and grid_step_pct and price:
            grid_distance = (price - grid_reference) / grid_reference * 100
            is_grid_buy  = grid_distance <= -grid_step_pct   # 跌到位 → 加仓
            is_grid_sell = grid_distance >=  grid_step_pct   # 涨到位 → 减仓

    v3.1: 空仓强校验
        if position <= 0 (空仓)：
            is_grid_sell 永远 False（空仓不能卖）
            is_grid_buy  仍可触发（空仓等跌到加仓位建仓，这是预期场景）
    """
    empty: dict[str, Any] = {
        "grid_reference_price": None,
        "grid_distance": None,
        "is_grid_buy": False,
        "is_grid_sell": False,
    }
    # 前置条件：现价 + 步长 + 基准价（last_grid_price 优先，fallback cost_price）
    if price is None or price <= 0:
        return empty
    if grid_step_pct is None or grid_step_pct <= 0:
        return empty
    reference = (
        last_grid_price if (last_grid_price is not None and last_grid_price > 0) else cost_price
    )
    if reference is None or reference <= 0:
        return empty

    grid_distance = round((float(price) - float(reference)) / float(reference) * 100.0, 2)
    is_grid_buy = bool(grid_distance <= -float(grid_step_pct))
    is_grid_sell = bool(grid_distance >= float(grid_step_pct))

    # v3.1: 空仓时禁掉 is_grid_sell（避免"幽灵卖出信号"）
    if position is None or int(position) <= 0:
        is_grid_sell = False
    # 关键保留：空仓时 is_grid_buy 仍触发（这是"等跌建仓"的预期信号）

    return {
        "grid_reference_price": float(reference),
        "grid_distance": grid_distance,
        "is_grid_buy": is_grid_buy,
        "is_grid_sell": is_grid_sell,
    }


# ====================== 功能 D: v4.0 K 线特征提取（给 AI 智能规划用）======================
def _empty_kline_features() -> dict[str, Any]:
    """K 线特征为空 / 数据不足时的兜底 dict。"""
    return {
        "data_points": 0,
        "latest_close": None,
        "ma5": None, "ma10": None, "ma20": None, "ma60": None,
        "atr14": None,
        "volatility_20d_pct": None,
        "recent_high_20d": None, "recent_low_20d": None,
        "support_level": None, "resistance_level": None,
        "volume_trend": "数据不足",
        "trend": "数据不足",
        "consecutive_up_days": 0,
        "consecutive_down_days": 0,
    }


def extract_kline_features(history_data: list[dict] | None) -> dict[str, Any]:
    """从最近 N 天 K 线提取技术特征（v4.0 AI 智能规划用）。

    Args:
        history_data: history_cache['data'] 列表，每项形如
            ``{date, open, close, high, low, volume_lots}``。允许为 None 或空。

    Returns:
        dict: 包含 MA / ATR / 波动率 / 支撑阻力 / 量能趋势 / 连阳连阴等结构化特征。
        数据不足时返回 ``_empty_kline_features()`` 形状（字段都是 None / 0）。
    """
    if not history_data:
        return _empty_kline_features()

    # 按日期升序
    data = sorted(history_data, key=lambda x: x.get("date", ""))

    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes_lots: list[float] = []
    for d in data:
        try:
            if d.get("close") is not None:
                closes.append(float(d["close"]))
            if d.get("high") is not None:
                highs.append(float(d["high"]))
            if d.get("low") is not None:
                lows.append(float(d["low"]))
            v = d.get("volume_lots", 0) or 0
            volumes_lots.append(float(v))
        except (TypeError, ValueError):
            continue

    n = len(closes)
    if n < 5:
        return _empty_kline_features()

    def _ma(arr: list[float], window: int) -> float | None:
        if len(arr) < window or window <= 0:
            return None
        return round(sum(arr[-window:]) / window, 4)

    def _atr(hs: list[float], ls: list[float], cs: list[float], window: int = 14) -> float | None:
        if len(cs) < window + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(cs)):
            tr = max(
                hs[i] - ls[i],
                abs(hs[i] - cs[i - 1]),
                abs(ls[i] - cs[i - 1]),
            )
            trs.append(tr)
        if len(trs) < window:
            return None
        return round(sum(trs[-window:]) / window, 4)

    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60) if n >= 60 else None
    atr14 = _atr(highs, lows, closes, 14)

    # 20 日波动率（收益率标准差，百分比）
    vol_20_pct: float | None = None
    if n >= 21:
        rets = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, n) if closes[i - 1] > 0]
        if len(rets) >= 20:
            last20 = rets[-20:]
            mean = sum(last20) / 20
            var = sum((r - mean) ** 2 for r in last20) / 20
            vol_20_pct = round((var ** 0.5) * 100, 3)

    # 支撑 / 阻力：取最近 20 日最低 / 最高
    recent_high_20d = round(max(highs[-20:]), 4) if len(highs) >= 20 else (round(max(highs), 4) if highs else None)
    recent_low_20d = round(min(lows[-20:]), 4) if len(lows) >= 20 else (round(min(lows), 4) if lows else None)
    support_level = recent_low_20d
    resistance_level = recent_high_20d

    # 量能趋势：最近 5 日均量 vs 前 5 日均量
    volume_trend = "数据不足"
    if len(volumes_lots) >= 10:
        recent5 = sum(volumes_lots[-5:]) / 5
        prior5 = sum(volumes_lots[-10:-5]) / 5
        if prior5 > 0:
            ratio = recent5 / prior5
            if ratio > 1.3:
                volume_trend = "放量"
            elif ratio < 0.7:
                volume_trend = "缩量"
            else:
                volume_trend = "平稳"
        else:
            volume_trend = "未知"

    # 趋势：MA5 vs MA20
    trend = "数据不足"
    if ma5 and ma20 and ma20 > 0:
        diff_pct = (ma5 - ma20) / ma20
        if diff_pct > 0.02:
            trend = "上升"
        elif diff_pct < -0.02:
            trend = "下降"
        else:
            trend = "震荡"

    # 连阳 / 连阴天数（从最近一根往前数）
    consec_up = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            consec_up += 1
        else:
            break
    consec_down = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            consec_down += 1
        else:
            break

    return {
        "data_points": n,
        "latest_close": round(closes[-1], 4) if closes else None,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "atr14": atr14,
        "volatility_20d_pct": vol_20_pct,
        "recent_high_20d": recent_high_20d,
        "recent_low_20d": recent_low_20d,
        "support_level": support_level,
        "resistance_level": resistance_level,
        "volume_trend": volume_trend,
        "trend": trend,
        "consecutive_up_days": consec_up,
        "consecutive_down_days": consec_down,
    }


def build_ai_plan_payload(
    ts_code: str,
    history_data: list[dict] | None,
    current_price: float | None,
    ohlcv_days: int = 10,
) -> tuple[dict, list[dict]]:
    """v4.0: 给 LLM 喂的"特征 + 原始 OHLCV"拼装工具。

    Returns:
        (features, ohlcv_10d)：
        - features:  ``extract_kline_features()`` 的输出
        - ohlcv_10d: 最近 ``ohlcv_days`` 天的原始 OHLCV 列表（按日期升序）

    调用方拿到这两个 dict 后传给 ``llm.generate_ai_plan()``。
    """
    features = extract_kline_features(history_data)
    if not history_data:
        return features, []

    # 按日期升序取最近 ohlcv_days 天
    sorted_data = sorted(history_data, key=lambda x: x.get("date", ""))
    last_n = sorted_data[-ohlcv_days:] if len(sorted_data) > ohlcv_days else sorted_data
    ohlcv_10d: list[dict] = []
    for d in last_n:
        try:
            ohlcv_10d.append({
                "date": d.get("date"),
                "open": float(d.get("open") or 0.0),
                "close": float(d.get("close") or 0.0),
                "high": float(d.get("high") or 0.0),
                "low": float(d.get("low") or 0.0),
                "volume_lots": float(d.get("volume_lots") or 0.0),
            })
        except (TypeError, ValueError):
            continue
    return features, ohlcv_10d

