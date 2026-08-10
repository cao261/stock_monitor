"""策略层 / 复盘战报。"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import analyzer
import market_fetcher as mf

from app import config
from app.crud.watchlist import watchlist as crud
from app.database import get_db
from app.services import llm

router = APIRouter(prefix="/strategy", tags=["strategy"])


@router.get(
    "/daily-summary",
    summary="今日盘后复盘战报（v2.3）",
)
def get_daily_summary(db: Session = Depends(get_db)) -> dict:
    """拼装三大模块：大盘情绪 + 自选股战况 + 全市场异动龙头。

    用于前端"今日复盘战报"模态框。
    任何时刻都能调（盘中、盘后都行），逻辑只看当前快照。
    """
    # ===== 1. 大盘情绪 =====
    sentiment = analyzer.calculate_market_sentiment()

    # ===== 2. 自选股战况 =====
    items = crud.list(db, skip=0, limit=1000, is_active=True)
    take_profit_hits: list[dict] = []
    stop_loss_hits: list[dict] = []
    pnl_winners: list[dict] = []
    pnl_losers: list[dict] = []
    no_position: list[dict] = []
    pnl_total = 0.0
    cost_total = 0.0
    market_total = 0.0
    for w in items:
        sig = analyzer.check_signals(
            w.ts_code,
            mf.get_stock(w.ts_code) or {},
            mf.get_history(w.ts_code),
        ) or {}
        signals = sig.get("signals", {}) or {}
        current = sig.get("current", {}) or {}
        price = current.get("close")
        cost = w.cost_price
        position = w.position
        pnl = sig.get("floating_pnl")
        ret = sig.get("return_rate")
        base = {
            "ts_code": w.ts_code,
            "name": w.name or sig.get("name") or "",
            "price": price,
            "cost_price": cost,
            "position": position,
            "floating_pnl": pnl,
            "return_rate": ret,
        }
        if signals.get("is_take_profit"):
            take_profit_hits.append({**base, "target_win": signals.get("target_win")})
        if signals.get("is_stop_loss"):
            stop_loss_hits.append({**base, "target_loss": signals.get("target_loss")})
        # 没持仓（cost 或 position 缺失）→ 单独一组
        if cost is None or position is None:
            no_position.append(base)
        else:
            if pnl is not None:
                pnl_total += pnl
                if pnl > 0:
                    pnl_winners.append(base)
                elif pnl < 0:
                    pnl_losers.append(base)
            # 持仓市值（用于算总成本 / 总市值）
            if price and price > 0:
                market_total += price * position
            cost_total += cost * position

    watchlist_battle = {
        "total": len(items),
        "winning_count": len(pnl_winners),
        "losing_count": len(pnl_losers),
        "no_position_count": len(no_position),
        "floating_pnl_total": round(pnl_total, 2),
        "cost_total": round(cost_total, 2),
        "market_total": round(market_total, 2),
        "total_return_rate": (
            round(pnl_total / cost_total * 100, 2) if cost_total > 0 else None
        ),
        "take_profit_triggered": take_profit_hits,
        "stop_loss_triggered": stop_loss_hits,
        "winners": sorted(pnl_winners, key=lambda x: x.get("floating_pnl") or 0, reverse=True)[:5],
        "losers": sorted(pnl_losers, key=lambda x: x.get("floating_pnl") or 0)[:5],
    }

    # ===== 3. 全市场异动龙头 =====
    all_stocks = mf.get_all_stocks()
    # 涨幅榜 Top 3（保留 (code, d) tuples 以便拼装响应）
    by_chg = sorted(
        (
            (code, d) for code, d in all_stocks.items()
            if d.get("change_pct") is not None
        ),
        key=lambda kv: kv[1]["change_pct"],
        reverse=True,
    )
    top_change = [
        {
            "code": code,
            "name": d.get("name", ""),
            "change_pct": round(d["change_pct"], 2),
            "price": d.get("price"),
        }
        for code, d in by_chg[:3]
    ]
    # 成交榜 Top 3
    by_vol = sorted(
        (
            (code, d) for code, d in all_stocks.items()
            if d.get("volume") is not None and d.get("volume", 0) > 0
        ),
        key=lambda kv: kv[1]["volume"],
        reverse=True,
    )
    top_volume = [
        {
            "code": code,
            "name": d.get("name", ""),
            "volume": d.get("volume"),
            "volume_lots": round(d["volume"] / 100, 0),
            "price": d.get("price"),
        }
        for code, d in by_vol[:3]
    ]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sentiment": {
            "score": sentiment.get("score"),
            "label": sentiment.get("label"),
            "up_count": sentiment.get("up_count"),
            "down_count": sentiment.get("down_count"),
            "up_ratio": sentiment.get("up_ratio"),
            "limit_up_count": sentiment.get("limit_up_count"),
            "limit_down_count": sentiment.get("limit_down_count"),
        },
        "watchlist_battle": watchlist_battle,
        "top_movers": {
            "by_change_pct": top_change,
            "by_volume": top_volume,
        },
    }


@router.post(
    "/ai-report",
    summary="AI 深度复盘（v2.4：LLM 生成）",
)
async def generate_ai_report(db: Session = Depends(get_db)) -> dict:
    """基于今日盘后数据调用 LLM 生成深度复盘 Markdown 报告。

    前置：项目根目录 .env 里有 LLM_API_KEY（或 OpenAI-compatible 服务）。

    没配 key 时：返 503 + 提示信息（不报错给前端，前端做降级 UI）。
    网络/限流错误：返 502 + 错误信息。
    """
    if not config.LLM_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM 未启用。请在项目根目录的 .env 里设置 LLM_API_KEY"
                "（参考 .env.example 切换 OpenAI / DeepSeek / 通义千问 / 智谱 等）。"
            ),
        )

    # 1. 先拿今日战报数据
    summary = get_daily_summary(db=db)

    # 2. 调用 LLM 生成报告
    try:
        report_md = await llm.generate_report(summary)
    except Exception as e:
        logger_msg = f"LLM 调用失败：{e!r}"
        import logging
        logging.getLogger("strategy").exception(logger_msg)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI 复盘生成失败：{e}",
        ) from e

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": config.LLM_MODEL_NAME,
        "report_markdown": report_md,
        # 把战报数据也回传，前端如果想要"对照看"不用再调一次
        "summary": summary,
    }
