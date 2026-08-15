<script setup>
import { computed } from 'vue'

const props = defineProps({
  lastUpdated: { type: Number, default: null },
  loading: { type: Boolean, default: false },
  canNotify: { type: Boolean, default: false },
  sentiment: { type: Object, default: null },
})

const emit = defineEmits([
  'refresh',
  'open-summary',
  'open-alpha',
  'open-ledger',
  'toggle-add-form',
  'request-notify',
])

function fmtTimeAgo(ts) {
  if (!ts) return '初始化中'
  const s = Math.floor((Date.now() - ts) / 1000)
  if (s < 5) return '刚刚'
  if (s < 60) return `${s}秒前`
  return `${Math.floor(s / 60)}分钟前`
}

const marketStatus = computed(() => {
  const d = new Date()
  const day = d.getDay()
  if (day === 0 || day === 6) return { label: '周末休市', code: 'closed' }
  const hour = d.getHours()
  const min = d.getMinutes()
  const totalMin = hour * 60 + min

  if (totalMin < 9 * 60 + 15) return { label: '盘前等待', code: 'pre' }
  if (totalMin <= 9 * 60 + 25) return { label: '集合竞价', code: 'call' }
  if (totalMin <= 11 * 60 + 30) return { label: '早盘交易中', code: 'trading' }
  if (totalMin < 13 * 60) return { label: '午间休市', code: 'lunch' }
  if (totalMin <= 15 * 60) return { label: '午盘交易中', code: 'trading' }
  return { label: '已收盘', code: 'closed' }
})
</script>

<template>
  <header class="rpt-panel p-3.5 mb-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 border-b-2 border-slate-700/60">
    <!-- 左侧：系统标识与盘面状态 -->
    <div class="flex items-center gap-4 flex-wrap">
      <div class="flex items-center gap-2.5">
        <div class="w-2.5 h-6 bg-blue-600 rounded-xs"></div>
        <div>
          <h1 class="text-base md:text-lg font-bold text-slate-100 tracking-wider flex items-center gap-2">
            A股量价情绪监控终端
            <span class="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400">v4.2 PRO</span>
          </h1>
        </div>
      </div>

      <!-- 市场运行状态指示 -->
      <div class="flex items-center gap-2 text-xs border-l border-slate-700/80 pl-3.5">
        <div class="flex items-center gap-1.5">
          <span 
            class="inline-block w-2 h-2 rounded-full"
            :class="marketStatus.code === 'trading' ? 'bg-emerald-500 animate-pulse' : 'bg-slate-500'"
          ></span>
          <span class="font-medium text-slate-300">{{ marketStatus.label }}</span>
        </div>
        <span class="text-slate-600">|</span>
        <span class="text-slate-400 font-mono">
          更新于 {{ fmtTimeAgo(props.lastUpdated) }}
        </span>
        <span v-if="props.loading" class="text-blue-400 text-xs flex items-center gap-1 font-mono">
          <svg class="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
          </svg>
          同步中
        </span>
      </div>
    </div>

    <!-- 右侧：功能按钮工具栏 -->
    <div class="flex items-center gap-2 flex-wrap self-end md:self-auto">
      <button
        v-if="!props.canNotify"
        @click="emit('request-notify')"
        class="rpt-btn rpt-btn-sm text-slate-300 hover:text-amber-300"
        title="开启浏览器桌面预警通知"
      >
        🔔 开启桌面通知
      </button>

      <button
        @click="emit('open-alpha')"
        class="rpt-btn rpt-btn-sm bg-purple-950/60 border-purple-600/50 text-purple-200 hover:bg-purple-900/60 hover:text-white"
        title="AI 游资共振挖掘"
      >
        🔭 发现 Alpha
      </button>

      <button
        @click="emit('open-summary')"
        class="rpt-btn rpt-btn-sm bg-amber-950/40 border-amber-600/40 text-amber-200 hover:bg-amber-900/50 hover:text-white"
        title="查看今日 A 股盘后战报"
      >
        📝 今日复盘
      </button>

      <button
        @click="emit('open-ledger')"
        class="rpt-btn rpt-btn-sm bg-emerald-950/40 border-emerald-600/40 text-emerald-200 hover:bg-emerald-900/50 hover:text-white"
        title="查看交割记录与资金账本"
      >
        💰 资金账本
      </button>

      <button
        @click="emit('toggle-add-form')"
        class="rpt-btn rpt-btn-sm bg-slate-800 text-slate-200 hover:bg-slate-700"
        title="展开/收起添加自选股表单"
      >
        ➕ 添加自选
      </button>

      <button
        @click="emit('refresh')"
        :disabled="props.loading"
        class="rpt-btn rpt-btn-sm rpt-btn-primary"
      >
        ⟳ 手动刷新
      </button>
    </div>
  </header>
</template>
