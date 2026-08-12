"""trade_note 智能解析。

从用户写的自然语言策略里，提取出**可执行的数字**：
- "跌破 1620 就清仓"  →  target_loss = 1620
- "突破 1850 减半仓"  →  target_win = 1850
- "次日不连板清仓"     →  纯语义规则，AI 监工处理
- "网格策略（底仓30%）"  →  纯策略描述，不提取数字

返回结构给前端：
{
    "target_loss": float | None,   # 智能识别的止损价
    "target_win":  float | None,   # 智能识别的止盈价
    "loss_patterns":  [str, ...],  # 命中的止损模式（调试用）
    "win_patterns":   [str, ...],  # 命中的止盈模式
    "semantic_rules": [str, ...],  # 命中的纯语义规则（不提取数字）
}

设计原则：
- **不**替用户做主：如果用户已经显式设置了 target_loss/target_win，前端优先用用户值
- 后端把"提取结果"也带进 response，前端展示"🤖 已从笔记识别止盈 X"
- signals 扫描时，如果用户没设 target_* 但 trade_note 提到数字，用 trade_note 提取值
"""
from __future__ import annotations

import re
from typing import TypedDict


# 止损关键词：跌破/破位/止损/杀跌到/跌穿/...
# 注意：必须在止盈关键词之前匹配；不写单字 "破"（避免被 "突破 18.50" 误命中）
_LOSS_KEYWORDS = (
    r"(?:跌破|破位|止损|杀跌到|杀到|跌到|跌穿|下破|杀穿|止损位|"
    r"防守价|防守位|底部|底部价)"
)
# 止盈关键词
_WIN_KEYWORDS = (
    r"(?:涨破|突破|上破|止盈|目标|涨到|看到|高位|高位价|目标价|止盈价)"
)
# 价格数字（4-5 位整数 + 可选 2 位小数，覆盖 A 股股价范围 0.01 ~ 9999.99）
_PRICE_PATTERN = r"(\d{1,4}(?:\.\d{1,2})?)"

_LOSS_RE = re.compile(_LOSS_KEYWORDS + r"\s*" + _PRICE_PATTERN)
_WIN_RE = re.compile(_WIN_KEYWORDS + r"\s*" + _PRICE_PATTERN)

# 日线数字屏蔽："跌破 5 日线" / "跌破5日线" / "跌破 10 日线" → 不应把 5 / 10 当价格
_DAYLINE_RE = re.compile(r"\d+\s*日线")

# 纯语义规则（不提取数字，但标记"有规则"）
_SEMANTIC_RULES = [
    r"次日不连板",
    r"次日不涨停",
    r"次日开盘",
    r"开盘价",
    r"5日线",
    r"10日线",
    r"20日线",
    r"放量突破",
    r"缩量回踩",
    r"封单",
    r"打板",
    r"格局",
    r"二波",
    r"网格策略",
    r"底仓",
    r"定投",
    r"冰点",
    r"潜伏",
]
_SEMANTIC_RE = re.compile("|".join(_SEMANTIC_RULES))


class TradeNoteParseResult(TypedDict):
    target_loss: float | None
    target_win: float | None
    loss_patterns: list[str]
    win_patterns: list[str]
    semantic_rules: list[str]


def parse_trade_note(text: str | None) -> TradeNoteParseResult:
    """从 trade_note 文本里提取可执行的价格数字。

    不会 raise，所有异常都返回空结果。
    """
    if not text or not text.strip():
        return {
            "target_loss": None,
            "target_win": None,
            "loss_patterns": [],
            "win_patterns": [],
            "semantic_rules": [],
        }

    text = text.strip()

    # 屏蔽"X日线"里的数字（避免"跌破5日线"被误识别为 5 元止损）
    masked_text = _DAYLINE_RE.sub("__DAYLINE__", text)

    # 止损提取
    loss_matches = _LOSS_RE.findall(masked_text)
    target_loss: float | None = None
    if loss_matches:
        try:
            # 取第一个匹配（最相关），并用 float 转换
            v = float(loss_matches[0])
            if 0.01 <= v <= 9999.99:
                target_loss = v
        except (ValueError, TypeError):
            target_loss = None

    # 止盈提取
    win_matches = _WIN_RE.findall(masked_text)
    target_win: float | None = None
    if win_matches:
        try:
            v = float(win_matches[0])
            if 0.01 <= v <= 9999.99:
                target_win = v
        except (ValueError, TypeError):
            target_win = None

    # 纯语义规则（标记"这条 trade_note 是有纪律的"，但无法提取数字）
    semantic_hits = _SEMANTIC_RE.findall(text)

    return {
        "target_loss": target_loss,
        "target_win": target_win,
        "loss_patterns": [m for m in loss_matches],
        "win_patterns": [m for m in win_matches],
        "semantic_rules": semantic_hits,
    }


# ===== 单元自测（直接 python trade_note_parser.py 可跑）=====
if __name__ == "__main__":
    samples = [
        "博弈情绪溢价。核心纪律：次日不连板或跌破5日线，无条件清仓止损，绝不格局！",
        "支撑位缩量低吸。逻辑：基本面反转。不放量跌破支撑线死拿，到前高压力位减半仓。",
        "网格策略（底仓30%）。纪律：每下跌5%加仓1手，每反弹5%卖出1手。忽略短期波动，赚回归的钱。",
        "缩量冰点潜伏，博弈题材二波预期。耐心等待资金回流，一旦触发『放量突破』视封单力度决定去留。",
        "白酒龙头博弈反弹。核心纪律：跌破1620无条件清仓止损，绝不格局！",
        "放量突破 18.50 加仓",
        "破位 1620 杀跌到 1580 止损",
    ]
    for s in samples:
        r = parse_trade_note(s)
        print(f"\n笔记: {s[:50]}...")
        print(f"  → 止盈 {r['target_win']}, 止损 {r['target_loss']}, 语义规则 {r['semantic_rules']}")
