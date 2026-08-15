<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  planData: { type: Object, default: null }, // { stock_id, ts_code, name, current_price, holding_info, existing, plan, explain, model }
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'apply'])

const formEntryMin = ref('')
const formEntryMax = ref('')
const formTargetWin = ref('')
const formTargetLoss = ref('')
const formTradeNote = ref('')

watch(() => props.planData, (val) => {
  if (val?.plan) {
    formEntryMin.value = val.plan.entry_price_min ?? ''
    formEntryMax.value = val.plan.entry_price_max ?? ''
    formTargetWin.value = val.plan.target_win ?? ''
    formTargetLoss.value = val.plan.target_loss ?? ''
    formTradeNote.value = val.plan.trade_note ?? ''
  }
}, { immediate: true })

const holding = computed(() => props.planData?.holding_info || {})
const hasPosition = computed(() => Boolean(holding.value?.has_position))
const costPrice = computed(() => holding.value?.cost_price)
const position = computed(() => holding.value?.position)
const floatingPnl = computed(() => holding.value?.floating_pnl)
const returnRate = computed(() => holding.value?.return_rate)

function handleApply() {
  emit('apply', {
    entry_price_min: formEntryMin.value !== '' ? parseFloat(formEntryMin.value) : null,
    entry_price_max: formEntryMax.value !== '' ? parseFloat(formEntryMax.value) : null,
    target_win: formTargetWin.value !== '' ? parseFloat(formTargetWin.value) : null,
    target_loss: formTargetLoss.value !== '' ? parseFloat(formTargetLoss.value) : null,
    trade_note: formTradeNote.value.trim() || null,
  })
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}

function fmtPnl(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return sign + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-2xl max-h-[90vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-sm font-bold text-purple-300 flex items-center gap-1.5">
            🤖 AI 前瞻操盘规划 · 个性化持仓策略
          </span>
          <span class="font-mono text-xs text-blue-300 font-bold">
            {{ props.planData?.ts_code }} {{ props.planData?.name }}
          </span>
          <span class="text-xs font-mono text-slate-400">现价 ¥{{ fmtPrice(props.planData?.current_price) }}</span>
        </div>
        <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
          ✕
        </button>
      </div>

      <!-- 内容区 -->
      <div class="p-4 overflow-y-auto space-y-3.5 bg-[#0d1322] text-xs">
        <!-- 👤 用户持仓底账与画像卡片 (核心个性化呈现) -->
        <div class="p-3 rounded border" :class="hasPosition ? 'bg-slate-900 border-slate-700' : 'bg-slate-900/60 border-slate-800'">
          <div class="flex items-center justify-between mb-2">
            <span class="font-bold text-slate-200 flex items-center gap-1.5">
              👤 我的持仓画像与成本底账
            </span>
            <span 
              class="rpt-badge font-mono"
              :class="hasPosition ? (returnRate >= 0 ? 'bg-up-subtle text-up' : 'bg-down-subtle text-down') : 'bg-slate-800 text-slate-400 border border-slate-700'"
            >
              {{ hasPosition ? `持仓中 (${position} 股)` : '当前空仓 / 观察池' }}
            </span>
          </div>

          <div v-if="hasPosition" class="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-center">
            <div class="bg-[#0b0f19] p-2 rounded border border-slate-800">
              <div class="text-slate-400 text-[11px]">持仓成本</div>
              <div class="text-slate-100 font-bold mt-0.5">¥{{ fmtPrice(costPrice) }}</div>
            </div>
            <div class="bg-[#0b0f19] p-2 rounded border border-slate-800">
              <div class="text-slate-400 text-[11px]">持仓数量</div>
              <div class="text-slate-100 font-bold mt-0.5">{{ position?.toLocaleString() }} 股</div>
            </div>
            <div class="bg-[#0b0f19] p-2 rounded border border-slate-800">
              <div class="text-slate-400 text-[11px]">浮动盈亏</div>
              <div class="font-bold mt-0.5" :class="floatingPnl >= 0 ? 'text-up' : 'text-down'">
                ¥{{ fmtPnl(floatingPnl) }}
              </div>
            </div>
            <div class="bg-[#0b0f19] p-2 rounded border border-slate-800">
              <div class="text-slate-400 text-[11px]">当前收益率</div>
              <div class="font-bold mt-0.5" :class="returnRate >= 0 ? 'text-up' : 'text-down'">
                {{ fmtPct(returnRate) }}
              </div>
            </div>
          </div>

          <div v-else class="text-slate-400 text-[11px] py-1">
            当前无持仓成本，领航员将基于 50 日 K 线形态为您寻找【最佳首次建仓买点甜区】与防守位。
          </div>
        </div>

        <!-- 💡 领航员个性化决策指令与依据 -->
        <div class="p-3 bg-purple-950/25 rounded border border-purple-800/40 space-y-2">
          <div class="flex items-center justify-between flex-wrap gap-1">
            <div class="flex items-center gap-2">
              <span class="text-purple-300 font-bold">💡 领航员操盘指令:</span>
              <span v-if="props.planData?.plan?.position_advice" class="rpt-badge bg-purple-900 text-purple-100 border border-purple-500 font-bold">
                {{ props.planData.plan.position_advice }}
              </span>
            </div>
            <div class="flex items-center gap-1">
              <span v-for="t in props.planData?.plan?.tags" :key="t" class="rpt-tag bg-purple-900/60 text-purple-200 border border-purple-700">
                {{ t }}
              </span>
            </div>
          </div>

          <!-- 个性化逻辑说明 -->
          <p class="text-slate-200 leading-relaxed text-xs">
            {{ props.planData?.plan?.rationale }}
          </p>
        </div>

        <!-- 📊 技术特征摘要 -->
        <div v-if="props.planData?.explain?.features" class="rpt-panel-sub p-2.5 space-y-1.5">
          <div class="font-bold text-slate-400 text-[11px]">📊 50日技术形态与量能参考</div>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-[11px]">
            <div class="bg-slate-900/80 p-1.5 rounded border border-slate-800">
              <span class="text-slate-400">均线趋势: </span>
              <span class="text-slate-200 font-semibold">{{ props.planData.explain.features.trend }}</span>
            </div>
            <div class="bg-slate-900/80 p-1.5 rounded border border-slate-800">
              <span class="text-slate-400">量能状态: </span>
              <span class="text-slate-200 font-semibold">{{ props.planData.explain.features.volume_trend }}</span>
            </div>
            <div class="bg-slate-900/80 p-1.5 rounded border border-slate-800">
              <span class="text-slate-400">关键支撑: </span>
              <span class="text-emerald-400 font-semibold">¥{{ fmtPrice(props.planData.explain.features.support_level) }}</span>
            </div>
            <div class="bg-slate-900/80 p-1.5 rounded border border-slate-800">
              <span class="text-slate-400">关键阻力: </span>
              <span class="text-red-400 font-semibold">¥{{ fmtPrice(props.planData.explain.features.resistance_level) }}</span>
            </div>
          </div>
        </div>

        <!-- 🎯 v4.3 真实支撑位/压力位/ATR 引擎矩阵（基于 MA20 / 箱体 / ATR 动态） -->
        <div v-if="props.planData?.explain?.ambush_levels" class="rpt-panel-sub p-2.5 space-y-2">
          <div class="flex items-center justify-between">
            <div class="font-bold text-purple-300 text-[11px] flex items-center gap-1">
              🎯 真实支撑/压力矩阵（量化引擎 · v4.3）
            </div>
            <span v-if="props.planData.explain.ambush_levels.volatility_tag" class="rpt-badge bg-purple-900/60 text-purple-200 border border-purple-700 font-mono text-[10px]">
              {{ props.planData.explain.ambush_levels.volatility_tag }}
            </span>
          </div>

          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-[11px]">
            <div class="bg-emerald-950/40 p-1.5 rounded border border-emerald-800/50">
              <div class="text-emerald-400 font-bold text-[10px]">🛡️ 真实支撑</div>
              <div class="text-emerald-200 font-bold mt-0.5">¥{{ fmtPrice(props.planData.explain.ambush_levels.support_price) }}</div>
              <div class="text-emerald-500/80 text-[9px] mt-0.5 truncate" :title="props.planData.explain.ambush_levels.support_name">
                {{ props.planData.explain.ambush_levels.support_name }}
              </div>
            </div>
            <div class="bg-red-950/40 p-1.5 rounded border border-red-800/50">
              <div class="text-red-400 font-bold text-[10px]">🏔️ 真实压力</div>
              <div class="text-red-200 font-bold mt-0.5">¥{{ fmtPrice(props.planData.explain.ambush_levels.resistance_price) }}</div>
              <div class="text-red-500/80 text-[9px] mt-0.5 truncate" :title="props.planData.explain.ambush_levels.resistance_name">
                {{ props.planData.explain.ambush_levels.resistance_name }}
              </div>
            </div>
            <div class="bg-slate-900/80 p-1.5 rounded border border-slate-800">
              <div class="text-slate-400 font-bold text-[10px]">📏 ATR 波动</div>
              <div class="text-slate-200 font-bold mt-0.5">¥{{ fmtPrice(props.planData.explain.ambush_levels.atr) }}</div>
              <div class="text-slate-500 text-[9px] mt-0.5">20日波动率 {{ props.planData.explain.ambush_levels.volatility_pct }}%</div>
            </div>
            <div class="bg-amber-950/30 p-1.5 rounded border border-amber-800/40">
              <div class="text-amber-400 font-bold text-[10px]">🎯 建议埋伏区间</div>
              <div class="text-amber-200 font-bold mt-0.5">
                <template v-if="props.planData.explain.ambush_levels.ambush_zone">
                  ¥{{ fmtPrice(props.planData.explain.ambush_levels.ambush_zone[0]) }} ~ ¥{{ fmtPrice(props.planData.explain.ambush_levels.ambush_zone[1]) }}
                </template>
                <template v-else>--</template>
              </div>
              <div class="text-amber-500/80 text-[9px] mt-0.5">紧贴支撑位低吸</div>
            </div>
          </div>

          <!-- 引擎技术面解读 -->
          <p v-if="props.planData.explain.ambush_levels.technical_basis" class="text-[11px] text-slate-300 bg-slate-900/60 p-2 rounded border border-slate-800 leading-relaxed">
            <span class="text-purple-300 font-bold">📐 引擎解读：</span>{{ props.planData.explain.ambush_levels.technical_basis }}
          </p>
        </div>

        <!-- 🛠️ 计划参数微调表单 -->
        <div class="space-y-2.5 pt-1">
          <div class="font-bold text-slate-200">
            {{ hasPosition ? '🛠️ 针对持仓的补仓买点与风控线（确认后自动生效）：' : '🛠️ 建议建仓区间与风控线（确认后自动生效）：' }}
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div>
              <label class="block text-[11px] text-amber-400 font-medium mb-1">
                {{ hasPosition ? '建议加仓/补仓下限 (元)' : '理想建仓下限 (元)' }}
              </label>
              <input 
                v-model="formEntryMin"
                type="number"
                step="0.001"
                class="rpt-input w-full font-mono font-semibold"
              />
            </div>
            <div>
              <label class="block text-[11px] text-amber-400 font-medium mb-1">
                {{ hasPosition ? '建议加仓/补仓上限 (元)' : '理想建仓上限 (元)' }}
              </label>
              <input 
                v-model="formEntryMax"
                type="number"
                step="0.001"
                class="rpt-input w-full font-mono font-semibold"
              />
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div>
              <label class="block text-[11px] text-emerald-400 font-medium mb-1">建议止盈目标价 (元)</label>
              <input 
                v-model="formTargetWin"
                type="number"
                step="0.001"
                class="rpt-input w-full font-mono font-semibold border-emerald-700/50"
              />
            </div>
            <div>
              <label class="block text-[11px] text-red-400 font-medium mb-1">建议防守止损价 (元)</label>
              <input 
                v-model="formTargetLoss"
                type="number"
                step="0.001"
                class="rpt-input w-full font-mono font-semibold border-red-700/50"
              />
            </div>
          </div>

          <div>
            <label class="block text-[11px] text-slate-400 font-medium mb-1">个性化交易纪律与操作备忘</label>
            <input 
              v-model="formTradeNote"
              type="text"
              class="rpt-input w-full"
            />
          </div>
        </div>
      </div>

      <!-- 底部操作栏 -->
      <div class="p-3 bg-[#131c2e] border-t border-slate-700 flex items-center justify-end gap-2.5">
        <button @click="emit('close')" class="rpt-btn text-slate-400">
          取消
        </button>
        <button 
          @click="handleApply"
          :disabled="props.saving"
          class="rpt-btn rpt-btn-primary font-semibold"
        >
          {{ props.saving ? '保存中…' : '✔ 确认并应用到自选股' }}
        </button>
      </div>
    </div>
  </div>
</template>
