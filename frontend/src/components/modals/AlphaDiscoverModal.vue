<script setup>
import { reactive } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  loadingStep: { type: Number, default: 0 },
  result: { type: Object, default: null }, // { discoveries, engine_type, engine_name, engine_desc, model, generated_at, meta }
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'refresh', 'open-chart', 'add-to-watchlist'])

const LOADING_STEPS = [
  '正在扫描 390 个概念板块：资金流向、60日涨幅、回撤与止跌结构…',
  '正在拉取板块指数历史K线，计算 MA20/MA60 粘合与量能收缩…',
  '正在匹配 7x24 快讯催化，推演左侧埋伏预期差与右侧启动信号…',
]

function fmtPrice(v) {
  if (v == null || isNaN(v)) return '--'
  return Number(v).toFixed(2)
}

function fmtSigned(v) {
  if (v == null || isNaN(v)) return '--'
  const n = Number(v)
  return (n >= 0 ? '+' : '') + n.toFixed(1)
}

function fmtAmount(v) {
  if (v == null || isNaN(v) || v <= 0) return '--'
  const n = Number(v)
  if (n >= 1e8) return (n / 1e8).toFixed(0) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(0) + '万'
  return n.toFixed(0)
}

function getScoreBadgeClass(score) {
  const s = Number(score || 75)
  if (s >= 85) return 'bg-red-950 text-red-200 border-red-600 font-bold'
  if (s >= 75) return 'bg-amber-950 text-amber-200 border-amber-600 font-bold'
  return 'bg-slate-800 text-slate-300 border-slate-600 font-medium'
}

// v4.4: LLM action 决策 (STRONG_BUY/BUY/WATCH/PASS) 颜色与标签
function getActionBadgeClass(action) {
  if (action === 'STRONG_BUY') return 'bg-red-950 text-red-200 border-red-500 animate-pulse font-bold'
  if (action === 'BUY') return 'bg-emerald-950 text-emerald-200 border-emerald-500 font-bold'
  if (action === 'WATCH') return 'bg-amber-950 text-amber-200 border-amber-500 font-semibold'
  return 'bg-slate-800 text-slate-400 border-slate-600'
}
function getActionIcon(action) {
  if (action === 'STRONG_BUY') return '🔥'
  if (action === 'BUY') return '✅'
  if (action === 'WATCH') return '👁'
  return '⛔'
}
function getActionLabel(action) {
  if (action === 'STRONG_BUY') return '强烈建仓'
  if (action === 'BUY') return '建仓 1/3'
  if (action === 'WATCH') return '观察池'
  return '放弃'
}
function getIntentLabel(intent) {
  return { accumulation: '吸筹', shakeout: '洗盘', markup: '拉升', distribution: '出货', consolidation: '震荡' }[intent] || intent
}

function getSignalBadgeClass(signal) {
  if (signal === '利多') return 'bg-emerald-950/70 text-emerald-200 border-emerald-700'
  if (signal === '利空') return 'bg-red-950/70 text-red-200 border-red-700'
  return 'bg-slate-800 text-slate-300 border-slate-700'
}

// v4.3: 每个方向的"详情展开"折叠状态。key = direction index
const expanded = reactive({})

function toggleDetail(idx) {
  expanded[idx] = !expanded[idx]
}

function hasDetail(item) {
  if (item.tech_indicators && item.tech_indicators.length) return true
  if (item.news_highlights && item.news_highlights.length) return true
  if (item.llm_verification?.t1_message) return true
  if (item.llm_verification?.t2_technical) return true
  if (item.llm_verification?.t3_cross) return true
  return false
}
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-4xl max-h-[88vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2.5">
          <span class="text-sm font-bold text-purple-300 flex items-center gap-1.5">
            🔭 前瞻 Alpha 掘金 · 板块级左侧埋伏（技术+资金+催化共振）
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
          <!-- 引擎来源与可信度说明条 -->
          <div 
            class="p-2.5 rounded border flex items-center justify-between flex-wrap gap-2 text-[11px]"
            :class="props.result.engine_type === 'fallback' 
              ? 'bg-amber-950/30 border-amber-800/60 text-amber-200' 
              : 'bg-purple-950/30 border-purple-800/60 text-purple-200'"
          >
            <div class="flex items-center gap-2">
              <span class="font-bold font-mono px-2 py-0.5 rounded"
                :class="props.result.engine_type === 'fallback' ? 'bg-amber-900 text-amber-100' : 'bg-purple-900 text-purple-100'"
              >
                {{ props.result.engine_name || (props.result.engine_type === 'fallback' ? '⚡ 量化规则低位筛选 (兜底引擎)' : '🤖 AI 深度前瞻研报') }}
              </span>
              <span>{{ props.result.engine_desc }}</span>
            </div>
            <span class="font-mono text-slate-400 text-[10px]">{{ props.result.generated_at }}</span>
          </div>

          <!-- 前瞻埋伏方向卡片列表 -->
          <div 
            v-for="(item, idx) in props.result.discoveries" 
            :key="idx"
            class="rpt-panel-sub p-4 space-y-3 border-l-4"
            :class="(item.score >= 85 || item.level === '高') ? 'border-l-red-500 bg-purple-950/15' : 'border-l-blue-500 bg-blue-950/15'"
          >
            <!-- 题材头部与状态标签 -->
            <div class="flex items-center justify-between flex-wrap gap-2 pb-1.5 border-b border-slate-800">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-sm font-bold text-slate-100 flex items-center gap-1.5">
                  <span class="text-purple-400 font-mono">#{{ idx + 1 }}</span>
                  {{ item.sector }}
                </span>

                <!-- 埋伏综合评分 -->
                <span
                  class="rpt-badge font-mono border"
                  :class="getScoreBadgeClass(item.score)"
                >
                  🎯 埋伏评分：{{ item.score || 75 }}分
                </span>

                <span class="rpt-badge bg-purple-900/80 text-purple-200 border border-purple-600 font-semibold">
                  🏷️ {{ item.ambush_type || '政策催化左侧潜伏' }}
                </span>
                <span class="rpt-badge bg-blue-950 text-blue-300 border border-blue-700 font-mono">
                  ⏱️ 预判窗口：{{ item.catalyst_window || '未来 1-3 个交易日' }}
                </span>
              </div>

              <div class="flex items-center gap-2">
                <!-- v4.4: LLM 验证后的 action 决策 -->
                <span
                  v-if="item.llm_verification?.action"
                  class="rpt-badge font-mono font-bold border"
                  :class="getActionBadgeClass(item.llm_verification.action)"
                  :title="`LLM 综合分 ${item.llm_verification.final_score} | 可信度 ${item.llm_verification.t3_cross?.trustworthiness || '—'}`"
                >
                  {{ getActionIcon(item.llm_verification.action) }} {{ getActionLabel(item.llm_verification.action) }}
                  <span v-if="item.llm_verification.final_score != null" class="text-[10px] ml-1 opacity-80">
                    {{ item.llm_verification.final_score }}分
                  </span>
                </span>
                <span
                  class="rpt-badge font-mono font-semibold"
                  :class="item.level === '高' ? 'bg-purple-950 text-purple-300 border border-purple-600' : 'bg-slate-800 text-slate-300 border border-slate-700'"
                >
                  确定性：{{ item.level }}
                </span>
              </div>
            </div>

            <!-- v4.4: 4 维评分 (TRADING_LOGIC 第 2.1 节) + 5 大左侧信号 -->
            <div v-if="item.score_4d" class="grid grid-cols-1 md:grid-cols-5 gap-2 text-[11px]">
              <!-- 4 维评分条 -->
              <div class="md:col-span-3 p-2 rounded bg-slate-950/40 border border-slate-800">
                <div class="text-[10px] text-slate-400 font-bold mb-1.5 flex items-center justify-between">
                  <span>📊 4 维量化评分</span>
                  <span class="font-mono text-slate-300">综合 {{ item.score_4d.total }} · {{ item.score_4d.grade }} 级</span>
                </div>
                <div class="space-y-1">
                  <div v-for="dim in [
                    {key: 'msg', label: '消息面', color: 'amber'},
                    {key: 'cap', label: '资金面', color: 'emerald'},
                    {key: 'tech', label: '技术面', color: 'sky'},
                    {key: 'sent', label: '情绪面', color: 'purple'},
                  ]" :key="dim.key" class="flex items-center gap-2">
                    <span class="w-12 text-slate-400">{{ dim.label }}</span>
                    <div class="flex-1 h-1.5 bg-slate-800 rounded overflow-hidden">
                      <div
                        class="h-full rounded transition-all"
                        :class="`bg-${dim.color}-500`"
                        :style="{ width: (item.score_4d[dim.key] || 0) + '%' }"
                      ></div>
                    </div>
                    <span class="w-10 text-right font-mono text-slate-300">{{ item.score_4d[dim.key] }}</span>
                  </div>
                </div>
              </div>

              <!-- 5 大左侧信号 -->
              <div class="md:col-span-2 p-2 rounded bg-slate-950/40 border border-slate-800">
                <div class="text-[10px] text-slate-400 font-bold mb-1.5 flex items-center justify-between">
                  <span>🎯 5 大左侧信号</span>
                  <span class="font-mono text-slate-300">触发 {{ item.left_signals?.filter(s => s.triggered).length || 0 }}/5</span>
                </div>
                <div v-if="!item.left_signals?.length" class="text-slate-500 text-[10px]">无信号触发</div>
                <div v-else class="space-y-1">
                  <div
                    v-for="sig in item.left_signals"
                    :key="sig.type"
                    class="text-[10px] flex items-start gap-1"
                    :class="sig.triggered ? 'text-emerald-300' : 'text-slate-500'"
                  >
                    <span class="shrink-0">{{ sig.triggered ? '✅' : '○' }}</span>
                    <span class="flex-1">
                      <span class="font-semibold">{{ sig.name }}</span>
                      <span v-if="sig.triggered && sig.conditions_met?.length" class="text-slate-400 block text-[9.5px]">
                        {{ sig.conditions_met.join(' · ') }}
                      </span>
                    </span>
                  </div>
                </div>
              </div>
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

            <!-- 低位技术特征与右侧质变信号 -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
              <div v-if="item.technical_pattern" class="p-2 bg-slate-900/60 rounded border border-slate-800/80 flex items-start gap-1">
                <span class="text-slate-300 font-semibold shrink-0">📊 低位蓄势形态:</span>
                <span class="text-slate-400">{{ item.technical_pattern }}</span>
              </div>
              <div v-if="item.breakout_trigger" class="p-2 bg-blue-950/30 rounded border border-blue-900/40 flex items-start gap-1">
                <span class="text-blue-300 font-semibold shrink-0">🚀 右侧质变信号:</span>
                <span class="text-blue-200">{{ item.breakout_trigger }}</span>
              </div>
            </div>

            <!-- v4.4: 板块指数级指标（60日涨幅/回撤/资金/成交） -->
            <div v-if="item.sector_metrics" class="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] font-mono">
              <div class="p-1.5 rounded bg-slate-950/50 border border-slate-800 flex flex-col items-center gap-0.5">
                <span class="text-[10px] text-slate-500">板块60日涨幅</span>
                <span :class="(item.sector_metrics.ret_60d ?? 0) >= 0 ? 'text-up' : 'text-down'">
                  {{ fmtSigned(item.sector_metrics.ret_60d) }}%
                </span>
              </div>
              <div class="p-1.5 rounded bg-slate-950/50 border border-slate-800 flex flex-col items-center gap-0.5">
                <span class="text-[10px] text-slate-500">距60日高点</span>
                <span class="text-down">{{ fmtSigned(item.sector_metrics.drawdown_pct) }}%</span>
              </div>
              <div class="p-1.5 rounded bg-slate-950/50 border border-slate-800 flex flex-col items-center gap-0.5">
                <span class="text-[10px] text-slate-500">主力净额(当日)</span>
                <span :class="(item.sector_metrics.net_amount ?? 0) >= 0 ? 'text-up' : 'text-down'">
                  {{ fmtSigned(item.sector_metrics.net_amount) }}亿
                </span>
              </div>
              <div class="p-1.5 rounded bg-slate-950/50 border border-slate-800 flex flex-col items-center gap-0.5">
                <span class="text-[10px] text-slate-500">趋势 / 成交额</span>
                <span class="text-slate-300">{{ item.sector_metrics.trend }} / {{ fmtAmount(item.sector_metrics.amount_last) }}</span>
              </div>
            </div>

            <!-- 🎯 低位最具爆发弹性埋伏标的矩阵（含真实支撑压力分析） -->
            <div class="space-y-1.5 pt-1">
              <div class="text-[11px] font-bold text-emerald-400 flex items-center justify-between">
                <span>🎯 重点低位埋伏标的与技术支撑压力矩阵：</span>
                <span class="text-[10px] font-normal text-slate-400">点击标的快速查看 K 线走势</span>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
                <div 
                  v-for="stk in item.stocks" 
                  :key="stk.code"
                  class="bg-slate-900/90 p-3 rounded border border-slate-700/80 hover:border-blue-500/80 transition flex flex-col justify-between space-y-2.5"
                >
                  <div class="space-y-1.5">
                    <!-- 代码名称与价格 -->
                    <div class="flex items-center justify-between">
                      <button 
                        @click="emit('open-chart', { ts_code: stk.code, name: stk.name })"
                        class="font-mono font-bold text-blue-300 hover:text-white hover:underline flex items-center gap-1 text-xs"
                      >
                        {{ stk.name }} ({{ stk.code }}) 📈
                      </button>
                      <span v-if="stk.current_price" class="font-mono font-bold text-slate-100 text-xs">
                        ¥{{ fmtPrice(stk.current_price) }}
                      </span>
                    </div>

                    <!-- 波动属性标签 -->
                    <div v-if="stk.volatility_tag" class="text-[10px] text-purple-300 font-mono">
                      {{ stk.volatility_tag }}
                    </div>

                    <!-- 真实支撑位与压力位 -->
                    <div class="p-1.5 bg-slate-950/60 rounded border border-slate-800 text-[11px] font-mono space-y-0.5">
                      <div class="flex items-center justify-between text-emerald-400">
                        <span class="text-slate-400">🛡️ 关键支撑:</span>
                        <span>¥{{ fmtPrice(stk.support_price) }}</span>
                      </div>
                      <div class="flex items-center justify-between text-red-400">
                        <span class="text-slate-400">🏔️ 第一压力:</span>
                        <span>¥{{ fmtPrice(stk.resistance_price) }}</span>
                      </div>
                    </div>

                    <!-- 建议买点区间 -->
                    <div v-if="stk.ambush_zone" class="flex items-center justify-between text-[11px] font-mono text-amber-300">
                      <span class="text-slate-400">建议低吸甜区:</span>
                      <span class="font-semibold">[¥{{ fmtPrice(stk.ambush_zone[0]) }} ~ ¥{{ fmtPrice(stk.ambush_zone[1]) }}]</span>
                    </div>

                    <!-- 目标位与止损位 -->
                    <div class="flex items-center justify-between text-[11px] font-mono">
                      <span v-if="stk.target_win" class="text-up font-semibold">止盈: ¥{{ fmtPrice(stk.target_win) }}</span>
                      <span v-if="stk.stop_loss" class="text-down font-semibold">止损: ¥{{ fmtPrice(stk.stop_loss) }}</span>
                    </div>

                    <!-- 技术面专属逻辑 -->
                    <p class="text-[11px] text-slate-400 line-clamp-2 mt-1">
                      {{ stk.stock_logic || stk.technical_basis }}
                    </p>
                  </div>

                  <!-- 快捷加自选按钮 -->
                  <div class="pt-2 border-t border-slate-800 flex items-center justify-between">
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

            <!-- v4.3: 📊 技术指标明细 + 📰 消息面利好点 + v4.4 LLM T1/T2/T3 + 右侧确认 展开区（用户感兴趣可点） -->
            <div v-if="hasDetail(item)" class="space-y-2 pt-1">
              <button
                @click="toggleDetail(idx)"
                class="w-full text-[11px] font-bold flex items-center justify-between px-3 py-1.5 rounded
                       bg-slate-900/60 border border-slate-700/60 hover:border-blue-500/60 hover:bg-slate-800/60 transition"
              >
                <span class="flex items-center gap-2">
                  <span class="text-blue-300">🔍</span>
                  <span class="text-slate-200">感兴趣？展开技术指标 + 消息面利好点 + LLM 验证详情</span>
                  <span class="rpt-badge bg-slate-800 text-slate-300 border border-slate-600 font-mono">
                    {{ (item.tech_indicators?.length || 0) + (item.news_highlights?.length || 0) + (item.llm_verification?.t1_message ? 1 : 0) + (item.llm_verification?.t2_technical ? 1 : 0) + (item.llm_verification?.t3_cross ? 1 : 0) }} 项
                  </span>
                </span>
                <span class="text-slate-400 font-mono">{{ expanded[idx] ? '▲ 收起' : '▼ 展开' }}</span>
              </button>

              <div v-if="expanded[idx]" class="space-y-2.5 p-2 rounded bg-slate-950/40 border border-slate-800">
                <!-- 📊 技术指标明细 -->
                <div v-if="item.tech_indicators?.length">
                  <div class="text-[11px] font-bold text-cyan-300 mb-1.5 flex items-center gap-1">
                    📊 技术指标明细
                    <span class="text-slate-500 font-normal text-[10px]">（{{ item.tech_indicators.length }} 项）</span>
                  </div>
                  <div class="space-y-1">
                    <div
                      v-for="(ti, tii) in item.tech_indicators"
                      :key="tii"
                      class="grid grid-cols-12 gap-2 items-center text-[11px] bg-slate-900/60 px-2 py-1.5 rounded border border-slate-800/60"
                    >
                      <div class="col-span-3 font-semibold text-slate-200 truncate" :title="ti.name">{{ ti.name }}</div>
                      <div class="col-span-3 font-mono text-slate-300 truncate" :title="ti.value">{{ ti.value || '--' }}</div>
                      <div class="col-span-2">
                        <span
                          class="rpt-badge font-mono text-[10px] border"
                          :class="getSignalBadgeClass(ti.signal)"
                        >{{ ti.signal }}</span>
                      </div>
                      <div class="col-span-4 text-slate-400 text-[10.5px]" :title="ti.comment">
                        {{ ti.comment || '—' }}
                      </div>
                    </div>
                  </div>
                </div>

                <!-- 📰 消息面利好点 -->
                <div v-if="item.news_highlights?.length">
                  <div class="text-[11px] font-bold text-amber-300 mb-1.5 flex items-center gap-1 mt-1">
                    📰 消息面利好点
                    <span class="text-slate-500 font-normal text-[10px]">（{{ item.news_highlights.length }} 条精选）</span>
                  </div>
                  <div class="space-y-1.5">
                    <div
                      v-for="(nh, nhi) in item.news_highlights"
                      :key="nhi"
                      class="bg-slate-900/60 px-2.5 py-1.5 rounded border border-slate-800/60"
                    >
                      <div class="flex items-start gap-2">
                        <div class="flex-1">
                          <div class="text-slate-200 text-[11px] font-medium leading-snug">
                            {{ nh.title }}
                          </div>
                          <div v-if="nh.why_relevant" class="text-emerald-300/90 text-[10.5px] mt-0.5 leading-relaxed">
                            💡 {{ nh.why_relevant }}
                          </div>
                        </div>
                        <div class="flex flex-col items-end gap-0.5 text-[10px] font-mono text-slate-500 shrink-0">
                          <span v-if="nh.time">{{ nh.time }}</span>
                          <span v-if="nh.source" class="text-slate-600">{{ nh.source }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- v4.4: 🛡️ T1 消息面真实性验证 (假政策检测) -->
                <div v-if="item.llm_verification?.t1_message" class="p-2 rounded bg-amber-950/20 border border-amber-900/40 space-y-1.5">
                  <div class="text-[11px] font-bold text-amber-300 flex items-center justify-between">
                    <span>🛡️ T1 消息面真实性验证 (防假政策)</span>
                    <span class="font-mono text-[10px] text-slate-400">
                      真实分 {{ item.llm_verification.t1_message.real_score ?? '—' }}
                      · 信心 {{ Math.round((item.llm_verification.t1_message.confidence || 0) * 100) }}%
                    </span>
                  </div>
                  <div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-[10.5px]">
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">真实情感</div>
                      <div class="text-slate-200 font-mono">{{ item.llm_verification.t1_message.real_sentiment }}</div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">假政策风险</div>
                      <div class="font-mono" :class="item.llm_verification.t1_message.fake_news_risk === 'low' ? 'text-emerald-300' : (item.llm_verification.t1_message.fake_news_risk === 'high' ? 'text-red-300' : 'text-amber-300')">
                        {{ item.llm_verification.t1_message.fake_news_risk }}
                      </div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded col-span-2">
                      <div class="text-slate-500 text-[9.5px]">关键催化剂</div>
                      <div class="text-emerald-200 text-[10.5px]">
                        {{ (item.llm_verification.t1_message.key_catalysts || []).join(' · ') || '—' }}
                      </div>
                    </div>
                  </div>
                  <div v-if="item.llm_verification.t1_message.title_tricks?.length" class="text-[10.5px] text-red-300/90">
                    ⚠️ 标题误导：{{ item.llm_verification.t1_message.title_tricks.join(' · ') }}
                  </div>
                  <div v-if="item.llm_verification.t1_message.summary" class="text-[10.5px] text-slate-300 leading-relaxed">
                    {{ item.llm_verification.t1_message.summary }}
                  </div>
                </div>

                <!-- v4.4: 🐂 T2 技术面操盘意图验证 (防假突破) -->
                <div v-if="item.llm_verification?.t2_technical" class="p-2 rounded bg-sky-950/20 border border-sky-900/40 space-y-1.5">
                  <div class="text-[11px] font-bold text-sky-300 flex items-center justify-between">
                    <span>🐂 T2 技术面操盘意图 (防假突破)</span>
                    <span class="font-mono text-[10px] text-slate-400">
                      真实分 {{ item.llm_verification.t2_technical.real_score ?? '—' }}
                      · 信心 {{ Math.round((item.llm_verification.t2_technical.confidence || 0) * 100) }}%
                    </span>
                  </div>
                  <div class="grid grid-cols-2 sm:grid-cols-4 gap-1.5 text-[10.5px]">
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">操盘意图</div>
                      <div class="text-slate-200 font-mono">{{ getIntentLabel(item.llm_verification.t2_technical.intent) }}</div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">假突破风险</div>
                      <div class="font-mono" :class="item.llm_verification.t2_technical.breakout_fake_risk === 'low' ? 'text-emerald-300' : (item.llm_verification.t2_technical.breakout_fake_risk === 'high' ? 'text-red-300' : 'text-amber-300')">
                        {{ item.llm_verification.t2_technical.breakout_fake_risk }}
                      </div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">关键阻力</div>
                      <div class="text-red-200 font-mono">{{ item.llm_verification.t2_technical.key_resistance || '—' }}</div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">关键支撑</div>
                      <div class="text-emerald-200 font-mono">{{ item.llm_verification.t2_technical.key_support || '—' }}</div>
                    </div>
                  </div>
                  <div v-if="item.llm_verification.t2_technical.fake_break_reasons?.length" class="text-[10.5px] text-red-300/90">
                    ⚠️ 假突破疑点：{{ item.llm_verification.t2_technical.fake_break_reasons.join(' · ') }}
                  </div>
                  <div v-if="item.llm_verification.t2_technical.summary" class="text-[10.5px] text-slate-300 leading-relaxed">
                    {{ item.llm_verification.t2_technical.summary }}
                  </div>
                </div>

                <!-- v4.4: 🧭 T3 跨维度一致性验证 (防虚假一致) -->
                <div v-if="item.llm_verification?.t3_cross" class="p-2 rounded bg-purple-950/20 border border-purple-900/40 space-y-1.5">
                  <div class="text-[11px] font-bold text-purple-300 flex items-center justify-between">
                    <span>🧭 T3 跨维度一致性 (防虚假一致)</span>
                    <span class="font-mono text-[10px] text-slate-400">
                      一致性 {{ item.llm_verification.t3_cross.coherence_score ?? '—' }}
                      · 可信度 {{ item.llm_verification.t3_cross.trustworthiness }}
                    </span>
                  </div>
                  <div class="grid grid-cols-2 sm:grid-cols-3 gap-1.5 text-[10.5px]">
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">消息面 ↔ 资金面</div>
                      <div class="font-mono" :class="(item.llm_verification.t3_cross.dimension_alignment?.msg_capital || 'weak') === 'consistent' ? 'text-emerald-300' : ((item.llm_verification.t3_cross.dimension_alignment?.msg_capital || 'weak') === 'contradict' ? 'text-red-300' : 'text-amber-300')">
                        {{ item.llm_verification.t3_cross.dimension_alignment?.msg_capital || 'weak' }}
                      </div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">技术面 ↔ 情绪面</div>
                      <div class="font-mono" :class="(item.llm_verification.t3_cross.dimension_alignment?.tech_sentiment || 'weak') === 'consistent' ? 'text-emerald-300' : ((item.llm_verification.t3_cross.dimension_alignment?.tech_sentiment || 'weak') === 'weak' ? 'text-amber-300' : 'text-red-300')">
                        {{ item.llm_verification.t3_cross.dimension_alignment?.tech_sentiment || 'weak' }}
                      </div>
                    </div>
                    <div class="bg-slate-900/60 px-1.5 py-1 rounded">
                      <div class="text-slate-500 text-[9.5px]">虚假一致性</div>
                      <div class="font-mono" :class="item.llm_verification.t3_cross.fake_consistency === 'low' ? 'text-emerald-300' : (item.llm_verification.t3_cross.fake_consistency === 'high' ? 'text-red-300' : 'text-amber-300')">
                        {{ item.llm_verification.t3_cross.fake_consistency }}
                      </div>
                    </div>
                  </div>
                  <div v-if="item.llm_verification.t3_cross.hidden_contradictions?.length" class="text-[10.5px] text-red-300/90">
                    ⚠️ 隐藏矛盾：{{ item.llm_verification.t3_cross.hidden_contradictions.join(' · ') }}
                  </div>
                  <div v-if="item.llm_verification.t3_cross.alerts?.length" class="text-[10.5px] text-amber-300/90">
                    🔔 警示：{{ item.llm_verification.t3_cross.alerts.join(' · ') }}
                  </div>
                  <div v-if="item.llm_verification.t3_cross.summary" class="text-[10.5px] text-slate-300 leading-relaxed">
                    {{ item.llm_verification.t3_cross.summary }}
                  </div>
                </div>

                <!-- v4.4: 右侧确认清单 (TRADING_LOGIC 第 4.3 节) -->
                <div v-if="item.right_side_confirmations?.length" class="p-2 rounded bg-slate-950/40 border border-slate-800 space-y-1">
                  <div class="text-[11px] font-bold text-slate-300 flex items-center gap-1">
                    ⏳ 右侧确认清单（任一触发再加仓）
                  </div>
                  <div class="grid grid-cols-1 sm:grid-cols-2 gap-1 text-[10.5px]">
                    <div
                      v-for="(rc, rci) in item.right_side_confirmations"
                      :key="rci"
                      class="flex items-center gap-1.5 text-slate-400"
                    >
                      <span class="text-slate-600">☐</span>
                      <span>{{ rc.name }}</span>
                    </div>
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
