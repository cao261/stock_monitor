<script setup>
import KLineChart from '../KLineChart.vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  chartCode: { type: String, default: null },
  chartName: { type: String, default: '' },
  chartCost: { type: Number, default: null },
  chartTargetWin: { type: Number, default: null },
  chartTargetLoss: { type: Number, default: null },
  chartKey: { type: Number, default: 0 },
})

const emit = defineEmits(['close'])
</script>

<template>
  <div v-if="props.show && props.chartCode" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-5xl h-[80vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
            📈 60日专业 K 线走势与持仓风控线
          </span>
          <span class="font-mono text-sm text-blue-300 font-bold">
            {{ props.chartCode }}
          </span>
          <span class="text-xs text-slate-300 font-medium">
            {{ props.chartName }}
          </span>
        </div>

        <!-- 价格水平线图例 -->
        <div class="hidden sm:flex items-center gap-3 text-xs font-mono">
          <span v-if="props.chartCost" class="flex items-center gap-1 text-slate-400">
            <span class="inline-block w-2.5 h-0.5 bg-slate-400"></span> 成本: ¥{{ props.chartCost.toFixed(2) }}
          </span>
          <span v-if="props.chartTargetWin" class="flex items-center gap-1 text-up">
            <span class="inline-block w-2.5 h-0.5 bg-red-500"></span> 止盈: ¥{{ props.chartTargetWin.toFixed(2) }}
          </span>
          <span v-if="props.chartTargetLoss" class="flex items-center gap-1 text-down">
            <span class="inline-block w-2.5 h-0.5 bg-emerald-500"></span> 止损: ¥{{ props.chartTargetLoss.toFixed(2) }}
          </span>
        </div>

        <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
          ✕
        </button>
      </div>

      <!-- KLineChart 容器 -->
      <div class="flex-1 bg-[#0d1322] p-2 relative overflow-hidden">
        <KLineChart 
          :key="props.chartKey"
          :ts-code="props.chartCode"
          :cost-price="props.chartCost"
          :target-win="props.chartTargetWin"
          :target-loss="props.chartTargetLoss"
        />
      </div>
    </div>
  </div>
</template>
