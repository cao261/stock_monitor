"""ORM 模型集合。导入时把全部模型注册到 Base.metadata。"""
from app.models.alert_rule import AlertRule
from app.models.watchlist import Watchlist

__all__ = ["Watchlist", "AlertRule"]
