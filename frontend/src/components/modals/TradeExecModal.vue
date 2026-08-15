<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  item: { type: Object, default: null }, // Watchlist item
  saving: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'confirm'])

const action = ref('BUY') // 'BUY' | 'SELL'
const tradePrice = ref('')
const tradeVolume = ref('100')

watch(() => props.item, (val) => {
  if (val) {
    tradePrice.value = val.price != null ? String(val.price) : (val.cost_price != null ? String(val.cost_price) : '')
    tradeVolume.value = '100'
    action.value = 'BUY'
  }
}, { immediate: true })

const curPos = computed(() => Number(props.item?.position || 0))
const curCost = computed(() => Number(props.item?.cost_price || 0))
const curPrice = computed(() => Number(props.item?.price || 0))

// 预估运算
const preview = computed(() => {
  const p = parseFloat(tradePrice.value)
  const v = parseInt(tradeVolume.value, 10)
  if (isNaN(p) || p <= 0 || isNaN(v) || v <= 0) {
    return null
  }

  if (action.value === 'BUY') {
    const newPos = curPos.value + v
    const newCost = curPos.value > 0 && curCost.value > 0
      ? (curCost.value * curPos.value + p * v) / newPos
      : p
    return {
      action: 'BUY',
      newPos,
      newCost: newCost.toFixed(3),
      realizedPnl: 0,
      turnover: (p * v).toFixed(2),
    }
  } else {
    // SELL
    if (v > curPos.value) {
      return { error: `减仓数量 (${v}) 超过当前持仓 (${curPos.value})` }
    }
    const newPos = curPos.value - v
    const realized = curCost.value > 0 ? (p - curCost.value) * v : 0
    return {
      action: 'SELL',
      newPos,
      newCost: curCost.value.toFixed(3),
      realizedPnl: realized.toFixed(2),
      turnover: (p * v).toFixed(2),
    }
  }
})

function handleConfirm() {
  const p = parseFloat(tradePrice.value)
  const v = parseInt(tradeVolume.value, 10)
  if (isNaN(p) || p <= 0 || isNaN(v) || v <= 0) return

  const signedVol = action.value === 'BUY' ? v : -v
  emit('confirm', {
    id: props.item.id,
    price: p,
    volume: signedVol,
  })
}

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-md">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
            ⚖️ 标的交割记账与仓位重算
          </span>
          <span class="font-mono text-xs text-blue-300 font-semibold">
            {{ props.item?.ts_code }} {{ props.item?.name }}
          </span>
        </div>
        <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
          ✕
        </button>
      </div>

      <!-- 内容区 -->
      <div class="p-4 space-y-3.5 text-xs bg-[#0d1322]">
        <div v-if="props.error" class="p-2.5 rounded bg-red-950/80 border border-red-500/50 text-red-200">
          {{ props.error }}
        </div>

        <!-- 当前仓位底账 -->
        <div class="p-2.5 bg-slate-900 rounded border border-slate-800 grid grid-cols-3 gap-2 font-mono text-center">
          <div>
            <div class="text-slate-400 text-[11px]">当前持仓</div>
            <div class="text-slate-200 font-bold mt-0.5">{{ curPos.toLocaleString() }} 股</div>
          </div>
          <div>
            <div class="text-slate-400 text-[11px]">持仓成本</div>
            <div class="text-slate-200 font-bold mt-0.5">¥{{ fmtPrice(curCost) }}</div>
          </div>
          <div>
            <div class="text-slate-400 text-[11px]">当前现价</div>
            <div class="font-bold mt-0.5 text-blue-300">¥{{ fmtPrice(curPrice) }}</div>
          </div>
        </div>

        <!-- 交易操作选项 -->
        <div class="space-y-3 pt-1">
          <!-- 方向选择 -->
          <div class="flex items-center gap-2">
            <label class="text-slate-300 font-medium w-16">操作类型:</label>
            <div class="flex items-center gap-2 flex-1">
              <button
                type="button"
                @click="action = 'BUY'"
                class="flex-1 py-1.5 rounded font-bold transition border"
                :class="action === 'BUY' ? 'bg-red-900 border-red-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'"
              >
                买入 / 补仓 (+)
              </button>
              <button
                type="button"
                @click="action = 'SELL'"
                class="flex-1 py-1.5 rounded font-bold transition border"
                :class="action === 'SELL' ? 'bg-emerald-900 border-emerald-500 text-white' : 'bg-slate-800 border-slate-700 text-slate-400'"
              >
                卖出 / 减仓 (-)
              </button>
            </div>
          </div>

          <!-- 成交价格 -->
          <div class="flex items-center gap-2">
            <label class="text-slate-300 font-medium w-16">成交价格:</label>
            <input 
              v-model="tradePrice"
              type="number"
              step="0.001"
              class="rpt-input flex-1 font-mono font-semibold"
              placeholder="元/股"
            />
          </div>

          <!-- 成交数量 -->
          <div class="flex items-center gap-2">
            <label class="text-slate-300 font-medium w-16">成交数量:</label>
            <input 
              v-model="tradeVolume"
              type="number"
              step="100"
              class="rpt-input flex-1 font-mono font-semibold"
              placeholder="股 (1手=100股)"
            />
          </div>
        </div>

        <!-- 预估重算结果 -->
        <div v-if="preview" class="p-3 bg-slate-900/90 rounded border border-slate-700 font-mono text-xs space-y-1">
          <div v-if="preview.error" class="text-red-400 font-semibold">
            ⚠️ {{ preview.error }}
          </div>
          <template v-else>
            <div class="flex items-center justify-between text-slate-400">
              <span>交易金额:</span>
              <span class="text-slate-200">¥{{ Number(preview.turnover).toLocaleString() }}</span>
            </div>
            <div class="flex items-center justify-between text-slate-400">
              <span>交割后持仓:</span>
              <span class="text-slate-100 font-bold">{{ Number(preview.newPos).toLocaleString() }} 股</span>
            </div>
            <div class="flex items-center justify-between text-slate-400">
              <span>加权新成本:</span>
              <span class="text-amber-300 font-bold">¥{{ preview.newCost }}</span>
            </div>
            <div v-if="action === 'SELL'" class="flex items-center justify-between text-slate-400 border-t border-slate-800 pt-1">
              <span>本笔实现盈亏:</span>
              <span class="font-bold" :class="preview.realizedPnl >= 0 ? 'text-up' : 'text-down'">
                {{ preview.realizedPnl >= 0 ? '+' : '' }}¥{{ Number(preview.realizedPnl).toLocaleString() }}
              </span>
            </div>
          </template>
        </div>
      </div>

      <!-- 底部栏 -->
      <div class="p-3 bg-[#131c2e] border-t border-slate-700 flex items-center justify-end gap-2.5">
        <button @click="emit('close')" class="rpt-btn text-slate-400">
          取消
        </button>
        <button 
          @click="handleConfirm"
          :disabled="props.saving || (preview && preview.error)"
          class="rpt-btn rpt-btn-primary"
        >
          {{ props.saving ? '记账中…' : '✔ 确认记账并更新仓位' }}
        </button>
      </div>
    </div>
  </div>
</template>
