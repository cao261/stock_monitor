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


# ====================== 1. Prompt 模板（v2.5 加入监工人格）======================
SYSTEM_PROMPT = (
    "你是一个顶级的 A 股量化基金经理，有 10 年实盘经验，"
    "对中国 A 股市场的板块轮动、情绪周期、资金行为有深刻洞察。\n"
    "请用专业、客观、有数据支撑的语气复盘。\n\n"
    "【v2.5 角色升级】你不仅是分析师，更是极其严格的【交易纪律执行官】。"
    "对自选股战况的审查必须一针见血——用户写下的交易逻辑就是他的军令状，"
    "违反军令必须被严厉批评，严格执行必须被专业肯定。"
)

USER_PROMPT_TEMPLATE = """请根据以下今日盘后数据，写一篇约 500-700 字的深度复盘小作文。
要求：
1. 语气客观专业、有洞察力，避免"今日市场普涨普跌"这种废话。
2. 点评大盘情绪是否过热（>70）或冰点（<30），说出你的判断依据。
3. 重点点评异动龙头（涨幅榜/成交榜 Top 3）所属板块的联动效应。
4. 对我的自选股战况（止盈/止损触发 + 盈亏合计 + 收益率）给出 1~2 句纪律性评价。
5. **必须使用 Markdown 格式**（# 标题 / **加粗** / - 列表 / > 引用等），排版要清晰。
6. 【监工重点】请务必仔细阅读每只持仓股的 trade_note（交易备忘/逻辑）、
   target_win（止盈价）、target_loss（止损价）三个字段，结合今日收盘价和触发的信号。
7. 【监工执行】逐只审查用户的执行力：
   - 如果 trade_note 写了"跌破止损价就清仓"，但今日收盘价已破止损线，
     且用户持仓未平（is_take_profit / is_stop_loss 都未触发）——
     **必须严厉且不留情面地批评**用户的情绪化扛单行为，措辞要狠。
   - 如果 is_stop_loss / is_take_profit 已触发，说明用户严格执行了纪律，
     **请给予专业级别的肯定**。
   - 如果 trade_note 写明"破位止损"但当前价格已破位未止损，
     比喻可参考："让亏损奔跑是交易的头号大忌" / "市场从不怜悯扛单者"。
   - 如果用户无 trade_note，可简短建议他补一条交易纪律。
8. 【v2.6.2 新增】请同时检查每只持仓股的 note_semantic_rules 数组（从 trade_note 文本里
   识别的"纪律标签"，比如 ["次日不连板", "网格策略", "缩量回踩"] 等共 17 个标签）。
   结合今日走势判断：
   - 用户声明的策略（如"次日不连板" / "放量突破" / "缩量回踩"）在今日是否被市场兑现？
     如果是，提醒用户按计划行动（减仓 / 加仓 / 离场）。
   - 如果策略声明"严格止损"但今日实际未止损（pnl 持续亏损且 stop_loss 未触发），
     用更重的措辞批评。
   - 对 winners / losers Top 5 里的每只持仓，给出"是否值得继续持有 / 应该止盈"
     的一针见血建议（不超过 1 行/只）。
9. 【v3.0 新增：真实交割单审查】请重点审阅 `today_trades.trades` 数组（今日用户通过
   补仓/减仓 Dialog 真实提交的成交记录）以及 `total_realized_pnl`（今日已实现盈亏合计）。
   - **逐笔审查**：每条交易都看一眼 ts_code / action / price / volume / realized_pnl。
     如果用户在 trade_note 里声明"跌破某价就清仓"，而今天的 SELL 是在触发价附近完成的
     （即真按纪律止损了），**请给予专业级别的肯定**，夸得重一点。
   - **盈利兑现夸奖**：如果 SELL 的 realized_pnl > 0，说明用户在浮盈时主动落袋，
     这是大多数交易者做不到的事，值得点名表扬（避免过度交易）。
   - **亏损止损客观**：如果 SELL 的 realized_pnl < 0，但用户严格按 trade_note 纪律执行，
     承认这是"成熟的亏损"（好的亏损 vs 坏的亏损）；如果看不出有纪律依据就止损，
     温和指出"提前止损会侵蚀本金"的复利危害。
   - **无操作 = 耐心持仓**：如果 today_trades.trades 是空数组，**不要**反复追问"为何不动"，
     反而应该表扬用户的耐心（持仓不动本身就是一种策略选择，特别是在震荡市）。
   - **过度交易预警**：如果今日 BUY+SELL 总笔数 ≥ 4，**必须**旗帜鲜明地警告
     "交易是有成本的（佣金 + 滑点 + 认知疲劳）"，并指出"频繁操作累计成本 = X 元
     即可观吃掉 N% 收益"这种量化思维。
   - **多档定投买入 OK**：如果用户是连续多笔 BUY（典型的网格/金字塔加仓），这是按计划
     加仓，**正面肯定**；但提醒"检查最后一次加仓后仓位是否过重"（可参考 position
     字段的现值）。

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
        # v2.5: 监工会更详细批评/肯定，500-700 字 + Markdown 余量
        max_tokens=1500,
    )
    content = resp.choices[0].message.content or ""
    return content.strip()
