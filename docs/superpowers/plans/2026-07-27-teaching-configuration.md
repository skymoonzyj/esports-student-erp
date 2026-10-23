# Teaching Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付阶段 1B 的第一个独立模块，使管理员能够维护电竞项目、职业队伍、营期、普通班/菁英班和主/助教分配，并安全执行手工或自动营期状态流转。

**Architecture:** 新建独立的 `app.teaching` 业务包，以 PostgreSQL 约束和服务事务共同保护项目、队伍、营期、班级和教练分配；纯状态机负责合法营期流转，独立 scheduler 进程负责到期自动开营和结营。React 新增“招生教学”工作区，桌面使用紧凑表格和页签，手机使用稳定的四项底部导航、卡片列表和全屏表单；现有 RBAC 的 `assigned_class` 范围在真实班级和当前教练分配上启用。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、lucide-react、Docker Compose

---

## Scope guardrails

- 本计划只实现已批准规格的“教学配置”模块：项目、职业队伍、营期、班级、教练分配、状态调度及其管理界面。
- 本计划不创建付款、退款、待分配、报名、退营退费、教练学员名单、家庭教学页面、评价、截图、游戏 ID 或住宿数据表。
- 自动结营在本模块只更新营期和状态事件；招生模块引入 `enrollments` 时再把完成报名的联动加入同一状态服务。
- 所有业务时间必须是带时区时间，并统一按 `Asia/Shanghai` 展示和计算默认日期边界。
- 新增写操作必须同时具备服务端对象权限、事务约束、稳定错误码和审计记录。
- 新页面必须同时通过 360、768、1024 和 1440 四档验收；不得用横向滚动承载核心操作。

## File map

**Create:**

- `apps/api/migrations/versions/0004_teaching_configuration.py` — 教学配置表、约束、初始项目和权限种子。
- `apps/api/app/teaching/__init__.py` — 教学包标记。
- `apps/api/app/teaching/models.py` — 项目、队伍、营期、状态事件、班级和教练分配 ORM 模型。
- `apps/api/app/teaching/schemas.py` — 输入、响应、分页和状态类型。
- `apps/api/app/teaching/errors.py` — 稳定教学业务错误。
- `apps/api/app/teaching/access.py` — 全局或可见班级集合的教学读取范围值对象。
- `apps/api/app/teaching/state.py` — 纯营期状态机和自动到期判定。
- `apps/api/app/teaching/catalog_service.py` — 项目与职业队伍事务服务。
- `apps/api/app/teaching/cohort_service.py` — 营期、班级和状态事务服务。
- `apps/api/app/teaching/coach_eligibility.py` — 教练人员类型、账号和角色的共享资格查询。
- `apps/api/app/teaching/coach_service.py` — 教练资格、分配和结束服务。
- `apps/api/app/teaching/scheduler.py` — advisory lock、到期营期扫描和幂等流转。
- `apps/api/app/teaching/router.py` — 教学配置 API。
- `apps/api/app/teaching/seed.py` — 确定性初始项目定义。
- `apps/api/app/cli/run_teaching_scheduler.py` — scheduler 的 `--once` 和持续运行入口。
- `apps/api/tests/unit/teaching/test_state.py` — 状态机和自动时间矩阵。
- `apps/api/tests/services/test_teaching_catalog.py` — 项目、队伍服务行为。
- `apps/api/tests/services/test_cohorts.py` — 营期、班级和状态服务行为。
- `apps/api/tests/services/test_coach_assignments.py` — 教练资格、唯一性、历史和 RBAC 范围。
- `apps/api/tests/services/test_teaching_scheduler.py` — 自动流转、幂等和锁测试。
- `apps/api/tests/api/test_teaching.py` — 教学 API、权限、审计和错误契约。
- `apps/api/tests/cli/test_teaching_scheduler.py` — `--once` 安全入口。
- `scripts/db-schema-manifest.sh` — 当前迁移 head 和恢复所需显式表清单。
- `apps/web/src/teaching/api.ts` — 教学 API 类型与请求函数。
- `apps/web/src/teaching/TeachingCatalogPage.tsx` — 项目与职业队伍页面。
- `apps/web/src/teaching/CohortsPage.tsx` — 营期列表、筛选和创建入口。
- `apps/web/src/teaching/CohortDetailPage.tsx` — 营期状态、班级和教练分配页签。
- `apps/web/src/teaching/teaching.css` — 教学模块响应式布局。
- `apps/web/src/teaching/TeachingCatalogPage.test.tsx` — 项目/队伍页面测试。
- `apps/web/src/teaching/CohortsPage.test.tsx` — 营期页面测试。
- `apps/web/src/teaching/CohortDetailPage.test.tsx` — 状态、班级和教练页面测试。
- `apps/web/tests/teaching-configuration.spec.ts` — 四档响应式管理员与范围教练旅程。

**Modify:**

- `apps/api/migrations/env.py`、`apps/api/app/api/router.py` — 注册模型和路由。
- `apps/api/app/rbac/seed.py`、`policy.py`、`service.py`、`router.py`、`schemas.py` — 教学权限和真实班级范围。
- `apps/api/app/audit/service.py` — 放行教学 UUID、状态和触发类型元数据。
- `apps/api/app/core/config.py` — scheduler 间隔配置。
- `apps/api/app/cli/seed_e2e.py`、`apps/api/tests/cli/test_seed_e2e.py` — 教练账号种子。
- `apps/api/tests/api/conftest.py`、`test_permissions.py`、`test_audit.py` — 教练、班级范围和审计测试。
- `apps/api/tests/db/test_migrations.py`、`test_rbac_seed.py` — `0004` 表、约束、升级和种子测试。
- `apps/web/package.json`、`package-lock.json` — 增加 `lucide-react`。
- `apps/web/src/app/router.tsx`、`router.test.tsx` — 教学路由和 scoped permission guard。
- `apps/web/src/layout/AppShell.tsx`、`AppShell.css`、`AppShell.test.tsx` — 教学导航、Lucide 图标和手机“更多”菜单。
- `apps/web/src/settings/api.ts`、`PermissionsPage.tsx`、`PermissionsPage.test.tsx` — 班级范围选择。
- `apps/web/playwright.config.ts`、`apps/web/tests/helpers.ts` — 四档视口和 E2E 辅助函数。
- `compose.yaml`、`compose.prod.yaml`、`compose.e2e.yaml` — scheduler 服务。
- `scripts/restore-db.sh`、`scripts/check.sh` — schema manifest、调度和总验证。
- `README.md` — 可用能力、scheduler 和阶段边界。

## Test environment

优先使用隔离 PostgreSQL 测试库：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://skymoonzyj@127.0.0.1:55432/erp_test'
```

若 Docker daemon 已启动，也可以使用独立 E2E 数据库：

```bash
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml up -d --wait db
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

数据库名必须以 `_test` 结尾或严格等于 `erp_e2e`。Docker daemon 不可用时可以执行本地 PostgreSQL、Web 和静态检查，但不得声称 Compose 容器链已通过。

### Task 1: Add the teaching schema, deterministic seeds, and restore manifest

**Files:**

- Create: `apps/api/migrations/versions/0004_teaching_configuration.py`
- Create: `apps/api/app/teaching/__init__.py`
- Create: `apps/api/app/teaching/models.py`
- Create: `apps/api/app/teaching/seed.py`
- Create: `scripts/db-schema-manifest.sh`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/restore-db.sh`

- [ ] **Step 1: Write failing migration and seed assertions**

Extend `EXPECTED_TABLES` with:

```python
TEACHING_TABLES = {
    "game_projects",
    "professional_teams",
    "cohorts",
    "cohort_status_events",
    "teaching_classes",
    "coach_assignments",
}
EXPECTED_TABLES |= TEACHING_TABLES
```

Add tests that assert all six tables, status/type checks, composite unique keys and assignment exclusions:

```python
def test_teaching_schema_has_cross_resource_and_history_constraints(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    class_checks = {
        item["name"] for item in inspector.get_check_constraints("teaching_classes")
    }
    cohort_checks = {
        item["name"] for item in inspector.get_check_constraints("cohorts")
    }
    event_checks = {
        item["name"] for item in inspector.get_check_constraints("cohort_status_events")
    }
    assignment_checks = {
        item["name"] for item in inspector.get_check_constraints("coach_assignments")
    }
    class_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("teaching_classes")
    }
    team_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("professional_teams")
    }
    class_foreign_keys = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
        )
        for item in inspector.get_foreign_keys("teaching_classes")
    }
    with postgres_engine.connect() as connection:
        exclusions = set(
            connection.scalars(text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'coach_assignments'::regclass AND contype = 'x'"
            ))
        )
    assert {"ck_teaching_classes_type_team", "ck_teaching_classes_capacity"} <= class_checks
    assert "ck_cohorts_status" in cohort_checks
    assert {
        "ck_cohort_status_events_trigger",
        "ck_cohort_status_events_actor",
    } <= event_checks
    assert {
        "ck_coach_assignments_type",
        "ck_coach_assignments_valid_until",
        "ck_coach_assignments_ended_at",
        "ck_coach_assignments_end_fields",
    } <= assignment_checks
    assert ("cohort_id", "name") in class_uniques
    assert ("id", "cohort_id") in class_uniques
    assert ("project_id", "name") in team_uniques
    assert ("id", "project_id") in team_uniques
    assert (
        ("professional_team_id", "project_id"),
        "professional_teams",
        ("id", "project_id"),
    ) in class_foreign_keys
    assert {
        "excl_coach_assignments_one_primary",
        "excl_coach_assignments_no_duplicate_coach",
    } <= exclusions
```

Add direct database tests proving a duplicate `(project_id,name)` team and a class referencing a team from another project are rejected. Add a seed test asserting the four initial projects and permission keys `teaching.read`, `projects.manage`, `professional_teams.manage`, `cohorts.manage`, `classes.manage`, `coach_assignments.manage`. Assert all six admin/global grants, customer-service/global and coach/assigned-class `teaching.read`, plus the deliberate absence of guardian/student grants in this module. Add downgrade tests proving an untouched seed-only database returns to `0003`, while a non-seed teaching row, modified initial project or account override on a new permission refuses downgrade without losing data.

- [ ] **Step 2: Run focused migration tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py -q
```

Expected: FAIL because revision `0004`, teaching tables and teaching seeds do not exist.

- [ ] **Step 3: Define deterministic seeds and ORM models**

Create `teaching/seed.py`:

```python
from dataclasses import dataclass
from uuid import UUID

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
```

Add `seed_initial_projects(db: Session) -> None` using PostgreSQL insert-on-conflict-do-nothing by frozen UUID/name, so E2E reset and explicit seed paths can restore missing rows idempotently without importing an Alembic migration or overwriting administrator edits to existing projects.

Add permission UUIDs ending `000010` through `000015`. Grant all six to admin, grant `teaching.read` globally to customer service and with `assigned_class` scope to coach. Do not yet grant `teaching.read` to guardian or student: the third phase 1B module will add the approved `family` / `self` defaults in the same migration that creates usable family and student teaching endpoints, preventing a visible navigation entry that can only fail in this independently deployable module.

In `models.py`, define `GameProject`, `ProfessionalTeam`, `Cohort`, `CohortStatusEvent`, `TeachingClass`, `CoachAssignment`. Only seeded projects may have a null creator; manual create paths require an actor, while updater/event actor fields remain nullable where scheduler work is valid. Use timezone-aware datetimes and these checks:

```text
sort_order >= 0
ends_at > starts_at
default_check_out_at >= default_check_in_at
capacity > 0
default_tuition_cents >= 0
class_type IN ('standard','elite')
(standard AND team IS NULL) OR (elite AND team IS NOT NULL)
status IN ('draft','open','upcoming','active','completed','archived')
transition trigger IN ('manual','scheduler','request_reconciliation')
(manual AND actor_user_id IS NOT NULL) OR
  (automatic trigger AND actor_user_id IS NULL)
assignment_type IN ('primary','assistant')
valid_until IS NULL OR valid_until > valid_from
ended_at IS NULL OR ended_at > valid_from
(ended_at IS NULL AND ended_by_user_id IS NULL AND end_reason IS NULL) OR
  (ended_at IS NOT NULL AND ended_by_user_id IS NOT NULL AND btrim(end_reason) <> '')
```

Each status event stores `old_status`, `new_status`, `trigger`, nullable `actor_user_id`, the safe request/scheduler identifier in `request_id`, and `occurred_at`. Use unique `(project_id,name)`, `(id,project_id)`, `(cohort_id,name)`, `(id,cohort_id)` and composite FK `(professional_team_id,project_id)`.

- [ ] **Step 4: Create revision `0004_teaching_configuration`**

Mirror every ORM constraint. Add the two exclusion constraints:

```sql
ALTER TABLE coach_assignments ADD CONSTRAINT excl_coach_assignments_one_primary
EXCLUDE USING gist (
  class_id WITH =,
  tstzrange(
    valid_from,
    COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, 'infinity'::timestamptz),
    '[)'
  ) WITH &&
) WHERE (assignment_type = 'primary');

ALTER TABLE coach_assignments ADD CONSTRAINT excl_coach_assignments_no_duplicate_coach
EXCLUDE USING gist (
  class_id WITH =,
  coach_person_id WITH =,
  tstzrange(
    valid_from,
    COALESCE(LEAST(valid_until, ended_at), valid_until, ended_at, 'infinity'::timestamptz),
    '[)'
  ) WITH &&
);
```

The exclusion ranges keep ended history authoritative, so a retroactive assignment cannot overlap a previously ended interval. Seed frozen copies of project and permission UUIDs in the migration. Downgrade must refuse when non-seed teaching rows exist, any initial project differs from its frozen name/order/active values, or account overrides reference the six new permissions; only then remove grants, permissions, projects and tables in reverse dependency order. Import teaching models from `migrations/env.py`; reuse the `btree_gist` extension installed by revision `0003`.

- [ ] **Step 5: Replace restore hard-coding with a committed manifest**

Create executable `scripts/db-schema-manifest.sh`:

```bash
EXPECTED_ALEMBIC_HEAD=0004_teaching_configuration
REQUIRED_TABLES=(
  people student_profiles staff_profiles visitor_profiles guardian_relationships
  user_accounts auth_sessions auth_rate_limits roles permissions role_permissions
  user_roles user_permission_overrides audit_events game_projects professional_teams
  cohorts cohort_status_events teaching_classes coach_assignments
)
ROW_COUNT_TABLES=(
  people user_accounts roles permissions role_permissions user_roles
  user_permission_overrides audit_events game_projects professional_teams cohorts
  cohort_status_events teaching_classes coach_assignments
)
```

Make `restore-db.sh` source the manifest relative to the script directory, compare against `EXPECTED_ALEMBIC_HEAD`, verify each required table individually and compare source/target rows only for manifest names.

```bash
chmod 0755 scripts/db-schema-manifest.sh
```

- [ ] **Step 6: Run migration, seed and shell checks**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py -q
bash -n scripts/db-schema-manifest.sh
bash -n scripts/restore-db.sh
.venv/bin/ruff check apps/api/app/teaching apps/api/migrations/versions/0004_teaching_configuration.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py
.venv/bin/mypy apps/api/app/teaching
```

Expected: all pass, including empty upgrade and `0003 -> 0004` upgrade.

- [ ] **Step 7: Commit schema foundation**

```bash
git add apps/api/migrations apps/api/app/teaching apps/api/app/rbac/seed.py \
  apps/api/tests/db scripts/db-schema-manifest.sh scripts/restore-db.sh
git commit -m "feat: add teaching configuration schema"
```

### Task 2: Implement project and professional-team services and API

**Files:**

- Create: `apps/api/app/teaching/errors.py`
- Create: `apps/api/app/teaching/schemas.py`
- Create: `apps/api/app/teaching/catalog_service.py`
- Create: `apps/api/app/teaching/router.py`
- Create: `apps/api/tests/services/test_teaching_catalog.py`
- Create: `apps/api/tests/api/test_teaching.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing catalog service tests**

Create the following exact `TeachingCatalogService` cases:

| Test | Arrange, action, and assertion |
| --- | --- |
| `test_project_names_are_unique_after_trimming` | Create `"  英雄联盟青训  "`, then create `"英雄联盟青训"`; assert `duplicate_project` and exactly one normalized row. |
| `test_inactive_project_rejects_new_team` | Deactivate a project, attempt to create its team; assert `inactive_project` and no team row. |
| `test_team_name_is_unique_inside_project` | Create the same trimmed team name twice in project A and once in project B; assert `duplicate_professional_team` only for project A. |
| `test_team_project_pair_is_immutable` | Create a team under project A, send project B through `ProfessionalTeamUpdate`; assert `team_project_immutable` and the original FK remains. |
| `test_deactivation_preserves_project_and_team_history` | Deactivate a team and its project; assert both rows remain queryable with `include_inactive=True`. |
| `test_catalog_lists_active_first_then_sort_order_and_name` | Create mixed active states, sort values and names; assert exact active/sort/name/UUID ordering and correct pagination total. |

For every rejected write, assert the exact error `code`, roll back the failed unit of work, and query the database to prove that no partial row or actor timestamp changed. For both project and team lists, assert trimmed exact-name filtering is applied before count/order/pagination.

- [ ] **Step 2: Run the service file and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_teaching_catalog.py -q
```

Expected: collection FAIL because schemas, errors and service are absent.

- [ ] **Step 3: Define catalog schemas and errors**

Use one strict, whitespace-normalizing base model so unknown fields do not disappear silently:

```python
class TeachingInputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

class ProjectCreate(TeachingInputModel):
    name: str = Field(min_length=2, max_length=80)
    sort_order: int = Field(default=0, ge=0, le=100_000)

class ProjectUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None

class ProfessionalTeamCreate(TeachingInputModel):
    project_id: UUID
    name: str = Field(min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=500)

class ProfessionalTeamUpdate(TeachingInputModel):
    project_id: UUID | None = None
    name: str | None = Field(default=None, min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
```

Define exact errors `project_not_found`, `professional_team_not_found`, `duplicate_project`, `duplicate_professional_team`, `inactive_project`, `team_project_immutable`.

- [ ] **Step 4: Implement catalog transactions**

Expose:

```python
list_projects(name: str | None, include_inactive: bool, page: int, page_size: int) -> tuple[list[GameProject], int]
create_project(input: ProjectCreate, actor_user_id: UUID) -> GameProject
update_project(project_id: UUID, input: ProjectUpdate, actor_user_id: UUID) -> GameProject
list_teams(project_id: UUID | None, name: str | None, include_inactive: bool, page: int, page_size: int) -> tuple[list[ProfessionalTeam], int]
create_team(input: ProfessionalTeamCreate, actor_user_id: UUID) -> ProfessionalTeam
update_team(team_id: UUID, input: ProfessionalTeamUpdate, actor_user_id: UUID) -> ProfessionalTeam
```

Normalize with the schema configuration only; do not apply case folding or fuzzy matching. Catch named unique constraints and translate them. If `ProfessionalTeamUpdate.project_id` differs from the stored value, raise `team_project_immutable`; a matching value is accepted as an idempotent request, but the FK is never patched.

- [ ] **Step 5: Write and run RED API permission/audit tests**

Test admin CRUD, customer-service read-only behavior, explicit deny precedence, inactive-project errors and exactly one audit event per successful write.

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py -q
```

Expected: FAIL with route-not-found and missing teaching audit keys.

- [ ] **Step 6: Add catalog routes and audit allowlist**

Register the teaching router and implement:

```text
GET/POST /projects
PATCH /projects/{project_id}
GET/POST /professional-teams
PATCH /professional-teams/{team_id}
```

Catalog reads require `teaching.read` against the global token; writes require the matching manage permission. Write and audit share one outer transaction. Allowlist only `project_id` and `professional_team_id`; never place names in audit metadata.

- [ ] **Step 7: Run focused backend gates**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_teaching_catalog.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/teaching apps/api/tests/services/test_teaching_catalog.py \
  apps/api/tests/api/test_teaching.py apps/api/app/audit/service.py
.venv/bin/mypy apps/api/app/teaching
```

Expected: all pass.

- [ ] **Step 8: Commit catalog management**

```bash
git add apps/api/app/teaching apps/api/app/api/router.py apps/api/app/audit/service.py \
  apps/api/tests/services/test_teaching_catalog.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py
git commit -m "feat: manage game projects and professional teams"
```

### Task 3: Implement cohort state, lifecycle API, and status history

**Files:**

- Create: `apps/api/app/teaching/state.py`
- Create: `apps/api/app/teaching/access.py`
- Create: `apps/api/app/teaching/cohort_service.py`
- Create: `apps/api/app/teaching/coach_eligibility.py`
- Create: `apps/api/tests/unit/teaching/test_state.py`
- Create: `apps/api/tests/services/test_cohorts.py`
- Modify: `apps/api/app/teaching/schemas.py`
- Modify: `apps/api/app/teaching/errors.py`
- Modify: `apps/api/app/teaching/router.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/api/test_teaching.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write the complete RED state matrix**

Parameterize all six statuses. The only allowed pairs are:

```python
ALLOWED = {
    ("draft", "open"), ("open", "upcoming"), ("upcoming", "open"),
    ("open", "active"), ("upcoming", "active"),
    ("active", "completed"), ("completed", "archived"),
}
```

All other pairs raise `InvalidCohortTransition`. Automatic tests cover due start for `open|upcoming`, due end for `active`, disabled flags, naive datetimes, one-adjacent-transition-per-reconciliation, and derived `auto_start_transition_blocked` / `auto_complete_transition_blocked` administrator warnings when dates are due but the current status is incompatible.

- [ ] **Step 2: Run unit tests and verify RED**

```bash
.venv/bin/python -m pytest apps/api/tests/unit/teaching/test_state.py -q
```

Expected: FAIL because `state.py` is absent.

- [ ] **Step 3: Implement pure state functions**

```python
CohortStatus = Literal["draft", "open", "upcoming", "active", "completed", "archived"]
TransitionTrigger = Literal["manual", "scheduler", "request_reconciliation"]
AutomationIssue = Literal[
    "auto_start_transition_blocked",
    "auto_complete_transition_blocked",
]

ALLOWED_TRANSITIONS: frozenset[tuple[CohortStatus, CohortStatus]] = frozenset({
    ("draft", "open"),
    ("open", "upcoming"),
    ("upcoming", "open"),
    ("open", "active"),
    ("upcoming", "active"),
    ("active", "completed"),
    ("completed", "archived"),
})

class InvalidCohortTransition(ValueError):
    def __init__(self, *, current: CohortStatus, target: CohortStatus) -> None:
        super().__init__(f"invalid cohort transition: {current} -> {target}")
        self.current = current
        self.target = target

def _require_aware(*values: datetime) -> None:
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("cohort datetimes must include an offset")

def require_transition(current: CohortStatus, target: CohortStatus) -> None:
    if (current, target) not in ALLOWED_TRANSITIONS:
        raise InvalidCohortTransition(current=current, target=target)

def due_transition(
    *, status: CohortStatus, starts_at: datetime, ends_at: datetime,
    auto_start_enabled: bool, auto_complete_enabled: bool, now: datetime,
) -> CohortStatus | None:
    _require_aware(starts_at, ends_at, now)
    if auto_start_enabled and status in {"open", "upcoming"} and now >= starts_at:
        return "active"
    if auto_complete_enabled and status == "active" and now >= ends_at:
        return "completed"
    return None

def automation_issues(
    *, status: CohortStatus, starts_at: datetime, ends_at: datetime,
    auto_start_enabled: bool, auto_complete_enabled: bool, now: datetime,
) -> list[AutomationIssue]:
    _require_aware(starts_at, ends_at, now)
    issues: list[AutomationIssue] = []
    if auto_start_enabled and status == "draft" and now >= starts_at:
        issues.append("auto_start_transition_blocked")
    if (
        auto_complete_enabled
        and status in {"draft", "open", "upcoming"}
        and now >= ends_at
    ):
        issues.append("auto_complete_transition_blocked")
    return issues
```

The warning function returns the start warning when auto-start is due from `draft`, and the completion warning when auto-complete is due from `draft|open|upcoming`; resolved, disabled, active-before-end, completed and archived cohorts return no stale warning. Keep `state.py` free of SQLAlchemy and FastAPI.

- [ ] **Step 4: Write failing cohort service/API tests**

Cover timezone validation, exact preservation of the four cohort timestamps, default stay ordering, exact-name/status and half-open `starts_at` filters, stable pagination, illegal transitions, opening with zero enabled classes, missing or ineligible primary coach on every enabled class, status-event contents and trigger values, archived immutability, read permission, write permission and exactly one audit row per successful create/update/transition. Assert cohorts order by `starts_at DESC, name ASC, id ASC` and events by `occurred_at DESC, id DESC`. The update matrix allows all schedule fields in `draft|open|upcoming`, allows only `ends_at`, `default_check_out_at` and `auto_complete_enabled` while `active`, and rejects schedule changes after `completed`; non-schedule name correction remains allowed until `archived`. Add request-reconciliation tests proving a due target cohort advances before a get/update/class response, creates exactly one actor-null event and audit row, never skips two statuses in one request, and is idempotent on the second request. Seed 51 earlier due cohorts and prove direct access still reconciles the requested 52nd cohort instead of returning stale state.

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_cohorts.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: FAIL because cohort schemas, service methods and routes are absent.

- [ ] **Step 5: Implement schemas and service**

Create `CohortResponse` and `CohortStatusEventResponse`, plus these strict inputs with aware-datetime validators:

```python
class CohortCreate(TeachingInputModel):
    name: str = Field(min_length=2, max_length=120)
    starts_at: datetime
    ends_at: datetime
    default_check_in_at: datetime
    default_check_out_at: datetime
    auto_start_enabled: bool = False
    auto_complete_enabled: bool = False

class CohortUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    default_check_in_at: datetime | None = None
    default_check_out_at: datetime | None = None
    auto_start_enabled: bool | None = None
    auto_complete_enabled: bool | None = None

class CohortTransitionRequest(TeachingInputModel):
    target: CohortStatus
```

Create the read-scope value object in `access.py`:

```python
@dataclass(frozen=True)
class TeachingVisibility:
    global_access: bool
    class_ids: frozenset[UUID] = frozenset()
    excluded_class_ids: frozenset[UUID] = frozenset()

    @classmethod
    def global_scope(cls) -> TeachingVisibility:
        return cls(global_access=True)
```

Expose this service contract:

```text
create_cohort(input: CohortCreate, actor_user_id: UUID) -> Cohort
update_cohort(cohort_id: UUID, input: CohortUpdate, actor_user_id: UUID, occurred_at: datetime, request_id: str) -> Cohort
list_cohorts(name: str | None, status: CohortStatus | None, date_from: datetime | None, date_to: datetime | None, visibility: TeachingVisibility, page: int, page_size: int) -> tuple[list[Cohort], int]
get_cohort(cohort_id: UUID, visibility: TeachingVisibility) -> Cohort
transition(cohort_id: UUID, target: CohortStatus, trigger: TransitionTrigger, actor_user_id: UUID | None, occurred_at: datetime, request_id: str) -> Cohort
list_status_events(cohort_id: UUID, visibility: TeachingVisibility, page: int, page_size: int) -> tuple[list[CohortStatusEvent], int]
reconcile_due_cohort(cohort_id: UUID, now: datetime, trigger: TransitionTrigger, request_id: str) -> bool
reconcile_due_batch_for_request(now: datetime, request_id: str, limit: int = 50) -> int
```

Create `CoachEligibility` with one reusable SQLAlchemy predicate requiring `staff_type='coach'`, an active account and the coach role at query time. Use a row-locking query with `FOR UPDATE`. `draft -> open` succeeds when no class is enabled; otherwise every enabled class requires an eligible current primary coach. All schedule fields are editable in `draft|open|upcoming`; an `active` cohort may only adjust its future end, default checkout and auto-complete flag; `completed|archived` schedules are locked, and `archived` rejects every update. Apply `TeachingVisibility` as an SQL predicate before list counts, ordering, limits and offsets: global readers exclude `excluded_class_ids`, while scoped readers include only `class_ids`; a cohort is visible when it has at least one visible class, with zero-class cohorts visible only globally. `reconcile_due_cohort` locks the requested row directly and applies at most one adjacent transition. List endpoints may additionally call the bounded batch helper, but get/update/transition and class writes always reconcile their exact target first. `update_cohort` reconciles the locked row after applying any allowed schedule change, so enabling an already-due transition is handled in that response. If nothing is due, a read remains side-effect free. Responses derive `automation_issues` from `state.py` and `missing_primary_class_count` from the shared eligibility predicate used by the opening gate.

- [ ] **Step 6: Add cohort routes and stable errors**

```text
GET/POST /cohorts
PATCH /cohorts/{cohort_id}
GET /cohorts/{cohort_id}
GET /cohorts/{cohort_id}/status-events
POST /cohorts/{cohort_id}/transitions
```

Map errors to `invalid_cohort_transition`, `primary_coach_required` and `cohort_schedule_locked`. Reads require `teaching.read`; writes require `cohorts.manage`. Extend the audit allowlist with UUID key `cohort_id`, status keys `old_status|new_status` restricted to `CohortStatus`, and `transition_trigger` restricted to `manual|scheduler|request_reconciliation`; reject all other values or keys. Only the manual route may supply an authenticated actor, and clients cannot submit internal trigger values. Task 5 replaces global-only route checks with final cohort/class resource tokens and deny-precedence tests.

- [ ] **Step 7: Run state, service and API gates**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/teaching/test_state.py apps/api/tests/services/test_cohorts.py \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/teaching apps/api/tests/unit/teaching \
  apps/api/tests/services/test_cohorts.py apps/api/tests/api/test_teaching.py \
  apps/api/app/audit/service.py apps/api/tests/api/test_audit.py
.venv/bin/mypy apps/api/app/teaching
```

Expected: all pass.

- [ ] **Step 8: Commit cohort lifecycle**

```bash
git add apps/api/app/teaching apps/api/tests/unit/teaching \
  apps/api/tests/services/test_cohorts.py apps/api/tests/api/test_teaching.py \
  apps/api/app/audit/service.py apps/api/tests/api/test_audit.py
git commit -m "feat: manage cohort lifecycle"
```

### Task 4: Implement standard and elite class management

**Files:**

- Modify: `apps/api/app/teaching/schemas.py`
- Modify: `apps/api/app/teaching/errors.py`
- Modify: `apps/api/app/teaching/cohort_service.py`
- Modify: `apps/api/app/teaching/router.py`
- Modify: `apps/api/tests/services/test_cohorts.py`
- Modify: `apps/api/tests/api/test_teaching.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing class invariant tests**

Add this service/API matrix:

| Test | Required assertion |
| --- | --- |
| `test_standard_class_rejects_professional_team` | A standard class with any team fails with `standard_class_team_forbidden`. |
| `test_elite_class_requires_active_same_project_team` | Missing team fails with `elite_team_required`; inactive or cross-project team fails without a row, using `inactive_professional_team` or `project_team_mismatch`. |
| `test_class_capacity_must_be_positive` | Zero, negative and values above 10,000 fail schema validation. |
| `test_class_default_tuition_is_integer_cents_and_non_negative` | Negative or non-integer cents fail; zero and the upper bound persist exactly. |
| `test_class_name_is_unique_within_cohort` | A trimmed duplicate in one cohort fails with `duplicate_class`, while the same name in another cohort succeeds. |
| `test_inactive_project_rejects_class` | Creating a class under an inactive project fails with `inactive_project`. |
| `test_archived_cohort_rejects_class_changes` | Create, edit and deactivate operations all fail with `cohort_archived`; the row remains unchanged. |

Also assert trimmed exact-name filtering, active-first/name/UUID list ordering, exact pagination totals and exactly one audit event for each successful class create/update/deactivation. The audit action and class `resource_id` identify tuition changes; never place a tuition amount in audit metadata.

- [ ] **Step 2: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_cohorts.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: class tests FAIL because service methods and routes are absent.

- [ ] **Step 3: Add class schemas and service methods**

```python
class TeachingClassCreate(TeachingInputModel):
    cohort_id: UUID
    project_id: UUID
    name: str = Field(min_length=2, max_length=120)
    class_type: Literal["standard", "elite"]
    professional_team_id: UUID | None = None
    capacity: int = Field(ge=1, le=10_000)
    default_tuition_cents: int = Field(ge=0, le=1_000_000_000)

class TeachingClassUpdate(TeachingInputModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    class_type: Literal["standard", "elite"] | None = None
    professional_team_id: UUID | None = None
    capacity: int | None = Field(default=None, ge=1, le=10_000)
    default_tuition_cents: int | None = Field(default=None, ge=0, le=1_000_000_000)
    is_active: bool | None = None
```

Use `model_fields_set` so explicit `professional_team_id: null` clears a team. Never patch `cohort_id` or `project_id`. Expose:

```text
create_class(input: TeachingClassCreate, actor_user_id: UUID) -> TeachingClass
update_class(class_id: UUID, input: TeachingClassUpdate, actor_user_id: UUID) -> TeachingClass
get_class(class_id: UUID, visibility: TeachingVisibility) -> TeachingClass
list_classes(cohort_id: UUID | None, project_id: UUID | None, name: str | None, is_active: bool | None, visibility: TeachingVisibility, page: int, page_size: int) -> tuple[list[TeachingClass], int]
```

Validate parent resources inside the write transaction, reconcile the exact parent cohort before each write, apply visibility before list counts/pagination, and map named constraints to `duplicate_class`, `elite_team_required`, `standard_class_team_forbidden`, `inactive_professional_team` or `project_team_mismatch`. This module has no enrollment rows yet; the admissions module must add the active-enrollment guard before class deactivation can affect real registrations.

- [ ] **Step 4: Add class routes and permission checks**

```text
GET/POST /classes
GET/PATCH /classes/{class_id}
```

Reads require `teaching.read`; writes require `classes.manage`. Task 5 adds object-scoped coach reads after assignment tokens exist.

- [ ] **Step 5: Verify and commit class management**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_cohorts.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py apps/api/tests/db/test_migrations.py -q
.venv/bin/ruff check apps/api/app/teaching apps/api/tests/services/test_cohorts.py \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py
.venv/bin/mypy apps/api/app/teaching
git add apps/api/app/teaching apps/api/tests/services/test_cohorts.py \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py \
  apps/api/tests/db/test_migrations.py
git commit -m "feat: manage standard and elite classes"
```

Expected: all checks pass before commit.

### Task 5: Implement coach assignment history and real assigned-class RBAC

**Files:**

- Create: `apps/api/app/teaching/coach_service.py`
- Create: `apps/api/tests/services/test_coach_assignments.py`
- Modify: `apps/api/app/teaching/access.py`
- Modify: `apps/api/app/teaching/schemas.py`
- Modify: `apps/api/app/teaching/errors.py`
- Modify: `apps/api/app/teaching/cohort_service.py`
- Modify: `apps/api/app/teaching/router.py`
- Modify: `apps/api/app/rbac/policy.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/rbac/router.py`
- Modify: `apps/api/app/rbac/schemas.py`
- Modify: `apps/api/tests/api/conftest.py`
- Modify: `apps/api/tests/api/test_permissions.py`
- Modify: `apps/api/tests/api/test_teaching.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `apps/api/tests/services/test_cohorts.py`

- [ ] **Step 1: Add eligible coach fixture and RED assignment tests**

Extend `platform_users` with `StaffCreate(staff_type="coach")`, active account and coach role. Prove:

- non-coach staff is rejected;
- missing/disabled account is rejected;
- missing coach role is rejected;
- primary assignments cannot overlap;
- the same coach cannot overlap as primary/assistant in one class;
- ending requires a reason and preserves history;
- ending resolves by both `class_id` and `assignment_id`, so a cross-class ID is not found;
- a second or concurrent end attempt cannot rewrite the first end timestamp/reason;
- archived cohorts reject assignment creation and ending;
- a later non-overlapping assignment is allowed;
- an ended historical interval still rejects a retroactive overlapping assignment;
- account disable or role removal immediately removes class access.
- global allow combined with a class-specific deny excludes that class before read totals/pagination and rejects the affected class PATCH, sibling class creation, cohort PATCH/transition, and assignment create/end for their respective permission keys.

- [ ] **Step 2: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_coach_assignments.py apps/api/tests/services/test_cohorts.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: FAIL because coach service and class resolution are absent.

- [ ] **Step 3: Implement assignment schemas and service**

```python
class CoachAssignmentCreate(TeachingInputModel):
    coach_person_id: UUID
    assignment_type: Literal["primary", "assistant"]
    valid_from: datetime
    valid_until: datetime | None = None

class CoachAssignmentEndRequest(TeachingInputModel):
    reason: str = Field(min_length=2, max_length=200)
```

Apply the same aware-datetime validator to assignment validity inputs. Expose:

```text
list_assignments(class_id: UUID, include_ended: bool, page: int, page_size: int) -> tuple[list[CoachAssignment], int]
create_assignment(class_id: UUID, input: CoachAssignmentCreate, actor_user_id: UUID) -> CoachAssignment
end_assignment(class_id: UUID, assignment_id: UUID, input: CoachAssignmentEndRequest, actor_user_id: UUID, occurred_at: datetime) -> CoachAssignment
eligible_coaches(class_id: UUID, query: str | None, page: int, page_size: int) -> tuple[list[Person], int]
```

Reuse `CoachEligibility` for creation and the eligible-coach directory. Current validity uses `valid_from <= at` and both optional upper bounds (`valid_until > at` and `ended_at > at`). Creation locks the class/cohort and rejects `archived`. Ending selects with both IDs under `FOR UPDATE`, rejects a missing/cross-class/already-ended assignment and an archived parent cohort, then sets `ended_at=occurred_at` from the server clock and the immutable first reason. Planned future expiry belongs in `valid_until`, not the end command. The migration exclusion expression uses the earlier effective endpoint. Map exclusion names to `primary_coach_conflict` or `coach_assignment_conflict`, map duplicate end to `coach_assignment_already_ended`, and order history by effective start descending then UUID.

- [ ] **Step 4: Correct assigned-class policy semantics first**

Add a regression test proving a generic assigned-class role rule does not match a specific token alone. Implement:

```python
if rule.scope_kind == "assigned_class" and rule.scope_id is None:
    return "assigned_class" in resource_tokens
```

Specific overrides continue matching `assigned_class:<uuid>`.

- [ ] **Step 5: Resolve real class resources in `RbacService`**

Add:

```python
def class_resource_tokens(
    self, *, actor_person_id: UUID, class_id: UUID, at: datetime | None = None,
) -> set[str]:
    tokens = {"global", f"assigned_class:{class_id}"}
    if self._person_has_current_assignment(actor_person_id, class_id, at=at):
        tokens.add("assigned_class")
    return tokens

def cohort_resource_tokens(
    self, *, actor_person_id: UUID, cohort_id: UUID, at: datetime | None = None,
) -> set[str]:
    class_ids = self._class_ids_for_cohort(cohort_id)
    tokens = {"global", *(f"assigned_class:{class_id}" for class_id in class_ids)}
    if any(
        self._person_has_current_assignment(actor_person_id, class_id, at=at)
        for class_id in class_ids
    ):
        tokens.add("assigned_class")
    return tokens
```

Add `teaching_visibility(user_id, permission, actor_person_id, at) -> TeachingVisibility`: evaluate global access, enumerate concrete class decisions, retain allowed IDs and retain explicit-deny IDs even for global readers. Apply the resulting SQL filter before count/limit/offset in `list_cohorts` and `list_classes`; get/status-history use the same visibility and return the same not-found response for missing and hidden resources. Generic assigned-class rules enumerate only the account person's current classes. Specific overrides validate and return their explicit class.

For writes, call policy on the actual target before the service mutation: class PATCH and assignment create/end use `class_resource_tokens`; class creation and cohort PATCH/transition use all parent cohort class tokens. A cohort-wide write requires global permission with no denied class, or permission on every class; a zero-class cohort requires global permission. Thus a specific deny always wins and a single specific allow cannot mutate sibling classes. Keep project/team writes global.

Replace the phase-1A assigned-class rejection in both add and preview paths. Resolve the class, reject a new `allow` when it is inactive, allow a `deny` or an already-stored rule to retain its historical label, and preview against `class_resource_tokens` rather than the family fallback. Add paginated class directory entries with class, cohort, project and active state; guard the endpoint with `permissions.manage`, and return only active rows for new selection while resolving inactive rows by ID for existing overrides. Recompute `missing_primary_class_count` on every read, so disabling the account or removing the coach role immediately produces the repair warning without deleting assignment history. Test real add/preview decisions, unauthorized directory access, inactive allow rejection and that a scoped coach cannot infer another cohort or class through totals, filters, 403/404 differences or nested response data.

- [ ] **Step 6: Add assignment and class-directory routes**

```text
GET/POST /classes/{class_id}/coach-assignments
POST /classes/{class_id}/coach-assignments/{assignment_id}/end
GET /classes/{class_id}/eligible-coaches
GET /permission-class-directory (query, page, page_size)
```

Management requires `coach_assignments.manage` evaluated on the target class tokens; current assigned coaches with scoped `teaching.read` can read their class and its parent cohort but receive only their authorized classes. Catalog management pages still require global `teaching.read`. Use the same not-found response for nonexistent and unauthorized class/cohort resources. Create and end each write exactly one audit row using the assignment `resource_id`; never include coach names or reasons in metadata.

- [ ] **Step 7: Verify and commit coach/RBAC work**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_coach_assignments.py apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/teaching apps/api/app/rbac \
  apps/api/tests/services/test_coach_assignments.py apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_teaching.py apps/api/tests/api/test_audit.py \
  apps/api/tests/services/test_cohorts.py
.venv/bin/mypy apps/api/app/teaching apps/api/app/rbac
git add apps/api/app/teaching apps/api/app/rbac \
  apps/api/tests/services/test_coach_assignments.py apps/api/tests/api/conftest.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_teaching.py \
  apps/api/tests/api/test_audit.py apps/api/tests/services/test_cohorts.py
git commit -m "feat: assign coaches with class-scoped access"
```

Expected: all pass, including explicit deny and expired-assignment denial.

### Task 6: Add idempotent cohort scheduler and Compose service

**Files:**

- Create: `apps/api/app/teaching/scheduler.py`
- Create: `apps/api/app/cli/run_teaching_scheduler.py`
- Create: `apps/api/tests/services/test_teaching_scheduler.py`
- Create: `apps/api/tests/cli/test_teaching_scheduler.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `compose.yaml`
- Modify: `compose.prod.yaml`
- Modify: `compose.e2e.yaml`
- Modify: `scripts/check.sh`

- [ ] **Step 1: Write RED scheduler tests**

Test due auto-start, due auto-complete, disabled flags, forbidden jumps, second-run idempotency, lock-not-acquired returning zero, two scheduler sessions producing one event, a scheduler racing request reconciliation producing one event, and one actor-null audit per transition. Add a two-candidate barrier test: pause the first scheduler after it commits neither but has transitioned candidate one, then prove a second session still cannot acquire the advisory lock before candidate two finishes. Assert a cohort that is past both dates moves only one adjacent state per reconciliation, then completes on the next reconciliation with two ordered events rather than one skipped transition.

- [ ] **Step 2: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_teaching_scheduler.py \
  apps/api/tests/cli/test_teaching_scheduler.py -q
```

Expected: FAIL because scheduler modules are absent.

- [ ] **Step 3: Implement advisory-locked reconciliation**

```python
SCHEDULER_LOCK_KEY = 0x455250_54454143

def new_scheduler_request_id() -> str:
    return f"scheduler-{uuid4()}"
```

Expose `reconcile_due_cohorts(db: Session, *, now: datetime, request_id_factory: Callable[[], str] = new_scheduler_request_id) -> int`. Reject naive `now`. The function owns one explicit outer `with db.begin()` transaction covering advisory-lock acquisition, the complete stable due-time/status/UUID scan, every `FOR UPDATE SKIP LOCKED` candidate transition and every audit write. Shared transition helpers may open nested savepoints but must never commit the outer transaction, so `pg_try_advisory_xact_lock` remains held until the entire batch commits or rolls back. Apply at most one adjacent transition per cohort per call, return zero without scanning when the lock is held elsewhere, and never sleep inside the service.

- [ ] **Step 4: Add testable CLI and setting**

Add `cohort_scheduler_interval_seconds: int = Field(default=30, ge=5, le=3600)`. CLI supports `--once` and `--interval`. Each iteration creates a fresh session and logs counts/errors without names or URLs.

- [ ] **Step 5: Add least-privilege Compose service**

Reuse API image/environment/database dependency. Run `python -m app.cli.run_teaching_scheduler`; use UID 10001, read-only root, tmpfs and `no-new-privileges`; production uses secrets/restart, E2E uses test keys, no ports. Extend `check.sh` to run scheduler `--once` after migration.

- [ ] **Step 6: Verify and commit scheduler**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_teaching_scheduler.py apps/api/tests/cli/test_teaching_scheduler.py -q
.venv/bin/ruff check apps/api/app/teaching/scheduler.py \
  apps/api/app/cli/run_teaching_scheduler.py apps/api/tests/services/test_teaching_scheduler.py \
  apps/api/tests/cli/test_teaching_scheduler.py
.venv/bin/mypy apps/api/app/teaching apps/api/app/cli/run_teaching_scheduler.py
docker compose -f compose.yaml config -q
docker compose -f compose.yaml -f compose.prod.yaml config -q
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml config -q
git add apps/api/app/teaching/scheduler.py apps/api/app/cli/run_teaching_scheduler.py \
  apps/api/app/core/config.py apps/api/tests/services/test_teaching_scheduler.py \
  apps/api/tests/cli/test_teaching_scheduler.py compose.yaml compose.prod.yaml \
  compose.e2e.yaml scripts/check.sh
git commit -m "feat: automate cohort state transitions"
```

Expected: Python checks pass; Compose config checks pass when the client exists.

### Task 7: Add Web contract, icons, stable mobile navigation, and class scopes

**Files:**

- Create: `apps/web/src/teaching/api.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.css`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/settings/api.ts`
- Modify: `apps/web/src/settings/PermissionsPage.tsx`
- Modify: `apps/web/src/settings/PermissionsPage.test.tsx`

- [ ] **Step 1: Write RED navigation and class-scope tests**

Test a maximum of four stable mobile tracks, exactly 首页/人员/账号/更多 for the current admin permission set, permission-filtered More contents and Escape close. Add permission-page tests for debounced class search, paginated results, inactive existing labels and rejection of a new inactive scope. Teaching route/navigation tests wait until their real pages exist in Tasks 8 and 9.

- [ ] **Step 2: Run the focused Web tests and verify RED**

```bash
npm --prefix apps/web test -- src/layout/AppShell.test.tsx \
  src/settings/PermissionsPage.test.tsx
```

Expected: FAIL because the four-slot mobile navigation and class directory are absent.

- [ ] **Step 3: Install Lucide and add the typed teaching API client**

```bash
npm --prefix apps/web install lucide-react@^0.468.0
```

```typescript
export type CohortStatus = "draft" | "open" | "upcoming" | "active" | "completed" | "archived"
export type TransitionTrigger = "manual" | "scheduler" | "request_reconciliation"
export type AutomationIssue = "auto_start_transition_blocked" | "auto_complete_transition_blocked"
export type ClassType = "standard" | "elite"
export type AssignmentType = "primary" | "assistant"
export type Page<T> = { items: T[]; page: number; page_size: number; total: number }
```

Add named `apiFetch` functions for every Task 2-5 endpoint. Components must not call raw fetch.

- [ ] **Step 4: Refactor navigation primitives without exposing teaching pages**

Convert the existing navigation item contract to Lucide component references and a tested priority/overflow model. Do not add a teaching item or route yet; every committed navigation target must resolve to a real page.

- [ ] **Step 5: Add Lucide navigation, mobile More, and class-scope selection**

Use `Home`, `Users`, `GraduationCap`, `UserCog`, `SlidersHorizontal`, `ShieldCheck`, `MoreHorizontal`, `LogOut`, `X`. Desktop shows all permitted items. Mobile has at most four stable tracks; More is an accessible menu/sheet. Replace the unavailable class-scope message with paginated `/permission-class-directory` search. Labels are `营期 · 班级 · 项目`; inactive scopes remain visible for existing rules but cannot be selected for a new allow. Do not add custom SVG, gradients or rounded text pills; all modified navigation and teaching text uses `letter-spacing: 0`.

- [ ] **Step 6: Verify and commit Web foundation**

```bash
npm --prefix apps/web test -- src/layout/AppShell.test.tsx \
  src/settings/PermissionsPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
git add apps/web/package.json apps/web/package-lock.json apps/web/src/teaching/api.ts \
  apps/web/src/layout apps/web/src/settings
git commit -m "feat: prepare teaching web foundation"
```

Expected: all checks pass before commit.

### Task 8: Build project and professional-team management UI

**Files:**

- Create: `apps/web/src/teaching/TeachingCatalogPage.tsx`
- Create: `apps/web/src/teaching/TeachingCatalogPage.test.tsx`
- Create: `apps/web/src/teaching/teaching.css`
- Modify: `apps/web/src/teaching/api.ts`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write RED catalog page tests**

Mock the typed API and cover active/inactive ordering, exact-name filters, project create/rename/sort/deactivate, team filter/create/edit/deactivate, reader-only UI, retained input after API 409, and semantic mobile card labels. Add a route test proving `/teaching/catalog` renders only with global `teaching.read`; scoped-only and denied users redirect.

- [ ] **Step 2: Run the page test and verify RED**

```bash
npm --prefix apps/web test -- src/teaching/TeachingCatalogPage.test.tsx \
  src/app/router.test.tsx
```

Expected: FAIL because the page is absent.

- [ ] **Step 3: Implement the catalog work surface**

Use tabs “电竞项目 / 职业队伍”, a compact filter band and one unframed data surface. Rows/cards use Lucide `Pencil`, `Power`, `PowerOff`; state changes require confirmation. Ordering uses numeric input/stepper, not drag-and-drop. Dialogs contain one form and are not nested inside cards. Query keys are `['projects', filters]` and `['professional-teams', filters]`; invalidate only affected lists.

After the real page compiles, register `/teaching/catalog` with a global-permission guard. Do not expose the main teaching navigation item until the cohort landing page exists in Task 9.

- [ ] **Step 4: Add restrained responsive styles**

Use 8px or smaller radii, stable tracks, neutral tokens, green active, amber draft/upcoming and red only blocked/inactive. Set `letter-spacing: 0` and do not use viewport-scaled fonts. Below 720px, wrap actions, move filters into a menu and turn rows into labeled cards. No control may overflow at 360px.

- [ ] **Step 5: Verify and commit catalog UI**

```bash
npm --prefix apps/web test -- src/teaching/TeachingCatalogPage.test.tsx src/app/router.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
git add apps/web/src/teaching apps/web/src/app
git commit -m "feat: add teaching catalog workspace"
```

Expected: all checks pass before commit.

### Task 9: Build cohort, class, status, and coach management UI

**Files:**

- Create: `apps/web/src/teaching/CohortsPage.tsx`
- Create: `apps/web/src/teaching/CohortsPage.test.tsx`
- Create: `apps/web/src/teaching/CohortDetailPage.tsx`
- Create: `apps/web/src/teaching/CohortDetailPage.test.tsx`
- Modify: `apps/web/src/teaching/teaching.css`
- Modify: `apps/web/src/teaching/api.ts`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1: Write RED list/detail tests**

Cover exact-name/status/date filters, pagination, Shanghai datetime conversion, start-at-midnight and end-next-day-midnight form defaults, editable exact times, default check-in/out initialization, independent auto toggles, automation warning display, legal next-state commands only, transition errors, ordinary/elite team fields, exact yuan-to-cents conversion, eligible coach selector, reason-required assignment end, history trigger visibility and read-only scoped coach UI. Add route/navigation tests for global and scoped cohort access, denial redirects, scoped catalog denial and final admin mobile tracks 首页/人员/招生教学/更多. The scoped test must prove unauthorized cohorts/classes and their aggregate counts never render.

- [ ] **Step 2: Run tests and verify RED**

```bash
npm --prefix apps/web test -- \
  src/teaching/CohortsPage.test.tsx src/teaching/CohortDetailPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
```

Expected: FAIL because pages are absent.

- [ ] **Step 3: Implement `CohortsPage`**

Render a dense filter band and table/card list with status, start/end, automation flags, authorized enabled-class count, authorized missing-primary warning and `automation_issues`. Use `CalendarDays`, `Clock3`, `RefreshCw`, `TriangleAlert`, `Plus`. In the create form, choosing an inclusive start/end date initializes `starts_at` to start-day `00:00`, `ends_at` to the day after the end date at `00:00`, and check-in/out to those same values; every datetime remains editable before submission. Centralize conversion in tested helpers: parse `datetime-local` as Shanghai wall time, handle the next-day calculation with calendar arithmetic, send an ISO `+08:00` offset, and format responses through `Intl.DateTimeFormat` with `timeZone: "Asia/Shanghai"`. Never append `Z` to naive input or rely on the browser host timezone.

- [ ] **Step 4: Implement `CohortDetailPage`**

Show literal cohort name, status, automation warnings and legal commands. Tabs are “概览 / 班级 / 教练”; do not add students yet. Class form uses segmented 普通班/菁英班, project menu, same-project team menu, capacity and yuan tuition. Coach rows show role, validity and ended history; status history distinguishes manual, scheduler and request-reconciliation triggers. Register `/teaching/cohorts` and `/teaching/cohorts/:cohortId` only after both real pages exist. The catalog guard requires global permission; cohort guards accept global or scoped permission. Add the teaching navigation item for either form and make it open the cohort list. Global readers see all authorized data and managers see commands. Scoped-only coaches see only their assigned classes and cohort summary, with no catalog link, aggregate data from hidden classes, or mutation controls. Mutation dialogs show server errors inline and disable pending submit.

- [ ] **Step 5: Complete four layout bands**

At 1024/1440 use compact two-column overview and full-width tabs; at 768 use one column/condensed rows; at 360 use stable two-column commands, labeled cards and full-screen dialogs. Long names wrap without covering controls.

- [ ] **Step 6: Verify and commit cohort UI**

```bash
npm --prefix apps/web test -- src/teaching src/layout/AppShell.test.tsx src/app/router.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
git add apps/web/src/teaching apps/web/src/app apps/web/src/layout
git commit -m "feat: add cohort and class management workspace"
```

Expected: all checks pass before commit.

### Task 10: Seed a coach and add four-viewport E2E journeys

**Files:**

- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/playwright.config.ts`
- Modify: `apps/web/tests/helpers.ts`
- Create: `apps/web/tests/teaching-configuration.spec.ts`

- [ ] **Step 1: Write RED seed assertions**

Define:

```python
COACH_PHONE = "19990000003"
COACH_PASSWORD = "E2ECoach1x26"
```

Assert seeded coach staff type, active account and coach role; extend `E2ESeedResult` with coach IDs. Run `seed_e2e` twice against the same E2E database and assert each reset leaves exactly the four frozen initial projects with no duplicates.

- [ ] **Step 2: Run seed test and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py -q
```

Expected: FAIL because coach seed is absent.

- [ ] **Step 3: Add reset-safe coach seed**

Create coach with `IdentityService`, account with `AccountService`, assign coach role and deterministic password. Keep the existing single `TRUNCATE TABLE people, audit_events, auth_rate_limits RESTART IDENTITY CASCADE`; PostgreSQL truncation cascades into teaching tables through actor FKs, so call `seed_initial_projects(db)` immediately after `expunge_all()` and before creating E2E business rows. Do not add unordered per-table truncates.

- [ ] **Step 4: Configure four Playwright projects**

```typescript
projects: [
  { name: "desktop-1440", use: { viewport: { width: 1440, height: 900 } } },
  { name: "desktop-1024", use: { viewport: { width: 1024, height: 768 } } },
  { name: "tablet-768", use: { viewport: { width: 768, height: 1024 } } },
  { name: "mobile-360", use: { viewport: { width: 360, height: 800 } } },
]
```

- [ ] **Step 5: Write administrator and scoped-coach teaching journeys**

Using visible UI only, the administrator logs in, creates a unique project/team/future cohort plus a second unassigned control cohort, verifies the default and editable exact times, creates standard and elite classes, proves team/project filtering, assigns the seeded coach as primary to both teaching classes, moves the assigned cohort from draft to open, verifies one manual status event, and asserts no horizontal overflow. Then log in as the coach, prove the teaching navigation and assigned cohort/classes are visible, the control cohort is absent, direct catalog access redirects, mutation controls are absent, and the page still has no horizontal overflow. Derive names from the Playwright project to avoid collisions.

- [ ] **Step 6: Run seed and E2E tests**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py -q
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
E2E_BASE_URL='http://127.0.0.1:5173' npm --prefix apps/web run test:e2e
```

Expected: seed passes and platform/teaching journeys pass in all four projects. Reset and seed `erp_e2e` immediately before local Playwright.

- [ ] **Step 7: Commit responsive E2E coverage**

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/web/playwright.config.ts apps/web/tests/helpers.ts \
  apps/web/tests/teaching-configuration.spec.ts
git commit -m "test: verify teaching configuration journeys"
```

### Task 11: Update docs, rehearse restore, and run the full module gate

**Files:**

- Modify: `README.md`
- Modify: `scripts/check.sh`
- Modify after failures: only files already listed in this plan

- [ ] **Step 1: Update README boundaries and operations**

Document editable projects/teams, cohort state automation, standard/elite classes, default tuition, assignment history, real class scope, responsive teaching UI and scheduler operation. State that guardian/student teaching grants remain deferred until their usable family/self endpoints ship. Keep payment, enrollment,退营退款, coach roster, family teaching, evaluation and housing explicitly unavailable.

- [ ] **Step 2: Run complete backend gate**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
```

Expected: all pass with no configured PostgreSQL teaching test skipped.

- [ ] **Step 3: Run complete Web gate**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all pass.

- [ ] **Step 4: Verify real `0003 -> 0004` migration**

Create an explicitly named `_test` database, migrate to `0003_guardian_relationship_history`, insert representative person/account data, upgrade to head, verify data retention, six teaching tables, four seed projects and head `0004_teaching_configuration`; then drop only that test database.

- [ ] **Step 5: Run real backup/restore rehearsal**

Before backup, ensure the source contains at least one cohort status event and one account permission override in addition to seeded role grants; assert each critical mapping/history table has a nonzero source count.

```bash
DATABASE_URL='postgresql://skymoonzyj@127.0.0.1:55432/erp_e2e' \
  bash scripts/backup-db.sh ./backups/phase1b-teaching-verification.dump
DATABASE_URL='postgresql://skymoonzyj@127.0.0.1:55432/erp_e2e' \
  bash scripts/restore-db.sh ./backups/phase1b-teaching-verification.dump \
  erp_phase1b_teaching_restore_test
```

Expected: checksum, head, manifest tables and row counts pass, including `role_permissions`, `user_roles`, `user_permission_overrides` and `cohort_status_events`; never overwrite a non-empty target.

- [ ] **Step 6: Run Compose and responsive E2E gate**

```bash
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  bash scripts/check.sh
```

Expected: API, Web, migration, scheduler `--once`, Compose and four Playwright projects exit 0. If Docker daemon is unavailable, record that blocker and run local PostgreSQL/E2E separately; do not claim Compose success.

- [ ] **Step 7: Review tracked output and commit docs**

```bash
git diff --check
git status --short
git add README.md scripts/check.sh
git commit -m "docs: document teaching configuration operations"
```

Expected: generated coverage, Playwright reports, dumps and secrets are untracked/ignored.

- [ ] **Step 8: Request final review and finish branch**

Use `superpowers:requesting-code-review` for full spec and quality review. Resolve all important findings, rerun the full gate, then use `superpowers:finishing-a-development-branch` to present integration options.
