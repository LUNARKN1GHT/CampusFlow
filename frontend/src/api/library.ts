// 资料库与后台处理作业：导入（文件／网页／粘贴文本）→ 版本与片段 → 归档／删除。
// 文件上传走 multipart（归属范围放在查询参数里），删除必须先取影响预览再用其中的确认令牌。

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
  WebImportInput,
  WebImportResult,
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
  importWeb: (input: WebImportInput) => apiRequest<WebImportResult>('/materials/web', {
    method: 'POST',
    body: JSON.stringify(input),
  }),
  // 多文件一次提交，逐项返回成功、重复或失败；allow_duplicate 决定重复文件是否仍导入。
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
  // 归档与恢复只切换标记：归档后退出检索与问答，原有片段与引用保留。
  archive: (id: number) => apiRequest<Material>(`/materials/${id}/archive`, { method: 'POST' }),
  restore: (id: number) => apiRequest<Material>(`/materials/${id}/restore`, { method: 'POST' }),
  deleteImpact: (id: number) => apiRequest<MaterialDeleteImpact>(`/materials/${id}/delete-impact`),
  // confirm_token 绑定预览时的影响内容，任何一项变化都会让旧令牌失效；keep_tasks 决定关联任务的去留。
  remove: (id: number, confirmToken: string, keepTasks: boolean) => apiRequest<void>(`/materials/${id}/delete`, {
    method: 'POST',
    body: JSON.stringify({ confirm_token: confirmToken, keep_tasks: keepTasks }),
  }),
  versions: (id: number) => apiRequest<MaterialVersion[]>(`/materials/${id}/versions`),
  // 按版本号而非版本 ID 取片段，便于引用直接用"资料 #id · 版本 #no · 片段 #id"定位。
  chunks: (id: number, versionNo: number) => apiRequest<SourceChunk[]>(`/materials/${id}/versions/${versionNo}/chunks`),
  fileBlob: (id: number, versionNo: number) => apiRequestBlob(`/materials/${id}/versions/${versionNo}/file`),
}

export const jobsApi = {
  forMaterial: (id: number) => apiRequest<ProcessingJob[]>(`/jobs?material_id=${id}`),
  get: (id: number) => apiRequest<ProcessingJob>(`/jobs/${id}`),
  attempts: (id: number) => apiRequest<JobAttempt[]>(`/jobs/${id}/attempts`),
  retry: (id: number) => apiRequest<ProcessingJob>(`/jobs/${id}/retry`, { method: 'POST' }),
  // 队列曾不可用时的补救入口：重投本空间所有待投递作业，返回成功与失败数量。
  redeliver: () => apiRequest<DeleteResult>('/jobs/redeliver', { method: 'POST' }),
}
