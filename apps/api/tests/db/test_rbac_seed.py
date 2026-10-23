from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person
from app.rbac.models import Permission, Role, RolePermission, UserRole
from app.rbac.seed import PERMISSIONS, ROLE_PERMISSION_GRANTS, ROLES
from app.rbac.service import RbacService

ADMISSIONS_PERMISSIONS = {
    "admission_cases.read",
    "admission_cases.manage",
    "payments.read",
    "payments.manage",
    "refunds.manage",
    "enrollments.read",
    "enrollments.manage",
    "enrollments.capacity_override",
    "payment_imports.manage",
}

PORTAL_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000025")
EVALUATION_ACCESS_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000026")
EVALUATION_ACCESS_GRANTS = {
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
}
EVALUATION_FOUNDATION_PERMISSION_IDS = {
    "evaluation_media.upload": UUID("20000000-0000-4000-8000-000000000027"),
    "evaluation_media.original.read": UUID(
        "20000000-0000-4000-8000-000000000028"
    ),
    "analysis_profiles.manage": UUID("20000000-0000-4000-8000-000000000030"),
    "student_game_accounts.manage": UUID(
        "20000000-0000-4000-8000-000000000031"
    ),
    "cohort_staff.manage": UUID("20000000-0000-4000-8000-000000000036"),
}
HOUSING_PERMISSION_IDS = {
    "housing.read": UUID("20000000-0000-4000-8000-000000000037"),
    "housing.rooms.manage": UUID("20000000-0000-4000-8000-000000000038"),
    "housing.stays.manage": UUID("20000000-0000-4000-8000-000000000039"),
    "housing.transfers.manage": UUID("20000000-0000-4000-8000-000000000040"),
    "housing.checkouts.manage": UUID("20000000-0000-4000-8000-000000000041"),
    "housing.issues.manage": UUID("20000000-0000-4000-8000-000000000042"),
    "housing.reports.export_sensitive": UUID(
        "20000000-0000-4000-8000-000000000043"
    ),
}


def test_housing_permissions_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.name, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(HOUSING_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(HOUSING_PERMISSION_IDS))
            )
        }

    assert permissions == {
        "housing.read": (
            HOUSING_PERMISSION_IDS["housing.read"],
            "查看房态",
            False,
        ),
        "housing.rooms.manage": (
            HOUSING_PERMISSION_IDS["housing.rooms.manage"],
            "管理房间目录",
            True,
        ),
        "housing.stays.manage": (
            HOUSING_PERMISSION_IDS["housing.stays.manage"],
            "管理住宿与预约",
            True,
        ),
        "housing.transfers.manage": (
            HOUSING_PERMISSION_IDS["housing.transfers.manage"],
            "管理调房",
            True,
        ),
        "housing.checkouts.manage": (
            HOUSING_PERMISSION_IDS["housing.checkouts.manage"],
            "管理退房",
            True,
        ),
        "housing.issues.manage": (
            HOUSING_PERMISSION_IDS["housing.issues.manage"],
            "管理问题房",
            True,
        ),
        "housing.reports.export_sensitive": (
            HOUSING_PERMISSION_IDS["housing.reports.export_sensitive"],
            "导出房态实名报送",
            True,
        ),
    }
    assert grants == {
        *{
            ("admin", permission_key, "global")
            for permission_key in HOUSING_PERMISSION_IDS
        },
        ("student", "housing.read", "self"),
        ("guardian", "housing.read", "family"),
    }


def test_migration_seed_matches_code_constants(postgres_engine: Engine) -> None:
    with Session(postgres_engine) as db:
        roles = {row.key for row in db.scalars(select(Role))}
        permissions = {row.key for row in db.scalars(select(Permission))}
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
            )
        }

    assert roles == set(ROLES)
    assert permissions == set(PERMISSIONS)
    assert grants == set(ROLE_PERMISSION_GRANTS)


def test_teaching_projects_permissions_and_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    from app.teaching.models import GameProject
    from app.teaching.seed import INITIAL_PROJECTS

    teaching_permission_keys = {
        "teaching.read",
        "projects.manage",
        "professional_teams.manage",
        "cohorts.manage",
        "classes.manage",
        "coach_assignments.manage",
    }
    with Session(postgres_engine) as db:
        projects = {
            row.id: (row.name, row.sort_order, row.is_active, row.created_by_user_id)
            for row in db.scalars(select(GameProject))
        }
        permissions = {
            row.key
            for row in db.scalars(
                select(Permission).where(Permission.key.in_(teaching_permission_keys))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(teaching_permission_keys))
            )
        }

    assert projects == {
        project.id: (project.name, project.sort_order, True, None)
        for project in INITIAL_PROJECTS
    }
    assert permissions == teaching_permission_keys
    assert grants == {
        *{("admin", permission_key, "global") for permission_key in teaching_permission_keys},
        ("customer_service", "teaching.read", "global"),
        ("coach", "teaching.read", "assigned_class"),
        ("guardian", "teaching.read", "family"),
        ("student", "teaching.read", "self"),
    }


def test_admissions_permissions_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            row.key: row.id
            for row in db.scalars(
                select(Permission).where(Permission.key.in_(ADMISSIONS_PERMISSIONS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(ADMISSIONS_PERMISSIONS))
            )
        }

    assert set(permissions) == ADMISSIONS_PERMISSIONS
    assert set(permissions.values()) == {
        UUID(f"20000000-0000-4000-8000-{suffix:012d}")
        for suffix in range(16, 25)
    }
    assert grants == {
        *{
            ("admin", permission_key, "global")
            for permission_key in ADMISSIONS_PERMISSIONS
        },
        ("customer_service", "admission_cases.read", "global"),
        ("customer_service", "enrollments.read", "global"),
        ("coach", "enrollments.read", "assigned_class"),
        ("guardian", "enrollments.read", "family"),
        ("guardian", "payments.read", "self"),
        ("student", "enrollments.read", "self"),
        ("student", "payments.read", "self"),
    }


def test_portal_permission_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(Permission.key == "coach_roster.read")
        )
        grants = {
            (role_key, scope_kind)
            for role_key, scope_kind in db.execute(
                select(Role.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key == "coach_roster.read")
            )
        }

    assert permission is not None
    assert (
        permission.id,
        permission.name,
        permission.sensitive,
    ) == (
        PORTAL_PERMISSION_ID,
        "查看教练下一期名单",
        False,
    )
    assert grants == {
        ("admin", "global"),
        ("coach", "assigned_class"),
    }


def test_evaluation_entry_permission_uses_least_privilege_defaults(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(Permission.key == "evaluation_center.access")
        )
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key == "evaluation_center.access")
            )
        }

    assert permission is not None
    assert (permission.id, permission.sensitive) == (
        EVALUATION_ACCESS_PERMISSION_ID,
        False,
    )
    assert grants == EVALUATION_ACCESS_GRANTS
    assert ("customer_service", "evaluation_center.access", "global") not in grants


def test_evaluation_permissions_use_least_privilege_defaults(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        rows = {
            key: (permission_id, sensitive)
            for key, permission_id, sensitive in db.execute(
                select(Permission.key, Permission.id, Permission.sensitive).where(
                    Permission.key.in_(EVALUATION_FOUNDATION_PERMISSION_IDS)
                )
            )
        }
        grants = set(
            db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(EVALUATION_FOUNDATION_PERMISSION_IDS))
            )
        )

    assert rows == {
        key: (permission_id, True)
        for key, permission_id in EVALUATION_FOUNDATION_PERMISSION_IDS.items()
    }
    assert grants == {
        *{
            ("admin", key, "global")
            for key in EVALUATION_FOUNDATION_PERMISSION_IDS
        },
        ("student", "student_game_accounts.manage", "self"),
        ("student", "evaluation_media.original.read", "self"),
        ("guardian", "evaluation_media.original.read", "family"),
        ("coach", "evaluation_media.original.read", "assigned_class"),
    }
    assert ("guardian", "student_game_accounts.manage", "family") not in grants
    assert ("customer_service", "evaluation_media.upload", "global") not in grants


def test_admissions_visibility_is_an_immutable_projection() -> None:
    from dataclasses import FrozenInstanceError

    from app.admissions.access import AdmissionsVisibility

    visibility = AdmissionsVisibility(
        can_read=True,
        can_manage=False,
        can_read_amounts=False,
    )

    assert visibility.can_read is True
    assert visibility.can_manage is False
    assert visibility.can_read_amounts is False
    with pytest.raises(FrozenInstanceError):
        visibility.can_manage = True  # type: ignore[misc]


def test_admissions_visibility_keeps_amount_access_independent(db: Session) -> None:
    marker = uuid4().hex
    admin_person = Person(
        full_name="招生权限管理员",
        identity_ciphertext="admissions-admin-ciphertext",
        identity_digest=f"admissions-admin-{marker}",
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    customer_person = Person(
        full_name="招生权限客服",
        identity_ciphertext="admissions-customer-ciphertext",
        identity_digest=f"admissions-customer-{marker}",
        sex="female",
        date_of_birth=date(1990, 1, 2),
    )
    db.add_all([admin_person, customer_person])
    db.flush()
    admin = UserAccount(
        person_id=admin_person.id,
        login_phone_digest=f"admissions-admin-phone-{marker}",
        email_normalized=f"admissions-admin-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    customer = UserAccount(
        person_id=customer_person.id,
        login_phone_digest=f"admissions-customer-phone-{marker}",
        email_normalized=f"admissions-customer-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add_all([admin, customer])
    db.flush()
    db.add_all(
        [
            UserRole(user_id=admin.id, role_id=ROLES["admin"].id),
            UserRole(user_id=customer.id, role_id=ROLES["customer_service"].id),
        ]
    )
    db.flush()

    service = RbacService(db)
    admin_visibility = service.admissions_visibility(user_id=admin.id)
    customer_visibility = service.admissions_visibility(user_id=customer.id)

    assert admin_visibility.can_read is True
    assert admin_visibility.can_manage is True
    assert admin_visibility.can_read_amounts is True
    assert customer_visibility.can_read is True
    assert customer_visibility.can_manage is False
    assert customer_visibility.can_read_amounts is False


def test_seed_initial_projects_restores_missing_without_overwriting_edits(
    db: Session,
) -> None:
    from app.evaluations.analysis_models import ProjectAnalysisProfile
    from app.teaching.models import GameProject
    from app.teaching.seed import INITIAL_PROJECTS, seed_initial_projects

    missing_project = INITIAL_PROJECTS[0]
    edited_project = INITIAL_PROJECTS[1]
    db.execute(
        delete(ProjectAnalysisProfile).where(
            ProjectAnalysisProfile.project_id == missing_project.id
        )
    )
    db.execute(delete(GameProject).where(GameProject.id == missing_project.id))
    db.execute(
        update(GameProject)
        .where(GameProject.id == edited_project.id)
        .values(name="管理员保留名称", sort_order=777, is_active=False)
    )

    seed_initial_projects(db)
    db.flush()

    restored = db.get(GameProject, missing_project.id)
    edited = db.get(GameProject, edited_project.id)
    restored_profile = db.scalar(
        select(ProjectAnalysisProfile).where(
            ProjectAnalysisProfile.project_id == missing_project.id
        )
    )
    assert restored is not None
    assert (
        restored.name,
        restored.sort_order,
        restored.is_active,
        restored.created_by_user_id,
    ) == (missing_project.name, missing_project.sort_order, True, None)
    assert edited is not None
    assert (edited.name, edited.sort_order, edited.is_active) == (
        "管理员保留名称",
        777,
        False,
    )
    assert restored_profile is not None
    assert (
        restored_profile.version,
        restored_profile.mode,
        restored_profile.adapter_id,
        restored_profile.adapter_version,
        restored_profile.scoring_rule_version,
        restored_profile.required_fields,
        restored_profile.confidence_thresholds,
        restored_profile.auto_publish_warning_levels,
        restored_profile.publish_mode,
        restored_profile.configured_by_user_id,
    ) == (1, "manual_only", None, None, None, [], {}, [], "review_required", None)
    assert restored_profile.effective_from.tzinfo is not None
    assert restored_profile.change_reason == "migration backfill"
