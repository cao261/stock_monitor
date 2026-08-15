<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  addToWatchlist,
  aiPlan,
  getAiReport,
  getDailySummary,
  getDiscover,
  getFundFlow,
  getSentiment,
  getSignals,
  getTopMovers,
  getTradeHistory,
  getWatchlist,
  recordTrade,
  refreshHistory,
  removeFromWatchlist,
  updateWatchlist,
} from '../api'

// 子组件引入
import TopNav from './header/TopNav.vue'
import MarketSentimentBar from './market/MarketSentimentBar.vue'
import WatchlistAddForm from './watchlist/WatchlistAddForm.vue'
import WatchlistTable from './watchlist/WatchlistTable.vue'
import MarketRadar from './radar/MarketRadar.vue'
import FundFlowSection from './fundflow/FundFlowSection.vue'

// 模态框引入
import DailySummaryModal from './modals/DailySummaryModal.vue'
import AiReportModal from './modals/AiReportModal.vue'
import AlphaDiscoverModal from './modals/AlphaDiscoverModal.vue'
import AiPlanModal from './modals/AiPlanModal.vue'
import TradeLedgerModal from './modals/TradeLedgerModal.vue'
import TradeExecModal from './modals/TradeExecModal.vue'
import KLineModal from './modals/KLineModal.vue'

// ====================== 常量与定时器配置 ======================
const REFRESH_INTERVAL_MS = 5000
const FUND_FLOW_INTERVAL_MS = 60_000
const RADAR_INTERVAL_MS = 5000
const SUMMARY_CHECK_INTERVAL_MS = 60_000

// ====================== 响应式状态 ======================
const sentiment = ref(null)
const watchlist = ref([])
const signals = ref([])
const fundFlow = ref({ items: [], refreshed_at: null, count: 0 })
const radarGainers = ref([])
const radarVolume = ref([])
const radarLosers = ref([])

const loading = ref(false)
const fundFlowLoading = ref(false)
const radarLoading = ref(false)
const lastUpdated = ref(null)
const globalError = ref('')

// 表单与排序
const showAddForm = ref(false)
const addFormRef = ref(null)
const adding = ref(false)
const addError = ref('')
const sortKey = ref('')
const sortDir = ref('desc')

// 模态框状态
// 1. K线走势
const klineModal = ref({
  show: false,
  code: null,
  name: '',
  cost: null,
  targetWin: null,
  targetLoss: null,
  key: 0,
})

// 2. 今日复盘战报
const summaryModal = ref({
  show: false,
  loading: false,
  data: null,
})

// 3. AI 深度复盘报告
const aiModal = ref({
  show: false,
  loading: false,
  data: null,
  error: '',
})

// 4. Alpha 共振挖掘
const alphaModal = ref({
  show: false,
  loading: false,
  step: 0,
  data: null,
  error: '',
})
let alphaStepTimer = null

// 5. AI 智能规划
const aiPlanModal = ref({
  show: false,
  data: null,
  saving: false,
})

// 6. 资金账本
const ledgerModal = ref({
  show: false,
  loading: false,
  data: { trades: [], total_count: 0, total_realized_pnl: 0 },
})

// 7. 真实交割记账
const tradeExecModal = ref({
  show: false,
  item: null,
  saving: false,
  error: '',
})

// 通知去重追踪
let prevSignalCodes = new Set()
const notifiedEntries = new Set()
const summaryNotifiedDate = ref('')

// ====================== 衍生计算 ======================
const signalMap = computed(() => {
  const m = new Map()
  for (const s of signals.value) m.set(s.ts_code, s)
  return m
})

const watchlistWithSignals = computed(() => {
  return watchlist.value.map((w) => {
    const sig = signalMap.value.get(w.ts_code)
    return {
      ...w,
      signal: sig || null,
      volume_ratio: sig?.volume_ratio ?? null,
    }
  })
})

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

// ====================== 桌面通知机制 ======================
function canNotify() {
  return typeof window !== 'undefined' && 'Notification' in window && Notification.permission === 'granted'
}

async function requestNotifyPermission() {
  if (typeof window !== 'undefined' && 'Notification' in window && Notification.permission !== 'granted') {
    try {
      await Notification.requestPermission()
    } catch (_) {}
  }
}

function notifySignals(freshSignals) {
  if (!canNotify()) return
  for (const s of freshSignals) {
    const title = `⚠️ 异动信号：${s.name || s.ts_code}`
    let body = ''
    if (s.signals?.is_take_profit) body = `🎯 达到止盈线：现价 ¥${s.current?.price}`
    else if (s.signals?.is_stop_loss) body = `🛑 触及止损线：现价 ¥${s.current?.price}`
    else if (s.signals?.is_volume_breakout) body = `📈 放量突破：量比 ${s.volume_ratio} 涨幅 ${s.current?.change_pct}%`
    else if (s.signals?.is_shrinking_pullback) body = `📉 缩量企稳：量比 ${s.volume_ratio}`

    if (body) {
      try {
        new Notification(title, { body, tag: s.ts_code })
      } catch (_) {}
    }
  }
}

function notifyEntry(item) {
  if (!canNotify() || notifiedEntries.has(item.ts_code)) return
  notifiedEntries.add(item.ts_code)
  try {
    new Notification(`🎯 建仓机会：${item.name || item.ts_code}`, {
      body: `现价 ¥${item.price} 已落入理想建仓区间 [${item.entry_price_min}, ${item.entry_price_max}]`,
      tag: `entry-${item.ts_code}`,
    })
  } catch (_) {}
}

// ====================== 核心数据拉取 ======================
async function refreshAll() {
  loading.value = true
  globalError.value = ''
  try {
    const [s, w, sg] = await Promise.all([
      getSentiment(),
      getWatchlist(),
      getSignals(true),
    ])
    sentiment.value = s.data
    watchlist.value = w.data

    const sigs = sg.data || []
    const fresh = sigs.filter(x => !prevSignalCodes.has(x.ts_code))
    if (prevSignalCodes.size > 0 && fresh.length > 0) {
      notifySignals(fresh)
    }
    prevSignalCodes = new Set(sigs.map(x => x.ts_code))
    signals.value = sigs

    // 检查理想建仓提醒
    for (const item of watchlist.value) {
      if (item.is_entry_opportunity) {
        notifyEntry(item)
      }
    }

    lastUpdated.value = Date.now()
  } catch (e) {
    globalError.value = e.message
  } finally {
    loading.value = false
  }
}

async function refreshFundFlowData() {
  fundFlowLoading.value = true
  try {
    const r = await getFundFlow({ limit: 300 })
    fundFlow.value = r.data
  } catch (e) {
    console.warn('fund flow fetch failed:', e)
  } finally {
    fundFlowLoading.value = false
  }
}

async function refreshRadarData() {
  radarLoading.value = true
  try {
    const [g, v, l] = await Promise.all([
      getTopMovers({ sort_by: 'change_pct', limit: 20 }),
      getTopMovers({ sort_by: 'volume', limit: 20 }),
      getTopMovers({ sort_by: 'change_pct', limit: 200 }),
    ])
    radarGainers.value = g.data || []
    radarVolume.value = v.data || []
    radarLosers.value = (l.data || []).slice().reverse().slice(0, 20)
  } catch (e) {
    console.warn('radar fetch failed:', e)
  } finally {
    radarLoading.value = false
  }
}

// ====================== 业务交互 ======================
async function handleAddWatchlist(payload) {
  adding.value = true
  addError.value = ''
  try {
    await addToWatchlist(payload)
    showAddForm.value = false
    addFormRef.value?.resetForm()
    await refreshAll()
    refreshHistory().catch(() => {})
  } catch (e) {
    addError.value = e.message
  } finally {
    adding.value = false
  }
}

async function handleUpdateWatchlist({ id, payload }) {
  try {
    await updateWatchlist(id, payload)
    await refreshAll()
  } catch (e) {
    globalError.value = `更新失败: ${e.message}`
  }
}

async function handleDeleteWatchlist(id) {
  if (!confirm('确认将此标的从自选监控中移除？')) return
  try {
    await removeFromWatchlist(id)
    await refreshAll()
  } catch (e) {
    globalError.value = `删除失败: ${e.message}`
  }
}

// 模态框打开与联动
function openChartModal(item) {
  const code = item?.ts_code || item?.code
  if (!code) return
  klineModal.value = {
    show: true,
    code,
    name: item.name || '',
    cost: item.cost_price != null ? Number(item.cost_price) : null,
    targetWin: item.target_win != null ? Number(item.target_win) : (item.eff_target_win != null ? Number(item.eff_target_win) : null),
    targetLoss: item.target_loss != null ? Number(item.target_loss) : (item.eff_target_loss != null ? Number(item.eff_target_loss) : null),
    key: Date.now(),
  }
}

async function openDailySummaryModal() {
  summaryModal.value.show = true
  summaryModal.value.loading = true
  try {
    const r = await getDailySummary()
    summaryModal.value.data = r.data
  } catch (e) {
    globalError.value = `战报加载失败: ${e.message}`
  } finally {
    summaryModal.value.loading = false
  }
}

async function triggerAiReport() {
  aiModal.value.show = true
  aiModal.value.loading = true
  aiModal.value.error = ''
  try {
    const r = await getAiReport()
    aiModal.value.data = r.data
  } catch (e) {
    aiModal.value.error = e.message
  } finally {
    aiModal.value.loading = false
  }
}

async function openAlphaDiscoverModal() {
  alphaModal.value.show = true
  alphaModal.value.loading = true
  alphaModal.value.error = ''
  alphaModal.value.step = 0

  if (alphaStepTimer) clearInterval(alphaStepTimer)
  alphaStepTimer = setInterval(() => {
    alphaModal.value.step = (alphaModal.value.step + 1) % 3
  }, 2500)

  try {
    const r = await getDiscover()
    alphaModal.value.data = r.data
  } catch (e) {
    alphaModal.value.error = e.message
  } finally {
    alphaModal.value.loading = false
    if (alphaStepTimer) clearInterval(alphaStepTimer)
  }
}

async function openAiPlanModal(item) {
  aiPlanModal.value.show = true
  aiPlanModal.value.data = null
  aiPlanModal.value.saving = true
  try {
    const r = await aiPlan(item.id)
    aiPlanModal.value.data = r.data
  } catch (e) {
    globalError.value = `AI规划失败: ${e.message}`
    aiPlanModal.value.show = false
  } finally {
    aiPlanModal.value.saving = false
  }
}

async function handleApplyAiPlan(payload) {
  if (!aiPlanModal.value.data?.stock_id) return
  aiPlanModal.value.saving = true
  try {
    await updateWatchlist(aiPlanModal.value.data.stock_id, payload)
    aiPlanModal.value.show = false
    await refreshAll()
  } catch (e) {
    globalError.value = `应用计划失败: ${e.message}`
  } finally {
    aiPlanModal.value.saving = false
  }
}

async function openTradeLedgerModal(tsCode = undefined) {
  ledgerModal.value.show = true
  ledgerModal.value.loading = true
  try {
    const r = await getTradeHistory({ ts_code: tsCode, limit: 300 })
    ledgerModal.value.data = r.data
  } catch (e) {
    globalError.value = `账本加载失败: ${e.message}`
  } finally {
    ledgerModal.value.loading = false
  }
}

function openTradeExecModal(item) {
  tradeExecModal.value = {
    show: true,
    item,
    saving: false,
    error: '',
  }
}

async function handleConfirmTradeExec({ id, price, volume }) {
  tradeExecModal.value.saving = true
  tradeExecModal.value.error = ''
  try {
    await recordTrade(id, { price, volume })
    tradeExecModal.value.show = false
    await refreshAll()
  } catch (e) {
    tradeExecModal.value.error = e.message
  } finally {
    tradeExecModal.value.saving = false
  }
}

// 自动触发收盘战报
function checkAutoSummary() {
  const d = new Date()
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  if (summaryNotifiedDate.value === today) return
  if (d.getHours() < 15) return
  const day = d.getDay()
  if (day === 0 || day === 6) return

  summaryNotifiedDate.value = today
  try { localStorage.setItem('summary_notified_date', today) } catch (_) {}
  openDailySummaryModal()
}

// ====================== 生命周期 ======================
let pollTimer = null
let fundFlowTimer = null
let radarTimer = null
let summaryTimer = null

onMounted(() => {
  requestNotifyPermission()
  refreshAll()
  refreshFundFlowData()
  refreshRadarData()

  pollTimer = setInterval(refreshAll, REFRESH_INTERVAL_MS)
  fundFlowTimer = setInterval(refreshFundFlowData, FUND_FLOW_INTERVAL_MS)
  radarTimer = setInterval(refreshRadarData, RADAR_INTERVAL_MS)
  summaryTimer = setInterval(checkAutoSummary, SUMMARY_CHECK_INTERVAL_MS)

  try {
    summaryNotifiedDate.value = localStorage.getItem('summary_notified_date') || ''
  } catch (_) {}
  checkAutoSummary()
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (fundFlowTimer) clearInterval(fundFlowTimer)
  if (radarTimer) clearInterval(radarTimer)
  if (summaryTimer) clearInterval(summaryTimer)
  if (alphaStepTimer) clearInterval(alphaStepTimer)
})
</script>

<template>
  <div class="min-h-screen p-3 md:p-5 max-w-[1440px] mx-auto text-slate-100">
    <!-- 顶部导航栏 -->
    <TopNav
      :last-updated="lastUpdated"
      :loading="loading"
      :can-notify="canNotify()"
      :sentiment="sentiment"
      @refresh="refreshAll"
      @open-summary="openDailySummaryModal"
      @open-alpha="openAlphaDiscoverModal"
      @open-ledger="openTradeLedgerModal()"
      @toggle-add-form="showAddForm = !showAddForm"
      @request-notify="requestNotifyPermission"
    />

    <!-- 全局错误条 -->
    <div 
      v-if="globalError" 
      class="mb-4 p-3 rounded bg-red-950/80 border border-red-500/50 text-red-200 text-xs flex items-center justify-between"
    >
      <span>⚠️ {{ globalError }}</span>
      <button @click="globalError = ''" class="text-red-400 hover:text-white px-2">✕</button>
    </div>

    <!-- 添加自选表单 (展开/折叠) -->
    <WatchlistAddForm
      v-show="showAddForm"
      ref="addFormRef"
      :adding="adding"
      :error="addError"
      @add="handleAddWatchlist"
      @close="showAddForm = false"
    />

    <!-- 大盘全景情绪指标 -->
    <MarketSentimentBar :sentiment="sentiment" />

    <!-- 核心自选与持仓监控网格 -->
    <WatchlistTable
      :items="sortedWatchlist"
      :sort-key="sortKey"
      :sort-dir="sortDir"
      @toggle-sort="toggleSort"
      @open-chart="openChartModal"
      @open-ai-plan="openAiPlanModal"
      @open-trade-exec="openTradeExecModal"
      @update-item="handleUpdateWatchlist"
      @delete-item="handleDeleteWatchlist"
    />

    <!-- 市场异动雷达 -->
    <MarketRadar
      :gainers="radarGainers"
      :volume-leaders="radarVolume"
      :losers="radarLosers"
      :loading="radarLoading"
      @open-chart="openChartModal"
    />

    <!-- 概念板块资金流向 -->
    <FundFlowSection
      :fund-flow="fundFlow"
      :loading="fundFlowLoading"
    />

    <!-- 模态框体系 -->
    <!-- 1. K线图走势 -->
    <KLineModal
      :show="klineModal.show"
      :chart-code="klineModal.code"
      :chart-name="klineModal.name"
      :chart-cost="klineModal.cost"
      :chart-target-win="klineModal.targetWin"
      :chart-target-loss="klineModal.targetLoss"
      :chart-key="klineModal.key"
      @close="klineModal.show = false"
    />

    <!-- 2. 今日复盘战报 -->
    <DailySummaryModal
      :show="summaryModal.show"
      :summary="summaryModal.data"
      :loading="summaryModal.loading"
      :ai-loading="aiModal.loading"
      :ai-error="aiModal.error"
      @close="summaryModal.show = false"
      @trigger-ai="triggerAiReport"
      @open-chart="openChartModal"
    />

    <!-- 3. AI 深度复盘报告 -->
    <AiReportModal
      :show="aiModal.show"
      :report="aiModal.data"
      @close="aiModal.show = false"
    />

    <!-- 4. Alpha 共振挖掘 -->
    <AlphaDiscoverModal
      :show="alphaModal.show"
      :loading="alphaModal.loading"
      :loading-step="alphaModal.step"
      :result="alphaModal.data"
      :error="alphaModal.error"
      @close="alphaModal.show = false"
      @refresh="openAlphaDiscoverModal"
      @open-chart="openChartModal"
    />

    <!-- 5. AI 智能规划确认 -->
    <AiPlanModal
      :show="aiPlanModal.show"
      :plan-data="aiPlanModal.data"
      :saving="aiPlanModal.saving"
      @close="aiPlanModal.show = false"
      @apply="handleApplyAiPlan"
    />

    <!-- 6. 资金账本与历史交割单 -->
    <TradeLedgerModal
      :show="ledgerModal.show"
      :ledger-data="ledgerModal.data"
      :loading="ledgerModal.loading"
      @close="ledgerModal.show = false"
      @filter-change="openTradeLedgerModal"
    />

    <!-- 7. 真实交割记账 -->
    <TradeExecModal
      :show="tradeExecModal.show"
      :item="tradeExecModal.item"
      :saving="tradeExecModal.saving"
      :error="tradeExecModal.error"
      @close="tradeExecModal.show = false"
      @confirm="handleConfirmTradeExec"
    />
  </div>
</template>
