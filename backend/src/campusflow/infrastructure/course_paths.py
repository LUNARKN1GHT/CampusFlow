"""培养方案与个人方案的 SQLAlchemy 仓储（C002 起）。"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campusflow.application.ports.repositories import (
    CourseAliasData,
    CourseRelationData,
    CurriculumCourseData,
    CurriculumPlanData,
    LearnerProfileData,
)
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import (
    CourseAlias,
    CourseRelation,
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


class SqlAlchemyCoursePathRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_plans(self, workspace_id: int) -> list[CurriculumPlanData]:
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
        row = self._session.scalar(
            select(CurriculumCourse).where(
                CurriculumCourse.source_candidate_id == candidate_id
            )
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

    def plan_impact_counts(self, workspace_id: int, plan_id: int | None) -> dict[str, int]:
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
        return {
            "courses": int(courses or 0),
            "relations": int(relations or 0),
            "study_records": 0,
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
