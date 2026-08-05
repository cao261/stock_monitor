<script setup>
/**
 * 板块资金流向力导向气泡图 (v3)
 *
 * v3 优化重点：
 *  - 强制最小半径 32px（d3.scaleSqrt([min_val, max_val]).range([32, 90])），
 *    配合 TOP_N_PER_SIDE=20（最多 40 节点），从源头消灭"空气泡"
 *  - 温度计色系：颜色透明度 (alpha 0.4-1.0) 与涨跌幅绝对值挂钩
 *    —— 涨得越猛红得越实、跌得越狠绿得越实
 *  - 5s 平滑更新：用 d3 .join() 模式 + .transition().duration(800)，
 *    配 simulation.alpha(0.3).restart() 让气泡"呼吸"式蠕动到新位置
 *    —— 不再每次销毁重建 SVG
 */
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue'
import * as d3 from 'd3'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

// ====================== 配置 ======================
const RADIUS_MIN = 32              // 强制最小半径，消灭空气泡
const RADIUS_MAX = 90              // 最大半径上限
const PRUNE_MIN_AMOUNT = 2         // |净额| < 2亿 过滤
const TOP_N_PER_SIDE = 20          // 流入/流出各取前 20（v2 是 40，更大气泡需要更少节点）
const TRANSITION_MS = 800          // 半径 / 颜色过渡时长
const ALPHA_RE_ENERGIZE = 0.3      // 数据更新时给 simulation 充能

// ====================== 工具 ======================
function fmtAmount(v) {
  if (v == null || Number.isNaN(v)) return '0.0'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(1)}`
}
function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '-'
  const sign = v > 0 ? '+' : ''
  return `${sign}${Number(v).toFixed(2)}%`
}
function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

// 温度计：涨跌幅绝对值 → [0, 1] 强度
//  0%   → 0 (alpha 0.4，淡淡一层)
//  5%+  → 1 (alpha 1.0，浓烈)
function intensityFromChange(changePct, isInflow) {
  const abs = isInflow ? changePct : -changePct
  const clamped = Math.max(0, Math.min(10, abs))
  return Math.min(1, clamped / 5)
}

function colorFor(d) {
  const intensity = intensityFromChange(d.change_pct, d.net_amount >= 0)
  const alpha = 0.4 + intensity * 0.6
  const a = alpha.toFixed(3)
  return d.net_amount >= 0
    ? `rgba(239, 68, 68, ${a})`
    : `rgba(34, 197, 94, ${a})`
}
function strokeFor(d) {
  const intensity = intensityFromChange(d.change_pct, d.net_amount >= 0)
  return d.net_amount >= 0
    ? (intensity > 0.6 ? 'rgba(252, 165, 165, 1.0)' : 'rgba(248, 113, 113, 0.95)')
    : (intensity > 0.6 ? 'rgba(134, 239, 172, 1.0)' : 'rgba(74, 222, 128, 0.95)')
}

// ====================== 数据剪枝 ======================
const displayItems = computed(() => {
  const all = props.items || []
  const filtered = all.filter((x) => Math.abs(x.net_amount) >= PRUNE_MIN_AMOUNT)
  const inflow = filtered
    .filter((x) => x.net_amount > 0)
    .sort((a, b) => b.net_amount - a.net_amount)
    .slice(0, TOP_N_PER_SIDE)
  const outflow = filtered
    .filter((x) => x.net_amount < 0)
    .sort((a, b) => a.net_amount - b.net_amount)
    .slice(0, TOP_N_PER_SIDE)
  return [...inflow, ...outflow]
})
const stats = computed(() => {
  const d = displayItems.value
  return {
    count: d.length,
    inflow: d.filter((x) => x.net_amount > 0).length,
    outflow: d.filter((x) => x.net_amount < 0).length,
  }
})

// ====================== 状态 ======================
const wrapRef = ref(null)
const svgRef = ref(null)
const containerSize = ref({ w: 800, h: 540 })
const hovered = ref(null)

let simulation = null
let resizeObserver = null
// 持久 d3 选中（横跨多次 updateData）
let svgSel = null
let layer = null
let bubbleG = null
let labelG = null
let centerLine = null
let leftGuideLine = null
let rightGuideLine = null
let leftCampLabel = null
let rightCampLabel = null
let bubbles = null
let labels = null
let nodes = []  // 持久 node 数组（保留 x/y）
let drag = null

// ====================== 尺寸 ======================
function measure() {
  const el = wrapRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  containerSize.value = {
    w: Math.max(320, Math.floor(rect.width)),
    h: Math.max(380, Math.floor(rect.height || 540)),
  }
}

// ====================== 初始化：建图层结构（只跑一次） ======================
function init() {
  measure()
  const { w, h } = containerSize.value

  const svg = d3.select(svgRef.value)
  svg.attr('viewBox', `0 0 ${w} ${h}`).attr('width', w).attr('height', h)
  svgSel = svg

  layer = svg.append('g').attr('class', 'layer')
  bubbleG = layer.append('g').attr('class', 'bubbles')
  labelG = layer.append('g').attr('class', 'labels')

  // 静态装饰：中线 + 阵营引导线 + 阵营标签
  centerLine = layer
    .append('line')
    .attr('x1', w / 2).attr('x2', w / 2)
    .attr('y1', 0).attr('y2', h)
    .attr('stroke', 'rgba(71, 85, 105, 0.28)')
    .attr('stroke-dasharray', '4,5')
    .attr('stroke-width', 1)

  leftGuideLine = layer
    .append('line')
    .attr('x1', w * 0.35).attr('x2', w * 0.35)
    .attr('y1', h * 0.05).attr('y2', h * 0.95)
    .attr('stroke', 'rgba(239, 68, 68, 0.10)')
    .attr('stroke-dasharray', '2,6')
  rightGuideLine = layer
    .append('line')
    .attr('x1', w * 0.65).attr('x2', w * 0.65)
    .attr('y1', h * 0.05).attr('y2', h * 0.95)
    .attr('stroke', 'rgba(34, 197, 94, 0.10)')
    .attr('stroke-dasharray', '2,6')

  leftCampLabel = layer
    .append('text')
    .attr('x', w * 0.35).attr('y', 24)
    .attr('text-anchor', 'middle')
    .attr('fill', 'rgba(244, 63, 94, 0.75)')
    .attr('font-size', '11px')
    .attr('font-family', 'ui-monospace, SFMono-Regular, monospace')
    .attr('letter-spacing', '0.25em')
    .text('◀ 净流入')
  rightCampLabel = layer
    .append('text')
    .attr('x', w * 0.65).attr('y', 24)
    .attr('text-anchor', 'middle')
    .attr('fill', 'rgba(34, 197, 94, 0.75)')
    .attr('font-size', '11px')
    .attr('font-family', 'ui-monospace, SFMono-Regular, monospace')
    .attr('letter-spacing', '0.25em')
    .text('净流出 ▶')

  // 拖拽（只创建一次）
  drag = d3
    .drag()
    .on('start', (event, d) => {
      if (!event.active) simulation.alphaTarget(0.25).restart()
      d.fx = d.x
      d.fy = d.y
    })
    .on('drag', (event, d) => {
      d.fx = event.x
      d.fy = event.y
    })
    .on('end', (event, d) => {
      if (!event.active) simulation.alphaTarget(0)
      d.fx = null
      d.fy = null
    })

  // Simulation（只创建一次，后续只更新 nodes + forces）
  simulation = d3
    .forceSimulation()
    .force('charge', d3.forceManyBody().strength(-10))
    .force(
      'collide',
      d3.forceCollide()
        .radius((d) => (d.targetR || RADIUS_MIN) + 2)
        .strength(0.8)
        .iterations(2),
    )
    .force('center', d3.forceCenter(w / 2, h / 2).strength(0.04))
    .force(
      'clusterX',
      d3.forceX()
        .x((d) => (d.net_amount >= 0 ? w * 0.35 : w * 0.65))
        .strength(0.08),
    )
    .force('y', d3.forceY().y(h / 2).strength(0.06))
    .alphaDecay(0.025)
    .on('tick', onTick)
}

// ====================== Resize：只调位置，不重建 ======================
function onResize() {
  measure()
  const { w, h } = containerSize.value
  if (!svgSel) return

  svgSel.attr('viewBox', `0 0 ${w} ${h}`).attr('width', w).attr('height', h)
  if (centerLine) centerLine.attr('x1', w / 2).attr('x2', w / 2).attr('y2', h)
  if (leftGuideLine)
    leftGuideLine.attr('x1', w * 0.35).attr('x2', w * 0.35).attr('y1', h * 0.05).attr('y2', h * 0.95)
  if (rightGuideLine)
    rightGuideLine.attr('x1', w * 0.65).attr('x2', w * 0.65).attr('y1', h * 0.05).attr('y2', h * 0.95)
  if (leftCampLabel) leftCampLabel.attr('x', w * 0.35)
  if (rightCampLabel) rightCampLabel.attr('x', w * 0.65)

  if (simulation) {
    simulation
      .force('center', d3.forceCenter(w / 2, h / 2).strength(0.04))
      .force(
        'clusterX',
        d3.forceX()
          .x((d) => (d.net_amount >= 0 ? w * 0.35 : w * 0.65))
          .strength(0.08),
      )
      .force('y', d3.forceY().y(h / 2).strength(0.06))
      .alpha(0.3)
      .restart()
  }
}

// ====================== 关键：data join + 过渡动画 ======================
function updateData() {
  if (!svgSel) return
  const { w, h } = containerSize.value
  const rawItems = displayItems.value

  // ====== 0) 数据为空：让现有气泡优雅淡出 ======
  if (rawItems.length === 0) {
    if (bubbles) {
      bubbles.transition().duration(500).attr('r', 0).style('opacity', 0).remove()
      bubbles = null
    }
    if (labels) {
      labels.transition().duration(500).style('opacity', 0).remove()
      labels = null
    }
    nodes = []
    if (simulation) simulation.nodes([])
    hovered.value = null
    return
  }

  // ====== 1) 构建新 node 数据，复用旧 node 的 x/y ======
  const newNodes = rawItems.map((d) => ({
    name: d.name,
    net_amount: Number(d.net_amount) || 0,
    change_pct: Number(d.change_pct) || 0,
    inflow: Number(d.inflow) || 0,
    outflow: Number(d.outflow) || 0,
    leading_stock: d.leading_stock || '',
    leading_change_pct: Number(d.leading_change_pct) || 0,
    company_count: Number(d.company_count) || 0,
    unit: d.unit || '亿',
  }))

  const oldMap = new Map(nodes.map((n) => [n.name, n]))
  for (const n of newNodes) {
    const old = oldMap.get(n.name)
    if (old) {
      // 复用：保留 x/y，给个旧 targetR，scale 算出新 targetR 后再覆盖
      n.x = old.x
      n.y = old.y
      n.targetR = old.targetR
    } else {
      // 新气泡：随机落到自己阵营
      if (n.net_amount >= 0) {
        n.x = w * 0.2 + Math.random() * w * 0.2
      } else {
        n.x = w * 0.6 + Math.random() * w * 0.2
      }
      n.y = h * 0.15 + Math.random() * h * 0.7
      n.targetR = RADIUS_MIN
    }
  }

  // ====== 2) 半径比例尺：domain = [min, max]，range = [32, 90] ======
  const absValues = newNodes.map((n) => Math.abs(n.net_amount))
  const minAbs = Math.max(PRUNE_MIN_AMOUNT, Math.min(...absValues))
  const maxAbs = Math.max(PRUNE_MIN_AMOUNT, Math.max(...absValues))
  const domain = minAbs === maxAbs ? [0, maxAbs] : [minAbs, maxAbs]
  const rScale = d3.scaleSqrt().domain(domain).range([RADIUS_MIN, RADIUS_MAX])
  for (const n of newNodes) {
    n.targetR = rScale(Math.abs(n.net_amount))
  }

  // 替换持久数组（这是 simulation 接下来要用的）
  nodes = newNodes

  // ====== 3) JOIN pattern: circles ======
  bubbles = bubbleG.selectAll('circle').data(nodes, (d) => d.name)

  // EXIT: 收缩消失
  bubbles
    .exit()
    .transition()
    .duration(TRANSITION_MS)
    .attr('r', 0)
    .style('opacity', 0)
    .remove()

  // ENTER: 从 0 半径淡入
  const enter = bubbles
    .enter()
    .append('circle')
    .attr('r', 0)
    .attr('cx', (d) => d.x)
    .attr('cy', (d) => d.y)
    .attr('fill', (d) => colorFor(d))
    .attr('stroke', (d) => strokeFor(d))
    .attr('stroke-width', 1.2)
    .style('cursor', 'pointer')
    .style('filter', 'drop-shadow(0 0 6px rgba(0, 0, 0, 0.35))')
    .style('opacity', 0)
    .on('mouseenter', onMouseEnter)
    .on('mousemove', onMouseMove)
    .on('mouseleave', onMouseLeave)
    .call(drag)

  enter
    .transition()
    .duration(TRANSITION_MS)
    .attr('r', (d) => d.targetR)
    .style('opacity', 1)

  // MERGE：进入 + 旧的
  bubbles = enter.merge(bubbles)

  // UPDATE: 已存在的 → 平滑过渡到新 r 和新颜色
  bubbles
    .transition('bubble-resize')
    .duration(TRANSITION_MS)
    .ease(d3.easeCubicOut)
    .attr('r', (d) => d.targetR)
    .attr('fill', (d) => colorFor(d))
    .attr('stroke', (d) => strokeFor(d))

  // ====== 4) JOIN pattern: labels（所有气泡都有，不再按 r 过滤） ======
  labels = labelG.selectAll('g.label').data(nodes, (d) => d.name)

  labels
    .exit()
    .transition()
    .duration(TRANSITION_MS)
    .style('opacity', 0)
    .remove()

  const labelEnter = labels
    .enter()
    .append('g')
    .attr('class', 'label')
    .style('pointer-events', 'none')
    .style('paint-order', 'stroke')
    .style('opacity', 0)

  // 板块名（上行）
  labelEnter
    .append('text')
    .attr('text-anchor', 'middle')
    .attr('fill', '#fff')
    .attr('font-weight', '600')
    .attr('stroke', 'rgba(0, 0, 0, 0.85)')
    .attr('stroke-width', '2.5')
    .attr('stroke-linejoin', 'round')
    .style('filter', 'drop-shadow(0px 1px 2px rgba(0, 0, 0, 0.8))')

  // 资金量（下行）
  labelEnter
    .append('text')
    .attr('text-anchor', 'middle')
    .attr('fill', 'rgba(255, 255, 255, 0.95)')
    .attr('font-family', 'ui-monospace, SFMono-Regular, monospace')
    .attr('stroke', 'rgba(0, 0, 0, 0.85)')
    .attr('stroke-width', '2')
    .attr('stroke-linejoin', 'round')
    .style('filter', 'drop-shadow(0px 1px 2px rgba(0, 0, 0, 0.8))')

  labels = labelEnter.merge(labels)

  // UPDATE 文字内容（半径变了字号要跟着变）
  labels
    .select('text:nth-child(1)')
    .attr('font-size', (d) => Math.min(14, Math.max(10, d.targetR * 0.32)))
    .attr('y', (d) => -d.targetR * 0.08)
    .text((d) => truncate(d.name, Math.max(3, Math.floor(d.targetR / 5.5))))
  labels
    .select('text:nth-child(2)')
    .attr('font-size', (d) => Math.min(11, Math.max(9, d.targetR * 0.24)))
    .attr('y', (d) => d.targetR * 0.32)
    .text((d) => `${fmtAmount(d.net_amount)}${d.unit}`)

  labels
    .transition('label-fade')
    .duration(TRANSITION_MS)
    .style('opacity', 1)

  // ====== 5) 喂数据给 simulation，给它充能"呼吸" ======
  if (simulation) {
    simulation.nodes(nodes).alpha(ALPHA_RE_ENERGIZE).restart()
  }
}

// ====================== Tick: simulation 每帧回调 ======================
function onTick() {
  const { w, h } = containerSize.value
  for (const n of nodes) {
    const r = n.targetR || RADIUS_MIN
    n.x = Math.max(r + 2, Math.min(w - r - 2, n.x))
    n.y = Math.max(r + 2, Math.min(h - r - 2, n.y))
  }
  if (bubbles) bubbles.attr('cx', (d) => d.x).attr('cy', (d) => d.y)
  if (labels) labels.attr('transform', (d) => `translate(${d.x}, ${d.y})`)
}

// ====================== Hover handlers ======================
function onMouseEnter(event, d) {
  d3.select(event.currentTarget).attr('stroke-width', 2.4)
  hovered.value = {
    x: event.clientX,
    y: event.clientY,
    name: d.name,
    net_amount: d.net_amount,
    change_pct: d.change_pct,
    leading_stock: d.leading_stock,
    leading_change_pct: d.leading_change_pct,
    company_count: d.company_count,
    unit: d.unit,
  }
}
function onMouseMove(event) {
  if (hovered.value) {
    hovered.value.x = event.clientX
    hovered.value.y = event.clientY
  }
}
function onMouseLeave(event) {
  d3.select(event.currentTarget).attr('stroke-width', 1.2)
  hovered.value = null
}

// ====================== 生命周期 ======================
onMounted(() => {
  init()
  updateData()  // 首次挂载就跑一次（watch 不会立即触发）
  resizeObserver = new ResizeObserver(() => onResize())
  if (wrapRef.value) resizeObserver.observe(wrapRef.value)
})
onUnmounted(() => {
  if (simulation) simulation.stop()
  if (resizeObserver) resizeObserver.disconnect()
  hovered.value = null
})

// 后续数据更新（Dashboard 每 60s 拉一次）
watch(
  () => props.items,
  () => {
    nextTick(() => updateData())
  },
  { deep: true },
)
</script>

<template>
  <div class="glass p-5">
    <div class="flex items-center justify-between mb-3">
      <h2 class="text-sm font-semibold text-slate-200 tracking-wide">
        板块资金流向
        <span class="text-slate-500 font-normal ml-2 font-mono">
          {{ stats.count }} / 40 板块
          <span v-if="stats.count > 0" class="text-rose-400/80">· 红 {{ stats.inflow }}</span>
          <span v-if="stats.count > 0" class="text-emerald-400/80 ml-1">· 绿 {{ stats.outflow }}</span>
        </span>
      </h2>
      <span class="text-xs text-slate-600 font-mono hidden md:inline">
        温度计色：涨跌越深色越浓 · 5s 呼吸更新
      </span>
    </div>

    <div ref="wrapRef" class="relative w-full" style="height: 540px;">
      <div
        v-if="loading && !displayItems.length"
        class="absolute inset-0 flex items-center justify-center text-slate-500 text-sm"
      >
        加载中…
      </div>
      <div
        v-else-if="!displayItems.length"
        class="absolute inset-0 flex items-center justify-center text-slate-500 text-sm"
      >
        暂无显著板块（|净额| ≥ 2亿 的均无数据）
      </div>
      <svg
        v-show="displayItems.length"
        ref="svgRef"
        class="w-full h-full select-none"
        role="img"
        aria-label="板块资金流向气泡图"
      ></svg>
    </div>

    <!-- ====================== Tooltip ====================== -->
    <Teleport to="body">
      <div
        v-if="hovered"
        class="fund-flow-tooltip"
        :style="{ left: hovered.x + 'px', top: hovered.y + 'px' }"
        role="tooltip"
      >
        <div class="text-slate-100 font-semibold text-sm leading-tight">{{ hovered.name }}</div>
        <div
          class="font-mono text-base font-bold leading-tight mt-0.5"
          :class="hovered.net_amount >= 0 ? 'text-rose-300' : 'text-emerald-300'"
        >
          {{ fmtAmount(hovered.net_amount) }}{{ hovered.unit }}
        </div>
        <div class="text-xs text-slate-400 mt-1 font-mono">涨跌 {{ fmtPct(hovered.change_pct) }}</div>
        <div
          v-if="hovered.leading_stock"
          class="text-xs text-slate-400 mt-0.5 font-mono"
        >
          领涨 {{ hovered.leading_stock }}
          <span
            :class="hovered.leading_change_pct >= 0 ? 'text-rose-300' : 'text-emerald-300'"
            >{{ fmtPct(hovered.leading_change_pct) }}</span
          >
        </div>
        <div
          v-if="hovered.company_count"
          class="text-xs text-slate-500 mt-0.5 font-mono"
        >
          {{ hovered.company_count }} 家公司
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.fund-flow-tooltip {
  position: fixed;
  pointer-events: none;
  z-index: 50;
  background: rgba(15, 23, 42, 0.96);
  border: 1px solid rgba(71, 85, 105, 0.55);
  border-radius: 0.4rem;
  padding: 0.5rem 0.75rem;
  min-width: 140px;
  backdrop-filter: blur(10px) saturate(140%);
  -webkit-backdrop-filter: blur(10px) saturate(140%);
  box-shadow: 0 8px 24px -4px rgba(0, 0, 0, 0.55);
  transform: translate(14px, 14px);
  white-space: nowrap;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.6);
}
</style>
