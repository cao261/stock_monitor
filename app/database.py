"""SQLAlchemy 引擎、会话与声明性基类。"""
from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

# SQLite 单文件场景下需要 check_same_thread=False，便于 FastAPI 多线程访问
engine = create_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)

# autocommit=False + autoflush=False 是 FastAPI 依赖里最稳的搭配
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI Depends 用的会话生成器：每次请求一个 Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """启动时建表 + 跑幂等迁移。

    - create_all: 新建缺失的表（新装环境）
    - migrate_db: 给已有表加新列（升级场景）
    """
    # 导入模型以触发 metadata 注册
    from app.models import alert_rule, watchlist  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_db(engine)


def migrate_db(engine) -> None:
    """幂等迁移：每次启动补齐缺失列。重复运行安全。

    为什么要这个函数：Base.metadata.create_all() 只创建**不存在的表**，
    对**已有表加列**无能为力。SQLite 的 ``ALTER TABLE ADD COLUMN`` 在列已存在时会
    抛 ``OperationalError``，所以我们 catch 后忽略——这样既是首次升级能加上列，
    又是重启时已加过的列不会报错。

    项目规模还小，引入 Alembic 有点重。等 schema 变更频繁 / 需要回滚版本时再迁。

    注意：SQLite 的 ``ALTER TABLE`` **不能修改已有列、不能加 NOT NULL 约束、不能加
    DEFAULT 表达式**。如果以后需要这些操作，得：
      1) 删 ``data/stock_monitor.db``（项目内只有自选股和告警规则，本地数据，删了重输），
      2) 启动时 ``Base.metadata.create_all`` 会按最新 model 重建。
    """
    inspections: list[tuple[str, str]] = [
        # v1.1
        ("cost_price", "ALTER TABLE watchlist ADD COLUMN cost_price FLOAT"),
        ("position",   "ALTER TABLE watchlist ADD COLUMN position   INTEGER"),
        ("trade_note", "ALTER TABLE watchlist ADD COLUMN trade_note VARCHAR(500)"),
        # v1.2
        ("target_win",  "ALTER TABLE watchlist ADD COLUMN target_win  FLOAT"),
        ("target_loss", "ALTER TABLE watchlist ADD COLUMN target_loss FLOAT"),
    ]
    with engine.begin() as conn:
        for col, ddl in inspections:
            try:
                conn.execute(text(ddl))
                logger.info("migration: added column watchlist.%s", col)
            except OperationalError as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue  # 已存在，幂等跳过
                raise
