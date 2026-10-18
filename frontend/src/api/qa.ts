import { apiRequest } from './client'

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
  valid: boolean
  status: string
  message: string | null
}

export type QaCoverage = {
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

function scopePayload(scope: QaScope, mode?: QaMode) {
  return {
    semester_id: scope.semester_id,
    course_id: scope.course_id,
    class_name: scope.class_name,
    ...(mode ? { mode } : {}),
  }
}

export const qaApi = {
  ask: (question: string, scope: QaScope, mode: QaMode) =>
    apiRequest<QaAnswer>('/qa/query', {
      method: 'POST',
      body: JSON.stringify({ question, ...scopePayload(scope, mode) }),
    }),
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
  getConversation: (id: number) =>
    apiRequest<QaConversationDetail>(`/qa/conversations/${id}`),
  switchScope: (id: number, scope: QaScope) =>
    apiRequest<QaConversation>(`/qa/conversations/${id}/scope`, {
      method: 'PATCH',
      body: JSON.stringify(scopePayload(scope)),
    }),
  sendMessage: (id: number, question: string) =>
    apiRequest<QaAnswer>(`/qa/conversations/${id}/messages`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
}
