"""培养方案与个人方案的 SQLAlchemy 仓储（C002 起）。"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campusflow.application.ports.repositories import (
    CourseAliasData,
    CourseRelationData,
    CourseStudyRecordData,
    CurriculumCourseData,
    CurriculumPlanData,
    LearnerProfileData,
)
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import (
    CourseAlias,
    CourseRelation,
    CourseStudyRecord,
    CurriculumCourse,
    CurriculumPlan,
    LearnerProfile,
)


def _plan_to_data(row: CurriculumPlan) -> CurriculumPlanData:
    return CurriculumPlanData(
        id=row.id,
        workspace_id=row.workspace_id,
        title=row.title,
        major=row.major,
        grade_year=row.grade_year,
        version_label=row.version_label,
        credit_limit_per_term=row.credit_limit_per_term,
        source_material_id=row.source_material_id,
        source_version_id=row.source_version_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _course_to_data(row: CurriculumCourse) -> CurriculumCourseData:
    return CurriculumCourseData(
        id=row.id,
        workspace_id=row.workspace_id,
        curriculum_plan_id=row.curriculum_plan_id,
        name=row.name,
        code=row.code,
        credits=row.credits,
        offered_terms=list(row.offered_terms or []),
        source_candidate_id=row.source_candidate_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _alias_to_data(row: CourseAlias) -> CourseAliasData:
    return CourseAliasData(
        id=row.id,
        workspace_id=row.workspace_id,
        curriculum_plan_id=row.curriculum_plan_id,
        curriculum_course_id=row.curriculum_course_id,
        alias=row.alias,
        class_name=row.class_name,
        semester_id=row.semester_id,
        created_at=row.created_at,
    )


def _relation_to_data(row: CourseRelation) -> CourseRelationData:
    return CourseRelationData(
        id=row.id,
        workspace_id=row.workspace_id,
        curriculum_plan_id=row.curriculum_plan_id,
        from_course_id=row.from_course_id,
        to_course_id=row.to_course_id,
        relation_type=row.relation_type,
        condition_text=row.condition_text,
        source_candidate_id=row.source_candidate_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _profile_to_data(row: LearnerProfile) -> LearnerProfileData:
    return LearnerProfileData(
        workspace_id=row.workspace_id,
        major=row.major,
        grade_year=row.grade_year,
        curriculum_plan_id=row.curriculum_plan_id,
        updated_at=row.updated_at,
    )


def _study_record_to_data(row: CourseStudyRecord) -> CourseStudyRecordData:
    return CourseStudyRecordData(
        id=row.id,
        workspace_id=row.workspace_id,
        curriculum_course_id=row.curriculum_course_id,
        semester_id=row.semester_id,
        status=row.status,
        grade=row.grade,
        credits_earned=row.credits_earned,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


STUDY_RECORD_CONFLICT_MESSAGE = "该课程在相同学期与状态下已有修读记录"


class SqlAlchemyCoursePathRepository:
    """课程路径相关表的仓储；唯一约束冲突统一转为中文领域错误。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_plans(self, workspace_id: int) -> list[CurriculumPlanData]:
        """按专业、年级与版本排序，保证列表顺序稳定。"""
        rows = self._session.scalars(
            select(CurriculumPlan)
            .where(CurriculumPlan.workspace_id == workspace_id)
            .order_by(CurriculumPlan.major, CurriculumPlan.grade_year, CurriculumPlan.version_label)
        ).all()
        return [_plan_to_data(row) for row in rows]

    def get_plan(self, plan_id: int) -> CurriculumPlanData | None:
        row = self._session.get(CurriculumPlan, plan_id)
        return None if row is None else _plan_to_data(row)

    def create_plan(
        self,
        workspace_id: int,
        *,
        title: str,
        major: str,
        grade_year: int,
        version_label: str,
        credit_limit_per_term: int | None,
    ) -> CurriculumPlanData:
        """新建方案；同一工作空间内专业、年级与版本唯一。"""
        row = CurriculumPlan(
            workspace_id=workspace_id,
            title=title,
            major=major,
            grade_year=grade_year,
            version_label=version_label,
            credit_limit_per_term=credit_limit_per_term,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            # flush 失败后事务已不可继续，回滚并转成可展示的领域错误。
            self._session.rollback()
            raise DomainError("同一专业、年级和版本的培养方案已存在") from None
        return _plan_to_data(row)

    def update_plan(
        self,
        plan_id: int,
        *,
        title: str,
        major: str,
        grade_year: int,
        version_label: str,
        credit_limit_per_term: int | None,
    ) -> CurriculumPlanData | None:
        """修改方案；改名或改版本同样受唯一约束限制。"""
        row = self._session.get(CurriculumPlan, plan_id)
        if row is None:
            return None
        row.title = title
        row.major = major
        row.grade_year = grade_year
        row.version_label = version_label
        row.credit_limit_per_term = credit_limit_per_term
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("同一专业、年级和版本的培养方案已存在") from None
        return _plan_to_data(row)

    def list_courses(self, plan_id: int) -> list[CurriculumCourseData]:
        """按课程名排序返回方案内课程。"""
        rows = self._session.scalars(
            select(CurriculumCourse)
            .where(CurriculumCourse.curriculum_plan_id == plan_id)
            .order_by(CurriculumCourse.name)
        ).all()
        return [_course_to_data(row) for row in rows]

    def get_course(self, course_id: int) -> CurriculumCourseData | None:
        row = self._session.get(CurriculumCourse, course_id)
        return None if row is None else _course_to_data(row)

    def create_course(
        self,
        workspace_id: int,
        plan_id: int,
        *,
        name: str,
        code: str | None,
        credits: float | None,
        offered_terms: list[str],
        source_candidate_id: int | None = None,
    ) -> CurriculumCourseData:
        """新建课程；同一方案内课程名唯一，来源候选编号用于转换幂等。"""
        row = CurriculumCourse(
            workspace_id=workspace_id,
            curriculum_plan_id=plan_id,
            name=name,
            code=code,
            credits=credits,
            offered_terms=offered_terms,
            source_candidate_id=source_candidate_id,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("同一方案内已存在同名课程") from None
        return _course_to_data(row)

    def get_course_by_source_candidate(self, candidate_id: int) -> CurriculumCourseData | None:
        """按来源候选找回已建课程，重复转换时返回同一条。"""
        row = self._session.scalar(
            select(CurriculumCourse).where(CurriculumCourse.source_candidate_id == candidate_id)
        )
        return None if row is None else _course_to_data(row)

    def update_course(
        self,
        course_id: int,
        *,
        name: str,
        code: str | None,
        credits: float | None,
        offered_terms: list[str],
    ) -> CurriculumCourseData | None:
        """修改课程；改名仍需满足方案内唯一。"""
        row = self._session.get(CurriculumCourse, course_id)
        if row is None:
            return None
        row.name = name
        row.code = code
        row.credits = credits
        row.offered_terms = offered_terms
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("同一方案内已存在同名课程") from None
        return _course_to_data(row)

    def delete_course(self, course_id: int) -> bool:
        """仍被任一课程关系引用时拒绝删除，避免留下悬空先修关系。"""
        row = self._session.get(CurriculumCourse, course_id)
        if row is None:
            return False
        related = self._session.scalar(
            select(func.count())
            .select_from(CourseRelation)
            .where(
                (CourseRelation.from_course_id == course_id)
                | (CourseRelation.to_course_id == course_id)
            )
        )
        if related:
            raise DomainError("课程仍被课程关系引用，请先删除相关关系")
        self._session.delete(row)
        self._session.flush()
        return True

    def list_relations(
        self, plan_id: int, *, relation_type: str | None = None
    ) -> list[CourseRelationData]:
        """按关系类型过滤；顺序固定便于界面展示与图分析复用。"""
        stmt = select(CourseRelation).where(CourseRelation.curriculum_plan_id == plan_id)
        if relation_type is not None:
            stmt = stmt.where(CourseRelation.relation_type == relation_type)
        rows = self._session.scalars(
            stmt.order_by(CourseRelation.from_course_id, CourseRelation.to_course_id)
        ).all()
        return [_relation_to_data(row) for row in rows]

    def get_relation(self, relation_id: int) -> CourseRelationData | None:
        row = self._session.get(CourseRelation, relation_id)
        return None if row is None else _relation_to_data(row)

    def create_relation(
        self,
        workspace_id: int,
        plan_id: int,
        *,
        from_course_id: int,
        to_course_id: int,
        relation_type: str,
        condition_text: str | None,
        source_candidate_id: int | None = None,
    ) -> CourseRelationData:
        """新建关系；同方案内同方向同类型唯一，自环留给分析层显式报告。"""
        row = CourseRelation(
            workspace_id=workspace_id,
            curriculum_plan_id=plan_id,
            from_course_id=from_course_id,
            to_course_id=to_course_id,
            relation_type=relation_type,
            condition_text=condition_text,
            source_candidate_id=source_candidate_id,
            created_by="user",
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("相同方向与类型的关系已存在") from None
        return _relation_to_data(row)

    def get_relation_by_source_candidate(self, candidate_id: int) -> CourseRelationData | None:
        """按来源候选找回已建关系，重复转换时返回同一条。"""
        row = self._session.scalar(
            select(CourseRelation).where(CourseRelation.source_candidate_id == candidate_id)
        )
        return None if row is None else _relation_to_data(row)

    def update_relation(
        self,
        relation_id: int,
        *,
        relation_type: str,
        condition_text: str | None,
    ) -> CourseRelationData | None:
        """修改关系；改类型仍需满足方向与类型唯一。"""
        row = self._session.get(CourseRelation, relation_id)
        if row is None:
            return None
        row.relation_type = relation_type
        row.condition_text = condition_text
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("相同方向与类型的关系已存在") from None
        return _relation_to_data(row)

    def delete_relation(self, relation_id: int) -> bool:
        row = self._session.get(CourseRelation, relation_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_aliases(self, plan_id: int) -> list[CourseAliasData]:
        """别名只用于名称匹配，按别名排序；不合并课程或教学班。"""
        rows = self._session.scalars(
            select(CourseAlias)
            .where(CourseAlias.curriculum_plan_id == plan_id)
            .order_by(CourseAlias.alias)
        ).all()
        return [_alias_to_data(row) for row in rows]

    def get_alias(self, alias_id: int) -> CourseAliasData | None:
        row = self._session.get(CourseAlias, alias_id)
        return None if row is None else _alias_to_data(row)

    def create_alias(
        self,
        workspace_id: int,
        plan_id: int,
        *,
        curriculum_course_id: int,
        alias: str,
        class_name: str | None,
        semester_id: int | None,
    ) -> CourseAliasData:
        """新建别名；课程、别名、教学班与学期组合唯一，空值按相同值比较。"""
        row = CourseAlias(
            workspace_id=workspace_id,
            curriculum_plan_id=plan_id,
            curriculum_course_id=curriculum_course_id,
            alias=alias,
            class_name=class_name,
            semester_id=semester_id,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("该课程在相同范围内已有这个别名") from None
        return _alias_to_data(row)

    def delete_alias(self, alias_id: int) -> bool:
        row = self._session.get(CourseAlias, alias_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def list_study_records(self, plan_id: int) -> list[CourseStudyRecordData]:
        """按课程名与学期排序，未知学期排在最前，便于补录。"""
        rows = self._session.scalars(
            select(CourseStudyRecord)
            .join(
                CurriculumCourse,
                CourseStudyRecord.curriculum_course_id == CurriculumCourse.id,
            )
            .where(CurriculumCourse.curriculum_plan_id == plan_id)
            .order_by(
                CurriculumCourse.name,
                CourseStudyRecord.semester_id.nulls_first(),
                CourseStudyRecord.id,
            )
        ).all()
        return [_study_record_to_data(row) for row in rows]

    def get_study_record(self, record_id: int) -> CourseStudyRecordData | None:
        row = self._session.get(CourseStudyRecord, record_id)
        return None if row is None else _study_record_to_data(row)

    def create_study_record(
        self,
        workspace_id: int,
        *,
        curriculum_course_id: int,
        semester_id: int | None,
        status: str,
        grade: str | None,
        credits_earned: float | None,
        note: str | None,
    ) -> CourseStudyRecordData:
        """新建修读记录；同一课程、学期与状态唯一，空学期按同一组比较。"""
        row = CourseStudyRecord(
            workspace_id=workspace_id,
            curriculum_course_id=curriculum_course_id,
            semester_id=semester_id,
            status=status,
            grade=grade,
            credits_earned=credits_earned,
            note=note,
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError(STUDY_RECORD_CONFLICT_MESSAGE) from None
        return _study_record_to_data(row)

    def update_study_record(
        self,
        record_id: int,
        *,
        semester_id: int | None,
        status: str,
        grade: str | None,
        credits_earned: float | None,
        note: str | None,
    ) -> CourseStudyRecordData | None:
        """修改修读记录；改学期或状态时需重新满足唯一约束。"""
        row = self._session.get(CourseStudyRecord, record_id)
        if row is None:
            return None
        row.semester_id = semester_id
        row.status = status
        row.grade = grade
        row.credits_earned = credits_earned
        row.note = note
        try:
            self._session.flush()
        except IntegrityError:
            self._session.rollback()
            raise DomainError(STUDY_RECORD_CONFLICT_MESSAGE) from None
        return _study_record_to_data(row)

    def delete_study_record(self, record_id: int) -> bool:
        row = self._session.get(CourseStudyRecord, record_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def plan_impact_counts(self, workspace_id: int, plan_id: int | None) -> dict[str, int]:
        """删除方案前的影响计数；方案编号为空时返回全零，不扫描其他方案。"""
        if plan_id is None:
            return {"courses": 0, "relations": 0, "study_records": 0}
        courses = self._session.scalar(
            select(func.count())
            .select_from(CurriculumCourse)
            .where(CurriculumCourse.curriculum_plan_id == plan_id)
        )
        relations = self._session.scalar(
            select(func.count())
            .select_from(CourseRelation)
            .where(CourseRelation.curriculum_plan_id == plan_id)
        )
        study_records = self._session.scalar(
            select(func.count())
            .select_from(CourseStudyRecord)
            .join(
                CurriculumCourse,
                CourseStudyRecord.curriculum_course_id == CurriculumCourse.id,
            )
            .where(CurriculumCourse.curriculum_plan_id == plan_id)
        )
        return {
            "courses": int(courses or 0),
            "relations": int(relations or 0),
            "study_records": int(study_records or 0),
        }

    def get_profile(self, workspace_id: int) -> LearnerProfileData | None:
        row = self._session.get(LearnerProfile, workspace_id)
        return None if row is None else _profile_to_data(row)

    def upsert_profile(
        self,
        workspace_id: int,
        *,
        major: str | None,
        grade_year: int | None,
        curriculum_plan_id: int | None,
    ) -> LearnerProfileData:
        """每个工作空间一条个人配置；未填写字段保持 NULL，不按其他数据推算。"""
        row = self._session.get(LearnerProfile, workspace_id)
        if row is None:
            row = LearnerProfile(
                workspace_id=workspace_id,
                major=major,
                grade_year=grade_year,
                curriculum_plan_id=curriculum_plan_id,
            )
            self._session.add(row)
        else:
            row.major = major
            row.grade_year = grade_year
            row.curriculum_plan_id = curriculum_plan_id
        self._session.flush()
        return _profile_to_data(row)
