<script setup>
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  sortKey: { type: String, default: '' },
  sortDir: { type: String, default: 'desc' },
})

const emit = defineEmits([
  'toggle-sort',
  'open-chart',
  'open-ai-plan',
  'open-trade-exec',
  'update-item',
  'delete-item',
])

// 行内编辑状态
const editingCell = ref(null) // { id, field } | null
const editValues = reactive({}) // { [`${id}-${field}`]: value }

function startEdit(item, field) {
  const key = `${item.id}-${field}`
  editValues[key] = item[field] ?? ''
  editingCell.value = { id: item.id, field }
}

function cancelEdit() {
  editingCell.value = null
}

function saveEdit(item, field) {
  if (!editingCell.value || editingCell.value.id !== item.id || editingCell.value.field !== field) return
  const key = `${item.id}-${field}`
  const rawVal = editValues[key]
  
  let val = rawVal
  if (typeof rawVal === 'string') {
    const trimmed = rawVal.trim()
    if (trimmed === '') {
      val = null
    } else if (['cost_price', 'target_win', 'target_loss', 'entry_price_min', 'entry_price_max', 'last_grid_price'].includes(field)) {
      const num = parseFloat(trimmed)
      val = isNaN(num) ? null : num
    } else if (field === 'position') {
      const num = parseInt(trimmed, 10)
      val = isNaN(num) ? null : num
    }
  }

  emit('update-item', { id: item.id, payload: { [field]: val } })
  editingCell.value = null
}

// 格式化工具
function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}

function fmtPct(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}

function fmtPnl(v) {
  if (v == null || isNaN(v)) return '--'
  const sign = v > 0 ? '+' : ''
  return sign + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtVolRatio(v) {
  if (v == null || isNaN(v) || v <= 0) return '--'
  return Number(v).toFixed(2)
}

// 策略建议生成算法 (机构专业风)
function getStrategyAdvice(w) {
  if (!w) return null
  const isEmpty = (w.position == null) || (Number(w.position) <= 0)
  const price = w.price != null ? Number(w.price) : null

  // 1. 理想建仓机会 (空仓 + 价格落在区间内)
  if (isEmpty && w.is_entry_opportunity && w.entry_price_min != null && w.entry_price_max != null) {
    return {
      type: 'entry',
      badgeClass: 'bg-warn-subtle text-warn font-semibold',
      label: '🎯 触达建仓甜区',
      desc: `现价 ${fmtPrice(price)} 处于建议区间 [${fmtPrice(w.entry_price_min)}, ${fmtPrice(w.entry_price_max)}]`,
    }
  }

  // 2. 网格动态触发
  if (w.is_grid_buy) {
    return {
      type: 'grid-buy',
      badgeClass: 'bg-up-subtle text-up font-semibold',
      label: '🪜 触发网格加仓',
      desc: `较基准回撤 ${Math.abs(w.grid_distance || 0).toFixed(1)}% ≥ 步长 ${w.eff_grid_step_pct}%`,
    }
  }
  if (w.is_grid_sell && !isEmpty) {
    return {
      type: 'grid-sell',
      badgeClass: 'bg-down-subtle text-down font-semibold',
      label: '🪜 触发网格减仓',
      desc: `较基准涨幅 +${Math.abs(w.grid_distance || 0).toFixed(1)}% ≥ 步长 ${w.eff_grid_step_pct}%`,
    }
  }

  // 3. 空仓等待
  if (isEmpty) {
    if (w.signal?.signals?.is_volume_breakout) {
      return {
        type: 'breakout',
        badgeClass: 'bg-up-subtle text-up',
        label: '📈 空仓放量突破',
        desc: '检测到放量阳线信号，可关注建仓契机',
      }
    }
    return {
      type: 'empty',
      badgeClass: 'bg-slate-800 text-slate-400 border border-slate-700',
      label: '👀 空仓观望中',
      desc: '暂无持仓，等待最佳买入时机',
    }
  }

  // 4. 持仓状态下的止盈 / 止损判断
  if (w.note_target_broken) {
    return {
      type: 'stop-loss',
      badgeClass: 'bg-red-900/80 text-red-100 border border-red-500',
      label: '🛑 破止损线 · 建议清仓',
      desc: `现价跌破止损位 ${fmtPrice(w.eff_target_loss)}`,
    }
  }
  if (w.note_target_reached) {
    return {
      type: 'take-profit',
      badgeClass: 'bg-emerald-900/80 text-emerald-100 border border-emerald-500',
      label: '🎯 达止盈线 · 注意落袋',
      desc: `现价触达目标位 ${fmtPrice(w.eff_target_win)}`,
    }
  }

  // 5. 接近警戒线 (距止损 < 5%)
  if (w.eff_target_loss && price && price > w.eff_target_loss) {
    const distPct = ((price - w.eff_target_loss) / w.eff_target_loss) * 100
    if (distPct < 5) {
      return {
        type: 'warn',
        badgeClass: 'bg-warn-subtle text-warn',
        label: `⚠️ 距止损仅 ${distPct.toFixed(1)}%`,
        desc: `临近止损防守点 ${fmtPrice(w.eff_target_loss)}`,
      }
    }
  }

  // 6. 普通量价信号
  if (w.signal?.signals?.is_volume_breakout) {
    return {
      type: 'breakout',
      badgeClass: 'bg-up-subtle text-up',
      label: '📈 放量突破中',
      desc: '量比突破2.5倍且涨幅>3%',
    }
  }
  if (w.signal?.signals?.is_shrinking_pullback) {
    return {
      type: 'pullback',
      badgeClass: 'bg-down-subtle text-down',
      label: '📉 缩量回踩企稳',
      desc: '缩量震荡企稳特征',
    }
  }

  // 7. 多梯级个性化持仓盈亏策略指示
  if (w.return_rate != null) {
    const r = Number(w.return_rate)
    if (r >= 15) {
      return {
        type: 'high-profit',
        badgeClass: 'bg-emerald-950 text-emerald-300 border border-emerald-600 font-semibold',
        label: `🌟 丰厚浮盈 +${r.toFixed(1)}%`,
        desc: '收益丰厚，建议主动上移止损线至成本线上方防利润回吐',
      }
    }
    if (r > 3) {
      return {
        type: 'profit',
        badgeClass: 'bg-up-subtle text-up font-semibold',
        label: `✅ 浮盈中 +${r.toFixed(1)}%`,
        desc: '趋势良性，按既定纪律持股，盯紧上方目标位',
      }
    }
    if (r >= -3 && r <= 3) {
      return {
        type: 'cost-zone',
        badgeClass: 'bg-slate-800 text-slate-200 border border-slate-600',
        label: `⚖️ 成本区 ${r >= 0 ? '+' : ''}${r.toFixed(1)}%`,
        desc: '现价在持仓成本线附近震荡，等待放量选择方向',
      }
    }
    if (r > -8) {
      return {
        type: 'slight-loss',
        badgeClass: 'bg-amber-950/60 text-amber-300 border border-amber-800',
        label: `🟡 浮亏调整 ${r.toFixed(1)}%`,
        desc: '短线回调回踩，关注下方均线支撑与量能萎缩状态',
      }
    }
    return {
      type: 'deep-loss',
      badgeClass: 'bg-red-950 text-red-300 border border-red-700 font-semibold',
      label: `🛡️ 深度浮亏 ${r.toFixed(1)}%`,
      desc: '浮亏较大，严格盯防止损防守位，切忌盲目主观抗单',
    }
  }

  return null
}

// 汇总统计计算
const portfolioSummary = computed(() => {
  let positionCount = 0
  let winnersCount = 0
  let losersCount = 0
  let totalCost = 0
  let totalMarket = 0
  let totalFloatingPnl = 0

  for (const item of props.items) {
    const pos = Number(item.position || 0)
    const cost = Number(item.cost_price || 0)
    const price = Number(item.price || 0)

    if (pos > 0 && cost > 0) {
      positionCount++
      const pnl = (price > 0 ? (price - cost) * pos : (item.floating_pnl || 0))
      totalCost += cost * pos
      totalMarket += (price > 0 ? price : cost) * pos
      totalFloatingPnl += pnl

      if (pnl > 0) winnersCount++
      else if (pnl < 0) losersCount++
    }
  }

  const totalReturnRate = totalCost > 0 ? (totalFloatingPnl / totalCost) * 100 : null

  return {
    totalItems: props.items.length,
    positionCount,
    winnersCount,
    losersCount,
    totalCost: totalCost.toFixed(2),
    totalMarket: totalMarket.toFixed(2),
    totalFloatingPnl: totalFloatingPnl.toFixed(2),
    totalReturnRate: totalReturnRate != null ? totalReturnRate.toFixed(2) : null,
  }
})
</script>

<template>
  <div class="rpt-panel mb-4 overflow-hidden">
    <!-- 头部栏：标题与汇总指标 -->
    <div class="p-3 bg-[#131c2e] border-b border-slate-700/80 flex flex-col md:flex-row items-start md:items-center justify-between gap-2.5">
      <div class="flex items-center gap-2">
        <span class="text-sm font-bold text-slate-100">自选与持仓实时监控网格</span>
        <span class="text-xs font-mono text-slate-400">共 {{ props.items.length }} 标的 ({{ portfolioSummary.positionCount }} 持仓)</span>
      </div>

      <!-- 持仓统计指标 -->
      <div v-if="portfolioSummary.positionCount > 0" class="flex items-center gap-4 text-xs font-mono">
        <div>
          <span class="text-slate-400">持仓市值: </span>
          <span class="text-slate-100 font-semibold">¥{{ Number(portfolioSummary.totalMarket).toLocaleString() }}</span>
        </div>
        <div>
          <span class="text-slate-400">浮动盈亏: </span>
          <span 
            class="font-bold"
            :class="portfolioSummary.totalFloatingPnl >= 0 ? 'text-up' : 'text-down'"
          >
            {{ portfolioSummary.totalFloatingPnl >= 0 ? '+' : '' }}{{ Number(portfolioSummary.totalFloatingPnl).toLocaleString() }}
            ({{ portfolioSummary.totalReturnRate >= 0 ? '+' : '' }}{{ portfolioSummary.totalReturnRate }}%)
          </span>
        </div>
        <div class="hidden sm:inline text-slate-400">
          胜率: <span class="text-up">{{ portfolioSummary.winnersCount }}盈</span> / <span class="text-down">{{ portfolioSummary.losersCount }}亏</span>
        </div>
      </div>
    </div>

    <!-- 表格本体 -->
    <div class="overflow-x-auto">
      <table class="rpt-table">
        <thead>
          <tr>
            <th class="cursor-pointer" @click="emit('toggle-sort', 'ts_code')">
              代码 / 名称 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'ts_code' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'price')">
              现价 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'price' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'change_pct')">
              涨跌幅 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'change_pct' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'volume_ratio')">
              5日量比 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'volume_ratio' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'position')">
              持仓 (股) <span class="text-[10px] text-slate-500">{{ props.sortKey === 'position' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'cost_price')">
              成本 (元) <span class="text-[10px] text-slate-500">{{ props.sortKey === 'cost_price' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'floating_pnl')">
              浮动盈亏 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'floating_pnl' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th class="text-right cursor-pointer" @click="emit('toggle-sort', 'return_rate')">
              收益率 <span class="text-[10px] text-slate-500">{{ props.sortKey === 'return_rate' ? (props.sortDir === 'asc' ? '↑' : '↓') : '↕' }}</span>
            </th>
            <th>建仓甜区 / 止盈止损</th>
            <th>策略指示与执行</th>
            <th class="text-center">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="props.items.length === 0">
            <td colspan="11" class="text-center py-8 text-slate-500">
              暂无自选监控标的，请点击上方「➕ 添加自选」添加
            </td>
          </tr>

          <tr 
            v-for="item in props.items" 
            :key="item.id"
            :class="{ 'row-active': item.is_entry_opportunity || item.note_target_broken }"
          >
            <!-- 代码与名称 -->
            <td>
              <div class="flex items-center gap-2">
                <button 
                  @click="emit('open-chart', item)"
                  class="font-mono font-bold text-blue-400 hover:text-blue-300 hover:underline"
                  title="点击查看 K线走势"
                >
                  {{ item.ts_code }}
                </button>
                <span class="font-medium text-slate-200 text-xs">
                  {{ item.name || item.name_from_market || '--' }}
                </span>
              </div>
            </td>

            <!-- 现价 -->
            <td class="text-right font-mono font-semibold" :class="item.change_pct > 0 ? 'text-up' : (item.change_pct < 0 ? 'text-down' : 'text-flat')">
              {{ fmtPrice(item.price) }}
            </td>

            <!-- 涨跌幅 -->
            <td class="text-right font-mono font-semibold" :class="item.change_pct > 0 ? 'text-up' : (item.change_pct < 0 ? 'text-down' : 'text-flat')">
              <span class="inline-block px-1 py-0.5 rounded" :class="item.change_pct > 0 ? 'bg-up-subtle' : (item.change_pct < 0 ? 'bg-down-subtle' : '')">
                {{ fmtPct(item.change_pct) }}
              </span>
            </td>

            <!-- 量比 -->
            <td class="text-right font-mono text-xs">
              <span :class="item.volume_ratio > 2.0 ? 'text-up font-bold' : (item.volume_ratio < 0.8 && item.volume_ratio > 0 ? 'text-down' : 'text-slate-400')">
                {{ fmtVolRatio(item.volume_ratio) }}
              </span>
            </td>

            <!-- 持仓股数 (可行内编辑) -->
            <td class="text-right font-mono text-xs cursor-pointer hover:bg-slate-800/80" @click="startEdit(item, 'position')">
              <template v-if="editingCell?.id === item.id && editingCell?.field === 'position'">
                <input 
                  v-model="editValues[`${item.id}-position`]"
                  type="number"
                  class="rpt-input rpt-input-sm w-20 text-right font-mono"
                  @blur="saveEdit(item, 'position')"
                  @keyup.enter="saveEdit(item, 'position')"
                  @keyup.esc="cancelEdit"
                  autofocus
                />
              </template>
              <template v-else>
                <span :class="item.position > 0 ? 'text-slate-100 font-semibold' : 'text-slate-600'">
                  {{ item.position ? Number(item.position).toLocaleString() : '--' }}
                </span>
              </template>
            </td>

            <!-- 成本价 (可行内编辑) -->
            <td class="text-right font-mono text-xs cursor-pointer hover:bg-slate-800/80" @click="startEdit(item, 'cost_price')">
              <template v-if="editingCell?.id === item.id && editingCell?.field === 'cost_price'">
                <input 
                  v-model="editValues[`${item.id}-cost_price`]"
                  type="number"
                  step="0.001"
                  class="rpt-input rpt-input-sm w-20 text-right font-mono"
                  @blur="saveEdit(item, 'cost_price')"
                  @keyup.enter="saveEdit(item, 'cost_price')"
                  @keyup.esc="cancelEdit"
                  autofocus
                />
              </template>
              <template v-else>
                <span :class="item.cost_price ? 'text-slate-200' : 'text-slate-600'">
                  {{ fmtPrice(item.cost_price) }}
                </span>
              </template>
            </td>

            <!-- 浮动盈亏 -->
            <td class="text-right font-mono font-semibold" :class="item.floating_pnl > 0 ? 'text-up' : (item.floating_pnl < 0 ? 'text-down' : 'text-slate-600')">
              {{ item.position > 0 && item.cost_price ? fmtPnl(item.floating_pnl) : '--' }}
            </td>

            <!-- 收益率 -->
            <td class="text-right font-mono font-semibold text-xs" :class="item.return_rate > 0 ? 'text-up' : (item.return_rate < 0 ? 'text-down' : 'text-slate-600')">
              {{ item.position > 0 && item.cost_price ? fmtPct(item.return_rate) : '--' }}
            </td>

            <!-- 建仓甜区与止盈止损 -->
            <td class="text-xs">
              <div class="flex flex-col gap-0.5">
                <!-- 建仓区间 -->
                <div v-if="item.entry_price_min || item.entry_price_max" class="flex items-center gap-1 font-mono text-[11px]">
                  <span class="text-amber-400">甜区:</span>
                  <span class="text-slate-300">[{{ fmtPrice(item.entry_price_min) }} ~ {{ fmtPrice(item.entry_price_max) }}]</span>
                </div>
                <!-- 止盈止损 -->
                <div v-if="item.eff_target_win || item.eff_target_loss" class="flex items-center gap-2 font-mono text-[11px]">
                  <span v-if="item.eff_target_win" class="text-emerald-400">止盈:{{ fmtPrice(item.eff_target_win) }}</span>
                  <span v-if="item.eff_target_loss" class="text-red-400">止损:{{ fmtPrice(item.eff_target_loss) }}</span>
                </div>
                <div v-if="!item.entry_price_min && !item.eff_target_win && !item.eff_target_loss" class="text-slate-600">
                  未设计划 (点🤖规划)
                </div>
              </div>
            </td>

            <!-- 策略指示与建议 -->
            <td>
              <div v-if="getStrategyAdvice(item)" class="flex flex-col gap-1 items-start">
                <span 
                  class="rpt-badge"
                  :class="getStrategyAdvice(item).badgeClass"
                  :title="getStrategyAdvice(item).desc"
                >
                  {{ getStrategyAdvice(item).label }}
                </span>
                <span class="text-[11px] text-slate-400 line-clamp-1 max-w-[200px]" :title="item.trade_note || getStrategyAdvice(item).desc">
                  {{ item.trade_note || getStrategyAdvice(item).desc }}
                </span>
              </div>
              <span v-else class="text-slate-600 text-xs">--</span>
            </td>

            <!-- 操作按钮 -->
            <td class="text-center">
              <div class="flex items-center justify-center gap-1">
                <button 
                  @click="emit('open-chart', item)"
                  class="rpt-btn rpt-btn-sm text-blue-300 hover:text-white"
                  title="查看图表"
                >
                  📈
                </button>
                <button 
                  @click="emit('open-ai-plan', item)"
                  class="rpt-btn rpt-btn-sm text-purple-300 hover:text-white"
                  title="AI 智能建仓规划"
                >
                  🤖
                </button>
                <button 
                  @click="emit('open-trade-exec', item)"
                  class="rpt-btn rpt-btn-sm text-emerald-300 hover:text-white"
                  title="真实交割记账"
                >
                  ⚖️
                </button>
                <button 
                  @click="emit('delete-item', item.id)"
                  class="rpt-btn rpt-btn-sm text-slate-500 hover:text-red-400"
                  title="删除自选"
                >
                  ✕
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
