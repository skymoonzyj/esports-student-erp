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
    "registered_user": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000008"),
        "注册用户",
    ),
    "super_admin": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000009"),
        "超级管理员",
    ),
    "administration": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000010"),
        "行政",
    ),
    "finance": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000011"),
        "财务",
    ),
    "team_member": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000012"), "队员"
    ),
    "team_manager": RoleSeed(
        UUID("10000000-0000-4000-8000-000000000013"), "队伍管理"
    ),
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
    "evaluation_jobs.review": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000029"),
        "审核系统评价",
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
    "student_reports.write": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000032"),
        "填写学员日报和周报",
        True,
    ),
    "student_reports.review": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000033"),
        "审核学员日报和周报",
        True,
    ),
    "camp_surveys.identity.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000034"),
        "查看营期问卷实名内容",
        True,
    ),
    "camp_surveys.print": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000035"),
        "打印营期工作人员评价",
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
    "evaluation_jobs.match": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000044"),
        "确认系统评价目标",
        True,
    ),
    "evaluation_jobs.manual_input": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000045"),
        "人工录入系统评价",
        True,
    ),
    "evaluation_adapters.certify": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000046"),
        "认证评价适配器",
        True,
    ),
    "food_preferences.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000047"),
        "查看个人饮食偏好",
    ),
    "food_preferences.update": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000048"),
        "修改个人饮食偏好",
        True,
    ),
    "food_preferences.catalog.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000049"),
        "管理菜系字典",
        True,
    ),
    "food_preferences.export": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000050"),
        "导出饮食偏好统计",
        True,
    ),
    "registration.review": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000051"),
        "审核注册冲突",
        True,
    ),
    "registration.sensitive.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000052"),
        "查看注册敏感信息",
        True,
    ),
    "staff_identity_applications.review": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000053"),
        "审核工作人员身份申请",
        True,
    ),
    "accounts.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000054"),
        "查看账号列表",
    ),
    "mail.settings.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000055"),
        "管理邮件发送设置",
        True,
    ),
    "assets.read_self": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000056"),
        "查看本人资产",
    ),
    "assets.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000057"),
        "查看资产台账",
    ),
    "assets.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000058"),
        "管理资产台账",
        True,
    ),
    "assets.assign": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000059"),
        "分配与归还资产",
        True,
    ),
    "assets.export": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000060"),
        "导出资产台账",
        True,
    ),
    "revenue_share.read_self": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000061"),
        "查看本人分成",
    ),
    "revenue_share.finance": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000062"),
        "管理分成结算",
        True,
    ),
    "revenue_share.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000063"),
        "管理邀请与分成规则",
        True,
    ),
    "revenue_share.export": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000064"),
        "导出分成结算",
        True,
    ),
    "procurement.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000065"),
        "查看日常采购",
        True,
    ),
    "procurement.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000066"),
        "管理日常采购",
        True,
    ),
    "procurement.settle": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000067"),
        "管理采购结算",
        True,
    ),
    "procurement.export": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000068"),
        "导出日常采购",
        True,
    ),
    "procurement.centers.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000069"),
        "管理采购结算中心",
        True,
    ),
    "procurement.invoices.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000070"),
        "管理采购发票",
        True,
    ),
    "operations.monitor.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000071"),
        "查看运维监控",
        True,
    ),
    "travel_reports.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000072"),
        "查看订票报表",
        True,
    ),
    "travel_reports.import": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000073"),
        "导入订票账单",
        True,
    ),
    "travel_reports.export": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000074"),
        "下载订票报表",
        True,
    ),
    "enrollment_applications.create": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000075"),
        "提交学员报名申请",
    ),
    "revenue_share.create_self_referral": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000076"),
        "生成本人邀请二维码",
    ),
    "project_coaches.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000077"),
        "配置项目可报名教练",
    ),
    "talent_tests.take": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000078"),
        "参加天赋训练测试",
    ),
    "talent_tests.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000079"),
        "查看天赋训练测试报告",
        True,
    ),
    "esports_assessments.take": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000080"),
        "参加天赋评测 2",
    ),
    "esports_assessments.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000081"),
        "查看天赋评测 2 报告",
        True,
    ),
    "team_memberships.read": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000082"), "查看队伍归属与名单", True
    ),
    "team_memberships.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000083"), "管理队伍成员", True
    ),
    "team_memberships.transfer": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000084"), "调动队伍成员", True
    ),
    "team_manager_assignments.manage": PermissionSeed(
        UUID("20000000-0000-4000-8000-000000000085"), "分配队伍管理人员", True
    ),
}

SUPER_ADMIN_ONLY_PERMISSIONS = frozenset(
    {
        "permissions.manage",
        "audit.read",
        "mail.settings.manage",
        "operations.monitor.read",
        "project_coaches.manage",
    }
)

ADMIN_OPERATIONAL_FINANCE_PERMISSIONS = frozenset(
    {
        "revenue_share.finance",
        "revenue_share.manage",
        "revenue_share.export",
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
)

ADMIN_GLOBAL_ASSET_PERMISSIONS = frozenset(
    {
        "assets.read",
        "assets.manage",
        "assets.assign",
        "assets.export",
    }
)

ADMIN_DEFAULT_EXCLUDED_PERMISSIONS = frozenset(
    {
        "talent_tests.read",
        "esports_assessments.read",
        "team_memberships.read",
        "team_memberships.manage",
        "team_memberships.transfer",
        "team_manager_assignments.manage",
    }
)

SELF_SCOPE_ONLY_PERMISSIONS = frozenset(
    {
        "assets.read_self",
        "enrollment_applications.create",
        "revenue_share.create_self_referral",
        "talent_tests.take",
        "esports_assessments.take",
    }
)

EVALUATION_ACCESS_ROLE_GRANTS = [
    (role_key, "evaluation_center.access", "global")
    for role_key in ("coach", "life_teacher", "staff", "guardian", "student")
]

ROLE_PERMISSION_GRANTS = [
    *[
        ("super_admin", permission, "global")
        for permission in PERMISSIONS
        if permission not in SELF_SCOPE_ONLY_PERMISSIONS
    ],
    *[
        ("admin", permission, "global")
        for permission in PERMISSIONS
        if permission not in SUPER_ADMIN_ONLY_PERMISSIONS
        and permission not in ADMIN_OPERATIONAL_FINANCE_PERMISSIONS
        and permission not in ADMIN_GLOBAL_ASSET_PERMISSIONS
        and permission not in ADMIN_DEFAULT_EXCLUDED_PERMISSIONS
        and permission not in SELF_SCOPE_ONLY_PERMISSIONS
    ],
    ("customer_service", "people.list", "global"),
    ("customer_service", "people.read", "global"),
    ("customer_service", "teaching.read", "global"),
    ("customer_service", "admission_cases.read", "global"),
    ("customer_service", "enrollments.read", "global"),
    ("coach", "teaching.read", "assigned_class"),
    ("coach", "coach_roster.read", "assigned_class"),
    ("coach", "enrollments.read", "assigned_class"),
    ("coach", "housing.read", "global"),
    ("administration", "people.list", "global"),
    ("administration", "people.read", "global"),
    ("administration", "accounts.read", "global"),
    ("administration", "housing.read", "global"),
    ("administration", "housing.rooms.manage", "global"),
    ("administration", "housing.stays.manage", "global"),
    ("administration", "housing.transfers.manage", "global"),
    ("administration", "housing.checkouts.manage", "global"),
    ("administration", "housing.issues.manage", "global"),
    ("administration", "housing.reports.export_sensitive", "global"),
    ("administration", "food_preferences.read", "global"),
    ("administration", "food_preferences.export", "global"),
    *[
        (role_key, "assets.read_self", "self")
        for role_key in (
            "admin",
            "coach",
            "customer_service",
            "life_teacher",
            "staff",
            "guardian",
            "super_admin",
            "administration",
            "finance",
        )
    ],
    ("guardian", "assets.read_self", "family"),
    ("administration", "assets.read", "global"),
    ("administration", "assets.manage", "global"),
    ("administration", "assets.assign", "global"),
    ("administration", "assets.export", "global"),
    ("administration", "procurement.read", "global"),
    ("administration", "procurement.manage", "global"),
    ("administration", "procurement.settle", "global"),
    ("administration", "procurement.export", "global"),
    ("administration", "procurement.centers.manage", "global"),
    ("administration", "procurement.invoices.manage", "global"),
    ("administration", "travel_reports.read", "global"),
    ("administration", "travel_reports.import", "global"),
    ("administration", "travel_reports.export", "global"),
    ("finance", "payments.read", "global"),
    ("finance", "revenue_share.finance", "global"),
    ("finance", "revenue_share.export", "global"),
    ("finance", "procurement.read", "global"),
    ("finance", "procurement.settle", "global"),
    ("finance", "procurement.export", "global"),
    ("finance", "procurement.invoices.manage", "global"),
    ("finance", "travel_reports.read", "global"),
    ("finance", "travel_reports.import", "global"),
    ("finance", "travel_reports.export", "global"),
    *[
        (role_key, "revenue_share.read_self", "self")
        for role_key in (
            "super_admin",
            "admin",
            "coach",
            "customer_service",
            "life_teacher",
            "staff",
            "guardian",
            "administration",
            "finance",
        )
    ],
    *[
        (role_key, "revenue_share.create_self_referral", "self")
        for role_key in ("super_admin", "admin", "coach")
    ],
    ("guardian", "people.read", "family"),
    ("guardian", "teaching.read", "family"),
    ("guardian", "enrollments.read", "family"),
    ("guardian", "payments.read", "self"),
    ("student", "people.read", "self"),
    ("student", "teaching.read", "self"),
    ("student", "enrollments.read", "self"),
    ("student", "payments.read", "self"),
    ("student", "enrollment_applications.create", "self"),
    ("student", "student_game_accounts.manage", "self"),
    ("student", "evaluation_media.original.read", "self"),
    ("super_admin", "talent_tests.take", "self"),
    ("super_admin", "talent_tests.read", "global"),
    ("student", "talent_tests.take", "self"),
    ("student", "talent_tests.read", "self"),
    ("guardian", "talent_tests.read", "family"),
    ("coach", "talent_tests.read", "assigned_class"),
    ("super_admin", "esports_assessments.take", "self"),
    ("super_admin", "esports_assessments.read", "global"),
    ("student", "esports_assessments.take", "self"),
    ("student", "esports_assessments.read", "self"),
    ("team_member", "team_memberships.read", "self"),
    ("team_manager", "team_memberships.read", "assigned_team"),
    ("team_manager", "team_memberships.manage", "assigned_team"),
    ("guardian", "esports_assessments.read", "family"),
    ("coach", "esports_assessments.read", "assigned_class"),
    ("guardian", "evaluation_media.original.read", "family"),
    ("guardian", "enrollment_applications.create", "family"),
    ("coach", "evaluation_media.original.read", "assigned_class"),
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
    ("student", "food_preferences.read", "self"),
    ("student", "food_preferences.update", "self"),
    ("guardian", "food_preferences.read", "family"),
    ("guardian", "food_preferences.update", "family"),
    ("registered_user", "food_preferences.read", "self"),
    ("registered_user", "food_preferences.update", "self"),
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
