// 待核对候选：列表／详情、字段修正、状态转换、转换正式事项，以及版本差异、接受与重复合并。
// 差异、适用性、影响与重复建议都是只读计算；接受与合并必须显式选择字段、填写原因并携带确认信息。

import { apiRequest } from './client'
import type {
  CandidateConversionResult,
  CandidateDiff,
  CandidateKind,
  CandidateReviewDetail,
  CandidateSummary,
  ChangeImpact,
  DuplicateReport,
  FieldOrigin,
  MergePreview,
  MergeRecord,
  MergeResult,
  ReviewStatus,
  VersionAcceptance,
  VersionApplicability,
} from './types'

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export type CandidateReviewFilters = {
  statuses?: ReviewStatus[]
  kind?: CandidateKind | null
  courseId?: number | null
  semesterId?: number | null
  materialId?: number | null
  reason?: string | null
}

export type ReviewStatusInput = {
  status: ReviewStatus
  reason?: string | null
  acknowledge_missing?: boolean
}

export type CandidateSummaryCounts = {
  total: number
  by_status: Partial<Record<ReviewStatus, number>>
  by_reason: Record<string, number>
}

export const candidatesApi = {
  // status 是可重复参数，queryString 只支持单值，故在列表接口里显式追加。
  list: (filters: CandidateReviewFilters = {}) => {
    const search = new URLSearchParams(queryString({
      kind: filters.kind,
      course_id: filters.courseId,
      semester_id: filters.semesterId,
      material_id: filters.materialId,
      reason: filters.reason,
    }).slice(1))
    ;(filters.statuses ?? []).forEach((status) => search.append('status', status))
    const text = search.toString()
    return apiRequest<CandidateSummary[]>(`/candidates${text ? `?${text}` : ''}`)
  },
  summary: (filters: Omit<CandidateReviewFilters, 'statuses' | 'reason'> = {}) => apiRequest<CandidateSummaryCounts>(`/candidates/summary${queryString({
    kind: filters.kind,
    course_id: filters.courseId,
    semester_id: filters.semesterId,
    material_id: filters.materialId,
  })}`),
  get: (id: number) => apiRequest<CandidateReviewDetail>(`/candidates/${id}`),
  // 只追加修正记录，原始提取与证据不改写；origin 只能是用户补充或规划建议。
  reviseField: (id: number, fieldName: string, input: { value: unknown; origin: FieldOrigin; note?: string | null }) => apiRequest<CandidateReviewDetail>(`/candidates/${id}/fields/${encodeURIComponent(fieldName)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
  // 按候选类型分派转换；已有来源关联时 created=false，界面据此提示没有重复创建。
  convert: (id: number) => apiRequest<CandidateConversionResult>(`/candidates/${id}/convert`, { method: 'POST' }),
  // 状态转换受后端 allowed_transitions 限制；缺必需字段时需 acknowledge_missing 并写原因。
  setReviewStatus: (id: number, input: ReviewStatusInput) => apiRequest<CandidateReviewDetail>(`/candidates/${id}/review-status`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
  // 只读比较两个显式指定的候选，不自动配对，也不判断版本是否生效。
  diff: (baselineId: number, comparisonId: number) => apiRequest<CandidateDiff>(`/candidates/${baselineId}/diff/${comparisonId}`),
  applicability: (baselineId: number, comparisonId: number) => apiRequest<VersionApplicability>(`/candidates/${baselineId}/diff/${comparisonId}/applicability`),
  // 变更影响只列出已关联的任务、时间块、日程与规则；未接入能力在 unsupported 中说明。
  impact: (baselineId: number, comparisonId: number) => apiRequest<ChangeImpact>(`/candidates/${baselineId}/diff/${comparisonId}/impact`),
  // 用户勾选字段并填写原因后才调用；存在冲突时须 acknowledge_conflict 才接受。
  accept: (baselineId: number, comparisonId: number, input: { field_names: string[]; reason: string; acknowledge_conflict: boolean }) => apiRequest<VersionAcceptance>(`/candidates/${baselineId}/diff/${comparisonId}/accept`, {
    method: 'POST',
    body: JSON.stringify(input),
  }),
  acceptances: (candidateId: number) => apiRequest<VersionAcceptance[]>(`/candidates/${candidateId}/acceptances`),
  // 只读建议：auto_merge 恒为 false，不删除、不合并、不改核对状态。
  duplicates: (candidateId: number) => apiRequest<DuplicateReport>(`/candidates/${candidateId}/duplicates`),
  // source_ids 为可重复参数，故手工拼接；返回的 token 用于限定后续合并范围。
  mergePreview: (candidateId: number, sourceIds: number[]) => apiRequest<MergePreview>(`/candidates/${candidateId}/merge-preview?${sourceIds.map((id) => `source_ids=${id}`).join('&')}`),
  // 必须携带预览令牌；重试幂等，来源候选的快照与进度写入合并历史。
  merge: (candidateId: number, input: { source_candidate_ids: number[]; preview_token: string; reason: string }) => apiRequest<MergeResult>(`/candidates/${candidateId}/merge`, {
    method: 'POST',
    body: JSON.stringify(input),
  }),
  merges: (candidateId: number) => apiRequest<MergeRecord[]>(`/candidates/${candidateId}/merges`),
}
