from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.teaching.models import GameProject


@dataclass(frozen=True)
class ProjectSeed:
    id: UUID
    name: str
    sort_order: int


INITIAL_PROJECTS = (
    ProjectSeed(UUID("30000000-0000-4000-8000-000000000001"), "英雄联盟", 10),
    ProjectSeed(UUID("30000000-0000-4000-8000-000000000002"), "和平精英", 20),
    ProjectSeed(UUID("30000000-0000-4000-8000-000000000003"), "三角洲行动", 30),
    ProjectSeed(UUID("30000000-0000-4000-8000-000000000004"), "无畏契约", 40),
)


def seed_initial_projects(db: Session) -> None:
    for project in INITIAL_PROJECTS:
        db.execute(
            postgresql_insert(GameProject)
            .values(
                id=project.id,
                name=project.name,
                sort_order=project.sort_order,
                is_active=True,
                created_by_user_id=None,
            )
            .on_conflict_do_nothing()
        )
        db.execute(
            postgresql_insert(ProjectAnalysisProfile)
            .values(
                id=uuid4(),
                project_id=project.id,
                version=1,
                mode="manual_only",
                adapter_id=None,
                adapter_version=None,
                scoring_rule_version=None,
                required_fields=[],
                confidence_thresholds={},
                auto_publish_warning_levels=[],
                publish_mode="review_required",
                effective_from=datetime.now(UTC),
                configured_by_user_id=None,
                change_reason="migration backfill",
            )
            .on_conflict_do_nothing(
                index_elements=["project_id", "version"],
            )
        )
