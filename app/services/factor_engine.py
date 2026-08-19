# -*- coding: utf-8 -*-
"""v4.6 三层多因子量化筛选引擎（板块 + 个股硬过滤 + 角色识别）。

设计目标：把 v4.1-v4.4 中"LLM 拍脑袋选股"换成
  Layer 1: 板块共振（板块涨幅 >1.5% 且涨停 >=2）→ Top 3
  Layer 2: 个股多因子（VWAP 承接 / 量价健康 / 形态临界 / 角色）
  Layer 3: 角色识别（容量中军 / 弹性先锋）
  → 每个 Top 板块留 2 只高分股，最终给 LLM 的"结构化数据"
  LLM 仅做"逻辑利空校验 + 150 字操盘总结"，不再参与底层选股。

数据约束（用户机器东财个股接口已 502，本引擎不依赖东财个股详情）：
- 行情来自 market_fetcher.all_stocks_cache：name/open/prev_close/price/high/low/volume/amount/change_pct
- 历史 K 线来自 market_fetcher.history_cache：[{date,open,close,high,low,volume_lots}, ...]
- 板块指数 K 线来自 sector_alpha._index_cache
- 不依赖流通市值：角色识别改用"成交额 + 价格带 + 换手相对值"做软分类
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import market_fetcher as mf
from app.services import sector_alpha

logger = logging.getLogger("factor_engine")


# ====================== 硬过滤阈值（实盘确认版）======================
# Layer 1 板块
SECTOR_MIN_CHANGE_PCT = 1.5      # 板块涨幅下限（%）
SECTOR_MIN_LIMIT_UP = 2          # 板块涨停家数下限
TOP_SECTORS = 3                  # 最终入选板块数

# Layer 2 个股
# 注：换手率 = 成交量/流通股本；流通股本无直接数据源（东财个股接口 502），
#      用 RVOL（相对量能）替代作为量能主约束；turnover_proxy_pct 字段保留软参考。
RVOL_MIN = 1.5                   # 相对量能下限（RVOL = 今日量 / 5 日均量）
RVOL_MAX = 3.5
UPPER_SHADOW_MAX = 25.0          # 上影线占比上限（%）（high - max(open,close)） / (high - low)
NEAR_HIGH_20D_PCT = 5.0          # 距离 20 日最高价上限（%）（越接近平台高点越好）
TOP_STOCKS_PER_SECTOR = 2        # 每板块入选个股数

# Layer 3 角色
MID_CAP_AMOUNT = 10.0            # 容量中军：当日成交额下限（亿）


# ====================== Layer 1: 板块共振 ======================
def score_sector_resonance(
    sector_name: str,
    members: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """板块共振硬过滤：返回通过层 1 的板块 dict，否则 None。

    字段：
      - change_pct: 板块当日涨幅（%），由成分股涨跌幅按等权近似
      - limit_up_count: 板块涨停家数
      - limit_down_count: 跌停家数
      - up_ratio: 上涨家数占比（%）
      - net_inflow_rank: 资金净流入排名（用成分股 amount 近似，板块维度没有直接净流入，详见 _sector_net_proxy）
    """
    if not members:
        return None
    change_pcts = [float(m.get("change_pct") or 0) for m in members]
    limit_up = sum(1 for m in members if (m.get("change_pct") or 0) >= 9.5)
    limit_down = sum(1 for m in members if (m.get("change_pct") or 0) <= -9.5)
    up = sum(1 for m in members if (m.get("change_pct") or 0) > 0)
    up_ratio = round(up / len(members) * 100, 2) if members else 0.0
    avg_chg = round(sum(change_pcts) / len(change_pcts), 2) if change_pcts else 0.0

    # 硬过滤
    if up_ratio < 70.0:
        return None
    if avg_chg < SECTOR_MIN_CHANGE_PCT:
        return None
    if limit_up < SECTOR_MIN_LIMIT_UP:
        return None
    return {
        "sector": sector_name,
        "change_pct": avg_chg,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "up_ratio": up_ratio,
        "company_count": len(members),
        "net_amount_yi": _sector_net_proxy(members),  # 资金净流入近似（亿）
    }


def _sector_net_proxy(members: list[dict[str, Any]]) -> float:
    """板块净流入近似：东财接口常 502，用全市场缓存的 amount × sign(change_pct) 求和。"""
    s = 0.0
    for m in members:
        amt = float(m.get("amount") or 0)  # 元
        chg = float(m.get("change_pct") or 0)
        # 涨的家按 30% 流入，跌的按 -30% 流出（粗近似，量级对即可）
        if chg > 0:
            s += amt * 0.3
        elif chg < 0:
            s -= amt * 0.3
    return round(s / 1e8, 2)  # 亿


def rank_top_sectors(
    candidate_sectors: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Layer 1 输出：所有通过硬过滤的板块按净流入排序，取 TOP_SECTORS。"""
    passed: list[dict[str, Any]] = []
    for name, members in candidate_sectors.items():
        info = score_sector_resonance(name, members)
        if info is not None:
            passed.append(info)
    passed.sort(key=lambda x: x["net_amount_yi"], reverse=True)
    return passed[:TOP_SECTORS]


# ====================== Layer 2: 个股多因子 ======================
def _ma(arr: list[float], w: int) -> float | None:
    return sum(arr[-w:]) / w if len(arr) >= w else None


def _stock_features(
    snapshot: dict[str, Any],
    history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """从行情 + 历史 K 线提取个股硬过滤所需特征。返回 None 表示数据不足。"""
    if not snapshot or not history:
        return None
    data = sorted(history, key=lambda x: x.get("date", ""))
    closes = [float(d["close"]) for d in data if d.get("close")]
    highs = [float(d["high"]) for d in data if d.get("high")]
    lows = [float(d["low"]) for d in data if d.get("low")]
    volumes = [float(d.get("volume_lots") or 0) * 100.0 for d in data]  # 手 → 股
    if len(closes) < 20 or len(volumes) < 5:
        return None

    price = float(snapshot.get("price") or 0)
    op = float(snapshot.get("open") or 0)
    high = float(snapshot.get("high") or price)
    low = float(snapshot.get("low") or price)
    vol = float(snapshot.get("volume") or 0)
    amt = float(snapshot.get("amount") or 0)
    if price <= 0 or high <= low:
        return None

    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    high_20d = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    low_20d = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    avg_vol_5d = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
    # 估算"分时均价"（VWAP 替代）：amount / volume；没有分钟数据就用日 K 近似当日均价
    vwap_proxy = (amt / vol) if vol > 0 else price
    vwap_dev_pct = round((price - vwap_proxy) / vwap_proxy * 100, 2) if vwap_proxy > 0 else 0.0
    rvol = round(vol / avg_vol_5d, 2) if avg_vol_5d > 0 else 0.0
    # 相对换手：今日量 / 5 日均量（用平均持仓时间≈5 日估算"日均换手"）
    turnover_proxy_pct = round(rvol * 100 / 5, 2)  # 粗近似
    # 上影线占比
    upper_shadow_pct = round(
        (high - max(op, price)) / (high - low) * 100, 2
    ) if (high - low) > 0 else 0.0
    # 距 20 日高点距离
    dist_high_20d_pct = round((price - high_20d) / high_20d * 100, 2) if high_20d > 0 else 0.0
    return {
        "price": price,
        "ma5": round(ma5, 4) if ma5 else None,
        "ma10": round(ma10, 4) if ma10 else None,
        "ma20": round(ma20, 4) if ma20 else None,
        "ma_alignment": (ma5 and ma10 and ma20 and ma5 > ma10 > ma20),
        "high_20d": round(high_20d, 4),
        "dist_high_20d_pct": dist_high_20d_pct,
        "vwap_proxy": round(vwap_proxy, 4),
        "vwap_dev_pct": vwap_dev_pct,
        "rvol": rvol,
        "turnover_proxy_pct": turnover_proxy_pct,
        "upper_shadow_pct": upper_shadow_pct,
        "avg_volume_5d": round(avg_vol_5d, 0),
        "amount_yi": round(amt / 1e8, 2),
    }


def _filter_one_stock(feats: dict[str, Any]) -> tuple[bool, str]:
    """个股硬过滤：任一不满足即拒绝，返回 (通过, 拒绝原因)。"""
    if not feats.get("ma_alignment"):
        return False, "MA5/10/20 非多头排列"
    dh = feats.get("dist_high_20d_pct")
    if dh is None or dh > NEAR_HIGH_20D_PCT or dh < -NEAR_HIGH_20D_PCT:
        return False, f"距20日高 {dh}%，偏离平台高点"
    if feats.get("vwap_dev_pct", 0) < 0:
        return False, f"现价低于分时均价 {feats['vwap_dev_pct']}%，弱势承接"
    rv = feats.get("rvol", 0)
    if not (RVOL_MIN <= rv <= RVOL_MAX):
        return False, f"RVOL {rv} 超出 [{RVOL_MIN},{RVOL_MAX}]"
    us = feats.get("upper_shadow_pct", 0)
    if us > UPPER_SHADOW_MAX:
        return False, f"上影线 {us}%，冲高回落"
    return True, ""


# ====================== Layer 3: 角色识别 ======================
def _tag_role(feats: dict[str, Any]) -> str:
    """角色标签：
      - 容量中军：当日成交额 >10 亿（板块定海神针）
      - 弹性先锋：价格 <= 50 元 且 量能放大（RVOL >= 2.0）
      - 一般标的：以上都不是
    """
    if feats.get("amount_yi", 0) >= MID_CAP_AMOUNT:
        return "容量中军"
    if feats.get("price", 999) <= 50.0 and feats.get("rvol", 0) >= 2.0:
        return "弹性先锋"
    return "一般"


# ====================== Pipeline ======================
def _build_candidate_sectors(
    members_by_sector: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """封装给单测用：直接喂（板块名 → 成分股行情列表）。"""
    return rank_top_sectors(members_by_sector)


def _score_and_filter_stocks(
    members: list[dict[str, Any]],
    ts_codes: list[str],
) -> list[dict[str, Any]]:
    """从板块成分股里挑出硬过滤通过的 Top N。"""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for code, m in zip(ts_codes, members):
        snap = mf.all_stocks_cache.get(code) or m
        hist = (mf.history_cache.get(code) or {}).get("data") or []
        feats = _stock_features(snap, hist)
        if feats is None:
            rejected.append({"code": code, "name": m.get("name"), "why": "数据不足"})
            continue
        ok, why = _filter_one_stock(feats)
        if not ok:
            rejected.append({"code": code, "name": m.get("name"), "why": why})
            continue
        feats["code"] = code
        feats["name"] = m.get("name") or snap.get("name") or code
        feats["role"] = _tag_role(feats)
        feats["change_pct"] = m.get("change_pct") or snap.get("change_pct")
        accepted.append(feats)
    accepted.sort(key=lambda x: (x.get("rvol", 0), x.get("amount_yi", 0)), reverse=True)
    return accepted[:TOP_STOCKS_PER_SECTOR], rejected  # type: ignore[return-value]


# ====================== 顶层入口（供 routers/strategy.py 调用）======================
async def run_multifactor_pipeline(
    sector_members: dict[str, list[dict[str, Any]]],
    sector_codes: dict[str, list[str]],
) -> dict[str, Any]:
    """三层多因子流水线。

    输入：
      sector_members: {板块名: [成分股行情dict, ...]} （含 change_pct / amount / volume）
      sector_codes:   {板块名: [对应 ts_code, ...]}（与 sector_members 顺序对齐）

    输出（给 LLM 的结构化数据）：
      {
        "top_sectors": [
          {
            "sector": "...",
            "change_pct": ..., "limit_up_count": ..., "up_ratio": ..., "net_amount_yi": ...,
            "stocks": [
              {"code", "name", "role", "price", "change_pct", "rvol", "turnover_proxy_pct",
               "vwap_dev_pct", "dist_high_20d_pct", "upper_shadow_pct", "ma_alignment", "amount_yi"},
              ...
            ],
            "rejected": [...],   # 通过但未入选的（如用于审计）
          },
          ...
        ],
        "meta": {"pool_size": ..., "sectors_passed": ...},
      }
    """
    # Layer 1
    top_sectors = _build_candidate_sectors(sector_members)
    out_sectors: list[dict[str, Any]] = []
    for s in top_sectors:
        name = s["sector"]
        members = sector_members[name]
        codes = sector_codes.get(name, [])
        # 确保对齐
        if len(codes) != len(members):
            # 容错：截短到等长
            n = min(len(codes), len(members))
            members = members[:n]
            codes = codes[:n]
        accepted, rejected = _score_and_filter_stocks(members, codes)
        out_sectors.append({**s, "stocks": accepted, "rejected_count": len(rejected)})
    return {
        "top_sectors": out_sectors,
        "meta": {
            "sectors_evaluated": len(sector_members),
            "sectors_passed": len(top_sectors),
            "stocks_evaluated": sum(len(v) for v in sector_members.values()),
        },
    }


# ====================== v4.6 真实数据版本 ======================
# 板块名 → 候选股 ts_code 列表的来源选择
# 1) 领涨股（fund_flow 提供的 leading_stock 在 all_stocks_cache 里的代码）
# 2) 板块主词匹配的近 N 日上涨 Top（限板块成员数）
# 注：东财 stock_board_concept_cons_em 当前在用户机器上 502
#     → 用"全市场上涨 Top 200 + watchlist + 板块名匹配"近似候选股池

# v4.6.1: 快照粗筛阈值（不依赖 K 线，纯内存计算）
SNAPSHOT_CHANGE_MIN = 2.0         # 涨幅下限（%）：剔除一字跌停 + 弱势股
SNAPSHOT_CHANGE_MAX = 9.5         # 涨幅上限（%）：剔除一字涨停（无参与空间）
SNAPSHOT_MIN_AMOUNT = 8_000_000.0  # 当日成交额下限（元 = 8000 万）：剔除微盘死水
SNAPSHOT_TOP_PER_SECTOR = 5       # 每板块经粗筛后最多保留的候选股数


def _snapshot_prefilter(
    pool: list[dict[str, Any]],
    *,
    cap: int = SNAPSHOT_TOP_PER_SECTOR,
) -> tuple[list[dict[str, Any]], list[str]]:
    """v4.6.1 快照粗筛：纯内存 0 网络，不拉 K 线。
    1) 涨幅 ∈ [2.0%, 9.5%]（剔除一字跌停与无参与空间的一字板）
    2) 当日成交额 > 8000 万元（排除微盘死水）
    3) 按 (涨幅 × log(volume)) 排序取 Top ``cap`` 只
    返回 (members, codes) — 顺序对齐。
    """
    import math
    passed: list[tuple[float, str, dict[str, Any]]] = []
    for s in pool:
        try:
            chg = float(s.get("change_pct") or 0)
        except (TypeError, ValueError):
            continue
        amt = float(s.get("amount") or 0)
        vol = float(s.get("volume") or 0)
        price = float(s.get("price") or 0)
        if price <= 0 or vol <= 0:
            continue
        if not (SNAPSHOT_CHANGE_MIN <= chg <= SNAPSHOT_CHANGE_MAX):
            continue
        if amt < SNAPSHOT_MIN_AMOUNT:
            continue
        # 排序分：涨幅 × log(volume)，量价齐升的优先
        score = chg * math.log10(max(vol, 1))
        code = s.get("__ts_code") or s.get("code") or ""
        passed.append((score, code, s))
    passed.sort(key=lambda x: (-x[0], x[1]))
    members = [s for _, _, s in passed[:cap]]
    codes = [c for _, c, _ in passed[:cap]]
    return members, codes


def _candidates_by_sector(
    sector_name: str,
    all_stocks: dict[str, dict[str, Any]],
    *,
    cap: int = 50,
    excluded_codes: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """为单个板块构造候选股池（不依赖东财成分股接口，502 时全市场兜底）。
    返回 (candidates, codes) — 两者顺序对齐。

    v4.6.1 强化：
    1. excluded_codes：被前序板块选中的股票不再进入本板块候选池
    2. 主词匹配 < 10 只时全市场兜底，且全市场兜底本身也排除 excluded_codes
    """
    import re
    excluded = excluded_codes or set()
    main = re.split(r"[\s,，、/／]+", sector_name)[0][:4]
    if len(main) < 2:
        main = sector_name[:2]
    matched: list[tuple[float, str, dict[str, Any]]] = []
    for code, s in all_stocks.items():
        if code in excluded:
            continue
        nm = str(s.get("name") or "")
        if not nm or main not in nm:
            continue
        try:
            chg = float(s.get("change_pct") or 0)
        except (TypeError, ValueError):
            continue
        price = float(s.get("price") or 0)
        if price <= 0 or chg < -2.0:
            continue
        matched.append((chg, code, s))
    matched.sort(key=lambda x: (-x[0], x[1]))

    if len(matched) >= 10:
        pool = matched[:cap]
    else:
        # 兜底：全市场按 (涨幅 × log(volume)) 排序拿前 cap 只
        # 严格排除 excluded_codes（避免跨板块重复）
        import math
        fallback: list[tuple[float, str, dict[str, Any]]] = []
        for code, s in all_stocks.items():
            if code in excluded:
                continue
            try:
                chg = float(s.get("change_pct") or 0)
            except (TypeError, ValueError):
                continue
            price = float(s.get("price") or 0)
            vol = float(s.get("volume") or 0)
            if price <= 0 or vol <= 0 or chg < -5.0:
                continue
            score = chg * math.log10(max(vol, 1))
            fallback.append((score, code, s))
        fallback.sort(key=lambda x: -x[0])
        # 合并去重：先放主词匹配，再放全市场 Top，按原顺序去重
        seen = {c for _, c, _ in matched}
        for item in fallback:
            if item[1] in seen:
                continue
            matched.append(item)
            seen.add(item[1])
            if len(matched) >= cap:
                break
        pool = matched[:cap]

    members = [s for _, _, s in pool]
    codes = [c for _, c, _ in pool]
    return members, codes


def _ensure_history(codes: list[str]) -> None:
    """为给定的 codes 补拉 history。

    v4.6.1: 10 并发（之前 5 并发）—— 30 只精选标的可在 5s 内拉完 60 天 K 线。
    同步实现：用 ThreadPoolExecutor 调 fetch_history_sync 后手动写 mf.history_cache。
    """
    from concurrent.futures import ThreadPoolExecutor
    missing = [c for c in codes if c not in mf.history_cache]
    if not missing:
        return
    def _fetch_and_store(code: str) -> None:
        try:
            data = mf.fetch_history_sync(code)
            if data.get("data"):
                mf.history_cache[code] = data
        except Exception:
            pass
    try:
        with ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(_fetch_and_store, missing))
    except Exception:
        pass


def pick_stocks_for_sector(
    sector_name: str,
    all_stocks: dict[str, dict[str, Any]],
    leading_code: str | None = None,
    excluded_codes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """为单个板块用 factor_engine 三层硬过滤挑高分股。
    返回最多 TOP_STOCKS_PER_SECTOR 只（与 sector_alpha.pick_sector_stocks schema 对齐）。

    v4.6.1 流水线：
    1. 候选股池 = 板块主词匹配 + 全市场兜底（默认 cap=50）
    2. 领涨股第一顺位插入（保证板块资金流信号不丢）
    3. **快照粗筛**：涨幅 [2.0%, 9.5%] + 成交额 > 8000 万 + Top 5
       —— 消灭 5 分钟 K 线拉取瓶颈（6 板块 × 5 = ≤30 只）
    4. 主动补拉 history（10 并发，单批 5s 内完成）
    5. 5 条个股硬过滤：MA 排列 / 距 20 日高 / VWAP 承接 / RVOL 区间 / 上影线
    6. 角色识别：容量中军 / 弹性先锋 / 一般
    """
    excluded = set(excluded_codes or set())
    members, codes = _candidates_by_sector(sector_name, all_stocks, excluded_codes=excluded)
    if leading_code and leading_code not in codes and leading_code in all_stocks:
        s = all_stocks[leading_code]
        # 领涨股即使涨幅/成交额不达标也插入候选（但快照粗筛仍会筛掉）
        members.insert(0, s)
        codes.insert(0, leading_code)
    if not members:
        return []
    # 阶段 1：快照粗筛（纯内存，0 网络）
    members, codes = _snapshot_prefilter(
        [{**m, "__ts_code": c} for m, c in zip(members, codes)],
        cap=SNAPSHOT_TOP_PER_SECTOR,
    )
    if not codes:
        return []
    # 阶段 2：补拉 history（10 并发，30 只 ≤ 5s）
    _ensure_history(codes)
    # 阶段 3：硬过滤 + 角色识别
    accepted, _rejected = _score_and_filter_stocks(members, codes)
    out: list[dict[str, Any]] = []
    for feats in accepted[:TOP_STOCKS_PER_SECTOR]:
        from analyzer import calculate_stock_ambush_levels
        price = feats.get("price")
        try:
            tech = calculate_stock_ambush_levels(feats["code"], cur_price=price or None)
        except Exception:
            tech = {"support_price": None, "support_name": "动态支撑",
                    "resistance_price": None, "resistance_name": "动态压力",
                    "volatility_tag": "波动未知", "technical_basis": "无",
                    "ambush_zone": [None, None], "target_win": None, "stop_loss": None}
        chg = feats.get("change_pct")
        role_label = feats.get("role", "一般")
        if chg is not None and chg >= 9.5:
            role_label = f"{role_label}(短线已热，等回踩低吸)"
        out.append({
            "code": feats["code"],
            "name": feats.get("name", ""),
            "current_price": price,
            "change_pct": chg,
            "role": role_label,
            "factor_score": {
                "rvol": feats.get("rvol"),
                "vwap_dev_pct": feats.get("vwap_dev_pct"),
                "dist_high_20d_pct": feats.get("dist_high_20d_pct"),
                "upper_shadow_pct": feats.get("upper_shadow_pct"),
                "ma_alignment": feats.get("ma_alignment"),
                "amount_yi": feats.get("amount_yi"),
            },
            "support_price": tech["support_price"],
            "support_name": tech["support_name"],
            "resistance_price": tech["resistance_price"],
            "resistance_name": tech["resistance_name"],
            "volatility_tag": tech["volatility_tag"],
            "technical_basis": tech["technical_basis"],
            "ambush_zone": tech["ambush_zone"],
            "target_win": tech["target_win"],
            "stop_loss": tech["stop_loss"],
            "stock_logic": _build_stock_logic(sector_name, feats, tech),
        })
    return out


def _build_stock_logic(sector_name: str, feats: dict[str, Any], tech: dict[str, Any]) -> str:
    """生成个股短描述（不依赖 LLM；用于 stocks.stock_logic 字段，LLM 复盘上下文）。"""
    role = feats.get("role", "一般")
    parts = [f"「{sector_name}」{role}"]
    if feats.get("ma_alignment"):
        parts.append("MA5/10/20 多头排列")
    if feats.get("rvol"):
        parts.append(f"RVOL {feats['rvol']}")
    if feats.get("dist_high_20d_pct") is not None:
        parts.append(f"距20日高 {feats['dist_high_20d_pct']:+.1f}%")
    if tech.get("technical_basis"):
        parts.append(tech["technical_basis"])
    return "；".join(parts)[:400]
