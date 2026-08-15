<script setup>
import { computed, ref } from 'vue'
import FundFlowBubble from '../FundFlowBubble.vue'

const props = defineProps({
  fundFlow: { type: Object, default: () => ({ items: [], refreshed_at: null, count: 0 }) },
  loading: { type: Boolean, default: false },
})

const viewMode = ref('bubble') // 'bubble' | 'table'
const filterMode = ref('all')  // 'all' | 'inflow' | 'outflow'

const items = computed(() => props.fundFlow?.items || [])

const filteredItems = computed(() => {
  if (filterMode.value === 'inflow') {
    return items.value.filter(x => (x.net_amount || 0) > 0)
  }
  if (filterMode.value === 'outflow') {
    return items.value.filter(x => (x.net_amount || 0) < 0)
  }
  return items.value
})

const totalInflow = computed(() => {
  const sum = items.value.filter(x => (x.net_amount || 0) > 0).reduce((acc, cur) => acc + (cur.net_amount || 0), 0)
  return sum.toFixed(1)
})

const totalOutflow = computed(() => {
  const sum = items.value.filter(x => (x.net_amount || 0) < 0).reduce((acc, cur) => acc + Math.abs(cur.net_amount || 0), 0)
  return sum.toFixed(1)
})

function fmtAmount(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)} 亿`
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}
</script>

<template>
  <div class="rpt-panel mb-4 overflow-hidden">
    <!-- 头部栏：标题、切换视图与统计 -->
    <div class="p-3 bg-[#131c2e] border-b border-slate-700/80 flex flex-col md:flex-row items-start md:items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
          🌊 概念板块资金流向监控
        </span>
        
        <!-- 视图切换 -->
        <div class="flex items-center bg-slate-900 rounded p-0.5 border border-slate-700">
          <button
            @click="viewMode = 'bubble'"
            class="text-xs px-2.5 py-0.5 rounded transition font-medium"
            :class="viewMode === 'bubble' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'"
          >
            🌐 力导向气泡
          </button>
          <button
            @click="viewMode = 'table'"
            class="text-xs px-2.5 py-0.5 rounded transition font-medium"
            :class="viewMode === 'table' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'"
          >
            📊 数据报表
          </button>
        </div>

        <!-- 筛选 -->
        <div v-if="viewMode === 'table'" class="flex items-center gap-1">
          <button
            @click="filterMode = 'all'"
            class="text-[11px] px-2 py-0.5 rounded border"
            :class="filterMode === 'all' ? 'bg-slate-700 border-slate-500 text-white' : 'border-slate-800 text-slate-400'"
          >
            全量
          </button>
          <button
            @click="filterMode = 'inflow'"
            class="text-[11px] px-2 py-0.5 rounded border"
            :class="filterMode === 'inflow' ? 'bg-red-950 border-red-700 text-red-200' : 'border-slate-800 text-slate-400'"
          >
            净流入 Top
          </button>
          <button
            @click="filterMode = 'outflow'"
            class="text-[11px] px-2 py-0.5 rounded border"
            :class="filterMode === 'outflow' ? 'bg-emerald-950 border-emerald-700 text-emerald-200' : 'border-slate-800 text-slate-400'"
          >
            净流出 Top
          </button>
        </div>
      </div>

      <!-- 统计指标 -->
      <div class="flex items-center gap-3 text-xs font-mono">
        <div>
          <span class="text-slate-400">主力流入: </span>
          <span class="text-up font-bold">+{{ totalInflow }} 亿</span>
        </div>
        <span class="text-slate-700">|</span>
        <div>
          <span class="text-slate-400">主力流出: </span>
          <span class="text-down font-bold">-{{ totalOutflow }} 亿</span>
        </div>
        <span class="text-slate-700">|</span>
        <span class="text-slate-400">覆盖 {{ items.length }} 个概念板块</span>
      </div>
    </div>

    <!-- 视图区域 -->
    <div class="p-3">
      <!-- 模式1：力导向气泡图 -->
      <div v-show="viewMode === 'bubble'" class="w-full">
        <FundFlowBubble 
          :items="items" 
          :loading="props.loading" 
        />
      </div>

      <!-- 模式2：结构化报表表格 -->
      <div v-show="viewMode === 'table'" class="max-h-[460px] overflow-y-auto">
        <table class="rpt-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>概念板块名称</th>
              <th class="text-right">板块涨跌幅</th>
              <th class="text-right">主力净额</th>
              <th class="text-right">流入资金</th>
              <th class="text-right">流出资金</th>
              <th>领涨龙头标的</th>
              <th class="text-right">龙头涨幅</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="filteredItems.length === 0">
              <td colspan="8" class="text-center py-6 text-slate-500">
                暂无板块资金流数据
              </td>
            </tr>
            <tr v-for="(sec, idx) in filteredItems" :key="sec.name">
              <td class="font-mono text-slate-500 text-xs">{{ idx + 1 }}</td>
              <td class="font-bold text-slate-200 text-xs">{{ sec.name }}</td>
              <td class="text-right font-mono font-semibold" :class="sec.change_pct > 0 ? 'text-up' : (sec.change_pct < 0 ? 'text-down' : 'text-flat')">
                {{ fmtPct(sec.change_pct) }}
              </td>
              <td class="text-right font-mono font-bold" :class="sec.net_amount > 0 ? 'text-up' : (sec.net_amount < 0 ? 'text-down' : 'text-flat')">
                {{ fmtAmount(sec.net_amount) }}
              </td>
              <td class="text-right font-mono text-xs text-slate-300">{{ fmtAmount(sec.inflow) }}</td>
              <td class="text-right font-mono text-xs text-slate-300">{{ fmtAmount(sec.outflow) }}</td>
              <td class="text-xs text-blue-300 font-medium">{{ sec.leading_stock || '--' }}</td>
              <td class="text-right font-mono text-xs" :class="sec.leading_change_pct > 0 ? 'text-up' : (sec.leading_change_pct < 0 ? 'text-down' : 'text-flat')">
                {{ fmtPct(sec.leading_change_pct) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
