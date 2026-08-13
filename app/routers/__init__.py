"""路由层。"""
from app.routers.market import router as market_router
from app.routers.watchlist import router as watchlist_router
from app.routers.strategy import router as strategy_router
from app.routers.trade import router as trade_router  # v3.1 资金账本

__all__ = ["watchlist_router", "market_router", "strategy_router", "trade_router"]
