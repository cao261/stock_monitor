"""应用配置。所有可调参数集中放在这里，方便后续切换 .env / 多环境。"""
from __future__ import annotations

import os
from pathlib import Path

# 加载 .env（如果存在）
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # 没装 python-dotenv 也能跑（用系统环境变量）

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

# ====================== v2.4: LLM 配置 ======================
# 兼容 OpenAI / DeepSeek / 一众 OpenAI-compatible 协议的服务
# 留空 = 不启用 LLM 功能，/ai-report 接口会优雅降级返回提示
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "").strip()
LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").strip()
LLM_MODEL_NAME: str = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo").strip()
# 一些常用预设（用户只要改 MODEL 名字就能切换）：
#   OpenAI:   gpt-4o-mini / gpt-4o / gpt-3.5-turbo
#   DeepSeek: deepseek-chat / deepseek-reasoner
#   通义千问: qwen-turbo / qwen-plus
#   月之暗面: moonshot-v1-8k
#   智谱 GLM: glm-4-flash / glm-4
LLM_ENABLED: bool = bool(LLM_API_KEY)
# v4.3 第二次调整: 120 -> 180, 实测 M2.7 思考 + JSON 完整响应需 113s, 120s + 重试会触发超时
LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "180"))
