/**
 * 后端 API 封装。
 *
 * 前端通过 /api/* 走 vite 代理到 FastAPI（uvicorn 启动在 :8000）。
 * 例如 ``/api/market/sentiment`` 实际打到 ``http://localhost:8000/market/sentiment``。
 */
import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 响应拦截器：把 axios 的 data 字段直接解出来，遇到非 2xx 抛出统一错误
api.interceptors.response.use(
  (resp) => resp.data,
  (err) => {
    const status = err.response?.status
    const detail = err.response?.data?.detail || err.message
    return Promise.reject(new Error(`[${status}] ${detail}`))
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

export default api
