"""培养方案与个人方案的 SQLAlchemy 仓储（C002 起）。"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campusflow.application.ports.repositories import CurriculumPlanData, LearnerProfileData
from campusflow.domain.errors import DomainError
from campusflow.infrastructure.db.models import CurriculumPlan, LearnerProfile


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

    def plan_impact_counts(self, workspace_id: int, plan_id: int | None) -> dict[str, int]:
        # 课程、关系与修读记录在后续叶子任务接入；当前方案尚未建立这些数据时计为 0。
        return {"courses": 0, "relations": 0, "study_records": 0}

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
