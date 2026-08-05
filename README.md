# A-Stock Sentiment Monitor v1.2

A local-first A-share (and ETF) real-time market sentiment dashboard with a
glass-morphism dark UI. Single binary-style deployment: double-click `start.bat`
and the browser opens automatically.

## Highlights

- **5,535 A-shares** + **ETFs** tracked in real-time (5s refresh)
- **300 concept-sector fund-flow bubble chart** (d3-force layout, 60s refresh)
- Glass-morphism dark dashboard with gradient text, tabular-nums, hover states
- TradingView **lightweight-charts** K-line on click
- Volume-ratio breakout / shrinking-pullback / **止盈 / 止损** signal engine
- **Private trading assistant** (v1.2): cost / position / take-profit / stop-loss / trade-note
  per row, inline edit, desktop notifications
- One-click Windows start (auto-builds frontend, opens browser)

## Quick start (Windows)

```powershell
# Double-click or in cmd:
start.bat
```

That will:

1. Activate `venv/` if present (else system Python)
2. Install backend deps on first run (writes `.deps_installed` marker)
3. Build frontend if `frontend/dist/index.html` is missing (`npm install + npm run build`)
4. Launch a background PowerShell watcher that opens the browser once port 8000 is ready
5. Run `uvicorn` in the foreground (Ctrl+C to stop)

The browser should open to <http://127.0.0.1:8000> within ~5s.

## Project layout

```
stock_monitor/
├── start.bat                  # one-click start (Windows)
├── requirements.txt           # backend Python deps
├── README.md                  # this file
├── analyzer.py                # sentiment + signal engine
├── market_fetcher.py          # real-time data fetcher (sina + tencent)
├── smoke_test.py              # watchlist CRUD integration test
├── fetcher_smoke_test.py      # fetcher + parser unit tests
├── analyzer_test.py           # analyzer unit tests
├── app/                       # FastAPI backend
│   ├── main.py                # lifespan + SPA static mount
│   ├── config.py
│   ├── database.py            # SQLAlchemy 2.0
│   ├── models/                # ORM (watchlist, alert_rule)
│   ├── schemas/               # Pydantic v2
│   ├── crud/                  # watchlist CRUD
│   └── routers/               # /watchlist + /market endpoints
├── frontend/                  # Vue 3 + Vite + Tailwind v4
│   ├── package.json
│   ├── vite.config.js         # tailwindcss + (dev-only) proxy
│   ├── dist/                  # build output (served by FastAPI)
│   └── src/
│       ├── api/index.js       # axios client
│       ├── style.css          # glass + gradient + tabular-nums
│       └── components/
│           ├── Dashboard.vue        # main dashboard
│           ├── KLineChart.vue       # lightweight-charts wrapper
│           └── FundFlowBubble.vue   # d3-force sector fund-flow bubble chart
└── data/
    └── stock_monitor.db       # SQLite (auto-created on first run)
```

## API endpoints

All endpoints are under `/api`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | health check |
| GET | `/api/info` | service metadata |
| GET | `/api/market/sentiment` | full-market sentiment (up/down/limit-up/limit-down + 0-100 score) |
| GET | `/api/market/meta` | fetcher cache meta + history meta |
| GET | `/api/market?skip=&limit=` | full-market list (paginated) |
| GET | `/api/market/{code}` | single-stock snapshot (auto-fetches on miss, supports ETF) |
| GET | `/api/market/{code}/history` | OHLC K-line (lightweight-charts format, auto-fetches on miss) |
| GET | `/api/market/top/gainers?n=20` | top gainers |
| GET | `/api/market/top/losers?n=20` | top losers |
| GET | `/api/market/fund-flow?top=&bottom=&limit=` | concept-sector fund flow (bubble chart) |
| POST | `/api/market/refresh` | force full-market fetch |
| POST | `/api/market/history/refresh` | force history refresh for watchlist |
| GET | `/api/watchlist` | CRUD on watchlist |
| POST | `/api/watchlist` | add ticker (v1.2: optional `cost_price` / `position` / `target_win` / `target_loss` / `trade_note`) |
| GET | `/api/watchlist/quotes` | watchlist joined with live quotes (v1.2: `floating_pnl` + `return_rate` + `target_win` / `target_loss` derived) |
| GET | `/api/watchlist/signals?only_triggered=true` | signal scan (v1.2: `is_take_profit` / `is_stop_loss` / `trade_message`) |
| GET | `/api/watchlist/{id}` | by id |
| GET | `/api/watchlist/by-code/{ts_code}` | by code |
| PATCH | `/api/watchlist/{id}` | partial update (v1.2: includes 5 portfolio fields; send `null` to clear) |
| DELETE | `/api/watchlist/{id}` | delete (cascades alert_rules) |

Interactive Swagger: <http://127.0.0.1:8000/docs>

## How the data flows

```
akshare (daily refresh @ 09:15)
    |
    v
all_stocks_cache (5535 codes)  -- + Sina realtime quotes every 5s
    |
    +--- GET /api/market/sentiment (analyzer)
    +--- GET /api/watchlist/quotes
    +--- GET /api/watchlist/signals

akshare.stock_fund_flow_concept('即时')   -- every 60s
    |
    v
fund_flow_cache (300 concept sectors)
    |
    +--- GET /api/market/fund-flow  -> d3-force bubble chart

Tencent K-line API
    |
    v
history_cache (per watchlist code, 5-day avg volume)
    |
    +--- analyzer.check_signals (volume ratio + signal flags)
    +--- GET /api/market/{code}/history
```

## Sector fund-flow bubble chart

Located between the sentiment panel and the watchlist table. **300 concept
sectors** rendered with `d3-force`:

- **Radius** scales with `sqrt(|net_amount|)` so area is proportional to the
  fund flow magnitude (`RADIUS_MIN=12px`, `RADIUS_MAX=70px`)
- **Color**: red `rgba(239, 68, 68, 0.78)` for net inflow, green
  `rgba(34, 197, 94, 0.78)` for net outflow
- **Cluster force** (`forceX`): inflow bubbles settle around the left 30%
  of the canvas, outflow around the right 30%
- **Collide force** (`forceCollide`): radius = self radius + 2px (per spec)
- **Center + many-body forces** keep the layout balanced
- **Text inside bubble**: sector name (top) + signed amount + `亿` (bottom);
  hidden when radius < 22px (too small to read)
- **Hover**: SVG `<title>` shows full breakdown (净额 / 涨跌幅 / 流入流出 /
  公司家数 / 领涨股)
- **Drag**: temporarily pin a bubble to inspect it; release returns to cluster

## How signals work

Two signals per watchlist stock, recomputed every 5s:

- **放量突破 (volume breakout)**: `vol_ratio > 2.5` AND `change_pct > 3%` AND `price > open`
- **缩量企稳 (shrinking pullback)**: `-1% <= change_pct <= 1%` AND `0 < vol_ratio < 0.8`

Where `vol_ratio = current_volume / (5day_avg_volume / 240 * minutes_since_open)`.

When a new signal appears (not in the previous tick), the browser fires a
desktop notification (Notification API, requires user opt-in).

## Watchlist portfolio (v1.1)

Beyond price watching, each watchlist row can carry portfolio state:

| Field | Type | Meaning |
|---|---|---|
| `cost_price` | float | 买入成本价（元/股），未持仓留空 |
| `position` | int | 持仓数量（股），未持仓留空 |
| `trade_note` | str | 交易逻辑备忘（≤ 500 字）|

When the watchlist row is loaded, `/api/watchlist/quotes` derives:

- `floating_pnl` = `(price - cost_price) * position`
- `return_rate`  = `(price - cost_price) / cost_price * 100`  (%)

Either input missing → both derived fields are `null`.

Frontend behavior:
- Adding a new row: optional `成本价` / `持仓股` / `交易逻辑` inputs in the form
- Display: 2 new columns (持仓盈亏, 收益率), color follows A-share convention (涨红跌绿)
- Inline edit: click `成本价` or `持仓股` cell → input → blur or Enter to save → PATCH `/api/watchlist/{id}`
- Hover the 📝 icon → glassmorphism tooltip shows the trade note

Database migration: `app/database.py:migrate_db()` runs `ALTER TABLE ... ADD COLUMN` on every
startup with `OperationalError` swallowed on already-existing columns — fully idempotent, no
Alembic needed for this scale.

## Watchlist portfolio v1.2 — 止盈止损 + 交易计划

v1.1 只算"赚多少"。v1.2 让自选股从『盯盘表』变成『私人交易助手』：
每只股票可以挂止盈价 / 止损价，价格触发时立即桌面通知。

### 字段扩展

| Field | Type | Meaning |
|---|---|---|
| `target_win` | float \| null | 止盈目标价（≥ 0；典型 = 成本 × (1 + 期望收益%)）|
| `target_loss` | float \| null | 止损 / 防守价（≥ 0；典型 = 成本 × (1 - 风险承受%)）|
| `trade_note` | str | 交易逻辑 + 计划备忘（v1.1 字段继续保留）|

约束：两个字段要么都填，要么都空；都填时 `target_win > target_loss`。

### 新信号

`analyzer.py:check_signals` 在原有 `is_volume_breakout` / `is_shrinking_pullback` 之外扩展两个：

| Signal | Trigger | trade_message 样例 |
|---|---|---|
| `is_take_profit` | `current.close >= target_win` | "到达止盈线 15.5，注意减仓" |
| `is_stop_loss`   | `current.close <= target_loss` | "触及止损线 13.2，建议减仓 / 离场" |

触发时一并写入 `/api/watchlist/signals?only_triggered=true` 响应。

### 前端

- **添加表单**：默认折叠的「高级选项」区，展开后填入 `止盈` / `止损` / `交易逻辑`。
  三个输入框分别配色：止盈 emerald、止损 rose、备注 amber。
- **表格新增列**：`止盈` / `止损`（行内编辑单元格，emerald-300 / rose-300 提示色）。
- **交易计划列**（合并 v1.1 备忘）：📝 图标 + 悬停 tooltip 显示原文；止盈/止损价以小字
  显示在右侧（`止盈 15.50` / `止损 13.20`）。
- **信号 badge**：`🎯 止盈`（`badge-win`，emerald 配色，1.5s pulse）和 `🛡️ 止损`
  （`badge-loss`，rose 配色，1s pulse）—— 止损脉冲更快，更扎眼。
- **桌面通知**：
  - 止盈 → "🎯 止盈信号"，通知体包含止盈价 + 现价
  - 止损 → "🛡️ 止损信号"，**`requireInteraction: true`** 强制手动关闭，不自动消失
- **行内编辑扩展到 4 字段**：成本价、持仓股、止盈、止损。点击数字 → input → blur/Enter 保存。

### 数据迁移

`app/database.py:migrate_db()` 自动添加两列；旧的 v1.1 数据库原地升级，
数据不丢。如果想从零开始：删 `data/stock_monitor.db` → 启动时 `create_all` 重建。

## ETF support

Adding any code matching `^(sh|sz|bj)\d{6}$` works — stocks and ETFs share the
same data pipeline. The fetcher auto-pulls any watchlist code that's missing
from the full-market cache, so 510300 (HuShen 300 ETF) or 159915 (ChiNext ETF)
"just work".

## Development (without the integrated build)

For hot-reload during UI work:

```powershell
# Terminal A: backend
uvicorn app.main:app --reload --port 8000

# Terminal B: frontend dev server (with proxy)
cd frontend
npm run dev
# -> http://127.0.0.1:5173 (vite proxies /api to :8000)
```

## Test suite

```powershell
python smoke_test.py            # watchlist CRUD + cache join
python fetcher_smoke_test.py   # parser + batching
python analyzer_test.py        # sentiment + signal + trading minutes
```

All three pass.

## Known limitations

- 非交易时段 vol_ratio 按 0 处理（避免误判，盘后看信号全空）
- 腾讯 K 线单次拉 1 只，并发 5（watchlist 几十只 OK，几百只建议降到 3）
- 新浪 hq 接口限流时 fetcher 走指数退避重试（最多 3 次），单批失败不会阻塞其他批
- akshare 进度条默认屏蔽（logging 配 WARNING）
- 东方财富域名被 GFW 屏蔽，已用腾讯 K 线兜底

## Tech stack

**Backend**: Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, APScheduler, aiohttp, akshare, requests

**Frontend**: Vue 3 (script setup), Vite 8, Tailwind CSS v4, Axios, lightweight-charts v5

**Data sources**: 新浪财经（实时行情）、腾讯财经（K 线）、akshare（全市场代码）

## License

Personal project. Use at your own risk — this is not investment advice.
