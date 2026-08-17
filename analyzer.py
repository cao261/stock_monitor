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


try:
    from zoneinfo import ZoneInfo
    SH_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    SH_TZ = None

# ====================== 工具：交易分钟 ======================
def _now_time() -> time:
    if SH_TZ:
        return datetime.now(SH_TZ).time()
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
            "close": price,  # v4.5: close 别名（strategy.py daily-summary 按 close 读价）
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


def calculate_stock_ambush_levels(
    code: str,
    cur_price: float | None = None,
    history_records: list[dict] | None = None,
) -> dict[str, Any]:
    """为低位埋伏精准计算真实支撑位、压力位、ATR波动率、建议低吸甜区、目标止盈与防守止损。

    依据真实技术面：
    - 支撑位：优先取 MA20 或 20日箱体下轨最低点
    - 压力位：取 20日箱体上轨高点或基于 ATR 动态扩展
    - 波动率：基于 20日收益率标准差与 ATR 划分进攻/稳健/防守型
    - 低吸甜区：围绕支撑位与短期均线粘合带构建
    """
    features = extract_kline_features(history_records)
    price = cur_price or features.get("latest_close") or 10.0

    ma20 = features.get("ma20")
    support_20d = features.get("support_level")
    resistance_20d = features.get("resistance_level")
    atr = features.get("atr14") or round(price * 0.035, 2)
    vol_pct = features.get("volatility_20d_pct") or 3.0

    # 1. 真实支撑位确定
    if ma20 and ma20 > 0 and price >= ma20 * 0.96:
        support_price = round(ma20, 2)
        support_name = f"20日均线支撑 (¥{support_price})"
    elif support_20d and support_20d > 0:
        support_price = round(support_20d, 2)
        support_name = f"近期箱体底部支撑 (¥{support_price})"
    else:
        support_price = round(max(0.01, price - 1.5 * atr), 2)
        support_name = f"ATR动态防守支撑 (¥{support_price})"

    # 2. 真实压力位确定
    if resistance_20d and resistance_20d > price * 1.01:
        resistance_price = round(resistance_20d, 2)
        resistance_name = f"近期箱体上轨/前高压力 (¥{resistance_price})"
    else:
        resistance_price = round(price + 2.5 * atr, 2)
        resistance_name = f"ATR趋势上攻目标压力 (¥{resistance_price})"

    # 3. 波动属性分类
    if vol_pct >= 4.5:
        vol_tag = f"⚡ 高弹性进攻型 (日均波动 {vol_pct:.1f}%, ATR ¥{atr:.2f})"
    elif vol_pct >= 2.5:
        vol_tag = f"📈 稳健成长型 (日均波动 {vol_pct:.1f}%, ATR ¥{atr:.2f})"
    else:
        vol_tag = f"🛡️ 低波防守型 (日均波动 {vol_pct:.1f}%, ATR ¥{atr:.2f})"

    # 4. 建议低吸买点甜区：围绕支撑位构建
    zone_min = round(max(0.01, support_price * 0.99), 2)
    zone_max = round(min(price * 1.01, max(zone_min + 0.01, (support_price + price) / 2)), 2)
    ambush_zone = [min(zone_min, zone_max), max(zone_min, zone_max)]

    # 5. 建议止盈与止损
    target_win = round(max(price * 1.05, resistance_price), 2)
    stop_loss = round(max(0.01, support_price * 0.965), 2)

    # 6. 技术面逻辑描述
    basis = f"{vol_tag}。关键防守位在 {support_name}，上方第一目标压力为 {resistance_name}。在接近支撑位 [¥{ambush_zone[0]} ~ ¥{ambush_zone[1]}] 区间低吸盈亏比最佳。"

    return {
        "support_price": support_price,
        "support_name": support_name,
        "resistance_price": resistance_price,
        "resistance_name": resistance_name,
        "volatility_pct": round(vol_pct, 2),
        "volatility_tag": vol_tag,
        "atr": round(atr, 2),
        "ambush_zone": ambush_zone,
        "target_win": target_win,
        "stop_loss": stop_loss,
        "technical_basis": basis,
    }


# ====================== v4.3: 4 维评分 + 5 大左侧信号 (集成自 sector_hunter TRADING_LOGIC.md) ======================
# 4 维：消息面 (msg) / 资金面 (cap) / 技术面 (tech) / 情绪面 (sent)，各 0-100
# 5 大左侧信号：底部吸筹 / 政策反转 / 动量反转 / 资金异动 / 事件预热
# 数据源：fund_flow_cache (板块资金流) + all_stocks_cache (全市场) + news_fetcher (新闻)
def score_sector_4d(sector_name: str, sector_data: dict | None = None) -> dict:
    """对单个板块（来自 fund_flow_cache）做 4 维量化评分（0-100）。

    简化版（区别于 sector_hunter 的复杂 11 子项）：
    - 资金面（30%）：net_amount（主力净流入）+ leading_change_pct（领涨股涨幅）
    - 技术面（25%）：板块当日 change_pct + 领涨股 leading_change_pct
    - 消息面（30%）：由 LLM T1 阶段验证后给出（此处仅占位 50 分中性分）
    - 情绪面（15%）：change_pct 越低（低位）得分越高（散户没注意 = 反向利多）

    Returns:
        dict: { msg, cap, tech, sent, total, grade, breakdown }
    """
    import market_fetcher as mf
    if sector_data is None:
        fund = mf.get_fund_flow()
        for s in fund.get("data", []):
            if s.get("name") == sector_name:
                sector_data = s
                break
    if not sector_data:
        return {"msg": 50.0, "cap": 50.0, "tech": 50.0, "sent": 50.0, "total": 50.0, "grade": "C", "breakdown": {}}

    net_amount = float(sector_data.get("net_amount", 0) or 0)
    leading_chg = float(sector_data.get("leading_change_pct", 0) or 0)
    sector_chg = float(sector_data.get("change_pct", 0) or 0)
    company_count = int(sector_data.get("company_count", 0) or 0)

    # ===== 资金面 (30%)：net_amount 是核心 =====
    # -10亿 = 0分, 0 = 50分, +10亿 = 75分, +50亿 = 100分
    cap_raw = 50 + net_amount * 2.5
    cap = max(0.0, min(100.0, cap_raw))

    # ===== 技术面 (25%)：板块当日 + 领涨股 =====
    # 0% = 50分, +5% = 75分, -5% = 25分（缩量企稳得分高）
    tech_raw = 50 + (sector_chg * 5) + (leading_chg * 2)
    tech = max(0.0, min(100.0, tech_raw))

    # ===== 消息面 (30%)：占位 50 分（实际由 LLM T1 验证后给出 real_score 覆盖） =====
    msg = 50.0

    # ===== 情绪面 (15%)：低位 + 散户没注意 = 反向利多 =====
    # 涨幅 0% = 50分, +5% = 30分 (高位散户涌入是反向), -5% = 70分 (低位无人问津)
    sent_raw = 50 - (sector_chg * 4)
    sent = max(0.0, min(100.0, sent_raw))

    # ===== 总分 (4 维加权平均) =====
    total = msg * 0.30 + cap * 0.30 + tech * 0.25 + sent * 0.15

    # ===== 等级 (A/B/C/D) =====
    if total >= 80:
        grade = "A"
    elif total >= 65:
        grade = "B"
    elif total >= 50:
        grade = "C"
    else:
        grade = "D"

    return {
        "msg": round(msg, 1),
        "cap": round(cap, 1),
        "tech": round(tech, 1),
        "sent": round(sent, 1),
        "total": round(total, 1),
        "grade": grade,
        "breakdown": {
            "net_amount": net_amount,
            "leading_change_pct": leading_chg,
            "sector_change_pct": sector_chg,
            "company_count": company_count,
        },
    }


def detect_left_side_signals(
    sector_name: str,
    sector_data: dict | None = None,
    quant_scores: dict | None = None,
    news: list[dict] | None = None,
) -> list[dict]:
    """检测板块是否触发 5 大左侧信号 (来自 sector_hunter TRADING_LOGIC 第 2.2 节)。

    Returns:
        list[dict]: [{"name": "底部吸筹型", "triggered": True, "conditions_met": ["价格分位 ≤ 30%", ...]}, ...]
    """
    import market_fetcher as mf
    if sector_data is None:
        fund = mf.get_fund_flow()
        for s in fund.get("data", []):
            if s.get("name") == sector_name:
                sector_data = s
                break
    if not sector_data:
        return []

    sector_chg = float(sector_data.get("change_pct", 0) or 0)
    leading_chg = float(sector_data.get("leading_change_pct", 0) or 0)
    net_amount = float(sector_data.get("net_amount", 0) or 0)
    company_count = int(sector_data.get("company_count", 0) or 0)

    signals: list[dict] = []

    # ① 底部吸筹：低涨幅 + 净流入 + 新闻平稳
    if -3.0 <= sector_chg <= 1.5 and net_amount > 0:
        # 检查新闻关键词（避免大涨/事件扰动）
        news_count = len(news) if news else 0
        cond = [
            f"板块涨幅 {sector_chg:+.2f}%（低位蓄势）",
            f"主力净流入 {net_amount:+.2f} 亿",
            f"新闻数 {news_count} 条（{('平稳' if news_count <= 5 else '偏热')}）",
        ]
        signals.append({
            "name": "底部吸筹型",
            "type": "bottom_accumulation",
            "triggered": True,
            "description": "聪明钱在底部建仓，散户毫无察觉，教科书级左侧机会",
            "conditions_met": cond,
        })

    # ② 政策反转：低涨幅 + 净流入 + 新闻含政策/利好关键词
    if news:
        policy_keywords = ["政策", "利好", "支持", "补贴", "印发", "规划", "部署", "推进", "试点"]
        news_with_policy = [n for n in news if any(kw in (n.get("title", "") or n.get("content", "")) for kw in policy_keywords)]
        if news_with_policy and -5.0 <= sector_chg <= 2.0 and net_amount > 0:
            signals.append({
                "name": "政策驱动反转型",
                "type": "policy_reversal",
                "triggered": True,
                "description": "重大政策发布 + 板块前期超跌 + 市场未充分反应",
                "conditions_met": [
                    f"近 3 天含政策/利好新闻 {len(news_with_policy)} 条",
                    f"板块涨幅 {sector_chg:+.2f}%（未充分反应）",
                    f"主力净流入 {net_amount:+.2f} 亿",
                ],
            })

    # ③ 动量反转：领涨股已反弹 + 板块整体仍弱
    if leading_chg > 2.0 and sector_chg < 1.5:
        signals.append({
            "name": "动量反转型",
            "type": "momentum_reversal",
            "triggered": True,
            "description": "领涨股已启动，板块整体未跟上，存在补涨空间",
            "conditions_met": [
                f"领涨股已涨 {leading_chg:+.2f}%",
                f"板块整体仅 {sector_chg:+.2f}%（跟涨滞后）",
            ],
        })

    # ④ 资金异动：净流入显著（>2亿）+ 板块未大涨
    if net_amount > 2.0 and sector_chg < 3.0:
        signals.append({
            "name": "资金底部异动型",
            "type": "capital_turn",
            "triggered": True,
            "description": "主力大额流入，但价格还没启动——先知先觉者入场",
            "conditions_met": [
                f"主力净流入 {net_amount:+.2f} 亿（> 2 亿阈值）",
                f"板块涨幅 {sector_chg:+.2f}%（未启动）",
            ],
        })

    # ⑤ 事件预热：新闻含事件关键词 + 板块小幅异动
    if news:
        event_keywords = ["大会", "会议", "发布", "开幕", "闭幕", "启动", "揭牌", "奠基", "首飞", "量产", "上市", "通车"]
        news_with_event = [n for n in news if any(kw in (n.get("title", "") or n.get("content", "")) for kw in event_keywords)]
        if news_with_event and 0.5 <= sector_chg <= 5.0:
            signals.append({
                "name": "事件预热型",
                "type": "event_warmup",
                "triggered": True,
                "description": "事件还没发生，但市场已开始定价",
                "conditions_met": [
                    f"近 7 天含事件性新闻 {len(news_with_event)} 条",
                    f"板块近 3 日 {sector_chg:+.2f}%（开始异动）",
                ],
            })

    return signals


def filter_low_ambush_sectors(top_n: int = 5) -> list[dict]:
    """从 fund_flow_cache 筛选"低位埋伏"候选板块：
    - 净流入为正（资金在动）
    - 当日涨幅 -2% ~ +3.8%（未启动 / 刚启动）
    - 至少 3 只成分股（板块有意义）
    按净流入排序取 top_n。

    Returns:
        list[dict]: [{name, change_pct, net_amount, leading_stock, leading_change_pct, company_count}, ...]
    """
    import market_fetcher as mf
    fund = mf.get_fund_flow()
    sectors_all = list(fund.get("data", []))
    # 过滤：净流入 > 0 + 涨幅温和 + 至少 3 只成分股
    candidates = [
        s for s in sectors_all
        if (s.get("net_amount") or 0) > 0
        and -2.0 <= (s.get("change_pct") or 0) <= 3.8
        and (s.get("company_count") or 0) >= 3
    ]
    # 按净流入降序
    candidates.sort(key=lambda s: s.get("net_amount", 0), reverse=True)
    return candidates[:top_n]


def merge_sector_news(sector_name: str, news: list[dict], max_n: int = 8) -> list[dict]:
    """从原始 news 池中按 sector 关键词匹配相关消息。

    Returns:
        list[dict]: [{title, time, source, content}, ...] 最多 max_n 条
    """
    if not news or not sector_name:
        return []
    import re
    parts = re.split(r"[\s,，、/／与和及()()（）]+", sector_name)
    keywords = {p for p in parts if len(p) >= 2}
    # 滑窗拆 2~3 字子词
    for p in list(keywords):
        for wlen in (2, 3):
            for i in range(0, max(0, len(p) - wlen + 1)):
                keywords.add(p[i:i + wlen])
    if not keywords:
        keywords = {sector_name[:4]}

    matched = []
    for n in news:
        title = (n.get("title") or n.get("content", "")[:80]) or ""
        if any(kw in title for kw in keywords):
            matched.append({
                "title": str(title)[:80],
                "time": n.get("time", ""),
                "source": n.get("source", ""),
                "content": str(n.get("content", ""))[:200],
            })
        if len(matched) >= max_n:
            break
    return matched


