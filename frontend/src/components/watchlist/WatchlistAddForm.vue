<script setup>
import { ref } from 'vue'

const props = defineProps({
  adding: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['add', 'close'])

const code = ref('')
const name = ref('')
const costPrice = ref('')
const position = ref('')
const targetWin = ref('')
const targetLoss = ref('')
const tradeNote = ref('')
const showAdvanced = ref(false)

const STRATEGY_PRESETS = [
  {
    key: 'scalp',
    label: '超短接力',
    class: 'border-red-600/40 text-red-300 hover:bg-red-950/40',
    text: '[超短] 龙头博弈。纪律：跌破5日线或亏损达5%无条件止损。不破5日线死拿。',
  },
  {
    key: 'swing',
    label: '中线右侧',
    class: 'border-sky-600/40 text-sky-300 hover:bg-sky-950/40',
    text: '[中线] 趋势跟随。纪律：20日均线处缩量低吸。放量跌破20日线止损，前高压力位止盈半仓。',
  },
  {
    key: 'grid',
    label: '网格震荡套利',
    class: 'border-emerald-600/40 text-emerald-300 hover:bg-emerald-950/40',
    text: '[网格] 震荡套利。纪律：以基准价为锚，每跌 5% 加仓 1 手，每涨 5% 卖出 1 手。利用时间换空间。',
  },
  {
    key: 'bottom',
    label: '左侧金字塔定投',
    class: 'border-amber-600/40 text-amber-300 hover:bg-amber-950/40',
    text: '[长线] 金字塔建仓（底仓20%）。纪律：无需止损，每跌幅达 8%、15%、25% 分别加仓 1x, 2x, 4x。',
  },
]

function applyPreset(text) {
  tradeNote.value = text
  showAdvanced.value = true
}

function normalizeCode(raw) {
  const s = String(raw || '').trim().toLowerCase()
  if (!s) return ''
  if (s.startsWith('sh') || s.startsWith('sz') || s.startsWith('bj')) return s
  if (!/^\d{6}$/.test(s)) return s
  if (s.startsWith('5') || s.startsWith('6')) return 'sh' + s
  if (s.startsWith('0') || s.startsWith('3')) return 'sz' + s
  if (s.startsWith('4') || s.startsWith('8') || s.startsWith('9')) return 'bj' + s
  return 'sh' + s
}

function handleSubmit() {
  const normCode = normalizeCode(code.value)
  if (!normCode) return

  emit('add', {
    ts_code: normCode,
    name: name.value.trim() || undefined,
    cost_price: costPrice.value !== '' ? parseFloat(costPrice.value) : undefined,
    position: position.value !== '' ? parseInt(position.value, 10) : undefined,
    target_win: targetWin.value !== '' ? parseFloat(targetWin.value) : undefined,
    target_loss: targetLoss.value !== '' ? parseFloat(targetLoss.value) : undefined,
    trade_note: tradeNote.value.trim() || undefined,
  })
}

function resetForm() {
  code.value = ''
  name.value = ''
  costPrice.value = ''
  position.value = ''
  targetWin.value = ''
  targetLoss.value = ''
  tradeNote.value = ''
}

defineExpose({ resetForm })
</script>

<template>
  <div class="rpt-panel p-4 mb-4 border-l-4 border-l-blue-500">
    <div class="flex items-center justify-between mb-3">
      <div class="flex items-center gap-2">
        <span class="text-sm font-bold text-slate-100">新增标的录入与交易计划设定</span>
        <span class="text-[11px] text-slate-400">支持 A股 / ETF 6位纯数字自动补全前缀</span>
      </div>
      <button 
        @click="emit('close')" 
        class="text-slate-400 hover:text-slate-200 text-xs px-2 py-1"
      >
        ✕ 收起
      </button>
    </div>

    <!-- 错误提示 -->
    <div v-if="props.error" class="mb-3 p-2.5 rounded bg-red-950/60 border border-red-500/50 text-red-200 text-xs flex items-center justify-between">
      <span>⚠️ {{ props.error }}</span>
    </div>

    <form @submit.prevent="handleSubmit" class="space-y-3">
      <!-- 基础字段 -->
      <div class="grid grid-cols-2 md:grid-cols-5 gap-2.5">
        <div class="col-span-1">
          <label class="block text-[11px] text-slate-400 font-medium mb-1">股票代码 *</label>
          <input 
            v-model="code"
            type="text" 
            placeholder="如 600519 / 510300"
            class="rpt-input w-full font-mono font-semibold text-white uppercase"
            required
          />
        </div>

        <div class="col-span-1">
          <label class="block text-[11px] text-slate-400 font-medium mb-1">自定义名称 (可选)</label>
          <input 
            v-model="name"
            type="text" 
            placeholder="留空自动获取"
            class="rpt-input w-full"
          />
        </div>

        <div class="col-span-1">
          <label class="block text-[11px] text-slate-400 font-medium mb-1">成本价 (元/股)</label>
          <input 
            v-model="costPrice"
            type="number" 
            step="0.001"
            placeholder="未持仓留空"
            class="rpt-input w-full font-mono"
          />
        </div>

        <div class="col-span-1">
          <label class="block text-[11px] text-slate-400 font-medium mb-1">持仓数量 (股)</label>
          <input 
            v-model="position"
            type="number" 
            step="100"
            placeholder="未持仓留空"
            class="rpt-input w-full font-mono"
          />
        </div>

        <div class="col-span-2 md:col-span-1 flex items-end">
          <button 
            type="submit" 
            :disabled="props.adding"
            class="rpt-btn rpt-btn-primary w-full h-[34px] font-semibold"
          >
            {{ props.adding ? '提交中…' : '✔ 保存并监控' }}
          </button>
        </div>
      </div>

      <!-- 高级交易计划选项折叠条 -->
      <div class="pt-1">
        <button 
          type="button" 
          @click="showAdvanced = !showAdvanced"
          class="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1.5 focus:outline-none"
        >
          <span>{{ showAdvanced ? '▼' : '▶' }} 交易纪律与风控参数 (止盈 / 止损 / 策略备忘)</span>
        </button>
      </div>

      <!-- 高级交易计划面板 -->
      <div v-show="showAdvanced" class="p-3 bg-slate-900/80 rounded border border-slate-800 space-y-3 mt-2">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label class="block text-[11px] text-emerald-400 font-medium mb-1">止盈目标价 (元)</label>
            <input 
              v-model="targetWin"
              type="number" 
              step="0.001"
              placeholder="触及自动桌面提醒"
              class="rpt-input w-full font-mono border-emerald-700/50"
            />
          </div>

          <div>
            <label class="block text-[11px] text-red-400 font-medium mb-1">防守止损价 (元)</label>
            <input 
              v-model="targetLoss"
              type="number" 
              step="0.001"
              placeholder="跌破强制桌面提醒"
              class="rpt-input w-full font-mono border-red-700/50"
            />
          </div>

          <div>
            <label class="block text-[11px] text-amber-400 font-medium mb-1">策略备忘与纪律逻辑</label>
            <input 
              v-model="tradeNote"
              type="text" 
              placeholder="如：跌破 15.20 清仓，每跌 5% 加仓"
              class="rpt-input w-full border-amber-700/50"
            />
          </div>
        </div>

        <!-- 经典交易模板一键填入 -->
        <div class="flex items-center gap-2 flex-wrap pt-1 border-t border-slate-800/80">
          <span class="text-[11px] text-slate-400">快速填入策略模板：</span>
          <button 
            v-for="p in STRATEGY_PRESETS" 
            :key="p.key"
            type="button"
            @click="applyPreset(p.text)"
            class="text-[11px] px-2 py-0.5 rounded border transition"
            :class="p.class"
          >
            {{ p.label }}
          </button>
        </div>
      </div>
    </form>
  </div>
</template>
