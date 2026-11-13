// 资料库与后台处理作业：导入（文件／网页／粘贴文本）→ 版本与片段 → 归档／删除。
// 文件上传走 multipart（归属范围放在查询参数里），删除必须先取影响预览再用其中的确认令牌。

import { queryString } from '../utils/query'

import { apiRequest, apiRequestBlob } from './client'
import type {
  Material,
  MaterialDeleteImpact,
  MaterialRecognition,
  MaterialRecognitionStart,
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

export type MaterialFilters = {
  semesterId?: number | null
  courseId?: number | null
  sourceType?: MaterialSourceType | null
  status?: MaterialStatus | null
  includeArchived?: boolean
}

export type TextImportResult = { material: Material; chunks: SourceChunk[] }
export type UploadResult = { results: MaterialUploadItem[] }

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
  // 粘贴文本只保存原文；识别状态与“开始识别”入口分开查询和触发，不自动调用模型。
  recognition: (id: number) => apiRequest<MaterialRecognition>(`/materials/${id}/recognition`),
  // 事项提取在请求内同步调用文字模型，最长等待 120 秒；这里放宽默认 8 秒超时。
  startRecognition: (id: number) => apiRequest<MaterialRecognitionStart>(`/materials/${id}/recognition`, {
    method: 'POST',
  }, 130000),
  // 网页抓取在后端最长可等 15 秒；前端至少放宽到 30 秒，避免正常慢页面被 8 秒默认超时误判。
  // 超时只表示前端先放弃等待，后端可能仍会建档，界面据此提示“结果未知”。
  importWeb: (input: WebImportInput) => apiRequest<WebImportResult>('/materials/web', {
    method: 'POST',
    body: JSON.stringify(input),
  }, 30000),
  // 多文件一次提交，逐项返回成功、重复或失败；allow_duplicate 决定重复文件是否仍导入。
  // 单文件上限 20 MB，读取与解析可能远超 8 秒，这里至少放宽到 60 秒。
  upload: (files: File[], params: MaterialUploadParams) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file, file.name))
    return apiRequest<UploadResult>(`/materials/upload${queryString({
      semester_id: params.semester_id,
      course_id: params.course_id,
      class_name: params.class_name,
      allow_duplicate: params.allow_duplicate,
    })}`, { method: 'POST', body: form }, 60000)
  },
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
  retry: (id: number) => apiRequest<ProcessingJob>(`/jobs/${id}/retry`, { method: 'POST' }),
}
