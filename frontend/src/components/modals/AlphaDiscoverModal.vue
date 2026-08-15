<script setup>
const props = defineProps({
  show: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  loadingStep: { type: Number, default: 0 },
  result: { type: Object, default: null }, // { discoveries, model, generated_at, meta }
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'refresh', 'open-chart', 'add-to-watchlist'])

const LOADING_STEPS = [
  '正在扫描 7x24 政策催化与重磅行业事件驱动日程…',
  '正在全市场 5,000+ 标的中筛选底部蓄势与缩量企稳标的…',
  '正在推演具备爆发预期差的低位买点甜区与盈亏比…',
]

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-4xl max-h-[88vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <span class="text-sm font-bold text-purple-300 flex items-center gap-1.5">
            🔭 前瞻 Alpha 掘金 · 低位埋伏与事件驱动决策矩阵
          </span>
          <span v-if="props.result?.model" class="text-[11px] font-mono px-1.5 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-800">
            {{ props.result.model }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button 
            v-if="!props.loading" 
            @click="emit('refresh')" 
            class="rpt-btn rpt-btn-sm text-slate-300 hover:text-white"
          >
            ⟳ 重新推演
          </button>
          <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
            ✕
          </button>
        </div>
      </div>

      <!-- 内容区 -->
      <div class="p-4 overflow-y-auto space-y-3.5 bg-[#0d1322] text-xs">
        <!-- 错误提示 -->
        <div v-if="props.error" class="p-3 rounded bg-red-950/80 border border-red-500/50 text-red-200">
          {{ props.error }}
        </div>

        <!-- 推演中 -->
        <div v-if="props.loading" class="py-14 text-center space-y-3">
          <div class="inline-block animate-spin h-7 w-7 border-2 border-purple-500 border-t-transparent rounded-full"></div>
          <div class="text-sm font-semibold text-purple-300 font-mono">
            {{ LOADING_STEPS[props.loadingStep % 3] }}
          </div>
          <div class="text-slate-500 text-xs">
            拒绝事后追高解释 · 聚焦低位蓄势标的与前瞻政策催化预期差
          </div>
        </div>

        <!-- 结果展示 -->
        <template v-else-if="props.result?.discoveries?.length">
          <!-- 元信息条 -->
          <div v-if="props.result.meta" class="p-2 bg-slate-900/80 rounded border border-slate-800 font-mono text-[11px] text-slate-400 flex items-center justify-between flex-wrap gap-2">
            <span>
              💡 挖掘底账：融合 {{ props.result.meta.news_count }} 条核心政策快讯、{{ props.result.meta.sectors_count }} 个主力资金流入板块、{{ props.result.meta.low_accum_count }} 只低位温和蓄势标的与热点扩散逻辑
            </span>
            <span>{{ props.result.generated_at }}</span>
          </div>

          <!-- 3 个前瞻埋伏方向卡片 -->
          <div 
            v-for="(item, idx) in props.result.discoveries" 
            :key="idx"
            class="rpt-panel-sub p-4 space-y-3 border-l-4"
            :class="item.level === '高' ? 'border-l-purple-500 bg-purple-950/15' : 'border-l-blue-500 bg-blue-950/15'"
          >
            <!-- 题材头部与状态标签 -->
            <div class="flex items-center justify-between flex-wrap gap-2 pb-1 border-b border-slate-800">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
                  <span class="text-purple-400 font-mono">#{{ idx + 1 }}</span>
                  {{ item.sector }}
                </span>
                <span class="rpt-badge bg-purple-900/80 text-purple-200 border border-purple-600 font-semibold">
                  🏷️ {{ item.ambush_type || '政策催化左侧潜伏' }}
                </span>
                <span class="rpt-badge bg-blue-950 text-blue-300 border border-blue-700 font-mono">
                  ⏱️ 预判窗口：{{ item.catalyst_window || '未来 1-3 个交易日' }}
                </span>
              </div>

              <span 
                class="rpt-badge font-mono font-semibold"
                :class="item.level === '高' ? 'bg-purple-950 text-purple-300 border border-purple-600' : 'bg-slate-800 text-slate-300 border border-slate-700'"
              >
                爆发确定性：{{ item.level }}
              </span>
            </div>

            <!-- 前瞻催化与预期差逻辑 -->
            <div class="space-y-1">
              <div class="text-[11px] font-bold text-amber-300 flex items-center gap-1">
                💡 前瞻催化逻辑与市场预期差：
              </div>
              <p class="text-slate-200 leading-relaxed text-xs bg-slate-900/60 p-2.5 rounded border border-slate-800">
                {{ item.catalyst_logic }}
              </p>
            </div>

            <!-- 低位技术特征 -->
            <div v-if="item.technical_pattern" class="text-slate-400 text-[11px] flex items-start gap-1">
              <span class="text-slate-300 font-semibold shrink-0">📊 低位蓄势特征:</span>
              <span>{{ item.technical_pattern }}</span>
            </div>

            <!-- 🎯 低位最具爆发弹性埋伏标的矩阵 -->
            <div class="space-y-1.5 pt-1">
              <div class="text-[11px] font-bold text-emerald-400 flex items-center justify-between">
                <span>🎯 重点低位埋伏标的与买点甜区：</span>
                <span class="text-[10px] font-normal text-slate-400">点击标的快速查看 K 线走势</span>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
                <div 
                  v-for="stk in item.stocks" 
                  :key="stk.code"
                  class="bg-slate-900/90 p-2.5 rounded border border-slate-700/80 hover:border-blue-500/80 transition flex flex-col justify-between space-y-2"
                >
                  <div>
                    <div class="flex items-center justify-between">
                      <button 
                        @click="emit('open-chart', { ts_code: stk.code, name: stk.name })"
                        class="font-mono font-bold text-blue-300 hover:text-white hover:underline flex items-center gap-1 text-xs"
                      >
                        {{ stk.name }} ({{ stk.code }}) 📈
                      </button>
                      <span v-if="stk.current_price" class="font-mono font-bold text-slate-200 text-xs">
                        ¥{{ fmtPrice(stk.current_price) }}
                      </span>
                    </div>

                    <!-- 埋伏买点区间 -->
                    <div v-if="stk.ambush_zone" class="mt-1 flex items-center gap-1 text-[11px] font-mono text-amber-300">
                      <span class="text-slate-400">建议低吸甜区:</span>
                      <span class="font-semibold">[¥{{ fmtPrice(stk.ambush_zone[0]) }} ~ ¥{{ fmtPrice(stk.ambush_zone[1]) }}]</span>
                    </div>

                    <!-- 目标位与止损位 -->
                    <div v-if="stk.target_win || stk.stop_loss" class="flex items-center gap-2 text-[11px] font-mono mt-0.5">
                      <span v-if="stk.target_win" class="text-up">止盈: ¥{{ fmtPrice(stk.target_win) }}</span>
                      <span v-if="stk.stop_loss" class="text-down">止损: ¥{{ fmtPrice(stk.stop_loss) }}</span>
                    </div>

                    <!-- 个股专属埋伏逻辑 -->
                    <p class="text-[11px] text-slate-400 line-clamp-2 mt-1">
                      {{ stk.stock_logic }}
                    </p>
                  </div>

                  <!-- 快捷加自选按钮 -->
                  <div class="pt-1.5 border-t border-slate-800 flex items-center justify-between">
                    <button 
                      @click="emit('open-chart', { ts_code: stk.code, name: stk.name })"
                      class="text-[10px] text-blue-400 hover:text-blue-200"
                    >
                      查看K线
                    </button>
                    <button 
                      @click="emit('add-to-watchlist', { ts_code: stk.code, name: stk.name, entry_price_min: stk.ambush_zone?.[0], entry_price_max: stk.ambush_zone?.[1], target_win: stk.target_win, target_loss: stk.stop_loss, trade_note: stk.stock_logic })"
                      class="text-[10px] px-2 py-0.5 rounded bg-blue-900/60 hover:bg-blue-800 text-blue-200 border border-blue-700"
                    >
                      ➕ 设为自选监控
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <!-- ⚠️ 风控与撤退纪律 -->
            <div v-if="item.risk_warning" class="p-2 bg-red-950/20 rounded border border-red-900/30 text-[11px] text-red-300 flex items-start gap-1">
              <span class="font-bold shrink-0">⚠️ 撤退纪律:</span>
              <span>{{ item.risk_warning }}</span>
            </div>
          </div>
        </template>

        <div v-else class="text-center py-12 text-slate-500">
          暂无前瞻埋伏挖掘结果，请点击右上角重新推演
        </div>
      </div>
    </div>
  </div>
</template>
