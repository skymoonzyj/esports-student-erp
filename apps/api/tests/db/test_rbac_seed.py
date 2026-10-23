from datetime import date
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person
from app.rbac.models import Permission, Role, RolePermission, UserRole
from app.rbac.seed import (
    ADMIN_DEFAULT_EXCLUDED_PERMISSIONS,
    ADMIN_GLOBAL_ASSET_PERMISSIONS,
    ADMIN_OPERATIONAL_FINANCE_PERMISSIONS,
    PERMISSIONS,
    ROLE_PERMISSION_GRANTS,
    ROLES,
    SELF_SCOPE_ONLY_PERMISSIONS,
)
from app.rbac.service import RbacService


def test_new_permissions_never_default_to_admin_global():
    assert "esports_assessments.take" in SELF_SCOPE_ONLY_PERMISSIONS
    assert "esports_assessments.read" in ADMIN_DEFAULT_EXCLUDED_PERMISSIONS
    grants = {p for role, p, _ in ROLE_PERMISSION_GRANTS if role == "admin"}
    assert grants.isdisjoint({"esports_assessments.take", "esports_assessments.read"})
    expected = {
        ("super_admin", "esports_assessments.take", "self"),
        ("super_admin", "esports_assessments.read", "global"),
        ("student", "esports_assessments.take", "self"),
        ("student", "esports_assessments.read", "self"),
        ("guardian", "esports_assessments.read", "family"),
        ("coach", "esports_assessments.read", "assigned_class"),
    }
    assert {g for g in ROLE_PERMISSION_GRANTS if g[1].startswith("esports_assessments.")} == expected


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
    ("super_admin", "evaluation_center.access", "global"),
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
}


def _administrator_grants(
    permission_keys: set[str] | dict[str, UUID],
) -> set[tuple[str, str, str]]:
    return {
        (role_key, permission_key, "global")
        for role_key in ("super_admin", "admin")
        for permission_key in permission_keys
    }


EVALUATION_FOUNDATION_PERMISSION_IDS = {
    "evaluation_media.upload": UUID("20000000-0000-4000-8000-000000000027"),
    "evaluation_media.original.read": UUID("20000000-0000-4000-8000-000000000028"),
    "analysis_profiles.manage": UUID("20000000-0000-4000-8000-000000000030"),
    "student_game_accounts.manage": UUID("20000000-0000-4000-8000-000000000031"),
    "cohort_staff.manage": UUID("20000000-0000-4000-8000-000000000036"),
}
SYSTEM_EVALUATION_PERMISSION_IDS = {
    "evaluation_jobs.review": UUID("20000000-0000-4000-8000-000000000029"),
    "evaluation_jobs.match": UUID("20000000-0000-4000-8000-000000000044"),
    "evaluation_jobs.manual_input": UUID("20000000-0000-4000-8000-000000000045"),
    "evaluation_adapters.certify": UUID("20000000-0000-4000-8000-000000000046"),
}
TEACHING_REPORT_PERMISSION_IDS = {
    "student_reports.write": UUID("20000000-0000-4000-8000-000000000032"),
    "student_reports.review": UUID("20000000-0000-4000-8000-000000000033"),
}
HOUSING_PERMISSION_IDS = {
    "housing.read": UUID("20000000-0000-4000-8000-000000000037"),
    "housing.rooms.manage": UUID("20000000-0000-4000-8000-000000000038"),
    "housing.stays.manage": UUID("20000000-0000-4000-8000-000000000039"),
    "housing.transfers.manage": UUID("20000000-0000-4000-8000-000000000040"),
    "housing.checkouts.manage": UUID("20000000-0000-4000-8000-000000000041"),
    "housing.issues.manage": UUID("20000000-0000-4000-8000-000000000042"),
    "housing.reports.export_sensitive": UUID("20000000-0000-4000-8000-000000000043"),
}
FOOD_PREFERENCE_PERMISSION_IDS = {
    "food_preferences.read": UUID("20000000-0000-4000-8000-000000000047"),
    "food_preferences.update": UUID("20000000-0000-4000-8000-000000000048"),
    "food_preferences.catalog.manage": UUID("20000000-0000-4000-8000-000000000049"),
    "food_preferences.export": UUID("20000000-0000-4000-8000-000000000050"),
}
CAMP_SURVEY_PERMISSION_IDS = {
    "camp_surveys.identity.read": UUID("20000000-0000-4000-8000-000000000034"),
    "camp_surveys.print": UUID("20000000-0000-4000-8000-000000000035"),
}
REGISTRATION_REVIEW_PERMISSION_IDS = {
    "registration.review": UUID("20000000-0000-4000-8000-000000000051"),
    "registration.sensitive.read": UUID("20000000-0000-4000-8000-000000000052"),
    "staff_identity_applications.review": UUID("20000000-0000-4000-8000-000000000053"),
}
SYSTEM_ADMIN_PERMISSION_IDS = {
    "accounts.read": UUID("20000000-0000-4000-8000-000000000054"),
    "mail.settings.manage": UUID("20000000-0000-4000-8000-000000000055"),
    "operations.monitor.read": UUID("20000000-0000-4000-8000-000000000071"),
    "project_coaches.manage": UUID("20000000-0000-4000-8000-000000000077"),
}

REVENUE_SHARE_PERMISSION_IDS = {
    "revenue_share.read_self": UUID("20000000-0000-4000-8000-000000000061"),
    "revenue_share.finance": UUID("20000000-0000-4000-8000-000000000062"),
    "revenue_share.manage": UUID("20000000-0000-4000-8000-000000000063"),
    "revenue_share.export": UUID("20000000-0000-4000-8000-000000000064"),
    "revenue_share.create_self_referral": UUID("20000000-0000-4000-8000-000000000076"),
}
PROCUREMENT_PERMISSION_IDS = {
    "procurement.read": UUID("20000000-0000-4000-8000-000000000065"),
    "procurement.manage": UUID("20000000-0000-4000-8000-000000000066"),
    "procurement.settle": UUID("20000000-0000-4000-8000-000000000067"),
    "procurement.export": UUID("20000000-0000-4000-8000-000000000068"),
    "procurement.centers.manage": UUID("20000000-0000-4000-8000-000000000069"),
    "procurement.invoices.manage": UUID("20000000-0000-4000-8000-000000000070"),
}
TRAVEL_REPORT_PERMISSION_IDS = {
    "travel_reports.read": UUID("20000000-0000-4000-8000-000000000072"),
    "travel_reports.import": UUID("20000000-0000-4000-8000-000000000073"),
    "travel_reports.export": UUID("20000000-0000-4000-8000-000000000074"),
}
ASSET_PERMISSION_KEYS = {
    "assets.read_self",
    *ADMIN_GLOBAL_ASSET_PERMISSIONS,
}


def test_finance_role_and_revenue_share_permissions_are_least_privilege_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        finance_role = db.scalar(select(Role).where(Role.key == "finance"))
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(REVENUE_SHARE_PERMISSION_IDS))
            )
        }
        finance_grants = set(
            db.execute(
                select(Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
                .where(Role.key == "finance")
            ).tuples()
        )
        self_revenue_share_roles = set(
            db.scalars(
                select(Role.key)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(
                    Permission.key == "revenue_share.read_self",
                    RolePermission.scope_kind == "self",
                )
            )
        )
        self_referral_roles = set(
            db.scalars(
                select(Role.key)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(
                    Permission.key == "revenue_share.create_self_referral",
                    RolePermission.scope_kind == "self",
                )
            )
        )

    assert finance_role is not None
    assert (finance_role.id, finance_role.name) == (
        UUID("10000000-0000-4000-8000-000000000011"),
        "财务",
    )
    assert permissions == {
        "revenue_share.read_self": (
            REVENUE_SHARE_PERMISSION_IDS["revenue_share.read_self"],
            False,
        ),
        "revenue_share.finance": (
            REVENUE_SHARE_PERMISSION_IDS["revenue_share.finance"],
            True,
        ),
        "revenue_share.manage": (
            REVENUE_SHARE_PERMISSION_IDS["revenue_share.manage"],
            True,
        ),
        "revenue_share.export": (
            REVENUE_SHARE_PERMISSION_IDS["revenue_share.export"],
            True,
        ),
        "revenue_share.create_self_referral": (
            REVENUE_SHARE_PERMISSION_IDS["revenue_share.create_self_referral"],
            False,
        ),
    }
    assert finance_grants == {
        ("assets.read_self", "self"),
        ("payments.read", "global"),
        ("revenue_share.read_self", "self"),
        ("revenue_share.finance", "global"),
        ("revenue_share.export", "global"),
        ("procurement.read", "global"),
        ("procurement.settle", "global"),
        ("procurement.export", "global"),
        ("procurement.invoices.manage", "global"),
        ("travel_reports.read", "global"),
        ("travel_reports.import", "global"),
        ("travel_reports.export", "global"),
    }
    assert self_revenue_share_roles == set(ROLES) - {
        "student", "registered_user", "team_member", "team_manager"
    }
    assert self_referral_roles == {"super_admin", "admin", "coach"}


def test_procurement_permissions_are_split_between_administration_and_finance(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(PROCUREMENT_PERMISSION_IDS))
            )
        }
        grants = {
            role_key: set(
                db.scalars(
                    select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(
                        Role.key == role_key,
                        Permission.key.in_(PROCUREMENT_PERMISSION_IDS),
                        RolePermission.scope_kind == "global",
                    )
                )
            )
            for role_key in ("super_admin", "admin", "administration", "finance")
        }

    assert permissions == {
        key: (permission_id, True) for key, permission_id in PROCUREMENT_PERMISSION_IDS.items()
    }
    assert grants["super_admin"] == set(PROCUREMENT_PERMISSION_IDS)
    assert grants["admin"] == set()
    assert grants["administration"] == set(PROCUREMENT_PERMISSION_IDS)
    assert grants["finance"] == {
        "procurement.read",
        "procurement.settle",
        "procurement.export",
        "procurement.invoices.manage",
    }


def test_travel_report_permissions_are_sensitive_and_finance_limited(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(TRAVEL_REPORT_PERMISSION_IDS))
            )
        }
        grants = {
            role_key: set(
                db.scalars(
                    select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(
                        Role.key == role_key,
                        Permission.key.in_(TRAVEL_REPORT_PERMISSION_IDS),
                        RolePermission.scope_kind == "global",
                    )
                )
            )
            for role_key in ("super_admin", "admin", "administration", "finance")
        }

    assert permissions == {
        key: (permission_id, True)
        for key, permission_id in TRAVEL_REPORT_PERMISSION_IDS.items()
    }
    assert grants == {
        "super_admin": set(TRAVEL_REPORT_PERMISSION_IDS),
        "admin": set(),
        "administration": set(TRAVEL_REPORT_PERMISSION_IDS),
        "finance": set(TRAVEL_REPORT_PERMISSION_IDS),
    }


def test_asset_permissions_are_limited_to_personal_access_except_for_asset_operators(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        grants = {
            role_key: {
                (permission_key, scope_kind)
                for permission_key, scope_kind in db.execute(
                    select(Permission.key, RolePermission.scope_kind)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(
                        Role.key == role_key,
                        Permission.key.in_(ASSET_PERMISSION_KEYS),
                    )
                )
            }
            for role_key in ROLES
        }

    global_asset_grants = {
        (permission_key, "global") for permission_key in ADMIN_GLOBAL_ASSET_PERMISSIONS
    }
    self_asset_grant = {("assets.read_self", "self")}
    assert grants == {
        "super_admin": global_asset_grants | self_asset_grant,
        "administration": global_asset_grants | self_asset_grant,
        "admin": self_asset_grant,
        "coach": self_asset_grant,
        "customer_service": self_asset_grant,
        "life_teacher": self_asset_grant,
        "staff": self_asset_grant,
        "finance": self_asset_grant,
        "guardian": self_asset_grant | {("assets.read_self", "family")},
        "student": set(),
        "registered_user": set(),
        "team_member": set(),
        "team_manager": set(),
    }


def test_super_admin_and_operations_roles_are_least_privilege_seeded(
    postgres_engine: Engine,
) -> None:
    administration_permissions = {
        "people.list",
        "people.read",
        "accounts.read",
        "housing.read",
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
        "housing.reports.export_sensitive",
        "food_preferences.read",
        "food_preferences.export",
        "assets.read",
        "assets.manage",
        "assets.assign",
        "assets.export",
        "procurement.read",
        "procurement.manage",
        "procurement.settle",
        "procurement.export",
        "procurement.centers.manage",
        "procurement.invoices.manage",
        "travel_reports.read",
        "travel_reports.import",
        "travel_reports.export",
    }
    with Session(postgres_engine) as db:
        role_names = {
            role.key: role.name
            for role in db.scalars(
                select(Role).where(Role.key.in_(("super_admin", "admin", "administration")))
            )
        }
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(SYSTEM_ADMIN_PERMISSION_IDS))
            )
        }
        grants = {
            role_key: set(
                db.scalars(
                    select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(Role.key == role_key, RolePermission.scope_kind == "global")
                )
            )
            for role_key in ("super_admin", "admin", "administration", "coach")
        }
        admin_talent_grants = set(
            db.execute(
                select(Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(Role, Role.id == RolePermission.role_id)
                .where(
                    Role.key == "admin",
                    Permission.key.in_(("talent_tests.take", "talent_tests.read")),
                )
            )
        )

    assert role_names == {
        "super_admin": "超级管理员",
        "admin": "管理员",
        "administration": "行政",
    }
    assert permissions == {
        "accounts.read": (SYSTEM_ADMIN_PERMISSION_IDS["accounts.read"], False),
        "mail.settings.manage": (
            SYSTEM_ADMIN_PERMISSION_IDS["mail.settings.manage"],
            True,
        ),
        "operations.monitor.read": (
            SYSTEM_ADMIN_PERMISSION_IDS["operations.monitor.read"],
            True,
        ),
        "project_coaches.manage": (
            SYSTEM_ADMIN_PERMISSION_IDS["project_coaches.manage"],
            False,
        ),
    }
    assert grants["super_admin"] == set(PERMISSIONS) - SELF_SCOPE_ONLY_PERMISSIONS
    assert (
        grants["admin"]
        == set(PERMISSIONS)
        - {
            "permissions.manage",
            "audit.read",
            "mail.settings.manage",
            "operations.monitor.read",
            "project_coaches.manage",
        }
        - ADMIN_OPERATIONAL_FINANCE_PERMISSIONS
        - ADMIN_GLOBAL_ASSET_PERMISSIONS
        - ADMIN_DEFAULT_EXCLUDED_PERMISSIONS
        - SELF_SCOPE_ONLY_PERMISSIONS
    )
    assert admin_talent_grants == set()
    assert grants["administration"] == administration_permissions
    assert grants["coach"] & set(HOUSING_PERMISSION_IDS) == {"housing.read"}


def test_registration_review_permissions_are_sensitive_and_admin_only(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(REGISTRATION_REVIEW_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(REGISTRATION_REVIEW_PERMISSION_IDS))
            )
        }

    assert permissions == {
        permission_key: (permission_id, True)
        for permission_key, permission_id in REGISTRATION_REVIEW_PERMISSION_IDS.items()
    }
    assert grants == _administrator_grants(REGISTRATION_REVIEW_PERMISSION_IDS)


def test_camp_survey_permissions_are_sensitive_and_admin_only(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            row.key: (row.id, row.name, row.sensitive)
            for row in db.scalars(
                select(Permission).where(Permission.key.in_(CAMP_SURVEY_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(CAMP_SURVEY_PERMISSION_IDS))
            )
        }
    assert permissions == {
        "camp_surveys.identity.read": (
            CAMP_SURVEY_PERMISSION_IDS["camp_surveys.identity.read"],
            "查看营期问卷实名内容",
            True,
        ),
        "camp_surveys.print": (
            CAMP_SURVEY_PERMISSION_IDS["camp_surveys.print"],
            "打印营期工作人员评价",
            True,
        ),
    }
    assert grants == _administrator_grants(CAMP_SURVEY_PERMISSION_IDS)


def test_food_preference_permissions_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(FOOD_PREFERENCE_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(FOOD_PREFERENCE_PERMISSION_IDS))
            )
        }

    assert permissions == {
        "food_preferences.read": (
            FOOD_PREFERENCE_PERMISSION_IDS["food_preferences.read"],
            False,
        ),
        "food_preferences.update": (
            FOOD_PREFERENCE_PERMISSION_IDS["food_preferences.update"],
            True,
        ),
        "food_preferences.catalog.manage": (
            FOOD_PREFERENCE_PERMISSION_IDS["food_preferences.catalog.manage"],
            True,
        ),
        "food_preferences.export": (
            FOOD_PREFERENCE_PERMISSION_IDS["food_preferences.export"],
            True,
        ),
    }
    assert grants == {
        *_administrator_grants(FOOD_PREFERENCE_PERMISSION_IDS),
        ("administration", "food_preferences.read", "global"),
        ("administration", "food_preferences.export", "global"),
        ("student", "food_preferences.read", "self"),
        ("student", "food_preferences.update", "self"),
        ("guardian", "food_preferences.read", "family"),
        ("guardian", "food_preferences.update", "family"),
        ("registered_user", "food_preferences.read", "self"),
        ("registered_user", "food_preferences.update", "self"),
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
        *_administrator_grants(HOUSING_PERMISSION_IDS),
        *{
            ("administration", permission_key, "global")
            for permission_key in HOUSING_PERMISSION_IDS
        },
        ("coach", "housing.read", "global"),
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
            row.id: (
                row.name, row.sort_order, row.is_active,
                row.created_by_user_id, row.is_team_division,
            )
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
        **{
            project.id: (
                project.name, project.sort_order, True, None,
                project.name in {"和平精英", "三角洲行动"},
            )
            for project in INITIAL_PROJECTS
        },
        UUID("30000000-0000-4000-8000-000000000005"):
            ("王者荣耀", 50, True, None, True),
    }
    assert permissions == teaching_permission_keys
    assert grants == {
        *_administrator_grants(teaching_permission_keys),
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
        UUID(f"20000000-0000-4000-8000-{suffix:012d}") for suffix in range(16, 25)
    }
    assert grants == {
        *_administrator_grants(ADMISSIONS_PERMISSIONS),
        ("customer_service", "admission_cases.read", "global"),
        ("customer_service", "enrollments.read", "global"),
        ("coach", "enrollments.read", "assigned_class"),
        ("guardian", "enrollments.read", "family"),
        ("guardian", "payments.read", "self"),
        ("student", "enrollments.read", "self"),
        ("student", "payments.read", "self"),
        ("finance", "payments.read", "global"),
    }


def test_portal_permission_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permission = db.scalar(select(Permission).where(Permission.key == "coach_roster.read"))
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
        ("super_admin", "global"),
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
        *_administrator_grants(EVALUATION_FOUNDATION_PERMISSION_IDS),
        ("student", "student_game_accounts.manage", "self"),
        ("student", "evaluation_media.original.read", "self"),
        ("guardian", "evaluation_media.original.read", "family"),
        ("coach", "evaluation_media.original.read", "assigned_class"),
    }
    assert ("guardian", "student_game_accounts.manage", "family") not in grants
    assert ("customer_service", "evaluation_media.upload", "global") not in grants


def test_system_evaluation_permissions_are_sensitive_and_admin_only_by_default(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(SYSTEM_EVALUATION_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(SYSTEM_EVALUATION_PERMISSION_IDS))
            )
        }

    assert permissions == {
        key: (permission_id, True)
        for key, permission_id in SYSTEM_EVALUATION_PERMISSION_IDS.items()
    }
    assert grants == _administrator_grants(SYSTEM_EVALUATION_PERMISSION_IDS)
    assert not {grant for grant in grants if grant[0] in {"coach", "life_teacher", "staff"}}


def test_teaching_report_permissions_are_sensitive_and_admin_only_by_default(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permissions = {
            permission.key: (permission.id, permission.sensitive)
            for permission in db.scalars(
                select(Permission).where(Permission.key.in_(TEACHING_REPORT_PERMISSION_IDS))
            )
        }
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(TEACHING_REPORT_PERMISSION_IDS))
            )
        }

    assert permissions == {
        key: (permission_id, True) for key, permission_id in TEACHING_REPORT_PERMISSION_IDS.items()
    }
    assert grants == _administrator_grants(TEACHING_REPORT_PERMISSION_IDS)
    assert not {grant for grant in grants if grant[0] in {"coach", "life_teacher", "staff"}}


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
            UserRole(user_id=admin.id, role_id=ROLES["super_admin"].id),
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
