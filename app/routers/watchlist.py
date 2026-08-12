"""/watchlist 路由：自选股 CRUD。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import analyzer
import market_fetcher as mf
from app.crud.watchlist import watchlist as crud
from app.database import get_db
from app.schemas.watchlist import (
    WatchlistCreate,
    WatchlistRead,
    WatchlistUpdate,
)
from app.schemas.watchlist_quote import WatchlistQuote

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get(
    "",
    response_model=list[WatchlistRead],
    summary="获取自选股列表",
)
def list_watchlist(
    skip: int = Query(0, ge=0, description="分页起始位置"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
    is_active: bool | None = Query(None, description="按启用状态过滤"),
    db: Session = Depends(get_db),
) -> list[WatchlistRead]:
    items = crud.list(db, skip=skip, limit=limit, is_active=is_active)
    # 响应里塞入告警规则数量（ORM 已用 selectin 预加载 alert_rules）
    result: list[WatchlistRead] = []
    for item in items:
        read = WatchlistRead.model_validate(item)
        read.alert_rules_count = len(item.alert_rules)
        result.append(read)
    return result


@router.post(
    "",
    response_model=WatchlistRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增自选股",
)
def create_watchlist(
    payload: WatchlistCreate, db: Session = Depends(get_db)
) -> WatchlistRead:
    if crud.get_by_ts_code(db, payload.ts_code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"ts_code {payload.ts_code} 已存在",
        )
    try:
        obj = crud.create(db, payload)
    except IntegrityError as exc:  # 兜底防并发重复
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="唯一约束冲突，可能 ts_code 已存在",
        ) from exc

    read = WatchlistRead.model_validate(obj)
    read.alert_rules_count = 0
    return read


@router.get(
    "/quotes",
    response_model=list[WatchlistQuote],
    summary="自选股 + 实时行情联调（一次拿到所有自选股的最新盘面，支持 ETF）",
)
async def list_watchlist_quotes(
    is_active: bool | None = Query(
        None, description="按启用状态过滤，默认全量"
    ),
    db: Session = Depends(get_db),
) -> list[WatchlistQuote]:
    """JOIN watchlist 表与 market_fetcher 内存缓存。

    cache miss 时（典型：用户刚加的 ETF 还没被 5s 轮询抓到）：
    立即调 ``ensure_price_in_cache`` 拉一次，保证 watchlist 里
    任何代码（股票 + ETF）都能立即返回行情。

    持仓字段（cost_price / position）若有 + 行情有现价，则计算：
      - floating_pnl = (price - cost_price) * position
      - return_rate  = (price - cost_price) / cost_price * 100
    任一字段缺失则相应返回 null。
    """
    items = crud.list(db, skip=0, limit=1000, is_active=is_active)
    result: list[WatchlistQuote] = []
    for item in items:
        quote = mf.get_stock(item.ts_code)
        if quote is None:
            # cache miss：实时单只拉取（支持 ETF）
            quote = await mf.ensure_price_in_cache(item.ts_code)

        # ===== 计算浮动盈亏 & 收益率（v1.1）=====
        floating_pnl: float | None = None
        return_rate: float | None = None
        if quote is not None and item.cost_price is not None and item.position is not None:
            price = quote.get("price")
            if price is not None:
                diff = float(price) - float(item.cost_price)
                floating_pnl = round(diff * float(item.position), 2)
                if float(item.cost_price) > 0:
                    return_rate = round(diff / float(item.cost_price) * 100.0, 2)

        # ===== v2.6: trade_note 智能解析 =====
        from app.utils.trade_note_parser import parse_trade_note
        note_parsed = parse_trade_note(item.trade_note)
        eff_target_win = item.target_win if item.target_win is not None else note_parsed["target_win"]
        eff_target_loss = item.target_loss if item.target_loss is not None else note_parsed["target_loss"]
        note_has_rule = bool(
            note_parsed["target_win"] or note_parsed["target_loss"] or note_parsed["semantic_rules"]
        )

        if quote is None:
            result.append(
                WatchlistQuote(
                    id=item.id,
                    ts_code=item.ts_code,
                    name=item.name,
                    name_from_market=None,
                    industry=item.industry,
                    is_active=item.is_active,
                    in_cache=False,
                    cost_price=item.cost_price,
                    position=item.position,
                    trade_note=item.trade_note,
                    target_win=item.target_win,
                    target_loss=item.target_loss,
                    floating_pnl=None,
                    return_rate=None,
                    # v2.6
                    note_extracted_target_win=note_parsed["target_win"],
                    note_extracted_target_loss=note_parsed["target_loss"],
                    eff_target_win=eff_target_win,
                    eff_target_loss=eff_target_loss,
                    note_has_rule=note_has_rule,
                    note_semantic_rules=note_parsed["semantic_rules"],
                )
            )
            continue
        result.append(
            WatchlistQuote(
                id=item.id,
                ts_code=item.ts_code,
                name=item.name,
                name_from_market=quote.get("name"),
                industry=item.industry,
                is_active=item.is_active,
                in_cache=True,
                price=quote.get("price"),
                open=quote.get("open"),
                prev_close=quote.get("prev_close"),
                high=quote.get("high"),
                low=quote.get("low"),
                volume=quote.get("volume"),
                amount=quote.get("amount"),
                change_pct=quote.get("change_pct"),
                quote_date=quote.get("quote_date"),
                quote_time=quote.get("quote_time"),
                updated_at=quote.get("updated_at"),
                # 持仓 / 止盈止损 (v1.1 + v1.2)
                cost_price=item.cost_price,
                position=item.position,
                trade_note=item.trade_note,
                target_win=item.target_win,
                target_loss=item.target_loss,
                floating_pnl=floating_pnl,
                return_rate=return_rate,
                # v2.6
                note_extracted_target_win=note_parsed["target_win"],
                note_extracted_target_loss=note_parsed["target_loss"],
                eff_target_win=eff_target_win,
                eff_target_loss=eff_target_loss,
                note_has_rule=note_has_rule,
                note_semantic_rules=note_parsed["semantic_rules"],
            )
        )
    return result


@router.get(
    "/signals",
    summary="自选股信号扫描（量比 + 放量/缩量判断）",
)
def list_watchlist_signals(
    is_active: bool | None = Query(None, description="按启用状态过滤"),
    only_triggered: bool = Query(
        False, description="仅返回触发了至少一个信号的股票"
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    """对每只自选股实时计算量比与放量/缩量信号。"""
    items = crud.list(db, skip=0, limit=1000, is_active=is_active)
    out: list[dict] = []
    for item in items:
        current = mf.get_stock(item.ts_code)
        if current is None:
            # 缓存里还没有，回报一个带 hint 的项
            if not only_triggered:
                out.append({
                    "stock_id": item.id,
                    "ts_code": item.ts_code,
                    "name": item.name,
                    "in_cache": False,
                    "signals": {
                        "is_volume_breakout": False,
                        "is_shrinking_pullback": False,
                        "is_take_profit": False,
                        "is_stop_loss": False,
                    },
                })
            continue
        history = mf.get_history(item.ts_code)
        # v2.6: 用 eff_target_* (用户值优先，trade_note 提取值兜底) 触发信号
        from app.utils.trade_note_parser import parse_trade_note
        _note = parse_trade_note(item.trade_note)
        _eff_tw = item.target_win if item.target_win is not None else _note["target_win"]
        _eff_tl = item.target_loss if item.target_loss is not None else _note["target_loss"]
        # v1.2: 把止盈/止损价也传进信号引擎，触发对应 is_take_profit / is_stop_loss
        sig = analyzer.check_signals(
            item.ts_code,
            current,
            history,
            target_win=_eff_tw,
            target_loss=_eff_tl,
        )
        triggered = (
            sig["signals"]["is_volume_breakout"]
            or sig["signals"]["is_shrinking_pullback"]
            or sig["signals"]["is_take_profit"]
            or sig["signals"]["is_stop_loss"]
        )
        if only_triggered and not triggered:
            continue
        out.append({
            "stock_id": item.id,
            "ts_code": item.ts_code,
            "name": item.name or sig.get("name"),
            "in_cache": True,
            **sig,
        })
    return out


@router.get(
    "/{stock_id}",
    response_model=WatchlistRead,
    summary="按 ID 获取单条自选股",
)
def get_watchlist(stock_id: int, db: Session = Depends(get_db)) -> WatchlistRead:
    obj = crud.get(db, stock_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="自选股不存在"
        )
    read = WatchlistRead.model_validate(obj)
    read.alert_rules_count = len(obj.alert_rules)
    return read


@router.get(
    "/by-code/{ts_code}",
    response_model=WatchlistRead,
    summary="按 ts_code 获取单条自选股",
)
def get_watchlist_by_code(ts_code: str, db: Session = Depends(get_db)) -> WatchlistRead:
    obj = crud.get_by_ts_code(db, ts_code)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ts_code {ts_code} 不存在",
        )
    read = WatchlistRead.model_validate(obj)
    read.alert_rules_count = len(obj.alert_rules)
    return read


@router.patch(
    "/{stock_id}",
    response_model=WatchlistRead,
    summary="更新自选股（部分字段）",
)
def update_watchlist(
    stock_id: int,
    payload: WatchlistUpdate,
    db: Session = Depends(get_db),
) -> WatchlistRead:
    obj = crud.get(db, stock_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="自选股不存在"
        )
    updated = crud.update(db, obj, payload)
    read = WatchlistRead.model_validate(updated)
    read.alert_rules_count = len(updated.alert_rules)
    return read


@router.delete(
    "/{stock_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除自选股（级联删除其告警规则）",
)
def delete_watchlist(stock_id: int, db: Session = Depends(get_db)) -> None:
    obj = crud.get(db, stock_id)
    if not obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="自选股不存在"
        )
    crud.delete(db, obj)
    return None
