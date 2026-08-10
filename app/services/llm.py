"""v2.4: LLM 客户端（OpenAI 兼容协议）。

为什么用 openai 库而不是各家 SDK？
- OpenAI 的 chat completion 协议已成事实标准
- DeepSeek / 通义千问 / 智谱 / 月之暗面都兼容这套协议
- 一套代码切换 base_url + model_name 就能换服务
"""
from __future__ import annotations

import json
import logging

import openai

from app import config

logger = logging.getLogger("llm")


# ====================== 1. Prompt 模板 ======================
SYSTEM_PROMPT = (
    "你是一个顶级的 A 股量化基金经理，有 10 年实盘经验，"
    "对中国 A 股市场的板块轮动、情绪周期、资金行为有深刻洞察。"
    "请用专业、客观、有数据支撑的语气复盘。"
)

USER_PROMPT_TEMPLATE = """请根据以下今日盘后数据，写一篇约 400 字的深度复盘小作文。
要求：
1. 语气客观专业、有洞察力，避免"今日市场普涨普跌"这种废话。
2. 点评大盘情绪是否过热（>70）或冰点（<30），说出你的判断依据。
3. 重点点评异动龙头（涨幅榜/成交榜 Top 3）所属板块的联动效应。
4. 对我的自选股战况（止盈/止损触发 + 盈亏合计 + 收益率）给出 1~2 句纪律性评价。
5. **必须使用 Markdown 格式**（# 标题 / **加粗** / - 列表 / > 引用等），排版要清晰。

数据如下（JSON 格式）：
```json
{summary_json}
```
"""


def build_messages(summary: dict) -> list[dict]:
    """把 daily-summary 数据打包成 messages 列表。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_PROMPT_TEMPLATE.format(
                summary_json=json.dumps(summary, ensure_ascii=False, indent=2)
            ),
        },
    ]


# ====================== 2. 客户端工厂 ======================
def _get_client() -> openai.AsyncOpenAI:
    """每次新建一个 client（避免不同请求间共享状态；连接池照样复用底层 http）。"""
    if not config.LLM_ENABLED:
        raise RuntimeError(
            "LLM 未启用：请在项目根目录的 .env 文件里设置 LLM_API_KEY。"
            "参考 .env.example。"
        )
    return openai.AsyncOpenAI(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        timeout=config.LLM_TIMEOUT_SECONDS,
    )


# ====================== 3. 异步调用 ======================
async def generate_report(summary: dict) -> str:
    """调用 LLM 生成深度复盘报告（Markdown 字符串）。

    错误处理：
    - LLM 未配置（key 为空）→ 抛出 RuntimeError，调用方应当捕获并降级
    - 网络 / 限流 / 余额不足 → 抛出原 openai 异常
    """
    client = _get_client()
    messages = build_messages(summary)
    logger.info(
        "calling LLM: model=%s base_url=%s messages=%d",
        config.LLM_MODEL_NAME, config.LLM_BASE_URL, len(messages),
    )
    resp = await client.chat.completions.create(
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.7,
        max_tokens=900,  # 400 字 + Markdown 标记 + 余量
    )
    content = resp.choices[0].message.content or ""
    return content.strip()
