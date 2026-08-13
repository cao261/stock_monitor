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
# 教 LLM 看 K 线，输出结构化建仓计划
PLAN_SYSTEM_PROMPT = (
    "你是【前瞻性交易领航员】，擅长从 K 线技术特征中提炼出可执行的建仓计划。\n"
    "你只输出【严格合法 JSON】，不要任何 markdown 包裹、不要解释性文字、不要前后缀。"
)

PLAN_USER_PROMPT_TEMPLATE = """请为以下 A 股生成前瞻建仓计划：

代码：{ts_code}
名称：{name}
当前价：{current_price}

【50 天技术特征摘要】
{features_json}

【最近 10 天原始 K 线 (OHLCV，单位：元/手)】
{ohlcv_json}

【输出 schema — 必须严格遵循，字段顺序不限】
{{
  "entry_price_min": float,      // 理想建仓下限（建议在 60日线 / 20日线 / 近期低点附近）
  "entry_price_max": float,      // 理想建仓上限（建议在 5日线 / 近期阻力位附近）
  "target_win": float,           // 建议止盈（建议在 60日线 上方 / 前高 附近）
  "target_loss": float,          // 建议止损（建议在 entry_price_min 下方 3-5%）
  "trade_note": string,          // ≤100 字的建仓策略简述（含触发条件 + 风险点 + 持有周期）
  "rationale": string,           // ≤50 字的决策依据（让用户能看懂你为什么这么定）
  "tags": [string, ...]          // 技术标签：["放量突破"] / ["缩量企稳"] / ["左侧建仓"] / ["右侧趋势"] 等
}}

注意：
1. 所有价格必须是正数，单位元
2. entry_price_min 必须 < entry_price_max
3. target_loss 必须 < entry_price_min
4. target_win 必须 > entry_price_max
5. trade_note 和 rationale 不要堆砌"风险提示"，要具体可执行
6. 如果数据不足（MA 缺失 / 数据点 < 5），合理外推或给保守建议，不要凭空捏造

请直接输出 JSON："""


def build_plan_messages(
    ts_code: str,
    name: str,
    current_price: float | None,
    features: dict,
    ohlcv_10d: list[dict],
) -> list[dict]:
    """打包 AI 智能规划用的 messages（强制 JSON 输出）。"""
    return [
        {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": PLAN_USER_PROMPT_TEMPLATE.format(
                ts_code=ts_code,
                name=name or "未知",
                current_price=current_price if current_price is not None else "未知",
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


# ====================== 5. 异步调用：复盘战报 ======================
async def generate_report(summary: dict) -> str:
    """调用 LLM 生成深度复盘报告（Markdown 字符串）。

    错误处理：
    - LLM 未配置（key 为空）→ 抛出 RuntimeError，调用方应当捕获并降级
    - 网络 / 限流 / 余额不足 → 抛出原 openai 异常
    """
    client = _get_client()
    messages = build_messages(summary)
    logger.info(
        "calling LLM (report): model=%s base_url=%s messages=%d",
        config.LLM_MODEL_NAME, config.LLM_BASE_URL, len(messages),
    )
    resp = await client.chat.completions.create(
        model=config.LLM_MODEL_NAME,
        messages=messages,
        temperature=0.7,
        # v4.0: max_tokens 维持 1500（500-700 字 + Markdown 余量）
        max_tokens=1500,
    )
    content = resp.choices[0].message.content or ""
    return content.strip()


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

    out["trade_note"] = str(raw.get("trade_note", "")).strip()[:500] or None
    out["rationale"] = str(raw.get("rationale", "")).strip()[:200] or None

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
) -> dict:
    """v4.0: 给定一只股票的技术特征 + 最近 10 天 OHLCV，让 LLM 输出一份前瞻建仓计划。

    Returns:
        dict: {
            "entry_price_min": float | None,
            "entry_price_max": float | None,
            "target_win": float | None,
            "target_loss": float | None,
            "trade_note": str | None,
            "rationale": str | None,
            "tags": list[str],
        }

    错误处理：
    - LLM 未配置 → RuntimeError（让 router 返 503）
    - 网络错误   → 透传原 openai 异常（让 router 返 502）
    - JSON 解析失败 → 走兜底清洗，trade_note / rationale 为 None
    """
    client = _get_client()
    messages = build_plan_messages(ts_code, name, current_price, features, ohlcv_10d)
    logger.info(
        "calling LLM (ai-plan): model=%s ts_code=%s features=%d keys ohlcv=%d bars",
        config.LLM_MODEL_NAME, ts_code, len(features), len(ohlcv_10d),
    )

    # 尝试用 response_format 强制 JSON（OpenAI 官方 / 部分兼容服务支持）
    # 不支持时 catch 后降级为普通 chat completion
    try:
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL_NAME,
            messages=messages,
            temperature=0.5,  # 比复盘稍低（决策要稳）
            max_tokens=800,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logger.warning(
            "ai-plan response_format=json_object 失败，降级为普通调用: %r", e
        )
        resp = await client.chat.completions.create(
            model=config.LLM_MODEL_NAME,
            messages=messages,
            temperature=0.5,
            max_tokens=800,
        )

    content = (resp.choices[0].message.content or "").strip()
    logger.info("ai-plan raw content: %s", content[:200])

    raw = _parse_plan_json(content)
    plan = _sanitize_plan(raw, current_price=current_price)

    # 关键字段必须至少有 entry_price_min + entry_price_max
    if plan["entry_price_min"] is None or plan["entry_price_max"] is None:
        raise ValueError(
            f"LLM 返回的计划缺少必要字段 (entry_price_min/max)，raw={raw!r}"
        )

    return plan
