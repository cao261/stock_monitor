"""analyzer + history_cache 的单元测试。"""
from __future__ import annotations

import asyncio
import sys
import time as time_mod
from datetime import time

import analyzer
import market_fetcher as mf


# ====================== 1. trading_minutes_elapsed ======================
def test_trading_minutes() -> None:
    print("== test_trading_minutes ==")
    cases = [
        # (time, expected_minutes)
        (time(9, 0), 0),       # 开盘前
        (time(9, 30), 0),      # 刚开盘
        (time(10, 0), 30),     # 开盘 30 分钟
        (time(11, 0), 90),     # 开盘 90 分钟
        (time(11, 30), 120),   # 上午收盘
        (time(12, 0), 120),    # 午休
        (time(12, 59), 120),   # 午休最后
        (time(13, 0), 120),    # 下午开盘
        (time(14, 0), 180),    # 下午 1 小时
        (time(15, 0), 240),    # 收盘
        (time(16, 0), 240),    # 收盘后
    ]
    for t, expected in cases:
        got = analyzer.trading_minutes_elapsed(t)
        assert got == expected, f"trading_minutes({t}) = {got}, expected {expected}"
    print(f"  OK ({len(cases)} cases)")


def test_is_trading_time() -> None:
    print("== test_is_trading_time ==")
    cases = [
        (time(9, 29), False),
        (time(9, 30), True),
        (time(11, 30), True),
        (time(12, 0), False),
        (time(13, 0), True),
        (time(15, 0), True),
        (time(15, 1), False),
    ]
    for t, expected in cases:
        got = analyzer._is_trading_time(t)
        assert got == expected, f"_is_trading_time({t}) = {got}, expected {expected}"
    print(f"  OK ({len(cases)} cases)")


# ====================== 2. calculate_market_sentiment ======================
def test_market_sentiment() -> None:
    print("== test_market_sentiment ==")
    # 注入 mock 数据
    mf.all_stocks_cache.clear()
    mf.all_stocks_cache["__meta__"] = {}
    # 60 涨 40 跌 0 平，涨停 5 跌停 0
    for i in range(60):
        mf.all_stocks_cache[f"up{i:04d}"] = {"change_pct": 1.0 + (i % 8), "price": 10.0}
    for i in range(5):
        mf.all_stocks_cache[f"lu{i:04d}"] = {"change_pct": 9.9, "price": 10.0}  # 涨停
    for i in range(40):
        mf.all_stocks_cache[f"dn{i:04d}"] = {"change_pct": -1.0 - (i % 5), "price": 10.0}

    result = analyzer.calculate_market_sentiment()
    print(f"  result={result}")
    assert result["total_stocks"] == 105
    # 涨停 5 只也属上涨，所以 up_count = 60 + 5 = 65
    assert result["up_count"] == 65
    assert result["down_count"] == 40
    assert result["limit_up_count"] == 5
    assert result["limit_down_count"] == 0
    # 上涨家数比 = 65/105 ≈ 0.619, swing_score = 11.9
    assert result["swing_score"] == 11.9
    # limit_premium = 5 * 0.1 = 0.5
    assert result["limit_premium"] == 0.5
    # score = 50 + 11.9 + 0.5 = 62.4
    assert result["score"] == 62.4
    assert 0 <= result["score"] <= 100
    print("  OK")

    # 测试极端：全部跌停
    mf.all_stocks_cache.clear()
    mf.all_stocks_cache["__meta__"] = {}
    for i in range(50):
        mf.all_stocks_cache[f"x{i:04d}"] = {"change_pct": -10.0}
    result2 = analyzer.calculate_market_sentiment()
    assert result2["limit_down_count"] == 50
    # 0 涨 50 跌 -> up_ratio=0, swing=-50, premium=-50*0.1=-5
    # score = 50 - 50 - 5 = -5 -> clamp 到 0
    assert result2["score"] == 0.0
    print(f"  extreme case (50 limit_down) score=0 OK")


# ====================== 3. check_signals ======================
def test_check_signals_breakout() -> None:
    print("== test_check_signals_volume_breakout ==")
    # 量比 > 2.5 + 涨幅 > 3% + 现价 > 开盘价 -> 放量突破
    # avg_vol_5d = 1000万, 假设 240 分钟全部开盘
    # expected = 10,000,000 / 240 * 240 = 10,000,000
    # 当前 vol = 30,000,000 -> vol_ratio = 3.0
    current = {
        "price": 11.0,
        "open": 10.0,
        "prev_close": 9.8,
        "change_pct": 5.0,
        "volume": 30_000_000,
    }
    history = {"avg_volume_5d": 10_000_000.0, "avg_amount_5d": 0.0, "data": []}

    # mock 强制 240 分钟，让量比最大
    orig = analyzer.trading_minutes_elapsed
    analyzer.trading_minutes_elapsed = lambda now=None: 240
    try:
        sig = analyzer.check_signals("sh600000", current, history)
    finally:
        analyzer.trading_minutes_elapsed = orig

    print(f"  volume_ratio={sig['volume_ratio']} signals={sig['signals']}")
    assert sig["volume_ratio"] == 3.0, sig["volume_ratio"]
    assert sig["signals"]["is_volume_breakout"] is True
    assert sig["signals"]["is_shrinking_pullback"] is False
    print("  OK (volume breakout detected)")


def test_check_signals_shrinking() -> None:
    print("== test_check_signals_shrinking ==")
    # 量比 < 0.8 + 涨幅在 ±1% 内 -> 缩量企稳
    current = {
        "price": 10.05,
        "open": 10.0,
        "prev_close": 10.0,
        "change_pct": 0.5,
        "volume": 5_000_000,
    }
    history = {"avg_volume_5d": 10_000_000.0, "avg_amount_5d": 0.0, "data": []}

    orig = analyzer.trading_minutes_elapsed
    analyzer.trading_minutes_elapsed = lambda now=None: 240
    try:
        sig = analyzer.check_signals("sh600000", current, history)
    finally:
        analyzer.trading_minutes_elapsed = orig
    print(f"  volume_ratio={sig['volume_ratio']} signals={sig['signals']}")
    assert sig["volume_ratio"] == 0.5
    assert sig["signals"]["is_volume_breakout"] is False
    assert sig["signals"]["is_shrinking_pullback"] is True
    print("  OK (shrinking pullback detected)")


def test_check_signals_normal() -> None:
    print("== test_check_signals_normal ==")
    # 普通情况：量比适中、涨幅 1.5% — 两个信号都不应触发
    current = {
        "price": 10.15,
        "open": 10.0,
        "prev_close": 10.0,
        "change_pct": 1.5,
        "volume": 10_000_000,
    }
    history = {"avg_volume_5d": 10_000_000.0, "avg_amount_5d": 0.0, "data": []}
    orig = analyzer.trading_minutes_elapsed
    analyzer.trading_minutes_elapsed = lambda now=None: 240
    try:
        sig = analyzer.check_signals("sh600000", current, history)
    finally:
        analyzer.trading_minutes_elapsed = orig
    assert sig["signals"]["is_volume_breakout"] is False
    assert sig["signals"]["is_shrinking_pullback"] is False
    print("  OK (no signal)")


def test_check_signals_no_history() -> None:
    print("== test_check_signals_no_history ==")
    current = {"price": 11.0, "open": 10.0, "change_pct": 5.0, "volume": 30_000_000}
    sig = analyzer.check_signals("sh600000", current, None)
    assert sig["volume_ratio"] == 0.0
    # 没有历史数据时放量信号不应触发
    assert sig["signals"]["is_volume_breakout"] is False
    print("  OK (no history -> no signal)")


# ====================== 4. fetch_history_sync (real network) ======================
def test_fetch_history_real() -> None:
    print("== test_fetch_history_real ==")
    data = mf.fetch_history_sync("sh600000", days=10)
    print(f"  avg_volume_5d={data['avg_volume_5d']:,.0f} 股")
    print(f"  avg_amount_5d={data['avg_amount_5d']:,.2f} 元")
    print(f"  data records={len(data.get('data', []))}")
    if data.get("data"):
        last = data["data"][-1]
        print(f"  last day: {last}")
    assert data.get("data"), "应至少有 5 条日线"
    assert data["avg_volume_5d"] > 0
    assert data["avg_amount_5d"] > 0
    print("  OK")


def test_fetch_history_async() -> None:
    print("== test_fetch_history_async (real) ==")
    async def run():
        codes = ["sh600000", "sh601318", "sz000001", "sz300750"]
        n = await mf.fetch_history_for_codes(codes, concurrency=4)
        for c in codes:
            h = mf.get_history(c)
            print(f"  {c}  avg_vol_5d={h['avg_volume_5d']:,.0f}  records={len(h.get('data', []))}")
            assert h.get("data")
    asyncio.run(run())
    print("  OK")


# ====================== 入口 ======================
def main() -> int:
    test_trading_minutes()
    test_is_trading_time()
    test_market_sentiment()
    test_check_signals_breakout()
    test_check_signals_shrinking()
    test_check_signals_normal()
    test_check_signals_no_history()
    test_fetch_history_real()
    test_fetch_history_async()
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
