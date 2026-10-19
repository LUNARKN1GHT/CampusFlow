// 课程路径：培养方案版本、课程与三类关系、别名、修读记录，以及图／顺序／下一步／学期试排分析。
// 分析接口都是只读计算结果，不落库；缺规则时返回 limitations 而不是伪造结论。

import { apiRequest } from './client'
import type {
  AliasResolution,
  CourseAlias,
  CourseRelation,
  CourseStudyRecord,
  CourseStudyStatusSummary,
  CourseGraph,
  CourseOrder,
  CurriculumCourse,
  CurriculumPlan,
  LearnerProfile,
  NextCourses,
  SemesterPlan,
  SemesterPlanInput,
  StudyStatus,
  SwitchPreview,
} from './types'

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') query.set(key, String(value))
  })
  const text = query.toString()
  return text ? `?${text}` : ''
}

export type CurriculumPlanInput = {
  title: string
  major: string
  grade_year: number
  version_label: string
  credit_limit_per_term: number | null
}

export type CurriculumCourseInput = {
  name: string
  code: string | null
  credits: number | null
  offered_terms: string[]
}

export type StudyRecordInput = {
  curriculum_course_id: number
  semester_id: number | null
  status: StudyStatus
  grade: string | null
  credits_earned: number | null
  note: string | null
}

export const pathsApi = {
  listPlans: () => apiRequest<CurriculumPlan[]>('/curriculum-plans'),
  createPlan: (input: CurriculumPlanInput) =>
    apiRequest<CurriculumPlan>('/curriculum-plans', { method: 'POST', body: JSON.stringify(input) }),
  updatePlan: (id: number, input: CurriculumPlanInput) =>
    apiRequest<CurriculumPlan>(`/curriculum-plans/${id}`, { method: 'PUT', body: JSON.stringify(input) }),
  profile: () => apiRequest<LearnerProfile>('/learning-profile'),
  updateProfile: (input: { major: string | null; grade_year: number | null; curriculum_plan_id: number | null }) =>
    apiRequest<LearnerProfile>('/learning-profile', { method: 'PUT', body: JSON.stringify(input) }),
  // 切换培养方案前的影响预览（关联数、课程数、修读记录数），不写库；确认仍走 updateProfile。
  switchPreview: (planId: number) =>
    apiRequest<SwitchPreview>(`/learning-profile/switch-preview${queryString({ plan_id: planId })}`),
  listCourses: (planId: number) =>
    apiRequest<CurriculumCourse[]>(`/curriculum-plans/${planId}/courses`),
  createCourse: (planId: number, input: CurriculumCourseInput) =>
    apiRequest<CurriculumCourse>(`/curriculum-plans/${planId}/courses`, { method: 'POST', body: JSON.stringify(input) }),
  updateCourse: (planId: number, courseId: number, input: CurriculumCourseInput) =>
    apiRequest<CurriculumCourse>(`/curriculum-plans/${planId}/courses/${courseId}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteCourse: (planId: number, courseId: number) =>
    apiRequest<void>(`/curriculum-plans/${planId}/courses/${courseId}`, { method: 'DELETE' }),
  listRelations: (planId: number) =>
    apiRequest<CourseRelation[]>(`/curriculum-plans/${planId}/relations`),
  createRelation: (planId: number, input: { from_course_id: number; to_course_id: number; relation_type: string; condition_text: string | null }) =>
    apiRequest<CourseRelation>(`/curriculum-plans/${planId}/relations`, { method: 'POST', body: JSON.stringify(input) }),
  deleteRelation: (planId: number, relationId: number) =>
    apiRequest<void>(`/curriculum-plans/${planId}/relations/${relationId}`, { method: 'DELETE' }),
  listAliases: (planId: number) =>
    apiRequest<CourseAlias[]>(`/curriculum-plans/${planId}/aliases`),
  createAlias: (planId: number, input: { curriculum_course_id: number; alias: string; class_name: string | null; semester_id: number | null }) =>
    apiRequest<CourseAlias>(`/curriculum-plans/${planId}/aliases`, { method: 'POST', body: JSON.stringify(input) }),
  deleteAlias: (planId: number, aliasId: number) =>
    apiRequest<void>(`/curriculum-plans/${planId}/aliases/${aliasId}`, { method: 'DELETE' }),
  // 严格按名称／别名匹配指定学期与教学班，不做模糊猜测；结果只是建议，不改动原适用范围。
  resolveAlias: (planId: number, name: string, scope: { semesterId?: number | null; className?: string | null } = {}) =>
    apiRequest<AliasResolution>(`/curriculum-plans/${planId}/aliases/resolve${queryString({ name, semester_id: scope.semesterId, class_name: scope.className })}`),
  listStudyRecords: (planId: number) =>
    apiRequest<CourseStudyRecord[]>(`/curriculum-plans/${planId}/study-records`),
  createStudyRecord: (planId: number, input: StudyRecordInput) =>
    apiRequest<CourseStudyRecord>(`/curriculum-plans/${planId}/study-records`, { method: 'POST', body: JSON.stringify(input) }),
  updateStudyRecord: (planId: number, recordId: number, input: Partial<StudyRecordInput>) =>
    apiRequest<CourseStudyRecord>(`/curriculum-plans/${planId}/study-records/${recordId}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteStudyRecord: (planId: number, recordId: number) =>
    apiRequest<void>(`/curriculum-plans/${planId}/study-records/${recordId}`, { method: 'DELETE' }),
  studyStatus: (planId: number) =>
    apiRequest<CourseStudyStatusSummary>(`/curriculum-plans/${planId}/study-status`),
  // 只读图：节点、三类边与证据，并单列成环／矛盾／缺项／待核对问题。
  graph: (planId: number) =>
    apiRequest<CourseGraph>(`/curriculum-plans/${planId}/graph`),
  // 只对强制先修分层；成环或缺项时 blocked=true，不返回伪造的完整顺序。
  order: (planId: number) =>
    apiRequest<CourseOrder>(`/curriculum-plans/${planId}/order`),
  // 区分未满足条件与不阻塞的建议先学，建议项不视为硬性门槛。
  nextCourses: (planId: number) =>
    apiRequest<NextCourses>(`/curriculum-plans/${planId}/next`),
  // POST 只为传结构化参数，不保存排课结果；固定声明不构成选课或毕业审核。
  semesterPlan: (planId: number, input: SemesterPlanInput) =>
    apiRequest<SemesterPlan>(`/curriculum-plans/${planId}/semester-plan`, {
      method: 'POST',
      body: JSON.stringify(input),
    }),
}
