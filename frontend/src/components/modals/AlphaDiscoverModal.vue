<script setup>
const props = defineProps({
  show: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  loadingStep: { type: Number, default: 0 },
  result: { type: Object, default: null }, // { discoveries, model, generated_at, meta }
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'refresh', 'open-chart'])

const LOADING_STEPS = [
  '正在扫描全市场 5,000+ 标的量价异动…',
  '正在深度解析 7x24 财经政策与行业催化…',
  '正在计算技术量能与消息面共振节点…',
]
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-3xl max-h-[85vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-sm font-bold text-purple-300 flex items-center gap-1.5">
            🔭 Alpha 共振挖掘 · 短线游资决策矩阵
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
            ⟳ 重新扫描
          </button>
          <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
            ✕
          </button>
        </div>
      </div>

      <!-- 内容区 -->
      <div class="p-4 overflow-y-auto space-y-3 bg-[#0d1322] text-xs">
        <!-- 错误提示 -->
        <div v-if="props.error" class="p-3 rounded bg-red-950/80 border border-red-500/50 text-red-200">
          {{ props.error }}
        </div>

        <!-- 加载中 -->
        <div v-if="props.loading" class="py-14 text-center space-y-3">
          <div class="inline-block animate-spin h-7 w-7 border-2 border-purple-500 border-t-transparent rounded-full"></div>
          <div class="text-sm font-semibold text-purple-300 font-mono">
            {{ LOADING_STEPS[props.loadingStep % 3] }}
          </div>
          <div class="text-slate-500 text-xs">AI 正在进行多维度技术资金与消息催化融合匹配</div>
        </div>

        <!-- 结果展示 -->
        <template v-else-if="props.result?.discoveries?.length">
          <!-- 元信息条 -->
          <div v-if="props.result.meta" class="p-2 bg-slate-900/80 rounded border border-slate-800 font-mono text-[11px] text-slate-400 flex items-center justify-between flex-wrap gap-2">
            <span>扫描依据：{{ props.result.meta.gainers_count }} 涨幅股 / {{ props.result.meta.volume_count }} 放量股 / {{ props.result.meta.sectors_count }} 资金板块 / {{ props.result.meta.news_count }} 核心快讯</span>
            <span>{{ props.result.generated_at }}</span>
          </div>

          <!-- 3 个共振方向卡片 -->
          <div 
            v-for="(item, idx) in props.result.discoveries" 
            :key="idx"
            class="rpt-panel-sub p-3.5 space-y-2 border-l-4"
            :class="item.level === '高' ? 'border-l-purple-500 bg-purple-950/20' : 'border-l-blue-500 bg-blue-950/20'"
          >
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-xs font-bold text-slate-100">{{ item.sector }}</span>
                <span 
                  class="rpt-badge font-mono"
                  :class="item.level === '高' ? 'bg-purple-900 text-purple-200 border border-purple-600' : 'bg-blue-900 text-blue-200 border border-blue-600'"
                >
                  共振强度：{{ item.level }}
                </span>
              </div>
            </div>

            <!-- 共振逻辑 -->
            <p class="text-slate-300 leading-relaxed text-xs">
              {{ item.logic }}
            </p>

            <!-- 代表个股 -->
            <div class="flex items-center gap-2 flex-wrap pt-1 border-t border-slate-800/80">
              <span class="text-slate-400 text-[11px]">代表标的（点击看盘）：</span>
              <button
                v-for="stk in item.stocks"
                :key="stk.code"
                @click="emit('open-chart', { ts_code: stk.code, name: stk.name })"
                class="rpt-tag bg-slate-800 hover:bg-slate-700 text-blue-300 hover:text-white border border-slate-700 cursor-pointer font-mono"
              >
                {{ stk.name }} ({{ stk.code }}) 📈
              </button>
            </div>
          </div>
        </template>

        <div v-else class="text-center py-12 text-slate-500">
          暂无共振挖掘结果，请点击右上角重新扫描
        </div>
      </div>
    </div>
  </div>
</template>
