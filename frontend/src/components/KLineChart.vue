<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import {
  CandlestickSeries,
  HistogramSeries,
  createChart,
} from 'lightweight-charts'
import { getStockHistory } from '../api'

const props = defineProps({
  tsCode: { type: String, required: true },
})

const container = ref(null)
const errorMsg = ref('')
const loading = ref(false)

let chart = null
let mainSeries = null
let volSeries = null
let resizeObserver = null

// ====================== 初始化 ======================
async function loadAndRender() {
  if (!container.value) return
  loading.value = true
  errorMsg.value = ''
  try {
    const data = await getStockHistory(props.tsCode)
    render(data)
  } catch (e) {
    errorMsg.value = e.message
  } finally {
    loading.value = false
  }
}

function render(data) {
  // 销毁旧实例
  if (chart) {
    chart.remove()
    chart = null
  }
  if (!data?.klines?.length) {
    errorMsg.value = '无历史数据'
    return
  }

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

  // ====================== 副图：成交量 ======================
  volSeries = chart.addSeries(HistogramSeries, {
    priceFormat: { type: 'volume' },
    priceScaleId: 'volume',
    color: '#64748b',
  })
  volSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.75, bottom: 0 },  // 上方 75% 给主图，下方 25% 给自己
  })
  volSeries.setData(data.volumes || [])

  // 默认显示最近 30 个交易日
  chart.timeScale().fitContent()
  const lastIdx = data.klines.length
  if (lastIdx > 30) {
    chart.timeScale().setVisibleLogicalRange({ from: lastIdx - 30, to: lastIdx + 2 })
  }
}

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

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (chart) {
    chart.remove()
    chart = null
  }
})
</script>

<template>
  <div class="relative w-full" style="height: 360px;">
    <div v-if="loading" class="absolute inset-0 flex items-center justify-center
                                text-slate-500 text-sm z-10">
      加载中...
    </div>
    <div v-else-if="errorMsg" class="absolute inset-0 flex items-center justify-center
                                      text-rose-400 text-sm z-10">
      {{ errorMsg }}
    </div>
    <div ref="container" class="w-full h-full" />
  </div>
</template>
