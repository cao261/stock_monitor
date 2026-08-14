"""v4.1: 7x24 财经快讯 fetcher。

数据源三级降级（v4.1 强韧性设计）:
  1. 主源: 财联社电报 (https://www.cls.cn/nodeapi/updateTelegraphList) - aiohttp
  2. 备选: 新浪财经 7x24 (https://feed.mix.sina.com.cn/api/roll/get) - aiohttp
  3. 兜底: akshare.stock_info_global_cls() - 同步 (在 executor 里跑)

防封禁:
  - 10 分钟内存缓存（get_news() 直接读，不发网络）
  - 后台每 NEWS_REFRESH_INTERVAL_SECONDS 秒补拉一次
  - 单源失败重试 2 次，间隔 3 秒
  - 拉取为空时**保留旧缓存**（避免被空覆盖）
  - 三个源都失败时返回旧缓存 + error 字段
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger("news_fetcher")

# ====================== 配置 ======================
NEWS_CACHE_TTL_SECONDS = 600              # 缓存 10 分钟
NEWS_REFRESH_INTERVAL_SECONDS = 600       # 后台每 10 分钟拉一次
NEWS_MAX_ITEMS = 50                       # 最多保留 50 条
NEWS_HTTP_TIMEOUT_SECONDS = 8             # 单次 HTTP 超时

# ===== 财联社 主源 =====
CLS_URL = "https://www.cls.cn/nodeapi/updateTelegraphList"
CLS_PARAMS = {
    "app": "CailianpressWeb",
    "category": "",
    "hasFirstVipArticle": "1",
    "os": "web",
    "refresh_type": "1",
    "rn": str(NEWS_MAX_ITEMS * 2),  # 多拉点，过滤后保留 50
    "sv": "7.7.5",
}
CLS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.cls.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ===== 新浪 7x24 备选 =====
# lid=2516 是「财经要闻」专题（含 A 股焦点新闻 + 大宗商品 + 政策）
# lid=1686 是「股市」专题（窄一些；2516 覆盖更广）
SINA_URL = "https://feed.mix.sina.com.cn/api/roll/get"
SINA_PARAMS = {
    "pageid": "153",
    "lid": "2516",
    "num": str(NEWS_MAX_ITEMS * 2),
    "page": "1",
    "format": "json",
}
SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn/",
    "Accept": "application/json, text/plain, */*",
}

# ====================== 内存缓存 ======================
_news_cache: dict[str, Any] = {
    "data": [],          # [{title, content, time, source, url, id}, ...]
    "fetched_at": None,  # ISO 字符串
    "source": None,      # "cls" | "sina" | "akshare" | None
    "error": None,       # 最近的错误信息（前端可降级提示）
}
_news_lock = asyncio.Lock()


# ====================== 归一化 ======================
def _normalize_cls_item(it: dict) -> dict | None:
    """把财联社 JSON 节点转成统一结构。"""
    title = (it.get("title") or "").strip()
    content = (it.get("content") or "").strip()
    if not (title or content):
        return None
    ctime = it.get("ctime")
    try:
        time_str = (
            datetime.fromtimestamp(int(ctime)).isoformat(timespec="seconds")
            if ctime else None
        )
    except Exception:
        time_str = None
    item_id = it.get("id")
    return {
        "id": str(item_id or it.get("shareurl") or f"cls-{time_str}"),
        "title": title,
        "content": content,
        "time": time_str,
        "source": "财联社",
        "url": (it.get("shareurl") or (f"https://www.cls.cn/detail/{item_id}" if item_id else None)),
    }


def _normalize_sina_item(it: dict) -> dict | None:
    """把新浪 JSON 节点转成统一结构。"""
    title = (it.get("title") or "").strip()
    if not title:
        return None
    intro = (it.get("intro") or "").strip()
    content = intro if intro else title
    ctime = it.get("ctime")
    try:
        time_str = (
            datetime.fromtimestamp(int(ctime)).isoformat(timespec="seconds")
            if ctime else None
        )
    except Exception:
        time_str = None
    item_id = it.get("docid") or it.get("oid")
    return {
        "id": str(item_id or f"sina-{time_str}"),
        "title": title,
        "content": content,
        "time": time_str,
        "source": "新浪财经",
        "url": it.get("url") or it.get("wapurl"),
    }


def _normalize_akshare_df(df) -> list[dict]:
    """akshare.stock_info_global_cls() 返回 DataFrame，转成统一结构。"""
    items: list[dict] = []
    try:
        records = df.to_dict(orient="records")
    except Exception:
        return []
    for r in records:
        title = (r.get("标题") or r.get("title") or "").strip()
        content = (r.get("内容") or r.get("content") or "").strip()
        if not (title or content):
            continue
        time_str = str(
            r.get("发布时间") or r.get("时间") or r.get("ctime") or ""
        )
        items.append({
            "id": f"ak-{len(items)}",
            "title": title,
            "content": content,
            "time": time_str,
            "source": "财联社(akshare)",
            "url": None,
        })
    return items


# ====================== 三个源的具体抓取 ======================
async def _fetch_from_cls() -> list[dict]:
    """从财联社拿快讯。失败抛异常。"""
    timeout = aiohttp.ClientTimeout(total=NEWS_HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(CLS_URL, params=CLS_PARAMS, headers=CLS_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    roll = ((data or {}).get("data") or {}).get("roll_data") or []
    items: list[dict] = []
    for it in roll:
        n = _normalize_cls_item(it)
        if n:
            items.append(n)
    return items[:NEWS_MAX_ITEMS]


async def _fetch_from_sina() -> list[dict]:
    """从新浪 7x24 拿快讯。失败抛异常。"""
    timeout = aiohttp.ClientTimeout(total=NEWS_HTTP_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(SINA_URL, params=SINA_PARAMS, headers=SINA_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    result = (data or {}).get("result") or {}
    items: list[dict] = []
    for it in result.get("data", []) or []:
        n = _normalize_sina_item(it)
        if n:
            items.append(n)
    return items[:NEWS_MAX_ITEMS]


def _fetch_from_akshare_sync() -> list[dict]:
    """akshare 兜底（同步）。"""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()
    except Exception as e:
        logger.warning("akshare stock_info_global_cls failed: %r", e)
        return []
    return _normalize_akshare_df(df)


# ====================== 主流程：三级降级 ======================
async def _fetch_news() -> tuple[list[dict], str | None, str | None]:
    """主备三级降级：财联社 → 新浪 → akshare。
    Returns:
        (items, source, error_msg)
    """
    # 1) 财联社
    for attempt in range(2):
        try:
            items = await _fetch_from_cls()
            if items:
                return items, "cls", None
            logger.warning("cls returned 0 items, falling through to next source")
            break
        except Exception as e:
            logger.warning("cls fetch failed (attempt %d): %r", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(3)
    # 2) 新浪
    for attempt in range(2):
        try:
            items = await _fetch_from_sina()
            if items:
                return items, "sina", "财联社不可用，已降级到新浪 7x24"
            logger.warning("sina returned 0 items, falling through to next source")
            break
        except Exception as e:
            logger.warning("sina fetch failed (attempt %d): %r", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(3)
    # 3) akshare 兜底
    try:
        loop = asyncio.get_running_loop()
        items = await loop.run_in_executor(None, _fetch_from_akshare_sync)
        if items:
            return items, "akshare", "aiohttp 源不可用，已降级到 akshare 财联社"
    except Exception as e:
        logger.warning("akshare fallback failed: %r", e)
    return [], None, "所有新闻源都不可用"


# ====================== 公开 API ======================
async def refresh_news() -> dict[str, Any]:
    """拉一次新快讯，更新 cache。返回新 cache。

    设计: 如果新拉到的有数据，更新；否则保留旧 cache (避免被空覆盖)。
    """
    items, source, err = await _fetch_news()
    async with _news_lock:
        if items:
            _news_cache["data"] = items
            _news_cache["fetched_at"] = datetime.now().isoformat(timespec="seconds")
            _news_cache["source"] = source
            _news_cache["error"] = err
        else:
            _news_cache["error"] = err or "本次拉取为空，保留旧缓存"
            logger.warning("news refresh returned empty: %s", _news_cache["error"])
    logger.info(
        "news refreshed: source=%s items=%d err=%s",
        _news_cache["source"], len(_news_cache["data"]), _news_cache["error"],
    )
    return dict(_news_cache)


def get_news() -> dict[str, Any]:
    """读内存中的快讯 cache。**不发网络请求**——由后台 periodic_news_loop 维持新鲜度。"""
    return dict(_news_cache)


def is_news_cache_fresh() -> bool:
    """cache 是否还在 TTL 内。"""
    fa = _news_cache.get("fetched_at")
    if not fa:
        return False
    try:
        age = (datetime.now() - datetime.fromisoformat(fa)).total_seconds()
        return age < NEWS_CACHE_TTL_SECONDS
    except Exception:
        return False


# ====================== 后台协程 ======================
async def periodic_news_loop() -> None:
    """后台协程：每 NEWS_REFRESH_INTERVAL_SECONDS 秒拉一次。"""
    # 启动时立即拉一次（不等满 10 分钟）
    try:
        await refresh_news()
    except Exception:
        logger.exception("initial news fetch failed, will retry periodically")
    while True:
        await asyncio.sleep(NEWS_REFRESH_INTERVAL_SECONDS)
        try:
            await refresh_news()
        except Exception:
            logger.exception("periodic news refresh crashed, will retry next tick")
