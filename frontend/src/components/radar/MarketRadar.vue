<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  gainers: { type: Array, default: () => [] },
  volumeLeaders: { type: Array, default: () => [] },
  losers: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['open-chart'])

const activeTab = ref('gainers') // 'gainers' | 'volume' | 'losers'

const currentList = computed(() => {
  if (activeTab.value === 'gainers') return props.gainers || []
  if (activeTab.value === 'volume') return props.volumeLeaders || []
  return props.losers || []
})

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}

function fmtVolume(v) {
  if (v == null || isNaN(v)) return '--'
  const num = Number(v)
  if (num >= 1e8) return (num / 1e8).toFixed(2) + '亿股'
  if (num >= 1e4) return (num / 1e4).toFixed(0) + '万股'
  return num.toLocaleString()
}
</script>

<template>
  <div class="rpt-panel mb-4 overflow-hidden">
    <!-- 头部 Tabs 切换 -->
    <div class="p-2.5 bg-[#131c2e] border-b border-slate-700/80 flex items-center justify-between flex-wrap gap-2">
      <div class="flex items-center gap-1.5">
        <span class="text-xs font-bold text-slate-200 mr-2 flex items-center gap-1">
          🔥 市场异动雷达
        </span>
        <button
          @click="activeTab = 'gainers'"
          class="rpt-btn rpt-btn-sm"
          :class="activeTab === 'gainers' ? 'bg-red-950/70 border-red-500/60 text-red-200 font-semibold' : 'text-slate-400'"
        >
          🚀 涨幅榜
        </button>
        <button
          @click="activeTab = 'volume'"
          class="rpt-btn rpt-btn-sm"
          :class="activeTab === 'volume' ? 'bg-amber-950/70 border-amber-500/60 text-amber-200 font-semibold' : 'text-slate-400'"
        >
          💰 成交量榜
        </button>
        <button
          @click="activeTab = 'losers'"
          class="rpt-btn rpt-btn-sm"
          :class="activeTab === 'losers' ? 'bg-emerald-950/70 border-emerald-500/60 text-emerald-200 font-semibold' : 'text-slate-400'"
        >
          🔻 跌幅榜
        </button>
      </div>

      <span v-if="props.loading" class="text-[11px] text-blue-400 font-mono">
        同步雷达中…
      </span>
    </div>

    <!-- 紧凑网格展示 (5列自适应卡片) -->
    <div class="p-3">
      <div v-if="currentList.length === 0" class="text-center py-6 text-slate-500 text-xs">
        暂无异动排行数据
      </div>

      <div v-else class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
        <div
          v-for="(item, idx) in currentList.slice(0, 20)"
          :key="item.code"
          @click="emit('open-chart', { ts_code: item.code, name: item.name })"
          class="rpt-panel-sub p-2 cursor-pointer hover:border-blue-500/60 transition group flex flex-col justify-between"
          :class="idx < 3 ? 'border-slate-600 bg-slate-800/40' : ''"
        >
          <div class="flex items-center justify-between text-xs mb-1">
            <span class="font-mono text-[11px] text-slate-400 flex items-center gap-1">
              <span 
                class="inline-block w-4 text-center rounded text-[10px] font-bold"
                :class="idx === 0 ? 'bg-red-600 text-white' : (idx === 1 ? 'bg-orange-600 text-white' : (idx === 2 ? 'bg-amber-600 text-white' : 'text-slate-500'))"
              >
                {{ idx + 1 }}
              </span>
              {{ item.code }}
            </span>
            <span class="font-semibold text-slate-200 group-hover:text-blue-300 truncate max-w-[70px]">
              {{ item.name }}
            </span>
          </div>

          <div class="flex items-center justify-between font-mono text-xs mt-0.5">
            <span class="text-slate-300 font-medium">{{ fmtPrice(item.price) }}</span>
            <span 
              class="font-bold px-1 rounded text-[11px]"
              :class="item.change_pct > 0 ? 'text-up bg-up-subtle' : (item.change_pct < 0 ? 'text-down bg-down-subtle' : 'text-slate-400')"
            >
              {{ fmtPct(item.change_pct) }}
            </span>
          </div>

          <div v-if="activeTab === 'volume'" class="text-[10px] text-slate-500 font-mono text-right mt-1">
            量: {{ fmtVolume(item.volume) }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
