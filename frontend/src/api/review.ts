import { apiRequest } from './client'
import type {
  CandidateConversionResult,
  CandidateDiff,
  CandidateKind,
  CandidateReviewDetail,
  CandidateSummary,
  ChangeImpact,
  FieldOrigin,
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
  reviseField: (id: number, fieldName: string, input: { value: unknown; origin: FieldOrigin; note?: string | null }) => apiRequest<CandidateReviewDetail>(`/candidates/${id}/fields/${encodeURIComponent(fieldName)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
  convert: (id: number) => apiRequest<CandidateConversionResult>(`/candidates/${id}/convert`, { method: 'POST' }),
  setReviewStatus: (id: number, input: ReviewStatusInput) => apiRequest<CandidateReviewDetail>(`/candidates/${id}/review-status`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
  diff: (baselineId: number, comparisonId: number) => apiRequest<CandidateDiff>(`/candidates/${baselineId}/diff/${comparisonId}`),
  applicability: (baselineId: number, comparisonId: number) => apiRequest<VersionApplicability>(`/candidates/${baselineId}/diff/${comparisonId}/applicability`),
  impact: (baselineId: number, comparisonId: number) => apiRequest<ChangeImpact>(`/candidates/${baselineId}/diff/${comparisonId}/impact`),
  accept: (baselineId: number, comparisonId: number, input: { field_names: string[]; reason: string; acknowledge_conflict: boolean }) => apiRequest<VersionAcceptance>(`/candidates/${baselineId}/diff/${comparisonId}/accept`, {
    method: 'POST',
    body: JSON.stringify(input),
  }),
  acceptances: (candidateId: number) => apiRequest<VersionAcceptance[]>(`/candidates/${candidateId}/acceptances`),
}
