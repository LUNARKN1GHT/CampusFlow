import { apiRequest } from './client'

// 有范围问答：一次性提问与持久会话两种入口。
// 范围（学期／课程／教学班）由服务端校验一致性；回答、引用、覆盖、对照与冲突分开返回，不合并成一段结论。

export type QaMode = 'keyword' | 'vector' | 'hybrid'

export type QaScope = {
  semester_id: number | null
  course_id: number | null
  class_name: string | null
}

export type QaCitation = {
  id: string
  material_id: number
  material_title: string
  version_id: number
  version_no: number
  block_id: string
  source_chunk_id: number
  location: string
  page: number | null
  paragraph: number | null
  excerpt: string
  // valid=false 表示读取时重新核对失败（已删除、已归档、版本被替代或原文改写），不能再当可验证引用。
  valid: boolean
  status: string
  message: string | null
}

export type QaCoverage = {
  // partial 部分解析失败；unavailable 当前不可读；unindexed 尚未完成解析或索引，未参与本次回答。
  kind: 'partial' | 'unavailable' | 'unindexed'
  material_id: number
  title: string
  message: string
  uncovered_source_count: number
  failed_blocks: number
}

export type QaComparison = {
  material_id: number
  title: string
  semester_id: number | null
  course_id: number | null
  class_name: string | null
  citation_ids: string[]
  excerpts: string[]
}

export type QaConflict = {
  code: string
  message: string
  citation_ids: string[]
  requires_review: boolean
}

export type QaAnswer = {
  answer: string
  insufficient: boolean
  citations: QaCitation[]
  coverage: QaCoverage[]
  comparisons: QaComparison[]
  conflicts: QaConflict[]
  uncertainties: string[]
  intent: string
}

export type QaTurn = QaAnswer & {
  id: number
  question: string
  semester_id: number | null
  course_id: number | null
  class_name: string | null
  created_at: string
}

export type QaConversation = QaScope & {
  id: number
  mode: QaMode
  created_at: string
  updated_at: string
}

export type QaConversationDetail = {
  conversation: QaConversation
  turns: QaTurn[]
  coverage: QaCoverage[]
  pending_conflict_count: number
}

export type QaIntent = {
  intent: 'query' | 'planning' | 'conflict_check'
  available: boolean
  boundary: string | null
  executed: boolean
  parameters: Record<string, string | number>
}

// mode 只在提问／建会话时显式传递；追问沿用会话已保存的范围与排序方式。
function scopePayload(scope: QaScope, mode?: QaMode) {
  return {
    semester_id: scope.semester_id,
    course_id: scope.course_id,
    class_name: scope.class_name,
    ...(mode ? { mode } : {}),
  }
}

export const qaApi = {
  // 一次性提问不建会话；无候选时由后端返回固定"未找到"文案，不调用模型。
  ask: (question: string, scope: QaScope, mode: QaMode) =>
    apiRequest<QaAnswer>('/qa/query', {
      method: 'POST',
      body: JSON.stringify({ question, ...scopePayload(scope, mode) }),
    }),
  // 只做意图识别；available=false 表示该意图尚未上线，不执行任意工具。
  interpret: (question: string, scope: QaScope) =>
    apiRequest<QaIntent>('/qa/intent', {
      method: 'POST',
      body: JSON.stringify({ question, ...scopePayload(scope) }),
    }),
  listConversations: () => apiRequest<QaConversation[]>('/qa/conversations'),
  createConversation: (scope: QaScope, mode: QaMode) =>
    apiRequest<QaConversation>('/qa/conversations', {
      method: 'POST',
      body: JSON.stringify(scopePayload(scope, mode)),
    }),
  // 每轮保存范围与引用快照；读取旧轮次时服务端会重新核对来源是否仍可验证。
  getConversation: (id: number) =>
    apiRequest<QaConversationDetail>(`/qa/conversations/${id}`),
  // 切换范围必须显式操作，之后追问不会跨学期或跨课程混合资料。
  switchScope: (id: number, scope: QaScope) =>
    apiRequest<QaConversation>(`/qa/conversations/${id}/scope`, {
      method: 'PATCH',
      body: JSON.stringify(scopePayload(scope)),
    }),
  // 追问只提交问题，范围取自会话当前快照。
  sendMessage: (id: number, question: string) =>
    apiRequest<QaAnswer>(`/qa/conversations/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
}
