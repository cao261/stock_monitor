"""v1.3 测试：6 位纯数字自动归一化 + exchange 交叉校验"""
import requests
import sys

BASE = "http://127.0.0.1:8000/api"

# 等服务
for i in range(20):
    try:
        r = requests.get(f"{BASE}/health", timeout=1)
        if r.status_code == 200:
            print(f"server up in {i}s")
            break
    except Exception:
        pass
    import time; time.sleep(0.5)
else:
    print("server didn't start"); sys.exit(1)

# 清空旧
for w in requests.get(f"{BASE}/watchlist").json():
    requests.delete(f"{BASE}/watchlist/{w['id']}")

print()
print("=== 1) 6 位纯数字 → 自动归一化 (589130 科创 ETF → sh589130) ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "589130", "name": "科创芯片ETF易方达"})
print(f"  status: {r.status_code}")
d = r.json()
print(f"  ts_code 归一化: {d['ts_code']}  (exchange={d['exchange']})")
assert d["ts_code"] == "sh589130", f"应该归一化为 sh589130，实际 {d['ts_code']}"
requests.delete(f"{BASE}/watchlist/{d['id']}")

print()
print("=== 2) 6 位纯数字 → sz000001 (深市主板) ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "000001"})
d = r.json()
print(f"  000001 → {d['ts_code']} (exchange={d['exchange']})")
assert d["ts_code"] == "sz000001"
requests.delete(f"{BASE}/watchlist/{d['id']}")

print()
print("=== 3) 6 位纯数字 → bj920001 (北交所) ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "920001"})
d = r.json()
print(f"  920001 → {d['ts_code']} (exchange={d['exchange']})")
assert d["ts_code"] == "bj920001"
requests.delete(f"{BASE}/watchlist/{d['id']}")

print()
print("=== 4) 已有前缀 + exchange 错配 → 422 (sh + BJ) ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh589130", "exchange": "BJ"})
print(f"  status: {r.status_code}, detail: {r.json()['detail'][0]['msg']}")
assert r.status_code == 422, "交叉校验应该 422"

print()
print("=== 5) 已有前缀 + exchange 不传 → OK ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh510300"})
d = r.json()
print(f"  status: {r.status_code}, ts_code={d['ts_code']}, exchange={d['exchange']}")
assert r.status_code == 201
assert d["ts_code"] == "sh510300"
requests.delete(f"{BASE}/watchlist/{d['id']}")

print()
print("=== 6) 非法代码（7 位）→ 422 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "1234567"})
print(f"  status: {r.status_code}, detail: {r.json()['detail'][0]['msg']}")
assert r.status_code == 422

print()
print("=== 7) 非法代码（带 x）→ 422 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600xx0"})
print(f"  status: {r.status_code}, detail: {r.json()['detail'][0]['msg']}")
assert r.status_code == 422

print()
print("=== 8) 带前缀正确配对 → 201 ===")
r = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600000", "name": "浦发", "exchange": "SH"})
d = r.json()
print(f"  status: {r.status_code}, ts_code={d['ts_code']}, exchange={d['exchange']}")
assert r.status_code == 201
requests.delete(f"{BASE}/watchlist/{d['id']}")

print()
print("=== 9) 重复添加 → 409 ===")
r1 = requests.post(f"{BASE}/watchlist", json={"ts_code": "sh600000"})
r2 = requests.post(f"{BASE}/watchlist", json={"ts_code": "600000"})  # 归一化后和 sh600000 一样
print(f"  第一次: {r1.status_code}, 第二次: {r2.status_code}")
assert r1.status_code == 201
assert r2.status_code == 409, "归一化后重复 → 409"
requests.delete(f"{BASE}/watchlist/{r1.json()['id']}")

print()
print("ALL 9 PASSED")
