"""market_fetcher 的端到端冒烟测试。

覆盖：
  1. 代码归一化
  2. 新浪响应解析（含停牌容错）
  3. 切片逻辑
  4. akshare 拉全市场代码
  5. fetch_all_prices 实际并发抓一批
"""
from __future__ import annotations

import asyncio
import sys
import time

import market_fetcher as mf


def test_normalize() -> None:
    print("== test_normalize ==")
    cases = {
        "600000": "sh600000",
        "sh600000": "sh600000",
        "000001": "sz000001",
        "SZ000001": "sz000001",
        "688001": "sh688001",
        "300750": "sz300750",
        "832000": "bj832000",
        "920000": "bj920000",
    }
    for raw, expected in cases.items():
        got = mf._normalize_code(raw)
        assert got == expected, f"{raw} -> {got}, expected {expected}"
    print(f"  OK ({len(cases)} cases)")


def test_parse_sina_line() -> None:
    print("== test_parse_sina_line ==")

    # 正常个股
    line_ok = (
        'var hq_str_sh600000="浦发银行,10.50,10.40,10.55,10.60,10.38,'
        '10.54,10.55,12345678,130000000.00,12345678,130000000,'
        '10.54,10.55,500000,200000,500000,200000,2026-08-03,'
        '13:30:00,10.54,10.55,500000,200000,500000,200000,500000,200000,500000,'
        '2026-08-03,13:30:00,0,0";'
    )
    parsed = mf._parse_sina_line(line_ok)
    assert parsed is not None
    code, data = parsed
    assert code == "sh600000", code
    assert data["name"] == "浦发银行"
    assert data["open"] == 10.50
    assert data["prev_close"] == 10.40
    assert data["price"] == 10.55
    assert data["high"] == 10.60
    assert data["low"] == 10.38
    assert data["volume"] == 12345678
    assert data["change_pct"] == round((10.55 - 10.40) / 10.40 * 100, 3)
    print("  normal line OK -> change_pct=", data["change_pct"])

    # 停牌：price="--"
    line_suspend = (
        'var hq_str_sh600001="停牌股票,0.00,0.00,--,0.00,0.00,'
        '0.00,0.00,0,0.00,0,0,0,0,0,0,0,0,--,--,0,0,0,0,0,0,0,0,0,--,13:30:00,0,0";'
    )
    parsed2 = mf._parse_sina_line(line_suspend)
    # 停牌应该返回 None（让调用方跳过），不污染 cache
    assert parsed2 is None, f"停牌行应跳过，实际: {parsed2}"
    print("  suspended line skipped OK")

    # 字段数不够
    line_short = 'var hq_str_sh600002="x,1,2,3";'
    assert mf._parse_sina_line(line_short) is None
    print("  short line skipped OK")

    # 空行
    assert mf._parse_sina_line("") is None
    assert mf._parse_sina_line("not a sina line") is None
    print("  garbage lines skipped OK")


def test_parse_sina_response_multi() -> None:
    print("== test_parse_sina_response_multi ==")
    text = (
        'var hq_str_sh600000="浦发银行,10.50,10.40,10.55,10.60,10.38,'
        '10.54,10.55,100,2000,100,2000,10.54,10.55,0,0,0,0,2026-08-03,'
        '13:30:00,10.54,10.55,0,0,0,0,0,0,0,2026-08-03,13:30:00,0,0";\n'
        'var hq_str_sz000001="平安银行,12.00,11.90,12.10,12.20,11.85,'
        '12.05,12.10,200,3000,200,3000,12.05,12.10,0,0,0,0,2026-08-03,'
        '13:30:00,12.05,12.10,0,0,0,0,0,0,0,2026-08-03,13:30:00,0,0";\n'
    )
    out = mf._parse_sina_response(text)
    assert set(out.keys()) == {"sh600000", "sz000001"}, out.keys()
    assert out["sh600000"]["name"] == "浦发银行"
    assert out["sz000001"]["name"] == "平安银行"
    print(f"  OK ({len(out)} stocks parsed)")


def test_batching() -> None:
    print("== test_batching ==")
    codes = [f"sh{600000 + i:06d}" for i in range(5000)]
    batches = [codes[i:i + 800] for i in range(0, len(codes), 800)]
    assert len(batches) == 7, f"5000/800 应切 7 批，实际 {len(batches)}"
    assert all(len(b) == 800 for b in batches[:-1])
    # 5000 = 800*6 + 200，最后一批 200
    assert len(batches[-1]) == 200
    print(f"  5000 codes -> {len(batches)} batches: {[len(b) for b in batches]}")


async def test_akshare_codes() -> None:
    print("== test_akshare_codes (real network) ==")
    t0 = time.time()
    code_list = await asyncio.get_running_loop().run_in_executor(
        None, mf.fetch_all_codes_sync
    )
    dt = time.time() - t0
    assert len(code_list) > 4000, f"A 股应 >4000 只，实际 {len(code_list)}"
    sample = code_list[:3] + code_list[-3:]
    print(f"  fetched {len(code_list)} codes in {dt:.1f}s")
    for c in sample:
        print(f"    {c['code']}  {c['name']}")


async def test_real_sina_fetch() -> None:
    print("== test_real_sina_fetch (real network) ==")
    # 拿代码清单
    n = await mf.refresh_codes_daily()
    assert n > 0
    codes = mf.all_stocks_cache["__meta__"]["codes"]
    # 只取前 80 个试一下
    sample = codes[:80]
    t0 = time.time()
    updated = await mf.fetch_all_prices(sample, batch_size=80, concurrency=1)
    dt = time.time() - t0
    assert updated > 0, "至少要拿到一条数据"
    print(f"  fetched {updated} stocks in {dt:.1f}s")
    one = mf.get_stock(sample[0])
    print(f"  sample[{sample[0]}] = {one}")


async def main() -> int:
    test_normalize()
    test_parse_sina_line()
    test_parse_sina_response_multi()
    test_batching()
    await test_akshare_codes()
    await test_real_sina_fetch()
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
