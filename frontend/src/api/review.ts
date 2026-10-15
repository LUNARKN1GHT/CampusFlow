import { apiRequest } from './client'
import type {
  CandidateKind,
  CandidateReviewDetail,
  CandidateSummary,
  ReviewStatus,
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
  setReviewStatus: (id: number, input: ReviewStatusInput) => apiRequest<CandidateReviewDetail>(`/candidates/${id}/review-status`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
}
