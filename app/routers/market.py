"""/market 路由：暴露内存缓存里的实时行情。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

import analyzer
import market_fetcher as mf
from app.schemas.market import MarketMeta, StockSnapshot

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/meta", response_model=MarketMeta, summary="fetcher 元信息")
def get_market_meta() -> dict[str, Any]:
    meta = mf.get_meta()
    hist = mf.get_history_meta()
    return {
        "code_count": meta.get("code_count", 0),
        "code_refreshed_at": meta.get("code_refreshed_at"),
        "last_fetch_at": meta.get("last_fetch_at"),
        "last_fetch_count": meta.get("last_fetch_count", 0),
        "history_size": hist.get("size", 0),
        "history_with_data": hist.get("codes_with_data", 0),
    }


@router.get("/sentiment", summary="全市场情绪评分")
def get_market_sentiment() -> dict[str, Any]:
    """统计全市场涨跌停家数并打分，0~100 分。"""
    return analyzer.calculate_market_sentiment()


@router.get(
    "/top/gainers",
    response_model=list[StockSnapshot],
    summary="涨幅榜 Top N",
)
def get_top_gainers(
    n: int = Query(20, ge=1, le=200, description="取前 N 只"),
) -> list[dict[str, Any]]:
    all_stocks = mf.get_all_stocks()
    items = sorted(
        all_stocks.items(),
        key=lambda kv: kv[1].get("change_pct", 0.0),
        reverse=True,
    )
    return [{"code": code, **data} for code, data in items[:n]]


@router.get(
    "/top",
    response_model=list[StockSnapshot],
    summary="全市场排行榜（v2.2：异动雷达用）",
)
def get_top_movers(
    sort_by: str = Query(
        "change_pct",
        pattern="^(change_pct|volume)$",
        description="排序字段：change_pct=涨跌幅，volume=成交量",
    ),
    limit: int = Query(20, ge=1, le=200, description="取前 N 只"),
) -> list[dict[str, Any]]:
    """统一的全市场排行榜：支持按涨跌幅 / 成交量排序。

    过滤掉缺数据的（change_pct / volume 为 None 的），
    避免 0 跟 null 混排导致无意义的名次。
    """
    all_stocks = mf.get_all_stocks()
    if sort_by == "change_pct":
        # 涨跌幅：从大到小，过滤 None
        items = [
            (code, d) for code, d in all_stocks.items()
            if d.get("change_pct") is not None
        ]
        items.sort(key=lambda kv: kv[1]["change_pct"], reverse=True)
    else:  # volume
        items = [
            (code, d) for code, d in all_stocks.items()
            if d.get("volume") is not None and d.get("volume", 0) > 0
        ]
        items.sort(key=lambda kv: kv[1]["volume"], reverse=True)
    return [{"code": code, **data} for code, data in items[:limit]]


@router.get(
    "/top/losers",
    response_model=list[StockSnapshot],
    summary="跌幅榜 Top N",
)
def get_top_losers(
    n: int = Query(20, ge=1, le=200, description="取前 N 只"),
) -> list[dict[str, Any]]:
    all_stocks = mf.get_all_stocks()
    items = sorted(
        all_stocks.items(),
        key=lambda kv: kv[1].get("change_pct", 0.0),
    )
    return [{"code": code, **data} for code, data in items[:n]]


@router.get(
    "/fund-flow",
    summary="概念板块资金流向（力导向气泡图用）",
)
def get_fund_flow(
    top: int = Query(0, ge=0, le=200, description="只看净流入前 N（0=不限）"),
    bottom: int = Query(0, ge=0, le=200, description="只看净流出前 N（0=不限）"),
    limit: int = Query(300, ge=1, le=500, description="总返回条数上限"),
) -> dict[str, Any]:
    """返回所有概念板块的资金流向数据（按 |净额| 倒序）。

    数据来源：akshare.stock_fund_flow_concept('即时')，由后台 60 秒轮询写入缓存。
    """
    cache = mf.get_fund_flow()
    data = list(cache.get("data", []))
    # 按 |净额| 倒序，让前端气泡布局时重要的板块先入图
    data.sort(key=lambda x: abs(x.get("net_amount", 0.0)), reverse=True)

    if top > 0 or bottom > 0:
        # 强制按净额正负切两片
        if top > 0:
            inflow = [x for x in data if x.get("net_amount", 0) > 0]
            inflow.sort(key=lambda x: x.get("net_amount", 0.0), reverse=True)
            inflow = inflow[:top]
        else:
            inflow = []
        if bottom > 0:
            outflow = [x for x in data if x.get("net_amount", 0) < 0]
            outflow.sort(key=lambda x: x.get("net_amount", 0.0))
            outflow = outflow[:bottom]
        else:
            outflow = []
        merged = inflow + outflow
        merged.sort(key=lambda x: abs(x.get("net_amount", 0.0)), reverse=True)
        data = merged

    data = data[:limit]

    total_in = sum(x.get("inflow", 0.0) for x in data)
    total_out = sum(x.get("outflow", 0.0) for x in data)
    return {
        "count": len(data),
        "refreshed_at": cache.get("refreshed_at"),
        "total_inflow": round(total_in, 2),
        "total_outflow": round(total_out, 2),
        "unit": "亿",
        "items": data,
    }


@router.get(
    "/{code}/history",
    summary="单只股票的历史 K 线（lightweight-charts 友好格式，支持 ETF）",
)
async def get_market_history(code: str) -> dict[str, Any]:
    """返回组装好的 K 线 + 成交量序列，适配 TradingView Lightweight Charts。

    注：必须在 ``/{code}`` 之前注册，否则会被通用 catch 拦截。

    cache miss 时（典型场景：用户刚加的 ETF 还没拉历史）：
    自动调一次 fetch_history_for_codes 单只拉取，写入 history_cache 再返回。
    """
    code_norm = code.strip().lower()
    h = mf.get_history(code_norm)
    data_records = list(h.get("data") or [])  # copy 以便追加当日实时 K 线
    if not data_records:
        # cache miss：实时拉一次
        await mf.fetch_history_for_codes([code_norm], concurrency=1)
        h = mf.get_history(code_norm)
        data_records = list(h.get("data") or [])
    if not data_records:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{code_norm} 无历史数据，请先调 POST /market/history/refresh 拉取"
            ),
        )
    # ===== v2.0: 拼装当日实时 K 线 =====
    # 历史接口盘中不更新，腾讯免费接口盘后才出当天 K 线。
    # 用 all_stocks_cache 里的实时切片（每 5 秒刷一次）合成最后一根 K 线，
    # 让图表和外面显示的现价永远一致。
    last_date = data_records[-1]["date"]
    realtime = mf.get_stock(code_norm) or {}
    rt_date = realtime.get("quote_date") or ""
    rt_price = realtime.get("price")
    if rt_date and rt_date != last_date and rt_price and rt_price > 0:
        # 用 实时 open / 实时 high / 实时 low / 实时 price 当 close
        rt_open = realtime.get("open") or 0
        rt_high = realtime.get("high") or 0
        rt_low = realtime.get("low") or 0
        # 防御：实时 high < price 时，把 price 算进去；实时 low > price 时同理
        high = max(rt_high, rt_price) if rt_high > 0 else rt_price
        low = min(rt_low, rt_price) if rt_low > 0 else rt_price
        # volume 单位是"股"，要除 100 转成"手"（与 fetch_history 一致）
        vol_lots = (realtime.get("volume") or 0) / 100.0
        data_records.append({
            "date": rt_date,
            "open": rt_open,
            "high": high,
            "low": low,
            "close": rt_price,
            "volume_lots": vol_lots,
            "amount": realtime.get("amount") or 0,
        })
    klines: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []
    for r in data_records:
        klines.append(
            {
                "time": r["date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
            }
        )
        is_up = r["close"] >= r["open"]
        volumes.append(
            {
                "time": r["date"],
                "value": r["volume_lots"],
                "color": "#ef4444" if is_up else "#22c55e",  # 红涨绿跌
            }
        )
    return {
        "code": code_norm,
        "avg_volume_5d": h.get("avg_volume_5d", 0.0),
        "avg_amount_5d": h.get("avg_amount_5d", 0.0),
        "klines": klines,
        "volumes": volumes,
    }


@router.get(
    "/{code}",
    response_model=StockSnapshot,
    summary="按代码查询行情快照（cache miss 时自动拉取，支持 ETF）",
)
async def get_market_by_code(code: str) -> dict[str, Any]:
    code_norm = code.strip().lower()
    data = mf.get_stock(code_norm)
    if not data:
        # cache miss（典型为 ETF 或新上市股票）→ 立即拉一次
        data = await mf.ensure_price_in_cache(code_norm)
    if not data:
        raise HTTPException(
            status_code=404,
            detail=f"code {code_norm} 拉取失败，可能不是有效代码或数据源异常",
        )
    return {"code": code_norm, **data}


@router.get(
    "",
    response_model=list[StockSnapshot],
    summary="全市场行情列表（分页）",
)
def list_market(
    skip: int = Query(0, ge=0, description="分页起始位置"),
    limit: int = Query(100, ge=1, le=1000, description="每页条数"),
) -> list[dict[str, Any]]:
    all_stocks = mf.get_all_stocks()
    items = list(all_stocks.items())[skip : skip + limit]
    return [{"code": code, **data} for code, data in items]


@router.post(
    "/refresh",
    summary="手动触发一次全市场抓取",
)
async def refresh_market() -> dict[str, Any]:
    """通常 fetcher 每 5 秒自动跑一次；用这个接口可以强制立刻跑。"""
    codes = mf.all_stocks_cache.get("__meta__", {}).get("codes", [])
    if not codes:
        # 缓存里没代码清单，先尝试 refresh
        n = await mf.refresh_codes_daily()
        if n == 0:
            return {"status": "no_codes", "updated": 0}
        codes = mf.all_stocks_cache["__meta__"]["codes"]
    updated = await mf.fetch_all_prices(codes)
    return {"status": "ok", "updated": updated, "total": len(codes)}


@router.post(
    "/history/refresh",
    summary="手动触发 watchlist 中自选股的历史 K 线刷新",
)
async def refresh_history() -> dict[str, Any]:
    """fetcher 启动时会拉一次 + 每 30 分钟补拉一次。
    用户加完自选股后可立即调这个端点刷历史。
    """
    from app.database import SessionLocal
    from app.models import Watchlist

    with SessionLocal() as db:
        codes = [
            w.ts_code
            for w in db.query(Watchlist)
            .filter(Watchlist.is_active == True)  # noqa: E712
            .all()
        ]
    if not codes:
        return {"status": "no_watchlist", "updated": 0}
    n = await mf.fetch_history_for_codes(codes)
    valid = sum(1 for c in codes if mf.get_history(c).get("data"))
    return {"status": "ok", "requested": n, "valid": valid}
