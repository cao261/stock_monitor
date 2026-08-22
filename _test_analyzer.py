# -*- coding: utf-8 -*-
"""v4.6.3 analyzer.py 核心单测（10 个断言）。

覆盖三大核心交易算法：
- check_signals：止盈/止损/建仓机会/空仓强校验
- check_grid_signals：网格买入/卖出/空仓边界
- calculate_stock_ambush_levels：理想建仓区间
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'D:\Desktop\杂物\股市情绪\stock_monitor')
import analyzer


def test_1_take_profit_triggered():
    """场景：正常持仓 + 现价 >= target_win → is_take_profit=True"""
    print('== 1. check_signals 止盈触发 ==')
    current = {"price": 11.0, "open": 10.0, "prev_close": 10.0, "change_pct": 10.0, "volume": 1000}
    history = {"avg_volume_5d": 0, "data": []}
    sig = analyzer.check_signals("sh600000", current, history, target_win=10.5, position=100)
    assert sig["signals"]["is_take_profit"] is True, f"应触发止盈, got {sig['signals']}"
    assert sig["signals"]["is_stop_loss"] is False
    assert sig["signals"]["is_entry_opportunity"] is False
    assert "止盈线 10.50" in (sig.get("trade_message") or ""), f"文案应含止盈价, got {sig.get('trade_message')}"
    print('  正常持仓 + 现价>=target_win → is_take_profit=True [OK]')


def test_2_stop_loss_triggered():
    """场景：正常持仓 + 现价 <= target_loss → is_stop_loss=True"""
    print('== 2. check_signals 止损触发 ==')
    current = {"price": 9.0, "open": 10.0, "prev_close": 10.0, "change_pct": -10.0, "volume": 1000}
    sig = analyzer.check_signals("sh600001", current, {"avg_volume_5d": 0, "data": []},
                                target_loss=9.5, position=100)
    assert sig["signals"]["is_stop_loss"] is True
    assert sig["signals"]["is_take_profit"] is False
    assert "止损线 9.50" in (sig.get("trade_message") or "")
    print('  正常持仓 + 现价<=target_loss → is_stop_loss=True [OK]')


def test_3_empty_position_no_take_profit():
    """v3.1: 空仓(position=0)即使现价>=target_win 也不应触发止盈（幽灵告警修复）"""
    print('== 3. check_signals 空仓强校验 position=0 ==')
    current = {"price": 12.0, "open": 10.0, "prev_close": 10.0, "change_pct": 20.0, "volume": 1000}
    sig = analyzer.check_signals("sh600002", current, {"avg_volume_5d": 0, "data": []},
                                target_win=10.0, target_loss=9.0, position=0)
    assert sig["signals"]["is_take_profit"] is False, "空仓 position=0 不应触发止盈"
    assert sig["signals"]["is_stop_loss"] is False, "空仓 position=0 不应触发止损"
    assert sig.get("trade_message") is None, "空仓时 trade_message 应为 None"
    print('  position=0 即使触发条件满足也不报止盈止损 [OK]')


def test_4_empty_position_no_stop_loss():
    """v3.1: 空仓(position=None)同样强校验"""
    print('== 4. check_signals 空仓强校验 position=None ==')
    current = {"price": 8.0, "open": 10.0, "prev_close": 10.0, "change_pct": -20.0, "volume": 1000}
    sig = analyzer.check_signals("sh600003", current, {"avg_volume_5d": 0, "data": []},
                                target_loss=9.0, position=None)
    assert sig["signals"]["is_stop_loss"] is False
    assert sig["signals"]["is_take_profit"] is False
    assert sig.get("trade_message") is None
    print('  position=None 即使触发条件满足也不报止盈止损 [OK]')


def test_5_entry_opportunity_in_range():
    """v4.0: 空仓 + 现价落入 entry_price 区间 → is_entry_opportunity=True"""
    print('== 5. check_signals 建仓机会（理想区间内）==')
    current = {"price": 1.50, "open": 1.45, "prev_close": 1.45, "change_pct": 3.4, "volume": 1000}
    sig = analyzer.check_signals("sh589130", current, {"avg_volume_5d": 0, "data": []},
                                target_loss=1.36, position=0,
                                entry_price_min=1.49, entry_price_max=1.51)
    assert sig["signals"]["is_entry_opportunity"] is True, f"应在建仓区间内, got {sig['signals']}"
    assert "理想建仓区间" in (sig.get("trade_message") or "")
    print('  空仓 + 现价∈[1.49, 1.51] → is_entry_opportunity=True [OK]')


def test_6_grid_buy_triggered():
    """check_grid_signals: 现价跌破 grid_step_pct → 网格买入信号"""
    print('== 6. check_grid_signals 网格买入触发 ==')
    # 现价 9.5，基准 10.0，距离 -5% <= -grid_step_pct(-3%) → 买入
    sig = analyzer.check_grid_signals(price=9.5, last_grid_price=10.0, cost_price=10.0,
                                      grid_step_pct=3.0, position=100)
    assert sig["is_grid_buy"] is True
    assert sig["is_grid_sell"] is False
    assert sig["grid_distance"] == -5.0
    print('  现价跌破基准 3% → is_grid_buy=True [OK]')


def test_7_grid_sell_triggered():
    """check_grid_signals: 现价涨过 grid_step_pct → 网格卖出信号"""
    print('== 7. check_grid_signals 网格卖出触发 ==')
    sig = analyzer.check_grid_signals(price=10.5, last_grid_price=10.0, cost_price=10.0,
                                      grid_step_pct=3.0, position=100)
    assert sig["is_grid_sell"] is True
    assert sig["is_grid_buy"] is False
    assert sig["grid_distance"] == 5.0
    print('  现价涨过基准 3% → is_grid_sell=True [OK]')


def test_8_empty_position_buy_allowed_sell_forbidden():
    """v3.1: 空仓时允许买入（等跌建仓），禁止卖出（幽灵卖出修复）"""
    print('== 8. check_grid_signals 空仓边界 ==')
    # 空仓 position=0 + 现价大涨（满足 sell 条件）+ 现价大跌（满足 buy 条件）
    # 应该：buy=True, sell=False
    sig = analyzer.check_grid_signals(price=8.0, last_grid_price=10.0, cost_price=10.0,
                                      grid_step_pct=3.0, position=0)
    assert sig["is_grid_buy"] is True, "空仓时仍可触发网格买入"
    assert sig["is_grid_sell"] is False, "空仓时禁掉卖出信号"
    print('  空仓时买可卖禁 → is_grid_buy=True, is_grid_sell=False [OK]')


def test_9_grid_insufficient_inputs():
    """check_grid_signals: 缺关键参数返回 empty dict"""
    print('== 9. check_grid_signals 缺参保护 ==')
    # price=None
    sig1 = analyzer.check_grid_signals(price=None, last_grid_price=10, cost_price=10, grid_step_pct=3)
    assert sig1 == {"grid_reference_price": None, "grid_distance": None,
                    "is_grid_buy": False, "is_grid_sell": False}
    # grid_step_pct=None
    sig2 = analyzer.check_grid_signals(price=10, last_grid_price=10, cost_price=10, grid_step_pct=None)
    assert sig2["is_grid_buy"] is False and sig2["is_grid_sell"] is False
    # 基准价都为 None
    sig3 = analyzer.check_grid_signals(price=10, last_grid_price=None, cost_price=None, grid_step_pct=3)
    assert sig3["is_grid_buy"] is False
    print('  缺 price/grid_step_pct/基准价 → 全部空信号 [OK]')


def test_10_ambush_zone_in_range():
    """calculate_stock_ambush_levels: 理想建仓区间应包含支撑位 + 在支撑与现价之间"""
    print('== 10. calculate_stock_ambush_levels 理想建仓区间 ==')
    # 构造 25 天横盘 K 线（近 20 日在 10.0-10.5 之间）
    history = []
    base = 10.2
    for i in range(25):
        c = base + (i % 5) * 0.05  # 10.20 ~ 10.40 波动
        history.append({
            "date": f"2026-08-{i+1:02d}",
            "open": c, "close": c, "high": c * 1.01, "low": c * 0.99,
            "volume_lots": 10000,
        })
    ambush = analyzer.calculate_stock_ambush_levels("sh600010", cur_price=10.3, history_records=history)
    # 验证字段
    assert ambush["support_price"] is not None and ambush["support_price"] > 0
    assert ambush["resistance_price"] is not None and ambush["resistance_price"] > ambush["support_price"]
    assert len(ambush["ambush_zone"]) == 2
    zone_min, zone_max = ambush["ambush_zone"]
    assert zone_min < zone_max, f"埋伏区间下界<上界, got {zone_min}/{zone_max}"
    # 埋伏区间应围绕支撑位（zone_min 接近 support_price）
    assert zone_min <= ambush["support_price"] * 1.02, \
        f"埋伏区间下界应接近支撑位, got {zone_min} vs {ambush['support_price']}"
    # 止盈 > 现价 > 止损
    assert ambush["target_win"] > 10.3
    assert ambush["stop_loss"] < 10.3
    # 波动率标签
    assert "型" in ambush["volatility_tag"], f"波动类型标签缺失, got {ambush['volatility_tag']}"
    print('  支持/压力/埋伏区间/止盈止损/波动标签 全部齐 [OK]')


if __name__ == "__main__":
    test_1_take_profit_triggered()
    test_2_stop_loss_triggered()
    test_3_empty_position_no_take_profit()
    test_4_empty_position_no_stop_loss()
    test_5_entry_opportunity_in_range()
    test_6_grid_buy_triggered()
    test_7_grid_sell_triggered()
    test_8_empty_position_buy_allowed_sell_forbidden()
    test_9_grid_insufficient_inputs()
    test_10_ambush_zone_in_range()
    print('\n[OK] 全部 10 个 analyzer 单测通过')
