"""watchlist 表的 CRUD 操作。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.watchlist import Watchlist
from app.schemas.watchlist import WatchlistCreate, WatchlistUpdate


class CRUDWatchlist:
    """封装 watchlist 的增删改查，便于复用与单元测试。"""

    def get(self, db: Session, stock_id: int) -> Watchlist | None:
        return db.get(Watchlist, stock_id)

    def get_by_ts_code(self, db: Session, ts_code: str) -> Watchlist | None:
        ts_code_norm = ts_code.strip().lower()
        stmt = select(Watchlist).where(Watchlist.ts_code == ts_code_norm)
        return db.execute(stmt).scalar_one_or_none()

    def list(
        self,
        db: Session,
        *,
        skip: int = 0,
        limit: int = 100,
        is_active: bool | None = None,
    ) -> list[Watchlist]:
        stmt = select(Watchlist)
        if is_active is not None:
            stmt = stmt.where(Watchlist.is_active == is_active)
        stmt = stmt.order_by(Watchlist.id.asc()).offset(skip).limit(limit)
        return list(db.execute(stmt).scalars().all())

    def count(self, db: Session, *, is_active: bool | None = None) -> int:
        stmt = select(func.count(Watchlist.id))
        if is_active is not None:
            stmt = stmt.where(Watchlist.is_active == is_active)
        return int(db.execute(stmt).scalar_one())

    def create(self, db: Session, obj_in: WatchlistCreate) -> Watchlist:
        db_obj = Watchlist(
            ts_code=obj_in.ts_code,
            name=obj_in.name,
            exchange=obj_in.exchange,
            market=obj_in.market,
            industry=obj_in.industry,
            is_active=obj_in.is_active,
        )
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self, db: Session, db_obj: Watchlist, obj_in: WatchlistUpdate
    ) -> Watchlist:
        data = obj_in.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(db_obj, field, value)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def delete(self, db: Session, db_obj: Watchlist) -> None:
        db.delete(db_obj)
        db.commit()


watchlist = CRUDWatchlist()
