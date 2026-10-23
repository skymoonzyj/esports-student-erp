# Coach and Family Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付阶段 1B 的教练与家庭门户，使教练一键查看下一期学员，监护人和学员在对象范围内查看教学信息，并让当前付款账号查看自己的付款与退款。

**Architecture:** 新建只读 `app.portal` 投影包，通过专用 DTO 和 `/portal/context`、`/coach/next-cohorts`、`/family/students`、`/family/students/{id}/teaching`、`/me/payments` 接口读取现有人员、教学、报名和付款数据。所有查询继续由服务端执行 `assigned_class`、`family`、`self` 和显式拒绝优先规则；React 在现有应用外壳内增加能力驱动的任务卡片、动态导航和三个响应式门户页面。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、lucide-react

---

## Scope guardrails

- 本计划只实现已批准的教练与家庭门户规格，不实现每日/每周评价正文、截图、OCR、自动评分、游戏 ID、住宿、实名导出、录像/GPU、自助注册或支付跳转。
- “学习评价”只显示不可操作的“下一阶段开放”卡片；前端不得请求评价 API，后端不得创建评价表。
- 门户不复制业务数据，不新增门户业务表。`0006_coach_family_portal` 只增加权限、默认授权和三个查询索引。
- 现有后台营期、报名和付款接口保持原样；门户不得复用会泄漏后台字段的宽响应。
- Docker/NAS 部署和运行时验证继续延期。由于生产镜像显式复制 Python 包，本计划只补充 `COPY app/portal ./app/portal`，不执行容器部署或 NAS 迁移。
- 所有门户响应使用 `Cache-Control: no-store, private`。客户端进入页面、切换学员和窗口重新聚焦时重新取数，不能把旧缓存当作持续授权。
- 教练名单绝不返回身份证、手机号、邮箱、监护关系、学费、付款状态或金额。
- 家庭教学绝不返回付款、待分配单、实名字段、监护诊断或后台操作上下文。
- 本人付款固定过滤当前 `payer_account_id`；家庭关系不扩大付款范围。
- 所有列表稳定分页；`page_size` 范围为 1–100。所有业务日期和成年状态使用 `Asia/Shanghai`。
- 固定验收宽度为 360、768、1024 和 1440；核心操作不得依赖页面级横向滚动或 `overflow-x: hidden` 掩盖裁切。

## Frozen contracts

后端必须保持以下名称与字段一致：

```python
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.identity.readiness import AgeStatus

PortalLearningMode = Literal["self", "family", "self_and_family"]
PortalAccessSource = Literal["self", "family"]
ClassType = Literal["standard", "elite"]
AssignmentType = Literal["primary", "assistant"]
EnrollmentStatus = Literal["active", "cancelled", "completed"]
PortalPaymentStatus = Literal["paid", "partially_refunded", "refunded"]
PortalRefundType = Literal["manual", "withdrawal_prorated", "withdrawal_full"]


class PortalContextResponse(BaseModel):
    coach_available: bool
    learning_available: bool
    payments_available: bool
    learning_mode: PortalLearningMode | None


class CoachStartOption(BaseModel):
    anchor_cohort_id: UUID
    starts_at: datetime
    cohort_names: list[str]
    class_count: int = Field(ge=1)


class CoachClassSummary(BaseModel):
    cohort_id: UUID
    cohort_name: str
    starts_at: datetime
    ends_at: datetime
    class_id: UUID
    class_name: str
    project_name: str
    class_type: ClassType
    professional_team_name: str | None
    assignment_type: AssignmentType
    student_count: int = Field(ge=0)


class CoachRosterStudent(BaseModel):
    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    age_status: AgeStatus
    cohort_id: UUID
    cohort_name: str
    project_name: str
    class_id: UUID
    class_name: str
    class_type: ClassType
    professional_team_name: str | None
    assignment_type: AssignmentType
    enrollment_status: Literal["active"]


class CoachNextCohortsResponse(BaseModel):
    observed_at: datetime
    selected_starts_at: datetime | None
    options: list[CoachStartOption]
    classes: list[CoachClassSummary]
    items: list[CoachRosterStudent]
    page: int
    page_size: int
    total: int


class LearningEnrollmentSummary(BaseModel):
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    starts_at: datetime
    ends_at: datetime
    project_name: str
    class_id: UUID
    class_name: str
    class_type: ClassType
    professional_team_name: str | None
    status: EnrollmentStatus
    confirmed_at: datetime
    cancelled_at: datetime | None
    completed_at: datetime | None


class LearningStudentSummary(BaseModel):
    student_person_id: UUID
    student_name: str
    student_number: str
    age_status: AgeStatus
    access_source: PortalAccessSource
    current_enrollment: LearningEnrollmentSummary | None


class LearningStudentsResponse(BaseModel):
    items: list[LearningStudentSummary]
    total: int


class LearningTeachingResponse(BaseModel):
    student: LearningStudentSummary
    items: list[LearningEnrollmentSummary]
    page: int
    page_size: int
    total: int


class PortalRefundSummary(BaseModel):
    refund_id: UUID
    amount_cents: int = Field(gt=0)
    refunded_at: datetime
    refund_type: PortalRefundType


class PortalPaymentSummary(BaseModel):
    payment_id: UUID
    source_kind: Literal["api", "manual"]
    amount_cents: int = Field(gt=0)
    currency: Literal["CNY"]
    paid_at: datetime
    refunded_cents: int = Field(ge=0)
    net_cents: int = Field(ge=0)
    status: PortalPaymentStatus
    related_student_name: str | None
    refunds: list[PortalRefundSummary]


class SelfPaymentsResponse(BaseModel):
    items: list[PortalPaymentSummary]
    page: int
    page_size: int
    total: int
```

`errors.py` 的稳定错误层级固定为：

```python
class PortalServiceError(ValueError):
    code = "portal_service_error"


class PortalPermissionDenied(PortalServiceError):
    code = "permission_denied"


class CoachCohortNotFound(PortalServiceError):
    code = "coach_cohort_not_found"


class FamilyStudentNotFound(PortalServiceError):
    code = "family_student_not_found"
```

前端镜像类型固定为：

```ts
export type PortalLearningMode = "self" | "family" | "self_and_family"
export type PortalAccessSource = "self" | "family"
export type AgeStatus = "adult" | "minor"
export type ClassType = "standard" | "elite"
export type AssignmentType = "primary" | "assistant"
export type EnrollmentStatus = "active" | "cancelled" | "completed"

export type PortalContext = {
  coach_available: boolean
  learning_available: boolean
  payments_available: boolean
  learning_mode: PortalLearningMode | null
}

export type PortalCapability = keyof Pick<
  PortalContext,
  "coach_available" | "learning_available" | "payments_available"
>

export type CoachStartOption = {
  anchor_cohort_id: string
  starts_at: string
  cohort_names: string[]
  class_count: number
}

export type CoachClassSummary = {
  cohort_id: string
  cohort_name: string
  starts_at: string
  ends_at: string
  class_id: string
  class_name: string
  project_name: string
  class_type: ClassType
  professional_team_name: string | null
  assignment_type: AssignmentType
  student_count: number
}

export type CoachRosterStudent = {
  enrollment_id: string
  student_person_id: string
  student_name: string
  student_number: string
  age_status: AgeStatus
  cohort_id: string
  cohort_name: string
  project_name: string
  class_id: string
  class_name: string
  class_type: ClassType
  professional_team_name: string | null
  assignment_type: AssignmentType
  enrollment_status: "active"
}

export type CoachNextCohorts = {
  observed_at: string
  selected_starts_at: string | null
  options: CoachStartOption[]
  classes: CoachClassSummary[]
  items: CoachRosterStudent[]
  page: number
  page_size: number
  total: number
}

export type LearningEnrollment = {
  enrollment_id: string
  cohort_id: string
  cohort_name: string
  starts_at: string
  ends_at: string
  project_name: string
  class_id: string
  class_name: string
  class_type: ClassType
  professional_team_name: string | null
  status: EnrollmentStatus
  confirmed_at: string
  cancelled_at: string | null
  completed_at: string | null
}

export type LearningStudent = {
  student_person_id: string
  student_name: string
  student_number: string
  age_status: AgeStatus
  access_source: PortalAccessSource
  current_enrollment: LearningEnrollment | null
}

export type LearningStudents = { items: LearningStudent[]; total: number }
export type LearningTeaching = {
  student: LearningStudent
  items: LearningEnrollment[]
  page: number
  page_size: number
  total: number
}

export type PortalRefund = {
  refund_id: string
  amount_cents: number
  refunded_at: string
  refund_type: "manual" | "withdrawal_prorated" | "withdrawal_full"
}

export type PortalPayment = {
  payment_id: string
  source_kind: "api" | "manual"
  amount_cents: number
  currency: "CNY"
  paid_at: string
  refunded_cents: number
  net_cents: number
  status: "paid" | "partially_refunded" | "refunded"
  related_student_name: string | null
  refunds: PortalRefund[]
}

export type SelfPayments = {
  items: PortalPayment[]
  page: number
  page_size: number
  total: number
}

export const portalKeys = {
  root: (accountId: string) => ["portal", accountId] as const,
  context: (accountId: string) => ["portal", accountId, "context"] as const,
  coach: (
    accountId: string,
    anchorCohortId: string | undefined,
    page: number,
  ) => ["portal", accountId, "coach", anchorCohortId ?? null, page] as const,
  students: (accountId: string) => ["portal", accountId, "students"] as const,
  teaching: (accountId: string, studentId: string, page: number) =>
    ["portal", accountId, "teaching", studentId, page] as const,
  payments: (accountId: string, page: number) =>
    ["portal", accountId, "payments", page] as const,
}
```

## File map

### Create

- `apps/api/migrations/versions/0006_coach_family_portal.py` — 权限、授权和查询索引迁移。
- `apps/api/app/portal/__init__.py` — 门户包标记。
- `apps/api/app/portal/routing.py` — 为门户成功与错误响应统一追加 `no-store, private` 的 ASGI 中间件。
- `apps/api/app/portal/schemas.py` — 上述全部门户响应契约。
- `apps/api/app/portal/errors.py` — 稳定的权限拒绝、营期掩码和家庭学员掩码错误。
- `apps/api/app/portal/context_service.py` — 多身份门户上下文。
- `apps/api/app/portal/coach_service.py` — 下一期时间组、班级和名单投影。
- `apps/api/app/portal/learning_service.py` — 家庭/本人学员集合和教学历史。
- `apps/api/app/portal/payment_service.py` — 当前付款账号的付款与退款投影。
- `apps/api/app/portal/dependencies.py` — 服务依赖构造。
- `apps/api/app/portal/router.py` — 五个门户 GET 接口。
- `apps/api/tests/services/test_portal_context.py` — 多身份上下文和拒绝优先测试。
- `apps/api/tests/services/test_portal_coach.py` — 下一期筛选、分组、分页和撤权测试。
- `apps/api/tests/services/test_portal_learning.py` — 家庭/self、成年关系和历史测试。
- `apps/api/tests/services/test_portal_payments.py` — 付款人隔离、关联姓名和退款投影测试。
- `apps/api/tests/api/test_portal.py` — 门户 HTTP、脱敏、错误、缓存头和审计契约。
- `apps/web/src/portal/api.ts` — TypeScript 类型和门户 GET 请求。
- `apps/web/src/portal/api.test.ts` — 请求路径、参数和返回类型测试。
- `apps/web/src/portal/usePortalContext.ts` — 门户上下文 Query hook。
- `apps/web/src/portal/usePortalAccessError.ts` — 401/403、撤权和普通故障的统一客户端处理。
- `apps/web/src/portal/PortalDashboardPage.tsx` — 统一任务卡片首页。
- `apps/web/src/portal/PortalDashboardPage.test.tsx` — 单身份、多身份和纯后台首页测试。
- `apps/web/src/portal/CoachNextCohortsPage.tsx` — 教练下一期页面。
- `apps/web/src/portal/CoachNextCohortsPage.test.tsx` — 时间组、名单、空态和脱敏测试。
- `apps/web/src/portal/LearningPage.tsx` — 学员切换、本人/家庭教学和评价占位卡。
- `apps/web/src/portal/LearningPage.test.tsx` — 切换、失权回退、历史和评价无请求测试。
- `apps/web/src/portal/MyPaymentsPage.tsx` — 本人付款与退款只读页面。
- `apps/web/src/portal/MyPaymentsPage.test.tsx` — 付款状态、金额和无后台操作测试。
- `apps/web/src/portal/portal.css` — 1440/1024/768/360 门户布局。
- `apps/web/tests/coach-family-portal.spec.ts` — 多身份、教练、家庭、学员、付款与四视口 E2E。

### Modify

- `apps/api/app/api/router.py` — 注册门户路由。
- `apps/api/app/main.py` — 注册门户私有响应中间件。
- `apps/api/app/core/errors.py` — 让最外层未预期错误也保留门户私有缓存头。
- `apps/api/app/rbac/seed.py` — 增加 `coach_roster.read` 和角色授权。
- `apps/api/app/teaching/models.py` — 增加教练/营期门户索引。
- `apps/api/app/admissions/models.py` — 增加学员教学历史索引。
- `apps/api/tests/db/test_migrations.py` — `0006` 权限和索引断言。
- `apps/api/tests/db/test_rbac_seed.py` — 新权限 UUID 和最小授权矩阵。
- `apps/api/app/cli/seed_e2e.py` — 固定门户账号、分配、报名、付款和撤权夹具。
- `apps/api/tests/cli/test_seed_e2e.py` — 门户种子幂等和关系断言。
- `apps/api/Dockerfile` — 生产镜像显式复制 `app.portal`，不执行容器验证。
- `apps/web/src/app/router.tsx` — 门户路由和能力守卫。
- `apps/web/src/app/router.test.tsx` — 门户直达、重定向和纯后台路由测试。
- `apps/web/src/layout/AppShell.tsx` — 动态教练、学习和付款导航。
- `apps/web/src/layout/AppShell.test.tsx` — 多身份及手机“更多”导航测试。
- `apps/web/src/auth/AuthProvider.tsx` — 登录和会话失效时清理跨账号查询缓存。
- `apps/web/src/auth/AuthProvider.test.tsx` — 401 会话失效及重新登录清缓存测试。
- `apps/web/src/api/client.ts` — 只对 `invalid_session` 发布会话失效通知。
- `apps/web/src/api/client.test.ts` — 401 通知以及普通 403/500 不误清会话测试。
- `apps/web/src/styles/global.css` — 移除会掩盖真实裁切的全局横向隐藏。
- `apps/web/tests/helpers.ts` — 监护人、学员登录和元素边界断言。
- `scripts/db-schema-manifest.sh` — Alembic head 更新到 `0006_coach_family_portal`。
- `README.md` — 门户账号、可用功能、测试入口和仍延期边界。
- `apps/api/tests/cli/test_operations_docs.py` — README 门户能力和延期边界断言。
- `docs/superpowers/specs/2026-07-29-coach-family-portal-design.md` — 状态更新为已确认。

## Test environment

后端测试使用本机隔离 PostgreSQL：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://erp@127.0.0.1:55433/teaching_test'
export FIELD_ENCRYPTION_MASTER_KEY='AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
export FIELD_LOOKUP_KEY='Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA='
export SESSION_TOKEN_PEPPER='EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8='
```

`teaching_test` 是可丢弃的本地测试库；pytest 夹具会删除并重建其整个
`public` schema。每条后端测试命令都必须使用
`TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须先设置隔离测试库}"`，避免变量缺失时
pytest 将数据库测试静默标为 skipped。Docker Compose 后续启用时可改用 Compose
专属的 `erp_e2e` 凭据，但不得把本机临时 PostgreSQL 与 Compose 账号混用。

前端单元测试使用 jsdom。浏览器 E2E 使用现有 1440、1024、768、360 Playwright 项目和本地 Web/API；Docker daemon 不可用时不得运行或声称 Compose/NAS 门禁通过。

---

### Task 1: Add portal RBAC grants and query indexes

**Files:**

- Create: `apps/api/migrations/versions/0006_coach_family_portal.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/teaching/models.py`
- Modify: `apps/api/app/admissions/models.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing RBAC and index tests**

Add these assertions to `test_rbac_seed.py` and `test_migrations.py`:

```python
PORTAL_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000025")


def test_portal_permission_and_least_privilege_grants_are_seeded(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(Permission.key == "coach_roster.read")
        )
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(
                    Permission.key.in_(
                        {"coach_roster.read", "teaching.read", "enrollments.read", "payments.read"}
                    )
                )
            )
        }
    assert permission is not None and permission.id == PORTAL_PERMISSION_ID
    assert ("admin", "coach_roster.read", "global") in grants
    assert ("coach", "coach_roster.read", "assigned_class") in grants
    assert ("coach", "enrollments.read", "assigned_class") in grants
    assert ("guardian", "teaching.read", "family") in grants
    assert ("guardian", "enrollments.read", "family") in grants
    assert ("guardian", "payments.read", "self") in grants
    assert ("student", "teaching.read", "self") in grants
    assert ("student", "enrollments.read", "self") in grants
    assert ("student", "payments.read", "self") in grants
    assert not {grant for grant in grants if grant[0] in {"life_teacher", "staff"}}


def test_portal_query_indexes_exist(postgres_engine: Engine) -> None:
    with postgres_engine.connect() as connection:
        indexes = dict(
            connection.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname LIKE 'ix_%_portal_%' "
                    "OR schemaname = 'public' "
                    "AND indexname = 'ix_enrollments_student_status_cohort'"
                )
            ).tuples()
        )
    assert "(status, starts_at, id)" in indexes["ix_cohorts_portal_next"]
    coach_index = indexes["ix_coach_assignments_portal_current"]
    assert "(coach_person_id, valid_from, valid_until, class_id)" in coach_index
    assert "WHERE (ended_at IS NULL)" in coach_index
    assert "(student_person_id, status, cohort_id, id)" in indexes[
        "ix_enrollments_student_status_cohort"
    ]


def test_portal_migration_upgrades_from_and_downgrades_to_0005(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0005_admissions_enrollment")
    try:
        command.upgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_coach_family_portal"
            assert connection.scalar(
                text(
                    "SELECT id FROM permissions "
                    "WHERE key = 'coach_roster.read'"
                )
            ) == PORTAL_PERMISSION_ID

        command.downgrade(config, "0005_admissions_enrollment")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE key = 'coach_roster.read'"
                )
            ) == 0
            index_names = set(
                connection.scalars(
                    text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
                )
            )
            assert "ix_cohorts_portal_next" not in index_names
            assert "ix_coach_assignments_portal_current" not in index_names
            assert "ix_enrollments_student_status_cohort" not in index_names
    finally:
        command.upgrade(config, "head")


def test_portal_downgrade_refuses_account_override_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    person_id, account_id = _insert_test_account(postgres_engine)
    override_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_permission_overrides "
                    "(id, user_id, permission_id, effect, scope_kind, scope_id) "
                    "VALUES (:id, :user_id, :permission_id, 'deny', 'self', NULL)"
                ),
                {
                    "id": override_id,
                    "user_id": account_id,
                    "permission_id": PORTAL_PERMISSION_ID,
                },
            )
        with pytest.raises(
            RuntimeError,
            match="cannot downgrade portal with account permission overrides",
        ):
            command.downgrade(config, "0005_admissions_enrollment")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_coach_family_portal"
            assert connection.scalar(
                text(
                    "SELECT to_regclass('public.ix_cohorts_portal_next') IS NOT NULL"
                )
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
        _delete_test_account(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )


def test_portal_upgrade_refuses_preexisting_grant_before_ddl(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000002"),
        "permission_id": UUID("20000000-0000-4000-8000-000000000021"),
    }
    command.downgrade(config, "0005_admissions_enrollment")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO role_permissions "
                    "(role_id, permission_id, scope_kind) "
                    "VALUES (:role_id, :permission_id, 'assigned_class')"
                ),
                params,
            )
        with pytest.raises(
            RuntimeError,
            match="cannot upgrade portal with preexisting default grants",
        ):
            command.upgrade(config, "0006_coach_family_portal")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0005_admissions_enrollment"
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            ) == 1
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE id = :permission_id"
                ),
                {"permission_id": PORTAL_PERMISSION_ID},
            ) == 0
            assert connection.scalar(
                text(
                    "SELECT to_regclass('public.ix_cohorts_portal_next') IS NULL"
                )
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = :role_id AND permission_id = :permission_id "
                    "AND scope_kind = 'assigned_class'"
                ),
                params,
            )
        command.upgrade(config, "head")
```

```python
@pytest.mark.parametrize(
    ("mutation", "cleanup", "message"),
    [
        (
            "UPDATE permissions SET name = '已修改名单权限' WHERE id = :permission_id",
            "UPDATE permissions SET name = '查看教练下一期名单' "
            "WHERE id = :permission_id",
            "cannot downgrade portal with modified permission seed",
        ),
        (
            "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
            "VALUES (:role_id, :permission_id, 'family')",
            "DELETE FROM role_permissions WHERE role_id = :role_id "
            "AND permission_id = :permission_id AND scope_kind = 'family'",
            "cannot downgrade portal with customized coach roster grants",
        ),
    ],
)
def test_portal_downgrade_refuses_seed_customization_before_ddl(
    postgres_engine: Engine,
    mutation: str,
    cleanup: str,
    message: str,
) -> None:
    config = _alembic_config(postgres_engine)
    params = {
        "role_id": UUID("10000000-0000-4000-8000-000000000006"),
        "permission_id": PORTAL_PERMISSION_ID,
    }
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(mutation), params)
        with pytest.raises(RuntimeError, match=message):
            command.downgrade(config, "0005_admissions_enrollment")
        with postgres_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "0006_coach_family_portal"
            assert connection.scalar(
                text(
                    "SELECT to_regclass('public.ix_cohorts_portal_next') IS NOT NULL"
                )
            ) is True
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(cleanup), params)
```

- [ ] **Step 2: Run the migration tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须先设置隔离测试库}" \
  .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py -q
```

Expected: FAIL because revision `0006_coach_family_portal`, permission UUID 25 and the three indexes do not exist.

- [ ] **Step 3: Implement the migration and seed constants**

Create the revision with these exact grants and indexes:

```python
"""Add coach and family portal grants and query indexes."""

from collections.abc import Sequence
from uuid import UUID

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

revision: str = "0006_coach_family_portal"
down_revision: str | Sequence[str] | None = "0005_admissions_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PORTAL_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000025")
PORTAL_GRANTS = (
    ("admin", "coach_roster.read", "global"),
    ("coach", "coach_roster.read", "assigned_class"),
    ("coach", "enrollments.read", "assigned_class"),
    ("guardian", "teaching.read", "family"),
    ("guardian", "enrollments.read", "family"),
    ("guardian", "payments.read", "self"),
    ("student", "teaching.read", "self"),
    ("student", "enrollments.read", "self"),
    ("student", "payments.read", "self"),
)


def upgrade() -> None:
    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    existing_permission = bind.execute(
        sa.text(
            "SELECT id, key FROM permissions "
            "WHERE id = :permission_id OR key = 'coach_roster.read'"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    ).mappings().one_or_none()
    if existing_permission is not None:
        raise RuntimeError(
            "cannot upgrade portal with preexisting coach roster permission"
        )
    existing_grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            )
        ).tuples()
    )
    if existing_grants.intersection(PORTAL_GRANTS):
        raise RuntimeError("cannot upgrade portal with preexisting default grants")
    bind.execute(
        postgresql_insert(permissions)
        .values(
            id=PORTAL_PERMISSION_ID,
            key="coach_roster.read",
            name="查看教练下一期名单",
            sensitive=False,
        )
        .on_conflict_do_nothing()
    )
    role_ids: dict[str, UUID] = dict(
        bind.execute(sa.text("SELECT key, id FROM roles")).tuples()
    )
    permission_ids: dict[str, UUID] = dict(
        bind.execute(sa.text("SELECT key, id FROM permissions")).tuples()
    )
    if permission_ids["coach_roster.read"] != PORTAL_PERMISSION_ID:
        raise RuntimeError("coach_roster.read UUID does not match the fixed seed")
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for role_key, permission_key, scope_kind in PORTAL_GRANTS:
        bind.execute(
            postgresql_insert(role_permissions)
            .values(
                role_id=role_ids[role_key],
                permission_id=permission_ids[permission_key],
                scope_kind=scope_kind,
            )
            .on_conflict_do_nothing()
        )
    op.create_index(
        "ix_cohorts_portal_next",
        "cohorts",
        ["status", "starts_at", "id"],
    )
    op.create_index(
        "ix_coach_assignments_portal_current",
        "coach_assignments",
        ["coach_person_id", "valid_from", "valid_until", "class_id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "ix_enrollments_student_status_cohort",
        "enrollments",
        ["student_person_id", "status", "cohort_id", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    permission_row = bind.execute(
        sa.text(
            "SELECT id, key, name, sensitive FROM permissions "
            "WHERE id = :permission_id"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    ).mappings().one_or_none()
    if permission_row is None or dict(permission_row) != {
        "id": PORTAL_PERMISSION_ID,
        "key": "coach_roster.read",
        "name": "查看教练下一期名单",
        "sensitive": False,
    }:
        raise RuntimeError("cannot downgrade portal with modified permission seed")
    override_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM user_permission_overrides "
            "WHERE permission_id = :permission_id"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    )
    if override_count:
        raise RuntimeError("cannot downgrade portal with account permission overrides")
    new_permission_grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "WHERE role_permissions.permission_id = :permission_id"
            ),
            {"permission_id": PORTAL_PERMISSION_ID},
        ).tuples()
    )
    if new_permission_grants != {
        ("admin", "global"),
        ("coach", "assigned_class"),
    }:
        raise RuntimeError("cannot downgrade portal with customized coach roster grants")
    for role_key, permission_key, scope_kind in PORTAL_GRANTS:
        exists = bind.scalar(
            sa.text(
                "SELECT count(*) FROM role_permissions rp "
                "JOIN roles r ON r.id = rp.role_id "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE r.key = :role_key AND p.key = :permission_key "
                "AND rp.scope_kind = :scope_kind"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
        if exists != 1:
            raise RuntimeError("cannot downgrade portal with modified default grants")

    op.drop_index(
        "ix_enrollments_student_status_cohort",
        table_name="enrollments",
    )
    op.drop_index(
        "ix_coach_assignments_portal_current",
        table_name="coach_assignments",
    )
    op.drop_index("ix_cohorts_portal_next", table_name="cohorts")
    for role_key, permission_key, scope_kind in PORTAL_GRANTS:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions rp USING roles r, permissions p "
                "WHERE rp.role_id = r.id AND rp.permission_id = p.id "
                "AND r.key = :role_key AND p.key = :permission_key "
                "AND rp.scope_kind = :scope_kind"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
    bind.execute(
        sa.text(
            "DELETE FROM permissions WHERE id = :permission_id "
            "AND key = 'coach_roster.read'"
        ),
        {"permission_id": PORTAL_PERMISSION_ID},
    )
```

In `rbac/seed.py`, add:

```python
"coach_roster.read": PermissionSeed(
    UUID("20000000-0000-4000-8000-000000000025"),
    "查看教练下一期名单",
),
```

and append the eight non-admin grants from `PORTAL_GRANTS`; the existing admin comprehension will add the global grant. Mirror all three `Index(...)` definitions in the ORM models, including `postgresql_where=text("ended_at IS NULL")` on the coach index. Update `EXPECTED_ALEMBIC_HEAD=0006_coach_family_portal`.

Update the existing exact grant tests as part of the same RED/GREEN cycle. The
teaching grant set must now include `guardian/teaching.read/family` and
`student/teaching.read/self`, so remove the old assertion that categorically forbids
guardian/student teaching grants. The admissions grant set must additionally include
`coach/enrollments.read/assigned_class`, guardian `enrollments.read/family` and
`payments.read/self`, plus student `enrollments.read/self` and `payments.read/self`.

- [ ] **Step 4: Run migration, seed and static checks**

Run:

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须先设置隔离测试库}" \
  .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py -q
.venv/bin/ruff check apps/api/app/rbac apps/api/app/teaching/models.py \
  apps/api/app/admissions/models.py apps/api/migrations/versions/0006_coach_family_portal.py
```

Expected: all selected tests PASS and Ruff exits 0.

- [ ] **Step 5: Commit the RBAC foundation**

```bash
git add apps/api/migrations/versions/0006_coach_family_portal.py \
  apps/api/app/rbac/seed.py apps/api/app/teaching/models.py \
  apps/api/app/admissions/models.py apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py scripts/db-schema-manifest.sh
git commit -m "feat: add portal permission foundation"
```

### Task 2: Add the portal package and multi-identity context

**Files:**

- Create: `apps/api/app/portal/__init__.py`
- Create: `apps/api/app/portal/routing.py`
- Create: `apps/api/app/portal/schemas.py`
- Create: `apps/api/app/portal/errors.py`
- Create: `apps/api/app/portal/context_service.py`
- Create: `apps/api/app/portal/dependencies.py`
- Create: `apps/api/app/portal/router.py`
- Create: `apps/api/tests/services/test_portal_context.py`
- Create: `apps/api/tests/api/test_portal.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/app/core/errors.py`
- Modify: `apps/api/Dockerfile`

- [ ] **Step 1: Write failing context service tests**

The `portal_context_scenario` fixture uses seven enabled accounts with fixed role sets: admin; coach; guardian; student; coach+guardian; student+guardian; and staff. It exposes `context_for(key)`, plus `allow()`/`deny()` helpers that insert `UserPermissionOverride` rows and flush the session. Assert the exact projection:

```python
def test_context_combines_coach_and_family_without_switching_sessions(
    portal_context_scenario,
) -> None:
    service = PortalContextService(portal_context_scenario.db)
    result = service.get_context(
        user_id=portal_context_scenario.multi_account.id,
        actor_person_id=portal_context_scenario.multi_person.id,
    )
    assert result == PortalContextResponse(
        coach_available=True,
        learning_available=True,
        payments_available=True,
        learning_mode="family",
    )


def test_context_keeps_pure_back_office_account_out_of_portal(
    portal_context_scenario,
) -> None:
    result = PortalContextService(portal_context_scenario.db).get_context(
        user_id=portal_context_scenario.admin_account.id,
        actor_person_id=portal_context_scenario.admin_person.id,
    )
    assert result == PortalContextResponse(
        coach_available=False,
        learning_available=False,
        payments_available=False,
        learning_mode=None,
    )


def test_context_honors_generic_explicit_deny(portal_context_scenario) -> None:
    portal_context_scenario.deny(
        portal_context_scenario.multi_account,
        "enrollments.read",
        "assigned_class",
    )
    result = PortalContextService(portal_context_scenario.db).get_context(
        user_id=portal_context_scenario.multi_account.id,
        actor_person_id=portal_context_scenario.multi_person.id,
    )
    assert result.coach_available is False
    assert result.learning_available is True


def test_context_reports_all_learning_modes(portal_context_scenario) -> None:
    assert portal_context_scenario.context_for("student").learning_mode == "self"
    assert portal_context_scenario.context_for("guardian").learning_mode == "family"
    assert portal_context_scenario.context_for("student_guardian").learning_mode == (
        "self_and_family"
    )


def test_staff_account_level_self_payment_allow_is_visible(
    portal_context_scenario,
) -> None:
    portal_context_scenario.allow(
        portal_context_scenario.staff_account,
        "payments.read",
        "self",
    )
    result = PortalContextService(portal_context_scenario.db).get_context(
        user_id=portal_context_scenario.staff_account.id,
        actor_person_id=portal_context_scenario.staff_person.id,
    )
    assert result.payments_available is True
    assert result.learning_available is False
```

Create `test_portal.py` with the existing `platform_users` fixture and a local `sign_in()` helper. The first route contract is:

```python
def sign_in(client: TestClient, platform_users, key: str) -> None:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert response.status_code == 200


def test_guardian_portal_context_is_private(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    response = db_client.get("/api/v1/portal/context")
    assert response.status_code == 200
    assert response.json() == {
        "coach_available": False,
        "learning_available": True,
        "payments_available": True,
        "learning_mode": "family",
    }
    assert response.headers["cache-control"] == "no-store, private"


def test_portal_authentication_error_is_also_private(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/portal/context")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
    assert response.headers["cache-control"] == "no-store, private"
```

- [ ] **Step 2: Run the context tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_context.py -q
```

Expected: collection/import failure because `app.portal` and `/portal/context` do not exist.

- [ ] **Step 3: Implement schemas, private routing and context service**

Copy the complete Pydantic contracts from the frozen-contract section into `schemas.py`. Put the four frozen error classes in `errors.py`. Implement an ASGI middleware so dependency errors, validation errors and masked errors receive the same private header as successful responses:

```python
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PORTAL_PREFIXES = (
    "/api/v1/portal/",
    "/api/v1/coach/",
    "/api/v1/family/",
)
_PORTAL_EXACT_PATHS = {"/api/v1/me/payments"}


def _is_private_portal_path(path: str) -> bool:
    return path in _PORTAL_EXACT_PATHS or path.startswith(_PORTAL_PREFIXES)


class PrivatePortalResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_private_portal_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        state["private_portal_response"] = True

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
            await send(message)

        await self.app(scope, receive, send_private)
```

In `core/errors.py`, construct the response first and preserve the header even when Starlette's outer server-error middleware handles an unexpected exception outside user middleware:

```python
response = JSONResponse(status_code=status, content=body)
if getattr(request.state, "private_portal_response", False):
    response.headers["Cache-Control"] = "no-store, private"
return response
```

Implement capability checks with generic resource tokens, while keeping data authorization in later services:

```python
class PortalContextService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def _allows_all(
        self,
        user_id: UUID,
        permissions: tuple[str, ...],
        resource_tokens: set[str],
    ) -> bool:
        return all(
            self.rbac.decide(
                user_id=user_id,
                permission=permission,
                resource_tokens=resource_tokens,
            ).allowed
            for permission in permissions
        )

    def get_context(self, *, user_id: UUID, actor_person_id: UUID) -> PortalContextResponse:
        coach = self.rbac.account_has_role(user_id, "coach") and self._allows_all(
            user_id,
            ("teaching.read", "coach_roster.read", "enrollments.read"),
            {"global", "assigned_class"},
        )
        family = self.rbac.account_has_role(user_id, "guardian") and self._allows_all(
            user_id,
            ("people.read", "teaching.read", "enrollments.read"),
            {"global", "family"},
        )
        self_learning = (
            self.db.get(StudentProfile, actor_person_id) is not None
            and self._allows_all(
                user_id,
                ("people.read", "teaching.read", "enrollments.read"),
                {"global", f"self:{actor_person_id}"},
            )
        )
        payments = self.rbac.decide(
            user_id=user_id,
            permission="payments.read",
            resource_tokens={"global", f"self:{actor_person_id}"},
        ).allowed and not (
            self.rbac.account_has_role(user_id, "admin")
            and not self.rbac.account_has_role(user_id, "guardian")
            and not self_learning
        )
        learning_mode: PortalLearningMode | None = None
        if family and self_learning:
            learning_mode = "self_and_family"
        elif family:
            learning_mode = "family"
        elif self_learning:
            learning_mode = "self"
        return PortalContextResponse(
            coach_available=coach,
            learning_available=family or self_learning,
            payments_available=payments,
            learning_mode=learning_mode,
        )
```

Pure admin exclusion above prevents the global sensitive permission from creating a personal-payment card unless the account also has guardian or student capability. The preceding staff test fixes the account-level `payments.read/self` behavior.

Create the first dependency factory in `dependencies.py`; later tasks append one factory when its service is created:

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.portal.context_service import PortalContextService


def get_portal_context_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalContextService:
    return PortalContextService(db)
```

- [ ] **Step 4: Add the context route and production package copy**

Add to `portal/router.py`:

```python
router = APIRouter(tags=["portal"])


def _raise_portal_error(exc: PortalServiceError) -> NoReturn:
    if isinstance(exc, PortalPermissionDenied):
        raise ProblemError(
            status=403,
            code=exc.code,
            detail="没有访问此门户的权限",
        ) from exc
    if isinstance(exc, CoachCohortNotFound):
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="下一期营期不存在",
        ) from exc
    if isinstance(exc, FamilyStudentNotFound):
        raise ProblemError(
            status=404,
            code=exc.code,
            detail="学员教学信息不存在",
        ) from exc
    raise AssertionError(f"unmapped portal error: {type(exc).__name__}")


@router.get("/portal/context", response_model=PortalContextResponse)
def get_portal_context(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalContextService, Depends(get_portal_context_service)],
) -> PortalContextResponse:
    return service.get_context(
        user_id=context.account.id,
        actor_person_id=context.person.id,
    )
```

Register `portal.router` in `app/api/router.py`, add `app.add_middleware(PrivatePortalResponseMiddleware)` in `create_app()` beside `RequestIdMiddleware`, and add `COPY app/portal ./app/portal` beside the other runtime package copies in `apps/api/Dockerfile`.

- [ ] **Step 5: Run service and API smoke tests**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py apps/api/tests/api/test_health.py -q
.venv/bin/mypy --config-file apps/api/pyproject.toml apps/api/app/portal
```

Expected: tests PASS, mypy exits 0, and the existing health route remains registered.

- [ ] **Step 6: Commit the portal context**

```bash
git add apps/api/app/portal apps/api/app/api/router.py apps/api/app/main.py \
  apps/api/app/core/errors.py \
  apps/api/Dockerfile apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: expose portal capability context"
```

### Task 3: Implement the coach next-cohort roster

**Files:**

- Create: `apps/api/app/portal/coach_service.py`
- Create: `apps/api/tests/services/test_portal_coach.py`
- Modify: `apps/api/app/portal/dependencies.py`
- Modify: `apps/api/app/portal/router.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Write failing coach selection and privacy tests**

The `portal_coach_scenario` fixture freezes `now=2026-07-29T08:00:00+08:00`, creates two authorized classes starting `2030-08-01T09:00:00+08:00`, one authorized class starting `2030-08-15T09:00:00+08:00`, one unassigned future class, one already-active cohort, one active enrollment per authorized class, and one cancelled enrollment. Assert:

```python
def test_coach_defaults_to_nearest_authorized_future_start(portal_coach_scenario) -> None:
    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )
    assert result.selected_starts_at == portal_coach_scenario.nearest_start
    assert {item.class_id for item in result.classes} == {
        portal_coach_scenario.primary_class.id,
        portal_coach_scenario.assistant_class.id,
    }
    assert {item.student_name for item in result.items} == {"最近一班学员", "最近二班学员"}
    assert all(item.enrollment_status == "active" for item in result.items)


def test_coach_can_select_a_later_authorized_start(portal_coach_scenario) -> None:
    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=portal_coach_scenario.later_cohort.id,
        page=1,
        page_size=20,
    )
    assert result.selected_starts_at == portal_coach_scenario.later_start
    assert [item.student_name for item in result.items] == ["稍后营期学员"]


def test_coach_anchor_masks_unassigned_cohort(portal_coach_scenario) -> None:
    with pytest.raises(CoachCohortNotFound):
        portal_coach_scenario.service.list_next(
            user_id=portal_coach_scenario.coach_account.id,
            actor_person_id=portal_coach_scenario.coach.id,
            now=portal_coach_scenario.now,
            anchor_cohort_id=portal_coach_scenario.unassigned_cohort.id,
            page=1,
            page_size=20,
        )


def test_generic_coach_deny_returns_permission_error(portal_coach_scenario) -> None:
    portal_coach_scenario.deny(
        portal_coach_scenario.coach_account,
        "coach_roster.read",
        "assigned_class",
    )
    with pytest.raises(PortalPermissionDenied):
        portal_coach_scenario.service.list_next(
            user_id=portal_coach_scenario.coach_account.id,
            actor_person_id=portal_coach_scenario.coach.id,
            now=portal_coach_scenario.now,
            anchor_cohort_id=None,
            page=1,
            page_size=20,
        )
```

At API level, assert serialized JSON contains none of `identity_number`, `phone`, `email`, `payment_status`, `paid_cents`, `agreed_tuition_cents`.

- [ ] **Step 2: Run coach tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_coach.py \
  apps/api/tests/api/test_portal.py -k coach -q
```

Expected: FAIL because `PortalCoachService` and `/coach/next-cohorts` are missing.

- [ ] **Step 3: Implement authorized class discovery and start grouping**

Use one aware `now` for assignment windows, cohort filtering and RBAC. The discovery query must include:

```python
def _require_capability(self, *, user_id: UUID) -> None:
    generic_tokens = {"global", "assigned_class"}
    allowed = self.rbac.account_has_role(user_id, "coach") and all(
        self.rbac.decide(
            user_id=user_id,
            permission=permission,
            resource_tokens=generic_tokens,
        ).allowed
        for permission in ("teaching.read", "coach_roster.read", "enrollments.read")
    )
    if not allowed:
        raise PortalPermissionDenied("coach portal capability is required")


self._require_capability(user_id=user_id)
assignment_rows = list(
    self.db.execute(
        select(CoachAssignment, TeachingClass, Cohort, GameProject, ProfessionalTeam)
        .join(TeachingClass, TeachingClass.id == CoachAssignment.class_id)
        .join(Cohort, Cohort.id == TeachingClass.cohort_id)
        .join(GameProject, GameProject.id == TeachingClass.project_id)
        .outerjoin(
            ProfessionalTeam,
            ProfessionalTeam.id == TeachingClass.professional_team_id,
        )
        .where(
            CoachAssignment.coach_person_id == actor_person_id,
            CoachAssignment.valid_from <= now,
            or_(
                CoachAssignment.valid_until.is_(None),
                CoachAssignment.valid_until > now,
            ),
            or_(CoachAssignment.ended_at.is_(None), CoachAssignment.ended_at > now),
            Cohort.status.in_(("open", "upcoming")),
            Cohort.starts_at > now,
            TeachingClass.is_active.is_(True),
            CoachEligibility.eligible_person_predicate(
                CoachAssignment.coach_person_id
            ),
        )
        .order_by(Cohort.starts_at, Cohort.id, TeachingClass.name, TeachingClass.id)
    ).tuples()
)
```

For every candidate class, call `RbacService.class_resource_tokens(..., at=now)` and retain it only when `teaching.read`, `coach_roster.read` and `enrollments.read` are all allowed. Group retained rows by exact `Cohort.starts_at`; use the lowest cohort UUID as `anchor_cohort_id` for that group. If an explicit anchor is not among retained rows, raise `CoachCohortNotFound`.

Append the coach service dependency only after `coach_service.py` exists:

```python
def get_portal_coach_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalCoachService:
    return PortalCoachService(db)
```

- [ ] **Step 4: Implement roster paging and response mapping**

For selected class IDs, query only `Enrollment.status == "active"`, join `Person` and `StudentProfile`, and compute age with:

```python
age_status: AgeStatus = (
    "minor" if is_minor(person.date_of_birth, business_date_at(now)) else "adult"
)
```

Count before offset/limit; order by cohort start, cohort ID, project sort order, class name/ID, person name/ID. Run a separate grouped count over all selected active enrollments so `CoachClassSummary.student_count` is independent of the current page. Sort options by `starts_at, anchor_cohort_id`, classes by `starts_at, cohort_id, project.sort_order, class_name, class_id`, and use `typing.cast` when mapping database strings into `ClassType` and `AssignmentType`. Construct `CoachClassSummary` and `CoachRosterStudent` exactly as frozen above. When no retained future rows exist, return:

```python
return CoachNextCohortsResponse(
    observed_at=now,
    selected_starts_at=None,
    options=[],
    classes=[],
    items=[],
    page=page,
    page_size=page_size,
    total=0,
)
```

- [ ] **Step 5: Add the route and masked error**

```python
@router.get("/coach/next-cohorts", response_model=CoachNextCohortsResponse)
def get_coach_next_cohorts(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalCoachService, Depends(get_portal_coach_service)],
    anchor_cohort_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CoachNextCohortsResponse:
    try:
        return service.list_next(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            now=datetime.now(UTC),
            anchor_cohort_id=anchor_cohort_id,
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)
```

The API test for the generic deny must assert `403 permission_denied`; an unassigned but otherwise authorized coach must still receive the documented `200` empty response. Add a parameterized cache test for the successful response, the generic `403`, a masked anchor `404`, and invalid `page=0` `422`; all four must contain `Cache-Control: no-store, private`.

- [ ] **Step 6: Run coach tests and commit**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_coach.py \
  apps/api/tests/api/test_portal.py -k coach -q
.venv/bin/ruff check apps/api/app/portal apps/api/tests/services/test_portal_coach.py
.venv/bin/mypy --config-file apps/api/pyproject.toml apps/api/app/portal
```

Expected: all selected tests PASS.

```bash
git add apps/api/app/portal apps/api/tests/services/test_portal_coach.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: expose coach next cohort roster"
```

### Task 4: Implement family and self learning projections

**Files:**

- Create: `apps/api/app/portal/learning_service.py`
- Create: `apps/api/tests/services/test_portal_learning.py`
- Modify: `apps/api/app/portal/dependencies.py`
- Modify: `apps/api/app/portal/router.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Write failing family, adult-relation and self tests**

Use one guardian bound to a minor and an adult plus one student account. Assert:

```python
def test_guardian_lists_minor_and_adult_active_relationships(portal_learning_scenario) -> None:
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )
    assert [(item.student_name, item.access_source) for item in result.items] == [
        ("未成年在营学员", "family"),
        ("成年后仍绑定学员", "family"),
    ]


def test_student_lists_only_self(portal_learning_scenario) -> None:
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.student_account.id,
        actor_person_id=portal_learning_scenario.adult_student.id,
        reference_date=portal_learning_scenario.today,
    )
    assert [item.student_person_id for item in result.items] == [
        portal_learning_scenario.adult_student.id
    ]
    assert result.items[0].access_source == "self"


def test_ended_relationship_masks_teaching_detail(portal_learning_scenario) -> None:
    portal_learning_scenario.end_minor_relationship()
    with pytest.raises(FamilyStudentNotFound):
        portal_learning_scenario.service.get_teaching(
            user_id=portal_learning_scenario.guardian_account.id,
            actor_person_id=portal_learning_scenario.guardian.id,
            student_person_id=portal_learning_scenario.minor_student.id,
            reference_date=portal_learning_scenario.today,
            page=1,
            page_size=20,
        )


def test_person_deny_removes_only_one_family_candidate(
    portal_learning_scenario,
) -> None:
    portal_learning_scenario.deny_person(
        portal_learning_scenario.guardian_account,
        "people.read",
        portal_learning_scenario.minor_student.id,
    )
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )
    assert [item.student_person_id for item in result.items] == [
        portal_learning_scenario.adult_student.id
    ]


def test_family_deny_removes_family_candidates_but_keeps_self(
    portal_learning_scenario,
) -> None:
    portal_learning_scenario.deny(
        portal_learning_scenario.student_guardian_account,
        "enrollments.read",
        "family",
    )
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.student_guardian_account.id,
        actor_person_id=portal_learning_scenario.adult_student.id,
        reference_date=portal_learning_scenario.today,
    )
    assert [(item.student_person_id, item.access_source) for item in result.items] == [
        (portal_learning_scenario.adult_student.id, "self")
    ]
```

Add one deterministic history assertion with four enrollment IDs proving the order is current active, nearest upcoming active, completed history, then cancelled history; ties end with `Enrollment.id`.

- [ ] **Step 2: Run learning tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_learning.py \
  apps/api/tests/api/test_portal.py -k learning -q
```

Expected: FAIL because `PortalLearningService` and family routes are missing.

- [ ] **Step 3: Implement candidate enumeration and per-object authorization**

Collect candidate IDs from active family relationships plus self:

```python
def _capability_sources(
    self,
    *,
    user_id: UUID,
    actor_person_id: UUID,
) -> tuple[bool, bool]:
    family = self.rbac.account_has_role(user_id, "guardian") and all(
        self.rbac.decide(
            user_id=user_id,
            permission=permission,
            resource_tokens={"global", "family"},
        ).allowed
        for permission in ("people.read", "teaching.read", "enrollments.read")
    )
    self_access = self.db.get(StudentProfile, actor_person_id) is not None and all(
        self.rbac.decide(
            user_id=user_id,
            permission=permission,
            resource_tokens={"global", f"self:{actor_person_id}"},
        ).allowed
        for permission in ("people.read", "teaching.read", "enrollments.read")
    )
    if not family and not self_access:
        raise PortalPermissionDenied("learning portal capability is required")
    return family, self_access


family_capability, self_capability = self._capability_sources(
    user_id=user_id,
    actor_person_id=actor_person_id,
)
candidate_sources: dict[UUID, PortalAccessSource] = {}
if family_capability:
    for student_id in self.db.scalars(
        select(GuardianRelationship.student_person_id).where(
            GuardianRelationship.guardian_person_id == actor_person_id,
            GuardianRelationship.ended_at.is_(None),
            GuardianRelationship.valid_from <= reference_date,
            or_(
                GuardianRelationship.valid_until.is_(None),
                GuardianRelationship.valid_until >= reference_date,
            ),
        )
    ):
        candidate_sources[student_id] = "family"
if self_capability:
    candidate_sources[actor_person_id] = "self"
```

Batch-load live `Person` and `StudentProfile` rows. Retain a candidate only when all three permissions pass against `RbacService.person_resource_tokens(...)`:

```python
def _student_allowed(self, *, user_id: UUID, actor_person_id: UUID, student_id: UUID, reference_date: date) -> bool:
    tokens = self.rbac.person_resource_tokens(
        actor_person_id=actor_person_id,
        target_person_id=student_id,
        reference_date=reference_date,
    )
    return all(
        self.rbac.decide(
            user_id=user_id,
            permission=permission,
            resource_tokens=tokens,
        ).allowed
        for permission in ("people.read", "teaching.read", "enrollments.read")
    )
```

Batch-load current enrollment summaries for retained IDs. Sort active cohort first, then future `open|upcoming` by nearest start, then name/ID.

Append the learning dependency in `dependencies.py`:

```python
def get_portal_learning_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalLearningService:
    return PortalLearningService(db)
```

- [ ] **Step 4: Implement teaching history and masked lookup**

`get_teaching()` must first verify that the target appears in the authorized candidate map; otherwise raise `FamilyStudentNotFound`. Query enrollment history joined to cohort, class, project and optional team, count before paging, and order using a SQL `case` expression:

```python
priority = case(
    (and_(Enrollment.status == "active", Cohort.status == "active"), 0),
    (
        and_(
            Enrollment.status == "active",
            Cohort.status.in_(("open", "upcoming")),
        ),
        1,
    ),
    else_=2,
)
query = query.order_by(
    priority,
    case((priority.in_((0, 1)), Cohort.starts_at), else_=None).asc(),
    Cohort.starts_at.desc(),
    Enrollment.id,
)
```

Return only frozen `LearningEnrollmentSummary` fields. Do not instantiate or serialize existing `EnrollmentResponse`. Map `status` and `class_type` with `typing.cast`; choose `current_enrollment` using the same priority/order expression and take the first row only.

- [ ] **Step 5: Add family routes and privacy assertions**

```python
@router.get("/family/students", response_model=LearningStudentsResponse)
def list_family_students(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalLearningService, Depends(get_portal_learning_service)],
) -> LearningStudentsResponse:
    try:
        return service.list_students(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            reference_date=business_today(),
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)


@router.get(
    "/family/students/{student_id}/teaching",
    response_model=LearningTeachingResponse,
)
def get_family_student_teaching(
    student_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalLearningService, Depends(get_portal_learning_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LearningTeachingResponse:
    try:
        return service.get_teaching(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            student_person_id=student_id,
            reference_date=business_today(),
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)
```

API tests must assert response text contains none of `identity`, `phone`, `email`, `guardian_issues`, `agreed_tuition_cents`, `payment_status`.

- [ ] **Step 6: Run learning tests and commit**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_learning.py \
  apps/api/tests/api/test_portal.py -k learning -q
.venv/bin/mypy --config-file apps/api/pyproject.toml apps/api/app/portal
```

Expected: all selected tests PASS and mypy exits 0.

```bash
git add apps/api/app/portal apps/api/tests/services/test_portal_learning.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: expose family learning portal"
```

### Task 5: Implement self-payment projection and audit

**Files:**

- Create: `apps/api/app/portal/payment_service.py`
- Create: `apps/api/tests/services/test_portal_payments.py`
- Modify: `apps/api/app/portal/dependencies.py`
- Modify: `apps/api/app/portal/router.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Write failing payer-isolation and refund tests**

Create two guardians paying for the same student, one payment with `payer_account_id=None`, and one partially refunded current-account payment:

```python
def test_self_payments_filter_by_current_account(portal_payment_scenario) -> None:
    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )
    assert [item.payment_id for item in result.items] == [
        portal_payment_scenario.first_payment.id
    ]
    assert result.items[0].status == "partially_refunded"
    assert result.items[0].refunded_cents == 20_000
    assert result.items[0].net_cents == 80_000
    assert [item.amount_cents for item in result.items[0].refunds] == [20_000]


def test_relationship_end_hides_student_name_but_keeps_payment(portal_payment_scenario) -> None:
    portal_payment_scenario.end_relationship()
    result = portal_payment_scenario.service.list_payments(
        user_id=portal_payment_scenario.first_account.id,
        actor_person_id=portal_payment_scenario.first_guardian.id,
        page=1,
        page_size=20,
    )
    assert result.items[0].payment_id == portal_payment_scenario.first_payment.id
    assert result.items[0].related_student_name is None


def test_self_payment_permission_deny_is_rejected(portal_payment_scenario) -> None:
    portal_payment_scenario.deny(
        portal_payment_scenario.first_account,
        "payments.read",
        "self",
    )
    with pytest.raises(PortalPermissionDenied):
        portal_payment_scenario.service.list_payments(
            user_id=portal_payment_scenario.first_account.id,
            actor_person_id=portal_payment_scenario.first_guardian.id,
            page=1,
            page_size=20,
        )
```

At API level, assert the other guardian payment, no-account manual payment, refund reason, manual note, external order ID and raw import payload never appear.

- [ ] **Step 2: Run payment tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_payments.py \
  apps/api/tests/api/test_portal.py -k payment -q
```

Expected: FAIL because `PortalPaymentService` and `/me/payments` are missing.

- [ ] **Step 3: Implement permission gate, paging and refund aggregation**

Start with an unforgeable self token and account filter:

```python
decision = self.rbac.decide(
    user_id=user_id,
    permission="payments.read",
    resource_tokens={"global", f"self:{actor_person_id}"},
)
if not decision.allowed:
    raise PortalPermissionDenied("payments.read/self is required")

query = select(Payment).where(Payment.payer_account_id == user_id)
total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
payments = list(
    self.db.scalars(
        query.order_by(Payment.paid_at.desc(), Payment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
)
```

Batch-load refunds by selected payment IDs and associated student IDs through `AdmissionCasePayment -> AdmissionCase`. Compute:

```python
refunded_cents = sum(refund.amount_cents for refund in refunds)
net_cents = payment.amount_cents - refunded_cents
status: PortalPaymentStatus = (
    "refunded"
    if net_cents == 0
    else "partially_refunded"
    if refunded_cents > 0
    else "paid"
)
```

Order refunds by `PaymentRefund.refunded_at, PaymentRefund.id`, and map `source_kind` and `refund_type` with `typing.cast`. Return `related_student_name` only when a current family/self relationship token exists and the permission decision still passes without a global token:

```python
tokens = self.rbac.person_resource_tokens(
    actor_person_id=actor_person_id,
    target_person_id=student_id,
)
has_current_relationship = (
    f"self:{student_id}" in tokens or f"family:{student_id}" in tokens
)
student_name_allowed = has_current_relationship and self.rbac.decide(
    user_id=user_id,
    permission="people.read",
    resource_tokens=tokens - {"global"},
).allowed
```

This preserves `person:{student_id}` so an object-specific deny still wins, while `people.read/global` alone cannot keep a name visible after relationship revocation. Never include `Payment.manual_note`, `Payment.external_order_id` or `PaymentRefund.reason`.

Append the payment dependency in `dependencies.py`:

```python
def get_portal_payment_service(
    db: Annotated[Session, Depends(get_db)],
) -> PortalPaymentService:
    return PortalPaymentService(db)
```

- [ ] **Step 4: Add `/me/payments`, audit and private headers**

Add the complete route. The service and audit dependency use the same request-scoped `Session`; a denied request never appends an amount audit:

```python
@router.get("/me/payments", response_model=SelfPaymentsResponse)
def get_self_payments(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[PortalPaymentService, Depends(get_portal_payment_service)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SelfPaymentsResponse:
    try:
        result = service.list_payments(
            user_id=context.account.id,
            actor_person_id=context.person.id,
            page=page,
            page_size=page_size,
        )
    except PortalServiceError as exc:
        _raise_portal_error(exc)
    AuditService(db).append(
        actor_user_id=context.account.id,
        action="portal.payment.list_amounts",
        resource_type="payment",
        resource_id=None,
        purpose=None,
        outcome="allowed",
        request_id=str(request.state.request_id),
        metadata={"result_count": len(result.items)},
    )
    db.commit()
    return result
```

The API test must assert `Cache-Control` equals `no-store, private` and the audit row exists with the current actor and no sensitive metadata.

- [ ] **Step 5: Run all portal backend tests and commit**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/services/test_portal_coach.py \
  apps/api/tests/services/test_portal_learning.py \
  apps/api/tests/services/test_portal_payments.py \
  apps/api/tests/api/test_portal.py -q
.venv/bin/ruff check apps/api/app/portal apps/api/tests/services/test_portal_*.py \
  apps/api/tests/api/test_portal.py
.venv/bin/mypy --config-file apps/api/pyproject.toml apps/api/app/portal
```

Expected: all portal tests PASS; Ruff and mypy exit 0.

```bash
git add apps/api/app/portal apps/api/tests/services/test_portal_payments.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: expose self payment history"
```

### Task 6: Add the typed Web portal client and context hook

**Files:**

- Create: `apps/web/src/portal/api.ts`
- Create: `apps/web/src/portal/api.test.ts`
- Create: `apps/web/src/portal/usePortalContext.ts`
- Create: `apps/web/src/portal/usePortalAccessError.ts`
- Modify: `apps/web/src/api/client.ts`
- Modify: `apps/web/src/api/client.test.ts`
- Modify: `apps/web/src/auth/AuthProvider.tsx`
- Modify: `apps/web/src/auth/AuthProvider.test.tsx`

- [ ] **Step 1: Write failing request-contract tests**

Stub `fetch` and assert exact URLs:

```ts
it("requests each private portal endpoint with stable pagination", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)

  await getCoachNextCohorts({ anchorCohortId: "cohort-1", page: 2, pageSize: 20 })
  await getLearningTeaching("student-1", { page: 3, pageSize: 10 })
  await getSelfPayments({ page: 1, pageSize: 5 })

  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    "/api/v1/coach/next-cohorts?anchor_cohort_id=cohort-1&page=2&page_size=20",
    "/api/v1/family/students/student-1/teaching?page=3&page_size=10",
    "/api/v1/me/payments?page=1&page_size=5",
  ])
})
```

Add separate assertions for `/portal/context` and `/family/students`.

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```bash
npm --prefix apps/web test -- src/portal/api.test.ts
```

Expected: FAIL because `src/portal/api.ts` does not exist.

- [ ] **Step 3: Implement exact TypeScript types and GET helpers**

Copy all frozen TypeScript contracts into `api.ts` and add:

```ts
function pageQuery(page = 1, pageSize = 20): URLSearchParams {
  return new URLSearchParams({ page: String(page), page_size: String(pageSize) })
}

export function getPortalContext(): Promise<PortalContext> {
  return apiFetch<PortalContext>("/portal/context")
}

export function getCoachNextCohorts(input: {
  anchorCohortId?: string
  page?: number
  pageSize?: number
} = {}): Promise<CoachNextCohorts> {
  const query = new URLSearchParams()
  if (input.anchorCohortId) {
    query.set("anchor_cohort_id", input.anchorCohortId)
  }
  query.set("page", String(input.page ?? 1))
  query.set("page_size", String(input.pageSize ?? 20))
  return apiFetch<CoachNextCohorts>(
    `/coach/next-cohorts?${query.toString()}`,
  )
}

export function getLearningStudents(): Promise<LearningStudents> {
  return apiFetch<LearningStudents>("/family/students")
}

export function getLearningTeaching(
  studentId: string,
  input: { page?: number; pageSize?: number } = {},
): Promise<LearningTeaching> {
  const query = pageQuery(input.page, input.pageSize)
  return apiFetch<LearningTeaching>(
    `/family/students/${studentId}/teaching?${query}`,
  )
}

export function getSelfPayments(
  input: { page?: number; pageSize?: number } = {},
): Promise<SelfPayments> {
  const query = pageQuery(input.page, input.pageSize)
  return apiFetch<SelfPayments>(`/me/payments?${query}`)
}
```

- [ ] **Step 4: Implement a shared refetching context hook**

```ts
export function usePortalContext() {
  const { user } = useAuth()
  return useQuery({
    queryKey: user ? portalKeys.context(user.account_id) : ["portal", "anonymous"],
    queryFn: getPortalContext,
    enabled: Boolean(user?.account_id),
    staleTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
  })
}

export const privatePortalQueryOptions = {
  staleTime: 0,
  gcTime: 0,
  refetchOnMount: "always" as const,
  refetchOnWindowFocus: true,
}
```

Every coach, learning and payment component must use `portalKeys` with the current `account_id`. While a private query has `isFetching === true`, hide its previous business rows behind the loading state; do not render cached names or amounts during reauthorization.

- [ ] **Step 5: Add global invalid-session cleanup and scoped portal revocation handling**

In `api/client.ts`, publish only a real session-invalid response; wrong login credentials and ordinary 403/500 responses must not trigger it:

```ts
let invalidSessionHandler: (() => void) | null = null

export function setInvalidSessionHandler(handler: (() => void) | null): void {
  invalidSessionHandler = handler
}

// Inside apiFetch, after constructing the error and before throwing it:
const error = await ProblemDetailsError.fromResponse(response)
if (response.status === 401 && error.problem.code === "invalid_session") {
  invalidSessionHandler?.()
}
throw error
```

In `AuthProvider`, clear all queries before accepting a newly logged-in account, and register the invalid-session handler before restoring the session:

```tsx
const clearInvalidSession = useCallback(() => {
  setCsrfToken(null)
  queryClient.clear()
  setUser(null)
  setStatus("anonymous")
}, [queryClient])

useEffect(() => {
  setInvalidSessionHandler(clearInvalidSession)
  return () => setInvalidSessionHandler(null)
}, [clearInvalidSession])

// First lines after a successful login response:
queryClient.clear()
setCsrfToken(result.csrf_token)
```

Create `usePortalAccessError.ts` for object-independent 403 revocation. A 500/network error is returned to the page for a retry state; a 401 is already handled globally:

```tsx
export function usePortalAccessError(error: unknown): {
  permissionRevoked: boolean
  message: string
} {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const permissionRevoked =
    error instanceof ProblemDetailsError && error.problem.status === 403

  useEffect(() => {
    if (!permissionRevoked || !user) return
    queryClient.removeQueries({ queryKey: portalKeys.root(user.account_id) })
    navigate("/", { replace: true })
  }, [navigate, permissionRevoked, queryClient, user])

  return {
    permissionRevoked,
    message:
      error instanceof ProblemDetailsError
        ? error.problem.detail
        : "服务暂时不可用，请重试。",
  }
}
```

Add tests that seed the QueryClient, then prove: successful login clears the old account key; `401 invalid_session` clears cached names/amounts and renders the login page; `403 permission_denied` does not mark the auth session anonymous; `500` retains the session and exposes a retry state.

- [ ] **Step 6: Run Web type, API and lint checks**

Run:

```bash
npm --prefix apps/web test -- src/portal/api.test.ts
npm --prefix apps/web test -- src/api/client.test.ts src/auth/AuthProvider.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: API tests PASS; TypeScript build and ESLint exit 0.

- [ ] **Step 7: Commit the portal client**

```bash
git add apps/web/src/portal/api.ts apps/web/src/portal/api.test.ts \
  apps/web/src/portal/usePortalContext.ts apps/web/src/portal/usePortalAccessError.ts \
  apps/web/src/api/client.ts apps/web/src/api/client.test.ts \
  apps/web/src/auth/AuthProvider.tsx apps/web/src/auth/AuthProvider.test.tsx
git commit -m "feat: add typed portal client"
```

### Task 7: Add the unified dashboard and dynamic navigation

**Files:**

- Create: `apps/web/src/portal/PortalDashboardPage.tsx`
- Create: `apps/web/src/portal/PortalDashboardPage.test.tsx`
- Create: `apps/web/src/portal/portal.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.css`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/styles/global.css`

- [ ] **Step 1: Write failing dashboard, navigation and route tests**

Cover a coach+guardian context and a pure admin context:

```tsx
it("shows coach, learning and payment tasks for a multi-identity account", async () => {
  mockPortalFetch({
    context: {
      coach_available: true,
      learning_available: true,
      payments_available: true,
      learning_mode: "family",
    },
  })
  renderPortalDashboard()
  expect(await screen.findByRole("link", { name: "查看下一期名单" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看家庭信息" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看本人付款" })).toBeVisible()
})


it("keeps the existing back-office overview for a pure admin", async () => {
  mockPortalFetch({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      learning_mode: null,
    },
  })
  renderPortalDashboard({ permissions: ["people.list", "accounts.manage"] })
  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(screen.queryByText("教练工作")).not.toBeInTheDocument()
})
```

In `router.test.tsx`, assert the index route renders the unified dashboard for portal and pure back-office accounts. Portal page files do not exist yet, so their routes are deliberately registered in Task 10 after all three page components compile. In `AppShell.test.tsx`, assert multi-identity items appear in desktop and mobile navigation without displacing the four-track overflow behavior.

- [ ] **Step 2: Run dashboard and routing tests and verify they fail**

Run:

```bash
npm --prefix apps/web test -- \
  src/portal/PortalDashboardPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
```

Expected: FAIL because the portal dashboard and dynamic navigation do not exist.

- [ ] **Step 3: Replace only the index dashboard**

```tsx
import { PortalDashboardPage } from "../portal/PortalDashboardPage"

// Authenticated children:
{ index: true, element: <PortalDashboardPage /> },
```

Do not import or register `CoachNextCohortsPage`, `LearningPage` or `MyPaymentsPage` in this task. `PortalDashboardPage.tsx` imports `./portal.css` once so all later portal components share the same stylesheet.

- [ ] **Step 4: Implement capability-driven navigation**

Extend `NavigationItem` with `portalCapability?: PortalCapability` and add:

```tsx
{
  to: "/coach/next-cohorts",
  label: "教练工作",
  shortLabel: "教练",
  icon: ClipboardList,
  mobilePriority: 1,
  portalCapability: "coach_available",
},
{
  to: "/learning",
  label: "学习信息",
  shortLabel: "学习",
  icon: BookOpenCheck,
  mobilePriority: 2,
  portalCapability: "learning_available",
},
{
  to: "/me/payments",
  label: "本人付款",
  shortLabel: "付款",
  icon: WalletCards,
  mobilePriority: 3,
  portalCapability: "payments_available",
},
```

Import `PortalCapability` from `portal/api.ts`; do not declare it in `router.tsx` or import it from the router. Pass the successful, non-fetching `portalContext` from `AuthenticatedLayout` into `AppShell` and filter portal items against it. Change existing mobile priorities to `people=10`, `teaching=11`, `accounts=12`, `permissions=13`, `audit=14`, leaving `home=0`, `coach=1`, `learning=2`, `payments=3`. With overflow, a multi-identity phone bar is therefore exactly 首页、教练、学习、更多; payment and back-office items remain reachable in 更多.

Upgrade the existing 更多 dialog in the same task: opening moves focus to its first link or close button, Tab/Shift+Tab stay inside, Escape/backdrop close it, closing restores focus to the 更多 trigger, and background scrolling is locked while open. Give the header logout, 更多 trigger/close, navigation links and portal card links `min-width`/`min-height: 44px` in `AppShell.css`/`portal.css`. Add exact focus-restoration and keyboard-cycle assertions to `AppShell.test.tsx`.

Delete `overflow-x: hidden` from `body` in `global.css`; responsive correctness is enforced by element-boundary tests rather than clipping.

- [ ] **Step 5: Implement task cards and preserve back-office cards**

`PortalDashboardPage` must fetch context and hide all previously rendered portal data whenever the context or an enabled card query is fetching. A 500/network failure renders an alert with a “重试” button and keeps old names/counts out of the DOM. The coach card calls `getCoachNextCohorts({ page: 1, pageSize: 1 })`; the learning card calls the unpaged `getLearningStudents()`. The payment card uses only `payments_available`: it must not call `/me/payments`, fetch amounts, or create an amount-view audit merely to render the dashboard. Render exact links:

```tsx
const learningTitle =
  context.learning_mode === "self"
    ? "我的学习"
    : context.learning_mode === "family"
      ? "家庭信息"
      : "学习信息"
const learningActionLabel = `查看${learningTitle}`

<Link to="/coach/next-cohorts" aria-label="查看下一期名单">查看名单</Link>
<Link to="/learning" aria-label={learningActionLabel}>查看学习信息</Link>
<Link to="/me/payments" aria-label="查看本人付款">查看记录</Link>
```

Render the existing `DashboardPage` foundation grid only when `user.permissions` contains a global back-office capability (`people.list`, `accounts.manage`, `teaching.read`, `admission_cases.read` or `audit.read`); scoped permissions alone do not make the back-office overview appear. Add a fetch assertion proving a payment-only dashboard never requests `/me/payments`.

- [ ] **Step 6: Run dashboard/navigation tests and commit**

Run:

```bash
npm --prefix apps/web test -- \
  src/portal/PortalDashboardPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
npm --prefix apps/web run build
```

Expected: selected tests and build PASS.

```bash
git add apps/web/src/portal/PortalDashboardPage.tsx \
  apps/web/src/portal/PortalDashboardPage.test.tsx apps/web/src/portal/portal.css \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.css \
  apps/web/src/layout/AppShell.test.tsx apps/web/src/styles/global.css
git commit -m "feat: add unified portal dashboard"
```

### Task 8: Build the responsive coach roster page

**Files:**

- Create: `apps/web/src/portal/CoachNextCohortsPage.tsx`
- Create: `apps/web/src/portal/CoachNextCohortsPage.test.tsx`
- Modify: `apps/web/src/portal/portal.css`

- [ ] **Step 1: Write failing coach page tests**

```tsx
it("shows the nearest start group and switches to a later group", async () => {
  const user = userEvent.setup()
  mockCoachResponses()
  renderCoachPage()

  expect(await screen.findByRole("heading", { name: "下一期学员" })).toBeVisible()
  expect(screen.getByText("最近一班学员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "2030-08-15 09:00" }))
  expect(await screen.findByText("稍后营期学员")).toBeVisible()
  expect(screen.queryByText("最近一班学员")).not.toBeInTheDocument()
})


it("does not render sensitive or financial fields", async () => {
  mockCoachResponses()
  renderCoachPage()
  await screen.findByText("最近一班学员")
  expect(document.body.textContent).not.toMatch(/身份证|手机号|邮箱|付款|学费|¥/)
})


it("shows an honest empty state when no future assignment exists", async () => {
  mockEmptyCoachResponse()
  renderCoachPage()
  expect(await screen.findByText("暂未安排下一期")).toBeVisible()
})


it("hides the old roster while refetching and shows a retryable server error", async () => {
  const scenario = mockCoachSuccessThenFailure(500)
  renderCoachPage()
  expect(await screen.findByText("最近一班学员")).toBeVisible()
  await scenario.refocus()
  expect(screen.queryByText("最近一班学员")).not.toBeInTheDocument()
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取下一期名单")
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
})


it("falls back to the nearest group when the selected anchor is revoked", async () => {
  mockRevokedCoachAnchor()
  const router = renderCoachPage("?anchor_cohort_id=later")
  expect(await screen.findByText("最近一班学员")).toBeVisible()
  expect(router.state.location.search).not.toContain("later")
})
```

- [ ] **Step 2: Run the component test and verify it fails**

Run:

```bash
npm --prefix apps/web test -- src/portal/CoachNextCohortsPage.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement start selection, class summaries and paging**

Use one query keyed by anchor and page:

```tsx
const { user } = useAuth()
const accountId = user?.account_id ?? "anonymous"
const roster = useQuery({
  queryKey: portalKeys.coach(accountId, anchorCohortId, page),
  queryFn: () => getCoachNextCohorts({ anchorCohortId, page, pageSize: 20 }),
  enabled: Boolean(user),
  ...privatePortalQueryOptions,
})
```

Changing a time option must call `setPage(1)` before setting its anchor. If a selected anchor returns `coach_cohort_not_found`, remove that exact query, clear the anchor and page to defaults, and refetch the nearest group once. Pass all other errors to `usePortalAccessError`; while `isFetching`, show only the loading state. A normal 500/network error shows a retry button, while 403 navigates home after clearing the account's portal cache. Render time options as buttons with `aria-pressed`; use `formatShanghaiDateTime` from teaching utilities. Render each `CoachClassSummary` as a section header and each student row with only name, number, adult/minor label, class and active status. Use the existing pagination button language “上一页”“下一页”. After anchor recovery, focus the page heading (`tabIndex={-1}`).

- [ ] **Step 4: Add responsive roster styles**

Implement these layout contracts in `portal.css`:

```css
.portalRosterHeader,
.portalRosterRow {
  display: grid;
  grid-template-columns: minmax(150px, 1.2fr) minmax(110px, 1fr) 88px 88px;
  gap: 12px;
  align-items: center;
}

@media (max-width: 960px) {
  .portalRosterHeader { display: none; }
  .portalRosterRow {
    grid-template-columns: 1fr 1fr;
    border: 1px solid var(--color-border-soft);
    border-radius: 14px;
    padding: 14px;
  }
}

@media (max-width: 600px) {
  .portalRosterRow { grid-template-columns: 1fr; }
  .portalStartOptions { display: grid; grid-template-columns: 1fr; }
}
```

All roster buttons must have `min-height: 44px`.

- [ ] **Step 5: Run coach page tests, build and commit**

Run:

```bash
npm --prefix apps/web test -- src/portal/CoachNextCohortsPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: test/build/lint PASS.

```bash
git add apps/web/src/portal/CoachNextCohortsPage.tsx \
  apps/web/src/portal/CoachNextCohortsPage.test.tsx apps/web/src/portal/portal.css
git commit -m "feat: show coach next cohort roster"
```

### Task 9: Build family and self learning pages

**Files:**

- Create: `apps/web/src/portal/LearningPage.tsx`
- Create: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/portal/portal.css`

- [ ] **Step 1: Write failing learner-switch and revocation tests**

```tsx
it("redirects the learning landing route to the first authorized student", async () => {
  mockLearningStudents([minorStudent, adultStudent])
  const router = renderLearningRoute("/learning")
  expect(await screen.findByRole("heading", { name: minorStudent.student_name })).toBeVisible()
  expect(router.state.location.pathname).toBe(`/learning/${minorStudent.student_person_id}`)
})


it("switches learners and keeps the student id in the query key and URL", async () => {
  const user = userEvent.setup()
  mockLearningStudents([minorStudent, adultStudent])
  const router = renderLearningRoute(`/learning/${minorStudent.student_person_id}`)
  const selector = await screen.findByLabelText("当前学员")
  await user.selectOptions(selector, adultStudent.student_person_id)
  expect(router.state.location.pathname).toBe(`/learning/${adultStudent.student_person_id}`)
  expect(await screen.findByText("成年后仍绑定学员")).toBeVisible()
})


it("shows the disabled review boundary without making a review request", async () => {
  const fetchMock = mockLearningStudents([minorStudent])
  renderLearningRoute(`/learning/${minorStudent.student_person_id}`)
  expect(await screen.findByText("学习评价")).toBeVisible()
  expect(screen.getByText("下一阶段开放")).toBeVisible()
  expect(screen.queryByRole("link", { name: /学习评价/ })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("review"))).toBe(false)
})


it("refetches candidates before falling back after relationship revocation", async () => {
  const scenario = mockRevokedStudentThenRemainingCandidate()
  const router = renderLearningRoute(`/learning/${minorStudent.student_person_id}`)
  expect(await screen.findByRole("heading", { name: adultStudent.student_name })).toBeVisible()
  expect(router.state.location.pathname).toBe(
    `/learning/${adultStudent.student_person_id}`,
  )
  expect(screen.queryByText(minorStudent.student_name)).not.toBeInTheDocument()
  expect(scenario.studentsRequests).toBe(2)
})
```

Add success-then-500 and success-then-403 refocus tests. Both must remove the old student name/history from the DOM before showing retry or navigating home.

- [ ] **Step 2: Run the learning page tests and verify they fail**

Run:

```bash
npm --prefix apps/web test -- src/portal/LearningPage.test.tsx
```

Expected: FAIL because `LearningPage` does not exist.

- [ ] **Step 3: Implement landing redirect, selector and history**

Use these queries:

```tsx
const { user } = useAuth()
const accountId = user?.account_id ?? "anonymous"
const students = useQuery({
  queryKey: portalKeys.students(accountId),
  queryFn: getLearningStudents,
  enabled: Boolean(user),
  ...privatePortalQueryOptions,
})

const teaching = useQuery({
  queryKey: portalKeys.teaching(accountId, studentId ?? "missing", page),
  queryFn: () => getLearningTeaching(studentId!, { page, pageSize: 20 }),
  enabled: Boolean(user && studentId),
  ...privatePortalQueryOptions,
})
```

When `/learning` has candidates, navigate with `{ replace: true }` to the first item returned by the server. When it has none, render “暂无可查看学员” and a link back to the dashboard without requesting a teaching detail. Show a `<select aria-label="当前学员">` only when `items.length > 1`; changing it resets page to 1 and updates the URL. While either query is fetching, hide all prior student names/history. Render current/upcoming enrollment first, then a history list with project, cohort, class type, optional professional team and status labels.

- [ ] **Step 4: Implement masked-error recovery and the review placeholder**

```tsx
useEffect(() => {
  if (!(teaching.error instanceof ProblemDetailsError)) return
  if (teaching.error.problem.code !== "family_student_not_found") return
  if (!user || !studentId) return
  let active = true
  queryClient.removeQueries({
    queryKey: portalKeys.teaching(user.account_id, studentId, page),
  })
  void queryClient
    .fetchQuery({
      queryKey: portalKeys.students(user.account_id),
      queryFn: getLearningStudents,
      staleTime: 0,
      gcTime: 0,
    })
    .then((refreshed) => {
      if (!active) return
      const next = refreshed.items[0]
      navigate(next ? `/learning/${next.student_person_id}` : "/learning", {
        replace: true,
      })
      requestAnimationFrame(() => headingRef.current?.focus())
    })
  return () => {
    active = false
  }
}, [navigate, page, queryClient, studentId, teaching.error, user])
```

Pass non-404 errors to `usePortalAccessError`. Render a 500/network error with retry and no old content; 403 clears the portal cache and navigates home. Render the review boundary as a non-interactive `<section aria-disabled="true">`; do not render a link or button.

- [ ] **Step 5: Add responsive learning styles**

Use two columns at 768+, one column at 360, and a full-width native select on phone. Keep every history card within `max-width: 100%`; long project/class names must wrap with `overflow-wrap: anywhere`.

- [ ] **Step 6: Run learning tests and commit**

Run:

```bash
npm --prefix apps/web test -- src/portal/LearningPage.test.tsx
npm --prefix apps/web run build
```

Expected: tests and build PASS.

```bash
git add apps/web/src/portal/LearningPage.tsx \
  apps/web/src/portal/LearningPage.test.tsx apps/web/src/portal/portal.css
git commit -m "feat: show family learning history"
```

### Task 10: Build the self-payment page

**Files:**

- Create: `apps/web/src/portal/MyPaymentsPage.tsx`
- Create: `apps/web/src/portal/MyPaymentsPage.test.tsx`
- Modify: `apps/web/src/portal/portal.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write failing payment display and least-privilege tests**

```tsx
it("shows only read-only self payments and refund totals", async () => {
  mockSelfPayments({
    items: [partiallyRefundedPayment],
    page: 1,
    page_size: 20,
    total: 1,
  })
  renderPaymentsPage()
  expect(await screen.findByText("¥1,000.00")).toBeVisible()
  expect(screen.getByText("已退款 ¥200.00")).toBeVisible()
  expect(screen.getByText("净额 ¥800.00")).toBeVisible()
  expect(screen.getByText("部分退款")).toBeVisible()
  expect(screen.queryByRole("button", { name: /退款|编辑|匹配|导出/ })).not.toBeInTheDocument()
})


it("uses a neutral label after related student access ends", async () => {
  mockSelfPayments({
    items: [{ ...partiallyRefundedPayment, related_student_name: null }],
    page: 1,
    page_size: 20,
    total: 1,
  })
  renderPaymentsPage()
  expect(await screen.findByText("历史付款")).toBeVisible()
})


it("removes old amounts before a refocus failure is handled", async () => {
  const scenario = mockPaymentSuccessThenFailure(500)
  renderPaymentsPage()
  expect(await screen.findByText("¥1,000.00")).toBeVisible()
  await scenario.refocus()
  expect(screen.queryByText("¥1,000.00")).not.toBeInTheDocument()
  expect(await screen.findByRole("alert")).toHaveTextContent("付款记录暂时无法读取")
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
})
```

- [ ] **Step 2: Run the payment page test and verify it fails**

Run:

```bash
npm --prefix apps/web test -- src/portal/MyPaymentsPage.test.tsx
```

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement read-only payment cards and pagination**

Use `formatCny` from `admissions/money.ts`. Query with private options and render payment amount, paid time, status, refund total, net amount, related student label and refund date/type. Never render backend identifiers, manual notes, external order IDs or refund reasons.

```tsx
const { user } = useAuth()
const accountId = user?.account_id ?? "anonymous"
const payments = useQuery({
  queryKey: portalKeys.payments(accountId, page),
  queryFn: () => getSelfPayments({ page, pageSize: 20 }),
  enabled: Boolean(user),
  ...privatePortalQueryOptions,
})

const PAYMENT_LABELS = {
  paid: "已付款",
  partially_refunded: "部分退款",
  refunded: "已全部退款",
} as const
```

While `payments.isFetching`, render only the loading state so cached amounts disappear during reauthorization. Pass errors through `usePortalAccessError`: 500/network failures show retry, 403 removes the account portal cache and returns home, and 401 is handled by `AuthProvider`. Render refunds inside a native `<details>`/`<summary>` control so they can be expanded by keyboard; changing pages focuses the page heading.

- [ ] **Step 4: Register all portal routes behind a resilient capability boundary**

All three page components now exist, so add their imports and route registration in this task. Reuse `PortalCapability` from `portal/api.ts`; a normal context failure stays on the route and offers retry, while a missing capability redirects home. Never render children while context is fetching, even if cached context data exists:

```tsx
function PortalCapabilityRoute({
  capability,
  children,
}: {
  capability: PortalCapability
  children: ReactNode
}) {
  const context = usePortalContext()
  const accessError = usePortalAccessError(context.error)
  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <LoadingScreen />
  }
  if (context.isError) {
    return (
      <main className="portalState" role="alert">
        <p>{accessError.message}</p>
        <button type="button" onClick={() => void context.refetch()}>重试</button>
      </main>
    )
  }
  if (!context.data?.[capability]) return <Navigate to="/" replace />
  return children
}

{ path: "/coach/next-cohorts", element: <PortalCapabilityRoute capability="coach_available"><CoachNextCohortsPage /></PortalCapabilityRoute> },
{ path: "/learning", element: <PortalCapabilityRoute capability="learning_available"><LearningPage /></PortalCapabilityRoute> },
{ path: "/learning/:studentId", element: <PortalCapabilityRoute capability="learning_available"><LearningPage /></PortalCapabilityRoute> },
{ path: "/me/payments", element: <PortalCapabilityRoute capability="payments_available"><MyPaymentsPage /></PortalCapabilityRoute> },
```

In `router.test.tsx`, cover each route with capability true and false, a pending/refetching context that does not mount the child, a 500 retry state that does not redirect, and `401 invalid_session` returning to `/login` after cache cleanup.

- [ ] **Step 5: Add responsive payment styles**

Use a two-column card grid at 1024+, one column below 768, and stacked amount lines at 360. Refund rows must wrap and all card actions are pagination only.

- [ ] **Step 6: Run payment and route tests, then commit**

Run:

```bash
npm --prefix apps/web test -- src/portal/MyPaymentsPage.test.tsx
npm --prefix apps/web test -- src/app/router.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: test/build/lint PASS.

```bash
git add apps/web/src/portal/MyPaymentsPage.tsx \
  apps/web/src/portal/MyPaymentsPage.test.tsx apps/web/src/portal/portal.css \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx
git commit -m "feat: show self payment history"
```

### Task 11: Extend deterministic E2E portal fixtures

**Files:**

- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/tests/helpers.ts`

- [ ] **Step 1: Write failing seed assertions**

Assert the seed contains:

```python
assert db.scalar(select(func.count(Person.id))) == 13
assert db.scalar(select(func.count(UserAccount.id))) == 6
roles_by_name: dict[str, set[str]] = defaultdict(set)
for full_name, role_key in db.execute(
    select(Person.full_name, Role.key)
    .join(UserAccount, UserAccount.person_id == Person.id)
    .join(UserRole, UserRole.user_id == UserAccount.id)
    .join(Role, Role.id == UserRole.role_id)
):
    roles_by_name[full_name].add(role_key)
assert roles_by_name["E2E教学教练"] == {"coach", "guardian"}
assert roles_by_name["E2E自助学员"] == {"student"}
assert roles_by_name["E2E另一付款家长"] == {"guardian"}
assert PasswordService().verify(
    "E2E-student-password-2026",
    student_account.password_hash,
)
assert db.get(UserRole, (coach_account.id, ROLES["guardian"].id)) is not None
assert db.scalar(
    select(func.count(CoachAssignment.id)).where(
        CoachAssignment.coach_person_id == E2E_COACH_PERSON_ID,
        CoachAssignment.ended_at.is_(None),
    )
) >= 3
assert db.scalar(
    select(func.count(Payment.id)).where(
        Payment.payer_account_id == E2E_GUARDIAN_ACCOUNT_ID,
    )
) >= 1
assert db.scalar(
    select(func.count(GuardianRelationship.id)).where(
        GuardianRelationship.guardian_person_id == E2E_GUARDIAN_PERSON_ID,
    )
) == 6
```

The standalone guardian owns exactly six active relationships (two existing minors plus four viewport adults); the seventh active relationship is coach → self student. Assert the total active relationship count is 7, the later future cohort starts after `E2E_COHORT_ID`, the three portal payments have the frozen payer accounts and amounts below, and each viewport-specific adult student has its distinct relationship ID. Run `seed_e2e()` a second time inside the same test and repeat the counts to prove ended relationships, permission overrides and browser-created rows are removed by rebuilding.

- [ ] **Step 2: Run the seed test and verify it fails**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py -q
```

Expected: FAIL because portal accounts, assignments, payments and later cohort are absent.

- [ ] **Step 3: Add deterministic portal identities and credentials**

Use these fixed values:

```python
STUDENT_PHONE = "19990000016"
STUDENT_PASSWORD = "E2E-student-password-2026"
OTHER_GUARDIAN_PHONE = "19990000010"
OTHER_GUARDIAN_PASSWORD = "E2E-other-guardian-password-2026"

E2E_SELF_STUDENT_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000007")
E2E_SELF_STUDENT_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000007")
E2E_OTHER_GUARDIAN_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000008")
E2E_OTHER_GUARDIAN_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000008")
E2E_LATER_COHORT_ID = UUID("e2100000-0000-4000-8000-000000000002")
E2E_LATER_CLASS_ID = UUID("e2120000-0000-4000-8000-000000000004")
E2E_STANDARD_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000001")
E2E_ELITE_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000002")
E2E_LATER_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000003")
E2E_COACH_FAMILY_RELATIONSHIP_ID = UUID("e2400000-0000-4000-8000-000000000005")
E2E_GUARDIAN_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000001")
E2E_ADULT_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000002")
E2E_SELF_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000003")
E2E_GUARDIAN_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000001")
E2E_ADULT_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000002")
E2E_SELF_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000003")
E2E_GUARDIAN_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000001")
E2E_STUDENT_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000002")
E2E_OTHER_GUARDIAN_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000003")
E2E_GUARDIAN_REFUND_ID = UUID("e2530000-0000-4000-8000-000000000001")

E2E_GUARDIAN_PAYMENT_CENTS = 100_000
E2E_STUDENT_PAYMENT_CENTS = 80_000
E2E_OTHER_GUARDIAN_PAYMENT_CENTS = 99_999
```

The self student is an adult with student role and account. Add guardian role to the coach account and an active guardian relationship from the coach person to the self student, making the coach a real multi-identity account without weakening coach eligibility.

- [ ] **Step 4: Seed assignments, enrollments and isolated payments**

Create current assignments with the three fixed assignment IDs: primary on the standard class, assistant on the elite class, and primary on the later class. Create active formal enrollments with the three fixed enrollment IDs: the bound minor in standard, the 1440 adult in elite, and the self student in later. Create the three fixed payments at `2030-07-01T09:00:00+08:00`, `2030-07-02T09:00:00+08:00`, and `2030-07-03T09:00:00+08:00`; attach guardian and other-guardian payments to `E2E_GUARDIAN_ADMISSION_CASE_ID`, attach the student payment to `E2E_SELF_ADMISSION_CASE_ID`, and add a `20_000` manual refund to the guardian payment. This freezes visible values at `¥1,000.00`, `¥800.00`, forbidden other-payer `¥999.99`, refunded `¥200.00`, and net `¥800.00`.

For the four existing viewport adult students, seed relationship IDs:

```python
PORTAL_RELATIONSHIPS_BY_VIEWPORT = {
    1440: UUID("e2400000-0000-4000-8000-000000000001"),
    1024: UUID("e2400000-0000-4000-8000-000000000002"),
    768: UUID("e2400000-0000-4000-8000-000000000003"),
    360: UUID("e2400000-0000-4000-8000-000000000004"),
}
```

All assignments must start at `2026-01-01T00:00:00+08:00` and end at `2031-01-01T00:00:00+08:00`. The later cohort starts at `2030-08-15T09:00:00+08:00`, ends at `2030-08-25T18:00:00+08:00`, and uses `open` status.

- [ ] **Step 5: Add Web login and viewport-boundary helpers**

```ts
export async function loginAsGuardian(page: Page): Promise<void> {
  await login(page, "19990000005", "E2E-guardian-password-2026")
}

export async function loginAsStudent(page: Page): Promise<void> {
  await login(page, "19990000016", "E2E-student-password-2026")
}

export async function expectHorizontallyInsideViewport(
  page: Page,
  selector: string,
): Promise<void> {
  const locator = page.locator(selector)
  expect(await locator.count()).toBeGreaterThan(0)
  await expect(locator.first()).toBeVisible()
  const boxes = await locator.evaluateAll((elements) =>
    elements
      .filter((element) => element.getClientRects().length > 0)
      .map((element) => element.getBoundingClientRect())
      .map((box) => ({ left: box.left, right: box.right })),
  )
  expect(boxes.length).toBeGreaterThan(0)
  const viewport = page.viewportSize()
  if (!viewport) throw new Error("Viewport is required for responsive E2E")
  for (const box of boxes) {
    expect(box.left).toBeGreaterThanOrEqual(0)
    expect(box.right).toBeLessThanOrEqual(viewport.width)
  }
}

export async function expectAboveMobileNavigation(
  page: Page,
  selector: string,
): Promise<void> {
  if (page.viewportSize()?.width !== 360) return
  const content = page.locator(selector).last()
  const navigation = page.locator(".shellMobileNavigation")
  await expect(content).toBeVisible()
  await expect(navigation).toBeVisible()
  const [contentBox, navigationBox] = await Promise.all([
    content.boundingBox(),
    navigation.boundingBox(),
  ])
  if (!contentBox || !navigationBox) throw new Error("Visible boxes are required")
  expect(contentBox.y + contentBox.height).toBeLessThanOrEqual(navigationBox.y)
}
```

- [ ] **Step 6: Run seed and existing E2E helper tests, then commit**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py -q
npm --prefix apps/web test
npm --prefix apps/web run lint
```

Expected: seed tests and all existing Web tests PASS.

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/web/tests/helpers.ts
git commit -m "test: seed portal user journeys"
```

### Task 12: Add four-viewport portal E2E and revocation journeys

**Files:**

- Create: `apps/web/tests/coach-family-portal.spec.ts`

- [ ] **Step 1: Write the coach and multi-identity journey**

```ts
test("coach opens the nearest roster and keeps family access in one session", async ({ page }) => {
  await loginAsCoach(page)
  await expect(page.getByRole("link", { name: "查看下一期名单" })).toBeVisible()
  await expect(page.getByRole("link", { name: "查看家庭信息" })).toBeVisible()
  await page.getByRole("link", { name: "查看下一期名单" }).click()
  await expect(page.getByRole("heading", { name: "下一期学员" })).toBeVisible()
  await expect(page.getByText("E2E英雄联盟普通班", { exact: true })).toBeVisible()
  await expect(page.getByText("E2E英雄联盟菁英班", { exact: true })).toBeVisible()
  await expectHorizontallyInsideViewport(page, ".portalRosterRow")
  await page.getByRole("button", { name: "2030-08-15 09:00" }).click()
  await expect(page.getByText("E2E自助学员", { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "学习信息" }).click()
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expect(page.getByText("身份证")).toHaveCount(0)
  await expect(page.getByText("付款金额")).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalLearningCard")
  await expectAboveMobileNavigation(page, ".portalLearningCard")
})
```

- [ ] **Step 2: Write guardian, student and payer journeys**

```ts
test("guardian switches minor and adult students and sees only own payments", async ({ page }) => {
  const viewport = page.viewportSize()?.width ?? 1440
  const adultName = `E2E招生学员${viewport}`
  await loginAsGuardian(page)
  await page.getByRole("link", { name: "查看家庭信息" }).click()
  const selector = page.getByLabel("当前学员")
  await selector.selectOption({ label: adultName })
  await expect(page.getByRole("heading", { name: adultName })).toBeVisible()
  await selector.selectOption({ label: "E2E已绑定学员" })
  await expect(page.getByRole("heading", { name: "E2E已绑定学员" })).toBeVisible()
  await expect(page.getByText("学习评价", { exact: true })).toBeVisible()
  await expect(page.getByText("下一阶段开放", { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "工作台" }).click()
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥1,000.00", { exact: true })).toBeVisible()
  await expect(page.getByText("已退款 ¥200.00", { exact: true })).toBeVisible()
  await expect(page.getByText("¥999.99", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalPaymentCard")
  await expectAboveMobileNavigation(page, ".portalPaymentCard")
})


test("student sees only self learning and self payments", async ({ page }) => {
  await loginAsStudent(page)
  await page.getByRole("link", { name: "查看我的学习" }).click()
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expect(page.getByLabel("当前学员")).toHaveCount(0)
  await page.goto("/learning/e1000000-0000-4000-8000-000000000004")
  await expect(page).not.toHaveURL(/e1000000-0000-4000-8000-000000000004$/)
  await expect(page.getByText("E2E已绑定学员", { exact: true })).toHaveCount(0)
  await page.getByRole("link", { name: "工作台" }).click()
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥800.00", { exact: true })).toBeVisible()
  await expect(page.getByText("¥1,000.00", { exact: true })).toHaveCount(0)
  await expect(page.getByText("¥999.99", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalPaymentCard")
})
```

- [ ] **Step 3: Write a viewport-isolated relationship revocation journey**

Define the map and use a second request context to authenticate as admin and end only that project's relationship:

```ts
const REVOCATION_STUDENTS = {
  1440: {
    id: "e2000000-0000-4000-8000-000000000010",
    name: "E2E招生学员1440",
    relationshipId: "e2400000-0000-4000-8000-000000000001",
  },
  1024: {
    id: "e2000000-0000-4000-8000-000000000020",
    name: "E2E招生学员1024",
    relationshipId: "e2400000-0000-4000-8000-000000000002",
  },
  768: {
    id: "e2000000-0000-4000-8000-000000000030",
    name: "E2E招生学员768",
    relationshipId: "e2400000-0000-4000-8000-000000000003",
  },
  360: {
    id: "e2000000-0000-4000-8000-000000000040",
    name: "E2E招生学员360",
    relationshipId: "e2400000-0000-4000-8000-000000000004",
  },
} as const

test("guardian relationship revocation clears the selected student", async ({ page }, testInfo) => {
  const width = page.viewportSize()?.width as keyof typeof REVOCATION_STUDENTS
  const student = REVOCATION_STUDENTS[width]
  await loginAsGuardian(page)
  await page.goto(`/learning/${student.id}`)
  await expect(page.getByRole("heading", { name: student.name })).toBeVisible()

const admin = await request.newContext({
  baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:18080",
})
const login = await admin.post("/api/v1/auth/login", {
  data: { phone: "19990000001", password: "E2E-admin-password-2026" },
})
const { csrf_token: csrfToken } = (await login.json()) as { csrf_token: string }
const ended = await admin.post(
  `/api/v1/people/${student.id}/guardians/${student.relationshipId}/end`,
  {
    headers: { "X-CSRF-Token": csrfToken },
    data: { reason: `四视口撤权 ${testInfo.project.name}` },
  },
)
expect(ended.status()).toBe(200)
await admin.dispose()

await page.goto(`/learning/${student.id}`)
await expect(page).not.toHaveURL(new RegExp(`/learning/${student.id}$`))
await expect(page.getByText(student.name, { exact: true })).toHaveCount(0)
})
```

Import the module-level `request` factory from `@playwright/test` and ensure each project mutates a distinct relationship.

- [ ] **Step 4: Add focus-driven permission/assignment revocation, keyboard and breakpoint tests**

Create helpers in the spec that authenticate an isolated admin request context and genuinely refocus the application page:

```ts
async function createAdminRequest(): Promise<{
  admin: APIRequestContext
  csrfToken: string
}> {
  const admin = await request.newContext({
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:18080",
  })
  const response = await admin.post("/api/v1/auth/login", {
    data: { phone: "19990000001", password: "E2E-admin-password-2026" },
  })
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  return { admin, csrfToken: body.csrf_token }
}

async function refocus(page: Page): Promise<void> {
  const other = await page.context().newPage()
  await other.bringToFront()
  await page.bringToFront()
  await other.close()
}
```

Add these two state-changing tests. Use `try/finally`: remove the payment override after the assertion, and recreate the ended standard-class assignment with the same coach, `primary` type, `valid_from` one second after the end request, and `valid_until="2031-01-01T00:00:00+08:00"`. Before ending an assignment, list `/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments` and select the active item whose `coach_person_id` is `e1000000-0000-4000-8000-000000000006`; this keeps the test repeatable after a prior restore generated a new assignment UUID.

```ts
test("window focus removes an ended coach assignment", async ({ page }) => {
  await loginAsCoach(page)
  await page.getByRole("link", { name: "查看下一期名单" }).click()
  await expect(page.getByText("E2E英雄联盟普通班", { exact: true })).toBeVisible()
  const { admin, csrfToken } = await createAdminRequest()
  let endedAt = new Date()
  let assignmentEnded = false
  try {
    const listed = await admin.get(
      "/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments",
    )
    const body = (await listed.json()) as {
      items: Array<{ id: string; coach_person_id: string; ended_at: string | null }>
    }
    const active = body.items.find(
      (item) =>
        item.coach_person_id === "e1000000-0000-4000-8000-000000000006" &&
        item.ended_at === null,
    )
    expect(active).toBeTruthy()
    endedAt = new Date()
    const ended = await admin.post(
      `/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments/${active!.id}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: "门户聚焦撤权验收" },
      },
    )
    expect(ended.status()).toBe(200)
    assignmentEnded = true
    await refocus(page)
    await expect(page.getByText("E2E英雄联盟普通班", { exact: true })).toHaveCount(0)
    await expect(page.getByText("E2E英雄联盟菁英班", { exact: true })).toBeVisible()
  } finally {
    if (assignmentEnded) {
      const restored = await admin.post(
        "/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments",
        {
          headers: { "X-CSRF-Token": csrfToken },
          data: {
            coach_person_id: "e1000000-0000-4000-8000-000000000006",
            assignment_type: "primary",
            valid_from: new Date(endedAt.getTime() + 1_000).toISOString(),
            valid_until: "2031-01-01T00:00:00+08:00",
          },
        },
      )
      expect(restored.status()).toBe(201)
    }
    await admin.dispose()
  }
})


test("window focus clears amounts after payments.read self is denied", async ({ page }) => {
  await loginAsGuardian(page)
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥1,000.00", { exact: true })).toBeVisible()
  const { admin, csrfToken } = await createAdminRequest()
  let overrideId: string | undefined
  try {
    const denied = await admin.post(
      "/api/v1/accounts/e1100000-0000-4000-8000-000000000003/permission-overrides",
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: {
          permission_id: "20000000-0000-4000-8000-000000000018",
          effect: "deny",
          scope_kind: "self",
          scope_id: null,
        },
      },
    )
    expect(denied.status()).toBe(201)
    overrideId = ((await denied.json()) as { id: string }).id
    await refocus(page)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByText("¥1,000.00", { exact: true })).toHaveCount(0)
    await expect(page.getByRole("link", { name: "查看本人付款" })).toHaveCount(0)
  } finally {
    if (overrideId) {
      const restored = await admin.delete(
        `/api/v1/accounts/e1100000-0000-4000-8000-000000000003/permission-overrides/${overrideId}`,
        { headers: { "X-CSRF-Token": csrfToken } },
      )
      expect(restored.status()).toBe(204)
    }
    await admin.dispose()
  }
})
```

```ts
test("keyboard opens roster, changes time group and enters learning", async ({ page }) => {
  await loginAsCoach(page)
  const coachLink = page.getByRole("link", { name: "查看下一期名单" })
  await coachLink.focus()
  await expect(coachLink).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(page).toHaveURL(/\/coach\/next-cohorts$/)
  const later = page.getByRole("button", { name: "2030-08-15 09:00" })
  await later.focus()
  await expect(later).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(later).toHaveAttribute("aria-pressed", "true")
  await expect(page.getByText("E2E自助学员", { exact: true })).toBeVisible()
  const learningLink = page.getByRole("link", { name: "学习信息" })
  await learningLink.focus()
  await expect(learningLink).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(page).toHaveURL(/\/learning\//)
})


test("shell changes cleanly at 767, 768, 1023 and 1024", async ({ page }) => {
  const original = page.viewportSize()
  if (!original) throw new Error("Project viewport is required")
  await loginAsCoach(page)
  try {
    await page.setViewportSize({ width: 767, height: 900 })
    await expect(page.locator(".shellMobileNavigation")).toBeVisible()
    await expect(page.locator(".shellRail")).toBeHidden()

    for (const width of [768, 1023]) {
      await page.setViewportSize({ width, height: 900 })
      await expect(page.locator(".shellMobileNavigation")).toBeHidden()
      await expect(page.locator(".shellRail")).toBeVisible()
      const topRail = await page.locator(".shellRail").boundingBox()
      if (!topRail) throw new Error("Top rail box is required")
      expect(Math.round(topRail.width)).toBe(width)
      expect(Math.round(topRail.height)).toBe(76)
    }

    await page.setViewportSize({ width: 1024, height: 768 })
    const sideRail = await page.locator(".shellRail").boundingBox()
    if (!sideRail) throw new Error("Side rail box is required")
    expect(sideRail.width).toBeLessThan(400)
    expect(sideRail.height).toBeGreaterThan(500)
    await expectNoHorizontalOverflow(page)
  } finally {
    await page.setViewportSize(original)
  }
})
```

- [ ] **Step 5: Run the portal E2E at one viewport first**

With local API/Web and seeded `erp_e2e` running, run:

```bash
E2E_BASE_URL='http://127.0.0.1:5175' npm --prefix apps/web run test:e2e -- \
  tests/coach-family-portal.spec.ts --project=desktop-1440
```

Expected: all portal journeys PASS at 1440.

- [ ] **Step 6: Rebuild fixtures and run all four portal viewport projects**

```bash
DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e' \
APP_ENV=test SESSION_COOKIE_SECURE=false \
FIELD_ENCRYPTION_MASTER_KEY='AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=' \
FIELD_LOOKUP_KEY='Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA=' \
SESSION_TOKEN_PEPPER='EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8=' \
PYTHONPATH=apps/api .venv/bin/python -m app.cli.seed_e2e

E2E_BASE_URL='http://127.0.0.1:5175' npm --prefix apps/web run test:e2e -- \
  tests/coach-family-portal.spec.ts
```

Expected: the same portal journeys PASS at 1440, 1024, 768 and 360 without overflow or clipped tracked elements.

- [ ] **Step 7: Commit portal E2E**

```bash
git add apps/web/tests/coach-family-portal.spec.ts
git commit -m "test: verify coach and family portal journeys"
```

### Task 13: Update operations documentation and run the complete local gate

**Files:**

- Modify: `README.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Modify: `docs/superpowers/specs/2026-07-29-coach-family-portal-design.md`

- [ ] **Step 1: Write failing documentation assertions**

Require README to state the now-available portal features and still-delayed boundaries:

```python
@pytest.mark.parametrize(
    "required_text",
    [
        "教练下一期学员名单",
        "家庭教学与多学员切换",
        "学员本人教学信息",
        "本人付款与退款",
        "学习评价将在下一阶段开放",
        "Docker/NAS 打包仍在最终阶段",
    ],
)
def test_readme_documents_portal_operations(required_text: str) -> None:
    assert required_text in README.read_text(encoding="utf-8")
```

Remove assertions that still classify coach/family portals as unavailable; retain evaluation, screenshot, housing, payment adapter, video/GPU and Docker/NAS as delayed.

- [ ] **Step 2: Run documentation tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_operations_docs.py -q
```

Expected: FAIL until README is updated.

- [ ] **Step 3: Update README and final spec status**

Add this operations section to README (keep the surrounding existing admissions and security sections intact):

```markdown
## 教练与家庭门户

登录后的统一工作台按账号实际能力显示任务卡片。教练可进入“教练下一期学员名单”，默认打开本人获权的最近开营时间组，并可切换更晚营期；名单不提供身份证、手机号、邮箱、学费或付款信息。监护人可在“家庭教学与多学员切换”中查看有效关系内的未成年和成年学员，成年不会自动解除既有关系。拥有启用账号的学员可查看“学员本人教学信息”；未成年账号仍受监护人就绪规则约束。

“本人付款与退款”只按当前付款账号显示记录。绑定同一学员的另一名监护人付款、无付款账号的手工记录、内部备注和导入原文均不可见。关系结束后付款仍属于原付款人，但关联学员显示为“历史付款”。学习评价将在下一阶段开放；当前卡片不可操作，也不会请求评价接口。

本地 E2E 另增加以下固定测试账号，仅存在于 `erp_e2e`：

- 监护人：`19990000005 / E2E-guardian-password-2026`
- 成年学员：`19990000016 / E2E-student-password-2026`

截图/OCR/自动评价、游戏 ID、住宿房态、实名导出、支付平台适配和录像/GPU 仍未开放。Docker/NAS 打包仍在最终阶段，本模块只完成本地门禁，不把容器、备份恢复或 NAS 迁移标记为已验证。
```

Set the design status to `已实现并通过本地门禁，待后续 Docker/NAS 统一复核` only after every local command below succeeds.

- [ ] **Step 4: Run the complete backend gate**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy --config-file apps/api/pyproject.toml apps/api/app
```

Expected: all backend tests PASS; Ruff and mypy exit 0.

- [ ] **Step 5: Run the complete Web gate**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all Web tests PASS; build and ESLint exit 0.

- [ ] **Step 6: Run the complete existing and portal E2E locally**

First migrate and rebuild the isolated data from the worktree root:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
export DATABASE_URL="$TEST_DATABASE_URL"
export APP_ENV=test
export SESSION_COOKIE_SECURE=false
export FIELD_ENCRYPTION_MASTER_KEY='AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
export FIELD_LOOKUP_KEY='Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA='
export SESSION_TOKEN_PEPPER='EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8='

PYTHONPATH=apps/api .venv/bin/alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api .venv/bin/python -m app.cli.run_teaching_scheduler --once
PYTHONPATH=apps/api .venv/bin/python -m app.cli.seed_e2e
```

Keep terminal 1 running:

```bash
DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e' \
APP_ENV=test SESSION_COOKIE_SECURE=false \
FIELD_ENCRYPTION_MASTER_KEY='AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=' \
FIELD_LOOKUP_KEY='Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA=' \
SESSION_TOKEN_PEPPER='EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8=' \
.venv/bin/uvicorn app.main:app --app-dir apps/api \
  --host 127.0.0.1 --port 8000
```

Keep terminal 2 running:

```bash
npm --prefix apps/web run dev -- \
  --host 127.0.0.1 --port 5175 --strictPort
```

Run the complete browser gate in terminal 3:

```bash
E2E_BASE_URL='http://127.0.0.1:5175' npm --prefix apps/web run test:e2e
```

Expected: platform, teaching, admissions and portal E2E all PASS at 1440, 1024, 768 and 360. Do not run `scripts/check.sh` while the Docker daemon is unavailable, and do not report Compose, image startup, backup/restore or NAS gates as passed.

- [ ] **Step 7: Verify the production source package list without building containers**

Run:

```bash
rg -n 'COPY app/portal ./app/portal' apps/api/Dockerfile
for script in \
  scripts/db-schema-manifest.sh \
  scripts/backup-db.sh \
  scripts/restore-db.sh
do
  bash -n "$script"
done
git diff --check
```

Expected: the portal copy line is present, shell syntax passes and Git reports no whitespace errors.

- [ ] **Step 8: Commit documentation and local verification status**

```bash
git add README.md apps/api/tests/cli/test_operations_docs.py \
  docs/superpowers/specs/2026-07-29-coach-family-portal-design.md
git commit -m "docs: document coach and family portal operations"
```

After the commit, `git status --short` must be empty. Record the exact passing test counts in the implementation handoff; explicitly list Docker/NAS as deferred rather than passed.
