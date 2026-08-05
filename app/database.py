"""SQLAlchemy 引擎、会话与声明性基类。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import DATABASE_URL

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
    """启动时建表。仅做骨架阶段使用，后续接 Alembic 后应改用迁移。"""
    # 导入模型以触发 metadata 注册
    from app.models import alert_rule, watchlist  # noqa: F401

    Base.metadata.create_all(bind=engine)
