/**
 * 后端 API 封装。
 *
 * 前端通过 /api/* 走 vite 代理到 FastAPI（uvicorn 启动在 :8000）。
 * 例如 ``/api/market/sentiment`` 实际打到 ``http://localhost:8000/market/sentiment``。
 */
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  // v4.3 第二次调整: 120s -> 180s, 实测 M2.7 单次响应 113s, 120s 不够
  timeout: 180000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 响应拦截器：v2.4.3 之前这里 return resp.data 把 data 解包了，导致 Dashboard 里所有
// `const r = await xxx(); r.data` 都拿到 undefined（r 已经是 dict）。
// 修法：保持 axios 默认行为（resp 是 AxiosResponse），让所有 r.data 继续生效。
// 错误处理：timeout / network error 没有 response，要 fallback 到 err.message
api.interceptors.response.use(
  (resp) => resp,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.message || '未知错误'
    return Promise.reject(new Error(`[${status ?? 'NETWORK'}] ${detail}`))
  },
)

/**
 * 全市场情绪。
 * GET /market/sentiment
 */
export function getSentiment() {
  return api.get('/market/sentiment')
}

/**
 * 自选股 + 实时行情联调。
 * GET /watchlist/quotes
 */
export function getWatchlist() {
  return api.get('/watchlist/quotes')
}

/**
 * 触发的告警信号扫描。
 * GET /watchlist/signals?only_triggered=true
 */
export function getSignals(onlyTriggered = true) {
  return api.get('/watchlist/signals', { params: { only_triggered: onlyTriggered } })
}

/**
 * 新增自选股（含可选持仓字段 v1.1）。
 * POST /watchlist
 * @param {{ ts_code: string, name?: string, exchange?: string,
 *          cost_price?: number, position?: number, trade_note?: string }} payload
 */
export function addToWatchlist(payload) {
  return api.post('/watchlist', payload)
}

/**
 * 更新自选股（部分字段）。inline 编辑用。
 * PATCH /watchlist/{id}
 * @param {number} id
 * @param {{ name?: string, cost_price?: number|null,
 *          position?: number|null, trade_note?: string|null,
 *          is_active?: boolean }} payload
 *   传 null 表示清空这个字段
 */
export function updateWatchlist(id, payload) {
  return api.patch(`/watchlist/${id}`, payload)
}

/**
 * 删除自选股。
 * DELETE /watchlist/{id}
 */
export function removeFromWatchlist(id) {
  return api.delete(`/watchlist/${id}`)
}

/**
 * 手动触发自选股历史 K 线刷新。
 * POST /market/history/refresh
 *
 * 加完自选股后立即调一次，5 秒后量比就能用了。
 */
export function refreshHistory() {
  return api.post('/market/history/refresh')
}

/**
 * 手动触发全市场行情抓取。
 * POST /market/refresh
 */
export function refreshMarket() {
  return api.post('/market/refresh')
}

/**
 * 单只股票的历史 K 线（lightweight-charts 友好格式）。
 * GET /market/{ts_code}/history
 *
 * 返回 ::
 *   {
 *     code, avg_volume_5d, avg_amount_5d,
 *     klines:  [{time, open, high, low, close}],
 *     volumes: [{time, value, color}]
 *   }
 */
export function getStockHistory(tsCode) {
  return api.get(`/market/${tsCode}/history`)
}

/**
 * 概念板块资金流向（力导向气泡图用）。
 * GET /market/fund-flow?top=&bottom=&limit=
 *
 * 返回 ::
 *   {
 *     count, refreshed_at, total_inflow, total_outflow, unit,
 *     items: [{name, net_amount, change_pct, leading_stock, leading_change_pct, ...}, ...]
 *   }
 */
export function getFundFlow(params = {}) {
  return api.get('/market/fund-flow', { params })
}

/**
 * 全市场排行榜（v2.2 异动雷达用）
 * GET /market/top?sort_by=change_pct|volume&limit=20
 * sort_by: 'change_pct' 涨跌幅 / 'volume' 成交量
 * 返回 Top N [{code, name, price, change_pct, volume, ...}]
 */
export function getTopMovers(params = {}) {
  return api.get('/market/top', { params })
}

/**
 * 今日盘后复盘战报（v2.3）
 * GET /strategy/daily-summary
 * 返回 { generated_at, sentiment, watchlist_battle, top_movers }
 */
export function getDailySummary() {
  return api.get('/strategy/daily-summary')
}

/**
 * AI 深度复盘（v2.4：LLM 生成 Markdown）
 * POST /strategy/ai-report
 * 返回 { generated_at, model, report_markdown, summary }
 * 注意：未配 LLM_API_KEY 时后端返 503，前端需要 catch 降级
 */
export function getAiReport() {
  return api.post('/strategy/ai-report')
}

/**
 * v3.0 真实交割：买入/卖出（自动算加权平均 + 写 trade_log）
 * POST /watchlist/{id}/trade
 * @param {number} id - watchlist 主键
 * @param {{ price: number, volume: number }} payload - volume 正=买入，负=卖出
 * @returns {Promise<AxiosResponse<{
 *   trade_id, ts_code, action, trade_price, trade_volume, realized_pnl,
 *   new_position, new_cost_price, new_last_grid_price
 * }>>}
 */
export function recordTrade(id, payload) {
  return api.post(`/watchlist/${id}/trade`, payload)
}

/**
 * v3.1 历史交割单（资金账本数据源）
 * GET /trades/history?ts_code=&limit=
 * @param {{ ts_code?: string, limit?: number }} params
 * @returns {Promise<AxiosResponse<{
 *   total_count, total_realized_pnl,
 *   trades: [{ id, ts_code, name, action, price, volume, realized_pnl, created_at }]
 * }>>}
 */
export function getTradeHistory(params = {}) {
  return api.get('/trades/history', { params })
}

/**
 * v4.0 AI 智能规划（领航员看 K 线，输出建仓计划）
 * POST /watchlist/{id}/ai-plan
 *
 * **关键设计**：本接口**不直接写库**。返回的 plan 由前端弹 Modal 让用户确认后，
 * 走 updateWatchlist(id, {entry_price_min, entry_price_max, target_win, target_loss, trade_note}) 写库。
 *
 * @param {number} id - watchlist 主键
 * @returns {Promise<AxiosResponse<{
 *   stock_id, ts_code, name, current_price,
 *   existing: { entry_price_min, entry_price_max, target_win, target_loss, trade_note },
 *   plan: {
 *     entry_price_min, entry_price_max, target_win, target_loss,
 *     trade_note, rationale, tags: string[]
 *   },
 *   explain: { features: {...}, ohlcv_10d: [{date, open, close, high, low, volume_lots}, ...] },
 *   model: string
 * }>>}
 */
export function aiPlan(id) {
  return api.post(`/watchlist/${id}/ai-plan`)
}

/**
 * v4.1 Alpha 共振挖掘（技术+消息面融合 → 3 个短线方向）
 * GET /strategy/discover
 *
 * @returns {Promise<AxiosResponse<{
 *   discoveries: [{
 *     sector: string,
 *     logic: string,
 *     stocks: [{code, name}, ...],
 *     level: '高' | '中' | '低',
 *   }, ...],
 *   model: string,
 *   generated_at: string,
 *   meta: {
 *     gainers_count, volume_count, sectors_count, news_count,
 *     news_source, news_fetched_at, news_error,
 *   },
 * }>>}
 */
export function getDiscover() {
  return api.get('/strategy/discover')
}

export default api
