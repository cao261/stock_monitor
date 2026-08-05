<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  addToWatchlist,
  getFundFlow,
  getSentiment,
  getSignals,
  getWatchlist,
  refreshHistory,
  removeFromWatchlist,
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

// 表单
const newCode = ref('')
const newName = ref('')
const adding = ref(false)
const addError = ref('')

// 排序
const sortKey = ref('')
const sortDir = ref('desc')

// K 线模态框
const chartCode = ref(null)
const chartName = ref('')

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
function fmtTimeAgo(ts) {
  if (!ts) return '-'
  const s = Math.floor((now.value - ts) / 1000)
  if (s < 5) return '刚刚'
  if (s < 60) return `${s} 秒前`
  return `${Math.floor(s / 60)} 分钟前`
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

async function onAdd() {
  const code = newCode.value.trim().toLowerCase()
  if (!code) { addError.value = '请输入股票代码'; return }
  if (!/^(sh|sz|bj)\d{6}$/.test(code)) {
    addError.value = '代码格式错误（示例：sh600000 / sh510300）'
    return
  }
  adding.value = true
  addError.value = ''
  try {
    const exchange = code.startsWith('sh') ? 'SH' : code.startsWith('sz') ? 'SZ' : 'BJ'
    await addToWatchlist({
      ts_code: code,
      name: newName.value.trim() || undefined,
      exchange,
    })
    await refreshHistory()
    newCode.value = ''
    newName.value = ''
    await refresh()
  } catch (e) {
    addError.value = e.message
  } finally {
    adding.value = false
  }
}

async function onRemove(id) {
  try {
    await removeFromWatchlist(id)
    await refresh()
  } catch (e) {
    error.value = e.message
  }
}

function openChart(code, name) {
  chartCode.value = code
  chartName.value = name || code
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
    const kind = s.signals.is_volume_breakout
      ? '放量突破'
      : s.signals.is_shrinking_pullback ? '缩量企稳' : '异动'
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
onMounted(() => {
  requestNotifyPermission()
  refresh()
  refreshFundFlow()  // 立即拉一次
  pollTimer = setInterval(refresh, REFRESH_INTERVAL_MS)
  fundFlowTimer = setInterval(refreshFundFlow, FUND_FLOW_INTERVAL_MS)
  clockTimer = setInterval(() => { now.value = Date.now() }, 1000)
  window.addEventListener('keydown', onKeyDown)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (clockTimer) clearInterval(clockTimer)
  if (fundFlowTimer) clearInterval(fundFlowTimer)
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

    <!-- ====================== 添加表单 ====================== -->
    <section class="glass p-5 mb-6">
      <form @submit.prevent="onAdd" class="flex flex-wrap items-end gap-3">
        <div class="flex-1 min-w-[160px]">
          <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
            股票 / ETF 代码
          </label>
          <input
            v-model="newCode"
            type="text"
            placeholder="sh600000 / sh510300"
            class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                   text-slate-100 placeholder-slate-600 focus:outline-none
                   focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                   font-mono text-sm transition"
          />
        </div>
        <div class="flex-1 min-w-[140px]">
          <label class="block text-xs text-slate-400 mb-1.5 tracking-wider">
            名称（可选）
          </label>
          <input
            v-model="newName"
            type="text"
            placeholder="浦发银行 / 沪深300ETF"
            class="w-full px-3 py-2 bg-slate-950/50 border border-slate-700/60 rounded
                   text-slate-100 placeholder-slate-600 focus:outline-none
                   focus:border-sky-500/70 focus:ring-1 focus:ring-sky-500/50
                   text-sm transition"
          />
        </div>
        <button
          type="submit"
          :disabled="adding"
          class="px-5 py-2 bg-emerald-600/90 hover:bg-emerald-500
                 disabled:bg-slate-700 disabled:text-slate-500
                 text-white rounded font-medium text-sm transition
                 shadow-lg shadow-emerald-900/30"
        >{{ adding ? '添加中…' : '+ 添加' }}</button>
        <p v-if="addError" class="basis-full text-rose-400 text-xs mt-1 font-mono">
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
                  @click="openChart(watchlist[0]?.ts_code, watchlist[0]?.name)"
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
              <th class="text-right py-3 px-4 font-medium">成交量</th>
              <th
                class="text-right py-3 px-4 font-medium cursor-pointer select-none
                       hover:text-slate-200 transition"
                :class="sortKey === 'volume_ratio' ? 'text-sky-400' : ''"
                @click="toggleSort('volume_ratio')"
              >
                量比 <span class="text-xs ml-0.5">{{ sortIndicator('volume_ratio') }}</span>
              </th>
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
                @click="openChart(w.ts_code, w.name || w.name_from_market)"
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
              <td class="py-3 px-4">
                <span v-if="w.signal?.signals?.is_volume_breakout" class="badge badge-breakout">
                  🔥 放量突破
                </span>
                <span v-else-if="w.signal?.signals?.is_shrinking_pullback" class="badge badge-shrinking">
                  🟢 缩量企稳
                </span>
                <span v-else class="text-slate-600 text-xs">—</span>
              </td>
              <td class="py-3 px-4 text-right whitespace-nowrap">
                <button
                  @click="openChart(w.ts_code, w.name || w.name_from_market)"
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
      </div>
    </section>

    <footer class="mt-6 text-center text-xs text-slate-600 font-mono">
      5s 自动刷新 · 数据：新浪财经 / 腾讯财经 · 支持股票 + ETF
    </footer>

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
          <KLineChart :ts-code="chartCode" />
        </div>
      </div>
    </Teleport>
  </div>
</template>
