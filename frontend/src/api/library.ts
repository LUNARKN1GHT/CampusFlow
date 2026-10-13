import { apiRequest, apiRequestBlob } from './client'
import type {
  JobAttempt,
  Material,
  MaterialDeleteImpact,
  MaterialMetadataInput,
  MaterialSourceType,
  MaterialStatus,
  MaterialUploadItem,
  MaterialUploadParams,
  MaterialVersion,
  ProcessingJob,
  SourceChunk,
  TextImportInput,
} from './types'

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export type MaterialFilters = {
  semesterId?: number | null
  courseId?: number | null
  sourceType?: MaterialSourceType | null
  status?: MaterialStatus | null
  includeArchived?: boolean
}

export type TextImportResult = { material: Material; chunks: SourceChunk[] }
export type UploadResult = { results: MaterialUploadItem[] }
export type DeleteResult = { delivered: number; failed: number }

export const materialsApi = {
  list: (filters: MaterialFilters = {}) => apiRequest<Material[]>(`/materials${queryString({
    semester_id: filters.semesterId,
    course_id: filters.courseId,
    source_type: filters.sourceType,
    status: filters.status,
    include_archived: filters.includeArchived,
  })}`),
  get: (id: number) => apiRequest<Material>(`/materials/${id}`),
  importText: (input: TextImportInput) => apiRequest<TextImportResult>('/materials/text', {
    method: 'POST',
    body: JSON.stringify(input),
  }),
  upload: (files: File[], params: MaterialUploadParams) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file, file.name))
    return apiRequest<UploadResult>(`/materials/upload${queryString({
      semester_id: params.semester_id,
      course_id: params.course_id,
      class_name: params.class_name,
      allow_duplicate: params.allow_duplicate,
    })}`, { method: 'POST', body: form })
  },
  update: (id: number, input: MaterialMetadataInput) => apiRequest<Material>(`/materials/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  }),
  archive: (id: number) => apiRequest<Material>(`/materials/${id}/archive`, { method: 'POST' }),
  restore: (id: number) => apiRequest<Material>(`/materials/${id}/restore`, { method: 'POST' }),
  deleteImpact: (id: number) => apiRequest<MaterialDeleteImpact>(`/materials/${id}/delete-impact`),
  remove: (id: number, confirmToken: string, keepTasks: boolean) => apiRequest<void>(`/materials/${id}/delete`, {
    method: 'POST',
    body: JSON.stringify({ confirm_token: confirmToken, keep_tasks: keepTasks }),
  }),
  versions: (id: number) => apiRequest<MaterialVersion[]>(`/materials/${id}/versions`),
  chunks: (id: number, versionNo: number) => apiRequest<SourceChunk[]>(`/materials/${id}/versions/${versionNo}/chunks`),
  fileBlob: (id: number, versionNo: number) => apiRequestBlob(`/materials/${id}/versions/${versionNo}/file`),
}

export const jobsApi = {
  forMaterial: (id: number) => apiRequest<ProcessingJob[]>(`/jobs?material_id=${id}`),
  get: (id: number) => apiRequest<ProcessingJob>(`/jobs/${id}`),
  attempts: (id: number) => apiRequest<JobAttempt[]>(`/jobs/${id}/attempts`),
  retry: (id: number) => apiRequest<ProcessingJob>(`/jobs/${id}/retry`, { method: 'POST' }),
  redeliver: () => apiRequest<DeleteResult>('/jobs/redeliver', { method: 'POST' }),
}
