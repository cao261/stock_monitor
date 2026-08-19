"""v4.4: 板块级 Alpha 挖掘引擎（左侧埋伏：技术面 + 资金面 + 消息面共振）。

与旧版个股筛选（alpha_discovery.py）的本质区别：
1. 板块池 = 概念题材（同花顺 375 + 东财资金流 387），"银行/证券"这类行业
   板块根本不在池里 —— 从源头排除"工商银行"式的伪候选。
2. 板块位置、量能、趋势用【板块指数历史 K 线】计算，而非单只个股。
3. 消息面催化是【自上而下的挖掘入口】：新闻密集但板块未涨 = 预期差。
4. 左侧纪律硬过滤：60 日涨幅过大（追涨）、下降趋势未见止跌、单日过热、
   流动性不足、资金出逃且无催化 —— 任一命中直接出局。

数据源（已验证可用）：
- 同花顺概念板块列表  stock_board_concept_name_ths()
- 同花顺板块指数 K 线  stock_board_concept_index_ths(名称)  （24h 磁盘缓存）
- 东财概念资金流      stock_fund_flow_concept("即时")   （后台 60s 刷新）
- 7x24 快讯           news_fetcher
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("sector_alpha")

# ====================== 配置 ======================
SECTOR_INDEX_CACHE_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "sector_index_cache.json"
)
SECTOR_INDEX_CACHE_TTL_HOURS = 24
SECTOR_INDEX_CONCURRENCY = 8
SECTOR_INDEX_LOOKBACK_DAYS = 105  # 拉 105 个自然日 ≈ 74 个交易日，够 MA20/MA60

# 粗筛规模：先按资金方向 + 新闻热度粗筛出候选，再逐个拉指数 K 线精细打分
PRESCREEN_LIMIT = 50
TARGET_SECTORS = 6
MAX_STOCKS_PER_SECTOR = 2

# ====================== 左侧纪律（硬过滤） ======================
MAX_RET_60D = 25.0             # 60 日涨幅上限：再高就是右侧追涨
MAX_DAILY_CHANGE = 4.0         # 当日涨跌幅上限：单日过热
MIN_DAILY_AMOUNT = 3e8         # 板块日成交额下限（元）：死水不埋伏
MAX_NET_OUTFLOW_NO_NEWS = 15.0 # 净流出上限（亿），且无任何新闻催化
MAX_DRAWDOWN = 45.0            # 距 60 日高点回撤上限：破位崩塌不接

# ====================== 评分权重（满分 100） ======================
W_POSITION = 25    # 左侧位置：回撤深度与区间位置
W_VOLUME = 20      # 量能结构：缩量收敛 + 止跌确认
W_FUND = 20        # 资金回流：主力净流入 + 领涨结构
W_CATALYST = 20    # 消息催化：新闻密度 + 政策类关键词
W_STRUCTURE = 15   # 弹性结构：成分股数量 + 均线粘合（变盘临近）

# 事件型伪板块（不可交易的季度性事件，非题材）——粗筛直接排除
EVENT_PSEUDO_SECTORS = (
    "业绩预增", "一季报", "中报", "年报", "业绩预告", "涨停", "跌停",
    "次新股", "新股", "ST", "st", "退市", "破净", "送转", "高送转",
    "质押", "解禁", "回购", "举牌", "复牌", "停牌",
)

# 政策类催化关键词（命中加权重）
POLICY_KEYWORDS = (
    "政策", "规划", "支持", "补贴", "批复", "实施方案", "白皮书",
    "涨价", "上调", "招标", "中标", "量产", "突破", "获批", "入市",
    "落地", "超预期", "增长", "扩产", "招标公告", "国家标准",
)

# 板块指数缓存
_index_cache: dict[str, Any] = {}
_index_cache_loaded = False


# ====================== 板块池构建 ======================
def _load_sector_pool_sync() -> list[dict[str, Any]]:
    """同步构建板块池：同花顺概念列表 + 东财资金流（按名称 join）。

    返回 [{name, ths_code, fund_flow: {...}|None}]，按资金流向与新闻热度无关，仅构建池。
    """
    pool: dict[str, dict[str, Any]] = {}

    # 1. 同花顺概念板块（有指数 K 线能力）
    try:
        df = ak_board_concept_name()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = str(row.get("name", "")).strip()
                code = str(row.get("code", "")).strip()
                if name:
                    pool[name] = {
                        "name": name,
                        "ths_code": code,
                        "fund_flow": None,
                        "index_available": True,
                    }
    except Exception as e:
        logger.warning("THS concept list failed: %r", e)

    # 2. 东财概念资金流（附加资金数据）
    try:
        ff = ak_fund_flow_concept()
        if ff is not None and not ff.empty:
            for _, row in ff.iterrows():
                try:
                    name = str(row.get("行业", "")).strip()
                    if not name:
                        continue
                    item = pool.get(name) or {
                        "name": name,
                        "ths_code": "",
                        "fund_flow": None,
                        "index_available": False,
                    }
                    item["fund_flow"] = {
                        "change_pct": float(row.get("行业-涨跌幅", 0) or 0),
                        "net_amount": float(row.get("净额", 0) or 0),
                        "inflow": float(row.get("流入资金", 0) or 0),
                        "outflow": float(row.get("流出资金", 0) or 0),
                        "leading_stock": str(row.get("领涨股", "")).strip(),
                        "leading_change_pct": float(row.get("领涨股-涨跌幅", 0) or 0),
                        "company_count": int(row.get("公司家数", 0) or 0),
                    }
                    pool[name] = item
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.warning("EM fund flow concept failed: %r", e)

    return list(pool.values())


def ak_board_concept_name():
    import akshare as ak
    return ak.stock_board_concept_name_ths()


def ak_fund_flow_concept():
    import akshare as ak
    return ak.stock_fund_flow_concept(symbol="即时")


async def fetch_sector_pool() -> list[dict[str, Any]]:
    """异步入口：构建板块池。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _load_sector_pool_sync)


# ====================== 板块指数 K 线（磁盘缓存 24h） ======================
def _load_index_cache_from_disk() -> None:
    global _index_cache_loaded
    if _index_cache_loaded:
        return
    _index_cache_loaded = True
    try:
        if SECTOR_INDEX_CACHE_FILE.exists():
            raw = json.loads(SECTOR_INDEX_CACHE_FILE.read_text(encoding="utf-8"))
            saved_at = raw.get("saved_at", "")
            if saved_at:
                age = (datetime.now() - datetime.fromisoformat(saved_at)).total_seconds()
                if age < SECTOR_INDEX_CACHE_TTL_HOURS * 3600:
                    for name, recs in raw.get("indexes", {}).items():
                        if isinstance(recs, list) and len(recs) >= 60:
                            _index_cache[name] = recs
                    logger.info("sector index cache loaded: %d sectors", len(_index_cache))
                    return
            logger.info("sector index cache expired (saved_at=%s), will refetch", saved_at)
    except Exception as e:
        logger.warning("load sector index cache failed: %r", e)


def _save_index_cache_to_disk() -> None:
    try:
        SECTOR_INDEX_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECTOR_INDEX_CACHE_FILE.write_text(
            json.dumps({
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "indexes": _index_cache,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("save sector index cache failed: %r", e)


def _fetch_sector_index_sync(name: str) -> list[dict[str, Any]]:
    """同步拉单个板块指数最近 ~55 个交易日 K 线。失败返回 []。"""
    try:
        import akshare as ak
        start = (date.today() - timedelta(days=SECTOR_INDEX_LOOKBACK_DAYS)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        df = ak.stock_board_concept_index_ths(symbol=name, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        records: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            try:
                records.append({
                    "date": str(row.get("日期", "")).strip(),
                    "open": float(row.get("开盘价", 0) or 0),
                    "close": float(row.get("收盘价", 0) or 0),
                    "high": float(row.get("最高价", 0) or 0),
                    "low": float(row.get("最低价", 0) or 0),
                    "volume": float(row.get("成交量", 0) or 0),
                    "amount": float(row.get("成交额", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
        return records
    except Exception as e:
        logger.warning("fetch sector index(%s) failed: %r", name, str(e)[:100])
        return []


async def ensure_sector_indexes(names: list[str]) -> dict[str, list[dict[str, Any]]]:
    """并发拉取缺失的板块指数 K 线，写磁盘缓存。返回 {name: [records]}。

    只拉缓存中缺失或过期的板块；并发受 SECTOR_INDEX_CONCURRENCY 限制。
    """
    _load_index_cache_from_disk()
    missing = [n for n in names if len(_index_cache.get(n) or []) < 60]
    if missing:
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(SECTOR_INDEX_CONCURRENCY)

        async def _guarded(name: str) -> tuple[str, list[dict[str, Any]]]:
            async with sem:
                records = await loop.run_in_executor(None, _fetch_sector_index_sync, name)
                return name, records

        results = await asyncio.gather(*(_guarded(n) for n in missing))
        valid = 0
        for name, records in results:
            if len(records) >= 40:
                _index_cache[name] = records
                valid += 1
        _save_index_cache_to_disk()
        logger.info("sector indexes fetched: %d/%d valid", valid, len(missing))
    return {name: _index_cache.get(name, []) for name in names}


# ====================== 板块技术特征（指数 K 线） ======================
def extract_sector_features(records: list[dict[str, Any]]) -> dict[str, Any]:
    """从板块指数 K 线提取左侧技术特征。数据不足时各字段为 None。

    返回：ret_60d / drawdown_pct / position_60d / ma20 / ma60 /
          ma_bunching_pct / vol_shrink_ratio / stabilized / new_low_5d /
          trend / amount_last / latest_close
    """
    if not records:
        return {}
    data = sorted(records, key=lambda x: x.get("date", ""))
    closes = [float(d["close"]) for d in data if d.get("close")]
    if len(closes) < 20:
        return {}

    def _ma(arr: list[float], w: int) -> float | None:
        return sum(arr[-w:]) / w if len(arr) >= w else None

    latest = closes[-1]
    ma20 = _ma(closes, 20)
    ma60 = _ma(closes, 60)
    highs = [float(d["high"]) for d in data if d.get("high")]
    lows = [float(d["low"]) for d in data if d.get("low")]

    # 60 日涨幅（用收盘序列）
    ret_60d = None
    if len(closes) >= 60:
        base = closes[-60]
        if base > 0:
            ret_60d = (latest / base - 1) * 100
    elif len(closes) >= 21:
        base = closes[-21]
        if base > 0:
            ret_60d = (latest / base - 1) * 100

    # 距 60 日高点回撤
    drawdown_pct = None
    recent_high = max(highs[-60:]) if len(highs) >= 60 else max(highs)
    if recent_high > 0:
        drawdown_pct = (latest / recent_high - 1) * 100

    # 60 日区间位置
    position_60d = None
    recent_low = min(lows[-60:]) if len(lows) >= 60 else min(lows)
    if recent_high > recent_low:
        position_60d = (latest - recent_low) / (recent_high - recent_low) * 100

    # 均线粘合度：|ma20 - ma60| / ma60
    ma_bunching_pct = None
    if ma20 and ma60 and ma60 > 0:
        ma_bunching_pct = abs(ma20 - ma60) / ma60 * 100

    # 量能收缩：近 10 日均量 / 前 10 日均量
    vol_shrink_ratio = None
    volumes = [float(d.get("volume") or 0) for d in data]
    if len(volumes) >= 20 and sum(volumes[-20:-10]) > 0:
        vol_shrink_ratio = (sum(volumes[-10:]) / 10) / (sum(volumes[-20:-10]) / 10)

    # 止跌确认：最近 5 日出现阳线（收盘 > 前收）且未创 10 日新低
    stabilized = False
    new_low_5d = False
    if len(closes) >= 6:
        five_ago = closes[-6]
        stabilized = latest >= five_ago
        if len(lows) >= 10:
            new_low_5d = min(lows[-5:]) <= min(lows[-10:-5]) - 1e-9

    # 趋势：MA20 vs MA60
    trend = "数据不足"
    if ma20 and ma60:
        diff = (ma20 - ma60) / ma60 * 100
        if diff > 2:
            trend = "上升"
        elif diff < -2:
            trend = "下降"
        else:
            trend = "粘合"

    amount_last = None
    amounts = [float(d.get("amount") or 0) for d in data]
    if amounts:
        amount_last = max(amounts[-1], 0)

    return {
        "data_points": len(closes),
        "latest_close": round(latest, 4),
        "ret_60d": round(ret_60d, 2) if ret_60d is not None else None,
        "drawdown_pct": round(drawdown_pct, 2) if drawdown_pct is not None else None,
        "position_60d": round(position_60d, 1) if position_60d is not None else None,
        "ma20": round(ma20, 4) if ma20 is not None else None,
        "ma60": round(ma60, 4) if ma60 is not None else None,
        "ma_bunching_pct": round(ma_bunching_pct, 2) if ma_bunching_pct is not None else None,
        "vol_shrink_ratio": round(vol_shrink_ratio, 2) if vol_shrink_ratio is not None else None,
        "stabilized": stabilized,
        "new_low_5d": new_low_5d,
        "trend": trend,
        "amount_last": amount_last,
    }


# ====================== 消息面催化匹配 ======================
def match_news_to_sector(sector_name: str, news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """板块名 → 新闻标题关键词匹配，返回命中新闻（最多 4 条）。

    匹配规则（防误匹配）：
    - 标题含板块完整名 → 命中（强相关）
    - 标题含板块名任意 3 字滑窗 → 命中（如 "固态电池" → "态电池"）
    - 不含 2 字通用词（"电池"/"经济" 之类滑窗会跨板块误匹配）
    政策类关键词单独标记 is_policy，用于催化强度加权。
    """
    if not sector_name or not news:
        return []
    # 主词：按标点切分的第一个 ≥2 字段（如 "液冷服务器" → ["液冷服务器"]）
    parts = re.split(r"[\s,，、/／与和及()()（）]+", sector_name)
    base_keywords = [p for p in parts if len(p) >= 2][:5]
    keywords: set[str] = set(base_keywords)
    # 3 字滑窗（防 "固态电池" 新闻误中 "TOPCON电池" 这类跨题材板块）
    for p in base_keywords:
        for i in range(max(0, len(p) - 3 + 1)):
            keywords.add(p[i : i + 3])
    if not keywords:
        keywords = {sector_name[:4]}

    hits: list[dict[str, Any]] = []
    for n in news:
        title = (n.get("title") or n.get("content") or "")[:120]
        if not title or not any(kw in title for kw in keywords):
            continue
        t = n.get("time") or ""
        if "T" in t:
            t = t.split("T", 1)[1][:5]
        hits.append({
            "title": title,
            "time": t,
            "source": str(n.get("source") or "")[:20],
            "is_policy": any(pk in title for pk in POLICY_KEYWORDS),
            "why_relevant": f"标题含「{sector_name}」相关关键词",
        })
        if len(hits) >= 4:
            break
    return hits


# ====================== 粗筛（免 K 线） ======================
def _preselect_sectors(
    pool: list[dict[str, Any]],
    news: list[dict[str, Any]],
) -> list[tuple[int, dict[str, Any]]]:
    """资金方向 + 新闻热度粗筛，返回按综合信号排序的 (rank, sector)。

    rank = 资金方向(0~2) + 新闻热度(0~3)，保证"有催化但资金未动"的左侧
    板块也能进入精细打分（资金流出但新闻密集 = 预期差最大的窗口）。
    """
    eligible: list[tuple[int, dict[str, Any]]] = []
    for sector in pool:
        ff = sector.get("fund_flow")
        name = sector["name"]
        if any(ep in name for ep in EVENT_PSEUDO_SECTORS):
            continue
        if ff:
            # 单日过热 / 恐慌直接排除
            if ff.get("change_pct", 0) > MAX_DAILY_CHANGE or ff.get("change_pct", 0) < -4.0:
                continue
            n_company = ff.get("company_count", 0)
            if n_company and (n_company < 10 or n_company > 400):
                continue
        hits = match_news_to_sector(name, news)
        news_rank = min(len(hits), 3)
        fund_rank = 0
        if ff:
            if ff.get("net_amount", 0) > 0:
                fund_rank = 2
            elif ff.get("net_amount", 0) > -10:
                fund_rank = 1
        eligible.append((fund_rank + news_rank, sector))
    eligible.sort(key=lambda x: (-x[0], x[1]["name"]))
    return eligible[:PRESCREEN_LIMIT]


# ====================== 左侧纪律（硬过滤） ======================
def _check_exclusion(
    sector: dict[str, Any],
    feats: dict[str, Any],
    news_hits: list[dict[str, Any]],
) -> tuple[bool, str]:
    """返回 (是否排除, 原因)。任一硬纪律命中即出局。"""
    name = sector["name"]
    ff = sector.get("fund_flow")

    ret_60d = feats.get("ret_60d")
    if ret_60d is not None and ret_60d > MAX_RET_60D:
        return True, f"60日涨幅 {ret_60d:+.1f}%，已大涨不追"
    drawdown = feats.get("drawdown_pct")
    if drawdown is not None and drawdown < -MAX_DRAWDOWN:
        return True, f"距60日高点回撤 {drawdown:.1f}%，破位崩塌不接"
    if feats.get("new_low_5d") and feats.get("trend") == "下降":
        return True, "下降趋势且5日内创新低，未见止跌"
    amount = feats.get("amount_last")
    if amount is not None and amount < MIN_DAILY_AMOUNT:
        return True, "板块日成交额不足，流动性差"
    if ff and ff.get("net_amount", 0) < -MAX_NET_OUTFLOW_NO_NEWS and not news_hits:
        return True, "资金大幅流出且无消息催化"
    if ff and ff.get("change_pct", 0) > MAX_DAILY_CHANGE:
        return True, f"当日已涨 {ff.get('change_pct'):+.1f}%，追高"
    return False, ""


# ====================== 板块打分 ======================
def _score_sector(
    sector: dict[str, Any],
    feats: dict[str, Any],
    news_hits: list[dict[str, Any]],
) -> tuple[int, dict[str, int], str]:
    """五维打分（满分 100）。返回 (score, breakdown, ambush_type)。"""
    ff = sector.get("fund_flow")
    b: dict[str, int] = {}

    # 1. 左侧位置（25）
    pos = 0
    drawdown = feats.get("drawdown_pct")
    if drawdown is not None:
        dd = abs(drawdown)
        if 8 <= dd <= 25:
            pos = 25            # 回调到位，甜区
        elif dd < 8:
            pos = round(12 + dd / 8 * 13)   # 位置偏高，线性降
        else:
            pos = round(max(6, 25 - (dd - 25) / 20 * 19))  # 深跌，需止跌确认降权
    b["左侧位置"] = pos

    # 2. 量能结构（20）：缩量收敛 12 + 止跌确认 8
    vol = 0
    ratio = feats.get("vol_shrink_ratio")
    if ratio is not None:
        if ratio <= 0.75:
            vol = 12            # 地量
        elif ratio <= 1.0:
            vol = 8
        else:
            vol = 4
    b["缩量收敛"] = vol
    stab = 8 if feats.get("stabilized") else 0
    b["止跌确认"] = stab

    # 3. 资金回流（20）：净流入 12 + 领涨结构 8
    fund = 0
    if ff:
        net = ff.get("net_amount", 0)
        if net > 0:
            fund = round(min(12, 3 + net / 10 * 9))   # 净流入 10 亿 ≈ 满分
        elif net > -5:
            fund = 2            # 温和流出，观察
    b["主力净流入"] = fund
    lead = 0
    if ff:
        lchg = ff.get("leading_change_pct", 0) or 0
        if 1 <= lchg <= 5:
            lead = 8            # 领涨股启动未高潮
        elif lchg > 5:
            lead = 3            # 领涨股已大涨，追高风险
        elif lchg > 0:
            lead = 5
    b["领涨结构"] = lead

    # 4. 消息催化（20）：新闻条数 16 + 政策关键词 4
    cat = 0
    n_hits = len(news_hits)
    if n_hits >= 3:
        cat = 16
    elif n_hits == 2:
        cat = 11
    elif n_hits == 1:
        cat = 6
    if any(h.get("is_policy") for h in news_hits):
        cat = min(20, cat + 4)
    b["消息催化"] = cat

    # 5. 弹性结构（15）：成分股数量 8 + 均线粘合 7
    stru = 0
    n_company = (ff or {}).get("company_count")
    if n_company:
        if 15 <= n_company <= 150:
            stru = 8            # 题材适中，可拉动
        elif n_company > 300:
            stru = 3            # 太宽泛，难聚焦
        else:
            stru = 5
    bunch = feats.get("ma_bunching_pct")
    if bunch is not None:
        if bunch <= 3.0:
            stru += 7           # 均线粘合，变盘前奏
        elif bunch <= 6.0:
            stru += 4
    b["弹性结构"] = stru

    score = pos + vol + stab + fund + lead + cat + stru

    # 埋伏类型判定
    drawdown_abs = abs(feats.get("drawdown_pct") or 0)
    n_hits = len(news_hits)
    if n_hits >= 2 and drawdown_abs >= 8:
        ambush_type = "事件催化左侧潜伏"
    elif ff and ff.get("net_amount", 0) > 0 and drawdown_abs >= 8:
        ambush_type = "资金回流低位埋伏"
    elif feats.get("trend") == "下降" and feats.get("stabilized"):
        ambush_type = "深调止跌拐点低吸"
    elif feats.get("ma_bunching_pct") is not None and feats.get("ma_bunching_pct") <= 3:
        ambush_type = "均线粘合变盘左侧"
    else:
        ambush_type = "缩量企稳低位潜伏"
    return score, b, ambush_type


# ====================== 板块内个股落地（v4.6 三层多因子硬过滤）======================
def pick_sector_stocks(
    sector: dict[str, Any],
    all_stocks: dict[str, dict[str, Any]],
    news_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """v4.6: 板块 → 代表股由 factor_engine 三层硬过滤挑选，替代 v4.4 的
    "领涨股 1 只 + 板块主词温和涨 1 只"启发式。

    调用 factor_engine.pick_stocks_for_sector 走完整流水线：
    - 候选股池 = 板块主词匹配全市场上涨 Top 50 + 领涨股
    - 5 条个股硬过滤：MA 排列 / 距 20 日高 / VWAP 承接 / RVOL 区间 / 上影线
    - 角色识别：容量中军 / 弹性先锋 / 一般
    """
    from app.services import factor_engine
    ff = sector.get("fund_flow") or {}
    lead_name = str(ff.get("leading_stock") or "").strip()
    lead_code: str | None = None
    if lead_name:
        for code, s in all_stocks.items():
            if s.get("name") == lead_name:
                lead_code = code
                break
        if lead_code is None:
            for code, s in all_stocks.items():
                if s.get("name") and lead_name[:2] in s["name"]:
                    lead_code = code
                    break
    try:
        return factor_engine.pick_stocks_for_sector(
            sector_name=sector["name"],
            all_stocks=all_stocks,
            leading_code=lead_code,
        )
    except Exception as e:
        logger.warning("pick_stocks_for_sector(%s) failed: %r, fallback empty", sector.get("name"), e)
        return []


# ====================== 板块候选构建（主入口） ======================
async def build_sector_candidates(
    all_stocks: dict[str, dict[str, Any]],
    news: list[dict[str, Any]],
    *,
    target_count: int = TARGET_SECTORS,
) -> dict[str, Any]:
    """完整管线：板块池 → 粗筛 → 指数 K 线 → 左侧纪律 → 打分 → 个股落地。

    返回：
    {
      "sectors": [候选板块 dict],
      "rejected": {reason: count},
      "pool_size": int,
      "prescreened": int,
      "index_ready": int,
    }
    """
    pool = await fetch_sector_pool()
    if not pool:
        return {"sectors": [], "rejected": {}, "pool_size": 0, "prescreened": 0, "index_ready": 0}

    prescreen = _preselect_sectors(pool, news)
    prescreened_names = [s[1]["name"] for s in prescreen]
    indexes = await ensure_sector_indexes(prescreened_names)

    rejected: dict[str, int] = {}
    scored: list[tuple[int, dict[str, Any]]] = []
    index_ready = 0
    for _rank, sector in prescreen:
        name = sector["name"]
        records = indexes.get(name) or []
        feats = extract_sector_features(records)
        if not feats or feats.get("data_points", 0) < 20:
            rejected["index_data"] = rejected.get("index_data", 0) + 1
            continue
        index_ready += 1
        news_hits = match_news_to_sector(name, news)
        excluded, reason = _check_exclusion(sector, feats, news_hits)
        if excluded:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        score, breakdown, ambush_type = _score_sector(sector, feats, news_hits)
        if score < 45:
            rejected["score_low"] = rejected.get("score_low", 0) + 1
            continue
        stocks = pick_sector_stocks(sector, all_stocks, news_hits)
        if not stocks:
            rejected["no_stock"] = rejected.get("no_stock", 0) + 1
            continue

        ff = sector.get("fund_flow") or {}
        scored.append((score, {
            "name": name,
            "score": score,
            "score_breakdown": breakdown,
            "ambush_type": ambush_type,
            "tech": feats,
            "fund_flow": ff,
            "news_hits": news_hits,
            "stocks": stocks,
        }))

    scored.sort(key=lambda x: (-x[0], x[1]["name"]))

    # 板块近义去重：名称含共同 2 字主词且新闻命中同源时，保留高分者
    # （防 "TOPCON电池/钙钛矿电池/BC电池" 同族题材重复输出）
    def _share_topic(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if a["name"] == b["name"]:
            return True
        a_words = set(re.split(r"[\s,，、/／]+", a["name"]))
        b_words = set(re.split(r"[\s,，、/／]+", b["name"]))
        if a_words & b_words:
            return True
        a_news = {h["title"][:20] for h in a.get("news_hits", [])}
        b_news = {h["title"][:20] for h in b.get("news_hits", [])}
        return bool(a_news & b_news) if a_news and b_news else False

    deduped: list[dict[str, Any]] = []
    for item in scored:
        if any(_share_topic(item[1], kept[1]) for kept in deduped):
            continue
        deduped.append(item)
        if len(deduped) >= target_count:
            break

    sectors = [item for _s, item in deduped]
    return {
        "sectors": sectors,
        "rejected": rejected,
        "pool_size": len(pool),
        "prescreened": len(prescreen),
        "index_ready": index_ready,
    }


# ====================== 引擎输出 → 前端兼容结构 ======================
def sector_to_discovery(sector: dict[str, Any]) -> dict[str, Any]:
    """把候选板块转成与前端 AlphaDiscoverModal 兼容的 discovery dict。"""
    tech = sector["tech"]
    ff = sector["fund_flow"]
    news_hits = sector["news_hits"]
    stocks = sector["stocks"]

    tech_indicators: list[dict[str, Any]] = []
    ret60 = tech.get("ret_60d")
    dd = tech.get("drawdown_pct")
    if ret60 is not None:
        sig = "利空" if ret60 > MAX_RET_60D else ("中性" if ret60 > 15 else "利多")
        tech_indicators.append({
            "name": "板块60日涨幅",
            "value": f"{ret60:+.1f}%",
            "signal": sig,
            "comment": "中期位置，避免追涨",
        })
    if dd is not None:
        sig = "利多" if -25 <= dd <= -8 else ("中性" if dd > -8 else "利空")
        tech_indicators.append({
            "name": "距60日高点回撤",
            "value": f"{dd:.1f}%",
            "signal": sig,
            "comment": "回调到位度，左侧埋伏核心",
        })
    ratio = tech.get("vol_shrink_ratio")
    if ratio is not None:
        sig = "利多" if ratio <= 0.8 else "中性"
        tech_indicators.append({
            "name": "量能收缩(10日/前10日)",
            "value": f"{ratio:.2f}x",
            "signal": sig,
            "comment": "地量见底信号" if ratio <= 0.8 else "量能仍活跃",
        })
    if tech.get("ma_bunching_pct") is not None:
        bunch = tech["ma_bunching_pct"]
        tech_indicators.append({
            "name": "MA20/MA60粘合度",
            "value": f"{bunch:.1f}%",
            "signal": "利多" if bunch <= 3 else "中性",
            "comment": "均线粘合，变盘临近",
        })
    if tech.get("stabilized"):
        tech_indicators.append({
            "name": "止跌确认",
            "value": "近5日企稳",
            "signal": "利多",
            "comment": "收盘未再创新低",
        })
    if ff:
        net = ff.get("net_amount")
        if net is not None:
            tech_indicators.append({
                "name": "板块主力净额",
                "value": f"{net:+.1f}亿",
                "signal": "利多" if net > 0 else "中性",
                "comment": "当日资金方向",
            })
        tech_indicators.append({
            "name": "领涨股",
            "value": f"{ff.get('leading_stock') or '-'} {ff.get('leading_change_pct', 0):+.1f}%",
            "signal": "利多" if 0 <= (ff.get("leading_change_pct") or 0) <= 5 else "中性",
            "comment": "板块内龙头强度",
        })

    reasons = "；".join(f"{k} {v}分" for k, v in sector["score_breakdown"].items())
    stock_logic = f"板块逻辑：{sector['name']}。{reasons}。"
    for s in stocks:
        s["stock_logic"] = f"{stock_logic} {s.get('stock_logic', '')}"[:400]

    # ===== v4.4 新增: 4 维评分 + 5 大左侧信号 (TRADING_LOGIC 第 2.1/2.2 节) =====
    import analyzer  # 延迟引用, 避免循环
    sector_4d = analyzer.score_sector_4d(sector["name"], ff)
    score_4d = {
        "msg": sector_4d["msg"],
        "cap": sector_4d["cap"],
        "tech": sector_4d["tech"],
        "sent": sector_4d["sent"],
        "total": round((sector_4d["msg"] + sector_4d["cap"] + sector_4d["tech"] + sector_4d["sent"]) / 4.0, 1),
        "grade": sector_4d["grade"],
    }
    left_signals = analyzer.detect_left_side_signals(
        sector["name"], ff, quant_scores=score_4d, news=news_hits,
    )
    right_side_confirmations = [
        {"name": "突破 5 日均线", "triggered": False},
        {"name": "板块当日涨幅 > 1% 且量能放大 50%", "triggered": False},
        {"name": "MACD 金叉", "triggered": False},
        {"name": "突破关键阻力位", "triggered": False},
    ]

    # 距60日高点回撤 + 60日涨幅展示（板块级指标）
    return {
        "sector": sector["name"],
        "score": sector["score"],
        "quantitative_score": sector["score"],
        "score_breakdown": sector["score_breakdown"],
        "ambush_type": sector["ambush_type"],
        "catalyst_window": "左侧埋伏窗口",
        "catalyst_logic": "",
        "technical_pattern": reasons,
        "breakout_trigger": "",
        "tech_indicators": tech_indicators,
        "news_highlights": news_hits,
        "stocks": stocks,
        "level": "高" if sector["score"] >= 70 else "中",
        "risk_warning": "",
        "sector_metrics": {
            "ret_60d": tech.get("ret_60d"),
            "drawdown_pct": tech.get("drawdown_pct"),
            "position_60d": tech.get("position_60d"),
            "trend": tech.get("trend"),
            "amount_last": tech.get("amount_last"),
            "net_amount": (ff or {}).get("net_amount"),
            "company_count": (ff or {}).get("company_count"),
            "index_ready": True,
        },
        # ===== v4.4 新增 TRADING_LOGIC 字段 =====
        "score_4d": score_4d,                          # 4 维量化评分
        "left_signals": left_signals,                  # 5 大左侧信号触发
        "right_side_confirmations": right_side_confirmations,  # 右侧确认清单
        "llm_verification": {                          # LLM T1/T2/T3 验证 (LLM 调用后填充)
            "t1_message": None,
            "t2_technical": None,
            "t3_cross": None,
            "final_score": None,
            "action": None,
        },
        "verification": {"status": "unverified", "risks": [], "referenced_news_ids": []},
    }
