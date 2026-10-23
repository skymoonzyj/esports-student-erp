from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.identity.models import Person
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.errors import (
    DuplicateProfessionalTeam,
    DuplicateProject,
    InactiveProject,
    TeamProjectImmutable,
)
from app.teaching.models import GameProject, ProfessionalTeam
from app.teaching.schemas import (
    ProfessionalTeamCreate,
    ProfessionalTeamUpdate,
    ProjectCreate,
    ProjectUpdate,
)


@pytest.fixture
def actor_user_id(db: Session):
    marker = uuid4().hex
    person = Person(
        full_name="教学服务测试操作人",
        identity_ciphertext="catalog-test-ciphertext",
        identity_digest=f"catalog-identity-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"catalog-phone-{marker}",
        email_normalized=f"catalog-{marker}@example.test",
        password_hash="catalog-test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    return account.id


def test_project_names_are_unique_after_trimming(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    created = service.create_project(
        ProjectCreate(name="  英雄联盟青训  "),
        actor_user_id=actor_user_id,
    )

    with pytest.raises(DuplicateProject) as error:
        service.create_project(
            ProjectCreate(name="英雄联盟青训"),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "duplicate_project"
    assert created.name == "英雄联盟青训"
    assert db.scalar(
        select(func.count(GameProject.id)).where(GameProject.name == "英雄联盟青训")
    ) == 1


def test_project_creation_adds_matching_manual_only_version_one(
    db: Session,
    actor_user_id,
) -> None:
    seeded_profile = db.scalar(
        select(ProjectAnalysisProfile).order_by(ProjectAnalysisProfile.project_id).limit(1)
    )
    assert seeded_profile is not None
    project = TeachingCatalogService(db).create_project(
        ProjectCreate(name="分析档案默认项目"),
        actor_user_id=actor_user_id,
    )

    profile = db.scalar(
        select(ProjectAnalysisProfile).where(
            ProjectAnalysisProfile.project_id == project.id
        )
    )

    assert profile is not None
    business_fields = (
        "version",
        "mode",
        "adapter_id",
        "adapter_version",
        "scoring_rule_version",
        "required_fields",
        "confidence_thresholds",
        "auto_publish_warning_levels",
        "publish_mode",
    )
    assert {field: getattr(profile, field) for field in business_fields} == {
        field: getattr(seeded_profile, field) for field in business_fields
    }
    assert profile.configured_by_user_id == actor_user_id
    assert profile.change_reason == "project default"
    assert profile.effective_from.tzinfo is not None


def test_project_creation_rolls_back_when_default_profile_insert_fails(
    db: Session,
    actor_user_id,
) -> None:
    project_name = "分析档案失败回滚项目"

    def reject_profile_insert(*_args: object) -> None:
        raise RuntimeError("forced profile insertion failure")

    event.listen(ProjectAnalysisProfile, "before_insert", reject_profile_insert)
    try:
        with pytest.raises(RuntimeError, match="forced profile insertion failure"):
            TeachingCatalogService(db).create_project(
                ProjectCreate(name=project_name),
                actor_user_id=actor_user_id,
            )
    finally:
        event.remove(ProjectAnalysisProfile, "before_insert", reject_profile_insert)

    assert db.scalar(
        select(func.count(GameProject.id)).where(GameProject.name == project_name)
    ) == 0


def test_inactive_project_rejects_new_team(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    project = service.create_project(
        ProjectCreate(name="停用项目"),
        actor_user_id=actor_user_id,
    )
    service.update_project(
        project.id,
        ProjectUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )
    db.refresh(project)
    updated_at = project.updated_at

    with pytest.raises(InactiveProject) as error:
        service.create_team(
            ProfessionalTeamCreate(project_id=project.id, name="不应创建的队伍"),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "inactive_project"
    db.expire_all()
    stored = db.get(GameProject, project.id)
    assert stored is not None and stored.updated_at == updated_at
    assert db.scalar(
        select(func.count(ProfessionalTeam.id)).where(
            ProfessionalTeam.project_id == project.id
        )
    ) == 0


def test_team_name_is_unique_inside_project(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    project_a = service.create_project(
        ProjectCreate(name="项目 A"),
        actor_user_id=actor_user_id,
    )
    project_b = service.create_project(
        ProjectCreate(name="项目 B"),
        actor_user_id=actor_user_id,
    )
    service.create_team(
        ProfessionalTeamCreate(project_id=project_a.id, name="  同名战队  "),
        actor_user_id=actor_user_id,
    )

    with pytest.raises(DuplicateProfessionalTeam) as error:
        service.create_team(
            ProfessionalTeamCreate(project_id=project_a.id, name="同名战队"),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "duplicate_professional_team"
    service.create_team(
        ProfessionalTeamCreate(project_id=project_b.id, name="同名战队"),
        actor_user_id=actor_user_id,
    )
    assert db.scalar(
        select(func.count(ProfessionalTeam.id)).where(
            ProfessionalTeam.name == "同名战队"
        )
    ) == 2


def test_team_project_pair_is_immutable(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    project_a = service.create_project(
        ProjectCreate(name="归属项目 A"),
        actor_user_id=actor_user_id,
    )
    project_b = service.create_project(
        ProjectCreate(name="归属项目 B"),
        actor_user_id=actor_user_id,
    )
    team = service.create_team(
        ProfessionalTeamCreate(
            project_id=project_a.id,
            name="归属不可变战队",
            notes="原备注",
        ),
        actor_user_id=actor_user_id,
    )
    db.refresh(team)
    updated_at = team.updated_at

    with pytest.raises(TeamProjectImmutable) as error:
        service.update_team(
            team.id,
            ProfessionalTeamUpdate(project_id=project_b.id, notes="不应保存"),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "team_project_immutable"
    db.expire_all()
    stored = db.get(ProfessionalTeam, team.id)
    assert stored is not None
    assert stored.project_id == project_a.id
    assert stored.notes == "原备注"
    assert stored.updated_at == updated_at


def test_deactivation_preserves_project_and_team_history(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    project = service.create_project(
        ProjectCreate(name="保留历史项目"),
        actor_user_id=actor_user_id,
    )
    team = service.create_team(
        ProfessionalTeamCreate(project_id=project.id, name="保留历史战队"),
        actor_user_id=actor_user_id,
    )

    service.update_team(
        team.id,
        ProfessionalTeamUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )
    service.update_project(
        project.id,
        ProjectUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )

    projects, project_total = service.list_projects(
        name="保留历史项目",
        include_inactive=True,
        page=1,
        page_size=20,
    )
    teams, team_total = service.list_teams(
        project_id=project.id,
        name="保留历史战队",
        include_inactive=True,
        page=1,
        page_size=20,
    )
    active_projects, _ = service.list_projects(
        name="保留历史项目",
        include_inactive=False,
        page=1,
        page_size=20,
    )
    active_teams, _ = service.list_teams(
        project_id=project.id,
        name="保留历史战队",
        include_inactive=False,
        page=1,
        page_size=20,
    )

    assert project_total == 1 and [item.id for item in projects] == [project.id]
    assert team_total == 1 and [item.id for item in teams] == [team.id]
    assert active_projects == []
    assert active_teams == []


def test_catalog_lists_active_first_then_sort_order_and_name(
    db: Session,
    actor_user_id,
) -> None:
    db.execute(delete(ProjectAnalysisProfile))
    db.execute(delete(GameProject))
    service = TeachingCatalogService(db)
    inactive = service.create_project(
        ProjectCreate(name="最先但停用", sort_order=0),
        actor_user_id=actor_user_id,
    )
    second = service.create_project(
        ProjectCreate(name="A 项目", sort_order=10),
        actor_user_id=actor_user_id,
    )
    third = service.create_project(
        ProjectCreate(name="B 项目", sort_order=10),
        actor_user_id=actor_user_id,
    )
    first = service.create_project(
        ProjectCreate(name="优先项目", sort_order=5),
        actor_user_id=actor_user_id,
    )
    service.update_project(
        inactive.id,
        ProjectUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )

    page_one, total = service.list_projects(
        name=None,
        include_inactive=True,
        page=1,
        page_size=3,
    )
    page_two, second_total = service.list_projects(
        name=None,
        include_inactive=True,
        page=2,
        page_size=3,
    )

    assert total == second_total == 4
    assert [item.id for item in page_one] == [first.id, second.id, third.id]
    assert [item.id for item in page_two] == [inactive.id]


def test_catalog_exact_name_filters_are_trimmed_before_count_and_pagination(
    db: Session,
    actor_user_id,
) -> None:
    service = TeachingCatalogService(db)
    project = service.create_project(
        ProjectCreate(name="精确筛选项目"),
        actor_user_id=actor_user_id,
    )
    service.create_project(
        ProjectCreate(name="精确筛选项目扩展"),
        actor_user_id=actor_user_id,
    )
    team = service.create_team(
        ProfessionalTeamCreate(project_id=project.id, name="精确筛选战队"),
        actor_user_id=actor_user_id,
    )
    service.create_team(
        ProfessionalTeamCreate(project_id=project.id, name="精确筛选战队扩展"),
        actor_user_id=actor_user_id,
    )

    projects, project_total = service.list_projects(
        name="  精确筛选项目  ",
        include_inactive=True,
        page=1,
        page_size=1,
    )
    teams, team_total = service.list_teams(
        project_id=project.id,
        name="  精确筛选战队  ",
        include_inactive=True,
        page=1,
        page_size=1,
    )

    assert project_total == 1 and [item.id for item in projects] == [project.id]
    assert team_total == 1 and [item.id for item in teams] == [team.id]
