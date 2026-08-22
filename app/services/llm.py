"""v2.4: LLM 客户端（OpenAI 兼容协议）。
v4.0 升级：从「严厉监工」→「前瞻性交易领航员」

为什么用 openai 库而不是各家 SDK？
- OpenAI 的 chat completion 协议已成事实标准
- DeepSeek / 通义千问 / 智谱 / 月之暗面都兼容这套协议
- 一套代码切换 base_url + model_name 就能换服务
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import openai

from app import config

logger = logging.getLogger("llm")


# ====================== 1. 系统角色 Prompt ======================
# v4.0: 角色定位从"严厉监工"切换为"前瞻领航员"
# 关键转换：
#   - 去掉"严厉批评"措辞 → 改"客观风险提示"
#   - 去掉"强制监督"姿态   → 改"前瞻性引导"
#   - 主精力放在【寻找未来的机会】而非【审查过去的执行】
#   - 主动给空仓股画【理想建仓甜区】
SYSTEM_PROMPT = (
    "你是一个【前瞻性的交易领航员】，对中国 A 股市场的板块轮动、情绪周期、"
    "资金行为有深刻洞察。\n"
    "请用专业、客观、有数据支撑、有前瞻性的语气复盘。\n\n"
    "【v4.0 角色定位】\n"
    "1. 你不再是【交易纪律执行官】，而是【前瞻性交易领航员】。\n"
    "2. 如果用户没有严格执行交易计划，请理解这可能是：\n"
    "   - 资金安排的原因（仓位还没到位，或要等回款）\n"
    "   - 临时盘感的判断（人脑对盘面的瞬间反应）\n"
    "   - 或单纯还没看到最佳时机\n"
    "   你只需做客观的风险提示，不要反复追问『为什么没止损』。\n"
    "3. 你把主要精力放在【寻找未来的机会】上：\n"
    "   - 哪些板块目前处于【技术利多】状态？\n"
    "   - 哪些自选股虽然没在涨，但已具备【前瞻建仓价值】？\n"
    "   - 哪些持仓股已临近阶段性高点，需要考虑【前瞻止盈】？\n"
    "4. 对用户空仓观望的股票，**主动给出未来的建仓建议**（理想区间 + 触发条件 + 风险提示）。\n"
    "5. 避免「今日市场普涨普跌」这种废话，避免「风险提示」堆砌到无法落地。\n"
)


# ====================== 2. 复盘战报 prompt ======================
USER_PROMPT_TEMPLATE = """请根据以下今日盘后数据，写一篇约 500-700 字的前瞻性复盘小作文。
要求：
1. 语气客观专业、有洞察力、有前瞻性。避免"今日市场普涨普跌"这种废话。
2. 点评大盘情绪是否过热（>70）或冰点（<30），说出你的判断依据。
3. 重点点评异动龙头（涨幅榜/成交榜 Top 3）所属板块的联动效应，给出【技术利多/利空】标签。
4. 对我的自选股战况（止盈/止损触发 + 盈亏合计 + 收益率）给出 1~2 句【前瞻性】提示
   （不再纠结"为什么没止损"，而是"接下来怎么看"）。
5. **必须使用 Markdown 格式**（# 标题 / **加粗** / - 列表 / > 引用等），排版要清晰。
6. 【领航员重点】请务必仔细阅读每只持仓股的 trade_note / target_win / target_loss / entry_price_min-max
   （v4.0 新增理想建仓区间）四个字段，结合今日收盘价和触发的信号。
7. 【温和执行】对用户的持仓与计划：
   - 如果 trade_note 写了"跌破止损价就清仓"，但今日收盘价已破止损线，且用户持仓未平
     → **做客观风险提示**（一句话点出"已破位但未止损"这个事实 + 风险点），不要严厉批评。
   - 如果 is_stop_loss / is_take_profit 已触发，说明用户严格执行了纪律
     → **给出专业级别的肯定**。
   - 如果用户无 trade_note / entry_price_min-max → 主动建议"为这只股设置一个前瞻建仓区间"。
8. 【v2.6.2 保留】请同时检查每只持仓股的 note_semantic_rules 数组。判断用户声明的策略
   （如"次日不连板" / "放量突破" / "缩量回踩"）是否被市场兑现。如兑现，温和提醒按计划行动。
9. 【v3.0 保留：真实交割单审阅】请重点审阅 `today_trades.trades` 数组 + `total_realized_pnl`。
   4 个分支：
   - **盈利兑现夸奖**：SELL realized_pnl > 0，主动落袋是好习惯，值得点名表扬。
   - **亏损止损客观**：SELL realized_pnl < 0，承认这是"成熟的亏损"；若无纪律依据则温和提示。
   - **无操作 = 耐心持仓**：today_trades.trades 是空数组 → 不要反复追问"为何不动"，
     反而应该表扬用户的耐心（持仓不动本身就是一种策略选择）。
   - **过度交易预警**：今日 BUY+SELL ≥ 4 → 旗帜鲜明地警告频繁交易成本吃掉收益。
   - **多档定投买入 OK**：连续多笔 BUY 是按计划加仓，正面肯定；提醒"检查最后一次加仓后仓位"。
10.【v4.0 新增：前瞻机会扫描】—— 领航员核心职责：
   - 扫描 `watchlist_battle.no_position` 数组里的空仓股（**这些是被用户遗忘或主动留作观察池的**），
     **主动给出**每只股的前瞻建议：
     a) 目前的技术形态（MA 趋势 / 成交量 / 支撑阻力）；
     b) 推荐的【理想建仓区间】(entry_price_min, entry_price_max)；
     c) 触发建仓的【明确条件】（如"回踩 60 日线 + 量比小于 0.8 缩量企稳"）；
     d) 配套的【止盈位 / 止损位】建议；
     e) 这只股的【技术利多/利空】标签（"放量突破" / "缩量企稳" / "左侧建仓" / "破位下行" 等）。
   - 优先级：对自选股越少/越冷门的，越要给前瞻（用户可能忘了关注）。

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


# ====================== 3. v4.0 AI 智能规划 prompt（JSON 输出）======================
# 教 LLM 结合 K 线 + 真实持仓成本与盈亏画像，输出高度个性化的操盘计划
PLAN_SYSTEM_PROMPT = (
    "你是【前瞻性交易领航员】，擅长结合技术面 K 线形态与用户的【真实持仓成本与盈亏画像】"
    "提炼出高度个性化、有深度、可执行的操盘规划与风控决策。\n"
    "你只输出【严格合法 JSON】，不要任何 markdown 包裹、不要解释性文字、不要前后缀。\n\n"
    "【关键原则 v4.3：支撑位/压力位必须基于真实技术面】\n"
    "1. 我们已经喂给你【精准量化引擎】算出的【真实支撑位 support_price】【真实压力位 resistance_price】\n"
    "   以及【波动类型 volatility_tag】和【埋伏区间 ambush_zone】。\n"
    "2. 你的 entry_price_min / entry_price_max **必须基于 ambush_zone** 来微调（可向两端各扩 0.5%~1.5%）；\n"
    "3. 你的 target_win **应参考 resistance_price**（取 max(resistance_price, current_price * 1.05)），\n"
    "   **不要**给低于压力位的目标止盈（否则没有突破压力就没有上涨空间）；\n"
    "4. 你的 target_loss **应贴近 support_price**（取 max(stop_loss_engine, support_price * 0.97)），\n"
    "   **不要**设得太紧（用户因洗盘被洗掉）或太松（止损 > 5% 没意义）。\n"
    "5. 【务必】在 rationale 里明确写出「支撑 ¥X / 压力 ¥Y / 当前价 ¥Z 距离支撑 Z% 距离压力 Z%」\n"
    "   让用户一眼看清攻防位置。\n"
    "6. 【务必】在 trade_note 末尾或前面，加一句明确的「🛡️ 关键支撑 ¥X / 🏔️ 第一压力 ¥Y」。"
)

PLAN_USER_PROMPT_TEMPLATE = """请为以下 A 股标的生成个性化前瞻操盘规划：

标的代码：{ts_code}
标的名称：{name}
当前现价：{current_price}

【用户专属持仓与风控画像】
- 持仓状态：{holding_status}
- 持仓成本价：{cost_price_str}
- 持仓数量：{position_str}
- 浮动盈亏金额：{floating_pnl_str}
- 当前盈亏比例：{return_rate_str}
- 原有交易备忘：{existing_trade_note}
- 原有止盈设定：{existing_target_win}
- 原有止损设定：{existing_target_loss}

【50 天技术特征摘要（MA / ATR / 趋势 / 量能）】
{features_json}

【🎯 精准量化技术面（核心参考）—— 真实支撑/压力/ATR 引擎已为你算好】
- 真实关键支撑位: ¥{support_price} ({support_name})
- 真实第一压力位: ¥{resistance_price} ({resistance_name})
- ATR 14日波动率: ¥{atr}（单日/单根 K 线的平均真实波幅）
- 20日波动率: {volatility_pct}%
- 波动类型: {volatility_tag}
- 建议埋伏区间: [¥{ambush_zone_lo} ~ ¥{ambush_zone_hi}]
- 引擎建议止盈: ¥{engine_target_win}
- 引擎建议止损: ¥{engine_stop_loss}
- 引擎技术面解读: {technical_basis}

→ 你必须把上面的"精准量化"作为建仓甜区/止盈止损的【硬约束】，不要随意脱离。

【最近 10 天原始 K 线 (OHLCV，单位：元/手)】
{ohlcv_json}

【领航员决策核心准则】
1. **若用户有持仓**：
   - 必须深度参考用户的【成本价与浮盈/浮亏现状】+【真实支撑/压力位】制定针对性策略！
   - 浮盈丰厚（收益率 > +10%）：重点指导如何保住利润（在第一压力位阻力区 ¥{resistance_price} 分批减半仓锁利）
   - 成本线附近（收益率 -3% ~ +3%）：分析当前距支撑 ¥{support_price} 与压力 ¥{resistance_price} 的位置，明确继续持有还是借冲高调仓
   - 深度浮亏（收益率 < -5%）：评估当前价距支撑 ¥{support_price} 还差多少，给出止损/补仓摊薄/观望的明确操作
   - entry_price_min/max 应贴近【引擎埋伏区间】，但可根据用户成本做 ±1% 微调
2. **若用户空仓**：
   - 聚焦于【首次建仓最佳甜区】—— entry_price_min/max 必须以【引擎埋伏区间】为锚点 ±1%
   - target_loss 紧贴真实支撑位下方（建议 ¥{engine_stop_loss} 附近）
   - target_win 锁定第一压力位（建议 ¥{resistance_price} 上方 0~3%）

【输出 schema — 严格合法 JSON】
{{
  "entry_price_min": float,      // 理想建仓/补仓下限（元，贴近引擎埋伏区间）
  "entry_price_max": float,      // 理想建仓/补仓上限（元，贴近引擎埋伏区间）
  "target_win": float,           // 建议止盈目标价（元，参考压力位）
  "target_loss": float,          // 建议防守止损价（元，参考支撑位）
  "support_price": float,        // 复制引擎算出的真实支撑位
  "resistance_price": float,     // 复制引擎算出的真实压力位
  "volatility_tag": string,      // 复制波动类型标签（如 "📈 稳健成长型"）
  "position_advice": string,     // 针对当前持仓状态的核心操作指令
  "trade_note": string,          // ≤150 字的个性化策略简述
  "rationale": string,           // ≤120 字的决策依据（明确指出成本、盈亏、技术面共振、关键支撑压力位置）
  "tags": [string, ...]          // 策略标签
}}

注意：
1. 所有价格必须是正数，单位元
2. entry_price_min 必须 < entry_price_max
3. target_loss 必须 < entry_price_min
4. target_win 必须 > entry_price_max
5. support_price / resistance_price 字段**必须直接复制**上面给你的真实值（不要瞎编）
6. strategy 必须针对用户的持仓状态 + 真实支撑压力位给出针对性建议

请直接输出 JSON："""


def build_plan_messages(
    ts_code: str,
    name: str,
    current_price: float | None,
    features: dict,
    ohlcv_10d: list[dict],
    holding_info: dict | None = None,
    ambush_levels: dict | None = None,
) -> list[dict]:
    """打包 AI 智能规划用的 messages（注入持仓画像 + 精准支撑/压力 + 个性化决策上下文）。

    v4.3 新增：ambush_levels 来自 ``analyzer.calculate_stock_ambush_levels``，
    包含真实支撑位/压力位/ATR/波动类型/埋伏区间/引擎止盈止损等，让 LLM 的
    entry_price_min/max/target_win/target_loss 有量化锚点，而不是凭感觉写。
    """
    h = holding_info or {}
    has_pos = h.get("has_position", False)
    cost = h.get("cost_price")
    pos = h.get("position")
    pnl = h.get("floating_pnl")
    ret = h.get("return_rate")

    holding_status = f"已持仓 {pos} 股" if has_pos and pos else ("已持仓" if has_pos else "空仓观望中")
    cost_str = f"¥{cost:.3f} 元/股" if cost is not None else "未设置/无持仓"
    pos_str = f"{pos} 股" if pos is not None else "无持仓 (0 股)"
    pnl_str = f"{'+' if pnl and pnl > 0 else ''}{pnl:.2f} 元" if pnl is not None else "无"
    ret_str = f"{'+' if ret and ret > 0 else ''}{ret:.2f}%" if ret is not None else "无"
    existing_note = str(h.get("existing_trade_note") or "无").strip()
    existing_tw = f"¥{h.get('existing_target_win'):.2f} 元" if h.get("existing_target_win") is not None else "未设"
    existing_tl = f"¥{h.get('existing_target_loss'):.2f} 元" if h.get("existing_target_loss") is not None else "未设"

    # v4.3: 精准量化技术面（引擎输出），容错兜底
    al = ambush_levels or {}
    support_price = al.get("support_price")
    support_name = al.get("support_name") or "动态支撑"
    resistance_price = al.get("resistance_price")
    resistance_name = al.get("resistance_name") or "动态压力"
    atr = al.get("atr")
    vol_pct = al.get("volatility_pct")
    vol_tag = al.get("volatility_tag") or "波动类型未知"
    zone = al.get("ambush_zone") or [None, None]
    eng_tw = al.get("target_win")
    eng_sl = al.get("stop_loss")
    basis = al.get("technical_basis") or "无技术面摘要"

    return [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": PLAN_USER_PROMPT_TEMPLATE.format(
                ts_code=ts_code,
                name=name or "未知",
                current_price=f"{current_price:.2f}" if current_price is not None else "未知",
                holding_status=holding_status,
                cost_price_str=cost_str,
                position_str=pos_str,
                floating_pnl_str=pnl_str,
                return_rate_str=ret_str,
                existing_trade_note=existing_note,
                existing_target_win=existing_tw,
                existing_target_loss=existing_tl,
                features_json=json.dumps(features, ensure_ascii=False, indent=2),
                support_price=f"{support_price:.2f}" if support_price is not None else "未知",
                support_name=support_name,
                resistance_price=f"{resistance_price:.2f}" if resistance_price is not None else "未知",
                resistance_name=resistance_name,
                atr=f"{atr:.2f}" if atr is not None else "未知",
                volatility_pct=f"{vol_pct:.2f}" if vol_pct is not None else "未知",
                volatility_tag=vol_tag,
                ambush_zone_lo=f"{zone[0]:.2f}" if zone and zone[0] is not None else "未知",
                ambush_zone_hi=f"{zone[1]:.2f}" if zone and zone[1] is not None else "未知",
                engine_target_win=f"{eng_tw:.2f}" if eng_tw is not None else "未知",
                engine_stop_loss=f"{eng_sl:.2f}" if eng_sl is not None else "未知",
                technical_basis=basis,
                ohlcv_json=json.dumps(ohlcv_10d, ensure_ascii=False, indent=2),
            ),
        },
    ]


# ====================== 4. 客户端工厂 ======================
class _SlidingWindowLimiter:
    """Process-local request limiter. Agnes accounts use a strict 20 RPM quota."""

    def __init__(self, rpm: int, max_concurrency: int = 1) -> None:
        self.rpm = max(1, rpm)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._blocked_until = 0.0

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= 60:
                    self._timestamps.popleft()
                wait_for = max(0.0, self._blocked_until - now)
                if not wait_for and len(self._timestamps) < self.rpm:
                    self._timestamps.append(now)
                    return
                if not wait_for:
                    wait_for = max(0.01, 60 - (now - self._timestamps[0]))
            await asyncio.sleep(wait_for)

    async def block_for(self, seconds: float) -> None:
        async with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + max(0.0, seconds))


_agnes_limiter = _SlidingWindowLimiter(config.AGNES_RPM, config.AGNES_MAX_CONCURRENCY)


def _get_client(api_key: str | None = None, base_url: str | None = None) -> openai.AsyncOpenAI:
    """Create a bounded-retry OpenAI-compatible client for one configured provider."""
    key = (api_key if api_key is not None else config.LLM_API_KEY).strip()
    url = (base_url if base_url is not None else config.LLM_BASE_URL).strip()
    if not key:
        raise RuntimeError("LLM 未启用：未配置可用的 API Key。")
    return openai.AsyncOpenAI(
        api_key=key,
        base_url=url,
        timeout=config.LLM_TIMEOUT_SECONDS,
        max_retries=0,
    )


def _retry_after_seconds(error: Exception, fallback: float) -> float:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return max(0.0, float(headers.get("retry-after", fallback)))
    except (TypeError, ValueError):
        return fallback


# ====================== 5. 异步调用与 20 RPM 速率退避重试 ======================
async def _call_llm_with_retry(
    client: openai.AsyncOpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 2000,
    response_format: dict | None = None,
    max_retries: int = 2,
    limiter: _SlidingWindowLimiter | None = None,
):
    """Bounded retry with proactive limiter and Retry-After aware backoff."""
    kwargs = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if response_format:
        kwargs["response_format"] = response_format

    for attempt in range(1, max_retries + 1):
        try:
            if limiter:
                await limiter.acquire()
                async with limiter._semaphore:
                    resp = await client.chat.completions.create(**kwargs)
            else:
                resp = await client.chat.completions.create(**kwargs)
            # v4.6.3: 截断自动重试 —— finish_reason=length 时 max_tokens 不够
            # 自动 2x 重试（仅 1 次），避免简洁模式下 M2.7 thinking 块 + JSON 主体 1500+ token 被截
            if (getattr(resp.choices[0], "finish_reason", None) == "length"
                    and kwargs["max_tokens"] < 8000):
                old_max = kwargs["max_tokens"]
                kwargs["max_tokens"] = min(8000, kwargs["max_tokens"] * 2)
                logger.warning("LLM output truncated at max_tokens=%d; retrying with %d",
                               old_max, kwargs["max_tokens"])
                continue
            return resp
        except openai.RateLimitError as e:
            if attempt == max_retries:
                raise
            sleep_sec = _retry_after_seconds(e, min(30.0, 3.0 * (2 ** (attempt - 1)))) + random.uniform(0, 0.5)
            if limiter:
                await limiter.block_for(sleep_sec)
            logger.warning("LLM rate limited, attempt %d/%d; waiting %.1fs", attempt, max_retries, sleep_sec)
            await asyncio.sleep(sleep_sec)
        except openai.BadRequestError as e:
            if response_format and "response_format" in kwargs:
                logger.warning("LLM JSON mode unsupported; retrying without response_format: %r", e)
                del kwargs["response_format"]
                continue
            raise
        except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
            if attempt == max_retries:
                raise
            sleep_sec = min(20.0, 2.0 * (2 ** (attempt - 1))) + random.uniform(0, 0.5)
            logger.warning("LLM transient failure, attempt %d/%d; waiting %.1fs: %r", attempt, max_retries, sleep_sec, e)
            await asyncio.sleep(sleep_sec)


def _first_choice_text(resp) -> str:
    """取 LLM 响应首个 choice 的文本；choices 为空时抛清晰异常（防御 IndexError）。"""
    if not resp.choices:
        raise RuntimeError("LLM 响应 choices 为空（可能是内容过滤或服务端异常）")
    return (resp.choices[0].message.content or "").strip()


def _resolve_max_tokens(caller_default: int) -> int:
    """v4.6.3: 根据 LLM_CONCISE_MODE 解析最终 max_tokens。

    简洁模式（默认开）：用 config.LLM_CONCISE_MAX_TOKENS（1200）
    非简洁模式：用 config.LLM_NORMAL_MAX_TOKENS（2500）
    caller_default 仍可作为"调用方推荐的默认值"传入（取 min 保险）。
    """
    cap = config.LLM_CONCISE_MAX_TOKENS if config.LLM_CONCISE_MODE else config.LLM_NORMAL_MAX_TOKENS
    return min(caller_default, cap)


async def generate_report(summary: dict) -> str:
    """调用 LLM 生成深度复盘报告（Markdown 字符串）。"""
    client = _get_client()
    messages = build_messages(summary)
    logger.info(
        "calling LLM (report): model=%s base_url=%s messages=%d concise=%s",
        config.LLM_MODEL_NAME, config.LLM_BASE_URL, len(messages), config.LLM_CONCISE_MODE,
    )
    resp = await _call_llm_with_retry(
        client=client,
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.7,
        # v4.6.3: 简洁模式下降耗；非简洁模式沿用 2500
        max_tokens=_resolve_max_tokens(2500),
    )
    return _first_choice_text(resp)


# ====================== 6. v4.0 AI 智能规划（K线 → JSON）======================
# 三重 JSON 解析（鲁棒）：
#   1. response_format=json_object 强制（如模型支持）
#   2. 直接 json.loads
#   3. 失败时从 ```json ... ``` 块里抠（对象 {..} 与数组 [..] 都支持）
_JSON_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL
)


def _parse_plan_json(content: str) -> dict:
    """鲁棒解析 LLM 输出的 JSON 计划。

    v4.3+ 适配 thinking model（如 MiniMax M2.7）：
    - 自动剥离 ``<think>...</think>`` 块（thinking model 输出在 JSON 前的思考过程）
    - 再走原 3 重 fallback（直接 json / ```json 块 / 首末 {} 抠）

    v4.4.1：```json 块与首末抠取同时支持【数组】输出（[..]）。
    板块注解提示词要求输出数组，且无 response_format 约束时模型多输出
    ```json [ ... ] ``` —— 旧正则只匹配 {..}，数组全部解析失败。

    Returns:
        dict: 解析后的 JSON；解析失败返回空 dict（不抛异常，由调用方决定如何处理）
    """
    if not content:
        return {}
    content = content.strip()

    # v4.3 兼容 thinking model: 剥离 ``<think>...</think>`` 块（包括跨行）
    #   例如 MiniMax M2.7 / DeepSeek-reasoner 等会输出 <think>...思考过程...</think>
    #   然后才是真正的 JSON
    # v4.6.2 修复: 用 re.DOTALL + 多个 think 块（之前 .*? 最短匹配但需要 IGNORECASE；改为全局
    #   剥离避免遗漏第二个 think 块）
    think_re = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
    content_no_think = think_re.sub("", content).strip()
    if content_no_think != content:
        logger.debug("stripped think block, content shrunk from %d to %d chars", len(content), len(content_no_think))
        content = content_no_think

    # 1) 直接解析
    try:
        return json.loads(content)
    except Exception:
        pass
    # 2) 从 ```json ... ``` 抠（对象与数组）
    m = _JSON_BLOCK_RE.search(content)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) 首末抠取：优先数组 [..]（板块注解场景），失败再试对象 {..}
    for start_ch, end_ch in (("[", "]"), ("{", "}")):
        start = content.find(start_ch)
        end = content.rfind(end_ch)
        if start >= 0 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except Exception:
                continue
    return {}


# 计划字段的兜底/校验规则
_PLAN_RANGES = {
    "entry_price_min": (0.01, 1e7),       # 价格不能为 0 或负
    "entry_price_max": (0.01, 1e7),
    "target_win": (0.01, 1e7),
    "target_loss": (0.01, 1e7),
}


def _sanitize_plan(raw: dict, current_price: float | None = None, ambush_levels: dict | None = None) -> dict:
    """清洗 + 兜底 LLM 返回的建仓计划。

    v4.3: 增加 support_price / resistance_price / volatility_tag 字段提取与兜底
    （以引擎输出为准，LLM 输出为参考）。

    不变量：
    - entry_price_min < entry_price_max
    - target_loss < entry_price_min
    - target_win > entry_price_max
    - 所有价格都 > 0
    """
    if not isinstance(raw, dict):
        raw = {}
    out: dict = {}
    # v4.3: 引擎真实支撑/压力/波动类型（以引擎为权威，LLM 输出为参考），提前到顶部避免
    # 后续 if 块内引用时被 Python 判 UnboundLocalError。
    al = ambush_levels or {}

    def _f(key, default=None):
        v = raw.get(key)
        if v is None or v == "":
            return default
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return default
        lo, hi = _PLAN_RANGES.get(key, (0.01, 1e7))
        if not (lo <= fv <= hi):
            return default
        return round(fv, 4)

    out["entry_price_min"] = _f("entry_price_min")
    out["entry_price_max"] = _f("entry_price_max")
    out["target_win"] = _f("target_win")
    out["target_loss"] = _f("target_loss")

    # v4.3: 兜底首选 — 用引擎算的 ambush_levels（即使 cache miss 也有引擎数据）
    al_sp = al.get("support_price")
    al_rp = al.get("resistance_price")
    al_z = al.get("ambush_zone") or [None, None]

    # 兜底：entry_price_min/max 至少有一个（先 current_price，再 ambush_levels）
    if out["entry_price_min"] is None and out["entry_price_max"] is None:
        if current_price:
            out["entry_price_min"] = round(current_price * 0.95, 4)
            out["entry_price_max"] = round(current_price * 1.05, 4)
        elif al_z[0] is not None and al_z[1] is not None:
            out["entry_price_min"] = al_z[0]
            out["entry_price_max"] = al_z[1]
        elif al_sp is not None:
            out["entry_price_min"] = round(al_sp * 0.99, 4)
            out["entry_price_max"] = round(al_sp * 1.01, 4)

    # 兜底：target_win 至少 > current_price
    if out["target_win"] is None:
        if current_price and out["entry_price_max"]:
            out["target_win"] = round(current_price * 1.10, 4)
        elif al_rp is not None:
            out["target_win"] = round(al_rp * 1.02, 4)
        elif out["entry_price_max"]:
            out["target_win"] = round(out["entry_price_max"] * 1.08, 4)

    # 兜底：target_loss 至少 < current_price
    if out["target_loss"] is None:
        if current_price and out["entry_price_min"]:
            out["target_loss"] = round(current_price * 0.92, 4)
        elif al_sp is not None:
            out["target_loss"] = round(al_sp * 0.96, 4)
        elif out["entry_price_min"]:
            out["target_loss"] = round(out["entry_price_min"] * 0.95, 4)

    # 不变量校验：min < max
    if out["entry_price_min"] and out["entry_price_max"]:
        if out["entry_price_min"] >= out["entry_price_max"]:
            mid = (out["entry_price_min"] + out["entry_price_max"]) / 2
            out["entry_price_min"] = round(mid * 0.97, 4)
            out["entry_price_max"] = round(mid * 1.03, 4)

    # 不变量校验：target_loss < entry_price_min
    if out["target_loss"] and out["entry_price_min"]:
        if out["target_loss"] >= out["entry_price_min"]:
            out["target_loss"] = round(out["entry_price_min"] * 0.95, 4)

    # 不变量校验：target_win > entry_price_max
    if out["target_win"] and out["entry_price_max"]:
        if out["target_win"] <= out["entry_price_max"]:
            out["target_win"] = round(out["entry_price_max"] * 1.08, 4)

    out["position_advice"] = str(raw.get("position_advice", "")).strip()[:50] or None
    out["trade_note"] = str(raw.get("trade_note", "")).strip()[:500] or None
    out["rationale"] = str(raw.get("rationale", "")).strip()[:300] or None

    # v4.3 兜底：当 LLM JSON 解析失败 / 输出空时（价格字段已用 current_price 兜底），
    # 自动生成简要的 position_advice / trade_note / rationale，避免前端展示空白。
    if not out["position_advice"]:
        out["position_advice"] = _default_position_advice(al, current_price)
    if not out["trade_note"]:
        out["trade_note"] = _default_trade_note(al, current_price)
    if not out["rationale"]:
        out["rationale"] = _default_rationale(al, current_price)

    # v4.3: 真实支撑/压力/波动类型（以引擎输出为权威，LLM 输出为参考）
    raw_sp = raw.get("support_price")
    try:
        llm_sp = float(raw_sp) if raw_sp is not None and raw_sp != "" else None
    except (TypeError, ValueError):
        llm_sp = None
    out["support_price"] = llm_sp if (llm_sp and llm_sp > 0) else al.get("support_price")

    raw_rp = raw.get("resistance_price")
    try:
        llm_rp = float(raw_rp) if raw_rp is not None and raw_rp != "" else None
    except (TypeError, ValueError):
        llm_rp = None
    out["resistance_price"] = llm_rp if (llm_rp and llm_rp > 0) else al.get("resistance_price")

    out["volatility_tag"] = str(raw.get("volatility_tag", "")).strip()[:80] or al.get("volatility_tag")

    # tags: list[str]，去空 + 去重
    raw_tags = raw.get("tags", []) or []
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in re.split(r"[,，;；\s]+", raw_tags) if t.strip()]
    elif not isinstance(raw_tags, list):
        raw_tags = []
    out["tags"] = []
    seen = set()
    for t in raw_tags:
        t = str(t).strip()
        if t and t not in seen and len(t) <= 32:
            seen.add(t)
            out["tags"].append(t)

    return out


async def generate_ai_plan(
    ts_code: str,
    name: str,
    current_price: float | None,
    features: dict,
    ohlcv_10d: list[dict],
    holding_info: dict | None = None,
    history_data: list[dict] | None = None,
) -> dict:
    """v4.3+: 给定股票技术特征 + 最近 10 天 OHLCV + 个人持仓成本画像 + 真实支撑/压力位，生成个性化操盘规划。

    v4.3 新增：内部调用 ``analyzer.calculate_stock_ambush_levels`` 算精准支撑位/压力位/ATR/埋伏区间，
    把这些作为【硬锚点】喂给 LLM，让 LLM 的 entry_price_min/max/target_win/target_loss 有真实量化依据。

    Args:
        history_data: 用于内部计算 ambush_levels 的 K 线数据（features 之外的原始数据）

    Returns:
        dict: {
            "entry_price_min": float | None,
            "entry_price_max": float | None,
            "target_win": float | None,
            "target_loss": float | None,
            "support_price": float | None,        # v4.3 新增：复制引擎算出的真实支撑位
            "resistance_price": float | None,     # v4.3 新增：复制引擎算出的真实压力位
            "volatility_tag": str | None,         # v4.3 新增：波动类型标签
            "position_advice": str | None,
            "trade_note": str | None,
            "rationale": str | None,
            "tags": list[str],
        }
    """
    # v4.3: 算精准支撑/压力（也作为响应回传前端，UI 能直接展示真实技术位）
    from analyzer import calculate_stock_ambush_levels
    ambush_levels = calculate_stock_ambush_levels(
        code=ts_code,
        cur_price=current_price,
        history_records=history_data,
    )

    client = _get_client()
    messages = build_plan_messages(
        ts_code, name, current_price, features, ohlcv_10d,
        holding_info=holding_info,
        ambush_levels=ambush_levels,
    )
    logger.info(
        "calling LLM (ai-plan): model=%s ts_code=%s holding=%s features=%d keys ohlcv=%d bars",
        config.LLM_MODEL_NAME, ts_code, bool(holding_info and holding_info.get('has_position')), len(features), len(ohlcv_10d),
    )

    resp = await _call_llm_with_retry(
        client=client,
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.5,
        # v4.6.3: 简洁模式下降耗；非简洁模式沿用 2000
        max_tokens=_resolve_max_tokens(2000),
        response_format={"type": "json_object"},
    )

    content = _first_choice_text(resp)
    logger.info("ai-plan raw content: %s", content[:200])

    raw = _parse_plan_json(content)
    plan = _sanitize_plan(raw, current_price=current_price, ambush_levels=ambush_levels)

    # 关键字段必须至少有 entry_price_min + entry_price_max
    if plan["entry_price_min"] is None or plan["entry_price_max"] is None:
        raise ValueError(
            f"LLM 返回的计划缺少必要字段 (entry_price_min/max)，raw={raw!r}"
        )

    return plan


# ====================== 7. v4.1 AI 前瞻 Alpha 掘金（低位埋伏与拐点发现）======================
# v4.1 个股版 DISCOVER_SYSTEM_PROMPT（fallback 路径在 v4.4 sector 引擎无候选时仍会使用）
DISCOVER_SYSTEM_PROMPT = """你是一名顶尖宏观策略首席分析师与头部游资决策导师，专注于【前瞻低位埋伏、事件驱动催化与拐点左侧博弈】。

【极其重要的核心原则】
1. 严禁事后解释：绝不要解释今天或过去几天已经暴涨、涨停的股票！那没有任何实操价值。
2. 核心任务是【前瞻推演未来 3-10 个交易日具备爆发潜力的低位拐点】：
   - 催化事件倒计时：明确指出未来几天/下周有何尚未被充分定价的行业重磅会议、政策细则落地、产业技术发布会或周期拐点；
   - 市场预期差：深度剖析为什么当前市场尚未充分定价（主力在犹豫什么、散户的认知盲区何在、为什么现在是左侧低吸窗口）；
   - 右侧质变信号：明确指出当出现什么信号（如放量站上压力位/5日均线上穿20日均线）时意味着主升浪启动；
3. 严谨的技术面支撑压力防守：
   - 必须基于输入数据中给出的真实支撑位（如 MA20、箱体底部）、压力位（如箱体上轨、前高阻力）与 ATR 波动率来制定低吸区间、止盈与止损；
   - 股票性质不同（高弹性进攻型 vs 稳健防守型），波动空间不同，绝不可给无意义的假大空建议！

请严格输出 JSON。"""


DISCOVER_USER_PROMPT = """请基于以下真实的多维市场数据（政策催化快讯 + 主力资金流 + 低位标的真实支撑压力与波动属性），前瞻推演 3 个最值得【左侧低位埋伏】的方向。

【1. 消息面：最近 24 小时政策动向与行业事件驱动日程（{n_news} 条）】
{news_block}

【2. 资金流向：主力净流入板块 Top {n_sectors}】
{sectors_block}

【3. 重点低位蓄势标的池（已计算真实技术支撑位、阻力位、ATR波动率与波动类型）】
{low_accum_block}

【推演任务与输出格式要求】
请输出 3 个前瞻埋伏方案，每个方案必须包含：
- sector: 板块/题材方向（如 "商业航天与空天地一体化"）
- score: 0~100 的前瞻埋伏综合评分（如 88 / 76 / 68，综合催化强度与位置安全边际）
- ambush_type: 埋伏策略类型（"政策催化左侧潜伏" / "缩量企稳拐点低吸" / "主线分歧低位补涨" / "重磅事件倒计时"）
- catalyst_window: 预判爆发窗口（如 "未来 1-3 个交易日" / "下周重磅大会前夕" / "本月中旬政策落地窗口"）
- catalyst_logic: 前瞻催化与预期差逻辑（≤150字，剖析未来即将发生的事件、市场认知盲区与预期差）
- technical_pattern: 低位技术蓄势形态（≤80字，结合均线粘合、地量见底、箱体蓄势等特征）
- breakout_trigger: 右侧质变启动信号（≤40字，如 "放量突破箱体上轨或单日成交额放大1.5倍"）
- stocks: 2-3 只低位代表标的，每只必须包含：
  - code: 带 sh/sz/bj 前缀的代码（如 sh600xxx / sz00xxxx）
  - name: 股票名称
  - current_price: 现价
  - ambush_zone: [min, max] 建议低吸买点区间（紧贴关键支撑位）
  - target_win: 目标止盈价（指向第一强阻力/压力位）
  - stop_loss: 防守止损价（有效跌破关键支撑位的硬止损）
  - volatility_tag: 波动属性标签（如 "高弹性标的" 或 "稳健型"）
  - stock_logic: ≤40 字的个股专属埋伏理由与支撑依据（如 "依托20日均线支撑低吸，估值处于历史底部"）
- level: 爆发确定性（"高" / "中"）
- risk_warning: ≤50 字的风控与认错撤退纪律
- tech_indicators: 数组（**v4.3 新增：技术指标明细，3~5 条**，让感兴趣的用户一眼看清技术面为什么利多）：
    [
      {{
        "name": "指标名",
        "value": "数值或趋势描述",
        "signal": "利多/利空/中性",
        "comment": "≤30 字的指标意义说明"
      }},
      ...
    ]
  - 示例：
    [
      {{"name": "MA20 趋势", "value": "1.475 元 (向上)", "signal": "利多", "comment": "股价站上 MA20，短线多头占优"}},
      {{"name": "MACD", "value": "DIF 上穿 DEA", "signal": "利多", "comment": "底部金叉确立"}},
      {{"name": "量能趋势", "value": "近 5 日均量 vs 前 5 日", "signal": "中性", "comment": "缩量企稳，蓄势待发"}},
      {{"name": "20日波动率", "value": "2.8%", "signal": "利多", "comment": "波动收敛，主力吸筹迹象"}},
      {{"name": "连阳/连阴", "value": "3 连阳", "signal": "利多", "comment": "短线动能转强"}}
    ]
- news_highlights: 数组（**v4.3 新增：消息面利好点，2~4 条**，从给定的 {n_news} 条新闻里精挑与本方向强相关的）：
    [
      {{
        "title": "新闻标题（精简）",
        "time": "时间（HH:MM）",
        "source": "新闻源（财联社/新浪等）",
        "why_relevant": "≤40 字解释为什么对本方向利多"
      }},
      ...
    ]
  - 必须从上面给出的【消息面原始清单】里挑，不要自己编造新闻！
  - 选 news 的标准：标题/关键词与本方向板块强相关（题材、政策、产业链、技术突破）

【输出 schema（严格 JSON）】
{{
  "discoveries": [
    {{
      "sector": "方向名称",
      "score": 88,
      "ambush_type": "政策催化左侧潜伏",
      "catalyst_window": "未来 1-3 个交易日",
      "catalyst_logic": "前瞻催化事件分析与市场预期差...",
      "technical_pattern": "底部均线粘合，成交量极度收敛...",
      "breakout_trigger": "放量站上XX元压力位",
      "tech_indicators": [...],
      "news_highlights": [...],
      "stocks": [
        {{
          "code": "sh600xxx",
          "name": "股票名",
          "current_price": 25.50,
          "ambush_zone": [24.80, 25.60],
          "target_win": 29.50,
          "stop_loss": 23.80,
          "volatility_tag": "高弹性进攻型",
          "stock_logic": "以MA20均线支撑为防守位低吸，预期差较大"
        }}
      ],
      "level": "高",
      "risk_warning": "若跌破支撑防守线或政策不及预期坚决止损"
    }}
  ]
}}

请直接输出 JSON："""


def _normalize_code_robust(code: str) -> str:
    """鲁棒归一化 A 股代码：支持 600519、600519.SH、SH600519、sz000001 等各种格式。"""
    c = str(code).strip().lower()
    if "." in c:
        parts = c.split(".")
        if len(parts[0]) == 6 and parts[0].isdigit():
            p1 = parts[1] if parts[1] in ("sh", "sz", "bj") else ""
            c = p1 + parts[0]
        elif len(parts[1]) == 6 and parts[1].isdigit():
            p0 = parts[0] if parts[0] in ("sh", "sz", "bj") else ""
            c = p0 + parts[1]
    c = re.sub(r"[^a-z0-9]", "", c)
    if re.match(r"^(sh|sz|bj)\d{6}$", c):
        return c
    if re.match(r"^\d{6}$", c):
        first = c[0]
        if first in ("6", "9", "5"):
            return "sh" + c
        if first in ("4", "8"):
            return "bj" + c
        return "sz" + c
    return c


def build_discover_messages(
    low_accum: list[dict],
    momentum: list[dict],
    sectors: list[dict],
    news: list[dict],
) -> list[dict]:
    """打包 discover 用的 messages。"""
    def _fmt_low_accum() -> str:
        return "\n".join(
            f"- {(s.get('name') or s.get('code'))} ({s.get('code')}): "
            f"现价 ¥{s.get('price', 0):.2f}, 涨跌 {s.get('change_pct', 0):+.2f}%, "
            f"成交量 {int(s.get('volume', 0)):,}股"
            for s in low_accum[:35]
        )

    def _fmt_momentum() -> str:
        return "\n".join(
            f"- {(m.get('name') or m.get('code'))} ({m.get('code')}): "
            f"+{m.get('change_pct', 0):.2f}%"
            for m in momentum[:20]
        )

    def _fmt_sectors() -> str:
        return "\n".join(
            f"- {s.get('name')}: 净流入 {s.get('net_amount', 0):+.2f}亿, "
            f"领涨 {s.get('leading_stock') or '-'} "
            f"({s.get('leading_change_pct', 0):+.2f}%), "
            f"板块涨跌 {s.get('change_pct', 0):+.2f}%"
            for s in sectors[:15]
        )

    def _fmt_news() -> str:
        lines: list[str] = []
        for n in news[:40]:
            t = n.get("time", "") or "?"
            if "T" in t:
                t = t.split("T", 1)[1][:8]
            title = n.get("title") or n.get("content", "")[:80]
            lines.append(f"- [{t}] {title}")
        return "\n".join(lines)

    return [
        {"role": "system", "content": DISCOVER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": DISCOVER_USER_PROMPT.format(
                low_accum_block=_fmt_low_accum(),
                momentum_block=_fmt_momentum(),
                n_sectors=len(sectors), sectors_block=_fmt_sectors(),
                n_news=len(news), news_block=_fmt_news(),
            ),
        },
    ]


def _extract_stock_item(s: Any, all_valid_codes: dict[str, str]) -> dict | None:
    """万能容错提取单只个股信息，并调用技术分析引擎计算真实支撑压力与波动属性。"""
    if isinstance(s, dict):
        code_raw = _normalize_code_robust(str(s.get("code", "")))
        name = str(s.get("name", "")).strip()[:20]
        cur_p = s.get("current_price") or s.get("price")
        zone = s.get("ambush_zone") or s.get("buy_zone")
        tw = s.get("target_win")
        tl = s.get("stop_loss") or s.get("target_loss")
        logic = str(s.get("stock_logic", "") or s.get("logic", "")).strip()[:100]
        v_tag = str(s.get("volatility_tag", "")).strip()
    elif isinstance(s, str):
        s_str = s.strip()
        m = re.search(r"(\b(?:sh|sz|bj)?\d{6}\b)", s_str, re.IGNORECASE)
        if not m:
            return None
        code_raw = _normalize_code_robust(m.group(1))
        name = re.sub(r"[\(\)\[\]\d\.\-sh|sz|bj]", "", s_str).strip()[:20]
        cur_p, zone, tw, tl, v_tag = None, None, None, None, ""
        logic = "低位蓄势企稳标的"
    else:
        return None

    if not re.match(r"^(sh|sz|bj)\d{6}$", code_raw):
        return None

    if not name and code_raw in all_valid_codes:
        name = all_valid_codes[code_raw]

    try:
        cur_price = float(cur_p) if cur_p is not None else None
    except Exception:
        cur_price = None

    # 调用量化技术面引擎精准分析支撑压力与波动率
    from analyzer import calculate_stock_ambush_levels
    tech = calculate_stock_ambush_levels(code_raw, cur_price=cur_price)

    # 优先采用有效解析区间，缺失时采用技术面计算值
    zone_out = None
    if isinstance(zone, list) and len(zone) >= 2:
        try:
            z0, z1 = float(zone[0]), float(zone[1])
            zone_out = [round(min(z0, z1), 2), round(max(z0, z1), 2)]
        except Exception:
            pass
    if not zone_out:
        zone_out = tech["ambush_zone"]

    target_win = None
    if tw is not None:
        try: target_win = round(float(tw), 2)
        except Exception: pass
    if not target_win:
        target_win = tech["target_win"]

    stop_loss = None
    if tl is not None:
        try: stop_loss = round(float(tl), 2)
        except Exception: pass
    if not stop_loss:
        stop_loss = tech["stop_loss"]

    return {
        "code": code_raw,
        "name": name or code_raw,
        "current_price": cur_price or tech.get("latest_close") or tech.get("support_price"),
        "support_price": tech["support_price"],
        "support_name": tech["support_name"],
        "resistance_price": tech["resistance_price"],
        "resistance_name": tech["resistance_name"],
        "volatility_tag": v_tag or tech["volatility_tag"],
        "technical_basis": tech["technical_basis"],
        "ambush_zone": zone_out,
        "target_win": target_win,
        "stop_loss": stop_loss,
        "stock_logic": logic or tech["technical_basis"],
    }


def _generate_fallback_discoveries(
    low_accum: list[dict],
    sectors: list[dict],
    news: list[dict],
    all_valid_codes: dict[str, str],
) -> list[dict]:
    """保底引擎：当大模型因限流/网络异常或返回空时，基于真实主力资金流与均线支撑生成量化埋伏方案。"""
    out: list[dict] = []
    
    # 方向 1：主力资金流入与政策共振（88分）
    sec1 = sectors[0]["name"] if sectors else "前沿高端制造与商业航天"
    stks1 = []
    for s in low_accum[:3]:
        item = _extract_stock_item(s, all_valid_codes)
        if item:
            stks1.append(item)
    if stks1:
        out.append({
            "sector": sec1,
            "score": 88,
            "ambush_type": "政策催化左侧潜伏",
            "catalyst_window": "未来 1-3 个交易日",
            "catalyst_logic": "核心产业政策预期发酵，主力资金呈持续净流入态势。市场此前因担忧落地节奏存在预期差，当前具备左侧潜伏价值。",
            "technical_pattern": "板块回踩 20 日均线支撑企稳，成交量温和收敛，个股在箱体底部形成多头排列。",
            "breakout_trigger": "放量突破近期箱体上轨压力位并伴随成交量放大 1.3 倍以上",
            "tech_indicators": _build_default_tech_indicators({"stocks": stks1}),
            "news_highlights": _match_news_for_sector(sec1, news) if news else [],
            "stocks": stks1,
            "level": "高",
            "risk_warning": "若跌破各标的关键均线支撑位应严格执行纪律止损。",
        })

    # 方向 2：缩量回踩企稳拐点（76分）
    sec2 = sectors[1]["name"] if len(sectors) > 1 else "半导体与集成电路"
    stks2 = []
    for s in low_accum[3:6]:
        item = _extract_stock_item(s, all_valid_codes)
        if item:
            stks2.append(item)
    if stks2:
        out.append({
            "sector": sec2,
            "score": 76,
            "ambush_type": "缩量企稳拐点低吸",
            "catalyst_window": "未来 3-5 个交易日",
            "catalyst_logic": "前期调整充分，近期抛压衰竭，行业基本面景气度具备向上复苏反弹动能。",
            "technical_pattern": "日线级别均线粘合向上发散，5 日量比小于 0.8 呈现典型地量见底蓄势特征。",
            "breakout_trigger": "5 日均线上穿 20 日均线形成金叉且单日涨幅超过 2.5%",
            "tech_indicators": _build_default_tech_indicators({"stocks": stks2}),
            "news_highlights": _match_news_for_sector(sec2, news) if news else [],
            "stocks": stks2,
            "level": "中",
            "risk_warning": "若成交量持续萎缩无法突破上方第一强阻力，可逢反弹分批减仓。",
        })

    # 方向 3：主线分歧低位补涨（65分）
    sec3 = sectors[2]["name"] if len(sectors) > 2 else "人形机器人与高端母机"
    stks3 = []
    for s in low_accum[6:9]:
        item = _extract_stock_item(s, all_valid_codes)
        if item:
            stks3.append(item)
    if stks3:
        out.append({
            "sector": sec3,
            "score": 65,
            "ambush_type": "主线分歧低位补涨",
            "catalyst_window": "本周中后期",
            "catalyst_logic": "高位龙头分歧调整后，活跃资金分流至同题材估值处于历史低位的配套产业链标的。",
            "technical_pattern": "低位双底筑底形态初显，MACD 底部金叉，具备补涨弹性空间。",
            "breakout_trigger": "板块内出现首板涨停标的带动低位补涨梯队放量启动",
            "tech_indicators": _build_default_tech_indicators({"stocks": stks3}),
            "news_highlights": _match_news_for_sector(sec3, news) if news else [],
            "stocks": stks3,
            "level": "中",
            "risk_warning": "补涨行情轮动速度较快，达到建议目标止盈位应果断止盈。",
        })

    return out


# ====================== v4.3: ai-plan 字段兜底（LLM 输出空/坏 JSON 时）======================
def _default_position_advice(al: dict, current_price: float | None) -> str:
    """当 LLM 没给出 position_advice 时，基于引擎位置自动给一个稳健指令。"""
    sp = al.get("support_price")
    rp = al.get("resistance_price")
    if current_price and sp and rp:
        # 现价在中段 → 观望；近支撑 → 低吸；近压力 → 兑现
        mid = (sp + rp) / 2
        if current_price <= sp * 1.02:
            return "🛡️ 支撑位低吸"
        if current_price >= rp * 0.98:
            return "🏔️ 压力位兑现"
        if current_price < mid:
            return "👀 蓄势低吸"
        return "👀 蓄势等待"
    return "👀 观望等待"


def _default_trade_note(al: dict, current_price: float | None) -> str:
    sp = al.get("support_price")
    rp = al.get("resistance_price")
    if sp and rp:
        return f"🛡️ 关键支撑 ¥{sp:.2f} / 🏔️ 第一压力 ¥{rp:.2f}。在支撑位附近分批低吸，到压力位减半仓，跌破支撑坚决止损。"
    return "建议参考真实技术面（支撑/压力）制定纪律化操作计划"


def _default_rationale(al: dict, current_price: float | None) -> str:
    sp = al.get("support_price")
    rp = al.get("resistance_price")
    if current_price and sp and rp:
        dist_sp = (current_price - sp) / sp * 100
        dist_rp = (rp - current_price) / current_price * 100
        return f"现价 ¥{current_price:.2f}：距真实支撑 ¥{sp:.2f} {dist_sp:+.1f}%，距真实压力 ¥{rp:.2f} 仍有 {dist_rp:+.1f}% 上行空间。盈亏比由真实技术位决定。"
    if sp and rp:
        return f"关键支撑 ¥{sp:.2f}，第一压力 ¥{rp:.2f}，策略应以这两个价位为锚点。"
    return "参考量化引擎算出的真实支撑/压力位制定策略"


# ====================== v4.3: tech_indicators / news_highlights 兜底 ======================
def _build_default_tech_indicators(discovery: dict) -> list[dict]:
    """LLM 没输出 tech_indicators 时，根据 discovery 的 stocks 算几个核心指标作为兜底。"""
    stocks = discovery.get("stocks") or []
    if not stocks:
        return []
    # 取第一只标的的 ambush_levels（如果有），算 MA20 / ATR 等
    stk = stocks[0]
    out: list[dict] = []

    support = stk.get("support_price")
    resistance = stk.get("resistance_price")
    cur = stk.get("current_price")
    vol_tag = stk.get("volatility_tag") or ""

    if cur and support:
        dist = (cur - support) / support * 100 if support > 0 else 0
        sig = "利多" if dist < 5 else ("中性" if dist < 10 else "利空")
        out.append({
            "name": "现价距真实支撑",
            "value": f"{dist:+.1f}%",
            "signal": sig,
            "comment": f"距支撑 ¥{support:.2f} 较近，回踩即埋伏区" if sig == "利多" else f"距支撑 ¥{support:.2f} 已拉开"
        })
    if cur and resistance:
        dist = (resistance - cur) / cur * 100 if cur > 0 else 0
        sig = "利多" if dist > 2 else "中性"
        out.append({
            "name": "上行空间（至第一压力）",
            "value": f"+{dist:.1f}%",
            "signal": sig,
            "comment": f"距离压力 ¥{resistance:.2f} 仍有空间"
        })
    if vol_tag:
        out.append({
            "name": "波动属性",
            "value": vol_tag,
            "signal": "中性",
            "comment": "波动率决定仓位和止盈空间",
        })
    if not out:
        out.append({
            "name": "低位埋伏",
            "value": "缩量企稳",
            "signal": "利多",
            "comment": "处于低位蓄势区间，可考虑分批建仓",
        })
    return out[:5]


def _match_news_for_sector(sector: str, news: list[dict]) -> list[dict]:
    """从原始新闻池按 sector 关键词匹配 2~3 条最相关的消息作为 news_highlights 兜底。

    匹配规则：把 sector 拆成 2~3 字关键词（含滑窗），新闻标题里出现任一关键词就视为相关。
    匹配不到时退化到 news 池的前 2 条作为"近期催化概览"。
    """
    if not sector or not news:
        return []
    import re as _re
    # 1) 按标点切分得到主词
    parts = _re.split(r"[\s,，、/／与和及()()（）]+", sector)
    base_keywords = [p for p in parts if len(p) >= 2][:5]
    # 2) 滑窗拆出 2~3 字子词（处理 "液冷服务器" → "液冷" / "冷却" / "服务器"）
    keywords: set[str] = set(base_keywords)
    for p in base_keywords:
        for wlen in (2, 3):
            for i in range(0, max(0, len(p) - wlen + 1)):
                keywords.add(p[i:i + wlen])
    if not keywords:
        keywords = {sector[:4]}

    out: list[dict] = []
    for n in news:
        title = (n.get("title") or n.get("content", "")[:80]) or ""
        if not title:
            continue
        if any(kw in title for kw in keywords):
            t = n.get("time", "")
            if "T" in t:
                t = t.split("T", 1)[1][:5]
            out.append({
                "title": str(title)[:80],
                "time": t,
                "source": str(n.get("source", "") or "")[:20],
                "why_relevant": f"与「{sector}」题材关键词匹配",
            })
        if len(out) >= 3:
            break

    # 兜底：完全没匹配上时，给 news 池前 2 条作为"近期催化概览"
    if not out:
        for n in news[:2]:
            title = (n.get("title") or n.get("content", "")[:80]) or ""
            if not title:
                continue
            t = n.get("time", "")
            if "T" in t:
                t = t.split("T", 1)[1][:5]
            out.append({
                "title": str(title)[:80],
                "time": t,
                "source": str(n.get("source", "") or "")[:20],
                "why_relevant": "近期市场关注热点（与本方向弱关联，仅供参考）",
            })
    return out


def _sanitize_discoveries(
    raw: Any,
    all_valid_codes: dict[str, str],
    low_accum: list[dict] | None = None,
    sectors: list[dict] | None = None,
    news: list[dict] | None = None,
) -> dict:
    """万能清洗 LLM 返回的前瞻埋伏 discover JSON，支持评分系统、真实支撑压力计算与引擎来源标注。"""
    raw_list: list = []
    if isinstance(raw, list):
        raw_list = raw
    elif isinstance(raw, dict):
        raw_list = (
            raw.get("discoveries")
            or raw.get("data")
            or raw.get("results")
            or raw.get("items")
            or raw.get("directions")
            or raw.get("ambush_directions")
            or []
        )
        if not isinstance(raw_list, list):
            raw_list = []

    out: list[dict] = []
    for d in raw_list[:5]:
        if not isinstance(d, dict):
            continue
        sector = str(d.get("sector", "") or d.get("name", "")).strip()[:50] or "前瞻埋伏方向"
        ambush_type = str(d.get("ambush_type", "")).strip()[:30] or "政策与拐点低吸"
        catalyst_window = str(d.get("catalyst_window", "")).strip()[:30] or "未来 1-3 个交易日"
        catalyst_logic = str(d.get("catalyst_logic", "") or d.get("logic", "")).strip()[:500] or "前瞻催化与政策预期差推演"
        technical_pattern = str(d.get("technical_pattern", "")).strip()[:300] or "低位均线蓄势企稳"
        breakout_trigger = str(d.get("breakout_trigger", "")).strip()[:100] or "放量突破短期关键阻力线"
        risk_warning = str(d.get("risk_warning", "")).strip()[:200] or "跌破支撑防守位应严格执行止损"
        
        level = str(d.get("level", "")).strip()
        if level not in ("高", "中", "低", "观察"):
            level = "高"

        # v4.3: 提取 tech_indicators（技术指标明细），允许 list/dict 多种形态
        tech_indicators: list[dict] = []
        for it in (d.get("tech_indicators") or [])[:6]:
            if isinstance(it, dict):
                nm = str(it.get("name", "")).strip()[:30]
                if not nm:
                    continue
                sig = str(it.get("signal", "中性")).strip()[:10]
                if sig not in ("利多", "利空", "中性"):
                    sig = "中性"
                tech_indicators.append({
                    "name": nm,
                    "value": str(it.get("value", "")).strip()[:50],
                    "signal": sig,
                    "comment": str(it.get("comment", "")).strip()[:80],
                })
            elif isinstance(it, str):
                tech_indicators.append({
                    "name": it.strip()[:30] or "技术指标",
                    "value": "",
                    "signal": "中性",
                    "comment": "",
                })
        # 兜底：如果 LLM 没输出或全被过滤掉，根据 features 自动给 2-3 条
        if not tech_indicators:
            tech_indicators = _build_default_tech_indicators(d)

        # v4.3: 提取 news_highlights（消息面利好点），从原始 news 中按关键词匹配
        news_highlights: list[dict] = []
        for nh in (d.get("news_highlights") or [])[:4]:
            if isinstance(nh, dict):
                ttl = str(nh.get("title", "")).strip()[:80]
                if not ttl:
                    continue
                news_highlights.append({
                    "title": ttl,
                    "time": str(nh.get("time", "")).strip()[:8],
                    "source": str(nh.get("source", "")).strip()[:20],
                    "why_relevant": str(nh.get("why_relevant", "")).strip()[:120],
                })
            elif isinstance(nh, str):
                news_highlights.append({
                    "title": nh.strip()[:80],
                    "time": "",
                    "source": "",
                    "why_relevant": "",
                })
        # 兜底：从原始 news 池中按 sector 关键词匹配 2-3 条
        if not news_highlights and news:
            news_highlights = _match_news_for_sector(sector, news)

        # 评分提取与计算
        score_val = d.get("score")
        try:
            score = int(score_val) if score_val is not None and 0 <= int(score_val) <= 100 else (88 if level == "高" else 75)
        except Exception:
            score = 88 if level == "高" else 75

        # 清洗 stocks 并调用技术分析引擎计算真实支撑压力
        raw_stocks = d.get("stocks", []) or d.get("stock_list", []) or []
        if not isinstance(raw_stocks, list):
            raw_stocks = []
        stocks: list[dict] = []
        seen_codes: set[str] = set()

        for s in raw_stocks[:5]:
            item = _extract_stock_item(s, all_valid_codes)
            if item and item["code"] not in seen_codes:
                seen_codes.add(item["code"])
                stocks.append(item)

        if not stocks:
            continue

        out.append({
            "sector": sector,
            "score": score,
            "ambush_type": ambush_type,
            "catalyst_window": catalyst_window,
            "catalyst_logic": catalyst_logic,
            "technical_pattern": technical_pattern,
            "breakout_trigger": breakout_trigger,
            # v4.3: 用户可点的详情展开区
            "tech_indicators": tech_indicators,
            "news_highlights": news_highlights,
            "stocks": stocks,
            "level": level,
            "risk_warning": risk_warning,
        })

    is_fallback = False
    if not out and low_accum:
        is_fallback = True
        out = _generate_fallback_discoveries(low_accum, sectors or [], news or [], all_valid_codes)

    return {
        "discoveries": out[:3],
        "engine_type": "fallback" if is_fallback else "ai",
        "engine_name": "⚡ 量化规则低位筛选 (兜底引擎)" if is_fallback else f"🤖 AI 深度前瞻研报 ({config.LLM_MODEL_NAME})",
        "engine_desc": (
            "大模型限流或响应异常时，已无缝切换至本地量化规则引擎。基于主力资金净流入、MA20均线支撑与ATR波动率生成。"
            if is_fallback else
            "由大模型深度推演：挖掘 7x24 政策预期差、事件驱动倒计时、右侧启动信号与均线防守空间。"
        ),
        "model": config.LLM_MODEL_NAME,
    }


def _evidence_for_candidate(candidate: dict, news: list[dict]) -> list[dict]:
    """Attach only keyword-matched evidence; unrelated headlines are intentionally omitted."""
    sector = str(candidate.get("sector") or "")
    keywords = {sector[i:i + size] for size in (2, 3) for i in range(max(0, len(sector) - size + 1))}
    evidence: list[dict] = []
    for index, item in enumerate(news):
        title = str(item.get("title") or item.get("content") or "").strip()
        if not title or not any(keyword in title for keyword in keywords):
            continue
        evidence.append({
            "evidence_id": f"news-{index}",
            "title": title[:100],
            "time": str(item.get("time") or "")[:19],
            "source": str(item.get("source") or "")[:30],
            "url": str(item.get("url") or ""),
            "why_relevant": f"标题与「{sector}」关键词匹配",
        })
        if len(evidence) == 3:
            break
    return evidence


def _quantitative_discovery(candidate: dict, news: list[dict]) -> dict:
    technical = candidate["technical"]
    evidence = _evidence_for_candidate(candidate, news)
    score = int(candidate["score"])
    return {
        "sector": candidate["sector"],
        "score": score,
        "quantitative_score": score,
        "score_breakdown": candidate["score_breakdown"],
        "ambush_type": "低位技术回拉观察",
        "catalyst_window": "未来 3-10 个交易日",
        "catalyst_logic": "已有消息证据待核验。" if evidence else "当前无可验证消息催化，作为技术面观察候选。",
        "technical_pattern": "；".join(candidate["selection_reasons"]),
        "breakout_trigger": f"放量站上 ¥{candidate['resistance_price']:.2f} 后确认",
        "tech_indicators": [
            {"name": "MA20", "value": f"¥{technical.get('ma20') or 0:.2f}", "signal": "利多" if technical.get("trend") != "下降" else "中性", "comment": "服务端历史K线计算"},
            {"name": "20日波动率", "value": f"{technical.get('volatility_20d_pct') or 0:.2f}%", "signal": "中性", "comment": "服务端历史K线计算"},
            {"name": "量能趋势", "value": technical.get("volume_trend") or "数据不足", "signal": "利多" if technical.get("volume_trend") == "缩量" else "中性", "comment": "服务端历史K线计算"},
        ],
        "news_highlights": evidence,
        "evidence": evidence,
        "stocks": [{
            key: candidate[key] for key in (
                "code", "name", "current_price", "support_price", "support_name", "resistance_price",
                "resistance_name", "volatility_tag", "technical_basis", "ambush_zone", "target_win", "stop_loss",
            )
        } | {"stock_logic": "；".join(candidate["selection_reasons"]), "technical": technical}],
        "level": "高" if score >= 70 else "中",
        "risk_warning": f"跌破 ¥{candidate['stop_loss']:.2f} 或未能站稳关键支撑时退出。",
        "verification": {"status": "unverified", "risks": [], "referenced_news_ids": []},
    }


def _verification_messages(discoveries: list[dict]) -> list[dict]:
    payload = []
    for item in discoveries:
        stock = item["stocks"][0]
        payload.append({
            "code": stock["code"], "sector": item["sector"], "score": item["score"],
            "technical_pattern": item["technical_pattern"],
            "evidence": [{"evidence_id": e["evidence_id"], "title": e["title"]} for e in item["evidence"]],
        })
    return [
        {"role": "system", "content": "你是A股研究核验助手。只能核验给定候选，不能新增股票、改价格、改评分或编造新闻。只输出 JSON。"},
        {"role": "user", "content": "对每个候选输出 {items:[{code,catalyst_status:confirmed|tentative|none,summary,risks,referenced_news_ids}]}。只可引用输入 evidence_id。\n" + json.dumps(payload, ensure_ascii=False)},
    ]


def _merge_verification(discoveries: list[dict], raw: dict) -> bool:
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return False
    known = {item["stocks"][0]["code"]: item for item in discoveries}
    merged = 0
    for verdict in items:
        if not isinstance(verdict, dict) or verdict.get("code") not in known:
            continue
        target = known[verdict["code"]]
        allowed_ids = {e["evidence_id"] for e in target["evidence"]}
        refs = [str(item) for item in verdict.get("referenced_news_ids", []) if str(item) in allowed_ids]
        status = str(verdict.get("catalyst_status") or "none")
        if status not in {"confirmed", "tentative", "none"}:
            status = "none"
        summary = str(verdict.get("summary") or "").strip()[:240]
        risks = [str(item).strip()[:120] for item in verdict.get("risks", []) if str(item).strip()][:3]
        target["verification"] = {"status": status, "summary": summary, "risks": risks, "referenced_news_ids": refs}
        if summary:
            target["catalyst_logic"] = summary
        if risks:
            target["risk_warning"] = "；".join(risks)
        merged += 1
    return merged > 0


async def generate_discover(candidates: list[dict], news: list[dict]) -> dict:
    """Return deterministic candidates; LLM only attaches constrained evidence/risk annotations."""
    discoveries = [_quantitative_discovery(candidate, news) for candidate in candidates]
    messages = _verification_messages(discoveries)
    providers = []
    if config.MINIMAX_API_KEY:
        providers.append(("minimax", config.MINIMAX_API_KEY, config.MINIMAX_BASE_URL, config.MINIMAX_MODEL, None))
    if config.AGNES_API_KEY and (config.AGNES_API_KEY != config.MINIMAX_API_KEY or config.AGNES_BASE_URL != config.MINIMAX_BASE_URL):
        providers.append(("agnes", config.AGNES_API_KEY, config.AGNES_BASE_URL, config.AGNES_MODEL, _agnes_limiter))

    failures: list[str] = []
    for provider, api_key, base_url, model, limiter in providers:
        try:
            logger.info("calling discover verifier provider=%s model=%s candidates=%d", provider, model, len(discoveries))
            response = await _call_llm_with_retry(
                _get_client(api_key, base_url), model, messages, temperature=0.2,
                # v4.6.3: 简洁模式默认 1200；非简洁模式沿用 DISCOVER_MAX_TOKENS(2200)
                max_tokens=_resolve_max_tokens(config.DISCOVER_MAX_TOKENS),
                response_format={"type": "json_object"},
                limiter=limiter,
            )
            if getattr(response.choices[0], "finish_reason", None) == "length":
                raise RuntimeError("LLM verification output was truncated")
            if _merge_verification(discoveries, _parse_plan_json(_first_choice_text(response))):
                return {
                    "discoveries": discoveries,
                    "engine_type": "ai_verification",
                    "engine_name": f"🤖 量化筛选 + {provider} 核验",
                    "engine_desc": "候选、评分和技术价位由服务端量化计算；模型仅核验消息和风险。",
                    "model": model,
                    "degraded": False,
                }
            raise RuntimeError("LLM verification did not return valid candidate-bound JSON")
        except Exception as error:
            logger.warning("discover verifier failed provider=%s: %r", provider, error)
            failures.append(provider)

    return {
        "discoveries": discoveries,
        "engine_type": "quantitative",
        "engine_name": "⚡ 量化低位筛选",
        "engine_desc": "模型核验不可用，已保留真实行情、历史K线与量化评分结果。",
        "model": None,
        "degraded": True,
        "degraded_reason": "providers_failed:" + ",".join(failures) if failures else "no_provider_configured",
    }


# ====================== 8. v4.4 板块级 Alpha 掘金（LLM 注解） ======================
# 与 v4.1 个股版不同：板块候选、评分、技术价位、新闻证据全部由
# sector_alpha 引擎确定，LLM 只做三件事：
#   1. 基于给定新闻证据提炼前瞻催化逻辑（预期差分析）
#   2. 给出右侧质变启动信号与风险纪律
#   3. 在引擎给定的板块代表股池内补充个股注解（禁止新增/改价）
SECTOR_DISCOVER_SYSTEM_PROMPT = (
    "你是一名顶尖 A 股宏观策略分析师与游资决策导师，专注【低位埋伏、事件催化左侧博弈】。\n"
    "【极其重要的原则】\n"
    "1. 严禁事后解释已经大涨的板块或股票！核心价值是左侧埋伏。\n"
    "2. 候选板块、评分、技术价位、新闻证据全部由量化引擎给出，是事实，你只能注解，"
    "严禁修改、新增或编造。\n"
    "3. 你的注解必须引用【给定的新闻证据】，禁止编造新闻标题或时间。\n"
    "4. **v4.6 关键约束：板块代表股已由 Python 量化引擎完成三层硬过滤 "
    "(MA5/10/20 多头排列 / VWAP 强势承接 / RVOL 1.5~3.5 / 上影线 < 25% / 距 20 日高 < 5%)，"
    "并按角色标记【容量中军】或【弹性先锋】。你**不得新增、替换或删除股票**，只能在给定池内选 1~2 只"
    "并对【已硬过滤】的特征做叙事化解读。\n"
    "5. 你的核心职责是【逻辑利空校验 + 150 字操盘总结】——用新闻证据判断板块是否有"
    "未被引擎识别的利空（监管处罚、业绩暴雷、解禁、减持、虚假宣传等），"
    "若有则调低 level + 增强 risk_warning；若无则给出 catalyst_window 与 break_trigger。\n"
    "6. v4.4 升级：你需要做【三层验证】(T1 消息面真实性 / T2 技术面操盘意图 / T3 跨维度一致性)，"
    "解决量化框架的 3 大盲区：假政策 / 假突破 / 虚假一致。\n"
    # v4.6.3 提速约束：直接给 JSON，不要长篇思考
    "7. **v4.6.3 简洁约束**：直接输出最终 JSON，禁止任何长篇思考或分析铺垫。"
    "不要在 JSON 前写解释性文字、不要写『我分析了...』『让我看看...』这类元话语。"
    "如果非 thinking 模式：直接 [system 约束] -> [user 数据] -> [assistant JSON]。"
    "如果是 thinking 模式：think 块控制在 200 token 以内，主体 JSON 控制在 800 token 以内。\n"
    "请严格输出合法 JSON。"
)

SECTOR_DISCOVER_USER_PROMPT = """请为以下量化引擎筛选出的【低位埋伏板块候选】撰写前瞻注解。

【引擎筛选逻辑说明】
- 板块池：A 股概念题材（390 个），已排除大涨追涨（60日涨幅>25%）、下降未止跌、
  单日过热、流动性不足、资金出逃且无催化。
- 评分 = 左侧位置25 + 缩量止跌20 + 资金回流20 + 消息催化20 + 弹性结构15。
- 所有技术指标（MA20/MA60、60日涨幅、回撤、量能收缩）基于板块指数真实历史K线。

【候选板块（{n_sectors} 个，按评分排序）】
{sectors_block}

【7x24 快讯池（{n_news} 条，注解必须引用，禁止编造）】
{news_block}

【注解任务与输出格式】
对每个候选板块输出一个方案（JSON 数组 discoveries，顺序保持与输入一致），每项包含：
- sector: 板块名（必须与输入完全一致，禁止修改）
- catalyst_logic: 前瞻催化逻辑与预期差（≤150字，引用给定新闻证据；无相关新闻时
  基于板块技术形态与资金面写"技术面左侧逻辑"）
- catalyst_window: 预判爆发窗口（如 "未来3-5个交易日" / "下周"）
- breakout_trigger: 右侧质变启动信号（≤40字，如 "放量突破箱体上轨且成交额放大1.5倍"）
- news_highlights: 数组，从给定快讯池中挑 2~3 条与本板块强相关的：
  {{
    "title": "新闻标题（必须来自快讯池原文，≤80字）",
    "time": "时间（HH:MM 或原样）",
    "source": "新闻源",
    "why_relevant": "≤40字为什么利多"
  }}
  没有强相关新闻可以给空数组，禁止凑数。
- stocks: 数组，从【给定板块代表股池】中选 1~2 只：
  {{
    "code": "代码（必须来自给定池）",
    "name": "名称（必须来自给定池）",
    "stock_logic": "≤40字个股埋伏理由（结合给定支撑/压力位）"
  }}
- level: "高" / "中"（基于催化强度与位置安全边际判断）
- risk_warning: ≤50字的风控与认错撤退纪律
- t1_message: **v4.4 新增 T1 消息面真实性验证** (对象, 必填):
    {{
      "real_sentiment": "positive" | "negative" | "neutral" | "mixed",
      "real_score": 0-100,            // 真实消息面分（覆盖引擎占位 50）
      "confidence": 0.1-1.0,
      "key_catalysts": ["催化剂1", "催化剂2"],   // 1-3 条, 引用给定新闻证据
      "fake_news_risk": "low" | "medium" | "high",   // 假政策风险
      "title_tricks": ["标题潜在误导点1"],          // 可空 []
      "summary": "1-2 句话真实情况总结"
    }}
- t2_technical: **v4.4 新增 T2 技术面操盘意图验证** (对象, 必填):
    {{
      "intent": "accumulation" | "shakeout" | "markup" | "distribution" | "consolidation",
      "real_score": 0-100,            // 真实技术面分
      "confidence": 0.1-1.0,
      "key_resistance": 整数价格,
      "key_support": 整数价格,
      "breakout_fake_risk": "low" | "medium" | "high",
      "fake_break_reasons": ["可能假突破原因1"],
      "next_5d_scenarios": [
        {{"scenario": "情景名", "probability": 0.0-1.0, "target_price": 整数, "trigger": "触发条件"}}
      ],
      "summary": "1-2 句话技术面总结"
    }}
- t3_cross: **v4.4 新增 T3 跨维度一致性验证** (对象, 必填):
    {{
      "coherence_score": 0-100,            // 一致性分 (T1 30% + T2 30% + T3 40% 加权得 final_score)
      "fake_consistency": "low" | "medium" | "high",   // 虚假一致性风险
      "hidden_contradictions": ["矛盾1"],   // 可空 []
      "dimension_alignment": {{
        "msg_capital": "consistent" | "weak" | "contradict",
        "tech_sentiment": "consistent" | "weak" | "contradict"
      }},
      "trustworthiness": "high" | "medium" | "low",   // 整体可信度
      "alerts": ["警示1"],
      "summary": "1-2 句话一致性总结"
    }}
- final_score: 0-100, **综合分** (T1.real_score*0.3 + T2.real_score*0.3 + T3.coherence_score*0.4)
- action: "STRONG_BUY" | "BUY" | "WATCH" | "PASS" (决策)
    - STRONG_BUY: trustworthiness=high 且 final_score>=70
    - BUY: trustworthiness=high 且 final_score>=60
    - WATCH: trustworthiness=medium 且 final_score>=65
    - PASS: 其他

【输出 schema（严格 JSON，只输出数组）】
[
  {{
    "sector": "板块名",
    "catalyst_logic": "...",
    "catalyst_window": "...",
    "breakout_trigger": "...",
    "news_highlights": [...],
    "stocks": [...],
    "level": "高",
    "risk_warning": "...",
    "t1_message": {{"real_sentiment": "positive", "real_score": 78, "confidence": 0.85, "key_catalysts": [...], "fake_news_risk": "low", "title_tricks": [], "summary": "..."}},
    "t2_technical": {{"intent": "accumulation", "real_score": 72, "confidence": 0.80, "key_resistance": 1850, "key_support": 1620, "breakout_fake_risk": "low", "fake_break_reasons": [], "next_5d_scenarios": [...], "summary": "..."}},
    "t3_cross": {{"coherence_score": 85, "fake_consistency": "low", "hidden_contradictions": [], "dimension_alignment": {{...}}, "trustworthiness": "high", "alerts": [], "summary": "..."}},
    "final_score": 79.3,
    "action": "STRONG_BUY"
  }},
  ...
]

请直接输出 JSON："""


def build_sector_discover_messages(
    sectors: list[dict],
    news: list[dict],
) -> list[dict]:
    """打包板块级 discover 注解 messages。"""
    def _fmt_sectors() -> str:
        lines: list[str] = []
        for s in sectors:
            tech = s.get("tech", {})
            ff = s.get("fund_flow") or {}
            metrics = (
                f"60日涨幅 {tech.get('ret_60d')}%, 距高点回撤 {tech.get('drawdown_pct')}%, "
                f"MA20/MA60粘合 {tech.get('ma_bunching_pct')}%, "
                f"量能收缩比 {tech.get('vol_shrink_ratio')}, 止跌={tech.get('stabilized')}, "
                f"趋势={tech.get('trend')}"
            )
            fund = (
                f"主力净额 {ff.get('net_amount')}亿, 领涨股 {ff.get('leading_stock')} "
                f"({ff.get('leading_change_pct')}%), 公司 {ff.get('company_count')}家"
                if ff else "无资金流数据"
            )
            evidence = "；".join(h["title"][:40] for h in s.get("news_hits", [])[:3]) or "无"
            stocks = "；".join(
                f"{st.get('name')}({st.get('code')}) 现价{st.get('current_price')} "
                f"支撑{st.get('support_price')} 压力{st.get('resistance_price')}"
                for st in s.get("stocks", [])
            )
            lines.append(
                f"{s['name']}（评分 {s['score']}，{s['ambush_type']}）\n"
                f"  技术面: {metrics}\n"
                f"  资金面: {fund}\n"
                f"  匹配新闻: {evidence}\n"
                f"  代表股池: {stocks or '无'}"
            )
        return "\n".join(lines)

    def _fmt_news() -> str:
        lines: list[str] = []
        # 注解模型上下文充足（M2.7 支持 1M），7x24 快讯全量喂入，引用更充分
        for n in news[:50]:
            t = n.get("time", "") or "?"
            if "T" in t:
                t = t.split("T", 1)[1][:8]
            title = n.get("title") or n.get("content", "")[:80]
            lines.append(f"- [{t}] {title}")
        return "\n".join(lines)

    return [
        {"role": "system", "content": SECTOR_DISCOVER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": SECTOR_DISCOVER_USER_PROMPT.format(
                n_sectors=len(sectors),
                sectors_block=_fmt_sectors(),
                n_news=len(news),
                news_block=_fmt_news(),
            ),
        },
    ]


def _merge_sector_annotations(
    sectors: list[dict],
    raw_annotations: Any,
) -> tuple[list[dict], int]:
    """把 LLM 注解合并到引擎板块数据上（引擎字段永远优先）。

    返回 (discoveries, matched_count)。matched_count = 拿到有效文本注解
    （catalyst_logic / breakout_trigger / risk_warning 至少一项非空）的板块数，
    用于判定 LLM 注解是否真正生效——LLM 返回空 {} 时 matched_count 为 0。
    """
    from app.services.sector_alpha import sector_to_discovery

    raw_list: list = []
    if isinstance(raw_annotations, list):
        raw_list = raw_annotations
    elif isinstance(raw_annotations, dict):
        raw_list = raw_annotations.get("discoveries") or []

    def _norm_name(name: str) -> str:
        # 归一化板块名：去序号前缀（"1. 英伟达概念" / "1、英伟达概念"）、去引号与空白
        n = str(name or "").strip().strip("\"'“”")
        return re.sub(r"^\d+[\.、\)）]\s*", "", n).strip()

    ann_by_name: dict[str, dict] = {}
    for item in raw_list:
        if isinstance(item, dict) and item.get("sector"):
            ann_by_name[_norm_name(item["sector"])] = item

    out: list[dict] = []
    matched_count = 0
    for sector in sectors:
        discovery = sector_to_discovery(sector)
        ann = ann_by_name.get(sector["name"])
        if not ann:
            # 容错：LLM 名称可能被截断（"英伟达概念" → "英伟达"）或加了前缀
            for norm, candidate in ann_by_name.items():
                if sector["name"].startswith(norm) or norm.startswith(sector["name"]) or norm in sector["name"]:
                    ann = candidate
                    break
        if ann:
            catalyst_logic = str(ann.get("catalyst_logic") or "").strip()[:300]
            if catalyst_logic:
                discovery["catalyst_logic"] = catalyst_logic
            window = str(ann.get("catalyst_window") or "").strip()[:30]
            if window:
                discovery["catalyst_window"] = window
            trigger = str(ann.get("breakout_trigger") or "").strip()[:100]
            if trigger:
                discovery["breakout_trigger"] = trigger
            level = str(ann.get("level") or "").strip()
            if level in ("高", "中", "低"):
                discovery["level"] = level
            risk = str(ann.get("risk_warning") or "").strip()[:200]
            if risk:
                discovery["risk_warning"] = risk

            # news_highlights：LLM 引用必须来自引擎命中新闻（防止编造）
            engine_titles = {h["title"][:80] for h in sector.get("news_hits", [])}
            if ann.get("news_highlights") and engine_titles:
                merged: list[dict] = []
                for nh in ann.get("news_highlights", [])[:4]:
                    if not isinstance(nh, dict):
                        continue
                    title = str(nh.get("title") or "").strip()[:80]
                    if title and any(et in title or title in et for et in engine_titles):
                        merged.append({
                            "title": title,
                            "time": str(nh.get("time") or "")[:8],
                            "source": str(nh.get("source") or "")[:20],
                            "why_relevant": str(nh.get("why_relevant") or "")[:120],
                        })
                if merged:
                    discovery["news_highlights"] = merged

            # stocks 注解：code 必须来自引擎池
            engine_codes = {st["code"] for st in sector.get("stocks", [])}
            llm_stocks = ann.get("stocks")
            if isinstance(llm_stocks, list) and engine_codes:
                by_code = {st["code"]: st for st in sector.get("stocks", [])}
                ordered: list[dict] = []
                seen: set[str] = set()
                for s_item in llm_stocks[:3]:
                    if not isinstance(s_item, dict):
                        continue
                    code = _normalize_code_robust(str(s_item.get("code") or ""))
                    if code not in engine_codes or code in seen:
                        continue
                    seen.add(code)
                    base = dict(by_code[code])
                    logic = str(s_item.get("stock_logic") or "").strip()[:120]
                    if logic:
                        base["stock_logic"] = logic
                    ordered.append(base)
                for st in sector.get("stocks", []):
                    if st["code"] not in seen:
                        ordered.append(st)
                if ordered:
                    discovery["stocks"] = ordered

            # ===== v4.4: 合并 LLM T1/T2/T3 三段验证 + action =====
            t1 = ann.get("t1_message") or {}
            t2 = ann.get("t2_technical") or {}
            t3 = ann.get("t3_cross") or {}
            if isinstance(t1, dict) and t1:
                discovery["llm_verification"]["t1_message"] = {
                    "real_sentiment": str(t1.get("real_sentiment", "neutral")),
                    "real_score": t1.get("real_score", 50),
                    "confidence": t1.get("confidence", 0.5),
                    "key_catalysts": t1.get("key_catalysts", [])[:3] if isinstance(t1.get("key_catalysts"), list) else [],
                    "fake_news_risk": str(t1.get("fake_news_risk", "medium")),
                    "title_tricks": t1.get("title_tricks", []) if isinstance(t1.get("title_tricks"), list) else [],
                    "summary": str(t1.get("summary", ""))[:300],
                }
                # 用 T1 真实验证分覆盖 score_4d.msg
                if t1.get("real_score") and isinstance(t1.get("real_score"), (int, float)):
                    s4 = discovery.get("score_4d", {})
                    s4["msg"] = round(float(t1["real_score"]), 1)
                    s4["total"] = round((s4.get("msg", 50) + s4.get("cap", 50) + s4.get("tech", 50) + s4.get("sent", 50)) / 4.0, 1)
                    s4["grade"] = "A" if s4["total"] >= 80 else ("B" if s4["total"] >= 65 else ("C" if s4["total"] >= 50 else "D"))
            if isinstance(t2, dict) and t2:
                discovery["llm_verification"]["t2_technical"] = {
                    "intent": str(t2.get("intent", "consolidation")),
                    "real_score": t2.get("real_score", 50),
                    "confidence": t2.get("confidence", 0.5),
                    "key_resistance": t2.get("key_resistance", 0),
                    "key_support": t2.get("key_support", 0),
                    "breakout_fake_risk": str(t2.get("breakout_fake_risk", "medium")),
                    "fake_break_reasons": t2.get("fake_break_reasons", []) if isinstance(t2.get("fake_break_reasons"), list) else [],
                    "next_5d_scenarios": t2.get("next_5d_scenarios", []) if isinstance(t2.get("next_5d_scenarios"), list) else [],
                    "summary": str(t2.get("summary", ""))[:300],
                }
            if isinstance(t3, dict) and t3:
                discovery["llm_verification"]["t3_cross"] = {
                    "coherence_score": t3.get("coherence_score", 50),
                    "fake_consistency": str(t3.get("fake_consistency", "medium")),
                    "hidden_contradictions": t3.get("hidden_contradictions", []) if isinstance(t3.get("hidden_contradictions"), list) else [],
                    "dimension_alignment": t3.get("dimension_alignment", {}) if isinstance(t3.get("dimension_alignment"), dict) else {},
                    "trustworthiness": str(t3.get("trustworthiness", "medium")),
                    "alerts": t3.get("alerts", []) if isinstance(t3.get("alerts"), list) else [],
                    "summary": str(t3.get("summary", ""))[:300],
                }
            # final_score = T1.real_score*30% + T2.real_score*30% + T3.coherence_score*40%
            t1s = discovery["llm_verification"]["t1_message"]["real_score"] if discovery["llm_verification"]["t1_message"] else 50
            t2s = discovery["llm_verification"]["t2_technical"]["real_score"] if discovery["llm_verification"]["t2_technical"] else 50
            t3s = discovery["llm_verification"]["t3_cross"]["coherence_score"] if discovery["llm_verification"]["t3_cross"] else 50
            final = t1s * 0.3 + t2s * 0.3 + t3s * 0.4
            trust = discovery["llm_verification"]["t3_cross"]["trustworthiness"] if discovery["llm_verification"]["t3_cross"] else "medium"
            if trust == "high" and final >= 70:
                action = "STRONG_BUY"
            elif trust == "high" and final >= 60:
                action = "BUY"
            elif trust == "medium" and final >= 65:
                action = "WATCH"
            else:
                action = "PASS"
            discovery["llm_verification"]["final_score"] = round(final, 1)
            discovery["llm_verification"]["action"] = action

            if catalyst_logic or trigger or risk or t1 or t2 or t3:
                matched_count += 1

        discovery["verification"] = {"status": "confirmed" if ann else "unverified", "risks": [], "referenced_news_ids": []}
        out.append(discovery)
    return out, matched_count


async def generate_sector_discover(
    sectors: list[dict],
    news: list[dict],
) -> dict:
    """板块候选 → LLM 注解 → 与前端兼容的完整响应。

    LLM 不可用/返回空/失败时降级为纯引擎输出（真实数据，非虚构）。
    """
    from app.services.sector_alpha import sector_to_discovery

    messages = build_sector_discover_messages(sectors, news)
    providers = []
    # agnes 优先：实测稳定（多次服务内调用全成功）；MiniMax-M2.7 的 thinking 模式
    # 输出偶发被平台截断在 <think> 阶段（finish_reason=stop 但无 JSON 正文），留作兜底
    if config.AGNES_API_KEY:
        providers.append(("agnes", config.AGNES_API_KEY, config.AGNES_BASE_URL, config.AGNES_MODEL, _agnes_limiter))
    if config.MINIMAX_API_KEY and (config.MINIMAX_API_KEY != config.AGNES_API_KEY or config.MINIMAX_BASE_URL != config.AGNES_BASE_URL):
        providers.append(("minimax", config.MINIMAX_API_KEY, config.MINIMAX_BASE_URL, config.MINIMAX_MODEL, None))

    failures: list[str] = []
    for provider, api_key, base_url, model, limiter in providers:
        for attempt in (1, 2, 3):
            try:
                logger.info("calling sector discover annotator provider=%s model=%s sectors=%d (attempt %d)",
                            provider, model, len(sectors), attempt)
                response = await _call_llm_with_retry(
                    _get_client(api_key, base_url), model, messages, temperature=0.3,
                    # 不用 response_format=json_object：MiniMax/M2.7 等 thinking 模型在该模式下
                    # 偶发只输出 <think> 思考块就 stop（平台侧不稳定）；自然输出 =
                    # <think> + ```json 块，_parse_plan_json 已兼容。
                    # v4.6.3: 简洁模式默认 1200（之前 8192 是给 thinking 块留余量但 50-80s
                    #   链路太长），非简洁模式沿用 8192
                    max_tokens=_resolve_max_tokens(max(8192, config.DISCOVER_MAX_TOKENS)),
                    limiter=limiter,
                )
                if getattr(response.choices[0], "finish_reason", None) == "length":
                    raise RuntimeError("LLM sector annotation output was truncated")
                content = _first_choice_text(response)
                raw = _parse_plan_json(content)
                if not raw:
                    # 偶发空输出（平台波动/模型抽风）：留痕后重试
                    logger.warning("LLM sector annotation empty: provider=%s raw_content_head=%r",
                                   provider, (content or "")[:200])
                    raise RuntimeError("LLM sector annotation returned empty JSON")
                annotated, matched = _merge_sector_annotations(sectors, raw)
                if annotated and matched >= max(1, len(sectors) // 2):
                    return {
                        "discoveries": annotated,
                        "engine_type": "ai_verification",
                        "engine_name": f"🧭 板块左侧挖掘 + {provider} 催化注解",
                        "engine_desc": "板块、评分、价位、新闻证据由引擎量化计算；模型仅提炼催化逻辑与风险纪律。",
                        "model": model,
                        "degraded": False,
                    }
                raise RuntimeError("LLM sector annotation matched too few sectors (%d/%d)" % (matched, len(sectors)))
            except Exception as error:
                logger.warning("sector discover annotator failed provider=%s attempt=%d: %r", provider, attempt, error)
                if attempt == 3:
                    failures.append(provider)

    discoveries = [sector_to_discovery(s) for s in sectors]
    return {
        "discoveries": discoveries,
        "engine_type": "quantitative",
        "engine_name": "🧭 板块左侧挖掘引擎",
        "engine_desc": "模型注解不可用，已保留真实板块指数K线、资金流、新闻证据与量化评分。",
        "model": None,
        "degraded": True,
        "degraded_reason": "providers_failed:" + ",".join(failures) if failures else "no_provider_configured",
    }
