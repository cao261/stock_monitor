<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  report: { type: Object, default: null }, // { generated_at, model, report_markdown, file_path, file_name }
})

const emit = defineEmits(['close'])

const copied = ref(false)

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderInline(s) {
  let out = escapeHtml(s)
  out = out.replace(/`([^`]+)`/g, '<code class="px-1 py-0.5 rounded bg-slate-900 border border-slate-700 text-amber-300 text-[0.85em] font-mono">$1</code>')
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong class="font-bold text-slate-100">$1</strong>')
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em class="text-slate-300 italic">$2</em>')
  return out
}

function renderMarkdown(md) {
  if (!md) return ''
  const lines = String(md).split('\n')
  const out = []
  let inOl = false
  let inUl = false
  let inCode = false
  let codeBuf = []

  const closeLists = () => {
    if (inOl) { out.push('</ol>'); inOl = false }
    if (inUl) { out.push('</ul>'); inUl = false }
  }

  for (const raw of lines) {
    const line = raw.replace(/\r$/, '')
    if (/^```/.test(line)) {
      if (inCode) {
        out.push(`<pre class="my-3 p-3 rounded bg-slate-950 border border-slate-800 overflow-x-auto text-xs font-mono text-slate-200"><code>${escapeHtml(codeBuf.join('\n'))}</code></pre>`)
        codeBuf = []
        inCode = false
      } else {
        closeLists()
        inCode = true
      }
      continue
    }
    if (inCode) {
      codeBuf.push(line)
      continue
    }

    let m
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      closeLists()
      const level = m[1].length
      const sizes = ['text-lg font-bold text-blue-400 mt-4 mb-2 border-b border-slate-800 pb-1', 'text-base font-bold text-slate-100 mt-3 mb-1.5', 'text-sm font-semibold text-slate-200 mt-2.5 mb-1', 'text-xs font-semibold text-slate-300 mt-2 mb-1']
      out.push(`<h${level} class="${sizes[level - 1]}">${renderInline(m[2])}</h${level}>`)
      continue
    }
    if ((m = /^>\s?(.*)$/.exec(line))) {
      closeLists()
      out.push(`<blockquote class="border-l-2 border-amber-500/80 bg-amber-950/20 px-3 py-1.5 my-2 text-slate-300 italic text-xs rounded-r">${renderInline(m[1])}</blockquote>`)
      continue
    }
    if ((m = /^\d+\.\s+(.*)$/.exec(line))) {
      if (!inOl) { closeLists(); out.push('<ol class="list-decimal list-inside my-2 space-y-1 text-slate-200 text-xs">'); inOl = true }
      out.push(`<li>${renderInline(m[1])}</li>`)
      continue
    }
    if ((m = /^[-*]\s+(.*)$/.exec(line))) {
      if (!inUl) { closeLists(); out.push('<ul class="list-disc list-inside my-2 space-y-1 text-slate-200 text-xs">'); inUl = true }
      out.push(`<li>${renderInline(m[1])}</li>`)
      continue
    }
    if (line.trim() === '') {
      closeLists()
      continue
    }
    closeLists()
    out.push(`<p class="my-2 leading-relaxed text-slate-200 text-xs">${renderInline(line)}</p>`)
  }
  closeLists()
  return out.join('')
}

const htmlContent = computed(() => renderMarkdown(props.report?.report_markdown || ''))

async function copyReport() {
  if (!props.report?.report_markdown) return
  try {
    await navigator.clipboard.writeText(props.report.report_markdown)
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch (e) {
    console.warn('Clipboard error:', e)
  }
}
</script>

<template>
  <div v-if="props.show" class="rpt-modal-backdrop" @click.self="emit('close')">
    <div class="rpt-modal-box w-full max-w-3xl max-h-[85vh]">
      <!-- 头部 -->
      <div class="p-3.5 bg-[#131c2e] border-b border-slate-700 flex items-center justify-between">
        <div class="flex items-center gap-2">
          <span class="text-sm font-bold text-purple-300 flex items-center gap-1.5">
            🤖 AI 交易领航员 · 深度复盘报告
          </span>
          <span v-if="props.report?.model" class="text-[11px] font-mono px-1.5 py-0.5 rounded bg-purple-950 text-purple-300 border border-purple-800">
            {{ props.report.model }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="copyReport"
            class="rpt-btn rpt-btn-sm text-slate-300 hover:text-white"
          >
            {{ copied ? '✔ 已复制 Markdown' : '📋 复制全文' }}
          </button>
          <button @click="emit('close')" class="text-slate-400 hover:text-white px-2 py-1 text-base">
            ✕
          </button>
        </div>
      </div>

      <!-- 报告正文 -->
      <div class="p-5 overflow-y-auto space-y-3 bg-[#0d1322]">
        <!-- v2026-08-23 审计加：LLM 数据授权提示（用户明确知情同意：自选股+持仓会发给 LLM） -->
        <div class="text-[11px] bg-amber-950/40 text-amber-200 border border-amber-800/60 rounded p-2.5 flex items-start gap-2">
          <span class="text-base leading-none">⚠️</span>
          <div class="flex-1 leading-relaxed">
            <div class="font-semibold mb-0.5">数据已发送至第三方 LLM 服务</div>
            <div class="text-amber-300/80">
              本次复盘调用模型 <span class="font-mono text-amber-200">{{ props.report?.model || '未知 LLM' }}</span>，
              您的<strong>自选股代码 / 持仓成本 / 浮盈亏 / 止盈止损 / 交易备忘</strong>等敏感信息将通过网络发送给该 LLM 提供商处理。
              <span class="text-amber-400/70">详见后端日志留痕。</span>
            </div>
          </div>
        </div>

        <div v-if="props.report?.file_path" class="text-[11px] font-mono text-slate-500 bg-slate-900 p-2 rounded border border-slate-800 flex items-center justify-between">
          <span>📁 已持久化保存至：{{ props.report.file_path }}</span>
          <span>{{ props.report.generated_at }}</span>
        </div>

        <div class="prose prose-invert max-w-none text-slate-200 leading-relaxed" v-html="htmlContent"></div>
      </div>
    </div>
  </div>
</template>
