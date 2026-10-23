from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.rbac.models import Permission, Role, RolePermission


@dataclass(frozen=True)
class RoleSeed:
    id: UUID
    name: str


@dataclass(frozen=True)
class PermissionSeed:
    id: UUID
    name: str
    sensitive: bool = False


ROLES = {
    "admin": RoleSeed(UUID("10000000-0000-4000-8000-000000000001"), "管理员"),
    "coach": RoleSeed(UUID("10000000-0000-4000-8000-000000000002"), "教练"),
    "customer_service": RoleSeed(UUID("10000000-0000-4000-8000-000000000003"), "客服"),
    "life_teacher": RoleSeed(UUID("10000000-0000-4000-8000-000000000004"), "生活老师"),
    "staff": RoleSeed(UUID("10000000-0000-4000-8000-000000000005"), "其他员工"),
    "guardian": RoleSeed(UUID("10000000-0000-4000-8000-000000000006"), "监护人"),
    "student": RoleSeed(UUID("10000000-0000-4000-8000-000000000007"), "学员"),
}

PERMISSIONS = {
    "people.list": PermissionSeed(UUID("20000000-0000-4000-8000-000000000001"), "查看人员列表"),
    "people.read": PermissionSeed(UUID("20000000-0000-4000-8000-000000000002"), "查看人员档案"),
    "people.create": PermissionSeed(UUID("20000000-0000-4000-8000-000000000003"), "创建人员"),
    "people.update": PermissionSeed(UUID("20000000-0000-4000-8000-000000000004"), "更新人员"),
    "people.phone.reveal": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000005"),
        "查看完整手机号",
        True,
    ),
    "people.identity.reveal": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000006"),
        "查看完整身份证",
        True,
    ),
    "accounts.manage": PermissionSeed(UUID("20000000-0000-4000-8000-000000000007"), "管理账号"),
    "permissions.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000008"),
        "管理角色与权限",
        True,
    ),
    "audit.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000009"),
        "查看安全审计",
        True,
    ),
    "teaching.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000010"),
        "查看教学配置",
    ),
    "projects.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000011"),
        "管理游戏项目",
    ),
    "professional_teams.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000012"),
        "管理职业战队",
    ),
    "cohorts.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000013"),
        "管理届次",
    ),
    "classes.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000014"),
        "管理班级",
    ),
    "coach_assignments.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000015"),
        "管理教练分配",
    ),
    "admission_cases.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000016"),
        "查看待分配",
    ),
    "admission_cases.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000017"),
        "管理待分配",
    ),
    "payments.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000018"),
        "查看付款金额",
        True,
    ),
    "payments.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000019"),
        "管理付款",
        True,
    ),
    "refunds.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000020"),
        "管理退款",
        True,
    ),
    "enrollments.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000021"),
        "查看正式报名",
    ),
    "enrollments.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000022"),
        "管理正式报名",
    ),
    "enrollments.capacity_override": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000023"),
        "超额报名审批",
        True,
    ),
    "payment_imports.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000024"),
        "管理付款导入",
        True,
    ),
    "coach_roster.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000025"),
        "查看教练下一期名单",
    ),
    "evaluation_center.access": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000026"),
        "进入评价中心",
    ),
    "evaluation_media.upload": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000027"),
        "上传评价媒体",
        True,
    ),
    "evaluation_media.original.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000028"),
        "查看评价媒体原图",
        True,
    ),
    "analysis_profiles.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000030"),
        "管理分析配置",
        True,
    ),
    "student_game_accounts.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000031"),
        "管理学员游戏账号",
        True,
    ),
    "cohort_staff.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000036"),
        "管理届次员工分配",
        True,
    ),
    "housing.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000037"),
        "查看房态",
    ),
    "housing.rooms.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000038"),
        "管理房间目录",
        True,
    ),
    "housing.stays.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000039"),
        "管理住宿与预约",
        True,
    ),
    "housing.transfers.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000040"),
        "管理调房",
        True,
    ),
    "housing.checkouts.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000041"),
        "管理退房",
        True,
    ),
    "housing.issues.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000042"),
        "管理问题房",
        True,
    ),
    "housing.reports.export_sensitive": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000043"),
        "导出房态实名报送",
        True,
    ),
}

EVALUATION_ACCESS_ROLE_GRANTS = [
    (role_key, "evaluation_center.access", "global")
    for role_key in ("coach", "life_teacher", "staff", "guardian", "student")
]

ROLE_PERMISSION_GRANTS = [
    *[("admin", permission, "global") for permission in PERMISSIONS],
    ("customer_service", "people.list", "global"),
    ("customer_service", "people.read", "global"),
    ("customer_service", "teaching.read", "global"),
    ("customer_service", "admission_cases.read", "global"),
    ("customer_service", "enrollments.read", "global"),
    ("coach", "teaching.read", "assigned_class"),
    ("coach", "coach_roster.read", "assigned_class"),
    ("coach", "enrollments.read", "assigned_class"),
    ("guardian", "people.read", "family"),
    ("guardian", "teaching.read", "family"),
    ("guardian", "enrollments.read", "family"),
    ("guardian", "payments.read", "self"),
    ("student", "people.read", "self"),
    ("student", "teaching.read", "self"),
    ("student", "enrollments.read", "self"),
    ("student", "payments.read", "self"),
    ("student", "student_game_accounts.manage", "self"),
    ("student", "evaluation_media.original.read", "self"),
    ("guardian", "evaluation_media.original.read", "family"),
    ("coach", "evaluation_media.original.read", "assigned_class"),
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
    *EVALUATION_ACCESS_ROLE_GRANTS,
]


def seed_roles_and_permissions(db: Session) -> None:
    for key, role in ROLES.items():
        db.execute(
            postgresql_insert(Role)
            .values(id=role.id, key=key, name=role.name)
            .on_conflict_do_update(index_elements=[Role.key], set_={"name": role.name})
        )
    for key, permission in PERMISSIONS.items():
        db.execute(
            postgresql_insert(Permission)
            .values(
                id=permission.id,
                key=key,
                name=permission.name,
                sensitive=permission.sensitive,
            )
            .on_conflict_do_update(
                index_elements=[Permission.key],
                set_={
                    "name": permission.name,
                    "sensitive": permission.sensitive,
                },
            )
        )
    db.flush()
    role_ids: dict[str, UUID] = {
        key: role_id for key, role_id in db.execute(select(Role.key, Role.id))
    }
    permission_ids: dict[str, UUID] = {
        key: permission_id
        for key, permission_id in db.execute(select(Permission.key, Permission.id))
    }
    for role_key, permission_key, scope_kind in ROLE_PERMISSION_GRANTS:
        db.execute(
            postgresql_insert(RolePermission)
            .values(
                role_id=role_ids[role_key],
                permission_id=permission_ids[permission_key],
                scope_kind=scope_kind,
            )
            .on_conflict_do_nothing()
        )
