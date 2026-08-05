"""v1.2 端到端测：止盈止损字段 + 信号触发"""
import requests
import time
import sys

BASE = "http://127.0.0.1:8000/api"

# 等服务
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

# 清空旧
for w in requests.get(f"{BASE}/watchlist/quotes").json():
    requests.delete(f"{BASE}/watchlist/{w['id']}")

print()
print("=== 1) POST with all 5 portfolio fields ===")
r = requests.post(f"{BASE}/watchlist", json={
    "ts_code": "sh600000",
    "name": "浦发银行",
    "exchange": "SH",
    "cost_price": 10.0,
    "position": 1000,
    "target_win": 12.0,    # 止盈
    "target_loss": 9.0,    # 止损
    "trade_note": "突破回踩 + 缩量企稳"
})
print(f"  POST status: {r.status_code}")
created = r.json()
print(f"  cost={created['cost_price']}, pos={created['position']}, win={created['target_win']}, loss={created['target_loss']}, note={created['trade_note']!r}")
wid = created["id"]

print()
print("=== 2) GET /quotes 看新字段 ===")
r = requests.get(f"{BASE}/watchlist/quotes")
item = r.json()[0]
print(f"  cost: {item['cost_price']}, pos: {item['position']}")
print(f"  win: {item.get('target_win')}, loss: {item.get('target_loss')}")
print(f"  price: {item.get('price')}")
print(f"  pnl: {item.get('floating_pnl')}, rate: {item.get('return_rate')}%")
print(f"  note: {item.get('trade_note')!r}")

# 3) PATCH 只改 target_win（验证行内编辑）
print()
print("=== 3) PATCH 只改 target_win（行内编辑场景）===")
r = requests.patch(f"{BASE}/watchlist/{wid}", json={"target_win": 11.5})
print(f"  status: {r.status_code}, win: {r.json()['target_win']}")

# 4) /signals 检查信号结构
print()
print("=== 4) /signals 应该有 is_take_profit / is_stop_loss / trade_message 字段 ===")
r = requests.get(f"{BASE}/watchlist/signals?only_triggered=false")
sigs = r.json()
if sigs:
    s = sigs[0]
    print(f"  signals keys: {list(s.get('signals', {}).keys())}")
    print(f"  trade_message: {s.get('trade_message')!r}")
    print(f"  is_take_profit: {s['signals']['is_take_profit']}, is_stop_loss: {s['signals']['is_stop_loss']}")
else:
    print("  no signals returned (cache miss?)")

# 5) /signals only_triggered=true 应该过滤出触发的
print()
print("=== 5) only_triggered=true （如果有触发，应返回；否则空）===")
r = requests.get(f"{BASE}/watchlist/signals?only_triggered=true")
print(f"  status: {r.status_code}, count: {len(r.json())}")
if r.json():
    for s in r.json():
        print(f"    {s['ts_code']} {s.get('trade_message')!r}")

# 6) 422 校验：target_win = -1
print()
print("=== 6) 422: target_win = -1 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600036", "name": "招行", "exchange": "SH", "target_win": -1})
print(f"  status: {r.status_code}, body: {r.json()}")

# 7) 清理
for w in requests.get(f"{BASE}/watchlist/quotes").json():
    requests.delete(f"{BASE}/watchlist/{w['id']}")
print()
print("cleaned up")
