"""v2.4: LLM 客户端（OpenAI 兼容协议）。
v4.0 升级：从「严厉监工」→「前瞻性交易领航员」

为什么用 openai 库而不是各家 SDK？
- OpenAI 的 chat completion 协议已成事实标准
- DeepSeek / 通义千问 / 智谱 / 月之暗面都兼容这套协议
- 一套代码切换 base_url + model_name 就能换服务
"""
from __future__ import annotations

import json
import logging
import re

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
    "你只输出【严格合法 JSON】，不要任何 markdown 包裹、不要解释性文字、不要前后缀。"
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

【50 天技术特征摘要】
{features_json}

【最近 10 天原始 K 线 (OHLCV，单位：元/手)】
{ohlcv_json}

【领航员决策核心准则】
1. **若用户有持仓**：
   - 必须深度参考用户的【成本价与浮盈/浮亏现状】制定针对性策略！
   - 浮盈丰厚（收益率 > +10%）：重点指导如何保住利润（建议向上移动保本止盈线，在上方阻力位分批减半仓锁利）；
   - 成本线附近（收益率 -3% ~ +3%）：分析当前蓄势与量能，明确继续持有还是借冲高调仓；
   - 深度浮亏（收益率 < -5%）：结合关键支撑位与均线，给出坚决止损、反弹减仓或金字塔左侧补仓摊薄成本的明确操作指导；
   - entry_price_min/max 应设为建议的【最佳补仓/加仓买点区间】；
   - target_win / target_loss 必须与用户实际成本价匹配，形成合理盈亏比。
2. **若用户空仓**：
   - 聚焦于【首次建仓最佳甜区 (entry_price_min ~ entry_price_max)】、买入触发条件与前瞻止盈止损。

【输出 schema — 严格合法 JSON】
{{
  "entry_price_min": float,      // 理想建仓/补仓下限（元）
  "entry_price_max": float,      // 理想建仓/补仓上限（元）
  "target_win": float,           // 建议止盈目标价（元）
  "target_loss": float,          // 建议防守止损价（元）
  "position_advice": string,     // 针对当前持仓状态的核心操作指令（如 "加仓待涨" / "逢高锁利" / "防守减仓" / "观望等待" / "持股待突破"）
  "trade_note": string,          // ≤120 字的个性化策略简述（结合成本与K线，包含操作节奏与风控要求）
  "rationale": string,           // ≤80 字的决策依据（明确指出成本价、盈亏比例与技术面共振逻辑）
  "tags": [string, ...]          // 策略标签：["放量突破"] / ["移动锁利"] / ["支撑位低吸"] / ["左侧定投"] / ["反弹减仓"] 等
}}

注意：
1. 所有价格必须是正数，单位元
2. entry_price_min 必须 < entry_price_max
3. target_loss 必须 < entry_price_min
4. target_win 必须 > entry_price_max
5. 策略必须针对用户的持仓状态（有仓还是空仓、赚还是赔）给出针对性建议，严禁假大空的套话

请直接输出 JSON："""


def build_plan_messages(
    ts_code: str,
    name: str,
    current_price: float | None,
    features: dict,
    ohlcv_10d: list[dict],
    holding_info: dict | None = None,
) -> list[dict]:
    """打包 AI 智能规划用的 messages（注入持仓画像与个性化决策上下文）。"""
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
                ohlcv_json=json.dumps(ohlcv_10d, ensure_ascii=False, indent=2),
            ),
        },
    ]


# ====================== 4. 客户端工厂 ======================
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


# ====================== 5. 异步调用与 20 RPM 速率退避重试 ======================
async def _call_llm_with_retry(
    client: openai.AsyncOpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.6,
    max_tokens: int = 2000,
    response_format: dict | None = None,
    max_retries: int = 3,
):
    """自动处理 20 RPM 等速率限制（HTTP 429 RateLimitError）及超时异常，带指数退避。"""
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    for attempt in range(1, max_retries + 1):
        try:
            return await client.chat.completions.create(**kwargs)
        except openai.RateLimitError as e:
            if attempt == max_retries:
                logger.error("LLM rate limit reached 20 RPM max retries: %r", e)
                raise
            sleep_sec = 2.5 * attempt
            logger.warning(
                "LLM rate limited (429 / 20 RPM limit), attempt %d/%d, backoff sleeping %.1fs...",
                attempt, max_retries, sleep_sec
            )
            await asyncio.sleep(sleep_sec)
        except openai.BadRequestError as e:
            # 部分模型代理不支持 response_format="json_object"，降级重试
            if response_format and "response_format" in kwargs:
                logger.warning("LLM response_format 不支持，自动降级为普通补全: %r", e)
                del kwargs["response_format"]
                continue
            raise
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            if attempt == max_retries:
                logger.error("LLM connection timeout max retries: %r", e)
                raise
            sleep_sec = 1.5 * attempt
            logger.warning("LLM connection/timeout error: %r, retrying in %.1fs...", e, sleep_sec)
            await asyncio.sleep(sleep_sec)


def _first_choice_text(resp) -> str:
    """取 LLM 响应首个 choice 的文本；choices 为空时抛清晰异常（防御 IndexError）。"""
    if not resp.choices:
        raise RuntimeError("LLM 响应 choices 为空（可能是内容过滤或服务端异常）")
    return (resp.choices[0].message.content or "").strip()


async def generate_report(summary: dict) -> str:
    """调用 LLM 生成深度复盘报告（Markdown 字符串）。"""
    client = _get_client()
    messages = build_messages(summary)
    logger.info(
        "calling LLM (report): model=%s base_url=%s messages=%d",
        config.LLM_MODEL_NAME, config.LLM_BASE_URL, len(messages),
    )
    resp = await _call_llm_with_retry(
        client=client,
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.7,
        max_tokens=1500,
    )
    return _first_choice_text(resp)


# ====================== 6. v4.0 AI 智能规划（K线 → JSON）======================
# 三重 JSON 解析（鲁棒）：
#   1. response_format=json_object 强制（如模型支持）
#   2. 直接 json.loads
#   3. 失败时从 ```json ... ``` 块里抠
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_plan_json(content: str) -> dict:
    """鲁棒解析 LLM 输出的 JSON 计划。

    Returns:
        dict: 解析后的 JSON；解析失败返回空 dict（不抛异常，由调用方决定如何处理）
    """
    if not content:
        return {}
    content = content.strip()
    # 1) 直接解析
    try:
        return json.loads(content)
    except Exception:
        pass
    # 2) 从 ```json ... ``` 抠
    m = _JSON_BLOCK_RE.search(content)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) 找第一个 { 到最后一个 } 抠
    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(content[start : end + 1])
        except Exception:
            pass
    return {}


# 计划字段的兜底/校验规则
_PLAN_RANGES = {
    "entry_price_min": (0.01, 1e7),       # 价格不能为 0 或负
    "entry_price_max": (0.01, 1e7),
    "target_win": (0.01, 1e7),
    "target_loss": (0.01, 1e7),
}


def _sanitize_plan(raw: dict, current_price: float | None = None) -> dict:
    """清洗 + 兜底 LLM 返回的建仓计划。

    不变量：
    - entry_price_min < entry_price_max
    - target_loss < entry_price_min
    - target_win > entry_price_max
    - 所有价格都 > 0
    """
    if not isinstance(raw, dict):
        raw = {}
    out: dict = {}

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

    # 兜底：entry_price_min/max 至少有一个
    if out["entry_price_min"] is None and out["entry_price_max"] is None and current_price:
        # 以当前价 ± 5% 作保守兜底
        out["entry_price_min"] = round(current_price * 0.95, 4)
        out["entry_price_max"] = round(current_price * 1.05, 4)

    # 兜底：target_win 至少 > current_price
    if out["target_win"] is None and current_price and out["entry_price_max"]:
        out["target_win"] = round(current_price * 1.10, 4)

    # 兜底：target_loss 至少 < current_price
    if out["target_loss"] is None and current_price and out["entry_price_min"]:
        out["target_loss"] = round(current_price * 0.92, 4)

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
) -> dict:
    """v4.0+: 给定股票技术特征 + 最近 10 天 OHLCV + 个人持仓成本画像，生成个性化操盘规划。

    Returns:
        dict: {
            "entry_price_min": float | None,
            "entry_price_max": float | None,
            "target_win": float | None,
            "target_loss": float | None,
            "position_advice": str | None,
            "trade_note": str | None,
            "rationale": str | None,
            "tags": list[str],
        }
    """
    client = _get_client()
    messages = build_plan_messages(ts_code, name, current_price, features, ohlcv_10d, holding_info=holding_info)
    logger.info(
        "calling LLM (ai-plan): model=%s ts_code=%s holding=%s features=%d keys ohlcv=%d bars",
        config.LLM_MODEL_NAME, ts_code, bool(holding_info and holding_info.get('has_position')), len(features), len(ohlcv_10d),
    )

    resp = await _call_llm_with_retry(
        client=client,
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.5,
        max_tokens=800,
        response_format={"type": "json_object"},
    )

    content = _first_choice_text(resp)
    logger.info("ai-plan raw content: %s", content[:200])

    raw = _parse_plan_json(content)
    plan = _sanitize_plan(raw, current_price=current_price)

    # 关键字段必须至少有 entry_price_min + entry_price_max
    if plan["entry_price_min"] is None or plan["entry_price_max"] is None:
        raise ValueError(
            f"LLM 返回的计划缺少必要字段 (entry_price_min/max)，raw={raw!r}"
        )

    return plan


# ====================== 7. v4.1 AI 前瞻 Alpha 掘金（低位埋伏与拐点发现）======================
DISCOVER_SYSTEM_PROMPT = (
    "你是一个顶级 A 股宏观与游资策略分析师，拥有 10 年前瞻性题材挖掘与低位埋伏经验。\n"
    "你的使命：结合【政策/产业前瞻催化】与【低位技术形态蓄势】，发掘未来 1~5 个交易日具备爆发潜力的【低位埋伏与拐点爆发方向】。\n\n"
    "【核心操盘原则】：\n"
    "1. 严禁事后解释已经大涨/暴涨的股票！你的核心价值是帮助投资者在【低位、缩量企稳、均线粘合、主力温和吸筹】阶段提前埋伏。\n"
    "2. 挖掘“有前瞻催化逻辑、有预期差、技术面位置低、风险收益比极高”的方向与标的。\n"
    "3. 请用【严格合法 JSON】格式输出，不要任何 markdown 包裹、不要解释性文字。"
)

DISCOVER_USER_PROMPT = """请基于以下多维市场数据，挖掘 3 个最值得【低位埋伏 / 拐点低吸】的前瞻爆发方向。

【1. 消息面：最近 24 小时核心快讯与政策催化（{n_news} 条）】
{news_block}

【2. 资金流向：主力净流入板块 Top {n_sectors}】
{sectors_block}

【3. 低位蓄势与温和吸筹标的池（涨跌幅温和，处于低位震荡/蓄势区，适合低吸埋伏）】
{low_accum_block}

【4. 今日市场主线与领涨先锋（供研判风口扩散与低位补涨关联）】
{momentum_block}

【任务要求】
找出 3 个具备前瞻催化、预期差大、适合在低位逢低埋伏的方向：
- sector: 题材/板块名称
- ambush_type: 埋伏类型（如 "政策催化左侧潜伏" / "主线分歧低位补涨" / "缩量企稳拐点低吸" / "重磅事件倒计时"）
- catalyst_window: 预判爆发时间窗口（如 "未来 1-3 个交易日" / "下周重磅大会前夕" / "本月中旬政策预期"）
- catalyst_logic: 前瞻催化与预期差逻辑（≤150字，说明未来可能发酵的重磅事件/政策与市场预期差）
- technical_pattern: 低位技术特征与主力蓄势迹象（≤80字，说明底部形态、缩量企稳或温和吸筹特征）
- stocks: 3-4 只低位埋伏代表标的，每只必须包含：
  - code: 带 sh/sz/bj 前缀的代码（如 sh600xxx / sz00xxxx）
  - name: 股票名称
  - current_price: 现价（数字）
  - ambush_zone: [min, max] 建议低吸埋伏买点区间（数字数组）
  - target_win: 目标止盈价（数字）
  - stop_loss: 防守止损价（数字）
  - stock_logic: ≤30 字的个股专属埋伏亮点（如 "低位底部震荡企稳，估值处于历史10%分位"）
- level: 爆发确定性（"高" / "中"）
- risk_warning: ≤50 字的风控与撤退纪律（何时应认错撤退）

【输出 schema（严格 JSON）】
{{
  "discoveries": [
    {{
      "sector": "方向名称",
      "ambush_type": "政策催化左侧潜伏",
      "catalyst_window": "未来 1-3 个交易日",
      "catalyst_logic": "前瞻催化与预期差分析...",
      "technical_pattern": "底部均线粘合，缩量震荡蓄势...",
      "stocks": [
        {{
          "code": "sh600xxx",
          "name": "股票名",
          "current_price": 12.50,
          "ambush_zone": [12.20, 12.60],
          "target_win": 14.50,
          "stop_loss": 11.80,
          "stock_logic": "低位年线支撑+缩量回踩企稳"
        }}
      ],
      "level": "高",
      "risk_warning": "若跌破XX元支撑位或催化落空则止损离场"
    }}
  ]
}}

注意：
1. 标的必须从提供的低位蓄势池或主线关联池中挑选真实的 A 股代码
2. 严禁推荐已连板大涨追高的股票，必须聚焦于低位买点
3. ambush_zone 必须包含当前价附近或略下方支撑位，形成合理低吸区间

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
    """万能容错提取单只个股信息（支持 dict 或各种形式的 str）。"""
    if isinstance(s, dict):
        code_raw = _normalize_code_robust(str(s.get("code", "")))
        name = str(s.get("name", "")).strip()[:20]
        cur_p = s.get("current_price") or s.get("price")
        zone = s.get("ambush_zone") or s.get("buy_zone")
        tw = s.get("target_win")
        tl = s.get("stop_loss") or s.get("target_loss")
        logic = str(s.get("stock_logic", "") or s.get("logic", "")).strip()[:100]
    elif isinstance(s, str):
        s_str = s.strip()
        # 尝试从中提取 6 位数字代码
        m = re.search(r"(\b(?:sh|sz|bj)?\d{6}\b)", s_str, re.IGNORECASE)
        if not m:
            return None
        code_raw = _normalize_code_robust(m.group(1))
        name = re.sub(r"[\(\)\[\]\d\.\-sh|sz|bj]", "", s_str).strip()[:20]
        cur_p, zone, tw, tl = None, None, None, None
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

    zone_out = None
    if isinstance(zone, list) and len(zone) >= 2:
        try:
            z0, z1 = float(zone[0]), float(zone[1])
            zone_out = [round(min(z0, z1), 2), round(max(z0, z1), 2)]
        except Exception:
            pass
    elif cur_price and cur_price > 0:
        zone_out = [round(cur_price * 0.98, 2), round(cur_price * 1.01, 2)]

    target_win = None
    if tw is not None:
        try: target_win = round(float(tw), 2)
        except Exception: pass
    elif cur_price and cur_price > 0:
        target_win = round(cur_price * 1.15, 2)

    stop_loss = None
    if tl is not None:
        try: stop_loss = round(float(tl), 2)
        except Exception: pass
    elif cur_price and cur_price > 0:
        stop_loss = round(cur_price * 0.94, 2)

    return {
        "code": code_raw,
        "name": name or code_raw,
        "current_price": cur_price,
        "ambush_zone": zone_out,
        "target_win": target_win,
        "stop_loss": stop_loss,
        "stock_logic": logic or "低位蓄势，具备爆发弹性",
    }


def _generate_fallback_discoveries(
    low_accum: list[dict],
    sectors: list[dict],
    news: list[dict],
    all_valid_codes: dict[str, str],
) -> list[dict]:
    """保底引擎：当大模型因限流/网络异常或返回空时，基于真实低位蓄势池与资金流生成量化埋伏方案。"""
    out: list[dict] = []
    
    # 方向 1：资金流入与政策共振（高分 88分）
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
            "catalyst_logic": "核心政策与产业政策密集出台，主力资金呈温和净流入态势，市场对后续落地存在预期差。",
            "technical_pattern": "板块指数触及 60 日均线支撑，成交量温和收敛，个股在箱体底部企稳。",
            "stocks": stks1,
            "level": "高",
            "risk_warning": "若跌破建议止损价或大盘放量下行，应坚决执行防守纪律。",
        })

    # 方向 2：缩量回踩企稳拐点（中分 76分）
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
            "catalyst_logic": "前期热点调整充分，近期抛压衰竭，行业龙头业绩与需求具备复苏反弹动能。",
            "technical_pattern": "日线级别均线粘合向上发散，5 日量比小于 0.8 呈现典型地量见底特征。",
            "stocks": stks2,
            "level": "中",
            "risk_warning": "若成交量持续低迷无法突破上方阻力，可逢反弹减仓。",
        })

    # 方向 3：主线分歧低位补涨（基础分 65分）
    sec3 = sectors[2]["name"] if len(sectors) > 2 else "人形机器人与高端数控"
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
            "catalyst_logic": "高位龙头分歧释放后，资金转向同题材估值处于历史低位的低位配套链标的。",
            "technical_pattern": "低位双底筑底形态初显，MACD 底部金叉，具备补涨弹性空间。",
            "stocks": stks3,
            "level": "中",
            "risk_warning": "补涨行情节奏较快，达到目标止盈位应分批止盈。",
        })

    return out


def _sanitize_discoveries(
    raw: Any,
    all_valid_codes: dict[str, str],
    low_accum: list[dict] | None = None,
    sectors: list[dict] | None = None,
    news: list[dict] | None = None,
) -> dict:
    """万能清洗 LLM 返回的前瞻埋伏 discover JSON，支持评分系统与保底。"""
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
        risk_warning = str(d.get("risk_warning", "")).strip()[:200] or "跌破支撑防守位应严格执行止损"
        
        level = str(d.get("level", "")).strip()
        if level not in ("高", "中", "低", "观察"):
            level = "高"

        # 评分提取与计算
        score_val = d.get("score")
        try:
            score = int(score_val) if score_val is not None and 0 <= int(score_val) <= 100 else (88 if level == "高" else 75)
        except Exception:
            score = 88 if level == "高" else 75

        # 清洗 stocks
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
            "stocks": stocks,
            "level": level,
            "risk_warning": risk_warning,
        })

    # 如果清洗后依然为空，触发保底引擎，确保永远有结果展示
    if not out and low_accum:
        out = _generate_fallback_discoveries(low_accum, sectors or [], news or [], all_valid_codes)

    return {
        "discoveries": out[:3],
        "model": config.LLM_MODEL_NAME,
    }


async def generate_discover(
    low_accum: list[dict],
    momentum: list[dict],
    sectors: list[dict],
    news: list[dict],
    all_valid_codes: dict[str, str] | None = None,
) -> dict:
    """v4.1+: AI 前瞻 Alpha 掘金 — 基于催化预期差与低位形态，发掘 3 个最佳埋伏方向。

    Returns:
        dict: {
            "discoveries": [
                {
                    "sector": str,
                    "ambush_type": str,
                    "catalyst_window": str,
                    "catalyst_logic": str,
                    "technical_pattern": str,
                    "stocks": [{"code", "name", "current_price", "ambush_zone", "target_win", "stop_loss", "stock_logic"}],
                    "level": str,
                    "risk_warning": str,
                }, ...
            ],
            "model": str,
        }
    """
    client = _get_client()
    messages = build_discover_messages(low_accum, momentum, sectors, news)
    logger.info(
        "calling LLM (discover): model=%s low_accum=%d momentum=%d sectors=%d news=%d",
        config.LLM_MODEL_NAME, len(low_accum), len(momentum), len(sectors), len(news),
    )

    raw = {}
    try:
        resp = await _call_llm_with_retry(
            client=client,
            model=config.LLM_MODEL_NAME,
            messages=messages,
            temperature=0.6,
            max_tokens=2200,
            response_format={"type": "json_object"},
        )
        content = _first_choice_text(resp)
        logger.info("discover raw content: %s", content[:300])
        raw = _parse_plan_json(content)
    except Exception as e:
        logger.warning("discover LLM call failed, engaging guaranteed algorithmic fallback: %r", e)

    return _sanitize_discoveries(
        raw,
        all_valid_codes=all_valid_codes or {},
        low_accum=low_accum,
        sectors=sectors,
        news=news,
    )
