/*
 * 课程关系图的纯布局计算：按强制先修做 Kahn 分层，推导节点坐标与连线路径。
 * 这里只处理已取回的图数据，不请求接口、不读写页面状态，便于单独验证布局结果；
 * 节点尺寸、间距与锚点保持固定，同一份输入重复计算结果稳定。
 */
import type { CourseGraph, CourseGraphEdge, CourseGraphNode } from '../api/types'

// 节点尺寸与间距固定，SVG 宽高、连线锚点和点击热区都由这些常量推导。
export const GRAPH_NODE_WIDTH = 172
export const GRAPH_NODE_HEIGHT = 54
export const GRAPH_COLUMN_GAP = 72
export const GRAPH_ROW_GAP = 18
export const GRAPH_PADDING = 18

export type GraphLayoutNode = {
  node: CourseGraphNode
  x: number
  y: number
  column: number
  row: number
}

export type GraphLayoutEdge = {
  edge: CourseGraphEdge
  path: string
  self: boolean
  cycle: boolean
}

export type GraphLayout = {
  nodes: GraphLayoutNode[]
  edges: GraphLayoutEdge[]
  width: number
  height: number
  columns: number
}

/**
 * 生成课程图布局。cycleRelationIds 来自后端返回的成环路径，仅用于高亮连线。
 */
export function buildGraphLayout(
  graph: CourseGraph | null,
  cycleRelationIds: ReadonlySet<number>,
): GraphLayout {
  const current = graph
  if (!current || !current.nodes.length) {
    return { nodes: [], edges: [], width: 0, height: 0, columns: 0 }
  }
  // 布局要画出全部课程，因此自行按强制先修做一次 Kahn 分层，不复用 /order：
  // 后者在存在问题时返回空层次，直接使用会让有问题的图无节点可渲染。
  const ids = current.nodes.map((node) => node.id)
  const idSet = new Set(ids)
  const indegree = new Map(ids.map((id) => [id, 0]))
  const adjacency = new Map<number, number[]>()
  current.edges.forEach((edge) => {
    if (edge.relation_type !== 'prerequisite') return
    if (!idSet.has(edge.from_course_id) || !idSet.has(edge.to_course_id)) return
    const targets = adjacency.get(edge.from_course_id) ?? []
    targets.push(edge.to_course_id)
    adjacency.set(edge.from_course_id, targets)
    indegree.set(edge.to_course_id, (indegree.get(edge.to_course_id) ?? 0) + 1)
  })
  const levelById = new Map<number, number>()
  let frontier = ids.filter((id) => (indegree.get(id) ?? 0) === 0)
  let depth = 0
  while (frontier.length) {
    frontier.forEach((id) => levelById.set(id, depth))
    const next: number[] = []
    frontier.forEach((id) => {
      for (const target of adjacency.get(id) ?? []) {
        const degree = (indegree.get(target) ?? 0) - 1
        indegree.set(target, degree)
        if (degree === 0) next.push(target)
      }
    })
    frontier = Array.from(new Set(next))
    depth += 1
  }
  // 成环及环下游的课程没有稳定层，统一放到最后一列，不伪造层次。
  const remaining = current.nodes.filter((node) => !levelById.has(node.id))
  const finalColumn = remaining.length ? Math.max(0, depth) : Math.max(0, depth - 1)
  remaining.forEach((node) => levelById.set(node.id, finalColumn))
  const groups = new Map<number, CourseGraphNode[]>()
  current.nodes.forEach((node) => {
    const column = levelById.get(node.id) ?? 0
    const group = groups.get(column) ?? []
    group.push(node)
    groups.set(column, group)
  })
  const columns = Array.from(groups.entries())
    .sort(([left], [right]) => left - right)
    .map(([, nodes]) => [...nodes].sort((left, right) => left.name.localeCompare(right.name, 'zh')))
  const maxRows = Math.max(...columns.map((nodes) => nodes.length))
  const width =
    columns.length * GRAPH_NODE_WIDTH +
    Math.max(0, columns.length - 1) * GRAPH_COLUMN_GAP +
    GRAPH_PADDING * 2
  const height =
    maxRows * GRAPH_NODE_HEIGHT +
    Math.max(0, maxRows - 1) * GRAPH_ROW_GAP +
    GRAPH_PADDING * 2
  // 列号来自强制先修链上的 Kahn 分层，行号按中文课名排序；
  // 位置只由节点集合推导，重复渲染结果稳定。
  const positions = new Map<number, { x: number; y: number; column: number; row: number }>()
  const layoutNodes: GraphLayout['nodes'] = []
  columns.forEach((nodes, column) => {
    nodes.forEach((node, row) => {
      const position = {
        x: GRAPH_PADDING + column * (GRAPH_NODE_WIDTH + GRAPH_COLUMN_GAP),
        y: GRAPH_PADDING + row * (GRAPH_NODE_HEIGHT + GRAPH_ROW_GAP),
        column,
        row,
      }
      positions.set(node.id, position)
      layoutNodes.push({ node, ...position })
    })
  })
  const layoutEdges = current.edges.flatMap((edge) => {
    const source = positions.get(edge.from_course_id)
    const target = positions.get(edge.to_course_id)
    if (!source || !target) return []
    const sx = source.x + GRAPH_NODE_WIDTH
    const sy = source.y + GRAPH_NODE_HEIGHT / 2
    const tx = target.x
    const ty = target.y + GRAPH_NODE_HEIGHT / 2
    // 连线从源节点右侧中点连到目标节点左侧中点；同列或回边改用绕行曲率只为
    // 避免穿过节点，箭头方向仍表示“来源课程 → 受限课程”。
    let path: string
    if (edge.from_course_id === edge.to_course_id) {
      // 自环必须可见：从节点右侧绕出再回到自身。
      path = `M ${sx} ${sy - 9} C ${sx + 52} ${sy - 34}, ${sx + 52} ${sy + 34}, ${sx} ${sy + 9}`
    } else if (target.column > source.column) {
      const handle = Math.max(36, (tx - sx) / 2)
      path = `M ${sx} ${sy} C ${sx + handle} ${sy}, ${tx - handle} ${ty}, ${tx} ${ty}`
    } else if (target.column === source.column) {
      path = `M ${sx} ${sy} C ${sx + 58} ${sy}, ${tx + 58} ${ty}, ${tx} ${ty}`
    } else {
      path = `M ${sx} ${sy} C ${sx + 76} ${sy - 66}, ${tx - 76} ${ty + 66}, ${tx} ${ty}`
    }
    return [{ edge, path, self: edge.from_course_id === edge.to_course_id, cycle: cycleRelationIds.has(edge.id) }]
  })
  return { nodes: layoutNodes, edges: layoutEdges, width, height, columns: columns.length }
}
