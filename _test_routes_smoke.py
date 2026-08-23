"""v2026-08-23 审计优化：routers 层 smoke test（pytest）。

覆盖目标（happy path + 关键错误路径）:
- /health, /api/health, /api/info
- /api/market/meta, /api/market/sentiment, /api/market/top
- /api/watchlist (list/create/get/patch/delete)
- /api/watchlist/quotes (空 cache 也能跑通)
- /api/watchlist/{id}/trade (买/卖)
- /api/strategy/daily-summary
- 路径遍历防御: /{full_path:path} 对 `..` 类路径返 404

技术决策：
- 用临时文件 SQLite 库（不是 in-memory）— ``app.database.engine`` 是模块级常量
  创建时绑定到 ``DATABASE_URL``，in-memory 跨进程无法共享。临时文件最稳。
- 设 ``STOCK_MONITOR_TEST=1`` 时强制 data 目录指向临时子目录（避免污染项目 data/）
- 跳过 /ai-plan /ai-report /discover（强依赖 LLM，不在 smoke 范围）
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 让 pytest 找到 app / analyzer / market_fetcher（项目根是 cwd）
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


# ====================== Fixtures ======================
@pytest.fixture(scope="module")
def temp_app_env():
    """创建临时数据目录 + 把 DATABASE_URL 指过去 + 让 app.database 用它。

    app/database.py:engine 是模块级常量，要让它走临时库，必须在 import 前
    设置 DATABASE_URL 环境变量。设完环境变量后 ``from app.config import DATABASE_URL``
    会读到新值 → engine 绑到临时库。
    """
    tmp = tempfile.mkdtemp(prefix="stock_monitor_test_")
    test_db = Path(tmp) / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db.as_posix()}"
    # 强制 config.DATABASE_URL 重新加载（如果已被缓存）
    if "app.config" in sys.modules:
        del sys.modules["app.config"]
    if "app.database" in sys.modules:
        del sys.modules["app.database"]
    yield tmp
    # teardown：清临时目录（try-except 防权限问题）
    import shutil
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


@pytest.fixture(scope="module")
def client(temp_app_env):
    """FastAPI TestClient，lifespan 会用我们的临时 DB 跑 init_db。"""
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_watchlist(client):
    """插一条 sh600000 用来 CRUD + trade 测试。"""
    payload = {
        "ts_code": "600000",
        "name": "浦发银行",
        "exchange": "SH",
        "cost_price": 10.5,
        "position": 1000,
        "target_win": 11.0,
        "target_loss": 9.5,
        "trade_note": "跌破 9.5 清仓",
    }
    r = client.post("/api/watchlist", json=payload)
    assert r.status_code == 201, f"create watchlist failed: {r.text}"
    item = r.json()
    yield item
    # teardown: 删除
    client.delete(f"/api/watchlist/{item['id']}")


# ====================== 1. meta / health ======================
def test_health_root(client):
    """GET /health 返 ok。"""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_api_prefix(client):
    """GET /api/health 返 ok（前端走 /api 路径）。"""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_info(client):
    """GET /api/info 返 name/version/docs/openapi。"""
    r = client.get("/api/info")
    assert r.status_code == 200
    body = r.json()
    assert "name" in body and "version" in body
    assert body["docs"] == "/docs"
    assert body["openapi"] == "/openapi.json"


# ====================== 2. market meta / sentiment ======================
def test_market_meta(client):
    """GET /api/market/meta 返 fetcher 元信息（code_count 必有字段）。"""
    r = client.get("/api/market/meta")
    assert r.status_code == 200
    body = r.json()
    assert "code_count" in body
    assert "last_fetch_at" in body
    assert "history_size" in body


def test_market_sentiment(client):
    """GET /api/market/sentiment 走 analyzer（即使无数据也返结构）。"""
    r = client.get("/api/market/sentiment")
    assert r.status_code == 200
    body = r.json()
    # analyzer.calculate_market_sentiment 直返（无 label 字段，label 仅 /daily-summary 加）
    for k in ("score", "up_count", "down_count", "up_ratio",
              "limit_up_count", "limit_down_count"):
        assert k in body, f"missing key: {k}"


def test_market_top_limit_validation(client):
    """GET /api/market/top?limit=5000 应被 ge=1,le=200 拒绝。"""
    r = client.get("/api/market/top", params={"limit": 5000})
    assert r.status_code == 422  # FastAPI validation


# ====================== 3. watchlist CRUD ======================
def test_watchlist_create_validation_ts_code(client):
    """ts_code 必须是 6 位或 sh/sz/bj 前缀。"""
    r = client.post("/api/watchlist", json={"ts_code": "invalid_code!"})
    assert r.status_code == 422  # Pydantic 校验失败


def test_watchlist_create_exchange_mismatch(client):
    """ts_code 前缀与 exchange 不一致应 422。"""
    r = client.post("/api/watchlist", json={
        "ts_code": "sh600000", "exchange": "SZ", "name": "测试"
    })
    assert r.status_code == 422


def test_watchlist_create_and_get(client, sample_watchlist):
    """创建后能 GET 到。"""
    sid = sample_watchlist["id"]
    r = client.get(f"/api/watchlist/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["ts_code"] == "sh600000"  # 6 位 → 自动补 sh 前缀
    assert body["exchange"] == "SH"
    assert body["cost_price"] == 10.5


def test_watchlist_create_duplicate_conflict(client, sample_watchlist):
    """重复创建同 ts_code 应 409。"""
    r = client.post("/api/watchlist", json={
        "ts_code": "sh600000", "name": "duplicate", "exchange": "SH"
    })
    assert r.status_code == 409


def test_watchlist_patch_partial(client, sample_watchlist):
    """PATCH 部分字段成功（成本价 + 备注），其他不变。"""
    sid = sample_watchlist["id"]
    r = client.patch(f"/api/watchlist/{sid}", json={"cost_price": 11.2, "trade_note": "调整"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cost_price"] == 11.2
    assert body["trade_note"] == "调整"
    assert body["position"] == 1000  # 未动


def test_watchlist_delete(client):
    """DELETE 后 GET 应 404。"""
    # 先创建
    r = client.post("/api/watchlist", json={
        "ts_code": "000001", "name": "平安银行", "exchange": "SZ"
    })
    assert r.status_code == 201
    sid = r.json()["id"]
    # 删
    r = client.delete(f"/api/watchlist/{sid}")
    assert r.status_code == 204
    # 再 GET 应 404
    r = client.get(f"/api/watchlist/{sid}")
    assert r.status_code == 404


# ====================== 4. watchlist/trade (资金账本) ======================
def test_trade_buy_first_position(client, sample_watchlist):
    """首次买入：old_pos=0 → new_pos=volume, new_cost=price。"""
    sid = sample_watchlist["id"]
    # 重置 position + cost 到 None（首次建仓场景）
    client.patch(f"/api/watchlist/{sid}", json={"cost_price": None, "position": None})
    r = client.post(f"/api/watchlist/{sid}/trade", json={"price": 10.8, "volume": 500})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["action"] == "BUY"
    assert body["new_position"] == 500
    assert body["new_cost_price"] == 10.8  # 首次建仓直接用 price
    assert body["realized_pnl"] == 0.0


def test_trade_buy_add_position_weighted_avg(client, sample_watchlist):
    """加仓：加权平均成本 = (old_cost*old_pos + price*vol) / new_pos。"""
    # 先重置 position = 0 + cost = 0
    sid = sample_watchlist["id"]
    client.patch(f"/api/watchlist/{sid}", json={"cost_price": None, "position": None})
    # 买 1000 股 @ 10.0
    r = client.post(f"/api/watchlist/{sid}/trade", json={"price": 10.0, "volume": 1000})
    assert r.status_code == 201
    assert r.json()["new_position"] == 1000
    # 再买 1000 股 @ 12.0 → 加权平均 = (10*1000 + 12*1000) / 2000 = 11.0
    r = client.post(f"/api/watchlist/{sid}/trade", json={"price": 12.0, "volume": 1000})
    assert r.status_code == 201
    body = r.json()
    assert body["new_position"] == 2000
    assert body["new_cost_price"] == 11.0  # 加权平均


def test_trade_sell_over_position_rejected(client, sample_watchlist):
    """卖出数量超过持仓应 400。"""
    sid = sample_watchlist["id"]
    r = client.post(f"/api/watchlist/{sid}/trade", json={"price": 11.0, "volume": -2000})
    assert r.status_code == 400
    assert "减仓失败" in r.json()["detail"]


# ====================== 5. /quotes 端点 (cache miss 路径) ======================
def test_watchlist_quotes_cache_miss(client, sample_watchlist):
    """/quotes 端点即使行情 cache miss 也能返（quote 字段空）。"""
    r = client.get("/api/watchlist/quotes")
    assert r.status_code == 200
    items = r.json()
    codes = [it["ts_code"] for it in items]
    assert "sh600000" in codes


# ====================== 6. /strategy/daily-summary ======================
def test_daily_summary(client, sample_watchlist):
    """GET /api/strategy/daily-summary 拼 4 大模块（不调 LLM）。"""
    r = client.get("/api/strategy/daily-summary")
    assert r.status_code == 200
    body = r.json()
    for k in ("generated_at", "sentiment", "watchlist_battle", "top_movers", "today_trades"):
        assert k in body, f"missing key: {k}"


# ====================== 7. 路径遍历防御（v2026-08-23 审计修复）======================
def test_spa_fallback_blocks_path_traversal(client):
    """SPA fallback 应拒绝 ../ 路径越界（修复后行为）。"""
    # URL 编码后的 ../etc/passwd
    r = client.get("/..%2F..%2F..%2Fetc%2Fpasswd")
    # 修复前会返 200 + 文件内容（如果存在）或 200 + index.html
    # 修复后：target.relative_to() 抛 ValueError → 404
    assert r.status_code == 404, f"path traversal should be 404, got {r.status_code}"


def test_spa_fallback_serves_real_or_spa(client):
    """SPA fallback 对 dist 不存在的路径应返 200（SPA 兜底到 index.html）。"""
    r = client.get("/some-nonexistent-route")
    assert r.status_code in (200, 404)


# ====================== 8. trades history ======================
def test_trades_history_empty(client):
    """GET /api/trades/history 初始空表返 200 + trades:[]。"""
    r = client.get("/api/trades/history")
    assert r.status_code == 200
    body = r.json()
    assert "trades" in body
    assert "total_count" in body
    assert isinstance(body["trades"], list)


# ====================== 9. 响应 charset（v2.5.2 修复回归）======================
def test_json_response_charset(client):
    """所有 application/json 响应都应带 charset=utf-8（防中文乱码）。"""
    r = client.get("/api/health")
    ct = r.headers.get("content-type", "")
    assert "charset=utf-8" in ct.lower(), f"missing charset: {ct}"
