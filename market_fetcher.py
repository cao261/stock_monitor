"""A 股全市场行情抓取模块。

- 内存缓存：``all_stocks_cache``
- 每天 09:15 用 akshare 拉一次全市场代码清单
- 每 5 秒用 aiohttp 从新浪财经拉一次实时行情
- 5000+ 只股票按 800 个/批切片，asyncio.gather 并发请求
- 单批内置指数退避重试

使用::

    from market_fetcher import all_stocks_cache, get_stock, fetch_all_prices
    from market_fetcher import start_scheduler, refresh_codes_daily, periodic_fetch_loop

    # 一次性使用
    asyncio.run(refresh_codes_daily())
    codes = all_stocks_cache["__meta__"]["codes"]
    asyncio.run(fetch_all_prices(codes))

    # 长驻进程
    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from datetime import datetime
from typing import Any

import aiohttp
import akshare as ak
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ====================== 配置 ======================
SINA_URL = "https://hq.sinajs.cn/list={codes}"
# 新浪要求带 Referer，否则 403；UA 走标准 Chrome
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

# 腾讯 K 线接口（用户环境里东方财富域名被屏蔽，用腾讯兜底）
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}

HISTORY_FETCH_DAYS = 60          # 拉 60 天（够算 MA20 + 完整月度趋势；增加请求量但 watchlist 最多几十只）
HISTORY_FETCH_CONCURRENCY = 5    # 拉历史的并发数（避免触发风控）
HISTORY_REFRESH_INTERVAL_CYCLES = 360  # 5s × 360 = 30 分钟补拉一次历史（处理 watchlist 新增）

BATCH_SIZE = 800                       # 新浪一批最多 800 个（用户指定）
FETCH_INTERVAL_SECONDS = 5             # 行情抓取间隔
MAX_RETRIES = 3                        # 单批最大重试次数
RETRY_BACKOFF_BASE = 1.5               # 指数退避基数（秒）
HTTP_TIMEOUT_SECONDS = 10              # 单批 HTTP 超时
SCHEDULE_HOUR = 9
SCHEDULE_MINUTE = 15
TIMEZONE = "Asia/Shanghai"

# ====================== 全局缓存 ======================
# 结构：{ "sh600000": {"name":..., "price":..., ...}, ..., "__meta__": {...} }
all_stocks_cache: dict[str, dict[str, Any]] = {}
_cache_lock = asyncio.Lock()

# 历史数据缓存（用于量比等需要 5 日均量的指标）
# 结构：{ "sh600000": {"avg_volume_5d": float(股), "avg_amount_5d": float(元),
#                       "data": [{"date":..., "open":..., "close":..., "high":..., "low":..., "volume_lots":...}, ...] } }
history_cache: dict[str, dict[str, Any]] = {}
_history_lock = asyncio.Lock()

logger = logging.getLogger("market_fetcher")


# ====================== 1. 代码归一化 ======================
def _normalize_code(code: str) -> str:
    """6 位纯数字代码 → ``sh/sz/bj`` 前缀。

    新浪接口必须带交易所前缀。akshare 不同接口返回的代码格式可能不同
    （有的带前缀如 ``sh600000``，有的只给 ``600000``），统一规范化。
    """
    raw = str(code).strip().lower()
    if not raw:
        return ""
    if raw[:2] in ("sh", "sz", "bj"):
        prefix, body = raw[:2], raw[2:]
    else:
        body = raw.zfill(6)
        prefix = None
    if prefix is None:
        # 沪市 sh：主板 60/68/90、ETF 5xxxxx、LOF/债券 11/13
        if body.startswith(("60", "68", "90", "5", "11", "13")):
            prefix = "sh"
        # 深市 sz：主板 00/20/30、ETF 15/16/18、债券 10/12
        elif body.startswith(("00", "20", "30", "15", "16", "18", "10", "12")):
            prefix = "sz"
        # 北交所 bj：43/83/87/88/92 开头（**只**这几种 8 开头是北交所，**不是所有 8 开头**）
        elif body.startswith(("43", "83", "87", "88", "92")):
            prefix = "bj"
        else:
            prefix = "sh"  # 兜底，避免落空
    return prefix + body


import json
from pathlib import Path

# 本地代码清单缓存路径
CODES_CACHE_FILE = Path(__file__).resolve().parent / "data" / "codes_cache.json"

import concurrent.futures

# ====================== 2. 拉代码清单（同步，run_in_executor 调用）======================
def _fetch_from_sina_codes() -> list[dict[str, str]]:
    """备用源：从新浪行情中心拉全量 A 股（5,500+ 只）"""
    sess = requests.Session()
    sess.trust_env = False
    headers = {"Referer": "http://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    
    def _fetch_page(p: int) -> list[dict[str, str]]:
        url = (
            f"http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={p}&num=100&sort=symbol&asc=1&node=hs_a&symbol=&_s_r_a=init"
        )
        try:
            r = sess.get(url, headers=headers, timeout=5)
            data = r.json()
            if isinstance(data, list):
                res = []
                for item in data:
                    sym = str(item.get("symbol", "")).strip().lower()
                    nm = str(item.get("name", "")).strip()
                    if sym:
                        res.append({"code": sym, "name": nm})
                return res
        except Exception:
            pass
        return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        pages = list(executor.map(_fetch_page, range(1, 60)))
    return [it for page in pages for it in page]


def fetch_all_codes_sync() -> list[dict[str, str]]:
    """从 akshare / 新浪 / 磁盘拉全 A 股代码清单。返回 ``[{"code": "sh600000", "name": "..."}, ...]``。
    
    优化：若本地 data/codes_cache.json 存在且包含完整清单，优先毫秒级直接读取。
    """
    # 0. 优先尝试本地有效磁盘缓存（毫秒级加载）
    if CODES_CACHE_FILE.exists():
        try:
            cached_data = json.loads(CODES_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(cached_data, list) and len(cached_data) >= 3000:
                return cached_data
        except Exception as e:
            logger.warning("read CODES_CACHE_FILE failed: %r", e)

    out: list[dict[str, str]] = []
    # 1. 尝试 akshare
    try:
        df = ak.stock_info_a_code_name()
        for _, row in df.iterrows():
            raw_code = str(row.get("code", "")).strip()
            name = str(row.get("code_name", "")).strip()
            code = _normalize_code(raw_code)
            if code and code != "sh" + "0" * 6:
                out.append({"code": code, "name": name})
    except Exception as e:
        logger.warning("ak.stock_info_a_code_name failed: %r, trying sina fallback", e)

    # 2. 尝试新浪备用源
    if len(out) < 3000:
        try:
            sina_codes = _fetch_from_sina_codes()
            if len(sina_codes) > len(out):
                out = sina_codes
        except Exception as e:
            logger.warning("sina fallback failed: %r, trying disk cache", e)

    # 去重
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for item in out:
        if item["code"] not in seen:
            seen.add(item["code"])
            uniq.append(item)

    if len(uniq) >= 3000:
        try:
            CODES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CODES_CACHE_FILE.write_text(json.dumps(uniq, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("save CODES_CACHE_FILE failed: %r", e)
        return uniq

    return uniq


# ====================== 3. 解析新浪响应 ======================
def _parse_sina_line(line: str) -> tuple[str, dict[str, Any]] | None:
    """解析单行 ``var hq_str_sh600000="...";``。

    新浪字段顺序（个位下标）:
    0 name | 1 open | 2 prev_close | 3 price | 4 high | 5 low
    6 bid | 7 ask | 8 volume(股) | 9 amount(元) | 10..29 其他
    30 行情日期 | 31 行情时间
    """
    if "=" not in line or "hq_str_" not in line:
        return None
    try:
        key_part, value_part = line.split("=", 1)
    except ValueError:
        return None
    code = key_part.replace("var hq_str_", "").strip()
    if not code:
        return None
    value = value_part.strip().rstrip(";").strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    fields = value.split(",")
    if len(fields) < 6 or not fields[0]:
        return None
    # 停牌：当前价等字段是 "--"，float() 会抛 ValueError，走 except 返回 None
    try:
        name = fields[0]
        open_p = float(fields[1])
        prev_close = float(fields[2])
        price = float(fields[3])
        high = float(fields[4])
        low = float(fields[5])
        volume = int(float(fields[8]))
        amount = float(fields[9])
        quote_date = fields[30] if len(fields) > 30 else ""
        quote_time = fields[31] if len(fields) > 31 else ""
        change_pct = (
            (price - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0
        )
    except (ValueError, IndexError):
        return None
    return code, {
        "name": name,
        "open": open_p,
        "prev_close": prev_close,
        "price": price,
        "high": high,
        "low": low,
        "volume": volume,
        "amount": amount,
        "change_pct": round(change_pct, 3),
        "quote_date": quote_date,
        "quote_time": quote_time,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _parse_sina_response(text: str) -> dict[str, dict[str, Any]]:
    """解析整个响应文本，返回 ``{code: {...}}``。"""
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parsed = _parse_sina_line(line)
        if parsed is not None:
            code, data = parsed
            out[code] = data
    return out


# ====================== 4. 异步抓取单批（含重试）======================
async def _fetch_batch(
    session: aiohttp.ClientSession,
    codes: list[str],
    *,
    max_retries: int = MAX_RETRIES,
) -> dict[str, dict[str, Any]]:
    """单批异步请求 + 指数退避重试。返回该批的 ``{code: data}``，失败返回 ``{}``。"""
    url = SINA_URL.format(codes=",".join(codes))
    last_err: str = ""
    for attempt in range(1, max_retries + 1):
        try:
            async with session.get(
                url,
                headers=SINA_HEADERS,
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS),
            ) as resp:
                # 新浪限流会直接 403，强制 backoff
                if resp.status == 403:
                    last_err = "sina 403 (rate-limited)"
                    logger.warning("batch size=%d %s, attempt=%d", len(codes), last_err, attempt)
                else:
                    resp.raise_for_status()
                    text = await resp.text(encoding="gbk")
                    parsed = _parse_sina_response(text)
                    if parsed:
                        return parsed
                    last_err = "empty sina response"
                    logger.warning("batch size=%d %s, attempt=%d", len(codes), last_err, attempt)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_err = repr(exc)
            logger.warning(
                "batch size=%d failed (attempt %d/%d): %s",
                len(codes), attempt, max_retries, last_err,
            )

        # 退避：指数 + 抖动
        if attempt < max_retries:
            backoff = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 0.5)
            await asyncio.sleep(backoff)
    logger.error("batch size=%d gave up after %d retries: %s", len(codes), max_retries, last_err)
    return {}


# ====================== 5. 异步抓取全市场 ======================
async def fetch_all_prices(
    ts_codes: list[str],
    *,
    batch_size: int = BATCH_SIZE,
    concurrency: int | None = None,
) -> int:
    """切片并发抓取全市场行情，更新到 ``all_stocks_cache``。

    参数
    ----
    ts_codes: 全部代码列表（带 sh/sz/bj 前缀）
    batch_size: 每批多少只股票，默认 800
    concurrency: 同时在飞的批次数，默认等于批次数（all-of）
    """
    if not ts_codes:
        logger.warning("no codes to fetch, skip")
        return 0

    batches = [ts_codes[i:i + batch_size] for i in range(0, len(ts_codes), batch_size)]
    n_batches = len(batches)
    concurrency = concurrency or n_batches
    logger.info(
        "fetching %d stocks in %d batches (batch_size=%d, concurrency=%d)",
        len(ts_codes), n_batches, batch_size, concurrency,
    )

    # 用信号量限制同时在飞的批次数，避免瞬时打开 5000+ 连接
    sem = asyncio.Semaphore(concurrency)

    async def _guarded(b: list[str]) -> dict[str, dict[str, Any]]:
        async with sem:
            return await _fetch_batch(session, b)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_guarded(b) for b in batches),
            return_exceptions=True,
        )

    total_updated = 0
    for r in results:
        if isinstance(r, Exception):
            logger.error("batch raised: %r", r)
            continue
        if not r:
            continue
        async with _cache_lock:
            all_stocks_cache.update(r)
        total_updated += len(r)

    async with _cache_lock:
        all_stocks_cache["__meta__"] = {
            **all_stocks_cache.get("__meta__", {}),
            "last_fetch_at": datetime.now().isoformat(timespec="seconds"),
            "last_fetch_count": total_updated,
        }
    logger.info("updated %d stocks (cache size=%d)", total_updated, _cache_size())
    return total_updated


# ====================== 6. 缓存读取工具 ======================
def _cache_size() -> int:
    return sum(1 for k in all_stocks_cache if k != "__meta__")


def get_stock(code: str) -> dict[str, Any] | None:
    """读取单只股票快照。"""
    return all_stocks_cache.get(code)


async def ensure_price_in_cache(code: str) -> dict[str, Any] | None:
    """保证指定代码在 ``all_stocks_cache`` 里有最新价。

    - 如果 cache 中已有，直接返回。
    - 如果没有（典型场景：用户新加的 ETF / 刚上市的股票 / 全市场列表漏掉的），
      立即向新浪单只拉一次（也走切片 + 重试），写回 cache 再返回。

    用于支持 watchlist 里加 ETF 也能立刻拿到行情。
    """
    code = (code or "").strip().lower()
    if not code:
        return None
    cached = all_stocks_cache.get(code)
    if cached:
        return cached
    if not re.match(r"^(sh|sz|bj)\d{6}$", code):
        return None
    try:
        async with aiohttp.ClientSession() as session:
            result = await _fetch_batch(session, [code])
        return result.get(code) or all_stocks_cache.get(code)
    except Exception as e:
        logger.warning("ensure_price_in_cache(%s) failed: %r", code, e)
        return None


async def fetch_watchlist_prices(codes: list[str]) -> int:
    """并发拉一批"非全市场"代码（典型：ETF）的实时价，写入 cache。

    适用于 watchlist 中存在但不在 ``__meta__.codes`` 里的代码。
    """
    if not codes:
        return 0
    # 过滤出 cache 里没有的
    targets = [c for c in codes if c not in all_stocks_cache]
    if not targets:
        return 0
    # 复用 fetch_all_prices 的批量并发机制
    updated = await fetch_all_prices(targets, batch_size=BATCH_SIZE, concurrency=4)
    if updated:
        logger.info("watchlist tickers refreshed: %d/%d", updated, len(targets))
    return updated


def get_all_stocks() -> dict[str, dict[str, Any]]:
    """返回所有股票快照（去掉 ``__meta__``）。"""
    return {k: v for k, v in list(all_stocks_cache.items()) if k != "__meta__"}


def get_meta() -> dict[str, Any]:
    return dict(all_stocks_cache.get("__meta__", {}))


# ====================== 6.5 历史 K 线 ======================
def _empty_history() -> dict[str, Any]:
    return {
        "avg_volume_5d": 0.0,
        "avg_amount_5d": 0.0,
        "data": [],
    }


def fetch_history_sync(code: str, days: int = HISTORY_FETCH_DAYS) -> dict[str, Any]:
    """从腾讯接口拉单只股票最近 N 个交易日的日 K 线。

    返回结构::

        {
            "avg_volume_5d": float,    # 单位：股
            "avg_amount_5d": float,    # 单位：元
            "data": [ {date, open, close, high, low, volume_lots}, ... ],
        }

    失败时返回 ``_empty_history()``（不抛异常，避免阻塞批处理）。
    """
    try:
        # 不读环境代理：腾讯域名在国内基本不靠代理，绕开 Windows 系统代理
        sess = requests.Session()
        sess.trust_env = False
        resp = sess.get(
            TENCENT_KLINE_URL,
            params={"param": f"{code},day,,,{days},qfq"},
            headers=TENCENT_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 0:
            return _empty_history()
        data = (payload.get("data") or {}).get(code) or {}
        klines = data.get("qfqday") or data.get("day") or []
        if not klines:
            return _empty_history()
        records: list[dict[str, Any]] = []
        for k in klines:
            # 字段：date, open, close, high, low, volume(手)
            try:
                records.append(
                    {
                        "date": str(k[0]),
                        "open": float(k[1]),
                        "close": float(k[2]),
                        "high": float(k[3]),
                        "low": float(k[4]),
                        "volume_lots": float(k[5]),  # 1 手 = 100 股
                    }
                )
            except (ValueError, IndexError):
                continue
        if not records:
            return _empty_history()
        # 取最近 5 个交易日
        last5 = records[-5:]
        avg_volume_lots = sum(r["volume_lots"] for r in last5) / len(last5)
        avg_volume_shares = avg_volume_lots * 100.0  # 手 → 股
        avg_price = sum((r["open"] + r["close"]) / 2.0 for r in last5) / len(last5)
        avg_amount = avg_price * avg_volume_shares  # 元
        return {
            "avg_volume_5d": round(avg_volume_shares, 2),
            "avg_amount_5d": round(avg_amount, 2),
            "data": records,
        }
    except Exception as e:
        logger.warning("fetch history for %s failed: %r", code, e)
        return _empty_history()


async def fetch_history_for_codes(
    codes: list[str],
    *,
    concurrency: int = HISTORY_FETCH_CONCURRENCY,
) -> int:
    """并发拉一批股票的历史 K 线，更新到 ``history_cache``。返回成功条目数。"""
    if not codes:
        return 0
    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()

    async def _guarded(code: str) -> tuple[str, dict[str, Any]]:
        async with sem:
            data = await loop.run_in_executor(None, fetch_history_sync, code)
            return code, data

    results = await asyncio.gather(*(_guarded(c) for c in codes))
    async with _history_lock:
        for code, data in results:
            history_cache[code] = data
    valid = sum(1 for _, d in results if d.get("data"))
    logger.info(
        "history refreshed: %d codes, %d valid (concurrency=%d)",
        len(codes), valid, concurrency,
    )
    return len(results)


def get_history(code: str) -> dict[str, Any]:
    """读取单只股票历史快照。空时返回 ``_empty_history()`` 形状的 dict。"""
    return history_cache.get(code) or _empty_history()


async def ensure_history_for_codes(
    codes: list[str],
    *,
    min_records: int = 60,
    concurrency: int = 4,
) -> dict[str, dict[str, Any]]:
    """Return cached history and fetch only symbols lacking a usable K-line window."""
    unique_codes = list(dict.fromkeys(codes))
    missing = [
        code for code in unique_codes
        if len((history_cache.get(code) or {}).get("data") or []) < min_records
    ]
    if missing:
        await fetch_history_for_codes(missing, concurrency=concurrency)
    return {code: get_history(code) for code in unique_codes}


def get_history_meta() -> dict[str, Any]:
    return {
        "size": len(history_cache),
        "codes_with_data": sum(1 for v in history_cache.values() if v.get("data")),
    }


# ====================== 6.6 板块资金流向 ======================
# 资金流向数据来自 akshare 的 stock_fund_flow_concept(symbol="即时")，
# 返回 300 行概念板块。主力资金列在 DataFrame 里叫"净额"（单位：亿元）。
# 这个接口不像股票行情那样高频刷新 —— 东方财富大概 1 分钟一次。
# 所以我们 60 秒后台拉一次，缓存里拿。

FUND_FLOW_CACHE_TTL_SECONDS = 60  # 后台轮询间隔
FUND_FLOW_REFRESH_CONCURRENCY = 1  # 拉一次就够

fund_flow_cache: dict[str, Any] = {
    "data": [],         # [{name, change_pct, net_amount, inflow, outflow, leading_stock, leading_change_pct, company_count, sector_index, current_price, unit}, ...]
    "refreshed_at": None,
}
_fund_flow_lock = asyncio.Lock()


def fetch_fund_flow_sync() -> list[dict[str, Any]]:
    """从 akshare 同步拉一次"概念板块"资金流向，返回 300 条结构化记录。

    失败返回 ``[]``（不抛异常）。同一进程内多次调用安全：
    akshare 在底层做了缓存（最近一次的结果直接复用），不会反复打远端。
    """
    try:
        df = ak.stock_fund_flow_concept(symbol="即时")
    except Exception as e:
        logger.warning("fetch_fund_flow_sync failed: %r", e)
        return []
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            name = str(row.get("行业", "")).strip()
            if not name:
                continue
            net_amount = float(row.get("净额", 0) or 0)
            out.append(
                {
                    "name": name,
                    "change_pct": float(row.get("行业-涨跌幅", 0) or 0),
                    "net_amount": net_amount,
                    "inflow": float(row.get("流入资金", 0) or 0),
                    "outflow": float(row.get("流出资金", 0) or 0),
                    "leading_stock": str(row.get("领涨股", "")).strip(),
                    "leading_change_pct": float(row.get("领涨股-涨跌幅", 0) or 0),
                    "company_count": int(row.get("公司家数", 0) or 0),
                    "sector_index": float(row.get("行业指数", 0) or 0),
                    "current_price": float(row.get("当前价", 0) or 0),
                    "unit": "亿",
                }
            )
        except (ValueError, TypeError):
            continue
    return out


async def fetch_fund_flow() -> dict[str, Any]:
    """异步：拉一次板块资金流向，刷新缓存。返回新的 cache。"""
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, fetch_fund_flow_sync)
    async with _fund_flow_lock:
        if not data:
            # v4.2: 空结果保留旧缓存（akshare 瞬时失败很常见），避免把已有板块数据冲掉
            logger.warning(
                "fund flow refresh returned empty, keep old cache (%d sectors)",
                len(fund_flow_cache["data"]),
            )
            return dict(fund_flow_cache)
        fund_flow_cache["data"] = data
        fund_flow_cache["refreshed_at"] = datetime.now().isoformat(timespec="seconds")
    logger.info("fund flow refreshed: %d sectors", len(data))
    return dict(fund_flow_cache)


def get_fund_flow() -> dict[str, Any]:
    """读取板块资金流向缓存（不触发网络请求）。"""
    return dict(fund_flow_cache)


async def periodic_fund_flow_loop() -> None:
    """后台协程：每 60 秒拉一次板块资金流向，写入 fund_flow_cache。"""
    while True:
        try:
            await fetch_fund_flow()
        except Exception:
            logger.exception("periodic fund flow fetch crashed, will retry next tick")
        await asyncio.sleep(FUND_FLOW_CACHE_TTL_SECONDS)


# ====================== 7. 每日刷新代码 ======================
async def refresh_codes_daily() -> int:
    """每日任务：akshare 拉代码 + 写入 ``__meta__``。返回代码条数。"""
    loop = asyncio.get_running_loop()
    try:
        code_list = await loop.run_in_executor(None, fetch_all_codes_sync)
    except Exception:
        logger.exception("fetch codes from akshare failed")
        return 0
    async with _cache_lock:
        all_stocks_cache["__meta__"] = {
            **all_stocks_cache.get("__meta__", {}),
            "codes": [c["code"] for c in code_list],
            "code_refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "code_count": len(code_list),
        }
    logger.info("refreshed %d stock codes", len(code_list))
    return len(code_list)


# ====================== 8. 5 秒轮询循环 ======================
async def periodic_fetch_loop(
    history_codes_provider=None,
    watchlist_codes_provider=None,
) -> None:
    """后台协程：每 5 秒拉一次全市场行情 + watchlist 中"非全市场"代码（ETF）。

    参数
    ----
    history_codes_provider: 可调用对象，返回当前要拉历史的代码列表。
        由调用方注入（如 ``main.py`` 提供读 watchlist 的函数），保持模块解耦。
        传 None 则不自动补拉历史。
    watchlist_codes_provider: 可调用对象，返回 watchlist 全部代码。
        协程每轮会 diff 出"不在 all_stocks_cache 的"代码（典型为 ETF），
        调用 ``fetch_watchlist_prices`` 单独拉一次，保证 watchlist 里的
        ETF 也能 5 秒更新一次。
    """
    cycle = 0
    while True:
        meta = all_stocks_cache.get("__meta__", {})
        codes = meta.get("codes", [])
        if codes:
            try:
                await fetch_all_prices(codes)
            except Exception:
                logger.exception("periodic fetch crashed, will retry next tick")
        else:
            logger.info("no codes in cache yet, run refresh_codes_daily() first")

        # 拉 watchlist 里的"非全市场"代码（典型 ETF）
        if watchlist_codes_provider is not None:
            try:
                wl_codes = watchlist_codes_provider() or []
                if wl_codes:
                    await fetch_watchlist_prices(wl_codes)
            except Exception:
                logger.exception("watchlist tickers fetch crashed")

        # 每 N 轮补拉一次历史（处理启动后新增 watchlist）
        cycle += 1
        if history_codes_provider is not None and cycle % HISTORY_REFRESH_INTERVAL_CYCLES == 0:
            try:
                hist_codes = history_codes_provider() or []
                if hist_codes:
                    await fetch_history_for_codes(hist_codes)
            except Exception:
                logger.exception("periodic history refresh crashed")

        await asyncio.sleep(FETCH_INTERVAL_SECONDS)


# ====================== 9. APScheduler 集成 ======================
def start_scheduler() -> AsyncIOScheduler:
    """构造并配置 AsyncIOScheduler（不 start）。调用方自行 ``scheduler.start()``。"""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        refresh_codes_daily,
        CronTrigger(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE),
        id="refresh_codes_daily",
        replace_existing=True,
        misfire_grace_time=300,
    )
    return scheduler


# ====================== 10. 长驻入口 ======================
async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    scheduler = start_scheduler()
    scheduler.start()
    logger.info(
        "scheduler started: refresh_codes_daily @ %02d:%02d %s",
        SCHEDULE_HOUR, SCHEDULE_MINUTE, TIMEZONE,
    )

    # 启动时立刻拉一次代码 + 一次行情
    n = await refresh_codes_daily()
    if n > 0:
        codes = all_stocks_cache["__meta__"]["codes"]
        await fetch_all_prices(codes)

    # 进入 5 秒轮询
    await periodic_fetch_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("shutdown by user")
