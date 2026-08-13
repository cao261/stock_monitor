"""/trades 路由：v3.1 历史交割单。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.trade_log import TradeLog
from app.models.watchlist import Watchlist

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get(
    "/history",
    summary="v3.1 历史交割单（资金账本数据源）",
)
def get_trade_history(
    ts_code: Optional[str] = Query(None, description="按股票代码过滤（可选）"),
    limit: int = Query(200, ge=1, le=1000, description="返回条数（默认 200，上限 1000）"),
    db: Session = Depends(get_db),
) -> dict:
    """查 trade_log 表所有记录（按时间倒序），附带 watchlist 名字映射。

    返回结构：
        {
            "total_count": int,            # 当前查询范围内的总条数
            "total_realized_pnl": float,   # 当前查询范围内的已实现盈亏合计
            "trades": [                    # 倒序（最新在前）
                { id, ts_code, name, action, price, volume, realized_pnl, created_at }
            ]
        }
    """
    q = db.query(TradeLog)
    if ts_code:
        # 用户可能传 sh600000 也可能传 600000，归一化只对前缀大小写不敏感
        q = q.filter(TradeLog.ts_code == ts_code.strip().lower())
    rows = q.order_by(TradeLog.created_at.desc()).limit(limit).all()

    # 一次查全 watchlist 拿 name 映射（避免 N+1）
    all_codes = {r.ts_code for r in rows}
    name_map: dict[str, str] = {}
    if all_codes:
        for w in db.query(Watchlist).filter(Watchlist.ts_code.in_(all_codes)).all():
            if w.name:
                name_map[w.ts_code] = w.name

    trades = [
        {
            "id": r.id,
            "ts_code": r.ts_code,
            "name": name_map.get(r.ts_code, ""),  # 没找到就空（trade_log 永留，不依赖 watchlist 存在）
            "action": r.action,
            "price": round(float(r.price), 4),
            "volume": int(r.volume),
            "realized_pnl": round(float(r.realized_pnl), 2),
            "created_at": r.created_at.isoformat(timespec="seconds"),
        }
        for r in rows
    ]
    total_realized = round(sum(t["realized_pnl"] for t in trades), 2)

    return {
        "total_count": len(trades),
        "total_realized_pnl": total_realized,
        "trades": trades,
    }
