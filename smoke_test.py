"""watchlist CRUD 端到端冒烟测试。"""
import sys

import requests

BASE = "http://127.0.0.1:8000"
HDR = {"Content-Type": "application/json"}


def main() -> int:
    # ===== 准备：清空旧数据 =====
    print("== cleanup: clear existing watchlist ==")
    r = requests.get(f"{BASE}/watchlist", timeout=5)
    for item in r.json():
        requests.delete(f"{BASE}/watchlist/{item['id']}", timeout=5)
    print(f"  cleared {len(r.json())} items")

    # ===== 1. POST 3 条 =====
    print()
    print("== 1. POST 3 watchlist items ==")
    test_codes = [
        {"ts_code": "sh600000", "name": "浦发银行", "exchange": "SH"},
        {"ts_code": "sh600036", "name": "招商银行", "exchange": "SH"},
        {"ts_code": "sz000001", "name": "平安银行", "exchange": "SZ"},
    ]
    for c in test_codes:
        r = requests.post(f"{BASE}/watchlist", headers=HDR, json=c, timeout=5)
        assert r.status_code == 201, r.text
        print(f"  +{c['ts_code']}  status={r.status_code}")

    # ===== 2. 重复 POST -> 409 =====
    print()
    print("== 2. POST duplicate (expect 409) ==")
    r = requests.post(f"{BASE}/watchlist", headers=HDR, json=test_codes[0], timeout=5)
    print(f"  status={r.status_code}  body={r.json()}")
    assert r.status_code == 409, r.text

    # ===== 3. 非法 ts_code -> 422 =====
    print()
    print("== 3. POST invalid ts_code (expect 422) ==")
    r = requests.post(f"{BASE}/watchlist", headers=HDR,
                      json={"ts_code": "xxxxxx", "name": "x", "exchange": "SH"}, timeout=5)
    print(f"  status={r.status_code}")
    assert r.status_code == 422, r.text

    # ===== 4. GET 列表 =====
    print()
    print("== 4. GET /watchlist ==")
    r = requests.get(f"{BASE}/watchlist", timeout=5)
    items = r.json()
    print(f"  count={len(items)}")
    assert len(items) == 3
    stock_id = items[0]["id"]

    # ===== 5. GET by id =====
    print()
    print("== 5. GET /watchlist/{id} ==")
    r = requests.get(f"{BASE}/watchlist/{stock_id}", timeout=5)
    print(f"  status={r.status_code}  body={r.json()}")
    assert r.status_code == 200
    assert r.json()["ts_code"] == "sh600000"

    # ===== 6. GET by code =====
    print()
    print("== 6. GET /watchlist/by-code/sh600000 ==")
    r = requests.get(f"{BASE}/watchlist/by-code/sh600000", timeout=5)
    print(f"  status={r.status_code}")
    assert r.status_code == 200
    assert r.json()["id"] == stock_id

    # ===== 7. PATCH =====
    print()
    print("== 7. PATCH /watchlist/{id} ==")
    r = requests.patch(
        f"{BASE}/watchlist/{stock_id}",
        headers=HDR,
        json={"industry": "银行/股份制"},
        timeout=5,
    )
    print(f"  status={r.status_code}  industry={r.json().get('industry')}")
    assert r.status_code == 200
    assert r.json()["industry"] == "银行/股份制"

    # ===== 8. /watchlist/quotes (需要 fetcher 缓存数据) =====
    print()
    print("== 8. GET /watchlist/quotes ==")
    r = requests.get(f"{BASE}/watchlist/quotes", timeout=10)
    quotes = r.json()
    print(f"  count={len(quotes)}")
    for q in quotes:
        in_cache = "OK" if q["in_cache"] else "no"
        print(f"    [{in_cache}] {q['ts_code']}  {q.get('name', '-')}  "
              f"price={q.get('price')}  chg={q.get('change_pct')}")
    assert r.status_code == 200
    assert len(quotes) == 3
    # 至少 1 只命中缓存（fetcher 5s 轮询）
    assert sum(1 for q in quotes if q["in_cache"]) >= 1

    # ===== 9. /watchlist/signals =====
    print()
    print("== 9. GET /watchlist/signals?only_triggered=true ==")
    r = requests.get(f"{BASE}/watchlist/signals?only_triggered=true", timeout=10)
    sigs = r.json()
    print(f"  triggered count: {len(sigs)}")

    # ===== 10. 清理 =====
    print()
    print("== 10. cleanup: delete all ==")
    r = requests.get(f"{BASE}/watchlist", timeout=5)
    for item in r.json():
        requests.delete(f"{BASE}/watchlist/{item['id']}", timeout=5)
    print("  cleaned up")

    print()
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
