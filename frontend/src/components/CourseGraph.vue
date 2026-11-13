<script setup lang="ts">
/*
 * 课程关系图渲染：图例、滚动容器、SVG 连线与节点，以及节点／连线的选中交互。
 * 布局由 utils/courseGraph 计算后通过 props 传入，本组件不请求接口也不改页面数据；
 * 选中状态由父页面持有，这里只发出 select-node／select-edge，保持节点与连线详情互斥。
 */
import { computed } from 'vue'

import { GRAPH_NODE_HEIGHT, GRAPH_NODE_WIDTH } from '../utils/courseGraph'
import type { GraphLayout } from '../utils/courseGraph'

const props = defineProps<{
  layout: GraphLayout
  selectedNodeId: number | null
  selectedEdgeId: number | null
  cycleCourseIds: ReadonlySet<number>
  relationLabels: Record<string, string>
  statusLabels: Record<string, string>
}>()

const emit = defineEmits<{
  (event: 'select-node', nodeId: number): void
  (event: 'select-edge', edgeId: number): void
}>()

const relationTypes = ['prerequisite', 'corequisite', 'recommended'] as const

const nameById = computed(
  () => new Map(props.layout.nodes.map((item) => [item.node.id, item.node.name])),
)

function courseName(id: number): string {
  return nameById.value.get(id) ?? `课程 #${id}`
}

// 节点宽度固定，超长课名截断；完整名称由模板里的 <title> 提供悬浮提示。
function shortNodeName(name: string): string {
  return name.length > 12 ? `${name.slice(0, 12)}…` : name
}
</script>

<template>
  <div class="legend-row graph-legend">
    <span class="legend-chip prerequisite">强制先修 · 实线箭头</span>
    <span class="legend-chip corequisite">并修 · 点划线</span>
    <span class="legend-chip recommended">建议先学 · 虚线</span>
    <span class="legend-chip cycle">成环 · 高亮加粗</span>
  </div>
  <p v-if="!layout.nodes.length" class="compact-empty">当前方案还没有课程，无法绘制关系图。</p>
  <div v-else class="graph-scroll">
    <svg
      :width="layout.width"
      :height="layout.height"
      :viewBox="`0 0 ${layout.width} ${layout.height}`"
      class="graph-svg"
      role="group"
      aria-label="课程关系图，可用键盘聚焦课程节点与连线查看详情和来源证据"
    >
      <defs>
        <marker
          v-for="type in relationTypes"
          :id="`graph-arrow-${type}`"
          :key="`marker-${type}`"
          markerWidth="9"
          markerHeight="9"
          refX="8"
          refY="4.5"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L9,4.5 L0,9 z" :class="`marker-${type}`" />
        </marker>
      </defs>
      <g>
        <g
          v-for="item in layout.edges"
          :key="`edge-${item.edge.id}`"
          :class="['graph-edge', { cycle: item.cycle, selected: selectedEdgeId === item.edge.id }]"
          role="button"
          tabindex="0"
          :aria-pressed="selectedEdgeId === item.edge.id"
          :aria-label="`${relationLabels[item.edge.relation_type] ?? item.edge.relation_type}：${courseName(item.edge.from_course_id)} → ${courseName(item.edge.to_course_id)}，查看条件原文与来源候选`"
          @click.stop="emit('select-edge', item.edge.id)"
          @keydown.enter.prevent="emit('select-edge', item.edge.id)"
          @keydown.space.prevent="emit('select-edge', item.edge.id)"
        >
          <path :d="item.path" class="edge-hit" />
          <path
            :d="item.path"
            :class="[
              'edge-line',
              `edge-${item.edge.relation_type}`,
              {
                selected: selectedEdgeId === item.edge.id,
                'edge-self': item.self,
                'edge-cycle': item.cycle,
              },
            ]"
            :marker-end="`url(#graph-arrow-${item.edge.relation_type})`"
          />
        </g>
      </g>
      <g
        v-for="item in layout.nodes"
        :key="`node-${item.node.id}`"
        :class="[
          'graph-node',
          {
            cycle: cycleCourseIds.has(item.node.id),
            selected: selectedNodeId === item.node.id,
          },
        ]"
        :transform="`translate(${item.x}, ${item.y})`"
        role="button"
        tabindex="0"
        :aria-pressed="selectedNodeId === item.node.id"
        :aria-label="`课程 ${item.node.name}，代码 ${item.node.code ?? '未填写'}，${item.node.credits ?? '未知'} 学分，修读状态 ${statusLabels[item.node.study_status] ?? item.node.study_status}，查看课程详情`"
        @click.stop="emit('select-node', item.node.id)"
        @keydown.enter.prevent="emit('select-node', item.node.id)"
        @keydown.space.prevent="emit('select-node', item.node.id)"
      >
        <title>{{ item.node.name }}</title>
        <rect :width="GRAPH_NODE_WIDTH" :height="GRAPH_NODE_HEIGHT" rx="12" />
        <text x="12" y="23" class="node-name">{{ shortNodeName(item.node.name) }}</text>
        <text x="12" y="41" class="node-meta">
          {{ item.node.code ?? '未填写代码' }} · {{ item.node.credits ?? '?' }} 学分 · {{ statusLabels[item.node.study_status] ?? item.node.study_status }}
        </text>
      </g>
    </svg>
  </div>
</template>

<style scoped>
.legend-row { display: flex; flex-wrap: wrap; gap: 8px; margin: 6px 0; }
.legend-chip { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 800; border: 1px solid var(--line-strong); }
.legend-chip.prerequisite { color: var(--danger-strong-ink); background: var(--danger-bg-soft); border-color: transparent; }
.legend-chip.corequisite { color: var(--warn-strong-ink); background: var(--accent-soft); border-color: transparent; }
.legend-chip.recommended { color: var(--info-ink); background: var(--info-bg); border-color: transparent; }
.legend-chip.cycle { color: var(--danger-strong-ink); background: var(--danger-bg); border-color: var(--danger-border); }
.graph-legend { margin: 10px 0 12px; }
.graph-scroll { overflow-x: auto; padding: 8px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-muted); }
.graph-svg { display: block; min-width: 100%; }
.graph-edge { cursor: pointer; outline: none; }
.edge-hit { fill: none; stroke: transparent; stroke-width: 14; }
.edge-line { fill: none; stroke: var(--line-strong); stroke-width: 1.8; }
.edge-prerequisite { stroke: var(--danger-strong-ink); stroke-width: 2.1; }
.edge-corequisite { stroke: var(--warn-strong-ink); stroke-width: 2.1; stroke-dasharray: 9 4 2 4; }
.edge-recommended { stroke: var(--info-ink); stroke-width: 1.8; stroke-dasharray: 5 5; }
.graph-edge.cycle .edge-line, .edge-cycle { stroke-width: 3.2; }
.graph-edge.selected .edge-line { stroke-width: 3.6; }
.graph-edge:focus-visible .edge-line { stroke: var(--brand); stroke-width: 4.2; }
.marker-prerequisite { fill: var(--danger-strong-ink); }
.marker-corequisite { fill: var(--warn-strong-ink); }
.marker-recommended { fill: var(--info-ink); }
.graph-node { cursor: pointer; outline: none; }
.graph-node rect { fill: var(--paper-strong); stroke: var(--line-strong); stroke-width: 1.5; }
.graph-node:hover rect, .graph-node:focus-visible rect { stroke: var(--brand-border); }
.graph-node.selected rect { stroke: var(--brand); stroke-width: 2.6; }
.graph-node.cycle rect { stroke: var(--danger-strong-ink); stroke-dasharray: 6 3; }
.node-name { fill: var(--ink); font-size: 13px; font-weight: 800; }
.node-meta { fill: var(--muted); font-size: 10.5px; }
.graph-node.cycle .node-name { fill: var(--danger-strong-ink); }
</style>
