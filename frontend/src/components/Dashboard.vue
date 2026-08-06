<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  addToWatchlist,
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

let pollTimer = null
let clockTimer = null
let fundFlowTimer = null
let radarTimer = null  // v2.2
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
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  window.addEventListener('keydown', onKeyDown)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (fundFlowTimer) clearInterval(fundFlowTimer)
  if (radarTimer) clearInterval(radarTimer)  // v2.2
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

    <footer class="mt-6 text-center text-xs text-slate-600 font-mono">
      5s 自动刷新 · 数据：新浪财经 / 腾讯财经 · 支持股票 + ETF · 持仓 / 止盈止损 v2.2
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
