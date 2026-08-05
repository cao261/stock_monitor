"""CRUD 层封装。把数据库读写集中在这里，router 保持薄。"""
from app.crud.watchlist import watchlist as watchlist_crud

__all__ = ["watchlist_crud"]
