"""端到端测：watchlist 持仓功能。"""
import requests
import time
import sys

BASE = "http://127.0.0.1:8000/api"

# 等待服务起来
for i in range(30):
    try:
        r = requests.get(f"{BASE}/health", timeout=1)
        if r.status_code == 200:
            print(f"server up in {i}s")
            break
    except Exception:
        time.sleep(1)
else:
    print("server didn't start")
    sys.exit(1)

print()
print("=== 1) 现有 watchlist 列表（看是不是有 5xxx ETF 残留，没有就新加） ===")
r = requests.get(f"{BASE}/watchlist/quotes")
print(f"  status: {r.status_code}, count: {len(r.json())}")
for w in r.json()[:3]:
    print(f"    {w['ts_code']}  cost={w.get('cost_price')}, pos={w.get('position')}, pnl={w.get('floating_pnl')}, rate={w.get('return_rate')}")

# 删掉旧的来一个干净测试（用 GET 看有没有，先 GET 出来 id 再 DELETE）
ids = [w["id"] for w in r.json()]
for i in ids:
    requests.delete(f"{BASE}/watchlist/{i}")
print(f"  cleaned {len(ids)} existing watchlist items")

print()
print("=== 2) POST with cost/position/note ===")
r = requests.post(f"{BASE}/watchlist", json={
    "ts_code": "sh600000",
    "name": "浦发银行",
    "exchange": "SH",
    "cost_price": 10.50,
    "position": 1000,
    "trade_note": "突破前高 + 缩量回踩 10 日线",
})
print(f"  POST status: {r.status_code}")
created = r.json()
print(f"  id: {created['id']}, cost={created['cost_price']}, pos={created['position']}, note={created['trade_note']!r}")
wid = created["id"]

print()
print("=== 3) GET /quotes 看派生字段 ===")
r = requests.get(f"{BASE}/watchlist/quotes")
item = r.json()[0]
print(f"  ts_code: {item['ts_code']}")
print(f"  cost: {item['cost_price']}, pos: {item['position']}")
print(f"  price: {item.get('price')} (current market price from sina)")
print(f"  pnl: {item.get('floating_pnl')}, rate: {item.get('return_rate')}%")
# 验证: 假设现价 X, pnl = (X - 10.5) * 1000
if item.get("price") and item.get("floating_pnl") is not None:
    expected = round((item["price"] - 10.5) * 1000, 2)
    expected_rate = round((item["price"] - 10.5) / 10.5 * 100, 2)
    ok_pnl = abs(item["floating_pnl"] - expected) < 0.01
    ok_rate = abs(item["return_rate"] - expected_rate) < 0.01
    print(f"  expected pnl={expected}, rate={expected_rate}%")
    print(f"  pnl correct: {ok_pnl}, rate correct: {ok_rate}")
else:
    print("  (price not available right now, can't verify pnl)")

print()
print("=== 4) PATCH partial: only cost_price (inline edit scenario) ===")
r = requests.patch(f"{BASE}/watchlist/{wid}", json={"cost_price": 11.20})
print(f"  PATCH status: {r.status_code}, new cost: {r.json()['cost_price']}")

print()
print("=== 5) PATCH clear: send null cost_price (用户清空持仓) ===")
r = requests.patch(f"{BASE}/watchlist/{wid}", json={"cost_price": None, "position": None, "trade_note": None})
print(f"  PATCH status: {r.status_code}, cost={r.json()['cost_price']}, pos={r.json()['position']}, note={r.json()['trade_note']!r}")

print()
print("=== 6) PATCH on non-existent ID -> 404 ===")
r = requests.patch(f"{BASE}/watchlist/99999", json={"cost_price": 10.0})
print(f"  status: {r.status_code}, body: {r.json()}")

print()
print("=== 7) add a second one without cost/pos (just observe) ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sz000001", "name": "平安银行", "exchange": "SZ"})
print(f"  POST status: {r.status_code}, cost={r.json()['cost_price']}, pos={r.json()['position']}")

print()
print("=== 8) /quotes mixed: 1 with position, 1 without ===")
r = requests.get(f"{BASE}/watchlist/quotes")
for w in r.json():
    has_pos = w.get("cost_price") is not None and w.get("position") is not None
    print(f"  {w['ts_code']}: has_pos={has_pos}, pnl={w.get('floating_pnl')}, rate={w.get('return_rate')}")

print()
print("=== 9) 验证 422: cost_price = -1 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600036", "name": "招商银行", "exchange": "SH", "cost_price": -1})
print(f"  status: {r.status_code}, body: {r.json()}")

print()
print("=== 10) 清理 ===")
for w in requests.get(f"{BASE}/watchlist/quotes").json():
    requests.delete(f"{BASE}/watchlist/{w['id']}")
print("  cleaned up")

print()
print("=== 11) 重启模拟：再 POST 一次，确认迁移幂等 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600519", "name": "茅台", "exchange": "SH", "cost_price": 1700.0, "position": 100, "trade_note": "长线"})
print(f"  status: {r.status_code}, body: {r.json()}")
# 清理
for w in requests.get(f"{BASE}/watchlist/quotes").json():
    requests.delete(f"{BASE}/watchlist/{w['id']}")
print("  cleaned up")
