"""策略层 / 复盘战报。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import analyzer
import market_fetcher as mf

from app import config
from app.crud.watchlist import watchlist as crud
from app.database import get_db
from app.services import llm
from app.utils.trade_note_parser import parse_trade_note

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
        # v2.6: 用 trade_note 智能解析 — 用户没设 target_* 时，从 trade_note 里挖
        note_parsed = parse_trade_note(w.trade_note)
        eff_target_win = w.target_win if w.target_win is not None else note_parsed["target_win"]
        eff_target_loss = w.target_loss if w.target_loss is not None else note_parsed["target_loss"]
        # 把 trade_note 解析结果也带进 base，前端能显示"🤖 已识别"
        sig = analyzer.check_signals(
            w.ts_code,
            mf.get_stock(w.ts_code) or {},
            mf.get_history(w.ts_code),
            target_win=eff_target_win,
            target_loss=eff_target_loss,
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
            # v2.5: 把交易备忘和止盈止损目标都带上，AI 监工需要逐只审查纪律
            "trade_note": w.trade_note or "",
            "target_win": w.target_win,
            "target_loss": w.target_loss,
            # v2.6: trade_note 智能解析结果
            #   - note_extracted_target_win / note_extracted_target_loss: 从笔记里挖出的价
            #   - eff_target_win / eff_target_loss: 实际生效的止盈/止损（用户值优先，笔记值兜底）
            #   - note_has_rule: True 表示笔记里有任何纪律（数字或语义）
            "note_extracted_target_win": note_parsed["target_win"],
            "note_extracted_target_loss": note_parsed["target_loss"],
            "eff_target_win": eff_target_win,
            "eff_target_loss": eff_target_loss,
            "note_has_rule": bool(
                note_parsed["target_win"] or note_parsed["target_loss"]
                or note_parsed["semantic_rules"]
            ),
            "note_semantic_rules": note_parsed["semantic_rules"],
        }
        if signals.get("is_take_profit"):
            take_profit_hits.append({**base, "target_win": signals.get("target_win") or w.target_win})
        if signals.get("is_stop_loss"):
            stop_loss_hits.append({**base, "target_loss": signals.get("target_loss") or w.target_loss})
        # v2.5: 三种状态分类（让 AI 监工能看见所有持仓的纪律）
        #   1. 没持仓（cost/position 缺失）        → no_position
        #   2. 有持仓但暂无价格（pnl 缺失）          → no_position（带 base）
        #   3. 有持仓有价格（pnl 有值）                → pnl_winners / pnl_losers
        if cost is None or position is None or pnl is None:
            no_position.append(base)
        else:
            if pnl > 0:
                pnl_winners.append(base)
            elif pnl < 0:
                pnl_losers.append(base)
            pnl_total += pnl
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
        # v2.5: 把 no_position 数组也带进响应（监工需要看持仓股的 trade_note / target_win / target_loss）
        "no_position": no_position,
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

    # 3. v2.4.3: 存盘到 data/ai_reports/YYYY-MM-DD_HHMMSS.md（按召唤时间）
    #    用户想随时翻历史报告；命名按时间排序
    now = datetime.now()
    report_dir = Path(config.DATA_DIR) / "ai_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_filename = now.strftime("%Y-%m-%d_%H%M%S") + ".md"
    report_path = report_dir / report_filename
    report_path.write_text(
        f"# AI 深度复盘 · {now.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"> 模型：{config.LLM_MODEL_NAME} · 召唤人：自用本地复盘工具\n\n"
        f"---\n\n"
        f"{report_md}\n",
        encoding="utf-8",
    )

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": config.LLM_MODEL_NAME,
        "report_markdown": report_md,
        "file_path": str(report_path),           # v2.4.3: 报告存盘路径
        "file_name": report_filename,
        # 把战报数据也回传，前端如果想要"对照看"不用再调一次
        "summary": summary,
    }
