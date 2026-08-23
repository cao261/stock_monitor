"""策略层 / 复盘战报。"""
from __future__ import annotations

import logging
from datetime import datetime, time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_
from sqlalchemy.orm import Session

import analyzer
import market_fetcher as mf
import news_fetcher  # v4.1: 7x24 快讯

from app import config
from app.crud.watchlist import watchlist as crud
from app.database import get_db
from app.models.trade_log import TradeLog
from app.services import llm
from app.utils.trade_note_parser import parse_trade_note

router = APIRouter(prefix="/strategy", tags=["strategy"])
logger = logging.getLogger("strategy")

# v4.4.1: /discover 结果缓存（LLM 全链路 150-280s，10 分钟内重复点击秒回）
_DISCOVER_CACHE_TTL_SECONDS = 600
_discover_cache: dict[str, dict] = {"ts": 0.0, "data": None}
# v4.6.2: 并发锁 — 防止两个请求同时 cache miss 重复跑 60s+ 完整链路
# （之前无锁：用户双击 /discover 按钮会触发 2 个并行 LLM 调用）
import asyncio
_discover_lock = asyncio.Lock()


def _discover_cache_hit() -> dict | None:
    if _discover_cache["data"] is None:
        return None
    age = datetime.now().timestamp() - _discover_cache["ts"]
    if age > _DISCOVER_CACHE_TTL_SECONDS:
        return None
    return _discover_cache["data"]


def get_today_trades(db: Session) -> list[dict]:
    """v3.0: 查今日（本地时间 00:00~23:59）的所有真实交割单。

    喂给 daily-summary 响应 + LLM 复盘 prompt（指令 8）。
    """
    today = datetime.now().date()
    start = datetime.combine(today, time(0, 0, 0))
    end = datetime.combine(today, time(23, 59, 59))
    rows = (
        db.query(TradeLog)
        .filter(and_(TradeLog.created_at >= start, TradeLog.created_at <= end))
        .order_by(TradeLog.created_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "ts_code": r.ts_code,
            "action": r.action,
            "price": round(float(r.price), 4),
            "volume": int(r.volume),
            "realized_pnl": round(float(r.realized_pnl), 2),
            "created_at": r.created_at.isoformat(timespec="seconds"),
        }
        for r in rows
    ]


@router.get(
    "/daily-summary",
    summary="今日盘后复盘战报（v2.3）",
)
def get_daily_summary(db: Session = Depends(get_db)) -> dict:
    """拼装三大模块：大盘情绪 + 自选股战况 + 全市场异动龙头 + 今日交割单（v3.0）。

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
        # v4.5: check_signals 的 current 有 price 别名 close；兜底再取一次 price
        price = current.get("close")
        if price is None:
            price = current.get("price")
        cost = w.cost_price
        position = w.position
        # v4.5: check_signals 不算浮盈亏，这里按 watchlist.py 同口径补算
        # （有持仓 + 有价格才算；空仓 / 缺价 → None → 归入 no_position）
        pnl = None
        ret = None
        if price and price > 0 and cost is not None and position is not None and int(position) > 0:
            try:
                diff = float(price) - float(cost)
                pnl = round(diff * int(position), 2)
                ret = round(diff / float(cost) * 100.0, 2)
            except (TypeError, ValueError):
                pnl = None
                ret = None
        # v2.6.2: 让 LLM 不用手算 —— 自动判断"trade_note 里的止损是否被破 / 止盈是否到"
        note_target_broken = False
        note_target_reached = False
        if price and eff_target_loss and price <= eff_target_loss:
            note_target_broken = True
        if price and eff_target_win and price >= eff_target_win:
            note_target_reached = True
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
            # v4.0: 理想建仓区间（指令 10 前瞻机会扫描用）
            "entry_price_min": w.entry_price_min,
            "entry_price_max": w.entry_price_max,
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
            # v2.6.2: 自动判字段
            #   - note_target_broken: 当前价 <= eff_target_loss (按 trade_note 提取的止损价已破)
            #   - note_target_reached: 当前价 >= eff_target_win (按 trade_note 提取的止盈价已到)
            # 跟 is_stop_loss / is_take_profit 不同点：这俩**只看 eff_target**，不管 is_take_profit 触发
            # 用来给 LLM 简化判断：即便用户没显式设 target_*, 但笔记里写了，也照样盯盘
            "note_target_broken": note_target_broken,
            "note_target_reached": note_target_reached,
        }
        if signals.get("is_take_profit"):
            take_profit_hits.append({**base, "target_win": signals.get("target_win") or w.target_win})
        if signals.get("is_stop_loss"):
            stop_loss_hits.append({**base, "target_loss": signals.get("target_loss") or w.target_loss})
        # v2.5: 三种状态分类（让 AI 监工能看见所有持仓的纪律）
        #   1. 没持仓（cost/position 缺失）        → no_position
        #   2. 有持仓但暂无价格（pnl 缺失）          → no_position（带 base）
        #   3. 有持仓有价格（pnl 有值）                → pnl_winners / pnl_losers
        # v4.6.2 修复: pnl == 0 时归入 winners（保本持仓也是合规状态，不应被前端忽略）
        if cost is None or position is None or pnl is None:
            no_position.append(base)
        else:
            if pnl >= 0:
                pnl_winners.append(base)
            if pnl < 0:
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
    # v4.5: ETF 成交量单位是"份"，跟个股"股"不可比；异动龙头榜只展示个股
    etf_codes = set(mf.get_meta().get("etf_codes") or [])
    all_stocks = {c: d for c, d in all_stocks.items() if c not in etf_codes}
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

    # ===== v3.0: 今日真实交割单（喂给 LLM 复盘 + 前端展示）=====
    today_trades_raw = get_today_trades(db)
    today_pnl_total = round(sum(t["realized_pnl"] for t in today_trades_raw), 2)

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
        # v3.0: 今日真实交割单
        # - 喂给 LLM（指令 8：审阅用户的"耐心持仓 / 严格止损 / 浮盈兑现"行为）
        # - 前端"今日复盘战报"也会展示这个板块
        "today_trades": {
            "trades": today_trades_raw,
            "total_count": len(today_trades_raw),
            "total_realized_pnl": today_pnl_total,
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
        # v4.2: 顶层 logger 已存在（line 24），不再需要局部 import
        logger.exception("LLM 调用失败：%r", e)
        # v2026-08-23 审计修复：detail 不直接返 e（避免 LLM 内部错误细节外泄）
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI 复盘生成失败，请稍后重试。",
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


# ====================== v4.1: AI 共振挖掘（Alpha Discovery）======================
# ====================== v4.4: 升级为【板块级左侧埋伏挖掘】======================
# 旧版（v4.1）在"全市场个股"里按成交量预选 + 低波动打分，结果天然偏向
# 工商银行这类低波动横盘蓝筹（无题材、无弹性、无埋伏价值）。v4.4 改为：
#   1. 板块池 = A 股概念题材（同花顺 375 + 东财资金流 387，按名称 join）
#      —— "银行/证券"行业不在池里，从源头杜绝伪候选
#   2. 板块指数真实 K 线算技术面（60日涨幅/回撤/均线粘合/量能收缩/止跌）
#   3. 消息面为自上而下的挖掘入口：新闻密集但板块未涨 = 预期差
#   4. 左侧纪律硬过滤：追涨（60日>25%）、下降未止跌、单日过热、死水、无催化出逃
@router.get(
    "/discover",
    summary="v4.4 板块级前瞻 Alpha 掘金（左侧埋伏：技术+资金+催化共振）",
)
async def discover() -> dict:
    """板块级左侧埋伏挖掘；板块数据不可用时降级到 v4.1 个股引擎。"""
    all_stocks = mf.get_all_stocks()
    if not all_stocks or len(all_stocks) < 50:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="全市场实时行情尚未就绪，请等待行情缓存刷新后重试。",
        )

    news_cache = news_fetcher.get_news()
    news = news_cache.get("data", [])
    if not news and not news_fetcher.is_news_cache_fresh():
        try:
            await news_fetcher.refresh_news()
            news_cache = news_fetcher.get_news()
            news = news_cache.get("data", [])
        except Exception:
            logger.warning("/discover news refresh failed", exc_info=True)

    # v4.4.1: 10 分钟结果缓存（LLM 全链路太慢，重复点击秒回）
    cached = _discover_cache_hit()
    if cached is not None:
        return cached

    # v4.6.2: 并发锁——避免两个请求同时 cache miss 重复跑 60s 完整链路
    async with _discover_lock:
        # 拿到锁后再次检查 cache（其他请求可能已填充）
        cached = _discover_cache_hit()
        if cached is not None:
            return cached

        # ===== v4.4 主路径：板块级引擎 =====
        from app.services import sector_alpha
        built = await sector_alpha.build_sector_candidates(all_stocks, news)
        if built["sectors"]:
            result = await llm.generate_sector_discover(built["sectors"], news)
            payload = {
                **result,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "meta": {
                    "engine_level": "sector",
                    "pool_size": built["pool_size"],
                    "prescreened": built["prescreened"],
                    "index_ready": built["index_ready"],
                    "rejected": built["rejected"],
                    "candidate_count": len(built["sectors"]),
                    "news_count": len(news),
                    "news_source": news_cache.get("source"),
                    "news_fetched_at": news_cache.get("fetched_at"),
                    "news_error": news_cache.get("error"),
                },
            }
            _discover_cache["ts"] = datetime.now().timestamp()
            _discover_cache["data"] = payload
            return payload

        # ===== v4.1 降级路径：个股引擎（板块数据不可用/无候选时） =====
        from app.services.alpha_discovery import build_quantitative_candidates, preselect_codes
        candidate_codes = preselect_codes(all_stocks)
        histories = await mf.ensure_history_for_codes(candidate_codes, min_records=25, concurrency=4)
        candidates, rejected = build_quantitative_candidates(all_stocks, histories, target_count=5)
        if not candidates:
            return {
                "discoveries": [],
                "engine_type": "quantitative",
                "engine_name": "⚡ 量化低位筛选",
                "engine_desc": "板块引擎与个股引擎均未产出满足左侧纪律的候选。",
                "model": None,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "meta": {
                    "engine_level": "fallback_stock",
                    "preselected_count": len(candidate_codes),
                    "rejected": rejected,
                    "degraded_reason": "no_sector_candidates",
                },
            }

        result = await llm.generate_discover(
            candidates=candidates,
            news=news,
        )
        return {
            **result,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "meta": {
                "engine_level": "fallback_stock",
                "preselected_count": len(candidate_codes),
                "candidate_count": len(candidates),
                "rejected": rejected,
                "news_count": len(news),
                "news_source": news_cache.get("source"),
                "news_fetched_at": news_cache.get("fetched_at"),
                "news_error": news_cache.get("error"),
            },
        }
