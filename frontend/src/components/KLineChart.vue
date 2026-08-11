<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
} from 'lightweight-charts'
import { getStockHistory } from '../api'

const props = defineProps({
  tsCode: { type: String, required: true },
  // v2.1: 持仓 / 止盈止损水平参考线（K 线上画线）
  costPrice: { type: Number, default: null },
  targetWin: { type: Number, default: null },
  targetLoss: { type: Number, default: null },
})

const container = ref(null)
const errorMsg = ref('')
const loading = ref(false)

// ====================== 十字光标 Legend ======================
const hover = ref(null)        // 当前十字光标指向的 K 线数据
const showHover = ref(false)   // 是否显示 legend

// MA 颜色：白 / 黄 / 紫
const MA_COLORS = {
  ma5: '#ffffff',
  ma10: '#facc15',
  ma20: '#c084fc',
}

let chart = null
let mainSeries = null
let volSeries = null
let ma5Series = null
let ma10Series = null
let ma20Series = null
let resizeObserver = null
let klinesData = []  // 保存原始 klines 用于 legend 计算

// v2.1: price line 句柄，用于 props 变化时移除旧线
let costLine = null
let winLine = null
let lossLine = null

// ====================== 计算 MA ======================
function calcMA(closes, period) {
  const out = []
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      out.push({ time: null, value: null })
      continue
    }
    let sum = 0
    for (let j = i - period + 1; j <= i; j++) sum += closes[j]
    out.push({ time: closes[i].time, value: sum / period })
  }
  // 过滤掉 null（lightweight-charts 喜欢连续点，但允许少量 null）
  return out.filter(p => p.value != null)
}

// ====================== 持仓 / 止盈止损参考线（v2.1）======================
function clearPriceLines() {
  // 移除旧的参考线（props 变化或重渲染时调用）
  for (const ln of [costLine, winLine, lossLine]) {
    if (ln && mainSeries) {
      try { mainSeries.removePriceLine(ln) } catch (_) { /* 已被 chart 释放 */ }
    }
  }
  costLine = null
  winLine = null
  lossLine = null
}

function renderPriceLines() {
  if (!mainSeries) return
  clearPriceLines()
  const { costPrice, targetWin, targetLoss } = props
  // 成本价（中性色 + 百分比 Label）
  if (costPrice != null && costPrice > 0) {
    costLine = mainSeries.createPriceLine({
      price: costPrice,
      color: '#94a3b8',         // slate-400
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: '成本',
    })
  }
  // 止盈价（红 + 相对成本幅度）
  if (targetWin != null && targetWin > 0) {
    let title = '止盈'
    if (costPrice > 0) {
      const pct = ((targetWin - costPrice) / costPrice) * 100
      title = `止盈 ${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
    }
    winLine = mainSeries.createPriceLine({
      price: targetWin,
      color: '#ef4444',         // 涨红
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title,
    })
  }
  // 止损价（绿 + 相对成本幅度）
  if (targetLoss != null && targetLoss > 0) {
    let title = '止损'
    if (costPrice > 0) {
      const pct = ((targetLoss - costPrice) / costPrice) * 100
      title = `止损 ${pct.toFixed(1)}%`
    }
    lossLine = mainSeries.createPriceLine({
      price: targetLoss,
      color: '#22c55e',         // 跌绿
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title,
    })
  }
}

// ====================== 初始化 ======================
async function loadAndRender() {
  if (!container.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const r = await getStockHistory(props.tsCode)
    // v2.4.7: axios 拦截器 return resp（不解包），所以 r.data 才是真正的 klines
    render(r.data)
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    loading.value = false
  }
}

function render(data) {
  // 销毁旧实例
  if (chart) {
    clearPriceLines()  // v2.1: 显式清旧 line 句柄
    chart.remove()
    chart = null
  }
  if (!data?.klines?.length) {
    errorMsg.value = '无历史数据'
    return
  }
  klinesData = data.klines

  // ====================== 创建图表 ======================
  chart = createChart(container.value, {
    autoSize: true,
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#94a3b8',  // slate-400
      fontSize: 11,
    },
    grid: {
      vertLines: { color: 'rgba(51,65,85,0.4)' },
      horzLines: { color: 'rgba(51,65,85,0.4)' },
    },
    timeScale: {
      timeVisible: false,
      borderColor: '#334155',
      rightOffset: 4,
      barSpacing: 8,
    },
    rightPriceScale: {
      borderColor: '#334155',
    },
    crosshair: {
      mode: 1,
      vertLine: { color: '#475569', width: 1, style: 3 },
      horzLine: { color: '#475569', width: 1, style: 3 },
    },
    // 隐藏 TradingView 水印
    watermark: { visible: false },
  })

  // ====================== 主图：K 线 ======================
  mainSeries = chart.addSeries(CandlestickSeries, {
    upColor: '#ef4444',          // 涨红
    downColor: '#22c55e',        // 跌绿
    borderUpColor: '#ef4444',
    borderDownColor: '#22c55e',
    wickUpColor: '#ef4444',
    wickDownColor: '#22c55e',
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  })
  mainSeries.setData(data.klines)

  // 顶部 5% 留白，下方 30% 给成交量
  mainSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.05, bottom: 0.30 },
  })

  // ====================== MA 均线（白/黄/紫）======================
  // 注意：MA 系列必须挂在主图价格 scale 上（默认行为）
  const closes = data.klines.map(k => ({ time: k.time, value: k.close }))
  ma5Series = chart.addSeries(LineSeries, {
    color: MA_COLORS.ma5,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  })
  ma5Series.setData(calcMA(closes, 5))

  ma10Series = chart.addSeries(LineSeries, {
    color: MA_COLORS.ma10,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  })
  ma10Series.setData(calcMA(closes, 10))

  ma20Series = chart.addSeries(LineSeries, {
    color: MA_COLORS.ma20,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
    crosshairMarkerVisible: false,
  })
  ma20Series.setData(calcMA(closes, 20))

  // ====================== 副图：成交量 ======================
  volSeries = chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
    color: '#64748b',
  })
  volSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.75, bottom: 0 },  // 上方 75% 给主图，下方 25% 给成交量
  })
  volSeries.setData(data.volumes || [])

  // 默认显示最近 60 个交易日
  chart.timeScale().fitContent()
  const lastIdx = data.klines.length
  if (lastIdx > 60) {
    chart.timeScale().setVisibleLogicalRange({ from: lastIdx - 60, to: lastIdx + 2 })
  }

  // v2.1: 持仓 / 止盈止损水平参考线（K 线画好了再画，不然找不到 mainSeries）
  renderPriceLines()

  // ====================== 十字光标 Legend 联动 ======================
  chart.subscribeCrosshairMove(param => {
    if (!param.time || param.point == null) {
      showHover.value = false
      return
    }
    // 找到十字光标对应的 K 线
    const kline = klinesData.find(k => k.time === param.time)
    if (!kline) {
      showHover.value = false
      return
    }
    // 计算 MA 值（用 kline 在 closes 里的索引）
    const idx = klinesData.indexOf(kline)
    const closes = klinesData.map(k => k.close)
    const maVal = (period) => {
      if (idx < period - 1) return null
      let s = 0
      for (let j = idx - period + 1; j <= idx; j++) s += closes[j]
      return s / period
    }
    const prevClose = idx > 0 ? klinesData[idx - 1].close : kline.open
    const chg = kline.close - prevClose
    const chgPct = prevClose > 0 ? (chg / prevClose) * 100 : 0
    hover.value = {
      time: kline.time,
      open: kline.open,
      high: kline.high,
      low: kline.low,
      close: kline.close,
      chg,
      chgPct,
      ma5: maVal(5),
      ma10: maVal(10),
      ma20: maVal(20),
    }
    showHover.value = true
  })
}

// ====================== Legend 文本格式化 ======================
const hoverText = computed(() => {
  if (!hover.value) return null
  const h = hover.value
  const fmt = (v) => v == null ? '-' : v.toFixed(2)
  const sign = (v) => (v > 0 ? '+' : '')
  return {
    date: h.time,
    open: h.open,
    high: h.high,
    low: h.low,
    close: h.close,
    chg: h.chg,
    chgPct: h.chgPct,
    ma5: h.ma5,
    ma10: h.ma10,
    ma20: h.ma20,
    fmt,
    sign,
    chgClass: h.chg >= 0 ? 'text-rose-400' : 'text-emerald-400',
  }
})

// ====================== resize 自适应 ======================
function handleResize() {
  if (chart && container.value) {
    chart.applyOptions({
      width: container.value.clientWidth,
      height: container.value.clientHeight,
    })
  }
}

// ====================== 生命周期 ======================
onMounted(() => {
  loadAndRender()
  // ResizeObserver 处理容器尺寸变化
  if (typeof ResizeObserver !== 'undefined' && container.value) {
    resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(container.value)
  }
  // 兜底：window resize
  window.addEventListener('resize', handleResize)
})

watch(() => props.tsCode, () => {
  loadAndRender()
})

// v2.1: 持仓 / 止盈止损值变了，不需要重拉历史，只重画参考线
watch(
  () => [props.costPrice, props.targetWin, props.targetLoss],
  () => {
    renderPriceLines()
  },
  { deep: true },
)

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  // v2.1: 先释放 price line 句柄（chart.remove() 也会清，但显式清更稳）
  clearPriceLines()
  mainSeries = null
  if (chart) {
    chart.remove()
    chart = null
  }
})
</script>

<template>
  <div class="relative w-full" style="height: 420px;">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center
                                text-slate-500 text-sm z-10">
      加载中...
    </div>
    <div v-else-if="errorMsg" class="absolute inset-0 flex items-center justify-center
                                      text-rose-400 text-sm z-10">
      {{ errorMsg }}
    </div>
    <div ref="container" class="w-full h-full" />

    <!-- ====== 十字光标 Legend（左上角悬浮）====== -->
    <div
      v-if="showHover && hoverText"
      class="absolute top-2 left-2 z-20 glass px-3 py-2 text-xs
             font-mono leading-relaxed pointer-events-none max-w-md"
    >
      <div class="text-slate-300 mb-1">{{ hoverText.date }}</div>
      <div class="text-slate-200">
        开 {{ hoverText.fmt(hoverText.open) }}
        高 {{ hoverText.fmt(hoverText.high) }}
        低 {{ hoverText.fmt(hoverText.low) }}
        收 {{ hoverText.fmt(hoverText.close) }}
      </div>
      <div :class="hoverText.chgClass">
        {{ hoverText.sign(hoverText.chg) }}{{ hoverText.chg.toFixed(2) }}
        ({{ hoverText.sign(hoverText.chgPct) }}{{ hoverText.chgPct.toFixed(2) }}%)
      </div>
      <div class="flex gap-3 mt-0.5">
        <span :style="{ color: MA_COLORS.ma5 }">MA5 {{ hoverText.fmt(hoverText.ma5) }}</span>
        <span :style="{ color: MA_COLORS.ma10 }">MA10 {{ hoverText.fmt(hoverText.ma10) }}</span>
        <span :style="{ color: MA_COLORS.ma20 }">MA20 {{ hoverText.fmt(hoverText.ma20) }}</span>
      </div>
    </div>
  </div>
</template>
