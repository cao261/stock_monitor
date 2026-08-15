<script setup>
const props = defineProps({
  show: { type: Boolean, default: false },
  summary: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  aiLoading: { type: Boolean, default: false },
  aiError: { type: String, default: '' },
})

const emit = defineEmits(['close', 'trigger-ai', 'open-chart'])

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
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-4xl max-h-[90vh]">
      <!-- 弹窗头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <span class="text-base font-bold text-slate-100 flex items-center gap-1.5">
            📝 今日 A 股复盘与实战内参
          </span>
          <span v-if="props.summary?.generated_at" class="text-xs font-mono text-slate-400">
            {{ props.summary.generated_at }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <!-- 召唤 AI 复盘按钮 -->
          <button
            @click="emit('trigger-ai')"
            :disabled="props.aiLoading"
            class="rpt-btn rpt-btn-sm bg-purple-900/60 border-purple-500/60 text-purple-200 hover:bg-purple-800"
          >
            {{ props.aiLoading ? '✨ AI 深度推演中…' : '✨ 召唤 AI 深度复盘' }}
          </button>
          <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
            ✕
          </button>
        </div>
      </div>

      <!-- 弹窗内容区 -->
      <div class="p-4 overflow-y-auto space-y-4 text-xs">
        <div v-if="props.aiError" class="p-2.5 rounded bg-red-950/80 border border-red-500/50 text-red-200">
          {{ props.aiError }}
        </div>

        <div v-if="props.loading" class="text-center py-12 text-slate-400">
          战报数据装载中…
        </div>

        <div v-else-if="!props.summary" class="text-center py-12 text-slate-500">
          暂无战报数据
        </div>

        <template v-else>
          <!-- 模块1：大盘情绪概况 -->
          <div class="rpt-panel-sub p-3.5 space-y-2">
            <div class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
              📊 大盘全景与情绪结构
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 font-mono">
              <div class="bg-slate-900/70 p-2.5 rounded border border-slate-800">
                <div class="text-slate-400 text-[11px]">大盘情绪分</div>
                <div class="text-lg font-bold text-amber-400">{{ props.summary.sentiment?.score }} 分</div>
              </div>
              <div class="bg-slate-900/70 p-2.5 rounded border border-slate-800">
                <div class="text-slate-400 text-[11px]">涨跌分布</div>
                <div class="text-sm font-semibold">
                  <span class="text-up">{{ props.summary.sentiment?.up_count }} 涨</span> / 
                  <span class="text-down">{{ props.summary.sentiment?.down_count }} 跌</span>
                </div>
              </div>
              <div class="bg-slate-900/70 p-2.5 rounded border border-slate-800">
                <div class="text-slate-400 text-[11px]">涨跌停极端数</div>
                <div class="text-sm font-semibold">
                  <span class="text-up">{{ props.summary.sentiment?.limit_up_count }} 涨停</span> / 
                  <span class="text-down">{{ props.summary.sentiment?.limit_down_count }} 跌停</span>
                </div>
              </div>
              <div class="bg-slate-900/70 p-2.5 rounded border border-slate-800">
                <div class="text-slate-400 text-[11px]">全市场上涨比</div>
                <div class="text-lg font-bold text-slate-200">
                  {{ (props.summary.sentiment?.up_ratio * 100).toFixed(1) }}%
                </div>
              </div>
            </div>
          </div>

          <!-- 模块2：自选股战况 -->
          <div class="rpt-panel-sub p-3.5 space-y-3">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-200 flex items-center gap-1.5">
                ⚔️ 自选与持仓战果盘点
              </span>
              <span class="font-mono text-xs">
                持仓盈亏: 
                <span :class="props.summary.watchlist_battle?.floating_pnl_total >= 0 ? 'text-up font-bold' : 'text-down font-bold'">
                  ¥{{ fmtPnl(props.summary.watchlist_battle?.floating_pnl_total) }}
                </span>
                <span v-if="props.summary.watchlist_battle?.total_return_rate != null">
                  ({{ props.summary.watchlist_battle.total_return_rate }}%)
                </span>
              </span>
            </div>

            <!-- 触发止盈/止损标签 -->
            <div v-if="props.summary.watchlist_battle?.take_profit_triggered?.length || props.summary.watchlist_battle?.stop_loss_triggered?.length" class="space-y-1.5">
              <div v-if="props.summary.watchlist_battle?.take_profit_triggered?.length" class="flex items-center gap-2 flex-wrap">
                <span class="rpt-badge bg-emerald-950 text-emerald-300 border border-emerald-700">🎯 达到止盈线:</span>
                <span 
                  v-for="stk in props.summary.watchlist_battle.take_profit_triggered" 
                  :key="stk.ts_code"
                  @click="emit('open-chart', stk)"
                  class="cursor-pointer hover:underline text-emerald-400 font-mono font-medium"
                >
                  {{ stk.name || stk.ts_code }} (¥{{ fmtPrice(stk.price) }} ≥ 目标 ¥{{ fmtPrice(stk.target_win) }})
                </span>
              </div>

              <div v-if="props.summary.watchlist_battle?.stop_loss_triggered?.length" class="flex items-center gap-2 flex-wrap">
                <span class="rpt-badge bg-red-950 text-red-300 border border-red-700">🛑 触及止损线:</span>
                <span 
                  v-for="stk in props.summary.watchlist_battle.stop_loss_triggered" 
                  :key="stk.ts_code"
                  @click="emit('open-chart', stk)"
                  class="cursor-pointer hover:underline text-red-400 font-mono font-medium"
                >
                  {{ stk.name || stk.ts_code }} (¥{{ fmtPrice(stk.price) }} ≤ 防守 ¥{{ fmtPrice(stk.target_loss) }})
                </span>
              </div>
            </div>

            <!-- 盈利榜 vs 亏损榜 -->
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                <div class="text-[11px] text-up font-bold mb-1.5">盈利前 5 标的</div>
                <div v-if="!props.summary.watchlist_battle?.winners?.length" class="text-slate-500 text-[11px]">无盈利标的</div>
                <div v-else class="space-y-1">
                  <div 
                    v-for="w in props.summary.watchlist_battle.winners" 
                    :key="w.ts_code"
                    @click="emit('open-chart', w)"
                    class="flex items-center justify-between font-mono text-[11px] cursor-pointer hover:bg-slate-800 p-1 rounded"
                  >
                    <span class="text-slate-300">{{ w.name || w.ts_code }}</span>
                    <span class="text-up font-semibold">+¥{{ fmtPrice(w.floating_pnl) }} ({{ fmtPct(w.return_rate) }})</span>
                  </div>
                </div>
              </div>

              <div class="bg-slate-900/60 p-2.5 rounded border border-slate-800">
                <div class="text-[11px] text-down font-bold mb-1.5">亏损前 5 标的</div>
                <div v-if="!props.summary.watchlist_battle?.losers?.length" class="text-slate-500 text-[11px]">无亏损标的</div>
                <div v-else class="space-y-1">
                  <div 
                    v-for="l in props.summary.watchlist_battle.losers" 
                    :key="l.ts_code"
                    @click="emit('open-chart', l)"
                    class="flex items-center justify-between font-mono text-[11px] cursor-pointer hover:bg-slate-800 p-1 rounded"
                  >
                    <span class="text-slate-300">{{ l.name || l.ts_code }}</span>
                    <span class="text-down font-semibold">-¥{{ fmtPrice(Math.abs(l.floating_pnl)) }} ({{ fmtPct(l.return_rate) }})</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 模块3：今日真实交割单 -->
          <div v-if="props.summary.today_trades?.total_count > 0" class="rpt-panel-sub p-3.5 space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-200">💰 今日真实交割单 (已实现盈亏)</span>
              <span class="font-mono text-xs" :class="props.summary.today_trades.total_realized_pnl >= 0 ? 'text-up font-bold' : 'text-down font-bold'">
                已实现合计: ¥{{ fmtPnl(props.summary.today_trades.total_realized_pnl) }}
              </span>
            </div>
            <div class="space-y-1">
              <div 
                v-for="t in props.summary.today_trades.trades" 
                :key="t.id"
                class="flex items-center justify-between font-mono text-[11px] bg-slate-900/70 p-1.5 rounded border border-slate-800"
              >
                <span class="text-slate-300">{{ t.ts_code }}</span>
                <span class="rpt-badge" :class="t.action === 'BUY' ? 'bg-up-subtle text-up' : 'bg-down-subtle text-down'">
                  {{ t.action === 'BUY' ? '买入建仓' : '卖出减仓' }} {{ t.volume }} 股 @ ¥{{ fmtPrice(t.price) }}
                </span>
                <span :class="t.realized_pnl > 0 ? 'text-up' : (t.realized_pnl < 0 ? 'text-down' : 'text-slate-400')">
                  {{ t.action === 'SELL' ? '实现: ¥' + fmtPnl(t.realized_pnl) : '--' }}
                </span>
              </div>
            </div>
          </div>

          <!-- 模块4：全市场异动龙头 -->
          <div class="rpt-panel-sub p-3.5 space-y-2">
            <div class="text-xs font-bold text-slate-200">🚀 全市场领涨与成交龙头</div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 font-mono">
              <div class="bg-slate-900/60 p-2 rounded border border-slate-800">
                <div class="text-[11px] text-up font-bold mb-1">全市场涨幅 Top 3</div>
                <div 
                  v-for="g in props.summary.top_movers?.by_change_pct" 
                  :key="g.code"
                  @click="emit('open-chart', { ts_code: g.code, name: g.name })"
                  class="flex items-center justify-between text-[11px] cursor-pointer hover:bg-slate-800 p-1 rounded"
                >
                  <span class="text-slate-300">{{ g.name || g.code }}</span>
                  <span class="text-up font-bold">{{ fmtPct(g.change_pct) }}</span>
                </div>
              </div>

              <div class="bg-slate-900/60 p-2 rounded border border-slate-800">
                <div class="text-[11px] text-amber-400 font-bold mb-1">全市场成交量 Top 3</div>
                <div 
                  v-for="v in props.summary.top_movers?.by_volume" 
                  :key="v.code"
                  @click="emit('open-chart', { ts_code: v.code, name: v.name })"
                  class="flex items-center justify-between text-[11px] cursor-pointer hover:bg-slate-800 p-1 rounded"
                >
                  <span class="text-slate-300">{{ v.name || v.code }}</span>
                  <span class="text-slate-300 font-semibold">{{ (v.volume_lots / 10000).toFixed(1) }} 万手</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
