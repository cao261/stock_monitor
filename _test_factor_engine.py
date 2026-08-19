# -*- coding: utf-8 -*-
"""v4.6 factor_engine 单元测试（纯函数，不依赖网络/AKShare）。
- 用 monkeypatch 把 mf.all_stocks_cache / mf.history_cache 替换成手工数据
- 验证板块共振 4 条硬过滤、个股 5 条硬过滤、角色识别、完整 pipeline 输出
"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'D:\Desktop\杂物\股市情绪\stock_monitor')
import asyncio
import importlib
import types
import market_fetcher as mf
import app.services.factor_engine as fe


# ---------------------- fake data helpers ----------------------
def fake_snap(price, op, high, low, vol, amt, chg=None, prev=None):
    if prev is None:
        prev = round(price * 0.98, 3)
    if chg is None:
        chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0
    return {
        "name": f"T{price}", "open": op, "prev_close": prev, "price": price,
        "high": high, "low": low, "volume": vol, "amount": amt, "change_pct": chg,
    }


def fake_history(closes, base_vol=1_000_000, base_high=None, base_low=None):
    """给一个收盘序列，构造 25 日 K 线，所有日成交量 = base_vol，5 日均量就是 base_vol。"""
    recs = []
    for i, c in enumerate(closes):
        recs.append({
            "date": f"2026-07-{i+1:02d}",
            "open": c, "close": c,
            "high": c * 1.01, "low": c * 0.99,
            "volume_lots": base_vol / 100.0,
        })
    return recs


# ---------------------- monkeypatch ----------------------
def install_fake_market(members, codes, histories=None):
    """members/snapshots: {code: snap}; histories: {code: hist_records}（可选）"""
    fake_cache = {c: members[c] for c in codes}
    mf.all_stocks_cache.clear()
    mf.all_stocks_cache.update(fake_cache)
    mf.history_cache.clear()
    if histories:
        for c, h in histories.items():
            mf.history_cache[c] = {"data": h, "avg_volume_5d": 0, "avg_amount_5d": 0}


# ====================== Layer 1: 板块硬过滤 ======================
def test_layer1_sector_resonance():
    print("== Layer 1: 板块共振硬过滤 ==")

    # 案例 A：5 只，4 涨 1 跌，3 涨停 —— 应通过
    members = [
        fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=10.0),  # 涨停
        fake_snap(11, 10.5, 11.1, 10.4, 1e6, 1.1e7, chg=10.0),  # 涨停
        fake_snap(12, 11.0, 12.5, 11.0, 1e6, 1.4e7, chg=10.0),  # 涨停
        fake_snap(13, 12.5, 13.2, 12.4, 1e6, 1.3e7, chg=4.0),
        fake_snap(14, 14.1, 14.2, 13.8, 1e6, 1.4e7, chg=-1.0),
    ]
    r = fe.score_sector_resonance("强共振", members)
    assert r is not None, "A 应通过"
    assert r["limit_up_count"] == 3 and r["up_ratio"] == 80.0
    print("  A 强共振通过 [OK] up_ratio=80% limit_up=3 chg=+6.6%")

    # 案例 B：上涨率 60%（不达标）
    members_b = [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=2.0) for _ in range(6)]
    members_b += [fake_snap(10, 10.0, 10.1, 9.4, 1e6, 1e7, chg=-1.0) for _ in range(4)]
    r = fe.score_sector_resonance("弱共振", members_b)
    assert r is None, "B 应被 up_ratio<70% 拒绝"
    print("  B 上涨率 60% 被拒 [OK]")

    # 案例 C：涨 80% 但仅 1 涨停（不达标）
    members_c = (
        [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=10.0)]  # 1 涨停
        + [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=2.0) for _ in range(7)]
        + [fake_snap(10, 10.0, 10.1, 9.4, 1e6, 1e7, chg=-2.0) for _ in range(2)]
    )
    r = fe.score_sector_resonance("缺涨停", members_c)
    assert r is None, "C 应被 limit_up<2 拒绝"
    print("  C 仅 1 涨停被拒 [OK]")

    # 案例 D：涨 80% 但 0 涨停（用 8.5% 模拟）+ 平均涨幅 <1.5%
    members_d = (
        [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=8.5) for _ in range(2)]
        + [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=0.5) for _ in range(6)]
        + [fake_snap(10, 10.0, 10.1, 9.4, 1e6, 1e7, chg=-0.5) for _ in range(2)]
    )
    avg = sum(m['change_pct'] for m in members_d) / len(members_d)
    r = fe.score_sector_resonance("弱涨幅", members_d)
    assert r is None, f"D 应被 change<1.5% 拒绝 (avg={avg}%, limit_up={sum(1 for m in members_d if m['change_pct']>=9.5)})"
    print(f"  D 平均涨幅 {avg:.2f}% < 1.5% 被拒 [OK]")


# ====================== Layer 2: 个股多因子 ======================
def _baseline_closes(latest=10.9):
    """构造 25 日：5/10/20 多头排列、close 接近 20 日高点。"""
    closes = [latest * 0.91] * 20
    closes[0:5] = [latest * 0.91] * 5
    closes[5:10] = [latest * 0.93 + i * 0.001 for i in range(5)]
    closes[10:15] = [latest * 0.95 + i * 0.001 for i in range(5)]
    closes[15:20] = [latest * 0.97 + i * 0.001 for i in range(5)]
    closes[20:25] = [latest * 0.985 + i * 0.003 for i in range(5)]
    return closes


def test_layer2_filters():
    print("== Layer 2: 个股多因子硬过滤 ==")
    closes = _baseline_closes(latest=10.9)
    hist = fake_history(closes, base_vol=1_000_000)

    # 基准 stock：close=10.92, op=10.83, high=10.95, low=10.81, vol=2e6（RVOL=2）, amt=2.18e7
    # 设计: close 接近 high（上影线短），20 日最高 10.95，dist_high=-0.27%（接近高点）
    # amount/volume = 10.9（VWAP 替代），close>vwap 强势承接；RVOL=2 在 [1.5,3.5]
    base = fake_snap(10.92, 10.83, 10.95, 10.81, 2_000_000, 21_800_000)
    feats = fe._stock_features(base, hist)
    ok, why = fe._filter_one_stock(feats)
    print(f"  基准 stock feats: dist_high={feats['dist_high_20d_pct']}%, rvol={feats['rvol']}, turn={feats['turnover_proxy_pct']}%, vwap_dev={feats['vwap_dev_pct']}%, shadow={feats['upper_shadow_pct']}%")
    print(f"  基准 stock -> {ok} ({why})")
    assert ok, "基准 stock 应通过"

    # 拒绝路径 1：MA 排列不成立（构造 5/10/20 倒挂）
    bad_ma_closes = list(reversed(closes[:25]))
    f1 = fe._stock_features(base, fake_history(bad_ma_closes, base_vol=1_000_000))
    if f1:
        ok, why = fe._filter_one_stock(f1)
        assert not ok and "MA" in why, f"MA 倒挂应被拒, got {ok}/{why}"
        print(f"  路径 1 MA倒挂 -> 拒 ({why}) [OK]")

    # 拒绝路径 2：距 20 日高 > 5%（价格远低于历史最高）
    low_price = fake_snap(9.5, 9.4, 9.7, 9.3, 2_000_000, 19_000_000)
    f2 = fe._stock_features(low_price, hist)
    if f2:
        ok, why = fe._filter_one_stock(f2)
        assert not ok and "20日高" in why, f"距 20 日高应被拒, got {ok}/{why}"
        print(f"  路径 2 远离高点 -> 拒 ({why}) [OK]")

    # 拒绝路径 3：现价低于 VWAP（短上影，但 close 远低于 amt/vol）
    # vwap = 11.0（10000/2000000 * 1e8 ... 算错了，重新算）: amt=21e6, vol=2e6, vwap=10.5
    # 让 amt/vol 显著高于 close：op=11.0, close=10.5, vwap_proxy=10.5=close → 不行
    # 改: amt=24e6, vol=2e6, vwap=12.0, close=10.5 < 12.0 → 弱势承接
    weak_snap = fake_snap(10.5, 11.0, 11.1, 10.4, 2_000_000, 24_000_000)  # close 10.5, vwap=12, 长上影被先命中
    # 改：让 amt/vol ≈ close 但 close<vwap（小差距）→ 命中 VWAP 规则
    weak_snap = fake_snap(10.85, 10.7, 10.92, 10.65, 2_000_000, 21_900_000)  # amt/vol=10.95, close=10.85
    f3 = fe._stock_features(weak_snap, hist)
    if f3:
        ok, why = fe._filter_one_stock(f3)
        assert not ok, f"弱势承接应被拒, got {ok}/{why}"
        # 命中原因应是 VWAP/分时均价，不是别的
        assert "分时" in why or "VWAP" in why or "弱势" in why, f"应被 VWAP 拒, got {why}"
        print(f"  路径 3 弱势承接 -> 拒 ({why}) [OK]")

    # 拒绝路径 4：换手过高（RVOL 过大）
    hot_snap = fake_snap(10.9, 10.85, 10.95, 10.83, 8_000_000, 87_000_000)  # RVOL=8
    f4 = fe._stock_features(hot_snap, hist)
    if f4:
        ok, why = fe._filter_one_stock(f4)
        assert not ok and ("换手" in why or "RVOL" in why), f"过度活跃应被拒, got {ok}/{why}"
        print(f"  路径 4 过度活跃 -> 拒 ({why}) [OK]")

    # 拒绝路径 5：上影线 > 25%（但 VWAP 通过、MA 排列通过、距高在 ±5%）
    # 设计：close=10.95, op=10.9, high=11.2, low=10.6, amt/vol 接近 10.95
    # 影线 = (11.2 - max(10.9, 10.95))/0.6 = 0.25/0.6 = 41.7% > 25%
    # vwap = amt/vol = 22e6/2e6 = 11.0, close=10.95 < 11.0 → 又被 VWAP 拒
    # 改：amt/vol ≈ close
    long_shadow = fake_snap(10.95, 10.9, 11.2, 10.6, 2_000_000, 21_900_000)  # vwap=10.95=close
    f5 = fe._stock_features(long_shadow, hist)
    if f5:
        ok, why = fe._filter_one_stock(f5)
        assert not ok and "影线" in why, f"长上影应被拒, got {ok}/{why}"
        print(f"  路径 5 长上影 -> 拒 ({why}) [OK]")


def test_layer3_role():
    print("== Layer 3: 角色识别 ==")
    # 容量中军：amount > 10 亿
    feats_a = {"amount_yi": 15.0, "price": 50, "rvol": 1.5}
    assert fe._tag_role(feats_a) == "容量中军"
    print("  容量中军 [OK]")
    # 弹性先锋：价格 <= 50 + RVOL >= 2
    feats_b = {"amount_yi": 5.0, "price": 20, "rvol": 2.5}
    assert fe._tag_role(feats_b) == "弹性先锋"
    print("  弹性先锋 [OK]")
    # 一般
    feats_c = {"amount_yi": 1.0, "price": 80, "rvol": 1.2}
    assert fe._tag_role(feats_c) == "一般"
    print("  一般 [OK]")


def test_pipeline_end_to_end():
    print("== Pipeline 端到端 ==")
    closes_a = _baseline_closes(latest=10.9)
    hist_a = fake_history(closes_a, base_vol=100_000_000)  # 容量中军：base_vol=100M，vol=200M → RVOL=2
    closes_b = _baseline_closes(latest=15.0)
    hist_b = fake_history(closes_b, base_vol=1_500_000)

    # 板块 A：5 只成分股，2 通过（容量 + 弹性），2 拒（弱势/长上影），1 拒（数据不足）
    passing_a = [
        fake_snap(10.92, 10.83, 10.95, 10.81, 200_000_000, 2_180_000_000),  # 容量中军：amt=21.8亿, RVOL=2
        fake_snap(15.05, 14.85, 15.1, 14.8, 3_200_000, 48_000_000),         # 弹性先锋（vol 3.2M / avg 1.5M = RVOL 2.13）
    ]
    failing_a = [
        fake_snap(10.5, 10.7, 10.85, 10.4, 200_000_000, 2_100_000_000),    # 弱势承接（用大盘量级）
        fake_snap(10.85, 11.0, 11.5, 10.7, 200_000_000, 2_200_000_000),    # 长上影
        fake_snap(9.0, 9.0, 9.0, 9.0, 0, 0),                            # 数据不足
    ]
    members_a = passing_a + failing_a
    codes_a = [f"sh6{i:05d}" for i in range(5)]
    # 涨停股 + 高涨幅凑够板块共振
    extra_up = [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=10.0) for _ in range(3)]
    members_a = members_a + extra_up
    codes_a = codes_a + [f"sh6{i:05d}" for i in range(5, 8)]

    # 板块 B：5 只，但全部弱势，应被 Layer 1 拒（不构成板块共振）
    members_b = [fake_snap(10, 9.5, 10.1, 9.4, 1e6, 1e7, chg=-2.0) for _ in range(5)]
    codes_b = [f"sz0{i:05d}" for i in range(5)]

    members_by_sector = {"A": members_a, "B": members_b}
    codes_by_sector = {"A": codes_a, "B": codes_b}

    # 装入 fake market
    all_codes = codes_a + codes_b
    all_snaps = dict(zip(codes_a, members_a))
    all_snaps.update(dict(zip(codes_b, members_b)))
    # 给通过的 2 只 + failing_a 1 只装历史（pipeline 拒绝时也要算出 feats）
    hist_map = {
        codes_a[0]: hist_a, codes_a[1]: hist_b,
        codes_a[2]: hist_a, codes_a[3]: hist_a,
    }
    install_fake_market(all_snaps, all_codes, hist_map)

    out = asyncio.run(fe.run_multifactor_pipeline(members_by_sector, codes_by_sector))
    print("  pipeline meta:", out["meta"])
    assert len(out["top_sectors"]) == 1, f"应只过 1 个板块, got {len(out['top_sectors'])}"
    sector = out["top_sectors"][0]
    print(f"  板块: {sector['sector']} chg={sector['change_pct']}% 涨停={sector['limit_up_count']} 入选={len(sector['stocks'])}")
    for s in sector["stocks"]:
        print(f"    {s['code']} {s['name']} role={s['role']} price={s['price']} rvol={s['rvol']} dist_high={s['dist_high_20d_pct']}%")
    assert len(sector["stocks"]) == 2, "应入选 2 只"
    roles = {s["role"] for s in sector["stocks"]}
    assert "容量中军" in roles and "弹性先锋" in roles, f"应同时含两种角色, got {roles}"
    print("  端到端通过 [OK]")


if __name__ == "__main__":
    test_layer1_sector_resonance()
    test_layer2_filters()
    test_layer3_role()
    test_pipeline_end_to_end()
    print("\n[OK] 全部单测通过")
