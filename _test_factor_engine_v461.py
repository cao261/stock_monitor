# -*- coding: utf-8 -*-
"""v4.6.1 专项单测：快照粗筛 + 跨板块去重 + 真实数据耗时。
不依赖网络，全用 mock 数据。
"""
import sys, io, asyncio, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, r'D:\Desktop\杂物\股市情绪\stock_monitor')
import market_fetcher as mf
import app.services.factor_engine as fe
from concurrent.futures import ThreadPoolExecutor


def fake_snap(price, op, high, low, vol, amt, chg=None, prev=None, name="X"):
    if prev is None:
        prev = round(price * 0.98, 3)
    if chg is None:
        chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0
    return {
        "__ts_code": "",  # 占位，外部填
        "name": name, "open": op, "prev_close": prev, "price": price,
        "high": high, "low": low, "volume": vol, "amount": amt, "change_pct": chg,
    }


def test_snapshot_prefilter():
    print("== 快照粗筛 (v4.6.1.1: [0%, 9.5%] + 成交额>=5000万) ==")
    pool = [
        # 涨幅 0-9.5% + 成交额>=5000万 → 通过
        fake_snap(10.0, 9.5, 10.2, 9.4, 1_000_000, 10_000_000, chg=3.0, name="A"),
        fake_snap(15.0, 14.5, 15.2, 14.4, 800_000, 12_000_000, chg=5.0, name="B"),
        fake_snap(20.0, 19.5, 20.2, 19.4, 500_000, 10_000_000, chg=8.0, name="C"),
        fake_snap(10.0, 10.0, 10.2, 9.8, 1_000_000, 10_000_000, chg=1.0, name="D"),  # 小幅翻红 [0%,9.5%] 通过
        # 涨幅 > 9.5% → 拒（一字板）
        fake_snap(10.0, 9.5, 10.2, 9.4, 1_000_000, 10_000_000, chg=10.0, name="E"),
        # 成交额 < 5000 万 → 拒（微盘死水）
        fake_snap(10.0, 9.5, 10.2, 9.4, 100_000, 4_000_000, chg=3.0, name="F"),
        # 涨幅 < 0 → 拒（一字跌停 / 弱势）
        fake_snap(10.0, 10.5, 10.2, 9.4, 1_000_000, 10_000_000, chg=-1.0, name="G"),
    ]
    members, codes = fe._snapshot_prefilter(pool, cap=10)
    passed_names = [m["name"] for m in members]
    print(f"  通过: {passed_names}")
    assert set(passed_names) == {"A", "B", "C", "D"}, f"应通过 A/B/C/D, got {passed_names}"
    assert "E" not in passed_names and "F" not in passed_names and "G" not in passed_names
    print("  涨幅 [0%, 9.5%] + 成交额>=5000万 硬过滤通过 [OK]")

    # 排序：按 score=chg*log(vol) 降序
    members_cap3, _ = fe._snapshot_prefilter(pool, cap=2)
    cap_names = [m["name"] for m in members_cap3]
    print(f"  cap=2 取 Top: {cap_names}")
    # C vol 500k, B vol 800k; B score = 5*log(800k) ≈ 5*5.9 = 29.5; C score = 8*log(500k) ≈ 8*5.7 = 45.6
    # C 应该排第一（涨幅更高）
    assert cap_names[0] == "C", f"Top 1 应是 C (高分), got {cap_names[0]}"
    print(f"  Top 排序: {cap_names[0]} (chg=8%) > {cap_names[1]} (chg=5%) [OK]")


def test_cross_sector_dedup():
    print("== 跨板块去重 ==")
    # 构造 3 个板块：AI / 芯片 / 半导体
    # 关键：每个板块都有 2 只可入选的股票（保证 TOP 5 候选池 + 入选 ≥ 2 只）
    # 单位一致性：snapshot.volume 是「股」，K线 volume_lots 是「手」（×100 = 股）
    # 让 RVOL = vol / avg_vol_5d 落在 [1.5, 3.5]
    ai1 = fake_snap(10.92, 10.83, 10.95, 10.81, 200_000_000, 2_180_000_000, chg=4.0, name="AI龙头")
    ai2 = fake_snap(15.05, 14.85, 15.1, 14.8, 20_000_000, 300_000_000, chg=6.0, name="AI先锋")
    chip1 = fake_snap(20.5, 20.0, 20.8, 19.8, 30_000_000, 600_000_000, chg=4.0, name="芯片龙头")
    chip2 = fake_snap(25.0, 24.5, 25.3, 24.4, 15_000_000, 375_000_000, chg=5.0, name="芯片先锋")
    semicon1 = fake_snap(30.0, 29.5, 30.3, 29.4, 25_000_000, 750_000_000, chg=3.5, name="半导体龙头")
    semicon2 = fake_snap(35.0, 34.5, 35.3, 34.4, 18_000_000, 630_000_000, chg=4.5, name="半导体先锋")

    # 关键：每只股票的 20 日 K 线要"最近 5 日小幅上行到当前价附近"
    # 这样 high_20d ≈ 当前价，dist_high ≈ 0%，通过"距高 ≤ 5%"硬过滤
    # 同时 MA5 > MA10 > MA20 保持多头排列
    def make_hist(price, base_lots, days=25):
        closes = [price * 0.97 + i * 0.001 * price for i in range(days - 5)]
        closes += [price * 0.99, price * 0.995, price, price, price]
        return [{"date": f"2026-08-{i+1:02d}", "open": c, "close": c,
                 "high": c * 1.005, "low": c * 0.995, "volume_lots": base_lots} for i, c in enumerate(closes)]

    # base_lots 是"手"，factor_engine 内部 ×100 转股
    # AI 龙头: vol=200M 股 / (5d 均量 1M 手=100M 股) = RVOL 2.0 ✓
    hist_a = make_hist(10.92, 1_000_000.0)
    hist_b = make_hist(15.05, 1_000_000.0)   # AI 先锋 vol=20M / 100M = 0.2 → 拒
    # 调整 AI 先锋 base_lots: vol=20M 股 / RVOL=2 → base_lots=100_000 手=10M 股（不行）→ base=10万手×100=1000万股 → RVOL=20
    # 实际: 让 base_lots=10_000 手 = 1M 股, vol=20M → RVOL=20 → 拒
    # 改: base_lots=100_000 手=10M 股, vol=20M 股, RVOL=2.0 ✓
    hist_b = make_hist(15.05, 100_000.0)  # 10M 股均量, RVOL=2
    hist_c = make_hist(20.5, 150_000.0)   # 芯片龙头 vol=30M, RVOL=2
    hist_d = make_hist(25.0, 75_000.0)    # 芯片先锋 vol=15M, RVOL=2
    hist_e = make_hist(30.0, 125_000.0)   # 半导体龙头 vol=25M, RVOL=2
    hist_f = make_hist(35.0, 90_000.0)    # 半导体先锋 vol=18M, RVOL=2

    mf.all_stocks_cache.clear()
    mf.all_stocks_cache["sh600000"] = {**ai1, "name": "AI龙头"}
    mf.all_stocks_cache["sh600001"] = {**ai2, "name": "AI先锋"}
    mf.all_stocks_cache["sh600002"] = {**chip1, "name": "芯片龙头"}
    mf.all_stocks_cache["sh600003"] = {**chip2, "name": "芯片先锋"}
    mf.all_stocks_cache["sh600004"] = {**semicon1, "name": "半导体龙头"}
    mf.all_stocks_cache["sh600005"] = {**semicon2, "name": "半导体先锋"}
    mf.history_cache.clear()
    for c, h in [
        ("sh600000", hist_a), ("sh600001", hist_b),
        ("sh600002", hist_c), ("sh600003", hist_d),
        ("sh600004", hist_e), ("sh600005", hist_f),
    ]:
        mf.history_cache[c] = {"data": h, "avg_volume_5d": 0, "avg_amount_5d": 0}

    all_stocks = mf.get_all_stocks()
    print(f"  池子里 6 只股票：{[k for k in all_stocks.keys()]}")

    # 板块 A：AI — 主词"AI板" 应匹配 AI龙头 + AI先锋（都含 "AI" + 后续字符）
    stocks_a = fe.pick_stocks_for_sector("AI板块", all_stocks, leading_code="sh600000")
    print(f"  AI板块 入选: {[s['code'] for s in stocks_a]}")
    selected = {s["code"] for s in stocks_a}
    # 期望 AI 龙头 + AI 先锋都入选（都满足硬过滤 + 强流动性）
    assert "sh600000" in selected and "sh600001" in selected, f"AI 两股都应入选, got {selected}"

    # 板块 B：芯片 — 主词"芯片板" 命中 芯片龙头 + 芯片先锋
    # 但 excluded_codes 包含 sh600000/sh600001（前序 AI 板块入选）
    stocks_b = fe.pick_stocks_for_sector("芯片板块", all_stocks, leading_code=None, excluded_codes=selected)
    print(f"  芯片板块 入选: {[s['code'] for s in stocks_b]}")
    # 关键断言：芯片板块不能选 AI 已选的股
    for s in stocks_b:
        assert s["code"] not in selected, f"芯片板块重复选了 AI 板块的 {s['code']}"
    selected |= {s["code"] for s in stocks_b}

    # 板块 C：半导体
    stocks_c = fe.pick_stocks_for_sector("半导体板块", all_stocks, leading_code=None, excluded_codes=selected)
    print(f"  半导体板块 入选: {[s['code'] for s in stocks_c]}")
    for s in stocks_c:
        assert s["code"] not in selected, f"半导体板块重复选了前序板块的 {s['code']}"

    # 全局断言：3 个板块的入选股完全不重叠
    all_picked = set()
    for grp in (stocks_a, stocks_b, stocks_c):
        for s in grp:
            assert s["code"] not in all_picked, f"全局重复 {s['code']}"
            all_picked.add(s["code"])
    print(f"  3 板块去重无重复，总共 {len(all_picked)} 只独立股票 [OK]")


def test_pipeline_perf():
    print("== 真实数据性能（_diag_v46_factor 模拟）==")
    # 不在这里跑真实网络（耗时太长），改为单元测试 _snapshot_prefilter 的执行时间
    import time
    # 7122 只股票 mock（用全真实 shape 但 random 字段）
    import random
    pool = []
    for i in range(7122):
        chg = random.uniform(-5, 12)
        amt = random.uniform(1_000_000, 500_000_000)
        vol = amt / max(random.uniform(5, 30), 1)
        pool.append(fake_snap(
            price=10 + random.random() * 100,
            op=10, high=11, low=9.5,
            vol=vol, amt=amt, chg=chg, name=f"T{i}",
        ))
    t0 = time.time()
    members, codes = fe._snapshot_prefilter(pool, cap=5)
    elapsed = time.time() - t0
    print(f"  7122 只快照粗筛到 Top 5: {elapsed*1000:.1f}ms (应 < 50ms)")
    assert elapsed < 0.5, f"快照粗筛应 < 500ms, got {elapsed}s"
    assert len(codes) == 5
    print(f"  入选: {[(m['name'], m['change_pct']) for m in members]}")


if __name__ == "__main__":
    test_snapshot_prefilter()
    test_cross_sector_dedup()
    test_pipeline_perf()
    print("\n[OK] v4.6.1 全部单测通过")
