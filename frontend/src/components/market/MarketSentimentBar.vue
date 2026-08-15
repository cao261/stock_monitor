<script setup>
import { computed } from 'vue'

const props = defineProps({
  sentiment: { type: Object, default: null },
})

const score = computed(() => props.sentiment?.score ?? 50)
const upCount = computed(() => props.sentiment?.up_count ?? 0)
const downCount = computed(() => props.sentiment?.down_count ?? 0)
const flatCount = computed(() => props.sentiment?.flat_count ?? 0)
const limitUp = computed(() => props.sentiment?.limit_up_count ?? 0)
const limitDown = computed(() => props.sentiment?.limit_down_count ?? 0)
const total = computed(() => props.sentiment?.total_stocks ?? (upCount.value + downCount.value + flatCount.value))

const upRatioPct = computed(() => {
  if (props.sentiment?.up_ratio != null) {
    return (props.sentiment.up_ratio * 100).toFixed(1)
  }
  const decided = upCount.value + downCount.value
  return decided > 0 ? ((upCount.value / decided) * 100).toFixed(1) : '50.0'
})

const upWidth = computed(() => {
  if (!total.value) return 50
  return Math.min(100, Math.max(0, (upCount.value / total.value) * 100))
})
const flatWidth = computed(() => {
  if (!total.value) return 0
  return Math.min(100, Math.max(0, (flatCount.value / total.value) * 100))
})
const downWidth = computed(() => {
  if (!total.value) return 50
  return Math.min(100, Math.max(0, (downCount.value / total.value) * 100))
})

const sentimentLevel = computed(() => {
  const s = score.value
  if (s >= 70) return { label: '强势做多', class: 'text-up bg-up-subtle', dialColor: '#f04438' }
  if (s >= 55) return { label: '偏强震荡', class: 'text-up bg-up-subtle', dialColor: '#f87171' }
  if (s >= 45) return { label: '中性平衡', class: 'text-slate-300 bg-slate-800/80 border border-slate-700', dialColor: '#94a3b8' }
  if (s >= 30) return { label: '偏弱震荡', class: 'text-down bg-down-subtle', dialColor: '#4ade80' }
  return { label: '极弱恐慌', class: 'text-down bg-down-subtle', dialColor: '#10b981' }
})
</script>

<template>
  <div class="rpt-panel p-4 mb-4">
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4 items-center">
      
      <!-- 模块1：大盘情绪指数 -->
      <div class="flex items-center gap-3.5 border-b md:border-b-0 md:border-r border-slate-800 pb-3 md:pb-0 pr-0 md:pr-4">
        <div class="text-center min-w-[70px]">
          <div class="text-[11px] text-slate-400 font-semibold mb-0.5 tracking-wider">市场情绪指数</div>
          <div class="text-3xl font-extrabold font-mono" :style="{ color: sentimentLevel.dialColor }">
            {{ Number(score).toFixed(1) }}
          </div>
        </div>
        <div class="flex flex-col gap-1.5 flex-1">
          <div class="flex items-center justify-between text-xs">
            <span class="rpt-badge font-semibold" :class="sentimentLevel.class">
              {{ sentimentLevel.label }}
            </span>
            <span class="text-[11px] font-mono text-slate-500">
              跟踪全市场 {{ total.toLocaleString() }} 支
            </span>
          </div>
          <!-- 情绪仪表槽条 -->
          <div class="w-full bg-slate-800/80 h-2 rounded-full overflow-hidden border border-slate-700/60 flex">
            <div 
              class="h-full transition-all duration-500"
              :style="{ 
                width: `${score}%`, 
                backgroundColor: sentimentLevel.dialColor 
              }"
            ></div>
          </div>
        </div>
      </div>

      <!-- 模块2：涨跌分布与比例条 -->
      <div class="md:col-span-2 flex flex-col justify-center border-b md:border-b-0 md:border-r border-slate-800 pb-3 md:pb-0 pr-0 md:pr-4">
        <div class="flex items-center justify-between text-xs font-mono mb-1.5">
          <span class="text-up font-semibold flex items-center gap-1">
            ▲ 上涨 {{ upCount.toLocaleString() }}
          </span>
          <span class="text-slate-400">
            平盘 {{ flatCount.toLocaleString() }}
          </span>
          <span class="text-down font-semibold flex items-center gap-1">
            ▼ 下跌 {{ downCount.toLocaleString() }}
          </span>
          <span class="text-slate-300 font-bold bg-slate-800 px-1.5 py-0.5 rounded border border-slate-700 text-[11px]">
            上涨占比 {{ upRatioPct }}%
          </span>
        </div>

        <!-- 涨跌分布比例尺 -->
        <div class="w-full h-3 rounded bg-slate-900 overflow-hidden flex border border-slate-700/80">
          <div 
            class="h-full bg-red-600/90 transition-all duration-300"
            :style="{ width: `${upWidth}%` }"
            :title="`上涨 ${upCount} 支 (${upWidth.toFixed(1)}%)`"
          ></div>
          <div 
            class="h-full bg-slate-600 transition-all duration-300"
            :style="{ width: `${flatWidth}%` }"
            :title="`平盘 ${flatCount} 支`"
          ></div>
          <div 
            class="h-full bg-emerald-600/90 transition-all duration-300"
            :style="{ width: `${downWidth}%` }"
            :title="`下跌 ${downCount} 支 (${downWidth.toFixed(1)}%)`"
          ></div>
        </div>
      </div>

      <!-- 模块3：极端涨跌停对比 -->
      <div class="flex items-center justify-around text-xs">
        <div class="text-center">
          <div class="text-slate-400 text-[11px] mb-1 font-medium">涨停板</div>
          <div class="text-xl font-bold font-mono text-up bg-up-subtle px-2.5 py-0.5 rounded inline-block">
            {{ limitUp }}
          </div>
        </div>
        <div class="text-slate-600 text-lg font-mono font-light">/</div>
        <div class="text-center">
          <div class="text-slate-400 text-[11px] mb-1 font-medium">跌停板</div>
          <div class="text-xl font-bold font-mono text-down bg-down-subtle px-2.5 py-0.5 rounded inline-block">
            {{ limitDown }}
          </div>
        </div>
        <div class="text-center pl-2 border-l border-slate-800">
          <div class="text-slate-400 text-[11px] mb-1 font-medium">板比净差</div>
          <div class="font-mono font-semibold" :class="limitUp >= limitDown ? 'text-up' : 'text-down'">
            {{ limitUp - limitDown > 0 ? '+' : '' }}{{ limitUp - limitDown }}
          </div>
        </div>
      </div>

    </div>
  </div>
</template>
