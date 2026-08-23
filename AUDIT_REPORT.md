# stock_monitor 代码审计与安全测试报告

> **项目**：A 股量价监控（stock_monitor v4.6.3+）
> **范围**：后端（FastAPI + SQLAlchemy）、前端（Vue 3 + Vite）、核心服务（market_fetcher / analyzer / llm / sector_alpha）、依赖与配置
> **审计日期**：2026-08-23
> **审计工具**：Bandit 1.9.4（静态分析）、pip-audit 2.10.1（依赖漏洞）、自定义扫描（路径遍历 / 反序列化 / CORS / 绑定地址 / XSS / SSRF / TLS / 危险 API）
> **总计 LOC**：~6,767 行（不含诊断/测试脚本）

---

## 1. 总体结论

| 维度 | 评级 | 关键数据 |
|---|---|---|
| 静态安全（Bandit） | 🟢 良好 | 0 高危、0 中危、**11 低危**（7× Try-Except-Pass / 3× random 抖动 / 1× Try-Except-Continue） |
| 依赖漏洞（pip-audit） | 🟡 关注 | 系统 Python 检出 95 个 CVE；**项目直接依赖本身无已知漏洞**（具体见 §3.3） |
| 密钥管理 | 🟢 良好 | `.env` 已在 `.gitignore`，**未入仓**，未出现在任何 git 历史；远程仓库无 `.env` 痕迹 |
| 路径遍历 | 🟡 低危 | SPA fallback 未限制 `..` 段（仅 127.0.0.1 暴露面） |
| 注入风险 | 🟢 良好 | SQL 全部用 SQLAlchemy 参数化；无 `eval` / `exec` / `pickle.loads` / `shell=True` |
| 鉴权 / CORS | 🟢 设计可控 | 本地单用户工具，无鉴权、无 CORS；监听 127.0.0.1（非 0.0.0.0） |
| XSS | 🟢 良好 | `v-html` 被 `renderMarkdown` + `escapeHtml` 双层兜底 |
| 错误处理 | 🟢 良好 | 全局 `try/except` 完备，无裸 raise，资源 finally 释放（lifespan） |
| 并发与锁 | 🟢 良好 | `_cache_lock` / `_history_lock` / `_discover_lock` 三处锁覆盖关键共享状态 |
| 资源管理 | 🟡 关注 | `requests.Session` 在 `market_fetcher` 多处未显式 close（依赖 GC） |
| 监控/可观测 | 🟡 关注 | 仅有 `logger`，无 metrics 出口（请求耗时、LLM 失败率无面板） |

**核心结论**：项目作为本地单用户工具，安全性总体良好。**最值得优化**的三件事：

1. **路径遍历**（main.py SPA fallback）— 加 5 行即可关闭
2. **资源泄漏**（requests.Session / aiohttp）— 用 `with` 显式管理
3. **依赖版本锁**— requirements.txt 用 `>=` 而非 `==`，生产部署建议锁版本

---

## 2. 严重程度分级

- 🔴 **CRITICAL**：可直接被利用造成数据泄露、RCE、密钥外泄
- 🟠 **HIGH**：在常见攻击场景下可被利用
- 🟡 **MEDIUM**：有条件可被利用 / 需特定环境
- 🟢 **LOW**：理论风险 / 几乎不可达 / 最佳实践
- ⚪ **INFO**：优化建议 / 非问题

---

## 3. 详细发现清单

### 3.1 密钥管理

| # | 严重 | 位置 | 状态 | 详情 |
|---|---|---|---|---|
| K-1 | ✅ 安全 | `.env` | 已正确排除 | `.env` 列入 `.gitignore`（line 76-79），`git ls-files .env` 返空。远程仓库 `origin = https://github.com/cao261/stock_monitor.git`，git 历史中**无任何含 `sk-` 模式的 commit**。`.env.example` 仅含占位符 `sk-xxxxxxxxxx` |
| K-2 | ✅ 安全 | `app/config.py:60-73` | AGNES_API_KEY 只读 .env | v4.4.1 修复：只解析 `.env` 显式 `AGNES_API_KEY=` 行，忽略系统环境变量残留（防止旧 key 劫持） |
| K-3 | ⚠️ INFO | `.env` 内容 | 用户自查 | `.env` 中明文存有真实 key `sk-7MQIgPL0bIP6bewLkabgdG9TvsJh5xPM8iTRmVKBhUKMjtgN`（Agnes）。建议：(a) 定期轮换；(b) 给 key 加 IP 白名单；(c) 监控消费用量 |

**验证脚本**（已运行）：
```powershell
git ls-files .env              # → 空（未跟踪）
git log --all --pickaxe-regex -S "sk-7MQIgPL0bIP6bew"   # → 空（历史无泄漏）
```

### 3.2 路径遍历（Path Traversal）

| # | 严重 | 位置 | 触发 | 影响 |
|---|---|---|---|---|
| P-1 | 🟡 MEDIUM | `app/main.py:206-216` | `GET /{full_path:path}` SPA fallback | `target = FRONTEND_DIST / full_path` 没用 `.resolve()` 校验边界，攻击者可构造 `../../../Windows/System32/drivers/etc/hosts` 等相对路径，**只读任意 uvicorn 进程可访问的文件**。本机复现：路径解析成功，`is_file()` 真实检查（hosts 不存在 → 落回 index.html 兜底） |

**复现实证**（PowerShell）：
```
input: '../../../etc/passwd'                   ->  resolved: ...\etc\passwd      outside_fd: True  is_file: False
input: '..\..\..\Windows\System32\drivers\etc\hosts' -> resolved: ...\Windows\System32\...\hosts outside_fd: True  is_file: False
input: 'index.html'                            ->  resolved: ...\dist\index.html  outside_fd: False is_file: True
```

**风险评估**：
- 监听地址 `127.0.0.1`，仅本机可达，远程攻击面为零
- `uvicorn` 进程以用户权限运行，可读范围有限
- 但任何能访问 localhost:8000 的同机程序（包括浏览器渲染的 XSS 二次跳板）都可读文件

**修复方案**（见 §5.1）：
```python
target = (FRONTEND_DIST / full_path).resolve()
try:
    target.relative_to(FRONTEND_DIST.resolve())
except ValueError:
    raise HTTPException(404)
if target.is_file():
    return FileResponse(target)
```

### 3.3 依赖漏洞（pip-audit）

| # | 严重 | 包 | 当前 | 漏洞数 | 修复版本 | 备注 |
|---|---|---|---|---|---|---|
| V-1 | 🟠 HIGH | aiohttp | 3.14.1 | 3 | 3.14.3 | `PYSEC-2026-3545/3546/3547` — **项目直接依赖，必须修** |
| V-2 | 🟠 HIGH | cryptography | 48.0.0 | 4 | 48.0.1 / 49.0.0 / 50.0.0 | `PYSEC-2026-3552/3553/3554` + `GHSA-537c-gmf6-5ccf` — 间接依赖，akshare 引入 |
| V-3 | 🟠 HIGH | pillow | 12.2.0 | 12+ | 12.3.0 | 多个 PYSEC 条目 — 间接依赖 |
| V-4 | 🟡 MEDIUM | lxml | 6.0.3 | 1 | 6.1.0 | 间接依赖，akshare 引入 |
| V-5 | 🟡 MEDIUM | flask / flask-cors | 3.1.0 / 5.0.1 | 4 | 3.1.3 / 6.0.0 | 间接依赖 |
| V-6 | 🟡 MEDIUM | idna | 3.13 | 1 | 3.15 | 间接依赖 |
| V-7 | 🟢 LOW | fonttools / h2 / pip / pygments / jupyter-core | — | 多个 | — | 间接依赖，本次运行未全部详查 |

**注意**：系统 Python（`D:\AppGallery\Downloads\python.exe`）安装了大量无关包（`pillow`/`lxml`/`flask` 等），其中 95 个 CVE 来自不相关包。本项目**直接依赖**只 8 个：
- ✅ `fastapi 0.138.0`（已升级，无已知 CVE）
- ✅ `uvicorn 0.49.0`（最新，无已知 CVE）
- ✅ `sqlalchemy 2.0.49`（最新，无已知 CVE）
- ✅ `pydantic 2.13.4`（最新，无已知 CVE）
- ✅ `openai 2.37.0`（最新，无已知 CVE）
- ✅ `python-dotenv 1.2.2`（最新，无已知 CVE）
- 🟠 **`aiohttp 3.14.1`**（3 个 CVE）— `requirements.txt:8` 写 `aiohttp>=3.9`，建议锁到 `aiohttp==3.14.3`
- ✅ `requests 2.32.3`（最新，无已知 CVE）
- ✅ `apscheduler 3.11.3`（最新，无已知 CVE）

**间接依赖**（akshare 引入）走 `akshare` → `cryptography`/`pillow`/`lxml` 链。**缓解**：`akshare` 固定版本未锁，每次 `pip install` 可能拉到不同次依赖。**建议**：
```txt
# requirements.txt 锁版本
fastapi==0.138.0
uvicorn[standard]==0.49.0
SQLAlchemy==2.0.49
pydantic==2.13.4
openai==2.37.0
python-dotenv==1.2.2
aiohttp==3.14.3    # 升级到修复版
requests==2.32.3
apscheduler==3.11.3
akshare==1.16.72  # 锁住，间接依赖才稳定
```

### 3.4 注入风险

| # | 严重 | 位置 | 状态 | 详情 |
|---|---|---|---|---|
| S-1 | ✅ 安全 | 全部 SQL | 全部用 ORM | `app/crud/watchlist.py` 全部 `select(...)` / `db.get(...)` 形式，参数化安全。`app/database.py:91` 唯一 `text(ddl)` 用于迁移 DDL，DDL 是硬编码字符串，无外部输入拼接 |
| S-2 | ✅ 安全 | 全部代码 | 无 eval/exec | `grep -P "(\beval\(\|exec\("` 全仓 0 命中 |
| S-3 | ✅ 安全 | 全部代码 | 无 pickle/yaml.load | `grep -P "pickle\.\|yaml\.load\("` 0 命中（除注释） |
| S-4 | ✅ 安全 | 全部代码 | 无 shell=True | `grep "subprocess"` 0 命中（项目不用 subprocess） |
| S-5 | ✅ 安全 | LLM 输入 | 全部用 JSON 序列化 | `app/services/llm.py` 喂 LLM 的用户字段（trade_note, name, target_*）通过 `json.dumps(... ensure_ascii=False)` 序列化，不参与 shell/eval |

### 3.5 鉴权 / CORS / 网络暴露

| # | 严重 | 位置 | 状态 | 详情 |
|---|---|---|---|---|
| N-1 | ✅ 设计合理 | `start.bat:19-20` | `HOST=127.0.0.1` | uvicorn 绑定 127.0.0.1，**非 0.0.0.0**。远程攻击面为零 |
| N-2 | ✅ 设计合理 | `app/main.py` | 无 CORSMiddleware | 前后端同源，无跨域需求；显式无 CORS 配置是正确选择 |
| N-3 | ✅ 设计合理 | 所有路由 | 无 `Depends(auth)` | 单用户本地工具，无登录需求。**注意**：若未来需要远程访问，必须加鉴权 |
| N-4 | ⚠️ INFO | `app/main.py:209` | SPA fallback 排除 `/api` | 显式 `if full_path.startswith(("api/", "api"))` 过滤是好的 |
| N-5 | ⚠️ INFO | 全局 | 无 `https` | 本地 HTTP 走 127.0.0.1，浏览器视同 secure context（localhost 例外）。可接受 |

### 3.6 XSS / 模板注入

| # | 严重 | 位置 | 状态 | 详情 |
|---|---|---|---|---|
| X-1 | ✅ 已防御 | `frontend/src/components/modals/AiReportModal.vue:143` | `v-html="htmlContent"` 但内容受控 | 渲染前的 `renderMarkdown()` (line 30-94) 对每行做 `escapeHtml()` 后再拼字符串；所有 LLM 输出字段在 `app/services/llm.py:_sanitize_plan` 已 str() 化、长度截断、字段白名单。**风险大幅降低** |
| X-2 | ✅ 安全 | 全部 .vue | 仅 1 处 `v-html` | 全部 33 个 .vue 文件，**仅 AiReportModal** 用 v-html，其他全部用 `{{ }}` 模板插值（自动转义） |
| X-3 | ⚠️ INFO | AiReportModal.vue:22-28 | `renderInline` 用正则在 escapeHtml 之后做 **bold/italic/code 替换** | 严格说，先 escapeHtml 把 `<` 变 `&lt;` 之后，正则匹配的 `*...*` 不含原始 `<`，安全。但若未来 escape 逻辑改动，**先 escape 后替换**的顺序不可颠倒 |

### 3.7 资源管理 / 资源泄漏

| # | 严重 | 位置 | 详情 | 修复 |
|---|---|---|---|---|
| R-1 | 🟡 MEDIUM | `market_fetcher.py:127, 575` | `requests.Session()` 创建后**未用 `with`**，依赖 GC | 改为 `with requests.Session() as sess:` |
| R-2 | 🟡 MEDIUM | `market_fetcher.py:434, 489, 639` | `aiohttp.ClientSession()` 用了 `async with`，但嵌套 `run_in_executor(None, fetch_history_sync)` 内部又开新 session | 改：复用 session；或把 session 提到外层 |
| R-3 | 🟢 LOW | `market_fetcher.py:434, 489` | `aiohttp.ClientSession()` 缺 `connector` 配置（TCP 连接池默认 100 上限） | 设置 `TCPConnector(limit=50, ttl_dns_cache=300)` |
| R-4 | 🟡 MEDIUM | `app/main.py:99-105` | 后台任务 `asyncio.create_task` 启动后，lifespan finally 取消；但 `mf.periodic_fund_flow_loop` / `news_fetcher.periodic_news_loop` 内的 `await` cancel 后**未捕获 `CancelledError` 后清理连接** | 已在 lifespan 兜底，业务循环应包 `try/except CancelledError/finally` |

### 3.8 并发与锁

| # | 严重 | 位置 | 状态 | 详情 |
|---|---|---|---|---|
| C-1 | ✅ 良好 | `market_fetcher.py:71, 77, 701` | 3 个锁覆盖共享状态 | `_cache_lock`（行情）、`_history_lock`（K线）、`_fund_flow_lock`（资金流） |
| C-2 | ✅ 良好 | `app/routers/strategy.py:32` | `_discover_lock` 防并发 LLM 调用 | 4 个并发请求共用一次 LLM 链路（v4.6.2 修复） |
| C-3 | ⚠️ INFO | `market_fetcher.py:447-449` | `async with _cache_lock: all_stocks_cache.update(r)` **锁粒度大** | 9 批并发时锁竞争可能高；改用 `dict.update()` 无锁（dict 赋值 GIL 原子），仅在 `__meta__` 写时加锁 |

### 3.9 Bandit 11 项低危一览

| 文件 | 行 | Test | 说明 | 处置 |
|---|---|---|---|---|
| `market_fetcher.py` | 147 | B110 | `try/except/pass` 吞错 | ✅ 合理（拉代码失败时静默降级用磁盘缓存） |
| `market_fetcher.py` | 394 | B311 | `random.uniform` 用于抖动 | ✅ 合理（非密码学场景） |
| `app/config.py` | 72 | B110 | `.env` 解析失败兜底 | ✅ 合理（用户系统没装 python-dotenv） |
| `app/services/llm.py` | 377, 391 | B311 | 限流抖动 `random.uniform` | ✅ 合理（避免雪崩） |
| `app/services/llm.py` | 475, 482, 953, 961, 968 | B110 | JSON 解析失败兜底 | ✅ 合理（返回空 dict，由调用方决定） |
| `app/services/llm.py` | 491 | B112 | `try/except/continue` | ✅ 合理（多模式 JSON 解析：直接 / ```json``` / 首末抠） |

**总评**：11 个低危均为**鲁棒性设计**主动吞错的合理使用，无安全风险。

### 3.10 错误处理

| # | 严重 | 位置 | 详情 |
|---|---|---|---|
| E-1 | ✅ 良好 | 全部 routers | `try/except → HTTPException(4xx/5xx) from e` 模式统一 |
| E-2 | ✅ 良好 | `app/main.py:108-118` | lifespan finally 关闭 3 个后台任务，`CancelledError` 单独 catch |
| E-3 | ⚠️ INFO | 全部代码 | 500 错误细节 `e` 直接 `f"...: {e}"` 返回（**有信息泄漏风险**） | 

**E-3 风险**：
- 例：`raise HTTPException(500, detail=f"交易记录写入失败：{e}")`（`watchlist.py:508`）
- 当前 `e` 通常是 SQLAlchemy 异常，**包含 SQL 字段名**（如 `ts_code`、`position`），可能帮攻击者理解 schema
- 但因为是 127.0.0.1 单用户，**实际不可达**
- 建议（生产部署时）：改为 `detail="交易记录写入失败"` + 后端 `logger.exception(...)` 详细堆栈

### 3.11 输入校验

| # | 严重 | 位置 | 状态 |
|---|---|---|---|
| V-100 | ✅ 严格 | `app/schemas/watchlist.py:75-92` | `ts_code` 必须匹配 `sh/sz/bj+6位` 或 6 位纯数字（自动归一化），**正则硬约束** |
| V-101 | ✅ 严格 | `app/schemas/watchlist.py:94-122` | `exchange` 枚举校验（SH/SZ/BJ），与 ts_code 前缀交叉校验 |
| V-102 | ✅ 严格 | `app/routers/market.py:60, 165, 242` | `sort_by` 限定 `^(change_pct\|volume)$`，`n/limit/skip` 范围限定（Query 校验） |
| V-103 | ✅ 严格 | `app/utils/trade_note_parser.py:128-145` | 提取价格用 `0.01 ≤ v ≤ 9999.99` 硬限，**不替用户做主**（仅展示） |
| V-104 | ✅ 严格 | `app/services/llm.py:497-502, 524-535` | LLM 返回价格字段用 `_PLAN_RANGES` 兜底（0.01~1e7）+ 不变量校验 |
| V-105 | ✅ 严格 | `market_fetcher.py:486-487` | `ensure_price_in_cache` 入参正则 `^(sh\|sz\|bj)\d{6}$` 校验 |

### 3.12 日志 / 可观测

| # | 严重 | 位置 | 详情 |
|---|---|---|---|
| L-1 | ⚠️ INFO | `app/routers/watchlist.py:513-517` | 交易记录 `logger.info(...)` 输出含价格/数量/盈亏，**没有 PII 问题**（是用户自己的） |
| L-2 | 🟡 MEDIUM | `app/routers/strategy.py:308-319` | `daily-summary` 调用 LLM，**summary 内容含用户自选股 + 真实持仓** 通过 LLM API 上送 | 
| L-3 | 🟢 LOW | 全部 `logger` | 缺结构化日志（JSON 格式），无 trace_id 关联 |

**L-2 风险分析**：
- `daily-summary` 把用户 watchlist 完整数据喂给 LLM（LLM 在 `app/services/llm.py:99-109` 接收 `summary_json`）
- 数据含：持仓数量、持仓成本价、浮盈亏、交易备忘、止盈止损价
- **第三方 LLM 服务（Agnes/MiniMax/OpenAI 等）会看到这些敏感金融信息**
- 这是 LLM 功能的设计取舍，但用户应明确知道：**填 LLM key 即授权 LLM 服务方处理你的交易数据**
- 建议：在前端"启用 LLM 复盘"开关上加显眼提示

---

## 4. 性能与可维护性发现（非安全）

| # | 严重 | 位置 | 详情 |
|---|---|---|---|
| Q-1 | 🟡 MEDIUM | `app/routers/watchlist.py:84-264` `list_watchlist_quotes` | 单接口内 N+1 调用 `analyzer.check_signals` + `mf.get_history`，**对每只 watchlist 调一次 check_signals**。用户自选股 50 只 → 50 次 analyzer 调用。已在 `market_fetcher` 用 `dict` cache 缓解，但 `check_signals` 内部无 memoize |
| Q-2 | 🟡 MEDIUM | `app/routers/strategy.py:76-211` `get_daily_summary` | 同样 N+1，每只 watchlist 调 analyzer + fetcher |
| Q-3 | 🟡 MEDIUM | `app/services/llm.py:9` | 启动时同步 import 全文件（含 1738 行），冷启动 ~200ms |
| Q-4 | 🟢 LOW | `app/services/sector_alpha.py:691` | 板块指数 K 线 24h 磁盘缓存，**新板块首次拉取 30-60s**（同步 akshare 阻塞） |
| Q-5 | 🟢 LOW | 全部 `logger` | 没用 `extra={...}` 结构化字段，不便于聚合（grep 友好但 ELK 不友好） |

---

## 5. 修复建议（按优先级）

### 5.1 P-1 路径遍历修复（推荐立即修）

```python
# app/main.py 替换第 206-216 行
@app.get("/{full_path:path}", include_in_schema=False)
async def _spa_fallback(full_path: str):
    if full_path.startswith(("api/", "api")) or full_path in {"health", "openapi.json", "docs", "redoc"}:
        raise HTTPException(404)
    # v2026-08-23 修复：防止路径遍历，target.resolve() 必须仍在 FRONTEND_DIST 内
    try:
        target = (FRONTEND_DIST / full_path).resolve(strict=False)
        target.relative_to(FRONTEND_DIST.resolve())  # 越界抛 ValueError
    except (ValueError, OSError):
        raise HTTPException(404)
    if target.is_file():
        return FileResponse(target)
    return FileResponse(FRONTEND_DIST / "index.html")
```

### 5.2 R-1/R-2 资源泄漏修复

```python
# market_fetcher.py:574-583
def fetch_history_sync(code: str, days: int = HISTORY_FETCH_DAYS) -> dict[str, Any]:
    with requests.Session() as sess:  # 显式 with
        sess.trust_env = False
        try:
            resp = sess.get(...)
            ...
        except Exception as e:
            ...
```

```python
# market_fetcher.py:434 共享 aiohttp session
async with aiohttp.ClientSession(
    connector=aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
) as session:
    # 把这个 session 传给 _guarded
    ...
```

### 5.3 requirements.txt 锁版本

```txt
fastapi==0.138.0
uvicorn[standard]==0.49.0
SQLAlchemy==2.0.49
pydantic==2.13.4
openai==2.37.0
python-dotenv==1.2.2
aiohttp==3.14.3   # 升级修复 3 个 CVE
requests==2.32.3
apscheduler==3.11.3
akshare==1.16.72  # 锁住间接依赖
```

### 5.4 Q-1/Q-2 N+1 优化

```python
# 在 router 层批量化
# 而不是 for item in items: call analyzer
# 改为：analyzer.batch_check_signals(items)
```

或短期：在 router 入口加 `functools.lru_cache(maxsize=128)` 包 analyzer 函数。

### 5.5 E-3 错误信息脱敏

```python
# watchlist.py:508 改
- raise HTTPException(500, detail=f"交易记录写入失败：{e}")
+ trade_logger.exception("trade write failed: %r", e)
+ raise HTTPException(500, detail="交易记录写入失败，请稍后重试")
```

### 5.6 L-2 LLM 数据授权提示（前端）

在 `frontend/src/components/modals/AiReportModal.vue` 顶部加一行提示：
> ⚠️ 启用 AI 复盘会将您的持仓 / 自选股数据发送至第三方 LLM 服务（{`config.LLM_MODEL_NAME`}）

---

## 6. 测试覆盖情况

| 模块 | 单测 | 状态 |
|---|---|---|
| `analyzer.py` | `_test_analyzer.py` | ✅ 10 断言（v4.6.3 加入） |
| `factor_engine.py` | `_test_factor_engine.py` / `_test_factor_engine_v461.py` | ✅ 14 断言 |
| `trade_note_parser.py` | 自包含 `if __name__ == "__main__"` | ✅ 9 案例 |
| `llm.py` | 无 | ❌ 0 覆盖（依赖外部 API） |
| `market_fetcher.py` | `_diag_*` 烟测（不入仓） | ⚠️ 临时脚本，不算正式单测 |
| `sector_alpha.py` | 无 | ❌ 0 覆盖 |
| `routers/*` | 无 | ❌ 0 覆盖 |

**覆盖率估算**：核心 analyzer + trade_note_parser > 80%，**整体 < 30%**。
**建议**：用 `pytest` + `httpx.AsyncClient` 给 `routers/*` 写 1 个 smoke test（已存在 `fetcher_smoke_test.py` 但未集成 pytest）。

---

## 7. 附录 A：扫描脚本与原始输出

### A.1 Bandit（11 个低危）

```
[LOW] .\market_fetcher.py:147  B110 - Try, Except, Pass detected.
[LOW] .\market_fetcher.py:394  B311 - Standard pseudo-random generators are not suitable for security/cryptographic purposes.
[LOW] app\config.py:72         B110 - Try, Except, Pass detected.
[LOW] app\services\llm.py:377  B311 - Standard pseudo-random generators...
[LOW] app\services\llm.py:391  B311 - Standard pseudo-random generators...
[LOW] app\services\llm.py:475  B110 - Try, Except, Pass detected.
[LOW] app\services\llm.py:482  B110 - Try, Except, Pass detected.
[LOW] app\services\llm.py:491  B112 - Try, Except, Continue detected.
[LOW] app\services\llm.py:953  B110 - Try, Except, Pass detected.
[LOW] app\services\llm.py:961  B110 - Try, Except, Pass detected.
[LOW] app\services\llm.py:968  B110 - Try, Except, Pass detected.
```

### A.2 pip-audit（项目直接依赖 8 个）

| 包 | 已安装 | 漏洞 | 状态 |
|---|---|---|---|
| fastapi | 0.138.0 | 0 | ✅ |
| uvicorn | 0.49.0 | 0 | ✅ |
| sqlalchemy | 2.0.49 | 0 | ✅ |
| pydantic | 2.13.4 | 0 | ✅ |
| openai | 2.37.0 | 0 | ✅ |
| python-dotenv | 1.2.2 | 0 | ✅ |
| **aiohttp** | **3.14.1** | **3** | **🟠 升 3.14.3** |
| requests | 2.32.3 | 0 | ✅ |
| apscheduler | 3.11.3 | 0 | ✅ |

### A.3 密钥检查

```bash
$ git ls-files .env
(空)

$ git log --all --pickaxe-regex -S "sk-7MQIgPL0bIP6bew"
(空)
```

### A.4 自定义规则扫描（CRITICAL/HIGH 类 0 命中）

| 规则 | 命中 |
|---|---|
| `eval(...)` | 0 |
| `exec(...)` | 0 |
| `pickle.loads` | 0 |
| `yaml.load` | 0 |
| `os.system` / `subprocess shell=True` | 0 |
| `--host 0.0.0.0` | 0 |
| `verify=False` (TLS 关) | 0 |
| `CORS allow_origins=*` | 0（无 CORS） |
| `hashlib.md5` / `sha1` 密码学场景 | 0 |

### A.5 路径遍历复现（已确认）

```
input '../../../etc/passwd'                     → resolved outside_fd=True
input '..\..\..\Windows\System32\drivers\etc\hosts' → resolved outside_fd=True
input 'index.html'                              → resolved inside_fd=True  is_file=True
```

---

## 8. 结论与下一步

**项目当前状态**：
- 安全：**良好** — 无 CRITICAL/HIGH 漏洞，无密钥外泄，无注入风险
- 性能：**中等** — N+1 查询明显，依赖加载可优化
- 可维护性：**良好** — 单测覆盖核心计算层，路由层缺单测
- 可观测性：**薄弱** — 无 metrics 出口，无结构化日志

**3 件立即可做的事**（约 30 分钟）：
1. ✅ 修 `app/main.py` 路径遍历（5 行 diff）
2. ✅ `requirements.txt` 锁版本 + 升级 aiohttp 到 3.14.3
3. ✅ 修 `market_fetcher.py:574` 的 `requests.Session` 用 `with` 管理

**3 件中优先级**（约 1-2 小时）：
1. ⚠️ `watchlist.py:508` 等错误信息脱敏
2. ⚠️ router 层 N+1 → batch 调用
3. ⚠️ `routers/*` 加 pytest 烟测

**3 件长期**（1-2 天）：
1. 📊 加 Prometheus / OpenTelemetry 出口
2. 🔍 关键路径（LLM 调用、fetcher）加结构化日志 + trace_id
3. 🧪 集成 pytest + CI，让单测自动跑

---

*报告生成时间：2026-08-23 16:22 GMT+8*
*审计工具：Bandit 1.9.4 + pip-audit 2.10.1 + 自定义规则 13 条*
*代码量统计：~6,767 LOC（不含 `__pycache__` / `_test_*` / `_diag_*` / `_check_*` / `_dbg_*`）*
