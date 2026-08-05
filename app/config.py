"""应用配置。所有可调参数集中放在这里，方便后续切换 .env / 多环境。"""
from __future__ import annotations

from pathlib import Path

# 项目根目录：stock_monitor/
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite 数据库文件路径
DATABASE_URL = f"sqlite:///{(DATA_DIR / 'stock_monitor.db').as_posix()}"

# 接口元信息
API_TITLE = "A 股量价监控 API"
API_DESCRIPTION = "自选股与告警规则管理的本地后端骨架。"
API_VERSION = "0.1.0"
