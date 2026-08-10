<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  addToWatchlist,
  getAiReport,
  getDailySummary,
  getFundFlow,
  getSentiment,
  getSignals,
  getTopMovers,
  getWatchlist,
  refreshHistory,
  removeFromWatchlist,
  updateWatchlist,
} from '../api'
import KLineChart from './KLineChart.vue'
import FundFlowBubble from './FundFlowBubble.vue'

// ====================== 状态 ======================
const REFRESH_INTERVAL_MS = 5000
const FUND_FLOW_INTERVAL_MS = 60_000  // 板块资金流向 60s 一次（akshare 接口刷新就这个速度）

const sentiment = ref(null)
const watchlist = ref([])
const fundFlow = ref({ items: [], refreshed_at: null, count: 0 })
const fundFlowLoading = ref(false)
const error = ref('')
const loading = ref(false)
const lastUpdated = ref(null)
const now = ref(Date.now())

// 表单（v1.2: 加 target_win / target_loss 止盈止损）
const newCode = ref('')
const newName = ref('')
const newCost = ref('')         // 字符串，提交时 parseFloat
const newPosition = ref('')     // 字符串，提交时 parseInt
const newTargetWin = ref('')    // 止盈价（可选）
const newTargetLoss = ref('')   // 止损价（可选）
const newNote = ref('')
const adding = ref(false)
const addError = ref('')
const showAdvanced = ref(false)  // 是否展开"高级选项"（止盈止损 + 备忘）

// 排序
const sortKey = ref('')
const sortDir = ref('desc')

// K 线模态框
const chartCode = ref(null)
const chartName = ref('')
// v2.1: 持仓 / 止盈止损参考线（K 线上画水平线用）
const chartCost = ref(null)
const chartTargetWin = ref(null)
const chartTargetLoss = ref(null)
// v2.2: 强制 KLineChart 重建的 key（每次 openChart 自增）
// 解决「关模态框再开同一只股票 → Vue diff 跳过 onMounted → 旧 chart 残留」bug
const chartKey = ref(0)

// v2.2: 实时异动雷达（默认涨跌幅榜）
const radar = ref([])         // Top 20 涨幅榜
const radarLoading = ref(false)
const RADAR_INTERVAL_MS = 5_000  // 跟主表同步刷新
const RADAR_LIMIT = 20

// v2.3: 今日复盘战报
const summary = ref(null)        // daily-summary 接口返回
const showSummary = ref(false)   // 模态框可见
const summaryLoading = ref(false)
// v2.4: AI 深度复盘
const aiReport = ref(null)       // { generated_at, model, report_markdown, summary }
const aiLoading = ref(false)
const aiError = ref('')          // 后端 503/502 时的提示信息
// 每天 15:00 后第一次轮询自动弹通知；用日期 + 标记避免重复触发
const summaryNotifiedDate = ref('')  // YYYY-MM-DD，已经触发过的日期
const SUMMARY_AUTO_TRIGGER_HOUR = 15  // 15:00 收盘
const SUMMARY_CHECK_INTERVAL_MS = 60_000  // 1 分钟检查一次

// ====================== v2.4: 轻量级 Markdown 渲染 ======================
// 为什么要自己写而不是用 marked + DOMPurify？
//   1. LLM 输出受控（system prompt 指定 Markdown + 中文），白名单标签足以
//   2. 不引第三方依赖，build size 友好
//   3. 样式自己说了算，跟毛玻璃风格统一
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
function renderInline(s) {
  // 顺序：code > bold > italic > link（先吃最里层）
  let out = escapeHtml(s)
  // 行内 code
  out = out.replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-slate-950/60 border border-slate-700/50 text-amber-300 text-[0.85em] font-mono">$1</code>')
  // bold
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-semibold text-slate-100">$1</strong>')
  // italic（单 * 或 _）
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em class="text-slate-300 italic">$2</em>')
  return out
}
function renderMarkdown(md) {
  if (!md) return ''
  const lines = String(md).split('\n')
  const out = []
  let inOl = false
  let inUl = false
  let inCode = false
  let codeBuf = []
  const closeLists = () => {
    if (inOl) { out.push('</ol>'); inOl = false }
    if (inUl) { out.push('</ul>'); inUl = false }
  }
  for (const raw of lines) {
    const line = raw.replace(/\r$/, '')
    // 代码块 ``` ... ```
    if (/^```/.test(line)) {
      if (inCode) {
        out.push(`<pre class="my-3 p-3 rounded bg-slate-950/70 border border-slate-700/40 overflow-x-auto text-[12px] font-mono text-slate-200"><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
        codeBuf = []
        inCode = false
      } else {
        closeLists()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      continue
    }
    // 标题
    let m
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      closeLists()
      const level = m[1].length
      const sizes = ['text-2xl', 'text-xl', 'text-lg', 'text-base']
      out.push(`<h${level} class="${sizes[level - 1]} font-semibold text-slate-100 mt-4 mb-2">${renderInline(m[2])}</h${level}>`)
      continue
    }
    // 引用 >
    if ((m = /^>\s?(.*)$/.exec(line))) {
      closeLists()
      out.push(`<blockquote class="border-l-2 border-amber-500/60 pl-3 my-2 text-slate-300 italic">${renderInline(m[1])}</blockquote>`)
      continue
    }
    // 有序列表
    if ((m = /^\d+\.\s+(.*)$/.exec(line))) {
      if (!inOl) { closeLists(); out.push('<ol class="list-decimal list-inside my-2 space-y-1 text-slate-200">'); inOl = true }
      out.push(`<li>${renderInline(m[1])}</li>`)
      continue
    }
    // 无序列表
    if ((m = /^[-*]\s+(.*)$/.exec(line))) {
      if (!inUl) { closeLists(); out.push('<ul class="list-disc list-inside my-2 space-y-1 text-slate-200">'); inUl = true }
      out.push(`<li>${renderInline(m[1])}</li>`)
      continue
    }
    // 空行 → 关闭列表
    if (line.trim() === '') {
      closeLists()
      continue
    }
    // 普通段落
    closeLists()
    out.push(`<p class="my-2 leading-relaxed text-slate-200">${renderInline(line)}</p>`)
  }
  closeLists()
  if (inCode) {
    out.push(`<pre class="my-3 p-3 rounded bg-slate-950/70 border border-slate-700/40 overflow-x-auto text-[12px] font-mono text-slate-200"><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
  }
  return out.join('')
}
const aiReportHtml = computed(() => renderMarkdown(aiReport.value?.report_markdown || ''))

// 行内编辑（v1.1）: 哪一行 + 哪个字段正在被编辑
const editingCell = ref(null)  // { id, field } | null
const editValues = ref({})     // { [`${id}-${field}`]: string }
const editError = ref('')      // 行内编辑错误（如 -1 价格）
// trade_note tooltip hover
const noteTip = ref(null)      // { x, y, text } | null

let prevSignalCodes = new Set()

// ====================== 工具 ======================
function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '-'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}
function fmtVol(v) {
  if (v == null) return '-'
  return Number(v).toLocaleString('zh-CN')
}
function fmtPrice(v) {
  if (v == null) return '-'
  return Number(v).toFixed(2)
}
// 浮动盈亏：带正负号 + 千分位
function fmtPnl(v) {
  if (v == null || Number.isNaN(v)) return '-'
  const sign = v > 0 ? '+' : ''
  return sign + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtTimeAgo(ts) {
  if (!ts) return '-'
  const s = Math.floor((now.value - ts) / 1000)
  if (s < 5) return '刚刚'
  if (s < 60) return `${s} 秒前`
  return `${Math.floor(s / 60)} 分钟前`
}

// ====================== 颜色辅助（A 股约定：涨红跌绿）======================
function pnlColorClass(v) {
  if (v == null) return 'text-slate-500'
  if (v > 0) return 'text-rose-400'
  if (v < 0) return 'text-emerald-400'
  return 'text-slate-400'
}
function pnlGlowClass(v) {
  if (v == null) return ''
  if (v > 0) return 'glow-rose'
  if (v < 0) return 'glow-emerald'
  return ''
}

// ====================== 自定义指令：v-focus ======================
// 行内编辑开始时自动 focus + 选中（让用户直接覆盖）
const vFocus = {
  mounted(el) {
    el.focus()
    if (typeof el.select === 'function') {
      try { el.select() } catch (_) {}
    }
  },
}

function sentimentGradientClass(score) {
  if (score == null) return 'text-gradient-neutral'
  if (score > 60) return 'text-gradient-bull'
  if (score < 40) return 'text-gradient-bear'
  return 'text-gradient-neutral'
}
function sentimentGlowClass(score) {
  if (score == null) return ''
  if (score > 60) return 'glow-rose'
  if (score < 40) return 'glow-emerald'
  return ''
}
function sentimentLabel(score) {
  if (score == null) return ''
  if (score > 70) return '强势'
  if (score > 55) return '偏强'
  if (score > 45) return '震荡'
  if (score > 30) return '偏弱'
  return '弱势'
}

// ====================== 数据合并 ======================
const signals = ref([])
const signalMap = computed(() => {
  const m = new Map()
  for (const s of signals.value) m.set(s.ts_code, s)
  return m
})
const watchlistWithSignals = computed(() =>
  watchlist.value.map((w) => {
    const sig = signalMap.value.get(w.ts_code)
    return {
      ...w,
      signal: sig || null,
      volume_ratio: sig?.volume_ratio ?? null,
    }
  }),
)

// ====================== 排序 ======================
const sortedWatchlist = computed(() => {
  if (!sortKey.value) return watchlistWithSignals.value
  const key = sortKey.value
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...watchlistWithSignals.value].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    return (Number(av) - Number(bv)) * dir
  })
})

function toggleSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}
function sortIndicator(key) {
  if (sortKey.value !== key) return '↕'
  return sortDir.value === 'asc' ? '↑' : '↓'
}

// ====================== 拉数据 + 通知 ======================
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const [s, w, sg] = await Promise.all([
      getSentiment(),
      getWatchlist(),
      getSignals(true),
    ])
    sentiment.value = s
    watchlist.value = w

    const newCodes = new Set(sg.map((x) => x.ts_code))
    const fresh = sg.filter((x) => !prevSignalCodes.has(x.ts_code))
    if (prevSignalCodes.size > 0 && fresh.length > 0) {
      notifyNewSignals(fresh)
    }
    prevSignalCodes = newCodes
    signals.value = sg
    lastUpdated.value = Date.now()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// ====================== 工具 ======================
// 与后端 _normalize_code 同源：A 股 6 位纯数字 → 自动补 sh/sz/bj 前缀
// 规则：5/6 开头 → sh（上交所含科创板/ETF），0/3 开头 → sz（深交所含创业板/ETF），
//      4/8/9 开头 → bj（北交所）
function normalizeTsCode(raw) {
  const s = String(raw || '').trim().toLowerCase()
  if (!s) return ''
  if (s.startsWith('sh') || s.startsWith('sz') || s.startsWith('bj')) {
    return s
  }
  if (!/^\d{6}$/.test(s)) return s
  if (s.startsWith('5') || s.startsWith('6')) return 'sh' + s
  if (s.startsWith('0') || s.startsWith('3')) return 'sz' + s
  if (s.startsWith('4') || s.startsWith('8') || s.startsWith('9')) return 'bj' + s
  return 'sh' + s  // 兜底
}
function prefixToExchange(prefixed) {
  if (prefixed.startsWith('sh')) return 'SH'
  if (prefixed.startsWith('sz')) return 'SZ'
  if (prefixed.startsWith('bj')) return 'BJ'
  return null
}

async function onAdd() {
  const raw = newCode.value.trim()
  if (!raw) { addError.value = '请输入股票代码'; return }
  // 先归一化：6 位纯数字自动补前缀
  const code = normalizeTsCode(raw)
  if (!/^(sh|sz|bj)\d{6}$/.test(code)) {
    addError.value = '代码格式错误（示例：sh600000 / 600000 / sh510300）'
    return
  }
  // 校验可选持仓字段
  let cost = null, position = null
  const costRaw = newCost.value.trim()
  if (costRaw !== '') {
    cost = parseFloat(costRaw)
    if (Number.isNaN(cost) || cost < 0) { addError.value = '成本价必须是 ≥ 0 的数字'; return }
  }
  const posRaw = newPosition.value.trim()
  if (posRaw !== '') {
    position = parseInt(posRaw, 10)
    if (Number.isNaN(position) || position < 0 || String(position) !== posRaw) {
      addError.value = '持仓股数必须是 ≥ 0 的整数'
      return
    }
  }
  // 注意：用户可能只填了成本没填股数（或反之）—— 不允许半残，让后端不要算
  // 简化策略：两个都必填，或都为空。要么两个都填，要么两个都空。
  if ((cost != null && position == null) || (cost == null && position != null)) {
    addError.value = '成本价和持仓股数要一起填（或都留空）'
    return
  }
  // v1.2: 解析止盈 / 止损
  let targetWin = null, targetLoss = null
  const twRaw = newTargetWin.value.trim()
  if (twRaw !== '') {
    targetWin = parseFloat(twRaw)
    if (Number.isNaN(targetWin) || targetWin <= 0) { addError.value = '止盈价必须是 > 0 的数字'; return }
  }
  const tlRaw = newTargetLoss.value.trim()
  if (tlRaw !== '') {
    targetLoss = parseFloat(tlRaw)
    if (Number.isNaN(targetLoss) || targetLoss <= 0) { addError.value = '止损价必须是 > 0 的数字'; return }
  }
  // 如果两个都填，止盈必须 > 止损
  if (targetWin != null && targetLoss != null && targetWin <= targetLoss) {
    addError.value = '止盈价必须高于止损价'
    return
  }
  const note = newNote.value.trim() || null

  // exchange 字段：从归一化后的 ts_code 前缀推断
  // 兜底逻辑：用户可以不传 exchange，后端允许 null；但我们顺手补上，前端体验更稳
  const exchange = prefixToExchange(code)

  adding.value = true
  addError.value = ''
  try {
    await addToWatchlist({
      ts_code: code,
      name: newName.value.trim() || undefined,
      exchange,
      cost_price: cost,
      position: position,
      target_win: targetWin,
      target_loss: targetLoss,
      trade_note: note,
    })
    await refreshHistory()
    newCode.value = ''
    newName.value = ''
    newCost.value = ''
    newPosition.value = ''
    newTargetWin.value = ''
    newTargetLoss.value = ''
    newNote.value = ''
    showAdvanced.value = false
    await refresh()
  } catch (e) {
    addError.value = e.message
  } finally {
    adding.value = false
  }
}

// ====================== 行内编辑（v1.1）=======================
function isEditing(id, field) {
  return editingCell.value && editingCell.value.id === id && editingCell.value.field === field
}
function editKey(id, field) {
  return `${id}-${field}`
}
function startEdit(id, field, currentValue) {
  editingCell.value = { id, field }
  editValues.value[editKey(id, field)] = currentValue == null ? '' : String(currentValue)
  editError.value = ''
}
function cancelEdit() {
  editingCell.value = null
  editError.value = ''
}
async function commitEdit(id, field) {
  const key = editKey(id, field)
  const raw = (editValues.value[key] ?? '').trim()
  editingCell.value = null
  // 空串 → 视为"清空这个字段"
  let value = null
  if (raw !== '') {
    if (field === 'cost_price') {
      value = parseFloat(raw)
      if (Number.isNaN(value) || value < 0) {
        editError.value = '成本价必须是 ≥ 0 的数字'
        return
      }
    } else if (field === 'position') {
      value = parseInt(raw, 10)
      if (Number.isNaN(value) || value < 0 || String(value) !== raw) {
        editError.value = '持仓股数必须是 ≥ 0 的整数'
        return
      }
    } else if (field === 'target_win' || field === 'target_loss') {
      value = parseFloat(raw)
      if (Number.isNaN(value) || value <= 0) {
        editError.value = `${field === 'target_win' ? '止盈' : '止损'}价必须是 > 0 的数字`
        return
      }
    }
  }
  try {
    await updateWatchlist(id, { [field]: value })
    await refresh()
  } catch (e) {
    editError.value = e.message
  }
}

// ====================== 交易备忘 hover tooltip =======================
function showNoteTip(event, text) {
  if (!text) return
  noteTip.value = { x: event.clientX, y: event.clientY, text }
}
function moveNoteTip(event) {
  if (noteTip.value) {
    noteTip.value = { ...noteTip.value, x: event.clientX, y: event.clientY }
  }
}
function hideNoteTip() {
  noteTip.value = null
}

async function onRemove(id) {
  try {
    await removeFromWatchlist(id)
    await refresh()
  } catch (e) {
    error.value = e.message
  }
}

function openChart(code, name, cost = null, win = null, loss = null) {
  chartCode.value = code
  chartName.value = name || code
  // v2.1: 把持仓信息也带过去，KLineChart 画水平参考线用
  chartCost.value = cost
  chartTargetWin.value = win
  chartTargetLoss.value = loss
  // v2.2: 强制重置 KLineChart 组件（解决重开同一只股票不刷新的问题）
  chartKey.value = Date.now()
}
function closeChart() {
  chartCode.value = null
}
function onKeyDown(e) {
  if (e.key === 'Escape' && chartCode.value) closeChart()
}

function canNotify() {
  return typeof Notification !== 'undefined' && Notification.permission === 'granted'
}
function notifyNewSignals(fresh) {
  if (!canNotify()) return
  for (const s of fresh) {
    const sigs = s.signals || {}
    // 优先级：止盈/止损 > 量价异动
    if (sigs.is_take_profit) {
      try {
        new Notification('🎯 止盈信号', {
          body: `${s.ts_code} ${s.name || ''} 到达止盈线 ${fmtPrice(sigs.target_win)} · 现价 ${fmtPrice(s.current.close)} · 注意减仓`,
          tag: `signal-${s.ts_code}-take-profit`,
        })
      } catch (_) { /* ignore */ }
      continue
    }
    if (sigs.is_stop_loss) {
      try {
        new Notification('🛡️ 止损信号', {
          body: `${s.ts_code} ${s.name || ''} 触及止损线 ${fmtPrice(sigs.target_loss)} · 现价 ${fmtPrice(s.current.close)} · 建议减仓 / 离场`,
          tag: `signal-${s.ts_code}-stop-loss`,
          requireInteraction: true,  // 止损必须手动关，不自动消失
        })
      } catch (_) { /* ignore */ }
      continue
    }
    // 量价异动
    const kind = sigs.is_volume_breakout
      ? '放量突破'
      : sigs.is_shrinking_pullback ? '缩量企稳' : '异动'
    try {
      new Notification('量价异动', {
        body: `${s.ts_code} ${s.name || ''} 触发 ${kind}（量比 ${s.volume_ratio}，涨幅 ${fmtPct(s.current.change_pct)}）`,
        tag: `signal-${s.ts_code}-${kind}`,
      })
    } catch (_) { /* ignore */ }
  }
}
async function requestNotifyPermission() {
  if (typeof Notification === 'undefined') return
  if (Notification.permission === 'default') {
    try { await Notification.requestPermission() } catch (_) {}
  }
}

// ====================== v2.3: 复盘战报 ======================
async function openSummary() {
  // 模态框立即打开（哪怕没数据），拉数据是异步
  showSummary.value = true
  summaryLoading.value = true
  summary.value = null
  try {
    const r = await getDailySummary()
    summary.value = r.data
  } catch (e) {
    // 模态框里显示错误（顶部留 addError 一致风格）
    summary.value = { error: e.message || '拉取失败' }
  } finally {
    summaryLoading.value = false
  }
}
function closeSummary() {
  showSummary.value = false
}

// v2.4: 召唤 AI 深度复盘
async function summonAiReport() {
  aiError.value = ''
  aiReport.value = null
  aiLoading.value = true
  try {
    const r = await getAiReport()
    aiReport.value = r.data
  } catch (e) {
    // 后端 503 / 502 / 网络错误 → 提示
    const status = e?.response?.status
    const detail = e?.response?.data?.detail
    if (status === 503) {
      aiError.value = '🔑 ' + (detail || '未配置 LLM_API_KEY，请到项目根目录的 .env 文件设置（参考 .env.example）。')
    } else if (status === 502) {
      aiError.value = '⚠️ ' + (detail || 'LLM 服务调用失败，请检查网络 / API key / 余额。')
    } else {
      aiError.value = '❌ ' + (detail || e.message || '未知错误')
    }
  } finally {
    aiLoading.value = false
  }
}

// 自动触发：每分钟检查一次，如果当前时间 ≥ 15:00 且今天还没触发过，
// 弹一次通知 + 把模态框也打开（用户已经看一天盘了）
function checkSummaryAutoTrigger() {
  if (typeof window === 'undefined') return
  const d = new Date()
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  // 已经触发过
  if (summaryNotifiedDate.value === today) return
  // 没到 15:00
  if (d.getHours() < SUMMARY_AUTO_TRIGGER_HOUR) return
  // 是工作日（A 股市场周一~周五，简单按 0~4）
  const day = d.getDay()
  if (day === 0 || day === 6) return
  // 标记已触发（持久化到 localStorage 防刷新丢失）
  summaryNotifiedDate.value = today
  try { localStorage.setItem('summary_notified_date', today) } catch (_) {}
  // 弹通知
  if (canNotify()) {
    try {
      const n = new Notification('🔔 收盘啦！', {
        body: '今日 A 股复盘战报已生成，点击查看。',
        tag: 'daily-summary',
        requireInteraction: true,
      })
      n.onclick = () => {
        window.focus()
        openSummary()
        n.close()
      }
    } catch (_) { /* 忽略 */ }
  }
  // 同时把模态框也开了（用户大概率正在看）
  openSummary()
}

let pollTimer = null
let clockTimer = null
let fundFlowTimer = null
let radarTimer = null  // v2.2
let summaryCheckTimer = null  // v2.3
async function refreshFundFlow() {
  fundFlowLoading.value = true
  try {
    const r = await getFundFlow({ limit: 300 })
    fundFlow.value = r
  } catch (e) {
    // 板块接口失败不打断主表（板块不在交易时段也可能没数据）
    console.warn('fund flow refresh failed:', e.message)
  } finally {
    fundFlowLoading.value = false
  }
}
// v2.2: 实时异动雷达
async function refreshRadar() {
  radarLoading.value = true
  try {
    const r = await getTopMovers({ sort_by: 'change_pct', limit: RADAR_LIMIT })
    radar.value = Array.isArray(r.data) ? r.data : []
  } catch (e) {
    // 雷达失败不打断主表
    console.warn('radar refresh failed:', e.message)
  } finally {
    radarLoading.value = false
  }
}
onMounted(() => {
  requestNotifyPermission()
  refresh()
  refreshFundFlow()  // 立即拉一次
  refreshRadar()     // 立即拉一次
  pollTimer = setInterval(refresh, REFRESH_INTERVAL_MS)
  fundFlowTimer = setInterval(refreshFundFlow, FUND_FLOW_INTERVAL_MS)
  radarTimer = setInterval(refreshRadar, RADAR_INTERVAL_MS)  // v2.2
  // v2.3: 复盘战报自动触发
  try { summaryNotifiedDate.value = localStorage.getItem('summary_notified_date') || '' } catch (_) {}
  checkSummaryAutoTrigger()  // 启动时立即检查一次（如果是 15:00 后才刷新页面，立即弹）
  summaryCheckTimer = setInterval(checkSummaryAutoTrigger, SUMMARY_CHECK_INTERVAL_MS)
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  window.addEventListener('keydown', onKeyDown)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (fundFlowTimer) clearInterval(fundFlowTimer)
  if (radarTimer) clearInterval(radarTimer)  // v2.2
  if (summaryCheckTimer) clearInterval(summaryCheckTimer)  // v2.3
  window.removeEventListener('keydown', onKeyDown)
})
</script>

<template>
  <div class="min-h-screen p-4 md:p-6 max-w-7xl mx-auto">
    <!-- ====================== 顶部：标题 + 状态栏 ====================== -->
    <header class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-2xl md:text-3xl font-bold text-slate-100 tracking-wide">
          <span class="text-gradient-bull">股市情绪</span>
          <span class="text-slate-100">监控终端</span>
        </h1>
        <p class="text-xs text-slate-500 mt-1.5 font-mono">
          上次更新 {{ fmtTimeAgo(lastUpdated) }}
          <span v-if="loading" class="ml-2 text-sky-400 animate-pulse">⟳ 拉取中</span>
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          v-if="!canNotify()"
          @click="requestNotifyPermission"
          class="px-3 py-1.5 glass text-slate-300 hover:text-slate-100
                 hover:border-sky-500/50 text-xs transition"
        >🔔 开启通知</button>
        <button
          @click="openSummary"
          class="px-3 py-1.5 glass text-amber-300 hover:text-amber-100
                 hover:border-amber-500/50 text-xs transition
                 border border-amber-500/30"
          title="查看今日 A 股复盘战报"
        >📝 今日复盘</button>
        <button
          @click="refresh"
          :disabled="loading"
          class="px-4 py-1.5 bg-sky-600/90 hover:bg-sky-500 disabled:bg-slate-700
                 disabled:text-slate-500 text-white rounded text-sm font-medium
                 transition shadow-lg shadow-sky-900/30"
        >{{ loading ? '加载中…' : '手动刷新' }}</button>
      </div>
    </header>

    <p v-if="error" class="mb-4 p-3 glass border-rose-500/40 text-rose-200 text-sm">
      {{ error }}
    </p>

    <!-- ====================== 添加表单（v1.2: 高级选项折叠）===================== -->
    <section class="glass p-5 mb-6">
      <form @submit.prevent="onAdd" class="space-y-3">
        <div class="flex flex-wrap items-end gap-3">
          <div class="flex-1 min-w-[140px]">
            <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
              股票 / ETF 代码
            </label>
            <input
              v-model="newCode"
              type="text"
              placeholder="sh600000 / 600000 / 510300"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                     font-mono text-sm transition"
            />
          </div>
          <div class="flex-1 min-w-[120px]">
            <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
              名称（可选）
            </label>
            <input
              v-model="newName"
              type="text"
              placeholder="浦发银行"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                     text-sm transition"
            />
          </div>
          <div class="w-24">
            <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
              成本价
            </label>
            <input
              v-model="newCost"
              type="number" step="0.01" min="0"
              placeholder="10.50"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                     font-mono text-sm transition"
            />
          </div>
          <div class="w-24">
            <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
              持仓股
            </label>
            <input
              v-model="newPosition"
              type="number" step="1" min="0"
              placeholder="1000"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                     font-mono text-sm transition"
            />
          </div>
          <button
            type="button"
            @click="showAdvanced = !showAdvanced"
            class="px-3 py-2 glass text-slate-300 hover:text-slate-100
                   hover:border-sky-500/50 text-xs transition"
            :title="showAdvanced ? '收起高级选项' : '展开高级选项（止盈止损 / 备忘）'"
          >
            <span class="inline-block transition" :class="showAdvanced ? 'rotate-90' : ''">▸</span>
            高级选项
          </button>
          <button
            type="submit"
            :disabled="adding"
            class="px-5 py-2 bg-emerald-600/90 hover:bg-emerald-500
                   disabled:bg-slate-700 disabled:text-slate-500
                   text-white rounded font-medium text-sm transition
                   shadow-lg shadow-emerald-900/30"
          >{{ adding ? '添加中…' : '+ 添加' }}</button>
        </div>

        <!-- 高级选项：止盈 / 止损 / 备忘 -->
        <div
          v-show="showAdvanced"
          class="grid grid-cols-1 md:grid-cols-3 gap-3 pt-3 border-t border-slate-700/40"
        >
          <div>
            <label class="block text-xs text-emerald-400 mb-1.5 tracking-wider font-medium">
              🎯 止盈价
            </label>
            <input
              v-model="newTargetWin"
              type="number" step="0.01" min="0"
              placeholder="15.50"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-emerald-500/70 focus:ring-1 focus:ring-emerald-500/50
                     font-mono text-sm transition"
            />
          </div>
          <div>
            <label class="block text-xs text-rose-400 mb-1.5 tracking-wider font-medium">
              🛡️ 止损价
            </label>
            <input
              v-model="newTargetLoss"
              type="number" step="0.01" min="0"
              placeholder="13.20"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-rose-500/70 focus:ring-1 focus:ring-rose-500/50
                     font-mono text-sm transition"
            />
          </div>
          <div>
            <label class="block text-xs text-amber-400 mb-1.5 tracking-wider font-medium">
              📝 交易逻辑
            </label>
            <input
              v-model="newNote"
              type="text"
              placeholder="突破前高 + 缩量回踩 10 日线"
              class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                     text-slate-100 placeholder-slate-600 focus:outline-none
                     focus:border-amber-500/70 focus:ring-1 focus:ring-amber-500/50
                     text-sm transition"
            />
          </div>
        </div>

        <p v-if="addError" class="text-rose-400 text-xs font-mono">
          {{ addError }}
        </p>
      </form>
    </section>

    <!-- ====================== 情绪仪表盘 ====================== -->
    <section v-if="sentiment" class="mb-6 grid grid-cols-1 md:grid-cols-4 gap-4">
      <!-- 主情绪分：渐变文字 + 闪光 -->
      <div class="md:col-span-2 glass p-6 flex items-center justify-between">
        <div>
          <p class="text-slate-400 text-xs uppercase tracking-[0.2em]">市场情绪</p>
          <p class="text-xs text-slate-500 mt-2 font-mono">
            基准 {{ sentiment.swing_score }} · 打板 {{ sentiment.limit_premium }}
          </p>
          <p class="text-xs text-slate-500 mt-1 font-mono">
            样本 {{ sentiment.total_stocks.toLocaleString() }} 只
          </p>
        </div>
        <div class="text-right">
          <p
            :class="[
              'text-7xl md:text-8xl font-bold font-mono leading-none tracking-tight',
              sentimentGradientClass(sentiment.score),
              sentimentGlowClass(sentiment.score),
            ]"
          >
            {{ sentiment.score.toFixed(1) }}
          </p>
          <p class="text-sm text-slate-400 mt-2 font-medium">
            {{ sentimentLabel(sentiment.score) }}
          </p>
        </div>
      </div>

      <!-- 涨/跌家数 -->
      <div class="glass p-5">
        <p class="text-slate-400 text-xs uppercase tracking-wider">上涨 / 下跌</p>
        <p class="text-3xl font-mono mt-3 leading-none">
          <span :class="sentiment.up_count > sentiment.down_count ? 'glow-rose text-rose-400' : 'text-rose-400'">
            {{ sentiment.up_count.toLocaleString() }}
          </span>
          <span class="text-slate-600 mx-1.5">/</span>
          <span :class="sentiment.down_count > sentiment.up_count ? 'glow-emerald text-emerald-400' : 'text-emerald-400'">
            {{ sentiment.down_count.toLocaleString() }}
          </span>
        </p>
        <p class="text-xs text-slate-500 mt-3 font-mono">
          比值 {{ sentiment.up_ratio.toFixed(2) }}
        </p>
      </div>

      <!-- 涨停/跌停 -->
      <div class="glass p-5">
        <p class="text-slate-400 text-xs uppercase tracking-wider">涨停 / 跌停</p>
        <p class="text-3xl font-mono mt-3 leading-none">
          <span :class="sentiment.limit_up_count > 0 ? 'glow-rose text-rose-400' : 'text-rose-400'">
            {{ sentiment.limit_up_count }}
          </span>
          <span class="text-slate-600 mx-1.5">/</span>
          <span :class="sentiment.limit_down_count > 0 ? 'glow-emerald text-emerald-400' : 'text-emerald-400'">
            {{ sentiment.limit_down_count }}
          </span>
        </p>
        <p class="text-xs text-slate-500 mt-3 font-mono">
          溢价 {{ sentiment.limit_premium.toFixed(2) }}
        </p>
      </div>
    </section>

    <!-- ====================== 板块资金流向气泡图 ====================== -->
    <section class="mb-6">
      <FundFlowBubble
        :items="fundFlow.items"
        :loading="fundFlowLoading"
      />
    </section>

    <!-- ====================== 自选股盯盘表 ====================== -->
    <section class="glass overflow-hidden">
      <div class="px-5 py-3 flex items-center justify-between border-b border-slate-700/40">
        <h2 class="text-sm font-semibold text-slate-200 tracking-wide">
          自选股盯盘
          <span class="text-slate-500 font-normal ml-2 font-mono">
            {{ watchlist.length }} 只 ·
            <span :class="signals.length > 0 ? 'text-rose-400' : 'text-slate-500'">
              {{ signals.length }} 触发
            </span>
          </span>
        </h2>
        <span class="text-xs text-slate-600 hidden md:inline font-mono">
          点击表头排序 · 点击名称 / 走势看 K 线
        </span>
      </div>

      <div v-if="!watchlist.length" class="p-12 text-center text-slate-500 text-sm">
        自选股为空。👆 在顶部输入代码（<code class="bg-slate-800/80 px-1.5 py-0.5 rounded font-mono text-sky-300">sh600000</code> / <code class="bg-slate-800/80 px-1.5 py-0.5 rounded font-mono text-sky-300">sh510300</code>）开始。
      </div>

      <div v-else class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="bg-slate-950/40 text-slate-400 text-xs uppercase tracking-wider">
            <tr>
              <th class="text-left py-3 px-4 font-medium">代码</th>
              <th class="text-left py-3 px-4 font-medium cursor-pointer hover:text-sky-300 transition"
                  @click="openChart(watchlist[0]?.ts_code, watchlist[0]?.name, watchlist[0]?.cost_price, watchlist[0]?.target_win, watchlist[0]?.target_loss)"
                  title="点击查看第一只的 K 线">名称</th>
              <th class="text-right py-3 px-4 font-medium">现价</th>
              <th
                class="text-right py-3 px-4 font-medium cursor-pointer select-none
                       hover:text-slate-200 transition"
                :class="sortKey === 'change_pct' ? 'text-sky-400' : ''"
                @click="toggleSort('change_pct')"
              >
                涨跌幅 <span class="text-xs ml-0.5">{{ sortIndicator('change_pct') }}</span>
              </th>
              <th
                class="text-right py-3 px-4 font-medium cursor-pointer select-none
                       hover:text-slate-200 transition"
                :class="sortKey === 'return_rate' ? 'text-sky-400' : ''"
                @click="toggleSort('return_rate')"
                title="点击按收益率排序"
              >
                收益率 <span class="text-xs ml-0.5">{{ sortIndicator('return_rate') }}</span>
              </th>
              <th class="text-right py-3 px-4 font-medium">成交量</th>
              <th
                class="text-right py-3 px-4 font-medium cursor-pointer select-none
                       hover:text-slate-200 transition"
                :class="sortKey === 'volume_ratio' ? 'text-sky-400' : ''"
                @click="toggleSort('volume_ratio')"
              >
                量比 <span class="text-xs ml-0.5">{{ sortIndicator('volume_ratio') }}</span>
              </th>
              <th class="text-right py-3 px-4 font-medium" title="点击单元格修改">成本价</th>
              <th class="text-right py-3 px-4 font-medium" title="点击单元格修改">止盈</th>
              <th class="text-right py-3 px-4 font-medium" title="点击单元格修改">止损</th>
              <th class="text-right py-3 px-4 font-medium" title="点击单元格修改">持仓股</th>
              <th class="text-right py-3 px-4 font-medium">持仓盈亏</th>
              <th class="text-center py-3 px-4 font-medium">交易计划</th>
              <th class="text-left py-3 px-4 font-medium">信号</th>
              <th class="text-right py-3 px-4 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="w in sortedWatchlist"
              :key="w.id"
              class="border-t border-slate-700/30 row-hover"
            >
              <td class="py-3 px-4 font-mono text-sky-300">{{ w.ts_code }}</td>
              <td
                class="py-3 px-4 text-slate-200 cursor-pointer hover:text-sky-300 transition font-medium"
                @click="openChart(w.ts_code, w.name || w.name_from_market, w.cost_price, w.target_win, w.target_loss)"
                title="点击查看 K 线"
              >
                {{ w.name || w.name_from_market || '-' }}
              </td>
              <td class="py-3 px-4 text-right font-mono text-slate-100">
                {{ w.in_cache ? fmtPrice(w.price) : '-' }}
              </td>
              <td
                class="py-3 px-4 text-right font-mono font-semibold"
                :class="{
                  'text-rose-400': w.change_pct > 0,
                  'text-emerald-400': w.change_pct < 0,
                  'text-slate-500': w.change_pct == null,
                }"
              >{{ w.in_cache ? fmtPct(w.change_pct) : '-' }}</td>
              <td
                class="py-3 px-4 text-right font-mono font-semibold"
                :class="pnlColorClass(w.return_rate)"
              >{{ fmtPct(w.return_rate) }}</td>
              <td class="py-3 px-4 text-right font-mono text-slate-400">
                {{ w.in_cache ? fmtVol(w.volume) : '-' }}
              </td>
              <td
                class="py-3 px-4 text-right font-mono"
                :class="{
                  'text-rose-400 font-bold': w.volume_ratio != null && w.volume_ratio > 2.5,
                  'text-emerald-400': w.volume_ratio != null && w.volume_ratio < 0.8,
                  'text-slate-400': w.volume_ratio == null || (w.volume_ratio >= 0.8 && w.volume_ratio <= 2.5),
                }"
              >{{ w.volume_ratio != null ? w.volume_ratio.toFixed(2) : '-' }}</td>

              <!-- ====== 成本价：行内编辑 ====== -->
              <td class="py-3 px-4 text-right font-mono">
                <input
                  v-if="isEditing(w.id, 'cost_price')"
                  v-focus
                  v-model="editValues[editKey(w.id, 'cost_price')]"
                  @blur="commitEdit(w.id, 'cost_price')"
                  @keyup.enter="commitEdit(w.id, 'cost_price')"
                  @keyup.escape="cancelEdit"
                  type="number" step="0.01" min="0"
                  class="w-24 px-2 py-1 bg-slate-900 border border-sky-500/60 rounded
                         text-slate-100 font-mono text-sm text-right
                         focus:outline-none focus:ring-1 focus:ring-sky-500/50"
                />
                <span
                  v-else
                  @click="startEdit(w.id, 'cost_price', w.cost_price)"
                  :class="[
                    'cursor-pointer hover:bg-slate-800/40 px-2 py-1 rounded inline-block min-w-[60px]',
                    w.cost_price != null ? 'text-slate-200' : 'text-slate-600',
                  ]"
                  :title="w.cost_price != null ? `当前 ${fmtPrice(w.cost_price)} · 点击修改` : '点击录入成本价'"
                >
                  {{ w.cost_price != null ? fmtPrice(w.cost_price) : '+' }}
                </span>
              </td>

              <!-- ====== 止盈：行内编辑 ====== -->
              <td class="py-3 px-4 text-right font-mono">
                <input
                  v-if="isEditing(w.id, 'target_win')"
                  v-focus
                  v-model="editValues[editKey(w.id, 'target_win')]"
                  @blur="commitEdit(w.id, 'target_win')"
                  @keyup.enter="commitEdit(w.id, 'target_win')"
                  @keyup.escape="cancelEdit"
                  type="number" step="0.01" min="0"
                  class="w-24 px-2 py-1 bg-slate-900 border border-emerald-500/60 rounded
                         text-slate-100 font-mono text-sm text-right
                         focus:outline-none focus:ring-1 focus:ring-emerald-500/50"
                />
                <span
                  v-else
                  @click="startEdit(w.id, 'target_win', w.target_win)"
                  :class="[
                    'cursor-pointer hover:bg-slate-800/40 px-2 py-1 rounded inline-block min-w-[60px]',
                    w.target_win != null ? 'text-emerald-300' : 'text-slate-600',
                  ]"
                  :title="w.target_win != null ? `止盈 ${fmtPrice(w.target_win)} · 点击修改` : '点击录入止盈价'"
                >
                  {{ w.target_win != null ? fmtPrice(w.target_win) : '+' }}
                </span>
              </td>

              <!-- ====== 止损：行内编辑 ====== -->
              <td class="py-3 px-4 text-right font-mono">
                <input
                  v-if="isEditing(w.id, 'target_loss')"
                  v-focus
                  v-model="editValues[editKey(w.id, 'target_loss')]"
                  @blur="commitEdit(w.id, 'target_loss')"
                  @keyup.enter="commitEdit(w.id, 'target_loss')"
                  @keyup.escape="cancelEdit"
                  type="number" step="0.01" min="0"
                  class="w-24 px-2 py-1 bg-slate-900 border border-rose-500/60 rounded
                         text-slate-100 font-mono text-sm text-right
                         focus:outline-none focus:ring-1 focus:ring-rose-500/50"
                />
                <span
                  v-else
                  @click="startEdit(w.id, 'target_loss', w.target_loss)"
                  :class="[
                    'cursor-pointer hover:bg-slate-800/40 px-2 py-1 rounded inline-block min-w-[60px]',
                    w.target_loss != null ? 'text-rose-300' : 'text-slate-600',
                  ]"
                  :title="w.target_loss != null ? `止损 ${fmtPrice(w.target_loss)} · 点击修改` : '点击录入止损价'"
                >
                  {{ w.target_loss != null ? fmtPrice(w.target_loss) : '+' }}
                </span>
              </td>

              <!-- ====== 持仓股：行内编辑 ====== -->
              <td class="py-3 px-4 text-right font-mono">
                <input
                  v-if="isEditing(w.id, 'position')"
                  v-focus
                  v-model="editValues[editKey(w.id, 'position')]"
                  @blur="commitEdit(w.id, 'position')"
                  @keyup.enter="commitEdit(w.id, 'position')"
                  @keyup.escape="cancelEdit"
                  type="number" step="1" min="0"
                  class="w-24 px-2 py-1 bg-slate-900 border border-sky-500/60 rounded
                         text-slate-100 font-mono text-sm text-right
                         focus:outline-none focus:ring-1 focus:ring-sky-500/50"
                />
                <span
                  v-else
                  @click="startEdit(w.id, 'position', w.position)"
                  :class="[
                    'cursor-pointer hover:bg-slate-800/40 px-2 py-1 rounded inline-block min-w-[60px]',
                    w.position != null ? 'text-slate-200' : 'text-slate-600',
                  ]"
                  :title="w.position != null ? `当前 ${fmtVol(w.position)} 股 · 点击修改` : '点击录入持仓'"
                >
                  {{ w.position != null ? fmtVol(w.position) : '+' }}
                </span>
              </td>

              <!-- ====== 持仓盈亏 ====== -->
              <td
                class="py-3 px-4 text-right font-mono font-semibold"
                :class="[pnlColorClass(w.floating_pnl), pnlGlowClass(w.floating_pnl)]"
                :title="w.floating_pnl != null
                         ? `${w.position} 股 × (现价 ${fmtPrice(w.price)} - 成本 ${fmtPrice(w.cost_price)})`
                         : '需要同时填入成本价和持仓股才计算盈亏'"
              >
                {{ fmtPnl(w.floating_pnl) }}
              </td>

              <!-- ====== 交易计划：trade_note tooltip + 止盈止损小字 ====== -->
              <td class="py-3 px-4 text-left">
                <div class="flex items-start gap-2">
                  <span
                    v-if="w.trade_note"
                    @mouseenter="showNoteTip($event, w.trade_note)"
                    @mousemove="moveNoteTip"
                    @mouseleave="hideNoteTip"
                    class="cursor-help text-amber-400 hover:text-amber-300 text-base
                           hover:scale-110 inline-block transition flex-shrink-0 mt-0.5"
                    :title="w.trade_note"
                  >📝</span>
                  <span
                    v-else
                    class="text-slate-700 text-xs flex-shrink-0 mt-0.5"
                  >—</span>
                  <div class="flex flex-col gap-0.5 text-xs font-mono leading-tight">
                    <span v-if="w.target_win != null" class="text-emerald-400/80">
                      止盈 {{ fmtPrice(w.target_win) }}
                    </span>
                    <span v-if="w.target_loss != null" class="text-rose-400/80">
                      止损 {{ fmtPrice(w.target_loss) }}
                    </span>
                  </div>
                </div>
              </td>

              <td class="py-3 px-4">
                <div class="flex flex-wrap gap-1">
                  <!-- v1.2: 止盈 / 止损优先显示，pulse 动画 -->
                  <span
                    v-if="w.signal?.signals?.is_take_profit"
                    class="badge badge-win"
                    :title="`触发止盈线 ${fmtPrice(w.signal.signals.target_win)}`"
                  >
                    🎯 止盈
                  </span>
                  <span
                    v-if="w.signal?.signals?.is_stop_loss"
                    class="badge badge-loss"
                    :title="`触及止损线 ${fmtPrice(w.signal.signals.target_loss)}`"
                  >
                    🛡️ 止损
                  </span>
                  <span v-if="w.signal?.signals?.is_volume_breakout" class="badge badge-breakout">
                    🔥 放量突破
                  </span>
                  <span v-else-if="w.signal?.signals?.is_shrinking_pullback" class="badge badge-shrinking">
                    🟢 缩量企稳
                  </span>
                  <span
                    v-if="!w.signal?.signals?.is_take_profit
                       && !w.signal?.signals?.is_stop_loss
                       && !w.signal?.signals?.is_volume_breakout
                       && !w.signal?.signals?.is_shrinking_pullback"
                    class="text-slate-600 text-xs"
                  >—</span>
                </div>
              </td>
              <td class="py-3 px-4 text-right whitespace-nowrap">
                <button
                  @click="openChart(w.ts_code, w.name || w.name_from_market, w.cost_price, w.target_win, w.target_loss)"
                  class="text-sky-400 hover:text-sky-300 text-xs mr-3 transition font-medium"
                >走势</button>
                <button
                  @click="onRemove(w.id)"
                  class="text-slate-500 hover:text-rose-400 text-xs transition"
                >删除</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="editError" class="px-5 py-2 text-rose-400 text-xs font-mono">
          行内编辑失败：{{ editError }}
        </p>
      </div>
    </section>

    <!-- ====================== 实时异动雷达（v2.2）====================== -->
    <section class="glass mt-4 overflow-hidden">
      <div class="px-5 py-3 flex items-center justify-between border-b border-slate-700/40">
        <h2 class="text-sm font-semibold text-slate-200 tracking-wide">
          🔥 实时异动雷达
          <span class="text-slate-500 font-normal ml-2 font-mono">
            涨幅榜 Top {{ RADAR_LIMIT }} · 每 5s 刷新
          </span>
        </h2>
        <span class="text-xs text-slate-600 hidden md:inline font-mono">
          点击名称 / 代码 → 弹出 K 线
        </span>
      </div>
      <div v-if="!radar.length" class="p-8 text-center text-slate-500 text-sm">
        {{ radarLoading ? '拉取中…' : '暂无数据' }}
      </div>
      <div
        v-else
        class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5
               divide-x divide-y divide-slate-700/30"
      >
        <div
          v-for="(r, i) in radar"
          :key="r.code"
          @click="openChart(r.code, r.name, null, null, null)"
          class="px-3 py-2 cursor-pointer hover:bg-slate-700/40 transition
                 group relative"
          :title="`${r.name || r.code} · 点击查看 K 线`"
        >
          <!-- 排名角标 -->
          <div class="flex items-baseline gap-1.5">
            <span
              class="text-[10px] font-mono tabular-nums"
              :class="i < 3 ? 'text-rose-400 font-bold' : 'text-slate-600'"
            >{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="text-xs text-slate-200 truncate group-hover:text-sky-300 transition">
              {{ r.name || r.code }}
            </span>
          </div>
          <div class="flex items-baseline justify-between mt-0.5">
            <span class="font-mono text-[11px] text-slate-500 tabular-nums">
              {{ r.price != null ? r.price.toFixed(2) : '-' }}
            </span>
            <span
              class="font-mono text-[11px] font-semibold tabular-nums"
              :class="r.change_pct > 0 ? 'text-rose-400' : r.change_pct < 0 ? 'text-emerald-400' : 'text-slate-500'"
            >
              {{ r.change_pct > 0 ? '+' : '' }}{{ r.change_pct.toFixed(2) }}%
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- ====================== 复盘战报模态框（v2.3）====================== -->
    <Teleport to="body">
      <div
        v-if="showSummary"
        class="fixed inset-0 z-50 flex items-center justify-center p-4
               bg-black/60 backdrop-blur-sm"
        @click.self="closeSummary"
      >
        <div
          class="relative bg-slate-900/90 backdrop-blur-md rounded-xl
                 border border-slate-700/60 shadow-2xl
                 w-full max-w-4xl max-h-[85vh] overflow-y-auto
                 before:absolute before:inset-0 before:rounded-xl before:p-[1px]
                 before:bg-gradient-to-br before:from-amber-500/30 before:via-purple-500/20 before:to-sky-500/30
                 before:-z-10 before:pointer-events-none"
        >
          <!-- 顶部条 -->
          <div class="sticky top-0 z-10 bg-slate-900/95 backdrop-blur
                      flex items-center justify-between px-6 py-4
                      border-b border-slate-700/40 rounded-t-xl">
            <div>
              <h3 class="text-xl font-semibold text-slate-100">
                📝 今日 A 股复盘战报
              </h3>
              <p v-if="summary?.generated_at" class="text-xs text-slate-500 mt-1 font-mono">
                生成于 {{ summary.generated_at }}
              </p>
            </div>
            <div class="flex items-center gap-2">
              <button
                @click="summonAiReport"
                :disabled="aiLoading"
                class="px-3 py-1.5 text-xs font-medium rounded
                       bg-gradient-to-r from-amber-500/20 via-purple-500/20 to-sky-500/20
                       border border-amber-400/40
                       text-amber-200 hover:text-amber-100
                       hover:from-amber-500/30 hover:via-purple-500/30 hover:to-sky-500/30
                       hover:border-amber-400/60
                       transition shadow-[0_0_20px_rgba(245,158,11,0.15)]
                       hover:shadow-[0_0_24px_rgba(245,158,11,0.3)]
                       disabled:opacity-50 disabled:cursor-not-allowed
                       flex items-center gap-1.5"
                title="用大模型基于今日数据写一篇 AI 深度复盘小作文"
              >
                <span class="inline-block animate-pulse">✨</span>
                <span>{{ aiLoading ? 'AI 思考中…' : '召唤 AI 深度复盘' }}</span>
              </button>
              <button
                @click="closeSummary"
                class="text-slate-500 hover:text-slate-200 text-2xl leading-none
                       w-8 h-8 flex items-center justify-center rounded
                       hover:bg-slate-800/60 transition"
              >×</button>
            </div>
          </div>

          <!-- 内容 -->
          <div v-if="summaryLoading" class="p-12 text-center text-slate-500">
            拉取战报数据中…
          </div>
          <div v-else-if="summary?.error" class="p-12 text-center text-rose-400">
            拉取失败：{{ summary.error }}
          </div>
          <div v-else-if="summary" class="p-6 space-y-4">
            <!-- ====== 卡片 1: 大盘情绪 ====== -->
            <div class="glass p-5">
              <div class="flex items-center gap-2 mb-3">
                <span class="text-base">📊</span>
                <h4 class="text-sm font-semibold text-slate-200 tracking-wide">
                  大盘情绪
                </h4>
              </div>
              <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p class="text-xs text-slate-500 uppercase tracking-wider">情绪分</p>
                  <p
                    class="text-3xl font-bold font-mono mt-1"
                    :class="(summary.sentiment?.score ?? 50) >= 50 ? 'text-rose-400' : 'text-emerald-400'"
                  >{{ summary.sentiment?.score?.toFixed?.(1) ?? '-' }}</p>
                </div>
                <div>
                  <p class="text-xs text-slate-500 uppercase tracking-wider">涨 / 跌</p>
                  <p class="text-2xl font-mono mt-1">
                    <span class="text-rose-400 font-semibold">{{ summary.sentiment?.up_count ?? 0 }}</span>
                    <span class="text-slate-600 mx-1">/</span>
                    <span class="text-emerald-400 font-semibold">{{ summary.sentiment?.down_count ?? 0 }}</span>
                  </p>
                </div>
                <div>
                  <p class="text-xs text-slate-500 uppercase tracking-wider">涨 / 跌停</p>
                  <p class="text-2xl font-mono mt-1">
                    <span class="text-rose-400 font-semibold">{{ summary.sentiment?.limit_up_count ?? 0 }}</span>
                    <span class="text-slate-600 mx-1">/</span>
                    <span class="text-emerald-400 font-semibold">{{ summary.sentiment?.limit_down_count ?? 0 }}</span>
                  </p>
                </div>
                <div>
                  <p class="text-xs text-slate-500 uppercase tracking-wider">上涨比</p>
                  <p class="text-2xl font-mono mt-1 text-slate-200">
                    {{ ((summary.sentiment?.up_ratio ?? 0.5) * 100).toFixed(1) }}%
                  </p>
                </div>
              </div>
            </div>

            <!-- ====== 卡片 2: 自选股战况 ====== -->
            <div class="glass p-5">
              <div class="flex items-center gap-2 mb-3">
                <span class="text-base">⚔️</span>
                <h4 class="text-sm font-semibold text-slate-200 tracking-wide">
                  自选股战况
                </h4>
                <span class="text-xs text-slate-500 ml-2 font-mono">
                  共 {{ summary.watchlist_battle?.total ?? 0 }} 只 ·
                  <span class="text-rose-400">{{ summary.watchlist_battle?.winning_count ?? 0 }} 盈</span> /
                  <span class="text-emerald-400">{{ summary.watchlist_battle?.losing_count ?? 0 }} 亏</span> /
                  <span class="text-slate-500">{{ summary.watchlist_battle?.no_position_count ?? 0 }} 观望</span>
                </span>
              </div>
              <!-- 核心指标 -->
              <div class="grid grid-cols-3 gap-3 mb-4">
                <div class="p-3 rounded bg-slate-950/40 border border-slate-700/40">
                  <p class="text-xs text-slate-500">浮动盈亏</p>
                  <p
                    class="text-xl font-mono font-bold mt-1"
                    :class="(summary.watchlist_battle?.floating_pnl_total ?? 0) > 0 ? 'text-rose-400' : (summary.watchlist_battle?.floating_pnl_total ?? 0) < 0 ? 'text-emerald-400' : 'text-slate-400'"
                  >{{ (summary.watchlist_battle?.floating_pnl_total ?? 0) > 0 ? '+' : '' }}{{ summary.watchlist_battle?.floating_pnl_total?.toLocaleString?.() ?? '0.00' }}</p>
                </div>
                <div class="p-3 rounded bg-slate-950/40 border border-slate-700/40">
                  <p class="text-xs text-slate-500">总收益率</p>
                  <p
                    class="text-xl font-mono font-bold mt-1"
                    :class="(summary.watchlist_battle?.total_return_rate ?? 0) > 0 ? 'text-rose-400' : (summary.watchlist_battle?.total_return_rate ?? 0) < 0 ? 'text-emerald-400' : 'text-slate-400'"
                  >{{ summary.watchlist_battle?.total_return_rate != null ? ((summary.watchlist_battle.total_return_rate > 0 ? '+' : '') + summary.watchlist_battle.total_return_rate.toFixed(2) + '%') : '-' }}</p>
                </div>
                <div class="p-3 rounded bg-slate-950/40 border border-slate-700/40">
                  <p class="text-xs text-slate-500">总市值 / 总成本</p>
                  <p class="text-sm font-mono mt-1 text-slate-200">
                    {{ summary.watchlist_battle?.market_total?.toLocaleString?.() ?? '-' }}
                    <span class="text-slate-600 mx-1">/</span>
                    {{ summary.watchlist_battle?.cost_total?.toLocaleString?.() ?? '-' }}
                  </p>
                </div>
              </div>
              <!-- 触发信号 -->
              <div v-if="(summary.watchlist_battle?.take_profit_triggered?.length ?? 0) > 0" class="mb-3">
                <p class="text-xs text-emerald-400 font-semibold mb-1.5">🎯 止盈触发</p>
                <div class="flex flex-wrap gap-2">
                  <span
                    v-for="x in summary.watchlist_battle.take_profit_triggered"
                    :key="`tp-${x.ts_code}`"
                    @click="openChart(x.ts_code, x.name)"
                    class="px-2 py-1 rounded bg-emerald-500/15 border border-emerald-500/40
                           text-xs text-emerald-200 cursor-pointer hover:bg-emerald-500/25 transition"
                    :title="`点击查看 ${x.name || x.ts_code} K 线`"
                  >
                    {{ x.name || x.ts_code }} · 现价 {{ x.price?.toFixed?.(2) }} → 止盈 {{ x.target_win }}
                  </span>
                </div>
              </div>
              <div v-if="(summary.watchlist_battle?.stop_loss_triggered?.length ?? 0) > 0" class="mb-3">
                <p class="text-xs text-rose-400 font-semibold mb-1.5">🛡️ 止损触发</p>
                <div class="flex flex-wrap gap-2">
                  <span
                    v-for="x in summary.watchlist_battle.stop_loss_triggered"
                    :key="`sl-${x.ts_code}`"
                    @click="openChart(x.ts_code, x.name)"
                    class="px-2 py-1 rounded bg-rose-500/15 border border-rose-500/40
                           text-xs text-rose-200 cursor-pointer hover:bg-rose-500/25 transition"
                    :title="`点击查看 ${x.name || x.ts_code} K 线`"
                  >
                    {{ x.name || x.ts_code }} · 现价 {{ x.price?.toFixed?.(2) }} ← 止损 {{ x.target_loss }}
                  </span>
                </div>
              </div>
              <!-- 盈亏排行 -->
              <div v-if="(summary.watchlist_battle?.winners?.length ?? 0) > 0" class="mt-3">
                <p class="text-xs text-rose-400 font-semibold mb-1.5">🏆 盈利 Top 5</p>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-xs">
                  <div
                    v-for="x in summary.watchlist_battle.winners"
                    :key="`w-${x.ts_code}`"
                    class="flex items-center justify-between px-2 py-1 rounded
                           bg-slate-950/30 hover:bg-slate-800/40 cursor-pointer transition"
                    @click="openChart(x.ts_code, x.name)"
                  >
                    <span class="text-slate-300">{{ x.name || x.ts_code }}</span>
                    <span class="text-rose-400 font-mono font-semibold">
                      +{{ x.floating_pnl?.toLocaleString?.() ?? x.floating_pnl }}
                      <span class="text-slate-500 text-[10px] ml-1">({{ x.return_rate?.toFixed?.(2) }}%)</span>
                    </span>
                  </div>
                </div>
              </div>
              <div v-if="(summary.watchlist_battle?.losers?.length ?? 0) > 0" class="mt-3">
                <p class="text-xs text-emerald-400 font-semibold mb-1.5">📉 亏损 Top 5</p>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-xs">
                  <div
                    v-for="x in summary.watchlist_battle.losers"
                    :key="`l-${x.ts_code}`"
                    class="flex items-center justify-between px-2 py-1 rounded
                           bg-slate-950/30 hover:bg-slate-800/40 cursor-pointer transition"
                    @click="openChart(x.ts_code, x.name)"
                  >
                    <span class="text-slate-300">{{ x.name || x.ts_code }}</span>
                    <span class="text-emerald-400 font-mono font-semibold">
                      {{ x.floating_pnl?.toLocaleString?.() ?? x.floating_pnl }}
                      <span class="text-slate-500 text-[10px] ml-1">({{ x.return_rate?.toFixed?.(2) }}%)</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <!-- ====== 卡片 3: 异动龙头 ====== -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div class="glass p-5">
                <div class="flex items-center gap-2 mb-3">
                  <span class="text-base">🚀</span>
                  <h4 class="text-sm font-semibold text-slate-200 tracking-wide">
                    涨幅榜 Top 3
                  </h4>
                </div>
                <div class="space-y-2">
                  <div
                    v-for="(x, i) in summary.top_movers?.by_change_pct ?? []"
                    :key="`c-${x.code}`"
                    @click="openChart(x.code, x.name)"
                    class="flex items-center justify-between px-3 py-2 rounded
                           bg-slate-950/40 hover:bg-slate-800/40 cursor-pointer transition"
                  >
                    <div class="flex items-center gap-2">
                      <span
                        class="text-xs font-mono w-6 text-center"
                        :class="i === 0 ? 'text-rose-400 font-bold' : 'text-slate-500'"
                      >{{ i + 1 }}</span>
                      <span class="text-slate-200">{{ x.name || x.code }}</span>
                    </div>
                    <span class="text-rose-400 font-mono font-semibold">+{{ x.change_pct }}%</span>
                  </div>
                  <p v-if="!summary.top_movers?.by_change_pct?.length" class="text-xs text-slate-500 text-center py-3">
                    暂无数据
                  </p>
                </div>
              </div>
              <div class="glass p-5">
                <div class="flex items-center gap-2 mb-3">
                  <span class="text-base">💰</span>
                  <h4 class="text-sm font-semibold text-slate-200 tracking-wide">
                    成交榜 Top 3
                  </h4>
                </div>
                <div class="space-y-2">
                  <div
                    v-for="(x, i) in summary.top_movers?.by_volume ?? []"
                    :key="`v-${x.code}`"
                    @click="openChart(x.code, x.name)"
                    class="flex items-center justify-between px-3 py-2 rounded
                           bg-slate-950/40 hover:bg-slate-800/40 cursor-pointer transition"
                  >
                    <div class="flex items-center gap-2">
                      <span
                        class="text-xs font-mono w-6 text-center"
                        :class="i === 0 ? 'text-amber-400 font-bold' : 'text-slate-500'"
                      >{{ i + 1 }}</span>
                      <span class="text-slate-200">{{ x.name || x.code }}</span>
                    </div>
                    <span class="text-amber-400 font-mono text-xs">
                      {{ ((x.volume || 0) / 100000000).toFixed(2) }} 亿股
                    </span>
                  </div>
                  <p v-if="!summary.top_movers?.by_volume?.length" class="text-xs text-slate-500 text-center py-3">
                    暂无数据
                  </p>
                </div>
              </div>
            </div>

            <!-- ====== 卡片 4: AI 深度复盘（v2.4）====== -->
            <div
              class="glass p-5 relative overflow-hidden
                     before:absolute before:inset-0 before:rounded-lg before:p-[1px]
                     before:bg-gradient-to-br before:from-amber-500/30 before:via-purple-500/25 before:to-sky-500/30
                     before:-z-10 before:pointer-events-none"
            >
              <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-2">
                  <span class="text-base">✨</span>
                  <h4 class="text-sm font-semibold text-slate-200 tracking-wide">
                    AI 深度复盘
                  </h4>
                  <span v-if="aiReport?.model" class="text-xs text-slate-500 ml-2 font-mono">
                    via {{ aiReport.model }}
                  </span>
                </div>
                <span v-if="aiReport?.generated_at" class="text-xs text-slate-600 font-mono">
                  {{ aiReport.generated_at }}
                </span>
              </div>

              <!-- Loading -->
              <div v-if="aiLoading" class="py-10 text-center">
                <div class="inline-flex items-center gap-3 text-slate-400">
                  <span class="text-2xl animate-pulse">✨</span>
                  <span class="font-mono text-sm">AI 正在深度思考今日盘面...</span>
                </div>
              </div>

              <!-- Error: 优雅降级（503 没配 key / 502 网络问题） -->
              <div
                v-else-if="aiError"
                class="p-4 rounded border border-amber-500/30 bg-amber-500/5 text-amber-200 text-sm"
              >
                <p class="font-medium mb-1">⚠️ AI 复盘暂不可用</p>
                <p class="text-xs text-amber-300/80 leading-relaxed">{{ aiError }}</p>
                <details class="mt-2 text-xs text-amber-300/60">
                  <summary class="cursor-pointer hover:text-amber-200 transition">配置步骤（点击展开）</summary>
                  <ol class="mt-2 ml-4 list-decimal space-y-1 font-mono">
                    <li>在项目根目录复制 <code class="text-amber-200">.env.example</code> 为 <code class="text-amber-200">.env</code></li>
                    <li>填入你的 LLM_API_KEY（OpenAI / DeepSeek / 通义千问 都行）</li>
                    <li>选好 LLM_BASE_URL 和 LLM_MODEL_NAME</li>
                    <li>重启 <code class="text-amber-200">uvicorn</code> 即可</li>
                  </ol>
                </details>
              </div>

              <!-- Report: Markdown 渲染（自写轻量级 renderer） -->
              <div
                v-else-if="aiReport"
                class="ai-report text-sm"
                v-html="aiReportHtml"
              ></div>

              <!-- Empty state：还没召唤 -->
              <div
                v-else
                class="py-6 text-center text-slate-500 text-sm"
              >
                <p class="mb-2">👆 点右上角「✨ 召唤 AI 深度复盘」</p>
                <p class="text-xs text-slate-600">基于今日大盘 / 自选股 / 异动龙头数据，让大模型写一篇 ~400 字复盘小作文。</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <footer class="mt-6 text-center text-xs text-slate-600 font-mono">
      5s 自动刷新 · 数据：新浪财经 / 腾讯财经 · 支持股票 + ETF · 持仓 / 止盈止损 v2.4
    </footer>

    <!-- ====================== 交易备忘 hover tooltip（v1.1）====================== -->
    <Teleport to="body">
      <div
        v-if="noteTip"
        class="note-tip"
        :style="{ left: noteTip.x + 'px', top: noteTip.y + 'px' }"
        role="tooltip"
      >
        <div class="text-xs text-amber-300/80 mb-1 font-mono uppercase tracking-wider">
          交易逻辑
        </div>
        <div class="text-slate-100 text-sm leading-relaxed whitespace-pre-wrap max-w-xs">
          {{ noteTip.text }}
        </div>
      </div>
    </Teleport>

    <!-- ====================== K 线模态框：毛玻璃 + 渐变边框 ====================== -->
    <Teleport to="body">
      <div
        v-if="chartCode"
        class="fixed inset-0 z-50 flex items-center justify-center p-4
               bg-black/60 backdrop-blur-sm"
        @click.self="closeChart"
      >
        <div
          class="relative bg-slate-900/90 backdrop-blur-md rounded-xl
                 border border-slate-700/60 shadow-2xl
                 w-full max-w-4xl p-5
                 before:absolute before:inset-0 before:rounded-xl before:p-[1px]
                 before:bg-gradient-to-br before:from-sky-500/30 before:via-purple-500/20 before:to-rose-500/30
                 before:-z-10 before:pointer-events-none"
        >
          <div class="flex items-center justify-between mb-3">
            <div>
              <h3 class="text-lg font-semibold text-slate-100">
                {{ chartName }}
                <span class="text-sky-300 font-mono text-sm ml-2">{{ chartCode }}</span>
              </h3>
              <p class="text-xs text-slate-500 mt-0.5 font-mono">
                前复权日 K · 按 Esc 关闭
              </p>
            </div>
            <button
              @click="closeChart"
              class="text-slate-500 hover:text-slate-200 text-2xl leading-none
                     w-8 h-8 flex items-center justify-center rounded
                     hover:bg-slate-800/60 transition"
            >×</button>
          </div>
          <KLineChart
            :key="chartKey"
            :ts-code="chartCode"
            :cost-price="chartCost"
            :target-win="chartTargetWin"
            :target-loss="chartTargetLoss"
          />
        </div>
      </div>
    </Teleport>
  </div>
</template>
