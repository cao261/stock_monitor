"""Deterministic candidate selection for the Alpha discovery endpoint.

The LLM may annotate candidates, but never selects stocks or market facts.
"""
from __future__ import annotations

from typing import Any

from analyzer import calculate_stock_ambush_levels, extract_kline_features

MIN_HISTORY_POINTS = 60
PRESELECT_LIMIT = 100
TARGET_CANDIDATES = 5


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _candidate_sector(stock: dict[str, Any]) -> str:
    """Use only source-provided classifications; do not let a model invent one."""
    for key in ("sector", "industry", "concept", "board"):
        value = str(stock.get(key) or "").strip()
        if value:
            return value[:50]
    return "未分类板块"


def _preselect(all_stocks: dict[str, dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Cheap liquidity filter before requesting K-line history."""
    eligible: list[tuple[str, dict[str, Any]]] = []
    for code, stock in all_stocks.items():
        price = _number(stock.get("price"))
        volume = _number(stock.get("volume"))
        change_pct = stock.get("change_pct")
        if price is None or volume is None or volume < 100_000:
            continue
        try:
            change = float(change_pct)
        except (TypeError, ValueError):
            continue
        # Do not chase a single-day surge. Mild pullbacks and early recovery remain eligible.
        if not -4.0 <= change <= 2.5:
            continue
        eligible.append((code, stock))
    return sorted(
        eligible,
        key=lambda item: (-float(item[1].get("volume") or 0), item[0]),
    )[:PRESELECT_LIMIT]


def preselect_codes(all_stocks: dict[str, dict[str, Any]]) -> list[str]:
    """Return bounded symbols whose history should be fetched for Alpha scoring."""
    return [code for code, _ in _preselect(all_stocks)]


def _score_candidate(
    price: float,
    change_pct: float,
    features: dict[str, Any],
    tech: dict[str, Any],
) -> tuple[int, dict[str, int], list[str]] | None:
    ma20 = _number(features.get("ma20"))
    ma60 = _number(features.get("ma60"))
    low20 = _number(features.get("recent_low_20d"))
    high20 = _number(features.get("recent_high_20d"))
    atr = _number(features.get("atr14"))
    volatility = _number(features.get("volatility_20d_pct"))
    if not all((ma20, ma60, low20, high20, atr, volatility)) or high20 <= low20:
        return None

    position = (price - low20) / (high20 - low20)
    atr_pct = atr / price * 100
    support_distance = (price - float(tech["support_price"])) / price * 100
    upside = (float(tech["resistance_price"]) - price) / price * 100
    if position > 0.70 or support_distance > 8 or atr_pct > 7 or upside < 2:
        return None

    position_score = round(max(0, min(30, (0.70 - position) / 0.70 * 30)))
    trend_score = 0
    if price >= ma20 * 0.98:
        trend_score += 12
    if ma20 >= ma60 * 0.96:
        trend_score += 10
    if features.get("trend") != "下降":
        trend_score += 5
    volume_score = {"缩量": 15, "平稳": 9, "放量": 4}.get(features.get("volume_trend"), 0)
    risk_score = round(max(0, min(15, (5.0 - atr_pct) / 5.0 * 15)))
    recovery_score = 8 if -2.5 <= change_pct <= 1.5 else 3
    opportunity_score = round(max(0, min(7, upside)))
    breakdown = {
        "低位位置": position_score,
        "趋势结构": trend_score,
        "量能收敛": volume_score,
        "波动风险": risk_score,
        "短线恢复": recovery_score,
        "上行空间": opportunity_score,
    }
    score = sum(breakdown.values())
    reasons = [
        f"20日区间位置 {position:.0%}",
        f"现价距支撑 {support_distance:+.1f}%",
        f"{features.get('volume_trend', '量能数据不足')}，20日波动 {volatility:.1f}%",
    ]
    return score, breakdown, reasons


def build_quantitative_candidates(
    all_stocks: dict[str, dict[str, Any]],
    histories: dict[str, dict[str, Any]],
    *,
    target_count: int = TARGET_CANDIDATES,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build real, explainable 3-10 trading-day candidates from cached market data."""
    rejected = {"history": 0, "technical": 0, "risk": 0}
    ranked: list[dict[str, Any]] = []
    for code, stock in _preselect(all_stocks):
        history = histories.get(code, {}).get("data") or []
        if len(history) < MIN_HISTORY_POINTS:
            rejected["history"] += 1
            continue
        price = _number(stock.get("price"))
        if price is None:
            continue
        features = extract_kline_features(history)
        tech = calculate_stock_ambush_levels(code, cur_price=price, history_records=history)
        try:
            change_pct = round(float(stock.get("change_pct") or 0), 2)
        except (TypeError, ValueError):
            change_pct = 0.0
        scored = _score_candidate(price, change_pct, features, tech)
        if scored is None:
            rejected["technical"] += 1
            continue
        score, breakdown, reasons = scored
        ranked.append({
            "code": code,
            "name": str(stock.get("name") or code),
            "sector": _candidate_sector(stock),
            "price": round(price, 2),
            "current_price": round(price, 2),
            "change_pct": change_pct,
            "volume": int(stock.get("volume") or 0),
            "score": score,
            "quantitative_score": score,
            "score_breakdown": breakdown,
            "selection_reasons": reasons,
            "technical": features,
            **tech,
        })

    ranked.sort(key=lambda item: (-item["score"], item["sector"], item["code"]))
    selected: list[dict[str, Any]] = []
    sector_counts: dict[str, int] = {}
    for item in ranked:
        sector = item["sector"]
        if sector_counts.get(sector, 0) >= 1:
            continue
        selected.append(item)
        sector_counts[sector] = 1
        if len(selected) >= target_count:
            break
    if len(selected) < target_count:
        for item in ranked:
            if item in selected or sector_counts.get(item["sector"], 0) >= 2:
                continue
            selected.append(item)
            sector_counts[item["sector"]] = sector_counts.get(item["sector"], 0) + 1
            if len(selected) >= target_count:
                break
    return selected, rejected
