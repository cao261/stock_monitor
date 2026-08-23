"""FastAPI 应用入口。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import API_DESCRIPTION, API_TITLE, API_VERSION
from app.database import SessionLocal, init_db
from app.models import Watchlist
from app.routers import market_router, watchlist_router, strategy_router, trade_router
import market_fetcher as mf
import news_fetcher  # v4.1 7x24 财经快讯

# 应用启动时统一配置 logging；屏蔽 akshare 进度条噪音
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("akshare").setLevel(logging.WARNING)
logging.getLogger("tqdm").setLevel(logging.WARNING)


def _read_watchlist_codes() -> list[str]:
    """从 DB 读自选股代码清单（在 fetcher 协程里用，避免 market_fetcher 依赖 app.*）"""
    with SessionLocal() as db:
        rows = (
            db.query(Watchlist)
            .filter(Watchlist.is_active == True)  # noqa: E712
            .all()
        )
    return [w.ts_code for w in rows]


async def _fetcher_lifecycle() -> None:
    """fetcher 后台协程：启动 scheduler + 立即拉一次 + 5 秒轮询。

    这个函数不会自己退出（最后进入 ``periodic_fetch_loop`` 的死循环），
    由 lifespan 在关闭时 cancel。
    """
    scheduler = mf.start_scheduler()
    scheduler.start()
    mf.logger.info(
        "fetcher scheduler started: refresh_codes_daily @ %02d:%02d %s",
        mf.SCHEDULE_HOUR, mf.SCHEDULE_MINUTE, mf.TIMEZONE,
    )

    # 启动时立即拉一次：先拿代码清单，再拉行情 + 历史
    try:
        n = await mf.refresh_codes_daily()
        if n > 0:
            codes = mf.all_stocks_cache["__meta__"]["codes"]
            await mf.fetch_all_prices(codes)
        # 拉 watchlist 中自选股的历史 K 线（用于量比计算）
        try:
            watch_codes = _read_watchlist_codes()
            if watch_codes:
                await mf.fetch_history_for_codes(watch_codes)
            else:
                mf.logger.info("no watchlist codes yet, skip history fetch")
        except Exception:
            mf.logger.exception("history fetch on startup failed")
    except Exception:
        mf.logger.exception("initial fetch failed (will rely on periodic loop)")

    # 延迟 5 秒后补拉一次历史（处理启动后立即加 watchlist 的情况）
    async def _delayed_history() -> None:
        await asyncio.sleep(5)
        try:
            watch_codes = _read_watchlist_codes()
            if watch_codes:
                await mf.fetch_history_for_codes(watch_codes)
        except Exception:
            mf.logger.exception("delayed history fetch failed")

    asyncio.create_task(_delayed_history())

    # 进入 5 秒轮询（不退出）；注入 history provider + watchlist provider
    # watchlist provider 让 fetcher 也能 5 秒同步 watchlist 中的 ETF 行情
    await mf.periodic_fetch_loop(
        history_codes_provider=_read_watchlist_codes,
        watchlist_codes_provider=_read_watchlist_codes,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动 DB 建表
    init_db()

    # 启动 fetcher 后台协程
    fetcher_task = asyncio.create_task(_fetcher_lifecycle(), name="fetcher")
    # 启动板块资金流后台协程（独立 60s 轮询，不与 5s 行情轮询抢资源）
    fund_flow_task = asyncio.create_task(
        mf.periodic_fund_flow_loop(), name="fund-flow"
    )
    # 启动 v4.1 7x24 快讯后台协程（10 分钟拉一次，10 分钟内存缓存）
    news_task = asyncio.create_task(
        news_fetcher.periodic_news_loop(), name="news-fetcher"
    )
    try:
        yield
    finally:
        # 关闭时取消后台协程
        for t in (fetcher_task, fund_flow_task, news_task):
            t.cancel()
        for t in (fetcher_task, fund_flow_task, news_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
            except Exception:
                mf.logger.exception("background task crashed on shutdown")
        mf.logger.info("all background tasks stopped, shutting down")


app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)


# v2.5.2: 全局 middleware — 强制给所有 application/json response 加 charset=utf-8
# 修复 watchlist 名称 / trade_note 等中文字段显示成 "????" 的 bug
# 根因：FastAPI 默认 content-type 是 "application/json"（无 charset），浏览器/Vue axios
# 拿到后按 Latin-1 解析，UTF-8 多字节序列被错误解码成 "?"。
# 修法：response 头里没有 charset 时，自动追加 "; charset=utf-8"
@app.middleware("http")
async def add_charset_to_json_response(request, call_next):
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    # 只处理 JSON 类，没 charset 的才补
    if ct.startswith("application/json") and "charset=" not in ct.lower():
        response.headers["content-type"] = "application/json; charset=utf-8"
    return response


# API 路由（先注册，匹配优先级高于下面的 catch-all）
# 前端走 /api/* 路径（之前是 vite 代理 rewrite 掉 /api，集成后由 FastAPI 直 serve
# 所以这里显式给两个 router 加 /api 前缀）
app.include_router(watchlist_router, prefix="/api")
app.include_router(market_router, prefix="/api")
app.include_router(strategy_router, prefix="/api")
app.include_router(trade_router, prefix="/api")  # v3.1 资金账本


# ====================== 健康检查 / 服务元信息 ======================
@app.get("/health", tags=["meta"], summary="健康检查（k8s 探针用）")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health", tags=["meta"], summary="健康检查（前端 API 路径）")
def api_health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/info", tags=["meta"], summary="服务元信息")
def api_info() -> dict[str, str]:
    return {
        "name": API_TITLE,
        "version": API_VERSION,
        "docs": "/docs",
        "openapi": "/openapi.json",
    }


# ====================== 前端静态资源（dist）======================
# 路径：<project>/frontend/dist
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_AVAILABLE = FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists()

if FRONTEND_AVAILABLE:
    # 1) 挂载 /assets 等静态目录
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(assets_dir)),
            name="frontend-assets",
        )
    # favicon.svg 等根目录静态文件
    @app.get("/favicon.ico", include_in_schema=False)
    @app.get("/favicon.svg", include_in_schema=False)
    async def _favicon():
        f = FRONTEND_DIST / "favicon.svg"
        if f.exists():
            return FileResponse(f)
        raise HTTPException(404)

    # 2) 根路径显式返回 SPA 入口
    @app.get("/", include_in_schema=False)
    async def _root_spa():
        return FileResponse(FRONTEND_DIST / "index.html")

    # 3) catch-all SPA fallback
    #    兜底 /watchlist、/signals 等前端路由（history 模式刷新不报 404）
    #    已经在前面注册过的（/api/*、/health、/assets/*、/favicon 等）会优先匹配
    #    v2026-08-23 审计修复：限制 full_path 解析后必须仍在 FRONTEND_DIST 内，
    #    防止 `../../etc/passwd` 类路径遍历读 uvicorn 进程可访问的任意文件
    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # 显式排除已知后端路径（防止任何误匹配）
        if full_path.startswith(("api/", "api")) or full_path in {"health", "openapi.json", "docs", "redoc"}:
            raise HTTPException(404)
        # 路径遍历防御：target.resolve() 必须仍在 FRONTEND_DIST 内
        try:
            target = (FRONTEND_DIST / full_path).resolve(strict=False)
            target.relative_to(FRONTEND_DIST.resolve())  # 越界抛 ValueError
        except (ValueError, OSError):
            raise HTTPException(404)
        # 如果是 dist 下真实存在的文件（favicon.svg 等），直接返回
        if target.is_file():
            return FileResponse(target)
        # 否则 fallback 到 index.html（SPA history 路由）
        return FileResponse(FRONTEND_DIST / "index.html")

    logging.getLogger("uvicorn").info(
        "frontend dist mounted at %s (SPA fallback enabled)", FRONTEND_DIST
    )
else:
    # dist 不存在（开发模式或首次启动未 build），降级为 JSON 根路径
    @app.get("/", tags=["meta"], summary="服务根信息（前端未构建）")
    def root() -> dict[str, str]:
        return {
            "name": API_TITLE,
            "version": API_VERSION,
            "docs": "/docs",
            "openapi": "/openapi.json",
            "frontend_hint": "frontend/dist 不存在，请先执行 npm run build",
        }
