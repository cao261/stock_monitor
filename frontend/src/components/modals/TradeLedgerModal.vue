<script setup>
import { ref } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  ledgerData: { type: Object, default: () => ({ trades: [], total_count: 0, total_realized_pnl: 0 }) },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'filter-change'])

const filterCode = ref('')

function handleFilter() {
  emit('filter-change', filterCode.value.trim() || undefined)
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
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-4xl max-h-[85vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
            💰 历史交割单与资金账本
          </span>
          <span class="text-xs font-mono text-slate-400">
            共计 {{ props.ledgerData.total_count }} 笔交易
          </span>
        </div>
        <div class="flex items-center gap-3">
          <div class="font-mono text-xs">
            <span class="text-slate-400">已实现总盈亏: </span>
            <span class="font-bold text-sm" :class="props.ledgerData.total_realized_pnl >= 0 ? 'text-up' : 'text-down'">
              ¥{{ fmtPnl(props.ledgerData.total_realized_pnl) }}
            </span>
          </div>
          <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
            ✕
          </button>
        </div>
      </div>

      <!-- 搜索筛选条 -->
      <div class="p-2.5 bg-slate-900/90 border-b border-slate-800 flex items-center gap-2 text-xs">
        <input 
          v-model="filterCode"
          type="text"
          placeholder="按代码筛选 (如 sh600000 / 600000)"
          class="rpt-input rpt-input-sm w-56 font-mono"
          @keyup.enter="handleFilter"
        />
        <button @click="handleFilter" class="rpt-btn rpt-btn-sm rpt-btn-primary">
          查询
        </button>
        <button 
          v-if="filterCode" 
          @click="filterCode = ''; handleFilter()" 
          class="rpt-btn rpt-btn-sm text-slate-400"
        >
          重置
        </button>
      </div>

      <!-- 账本表格区 -->
      <div class="overflow-y-auto max-h-[60vh]">
        <div v-if="props.loading" class="text-center py-12 text-slate-400 text-xs">
          账本记录加载中…
        </div>
        <table v-else class="rpt-table">
          <thead>
            <tr>
              <th>交割时间</th>
              <th>标的代码</th>
              <th>标的名称</th>
              <th>操作方向</th>
              <th class="text-right">成交价 (元)</th>
              <th class="text-right">成交量 (股)</th>
              <th class="text-right">成交金额 (元)</th>
              <th class="text-right">已实现盈亏 (元)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!props.ledgerData.trades?.length">
              <td colspan="8" class="text-center py-8 text-slate-500 text-xs">
                暂无交易记录
              </td>
            </tr>
            <tr v-for="t in props.ledgerData.trades" :key="t.id">
              <td class="font-mono text-xs text-slate-400">{{ t.created_at }}</td>
              <td class="font-mono font-bold text-xs text-blue-300">{{ t.ts_code }}</td>
              <td class="text-xs text-slate-200">{{ t.name || '--' }}</td>
              <td>
                <span 
                  class="rpt-badge font-mono"
                  :class="t.action === 'BUY' ? 'bg-up-subtle text-up' : 'bg-down-subtle text-down'"
                >
                  {{ t.action === 'BUY' ? '买入建仓' : '卖出减仓' }}
                </span>
              </td>
              <td class="text-right font-mono text-xs">{{ fmtPrice(t.price) }}</td>
              <td class="text-right font-mono text-xs font-medium">{{ Number(t.volume).toLocaleString() }}</td>
              <td class="text-right font-mono text-xs text-slate-300">
                ¥{{ (t.price * t.volume).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
              </td>
              <td class="text-right font-mono font-bold text-xs" :class="t.realized_pnl > 0 ? 'text-up' : (t.realized_pnl < 0 ? 'text-down' : 'text-slate-500')">
                {{ t.action === 'SELL' ? fmtPnl(t.realized_pnl) : '--' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
