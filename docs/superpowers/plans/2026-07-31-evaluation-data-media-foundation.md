# Evaluation Data and Media Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付评价中心第二实施单元，为工作人员服务历史、游戏 ID、辅助手机号、对象范围权限、版本化分析配置和私有截图存储建立可审计的 ERP 主数据与响应式管理页面，同时保持截图上传和评分处理关闭。

**Architecture:** ERP PostgreSQL 成为本单元全部正式元数据的唯一主记录；`app.evaluations` 按授权、工作人员、游戏账号、分析配置和媒体存储拆成独立服务。ERP Web 提供学员/家庭游戏账号页、营期工作人员页和管理员基础配置页；私有媒体通过 Git 外目录、流式校验、内容对象去重和去元数据预览建立服务底座，但不开放浏览器上传端点。`game-score-tool` 的 v1 登录桥接和只读页面保持不变，第三实施单元再接入上传、任务、OCR、审核与发布。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic、Pillow、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright

---

## Plan orientation

本计划冻结以下范围，执行时不得扩大：

- 只修改 ERP 仓库；评分工具仓库及跨仓库 v1 登录契约保持不变。
- 不修改 Docker、Compose、NAS、备份恢复或生产部署文件。
- 不读取、迁移、复制或删除评分工具旧 `data/`、`uploads/`、`runtime/`。
- 本单元不建立评价任务，不执行 OCR/评分，不开放人工录分、审核、发布、日报、周报或营期问卷。
- 系统评价页面继续显示“截图上传和评分处理将在后续单元启用”；`upload_available` 仍为 `false`。
- 私有媒体服务只通过后端服务测试和管理员存储状态接口验收；没有任务就没有浏览器上传入口，避免产生无法处理的业务记录。
- 所有手机号和游戏 ID 原文使用现有信封加密；检索只使用带密钥 HMAC。日志、审计和错误不得返回原始辅助手机号或完整游戏 ID。辅助手机号始终脱敏；完整游戏 ID 只在对象授权通过后返回给本人、获权家庭或管理员。
- 工作人员中途更换、游戏 ID 解绑、辅助手机号停用、权限收回和分析配置换版均保留历史，不做硬删除。
- 所有日期时间由 API 返回带时区 ISO 值；业务窗口按 `Asia/Shanghai` 计算，前端不自行推算。

### Approaches considered

1. **数据管理与媒体底座先行（采用）**：本单元交付可使用的人员、账号和配置管理页面，并完整测试媒体存储服务，但不开放孤立上传。
2. **提前开放“待处理上传”**：用户能立刻上传，但第三单元完成前无法评分，会积累无完成路径的数据并制造误解，因此拒绝。
3. **纯数据库骨架**：提交更快，但无法验证权限、服务历史和响应式管理旅程，后续集成风险更高，因此拒绝。

### Repository and responsibility map

| 边界 | 主要文件 | 单一职责 |
| --- | --- | --- |
| 数据模型/迁移 | `app/evaluations/*_models.py`、`0008_evaluation_data_media_foundation.py` | 工作人员、游戏账号、配置、媒体元数据和约束 |
| 对象授权 | `app/rbac/*`、`app/evaluations/access.py` | 扩展同一权限覆盖系统；合并固定 RBAC、服务分配和显式拒绝 |
| 工作人员 | `app/evaluations/staff_service.py` | 自定义角色、项目/班级分配、结束服务和历史查询 |
| 游戏账号 | `app/evaluations/game_account_service.py` | 游戏 ID 上限、唯一性、解绑历史和辅助手机号 |
| 分析配置 | `app/evaluations/analysis_profile_service.py` | 项目默认配置、不可变版本和当前版本选择 |
| 私有媒体 | `app/evaluations/media_store.py`、`media_service.py` | 临时流、哈希、解码、原子存储、预览和元数据事务 |
| API | `app/evaluations/{portal,admin}_router.py`、`schemas.py` | 本人/家庭与管理员最小 DTO；不暴露物理路径 |
| Web | `apps/web/src/evaluations/*`、`CohortDetailPage.tsx`、`PersonDetailPage.tsx` | 账号、工作人员和配置管理页面 |
| 验收 | API/服务/迁移测试、Vitest、Playwright | 权限、历史、并发约束和四视口旅程 |

### Frozen route summary

```text
GET    /api/v1/evaluations/context
GET    /api/v1/evaluations/game-account-targets
GET    /api/v1/evaluations/students/{student_id}/game-account-projects?page=&page_size=
GET    /api/v1/evaluations/students/{student_id}/game-accounts?include_unbound=true
POST   /api/v1/evaluations/students/{student_id}/game-accounts
POST   /api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind

GET    /api/v1/evaluations/admin/students?query=&page=&page_size=
GET    /api/v1/evaluations/admin/students/{student_id}/matching-profile
GET    /api/v1/evaluations/admin/students/{student_id}/game-account-limit
PUT    /api/v1/evaluations/admin/students/{student_id}/game-account-limit
GET    /api/v1/evaluations/admin/students/{student_id}/auxiliary-phones
POST   /api/v1/evaluations/admin/students/{student_id}/auxiliary-phones
POST   /api/v1/evaluations/admin/students/{student_id}/auxiliary-phones/{phone_id}/replace
POST   /api/v1/evaluations/admin/students/{student_id}/auxiliary-phones/{phone_id}/deactivate
GET    /api/v1/evaluations/admin/staff-roles
POST   /api/v1/evaluations/admin/staff-roles
GET    /api/v1/evaluations/admin/staff-directory?cohort_id=&query=&page=&page_size=
POST   /api/v1/evaluations/admin/staff-roles/{role_id}/deactivate
GET    /api/v1/evaluations/admin/cohorts?query=&page=&page_size=
GET    /api/v1/evaluations/admin/cohorts/{cohort_id}/staff-scopes?scope_kind=&page=&page_size=
GET    /api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments
POST   /api/v1/evaluations/admin/cohorts/{cohort_id}/staff-assignments
POST   /api/v1/evaluations/admin/staff-assignments/{assignment_id}/end
GET    /api/v1/evaluations/admin/projects?query=&page=&page_size=
GET    /api/v1/evaluations/admin/projects/{project_id}/analysis-profiles
POST   /api/v1/evaluations/admin/projects/{project_id}/analysis-profiles
GET    /api/v1/evaluations/admin/media-storage/status
GET    /api/v1/evaluations/media-assets/{media_id}/preview
GET    /api/v1/evaluations/media-assets/{media_id}/original
```

`/integrations/evaluations/v1/*` 不在本单元扩展。浏览器和评分工具都不能调用媒体写入服务。

---

### Task 1: Add the evaluation foundation schema and permissions

**Files:**

- Create: `apps/api/app/evaluations/staff_models.py`
- Create: `apps/api/app/evaluations/game_models.py`
- Create: `apps/api/app/evaluations/analysis_models.py`
- Create: `apps/api/app/evaluations/media_models.py`
- Modify: `apps/api/app/evaluations/models.py`
- Create: `apps/api/migrations/versions/0008_evaluation_data_media_foundation.py`
- Modify: `apps/api/app/identity/models.py`
- Modify: `apps/api/app/admissions/models.py`
- Modify: `apps/api/app/rbac/models.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing permission seed tests**

Add fixed permission IDs after `evaluation_center.access`:

```python
EVALUATION_FOUNDATION_PERMISSION_IDS = {
    "evaluation_media.upload": UUID("20000000-0000-4000-8000-000000000027"),
    "evaluation_media.original.read": UUID("20000000-0000-4000-8000-000000000028"),
    "analysis_profiles.manage": UUID("20000000-0000-4000-8000-000000000030"),
    "student_game_accounts.manage": UUID("20000000-0000-4000-8000-000000000031"),
    "cohort_staff.manage": UUID("20000000-0000-4000-8000-000000000036"),
}
EVALUATION_LIFE_TEACHER_ROLE_ID = UUID(
    "41000000-0000-4000-8000-000000000001"
)


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
        assert rows == {
            key: (permission_id, True)
            for key, permission_id in EVALUATION_FOUNDATION_PERMISSION_IDS.items()
        }
        grants = set(
            db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key.in_(EVALUATION_FOUNDATION_PERMISSION_IDS))
            )
        )
        assert {
            ("admin", key, "global")
            for key in EVALUATION_FOUNDATION_PERMISSION_IDS
        } <= grants
        assert ("student", "student_game_accounts.manage", "self") in grants
        assert ("student", "evaluation_media.original.read", "self") in grants
        assert ("guardian", "evaluation_media.original.read", "family") in grants
        assert ("coach", "evaluation_media.original.read", "assigned_class") in grants
        assert ("guardian", "student_game_accounts.manage", "family") not in grants
        assert ("customer_service", "evaluation_media.upload", "global") not in grants
```

- [ ] **Step 2: Write failing migration shape and downgrade-guard tests**

Freeze the table set:

```python
EVALUATION_FOUNDATION_TABLES = {
    "evaluation_staff_roles",
    "cohort_staff_assignments",
    "cohort_staff_assignment_permissions",
    "student_game_accounts",
    "student_auxiliary_phones",
    "project_analysis_profiles",
    "evaluation_media_blobs",
    "evaluation_media_assets",
}
EXPECTED_TABLES |= EVALUATION_FOUNDATION_TABLES
```

Assert at minimum:

```python
def test_evaluation_foundation_schema_has_history_and_uniqueness_guards(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert EVALUATION_FOUNDATION_TABLES <= set(inspector.get_table_names())
    assert "uq_student_game_accounts_active_project_digest" in {
        index["name"] for index in inspector.get_indexes("student_game_accounts")
    }
    assert "uq_project_analysis_profiles_project_version" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("project_analysis_profiles")
    }
    assert "uq_evaluation_media_blobs_sha256" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("evaluation_media_blobs")
    }
    override_checks = {
        check["name"]
        for check in inspector.get_check_constraints("user_permission_overrides")
    }
    assert {
        "ck_permission_overrides_scope_kind",
        "ck_permission_overrides_scope_id",
        "ck_permission_overrides_validity",
    } <= override_checks
```

Add a downgrade guard that inserts one row into each business-history table and one new-scope permission override, runs `alembic downgrade 0007_evaluation_page_integration`, expects `RuntimeError`, and then proves revision `0008_evaluation_data_media_foundation` plus every inserted row remains intact. Empty-schema upgrade/downgrade/upgrade must still pass.

- [ ] **Step 3: Run the migration tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
```

Expected: FAIL because revision `0008_evaluation_data_media_foundation`, new tables and permissions do not exist.

- [ ] **Step 4: Add SQLAlchemy models with fixed checks and history fields**

Implement focused models in `staff_models.py`, `game_models.py`, `analysis_models.py` and `media_models.py`; `app/evaluations/models.py` imports and re-exports them so Alembic's existing single import still registers metadata:

```python
class EvaluationStaffRole(TimestampMixin, Base):
    __tablename__ = "evaluation_staff_roles"
    # key, name, category(teaching|life_service|operations), is_builtin,
    # is_active, created_by_user_id. Used roles become immutable; deactivate only.


class CohortStaffAssignment(TimestampMixin, Base):
    __tablename__ = "cohort_staff_assignments"
    # role_id, cohort_id, staff_person_id, exactly one of project_id/class_id,
    # valid_from, valid_until, ended_at/by/reason, created/updated actor


class CohortStaffAssignmentPermission(Base):
    __tablename__ = "cohort_staff_assignment_permissions"
    # assignment_id + permission_id composite primary key; actor and created_at


class StudentGameAccount(TimestampMixin, Base):
    __tablename__ = "student_game_accounts"
    # student_person_id, project_id, identifier_ciphertext/digest,
    # valid_from, unbound_at/by/reason, created_by_user_id


class StudentAuxiliaryPhone(TimestampMixin, Base):
    __tablename__ = "student_auxiliary_phones"
    # student_person_id, phone_ciphertext/digest, valid_from,
    # disabled_at/by/reason, created_by_user_id and timestamps


class ProjectAnalysisProfile(TimestampMixin, Base):
    __tablename__ = "project_analysis_profiles"
    # project_id, version, mode, adapter_id/version, rule_version,
    # required_fields JSONB, confidence_thresholds JSONB,
    # auto_publish_warning_levels JSONB, publish_mode, effective_from,
    # change_reason, created_by_user_id; rows are immutable after insert


class EvaluationMediaBlob(TimestampMixin, Base):
    __tablename__ = "evaluation_media_blobs"
    # storage_key and preview_storage_key only, never an absolute path;
    # sha256 unique, mime/extension/bytes/pixels, storage/retention state


class EvaluationMediaAsset(TimestampMixin, Base):
    __tablename__ = "evaluation_media_assets"
    # blob_id, uploaded_by_user_id, project_id; target student/enrollment/class
    # is all-null pending or all-filled matched exactly once; no job fields
```

Extend `StudentProfile` with `game_account_limit INTEGER NOT NULL DEFAULT 3 CHECK >= 3`. Extend `UserPermissionOverride` with scope kinds `cohort`, `project`, `enrollment`, nullable `valid_from/valid_until`, `created_by_user_id`, `grant_reason` and `created_at`; existing rules remain indefinite. Add `UNIQUE(id, student_person_id)` to `Enrollment` for the media asset composite foreign key.

Use `RESTRICT` for all business/history foreign keys. The active game-ID index is partial on `unbound_at IS NULL` and a GiST interval constraint prevents overlapping historical ownership. Auxiliary phone digests are deliberately non-unique across students but unique per active student/digest pair. `EvaluationMediaBlob.storage_key` is unique and opaque; it may not start with `/` or contain `..`. A database trigger permits media target columns to move from all-null to all-filled once and rejects every later rewrite.

- [ ] **Step 5: Implement revision 0008 and safe downgrade refusal**

The migration must:

1. insert the five current-unit permissions and least-privilege grants;
2. extend `student_profiles`, `user_permission_overrides` and `enrollments` without changing existing semantics;
3. create the eight new tables, indexes, exclusion constraints and target-immutability trigger exactly as the models define them;
4. insert one deterministic built-in `life_teacher` evaluation staff role, while main/assistant coaches remain projected from `coach_assignments`;
5. backfill one immutable `manual_only` analysis profile version 1 for every existing project;
6. refuse downgrade if any business rows, new-scope/time-bounded override, non-default profile version, changed seed row, permission or grant exists;
7. remove only untouched backfilled profiles, built-in staff role, fixed grants, fixed permissions, empty tables and extension columns.

Do not add or edit Docker/Compose files.

- [ ] **Step 6: Register permissions and schema manifest**

Add the five current-unit permissions to `PERMISSIONS`. Grant all five to `admin`; grant `student_game_accounts.manage/self` and `evaluation_media.original.read/self` to `student`; grant original read to `guardian/family` and `coach/assigned_class`. Extend `scripts/db-schema-manifest.sh` with the eight new tables and revision `0008_evaluation_data_media_foundation`.

- [ ] **Step 7: Run focused migration, Ruff and model type checks**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
.venv/bin/ruff check apps/api/app/evaluations/models.py \
  apps/api/app/evaluations/staff_models.py \
  apps/api/app/evaluations/game_models.py \
  apps/api/app/evaluations/analysis_models.py \
  apps/api/app/evaluations/media_models.py \
  apps/api/migrations/versions/0008_evaluation_data_media_foundation.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py
.venv/bin/mypy apps/api/app/evaluations/models.py \
  apps/api/app/evaluations/staff_models.py \
  apps/api/app/evaluations/game_models.py \
  apps/api/app/evaluations/analysis_models.py \
  apps/api/app/evaluations/media_models.py
bash -n scripts/db-schema-manifest.sh
```

Expected: PASS with revision `0008_evaluation_data_media_foundation`; static checks exit 0.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/evaluations/models.py \
  apps/api/app/evaluations/staff_models.py \
  apps/api/app/evaluations/game_models.py \
  apps/api/app/evaluations/analysis_models.py \
  apps/api/app/evaluations/media_models.py \
  apps/api/migrations/versions/0008_evaluation_data_media_foundation.py \
  apps/api/app/identity/models.py apps/api/app/admissions/models.py \
  apps/api/app/rbac/models.py apps/api/app/rbac/seed.py apps/api/migrations/env.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  scripts/db-schema-manifest.sh
git commit -m "feat: add evaluation foundation schema"
```

---

### Task 2: Extend the single RBAC system for timed evaluation scopes

**Files:**

- Create: `apps/api/app/evaluations/access.py`
- Create: `apps/api/tests/services/test_evaluation_access.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/rbac/models.py`
- Modify: `apps/api/app/rbac/policy.py`
- Modify: `apps/api/app/rbac/schemas.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/rbac/router.py`
- Modify: `apps/api/app/audit/service.py`

- [ ] **Step 1: Write failing authorization matrix tests**

Create fixtures for admin, student, guardian, primary coach, assistant coach, unrelated coach and customer service. Implement these named tests with the stated final assertions:

- `test_default_evaluation_authorization_is_least_privilege`: admin and student can manage the student's IDs; default guardian and customer service decisions are false.
- `test_family_allow_stops_on_relationship_end`: a finite family allow is true one microsecond before relationship end and false at the half-open end boundary.
- `test_staff_assignment_allow_is_limited_to_assignment_scope_and_time`: the assigned project/class is allowed inside `[valid_from, effective_end)` and an unrelated class plus the exact end instant are denied.
- `test_account_deny_beats_admin_role_staff_role_and_specific_allow`: add a matching deny and assert `EvaluationDecision(False, "explicit_deny")` despite every other allow source.
- `test_expired_override_fails_on_the_next_decision`: make two consecutive decisions around `valid_until` and assert the second is denied without rebuilding the service.
- `test_inactive_account_has_no_evaluation_business_access`: disable the account and assert all permissions deny.
- `test_listing_filters_with_the_same_predicate_as_detail`: assert the filtered IDs equal the resources whose individual decisions are allowed.
- `test_actor_cannot_delegate_permission_or_scope_they_do_not_hold`: attempt a wider-scope override and assert `PermissionDelegationDenied` with no inserted row.

The override fixtures must cover `cohort`, `project`, `assigned_class`, `enrollment`, `person`, `self` and `family` tokens. Explicit deny at a broader matching scope must beat every allow. Existing indefinite overrides must keep their current behavior.

- [ ] **Step 2: Run the focused test and verify it fails**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_access.py -q
```

Expected: FAIL because the new timed scopes and `EvaluationAccessService` do not exist.

- [ ] **Step 3: Implement one authorization decision API**

Use immutable inputs so every later service calls the same predicate:

```python
@dataclass(frozen=True)
class EvaluationResource:
    student_person_id: UUID | None = None
    staff_person_id: UUID | None = None
    cohort_id: UUID | None = None
    project_id: UUID | None = None
    class_id: UUID | None = None
    enrollment_id: UUID | None = None


@dataclass(frozen=True)
class EvaluationDecision:
    allowed: bool
    reason: str


class EvaluationAccessService:
    def decide(
        self,
        *,
        account_id: UUID,
        permission: str,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> EvaluationDecision:
        raise NotImplementedError

    def filter_resources(
        self,
        *,
        account_id: UUID,
        permission: str,
        resources: Iterable[EvaluationResource],
        occurred_at: datetime,
    ) -> list[EvaluationResource]:
        raise NotImplementedError
```

Decision order is fixed:

1. reject disabled/non-standard account;
2. collect active account-level denies from the existing `user_permission_overrides` table;
3. if any matching deny exists, return `explicit_deny`;
4. evaluate fixed global/self/family RBAC roles;
5. evaluate active account permission overrides;
6. evaluate current/overlapping coach assignment and generic assignment permissions;
7. otherwise return `no_matching_grant`.

`occurred_at` must be timezone-aware. The service must query current guardian and service relationships on every call; it must not cache authorization across requests.

- [ ] **Step 4: Extend the existing permission override lifecycle**

Extend `PermissionOverrideCreate/Response`, permission preview, directories and `RbacService.add_override()` with `valid_from`, `valid_until`, `grant_reason`, `cohort`, `project` and `enrollment`. Keep `assigned_class` as the class scope name so old rows and clients remain compatible.

For a sensitive evaluation `allow`, require an object, self or family scope, an aware `valid_from`, an aware finite `valid_until`, and a non-sensitive reason. Fixed admin role grants remain global. Denies may be broader and indefinite. Validate every scope target exists and require the actor to hold the same permission over an equal or wider active scope; even an administrator route may not transfer a permission the actor lacks. Existing account override removal continues to delete the row only after its immutable audit event is appended; do not create a second evaluation-grant table.

- [ ] **Step 5: Extend audit metadata without admitting secrets**

Allow only these new safe identifiers and enums:

```python
_PUBLIC_METADATA_KEYS |= {
    "game_account_id", "auxiliary_phone_id", "staff_role_id",
    "staff_assignment_id", "analysis_profile_id",
    "media_id", "media_blob_id", "profile_version", "analysis_mode",
    "old_limit", "new_limit",
}
_PUBLIC_SCOPE_KINDS |= {
    "cohort", "project", "enrollment",
}
```

Do not add game ID, phone digest, media hash, storage key, file name, adapter payload or physical path to the allowlist.
Validate `analysis_mode` separately against `manual_only | automatic`; do not reuse the existing bridge `mode` metadata field, which remains limited to `standalone | embedded`. Accept `old_limit/new_limit` only as integers at least 3; reject booleans and other values.

- [ ] **Step 6: Run authorization, RBAC and audit regressions**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_access.py \
  apps/api/tests/unit/rbac/test_policy.py apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/evaluations/access.py \
  apps/api/app/rbac apps/api/app/audit/service.py \
  apps/api/tests/services/test_evaluation_access.py
.venv/bin/mypy apps/api/app/evaluations/access.py apps/api/app/rbac
```

Expected: PASS; deny-first and relationship-change cases pass without request caching.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/evaluations/access.py \
  apps/api/app/evaluations/errors.py apps/api/app/rbac/models.py \
  apps/api/app/rbac/policy.py apps/api/app/rbac/schemas.py \
  apps/api/app/rbac/service.py apps/api/app/rbac/router.py \
  apps/api/app/audit/service.py \
  apps/api/tests/services/test_evaluation_access.py
git commit -m "feat: add scoped evaluation authorization"
```

---

### Task 3: Add cohort staff roles, assignments and unified service history

**Files:**

- Create: `apps/api/app/evaluations/staff_service.py`
- Create: `apps/api/tests/services/test_evaluation_staff.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/schemas.py`

- [ ] **Step 1: Write failing staff-role and assignment tests**

Cover both generic assignments and the read-only projection of existing coach assignments with these exact tests:

- unified history contains one main coach, one assistant and one generic assignment while `cohort_staff_assignments` contains only the generic row;
- project/class are mutually exclusive and must belong to the assignment's target cohort;
- two concurrent overlapping inserts for the same person/role/scope produce one row and one `StaffAssignmentConflict`, while adjacent intervals both succeed;
- ending a row writes actor/time/reason and the response keeps the original role name, category and scope snapshot;
- ended staff stays in history but loses live assignment capabilities at the exact half-open boundary;
- archived cohorts reject new assignments without changing existing rows; global role definitions are managed independently of cohort status;
- assignment capability keys accept only the two allowlisted media permissions in this unit;
- a person already projected from `CoachAssignment` cannot be duplicated as the same teaching service/scope.

The unified DTO must identify `source="coach_assignment" | "cohort_staff_assignment"`. Main/assistant coaches are never inserted into `cohort_staff_assignments` merely to make the UI uniform.

- [ ] **Step 2: Run the service test and verify it fails**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_staff.py -q
```

Expected: FAIL because `CohortStaffService` does not exist.

- [ ] **Step 3: Implement fixed staff service DTOs**

Add these response contracts:

```python
class StaffRoleCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    name: str = Field(min_length=2, max_length=80)
    category: Literal["teaching", "life_service", "operations"]


class StaffAssignmentCreate(BaseModel):
    role_id: UUID
    staff_person_id: UUID
    scope_kind: Literal["project", "class"]
    scope_id: UUID
    valid_from: datetime
    valid_until: datetime | None = None
    permission_keys: list[str] = Field(default_factory=list, max_length=10)


class StaffAssignmentEnd(BaseModel):
    reason: str = Field(min_length=2, max_length=200)


class StaffServiceItem(BaseModel):
    id: UUID
    source: Literal["coach_assignment", "cohort_staff_assignment"]
    staff_person_id: UUID
    staff_name: str
    role_name: str
    role_category: Literal["teaching", "life_service", "operations"]
    scope_kind: Literal["project", "class"]
    project_id: UUID
    project_name: str
    class_id: UUID | None
    class_name: str | None
    valid_from: datetime
    valid_until: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    manage_allowed: bool
```

- [ ] **Step 4: Implement role and assignment transactions**

`CohortStaffService` must:

- create reusable global staff-role definitions; the built-in life-teacher role is seeded, custom roles require an actor;
- refuse rename/category edits after a role is used; deactivation affects new assignment choices but never rewrites history;
- lock the cohort before every assignment write;
- require a non-deleted person with `StaffProfile` and an enabled account for new assignments;
- reject archived cohorts;
- validate project scope has at least one class in the cohort and class scope belongs to the cohort;
- snapshot role name/category on the assignment so later role deactivation cannot rewrite history;
- reject naive datetimes and normalize no timestamps on the client;
- translate exclusion/unique constraints into `staff_assignment_conflict`;
- end rather than delete assignments, requiring actor, time and reason;
- attach capabilities to the concrete assignment, not the reusable role name; allow only `evaluation_media.upload` and `evaluation_media.original.read` in this unit;
- append safe audit events for role create/deactivate, capability replace, assignment create/end.

Use the existing `CoachAssignment` interval as the source of truth for primary/assistant services. For coach projection, `role_name` is “主教练” or “助教”, category is `teaching`, and scope is the assigned class.

- [ ] **Step 5: Run staff, teaching and portal regressions**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_staff.py \
  apps/api/tests/services/test_coach_assignments.py \
  apps/api/tests/services/test_portal_coach.py -q
.venv/bin/ruff check apps/api/app/evaluations/staff_service.py \
  apps/api/tests/services/test_evaluation_staff.py
.venv/bin/mypy apps/api/app/evaluations/staff_service.py
```

Expected: PASS; existing coach assignment behavior remains unchanged.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/evaluations/staff_service.py \
  apps/api/app/evaluations/errors.py apps/api/app/evaluations/schemas.py \
  apps/api/tests/services/test_evaluation_staff.py
git commit -m "feat: track cohort staff service history"
```

---

### Task 4: Add game IDs, per-student limits and auxiliary phones

**Files:**

- Create: `apps/api/app/evaluations/game_account_service.py`
- Create: `apps/api/tests/services/test_evaluation_game_accounts.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/schemas.py`

- [ ] **Step 1: Write failing normalization and service tests**

Freeze normalization before persistence:

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  PlayerＡ  ", "PlayerA"),
        ("player a", "player a"),
        ("PlayerA", "PlayerA"),
        ("playera", "playera"),
    ],
)
def test_game_id_normalization_uses_nfkc_and_trim_but_preserves_case(
    raw: str,
    expected: str,
) -> None:
    assert normalize_game_id(raw) == expected
```

Case remains significant in this unit because the source projects do not yet expose a verified case-insensitivity rule. Never silently `lower()` or `casefold()` account identifiers.

Add service tests that assert:

- a fresh `StudentProfile.game_account_limit` is 3 across all projects;
- an administrator change requires a reason, cannot go below 3 or active count, and audits old/new integers;
- the same normalized ID in one project has exactly one active owner, while another project accepts the same text;
- unbind retains the original row and permits a different student only at/after `unbound_at`;
- media before unbind remains with the first student and is absent from the second student's authorized history;
- self management succeeds, default guardian fails, finite family allow succeeds, and relationship/override expiry revokes the next request;
- two concurrent fourth binds yield one committed row at most and every final active count is at or below the locked limit;
- auxiliary phone ciphertext differs for identical values while HMAC matches, duplicate students are allowed, and authentication never resolves it;
- replace ends the old row and creates a new row without overwriting ciphertext;
- a digest matching two students returns `ambiguous=True`, no automatically selected student and no names/phones.

- [ ] **Step 2: Run the focused tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_game_accounts.py -q
```

Expected: FAIL because the service and normalization function do not exist.

- [ ] **Step 3: Implement normalization and privacy-safe responses**

```python
def normalize_game_id(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not 2 <= len(normalized) <= 64:
        raise InvalidGameId("game id must contain 2 to 64 characters")
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise InvalidGameId("game id cannot contain control characters")
    return normalized
```

The service stores `EnvelopeCipher.encrypt(normalized, field_name="student_game_id")` and `LookupDigest.digest(normalized)`. Full decrypted game ID may be returned only after object authorization to the student, current authorized family member or administrator; list logs, audit metadata, exceptions and query parameters never contain it. Auxiliary phone responses always return `masked_phone`, never the decrypted value.

- [ ] **Step 4: Implement bind, unbind and limit transactions**

`GameAccountService` must lock the `StudentProfile` row, compute the explicit override or default limit 3, count only `unbound_at IS NULL`, and insert within the same transaction. Translate the partial unique index into `game_id_already_bound`; do not reveal the other student. Unbind locks the row and writes `unbound_at`, `unbound_by_user_id` and reason. It never updates the student/project/digest/ciphertext or deletes history.

Limit changes are administrator-only, require `max_active_accounts >= current active count`, store reason and actor, and append an audit event with old/new integer values only.

- [ ] **Step 5: Implement auxiliary phone lifecycle**

Normalize through existing `normalize_phone`, encrypt with field name `student_auxiliary_phone`, and store HMAC digest. Duplicate digests are allowed. “修改” is one transaction that disables the old row with reason and creates a new row; direct in-place ciphertext replacement is forbidden. Matching returns zero, one or multiple student IDs, and callers may auto-select only the unique result.

Never join auxiliary phone data into `auth.service` or password recovery.

- [ ] **Step 6: Run privacy, identity and concurrency regressions**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_game_accounts.py \
  apps/api/tests/services/test_people.py apps/api/tests/services/test_accounts.py \
  apps/api/tests/api/test_auth_sessions.py -q
.venv/bin/ruff check apps/api/app/evaluations/game_account_service.py \
  apps/api/tests/services/test_evaluation_game_accounts.py
.venv/bin/mypy apps/api/app/evaluations/game_account_service.py
```

Expected: PASS; login remains keyed only by the primary account phone.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/evaluations/game_account_service.py \
  apps/api/app/evaluations/errors.py apps/api/app/evaluations/schemas.py \
  apps/api/tests/services/test_evaluation_game_accounts.py
git commit -m "feat: manage student game accounts"
```

---

### Task 5: Add immutable project analysis profile versions

**Files:**

- Create: `apps/api/app/evaluations/analysis_profile_service.py`
- Create: `apps/api/tests/services/test_analysis_profiles.py`
- Modify: `apps/api/app/teaching/catalog_service.py`
- Modify: `apps/api/tests/services/test_teaching_catalog.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/schemas.py`

- [ ] **Step 1: Write failing version and project-default tests**

Implement named tests with these exact assertions:

- every migrated project has exactly `version=1`, `mode=manual_only`, `publish_mode=review_required`;
- creating a new project creates its v1 profile in the same transaction, and a forced profile insert failure rolls the project back;
- a profile change inserts version 2 and a byte-for-byte snapshot of version 1 remains unchanged;
- two requests with `expected_current_version=1` result in one v2 and one `AnalysisProfileVersionConflict`, never duplicate v2 rows;
- a future-effective profile is absent from `current_at(now)` and selected exactly at its effective instant;
- `manual_only` rejects adapter/rule/threshold fields and `auto_publish`;
- `automatic` rejects every adapter while the accepted registry is empty;
- automatic thresholds are finite `[0,1]` values with keys exactly equal to required fields;
- actor and trimmed 4–300 character reason are required, and audit stores only project/profile/version/`analysis_mode` identifiers.

- [ ] **Step 2: Run focused tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_teaching_catalog.py -q
```

Expected: FAIL because profile creation is not wired to projects.

- [ ] **Step 3: Implement strict profile DTOs**

```python
class AnalysisProfileCreate(BaseModel):
    expected_current_version: int = Field(ge=1)
    mode: Literal["manual_only", "automatic"]
    adapter_id: str | None = Field(default=None, max_length=80)
    adapter_version: str | None = Field(default=None, max_length=40)
    scoring_rule_version: str | None = Field(default=None, max_length=40)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    confidence_thresholds: dict[str, float] = Field(default_factory=dict)
    auto_publish_warning_levels: list[str] = Field(default_factory=list)
    publish_mode: Literal["auto_publish", "review_required"]
    effective_from: datetime
    change_reason: str = Field(min_length=4, max_length=300)
```

For `manual_only`, adapter/rule fields and threshold collections must be empty and `publish_mode` must be `review_required`. For `automatic`, every required field has exactly one finite threshold in `[0, 1]`, warning levels come from a fixed enum, and `(adapter_id, adapter_version)` must exist in an accepted adapter registry. This unit ships the registry empty; automatic activation therefore stays blocked until Task 3 system-evaluation acceptance adds a tested adapter.

- [ ] **Step 4: Implement append-only profile service**

Lock the project and latest profile, compare `expected_current_version`, insert `version + 1`, and never expose update/delete methods. Select current profile as the greatest `effective_from <= occurred_at`, breaking ties by version. Append only safe profile/version IDs to audit metadata; do not audit thresholds or adapter payloads.

Update `TeachingCatalogService.create_project` so project and default `manual_only` v1 profile commit or roll back together. The initial project migration backfill and runtime default must use the same field values.

- [ ] **Step 5: Run analysis and teaching catalog regressions**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_teaching_catalog.py \
  apps/api/tests/api/test_teaching.py -q
.venv/bin/ruff check apps/api/app/evaluations/analysis_profile_service.py \
  apps/api/app/teaching/catalog_service.py \
  apps/api/tests/services/test_analysis_profiles.py
.venv/bin/mypy apps/api/app/evaluations/analysis_profile_service.py \
  apps/api/app/teaching/catalog_service.py
```

Expected: PASS; project creation still passes all existing API tests.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/evaluations/analysis_profile_service.py \
  apps/api/app/evaluations/errors.py apps/api/app/evaluations/schemas.py \
  apps/api/app/teaching/catalog_service.py \
  apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_teaching_catalog.py
git commit -m "feat: version project analysis profiles"
```

---

### Task 6: Build the private image store and protected readers

**Files:**

- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/app/core/config.py`
- Modify: `.env.example`
- Create: `apps/api/app/evaluations/media_store.py`
- Create: `apps/api/app/evaluations/media_service.py`
- Create: `apps/api/tests/services/test_evaluation_media.py`
- Modify: `apps/api/app/evaluations/errors.py`

- [ ] **Step 1: Add Pillow and write failing configuration tests**

Add `Pillow>=12.3,<13` to API dependencies; 12.3.0 is the current verified release and supports Python 3.10+, including the project's Python 3.12 ([PyPI](https://pypi.org/project/pillow/)). Test that:

- development/test may use a relative Git-ignored root;
- production requires absolute, distinct media and quarantine roots;
- roots cannot sit under ERP `apps/web`, `static` or `public` directories;
- max bytes is `20 * 1024 * 1024`, max pixels is `50_000_000` and no video MIME is configured.

Configuration contract:

```python
evaluation_media_root: Path = Path("runtime/evaluation-media")
evaluation_media_quarantine_root: Path = Path("runtime/evaluation-quarantine")
evaluation_media_max_bytes: int = 20 * 1024 * 1024
evaluation_media_max_pixels: int = 50_000_000
evaluation_media_preview_max_edge: int = 1600
```

Resolve relative development/test paths against the ERP repository root derived from `config.py`'s location, never against the caller's current working directory. Production accepts only absolute roots.

- [ ] **Step 2: Write failing synthetic-image tests**

Generate images in memory; never commit real learner media:

Generate one synthetic JPEG, PNG and WebP with known dimensions and metadata. Implement tests that assert:

- each allowed format produces the real MIME/extension, correct byte/pixel metadata and a WebP preview whose EXIF, ICC and text metadata are empty;
- declared extension/MIME mismatch, truncated input, random bytes, byte limit + 1 and pixel limit + 1 all raise the same public `MediaValidationFailed` while recording a safe internal reason;
- every storage key is relative, uses the fixed shard grammar and rejects absolute, `..`, backslash, encoded traversal and symlink escape attempts;
- identical content reuses one blob but creates distinct pending assets without revealing reuse to the second caller;
- a forced database failure removes files created by that call but leaves pre-existing shared blob files intact;
- original/preview responses use real MIME, `private, no-store`, `nosniff` and the constant safe filename;
- unauthorized and random media IDs return the same 404 body;
- original reads append actor/media/outcome only, never hash, storage key, declared filename or path;
- route inspection proves no video or browser/integration upload method exists.

- [ ] **Step 3: Run focused tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_media.py -q
```

Expected: FAIL because media storage classes do not exist.

- [ ] **Step 4: Implement a filesystem-only store boundary**

```python
@dataclass(frozen=True)
class ValidatedImage:
    quarantine_path: Path
    sha256: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    extension: Literal["jpg", "png", "webp"]
    byte_size: int
    width: int
    height: int


@dataclass(frozen=True)
class StoredImageFiles:
    storage_key: str
    preview_storage_key: str
    created_original: bool
    created_preview: bool


class PrivateMediaStore:
    def stage(self, stream: BinaryIO, *, declared_filename: str) -> ValidatedImage:
        raise NotImplementedError

    def persist(self, image: ValidatedImage, *, blob_id: UUID) -> StoredImageFiles:
        raise NotImplementedError

    def resolve(self, storage_key: str) -> Path:
        raise NotImplementedError

    def remove_if_created(self, files: StoredImageFiles) -> None:
        raise NotImplementedError
```

Stream in 1 MiB chunks, stop immediately above the byte limit, hash while writing, and use a random quarantine filename. Derive real format only from Pillow after decoding. Validate `width * height` before full load, call `verify()`, reopen, apply EXIF orientation, convert to RGB/RGBA as needed and encode preview as WebP without EXIF/ICC/text metadata. Final keys use `objects/<2>/<2>/<uuid>.<ext>` and `previews/<2>/<2>/<uuid>.webp`; resolve joins only validated relative components and proves the result remains under the configured root.

Create private directories with mode `0700` and files with mode `0600`, independent of the process umask. At service startup verify media and quarantine roots are writable and on the same filesystem so final `os.replace` operations are atomic; otherwise storage status is not ready and no persistence call proceeds.

- [ ] **Step 5: Implement database coordination and content-object reuse**

`EvaluationMediaService.register_validated_image` must:

1. look up the media blob by SHA-256 without returning that fact to callers;
2. if ready, reuse its ID and remove quarantine input;
3. otherwise persist files, insert one `EvaluationMediaBlob` and one business `EvaluationMediaAsset` in a write transaction;
4. on integrity race, remove only files created by this call, load the winning blob and create the business media row;
5. on any failed database transaction, remove only newly created files;
6. never accept an HTTP `UploadFile` directly in this unit—the third unit owns the upload session and task transaction.

`read_original` and `read_preview` resolve by opaque media ID, call `EvaluationAccessService`, return the same domain error for absent and unauthorized objects, and append an audit event for every original read. No list endpoint exposes storage keys or hashes.

- [ ] **Step 6: Add protected read and storage-status responses**

Add only these browser-safe routes later wired by Task 7:

```text
GET /api/v1/evaluations/media-assets/{media_id}/preview
GET /api/v1/evaluations/media-assets/{media_id}/original
GET /api/v1/evaluations/admin/media-storage/status
```

Responses use `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, a constant safe download filename such as `evaluation-image.<ext>`, and never include an absolute path. The admin status response contains writable/ready booleans and configured limits only; it omits roots, free-space device names and stored object counts.

- [ ] **Step 7: Run media, configuration and privacy checks**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_media.py \
  apps/api/tests/api/test_health.py apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/evaluations/media_store.py \
  apps/api/app/evaluations/media_service.py \
  apps/api/tests/services/test_evaluation_media.py
.venv/bin/mypy apps/api/app/evaluations/media_store.py \
  apps/api/app/evaluations/media_service.py
git diff --check
```

Expected: PASS; test-created files live only under pytest temporary directories.

- [ ] **Step 8: Commit**

```bash
git add apps/api/pyproject.toml apps/api/app/core/config.py .env.example \
  apps/api/app/evaluations/media_store.py \
  apps/api/app/evaluations/media_service.py \
  apps/api/app/evaluations/errors.py \
  apps/api/tests/services/test_evaluation_media.py
git commit -m "feat: build private evaluation media store"
```

---

### Task 7: Expose portal and administrator foundation APIs

**Files:**

- Create: `apps/api/app/evaluations/context_service.py`
- Create: `apps/api/app/evaluations/portal_router.py`
- Create: `apps/api/app/evaluations/admin_router.py`
- Create: `apps/api/tests/api/test_evaluation_foundation.py`
- Modify: `apps/api/app/evaluations/router.py`
- Modify: `apps/api/app/evaluations/schemas.py`
- Modify: `apps/api/app/evaluations/dependencies.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/tests/api/conftest.py`

> **2026-08-02 review correction:** Task 7 owns the four read-only directory routes for game-account projects, staff cohorts, cohort staff scopes and analysis projects. The existing staff directory also accepts an optional `cohort_id`. This corrects page dependencies and scoped authorization only; it does not add upload, OCR, scoring, review, publication, report or survey scope.

- [ ] **Step 1: Freeze the API contract with failing tests**

Test the exact routes from the plan header plus these target/directory helpers:

```text
GET  /api/v1/evaluations/game-account-targets
GET  /api/v1/evaluations/students/{student_id}/game-account-projects?page=&page_size=
GET  /api/v1/evaluations/students/{student_id}/game-accounts?include_unbound=true
POST /api/v1/evaluations/students/{student_id}/game-accounts
POST /api/v1/evaluations/students/{student_id}/game-accounts/{account_id}/unbind
GET  /api/v1/evaluations/admin/students?query=&page=&page_size=
GET  /api/v1/evaluations/admin/students/{student_id}/matching-profile
GET  /api/v1/evaluations/admin/cohorts?query=&page=&page_size=
GET  /api/v1/evaluations/admin/cohorts/{cohort_id}/staff-scopes?scope_kind=&page=&page_size=
GET  /api/v1/evaluations/admin/staff-directory?cohort_id=&query=&page=&page_size=
GET  /api/v1/evaluations/admin/projects?query=&page=&page_size=
GET  /api/v1/evaluations/admin/projects/{project_id}/analysis-profiles
POST /api/v1/evaluations/admin/projects/{project_id}/analysis-profiles
```

Freeze these behaviors:

Implement API tests that assert:

- context capabilities reflect real resources/admin sections while `upload_available` remains literal false;
- student targets contain self, guardian targets contain only current relationships and ended relationships disappear on the next request;
- the target-specific game-account project directory returns only active projects authorized for that student and uses one uniform 404 for missing and unauthorized targets;
- game-account endpoints return the same 404 for an arbitrary student and an existing unauthorized student;
- auxiliary-phone routes require administrator capability and every JSON value remains masked;
- scoped staff managers see only cohorts containing a manageable project/class resource and only the unique manageable project/class scopes inside that cohort;
- missing and unauthorized cohort directories return the same evaluation 404;
- scoped staff-directory calls require `cohort_id`, filter authorized cohort/project/class resources before adding each candidate `staff_person_id`, preserve person-deny precedence, and compute `total` after authorization;
- the analysis project directory returns only projects authorized by `analysis_profiles.manage`, while a global manager may still select an inactive project to inspect its immutable history;
- staff history contains projected coaches plus generic rows exactly once;
- staff/profile writes independently require `cohort_staff.manage` or `analysis_profiles.manage`;
- losing an operation permission returns 403 but `/auth/me` remains authenticated;
- media responses contain fixed privacy headers and one sanitized read audit;
- OpenAPI adds exactly the four new directory GET paths and no additional mutation or media-write operation;
- OpenAPI has no browser/integration media upload operation;
- integration v1 capabilities remain exactly `["auth_bridge", "page_shell"]`.

- [ ] **Step 2: Run the API test and verify it fails**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/api/test_evaluation_foundation.py -q
```

Expected: FAIL because the portal/admin routers and new context fields do not exist.

- [ ] **Step 3: Add context capabilities without trusting client booleans**

Extend the existing context response:

```python
class EvaluationAdminSections(BaseModel):
    cohort_staff: bool
    student_matching: bool
    analysis_profiles: bool


class EvaluationCenterContextResponse(BaseModel):
    accessible: Literal[True] = True
    system_evaluation_available: Literal[True] = True
    embedded_start_url: str
    standalone_start_url: str
    upload_available: Literal[False] = False
    video_available: Literal[False] = False
    reports_available: Literal[False] = False
    surveys_available: Literal[False] = False
    game_accounts_available: bool
    admin_available: bool
    admin_sections: EvaluationAdminSections
    integration_api_version: Literal["v1"] = "v1"
```

`EvaluationContextService` computes game-account targets and admin sections through actual object-aware decisions. `game_accounts_available` is only for self/family targets; administrators use the bounded student search in the admin panel rather than receiving an unbounded target list. These booleans only control navigation; every endpoint repeats authorization and never trusts the client context.

- [ ] **Step 4: Implement portal routes with uniform privacy errors**

Require `evaluation_center.access` first, then object permission. Return:

- `evaluation_access_denied` only when the whole center entry is gone;
- `evaluation_resource_not_found` for absent or unauthorized student/media targets;
- `evaluation_operation_denied` when the object is visible but a write capability was removed;
- `game_account_limit_reached`, `game_id_already_bound` and `game_account_already_unbound` as 409;
- field-specific 422 validation without echoing raw identifiers.

Every successful write and denied sensitive original read appends a sanitized audit event. Do not log request bodies.

- [ ] **Step 5: Implement administrator routes and pagination**

Use existing page bounds `page >= 1`, `1 <= page_size <= 100`. Student search returns only person ID, name and student number; it never returns phone/identity/game ID. The matching-profile detail returns authorized game IDs, masked auxiliary phones and limit. The portal project directory is target-specific and excludes inactive or unauthorized projects. The staff cohort and scope directories are derived from authorized evaluation resources, not from teaching-directory read permissions. Staff directory returns only eligible, non-deleted staff with enabled accounts; global managers may omit `cohort_id`, while scoped-only managers must supply an authorized cohort or receive controlled 422/404 responses. Candidate authorization composes cohort/project/class scope with `staff_person_id` so explicit person denies still win and pagination totals cannot leak denied people. The analysis project directory is filtered by `analysis_profiles.manage`; analysis profile routes remain append-only. Media storage status omits physical paths.

Extend the existing permissions API rather than adding evaluation-specific grant routes. Its DTO now exposes validity and the new scope directories; all old clients without validity fields remain valid for non-evaluation permissions.

- [ ] **Step 6: Mount routers and add dependency factories**

Mount portal paths beneath `/evaluations` and administrator paths beneath `/evaluations/admin`; keep the existing bridge routes in `router.py`. Add service factories for the cipher/lookup/access/staff/game/profile/media services, using the same per-request SQLAlchemy session and app settings.

- [ ] **Step 7: Run API, bridge, permissions and audit regressions**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/api/test_evaluation_foundation.py \
  apps/api/tests/api/test_evaluation_integration.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/evaluations apps/api/tests/api/test_evaluation_foundation.py
.venv/bin/mypy apps/api/app/evaluations apps/api/app/rbac
```

Expected: PASS; the frozen integration v1 JSON remains byte-for-byte unchanged.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/evaluations/context_service.py \
  apps/api/app/evaluations/portal_router.py \
  apps/api/app/evaluations/admin_router.py \
  apps/api/app/evaluations/router.py apps/api/app/evaluations/schemas.py \
  apps/api/app/evaluations/dependencies.py apps/api/app/evaluations/errors.py \
  apps/api/app/api/router.py apps/api/tests/api/conftest.py \
  apps/api/tests/api/test_evaluation_foundation.py
git commit -m "feat: expose evaluation foundation api"
```

---

### Task 8: Build responsive game-account and foundation administration pages

**Files:**

- Create: `apps/web/src/evaluations/useEvaluationContext.ts`
- Create: `apps/web/src/evaluations/foundationApi.ts`
- Create: `apps/web/src/evaluations/foundationApi.test.ts`
- Create: `apps/web/src/evaluations/GameAccountsPage.tsx`
- Create: `apps/web/src/evaluations/GameAccountsPage.test.tsx`
- Create: `apps/web/src/evaluations/EvaluationAdminPage.tsx`
- Create: `apps/web/src/evaluations/EvaluationAdminPage.test.tsx`
- Create: `apps/web/src/evaluations/CohortStaffPanel.tsx`
- Create: `apps/web/src/evaluations/CohortStaffPanel.test.tsx`
- Create: `apps/web/src/evaluations/StudentMatchingPanel.tsx`
- Create: `apps/web/src/evaluations/StudentMatchingPanel.test.tsx`
- Create: `apps/web/src/evaluations/AnalysisProfilesPanel.tsx`
- Create: `apps/web/src/evaluations/AnalysisProfilesPanel.test.tsx`
- Create: `apps/web/src/evaluations/evaluation-foundation.css`
- Modify: `apps/web/src/evaluations/api.ts`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`
- Modify: `apps/web/src/evaluations/useEvaluationAccessError.ts`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/settings/api.ts`
- Modify: `apps/web/src/settings/PermissionsPage.tsx`
- Modify: `apps/web/src/settings/PermissionsPage.test.tsx`
- Modify: `apps/web/src/settings/settings.css`
- Modify: `apps/web/src/teaching/CohortDetailPage.tsx`
- Modify: `apps/web/src/teaching/CohortDetailPage.test.tsx`
- Modify: `apps/web/src/people/PersonDetailPage.tsx`
- Modify: `apps/web/src/people/PersonDetailPage.test.tsx`

- [ ] **Step 1: Write failing API client and query-key tests**

Freeze exact URLs, bodies, pagination serialization and account-scoped cache keys. Types must match the Appendix A DTOs. No client function may accept or return media storage paths, hashes or full auxiliary phones.

```typescript
export const evaluationFoundationKeys = {
  root: (accountId: string) => ["evaluations", accountId, "foundation"] as const,
  targets: (accountId: string) =>
    ["evaluations", accountId, "foundation", "game-account-targets"] as const,
  gameAccounts: (accountId: string, studentId: string) =>
    ["evaluations", accountId, "foundation", "game-accounts", studentId] as const,
  gameAccountProjects: (accountId: string, studentId: string) =>
    ["evaluations", accountId, "foundation", "game-account-projects", studentId] as const,
  admin: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin"] as const,
  staffCohorts: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin", "cohorts"] as const,
  staffScopes: (accountId: string, cohortId: string, scopeKind: "project" | "class") =>
    ["evaluations", accountId, "foundation", "admin", "cohorts", cohortId,
      "staff-scopes", scopeKind] as const,
  analysisProjects: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin", "analysis-projects"] as const,
}
```

- [ ] **Step 2: Write failing component and router tests**

Cover:

```text
EvaluationCenterPage
- displays My game IDs only when game_accounts_available
- displays Foundation administration only for an enabled admin section
- never displays an upload control while upload_available=false

GameAccountsPage
- self default, current-family switcher, active limit and unbound history
- target-specific project options come only from the evaluation game-account project directory
- retains typed ID after 409 and shows a useful non-leaking conflict
- requires an unbind reason and invalidates only the target/account queries
- removes a family target on the next context refetch after relationship/grant expiry
- hides cached family targets and complete game IDs throughout context/target revalidation

EvaluationAdminPage and panels
- cohort, staff scope and analysis project selectors use only evaluation-owned directories
- scoped-only staff managers may assign existing roles but cannot create or deactivate global roles
- staff role/assignment create and end with history preserved
- student search, game limit, full authorized game ID and masked auxiliary phones
- current and historical profile versions, append-only creation
- pending mutations lock duplicate submit and close actions

PermissionsPage
- cohort/project/class/enrollment/person/self/family scope choices
- required effective period and reason for sensitive evaluation allows
- preview and create keep existing non-evaluation override behavior

Router
- direct child route requires server context capability, not only local permission array
- capability loss returns to /evaluations without logging the user out
```

- [ ] **Step 3: Run the focused Web tests and verify they fail**

```bash
npm --prefix apps/web test -- \
  src/evaluations/foundationApi.test.ts \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/evaluations/GameAccountsPage.test.tsx \
  src/evaluations/EvaluationAdminPage.test.tsx \
  src/evaluations/CohortStaffPanel.test.tsx \
  src/evaluations/StudentMatchingPanel.test.tsx \
  src/evaluations/AnalysisProfilesPanel.test.tsx \
  src/settings/PermissionsPage.test.tsx src/app/router.test.tsx
```

Expected: FAIL because the pages, types and routes do not exist.

- [ ] **Step 4: Implement shared context and error semantics**

Move the existing context query into `useEvaluationContext`. `useEvaluationAccessError` may clear all evaluation caches and redirect only for `evaluation_access_denied`; an object-level 403/404 remains on the page and preserves the ERP session. On account change or logout, all old-account evaluation query roots are removed.

Game-account targets remain enabled while an existing successful context is revalidated. After each successful context update after the initial load, issue exactly one explicit target refresh. While context or targets are fetching, or the context `dataUpdatedAt` is newer than the target data, render only the permission-refresh state: do not expose cached family target names, selectors, dialogs, account histories or complete game IDs. Keep the same target query mounted throughout this sequence so context revalidation cannot cause a disable/re-enable duplicate request.

- [ ] **Step 5: Implement the game-account page**

Desktop/tablet render a compact history table; 360px renders semantic cards with `data-label`. The target selector shows only the server-provided self/family list. The bind project selector calls `/evaluations/students/{student_id}/game-account-projects` for the currently selected target and never depends on teaching catalog reads. Display active count/limit, project, authorized game ID, bind time and unbind history. Bind and unbind use accessible dialogs, keep inputs on recoverable errors and refetch permission/context after any 403.

- [ ] **Step 6: Implement focused administration panels**

Keep the page split into three modules:

- `CohortStaffPanel`: evaluation-owned cohort filter and project/class scope directories, cohort-scoped staff candidates, existing primary/assistant read-only rows, custom role, effective dates, assignment permissions and end reason. Creating/deactivating global role definitions requires the global `user.permissions` entry for `cohort_staff.manage`; a scoped permission alone may assign an existing role only;
- `StudentMatchingPanel`: paginated student search, game ID history, limit adjustment reason, masked auxiliary phones and replace/deactivate actions;
- `AnalysisProfilesPanel`: evaluation-owned, authorization-filtered project selector, immutable history and a new-version form. Because the accepted adapter registry is empty, automatic mode is visibly unavailable with an explanation; it must not send a bypass request.

Reuse `CohortStaffPanel` as the “工作人员” tab in `CohortDetailPage`, and reuse `StudentMatchingPanel` in the student `PersonDetailPage` for administrators. Avoid copying mutation logic.

- [ ] **Step 7: Extend the existing permission center**

Add scope directories and effective-period/reason controls to `PermissionsPage`; do not create a second evaluation permission page. Hide evaluation permissions whose business unit is not implemented. Show the effect, scope label and validity in the existing access table. Keep the current self-lockout and last-admin protections.

- [ ] **Step 8: Add responsive styling and accessibility checks**

At 1440/1024 use table + side detail; at 768 allow stacked panels; at 360 use cards and full-screen dialogs above the existing mobile navigation. Every dialog has `aria-modal`, stable labelled title, Escape close when idle, initial focus, disabled duplicate submit and reachable cancel/submit actions. No horizontal scroll is allowed.

- [ ] **Step 9: Run Web tests, build and lint**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all Vitest files pass; TypeScript/Vite and ESLint exit 0.

- [ ] **Step 10: Commit**

```bash
git add apps/web/src/evaluations apps/web/src/app/router.tsx \
  apps/web/src/app/router.test.tsx apps/web/src/settings/api.ts \
  apps/web/src/settings/PermissionsPage.tsx \
  apps/web/src/settings/PermissionsPage.test.tsx apps/web/src/settings/settings.css \
  apps/web/src/teaching/CohortDetailPage.tsx \
  apps/web/src/teaching/CohortDetailPage.test.tsx \
  apps/web/src/people/PersonDetailPage.tsx \
  apps/web/src/people/PersonDetailPage.test.tsx
git commit -m "feat: manage evaluation foundation data"
```

---

### Task 9: Add deterministic fixtures, media manifest and four-viewport acceptance

**Files:**

- Create: `apps/api/app/cli/evaluation_media_manifest.py`
- Create: `apps/api/tests/cli/test_evaluation_media_manifest.py`
- Create: `apps/web/tests/evaluation-data-media-foundation.spec.ts`
- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/tests/helpers.ts`
- Modify: `scripts/run-evaluation-page-e2e.sh`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md`

- [ ] **Step 1: Write failing seed and media-manifest tests**

The E2E seed must deterministically create:

- one active student with limit 3, two active IDs and one unbound history row;
- one default-denied guardian plus an optional finite family permission fixture;
- one life teacher, one custom role and current/ended project/class assignments;
- `manual_only` v1 profiles for all E2E projects;
- no real media files and no automatic adapter.

Manifest tests create synthetic temp-root rows/files and assert four result classes: registered-and-valid, database-row-missing-file, hash-mismatch, and unregistered-orphan. The command is read-only by default and never deletes; `--json-output` must resolve under an explicitly configured Git-ignored directory and create mode `0600`.

- [ ] **Step 2: Run focused CLI tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/cli/test_evaluation_media_manifest.py \
  apps/api/tests/cli/test_seed_e2e.py apps/api/tests/cli/test_operations_docs.py -q
```

Expected: FAIL because the manifest and fixture rows do not exist.

- [ ] **Step 3: Implement read-only reconciliation output**

The manifest hashes files in bounded chunks, compares only database-declared relative keys, reports counts plus opaque IDs, and never emits full paths, game IDs, names, phones or media contents. Missing and mismatch statuses may update database state only when a separate explicit `--record-status` flag is supplied in a test/dev environment; even then it never deletes files.

- [ ] **Step 4: Write the two Playwright journeys**

`evaluation-data-media-foundation.spec.ts` contains:

1. `admin manages evaluation foundation` — create a viewport-suffixed custom role and assignment, end it and see history; change one viewport-suffixed student's limit; add/replace a masked auxiliary phone; inspect manual profile history.
2. `student and unauthorized roles see only allowed actions` — student binds and unbinds a viewport-suffixed game ID; guardian default has no edit action; customer service has no evaluation entry.

Both use `expectNoHorizontalOverflow`, `expectHorizontallyInsideViewport`, mobile-navigation clearance and semantic card/table assertions. Keep `evaluation-page-integration.spec.ts` unchanged as the eight-test bridge/read-only regression.

- [ ] **Step 5: Implement fixtures and update the local launcher**

Update the launcher to run both specs:

```bash
E2E_BASE_URL="$web_origin" E2E_SCORE_ORIGIN="$score_browser_origin" \
npm --prefix apps/web run test:e2e -- \
  evaluation-page-integration.spec.ts \
  evaluation-data-media-foundation.spec.ts
```

Expected final browser count: 16 passes, two journeys from each spec across four viewport projects. The script still starts only local processes and never invokes Docker.

- [ ] **Step 6: Document exact delivered and deferred behavior**

README must state:

- game ID, auxiliary phone, staff history and manual analysis profiles are available;
- private media storage/validation/read primitives are present, with the manifest command;
- screenshot upload, tasks, OCR/score, review and publication remain closed;
- old score-tool data is untouched;
- Docker/NAS remains deferred.

Update design section 20 with this plan link and status “第二实施单元本地实现与非 Docker 门禁已完成” only after Task 10 passes. Before that use “第二实施单元计划已编写，待执行”.

- [ ] **Step 7: Run CLI and both browser gates**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/cli/test_evaluation_media_manifest.py \
  apps/api/tests/cli/test_seed_e2e.py apps/api/tests/cli/test_operations_docs.py -q
bash -n scripts/run-evaluation-page-e2e.sh
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool \
  scripts/run-evaluation-page-e2e.sh
```

Expected: CLI tests PASS and Playwright reports 16 passes. Record that Docker was not invoked.

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/cli/evaluation_media_manifest.py \
  apps/api/tests/cli/test_evaluation_media_manifest.py \
  apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/api/tests/cli/test_operations_docs.py \
  apps/web/tests/evaluation-data-media-foundation.spec.ts \
  apps/web/tests/helpers.ts scripts/run-evaluation-page-e2e.sh \
  README.md docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md
git commit -m "test: cover evaluation data foundation"
```

---

### Task 10: Run full gates, review boundaries and prepare local integration

**Files:**

- Modify only if a gate finds a defect in files already owned by Tasks 1–9.

- [ ] **Step 1: Run the complete ERP backend gate**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
```

Expected: all backend tests pass; Ruff and mypy exit 0.

- [ ] **Step 2: Run the complete Web and script gate**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
bash -n scripts/db-schema-manifest.sh
bash -n scripts/run-evaluation-page-e2e.sh
git diff --check
```

Expected: all unit tests pass; build, lint, shell syntax and whitespace checks exit 0.

- [ ] **Step 3: Re-run the local three-service browser gate**

```bash
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
E2E_API_PORT=18000 E2E_WEB_PORT=15175 E2E_SCORE_PORT=18010 \
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
FIELD_ENCRYPTION_MASTER_KEY="${FIELD_ENCRYPTION_MASTER_KEY:?}" \
FIELD_LOOKUP_KEY="${FIELD_LOOKUP_KEY:?}" \
SESSION_TOKEN_PEPPER="${SESSION_TOKEN_PEPPER:?}" \
EVALUATION_TOOL_SERVICE_TOKEN="${EVALUATION_TOOL_SERVICE_TOKEN:?}" \
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool \
scripts/run-evaluation-page-e2e.sh
```

Expected: 16 passed and all three temporary service ports are free after cleanup.

- [ ] **Step 4: Prove the independent score-tool contract is unchanged**

```bash
cmp apps/api/tests/contracts/evaluation_integration_v1.json \
  /Users/skymoonzyj/Documents/game-score-tool/tests/contracts/evaluation_integration_v1.json
git -C /Users/skymoonzyj/Documents/game-score-tool status --short --branch
git -C /Users/skymoonzyj/Documents/game-score-tool diff --check
```

Expected: contracts match and score-tool working tree has no changes from this unit.

- [ ] **Step 5: Prove deployment and legacy data boundaries**

```bash
git diff --name-status HEAD~9..HEAD -- \
  Dockerfile compose.yml compose.yaml docker-compose.yml docker-compose.yaml \
  data uploads runtime backups
```

Expected: no output. Also inspect `git status --ignored --short` only to confirm ignored local media paths remain outside version control; do not enumerate, open or delete old score-tool files.

- [ ] **Step 6: Run requirement and code-quality reviews**

Perform two reviews:

1. specification review against unit 2 only, explicitly rejecting task/OCR/review/report/survey scope;
2. code-quality/security review for authorization, encryption, path traversal, decompression bombs, file/DB compensation, audit leakage, concurrent uniqueness and responsive access.

Any finding is fixed with a failing regression test first, then all affected focused and full gates are rerun.

- [ ] **Step 7: Update final status and commit review fixes**

Only after all fresh gates pass, update the design status and README from “计划已编写” to “本地实现与非 Docker 门禁已完成”. Commit any review fixes and status update with exact affected files.

- [ ] **Step 8: Offer local branch completion choices**

Use `superpowers:finishing-a-development-branch`; do not push, merge, delete the worktree or modify Docker unless the user chooses the corresponding safe option.

---

## Appendix A: Frozen public DTOs

```typescript
export type EvaluationFoundationContext = {
  accessible: true
  system_evaluation_available: true
  embedded_start_url: string
  standalone_start_url: string
  upload_available: false
  video_available: false
  reports_available: false
  surveys_available: false
  game_accounts_available: boolean
  admin_available: boolean
  admin_sections: {
    cohort_staff: boolean
    student_matching: boolean
    analysis_profiles: boolean
  }
  integration_api_version: "v1"
}

export type GameAccountTarget = {
  student_person_id: string
  student_name: string
  student_number: string
  access_source: "self" | "family"
  active_count: number
  active_limit: number
}

export type EvaluationProjectDirectoryItem = {
  id: string
  name: string
  is_active: boolean
}

export type EvaluationCohortDirectoryItem = {
  id: string
  name: string
}

export type EvaluationStaffScopeDirectoryItem = {
  id: string
  name: string
  project_id: string
  project_name: string
  is_active: boolean
}

export type FoundationPage<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type StudentGameAccount = {
  id: string
  project_id: string
  project_name: string
  game_id: string
  status: "active" | "unbound"
  valid_from: string
  unbound_at: string | null
  unbind_reason: string | null
}

export type AuxiliaryPhone = {
  id: string
  masked_phone: string
  status: "active" | "inactive"
  created_at: string
  disabled_at: string | null
}

export type StaffServiceItem = {
  id: string
  source: "coach_assignment" | "cohort_staff_assignment"
  staff_person_id: string
  staff_name: string
  role_name: string
  role_category: "teaching" | "life_service" | "operations"
  scope_kind: "project" | "class"
  project_id: string
  project_name: string
  class_id: string | null
  class_name: string | null
  permission_keys: string[]
  valid_from: string
  valid_until: string | null
  ended_at: string | null
  end_reason: string | null
  manage_allowed: boolean
}

export type AnalysisProfile = {
  id: string
  project_id: string
  project_name: string
  version: number
  mode: "manual_only" | "automatic"
  adapter_id: string | null
  adapter_version: string | null
  scoring_rule_version: string | null
  required_fields: string[]
  confidence_thresholds: Record<string, number>
  auto_publish_warning_levels: string[]
  publish_mode: "auto_publish" | "review_required"
  effective_from: string
  configured_by_name: string | null
  change_reason: string
  is_current: boolean
}
```

## Appendix B: Error code contract

| Code | HTTP | Meaning |
| --- | ---: | --- |
| `evaluation_access_denied` | 403 | Whole evaluation-center entry was removed |
| `evaluation_resource_not_found` | 404 | Object absent or outside actor scope |
| `evaluation_operation_denied` | 403 | Visible object but requested mutation capability absent |
| `game_account_limit_reached` | 409 | Active cross-project limit reached |
| `game_id_already_bound` | 409 | Active project ID conflict; no owner disclosed |
| `game_account_already_unbound` | 409 | History row is already closed |
| `staff_assignment_conflict` | 409 | Overlapping same staff/role/scope service |
| `analysis_profile_version_conflict` | 409 | Stale expected current version |
| `analysis_adapter_not_accepted` | 409 | Automatic adapter not yet accepted |
| `media_asset_not_found` | 404 | Media absent or unauthorized |
| `media_validation_failed` | 422 | Invalid type, bytes, pixels or decode; detail is generic |

## Appendix C: Explicit deferrals

- Browser/score-tool screenshot upload and multipart endpoints.
- Evaluation jobs, per-task three-image limit, idempotency key and worker lease.
- Identity OCR, scoring OCR, automatic matching and manual match queue.
- OCR/score adapters, accepted sample registry and real-game golden images.
- Review, publish, revision and published history.
- Daily/weekly reports and camp surveys.
- Video upload, queue, codecs and GPU processing.
- Docker images, Compose services, NAS volume paths and joint database/media restore.
- Migration, reading or deletion of old score-tool SQLite, screenshots, videos or runtime data.

## Appendix D: Frozen database field map

### Existing-table extensions

| Table | Columns/constraints added |
| --- | --- |
| `student_profiles` | `game_account_limit INTEGER NOT NULL DEFAULT 3`; check `game_account_limit >= 3` |
| `user_permission_overrides` | `valid_from TIMESTAMPTZ NULL`, `valid_until TIMESTAMPTZ NULL`, `created_by_user_id UUID NULL REFERENCES user_accounts RESTRICT`, `grant_reason VARCHAR(200) NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`; new scopes `cohort`, `project`, `enrollment`; validity check `valid_until IS NULL OR valid_from IS NOT NULL AND valid_until > valid_from` |
| `enrollments` | unique `(id, student_person_id)` for media target integrity |

Legacy permission overrides keep null validity/actor/reason and remain indefinite. New sensitive evaluation allows cannot use that legacy shape through the application service.

### `evaluation_staff_roles`

| Column | Type/rule |
| --- | --- |
| `id` | UUID primary key |
| `key` | VARCHAR(40), unique, lowercase snake-case |
| `name` | VARCHAR(80), non-blank |
| `category` | `teaching`, `life_service` or `operations` |
| `is_builtin` / `is_active` | BOOLEAN NOT NULL |
| `created_by_user_id` | nullable only for deterministic built-in row; FK RESTRICT |
| `created_at` / `updated_at` | timezone-aware timestamps |

Seed `life_teacher` with `41000000-0000-4000-8000-000000000001`. Once referenced, key/name/category cannot change; only `is_active` may become false.

### `cohort_staff_assignments`

| Column | Type/rule |
| --- | --- |
| `id` | UUID primary key |
| `cohort_id` / `role_id` / `staff_person_id` | UUID FK RESTRICT |
| `project_id` / `class_id` | UUID; exactly one non-null; class uses composite `(class_id, cohort_id)` FK |
| `role_name_snapshot` / `role_category_snapshot` | immutable VARCHAR values copied on creation |
| `valid_from` / `valid_until` | TIMESTAMPTZ; end greater than start |
| `ended_at` / `ended_by_user_id` / `end_reason` | all null or all non-null; end greater than start |
| `created_by_user_id` / `updated_by_user_id` | UUID FK RESTRICT |
| timestamps | `created_at`, `updated_at` |

Two GiST exclusions use effective `[valid_from, min(valid_until, ended_at))` ranges to prevent overlap for the same staff/role/project or staff/role/class. Adjacent intervals are valid.

### `cohort_staff_assignment_permissions`

Composite primary key `(assignment_id, permission_id)`, plus `created_by_user_id` and `created_at`. All foreign keys use RESTRICT. Application allowlist contains only `evaluation_media.upload` and `evaluation_media.original.read` in this unit.

### `student_game_accounts`

| Column | Type/rule |
| --- | --- |
| `id` | UUID primary key |
| `student_person_id` / `project_id` | UUID FK RESTRICT |
| `identifier_ciphertext` | TEXT envelope ciphertext |
| `identifier_digest` | CHAR(64) keyed lookup digest |
| `valid_from` | TIMESTAMPTZ NOT NULL |
| `unbound_at` / `unbound_by_user_id` / `unbind_reason` | all null or all non-null; end after start |
| `created_by_user_id` | UUID FK RESTRICT |
| timestamps | `created_at`, `updated_at` |

Partial unique `(project_id, identifier_digest) WHERE unbound_at IS NULL`; GiST prevents overlapping historical intervals for the same project/digest. Current-student and historical-match indexes follow the fields in Task 1.

### `student_auxiliary_phones`

`id`, `student_person_id`, `phone_ciphertext`, `phone_digest`, `valid_from`, `disabled_at`, `disabled_by_user_id`, `disable_reason`, `created_by_user_id`, timestamps. Disable fields are all null or all non-null. Partial unique `(student_person_id, phone_digest) WHERE disabled_at IS NULL`; non-unique lookup index `(phone_digest, student_person_id) WHERE disabled_at IS NULL` permits multiple students to share one phone.

### `project_analysis_profiles`

| Column | Type/rule |
| --- | --- |
| `id` | UUID primary key |
| `project_id` / `version` | FK RESTRICT; unique pair; version >= 1 |
| `mode` | `manual_only` or `automatic` |
| `adapter_id` / `adapter_version` / `scoring_rule_version` | nullable VARCHAR; all absent in manual mode |
| `required_fields` | JSONB array |
| `confidence_thresholds` | JSONB object |
| `auto_publish_warning_levels` | JSONB array |
| `publish_mode` | `auto_publish` or `review_required`; manual forces review |
| `effective_from` | TIMESTAMPTZ NOT NULL |
| `configured_by_user_id` | nullable only for migration backfill; FK RESTRICT |
| `change_reason` | VARCHAR(300), non-blank |
| timestamps | `created_at`, `updated_at`; application never updates a version row |

### `evaluation_media_blobs`

`id`, unique opaque relative `storage_key`, unique relative `preview_storage_key`, unique SHA-256, real MIME, canonical extension, `size_bytes`, `width_pixels`, `height_pixels`, `storage_status`, `validation_status`, `retention_status`, timestamps. Checks enforce MIME/extension mapping, byte range `1..20 MiB`, positive dimensions and pixel product at most 50,000,000. No original filename or absolute path is stored.

### `evaluation_media_assets`

`id`, `blob_id`, `uploaded_by_user_id`, `project_id`, nullable `student_person_id`, `enrollment_id`, `class_id`, `matched_at`, `matched_by_user_id`, `match_reason`, `delegated_upload_reason`, timestamps. Target and match fields are either all null or all non-null. `(enrollment_id, student_person_id)` references the enrollment composite unique key; class is a match-time snapshot. A trigger permits exactly one pending-to-matched transition and rejects later target rewrites. Partial unique `(student_person_id, enrollment_id, project_id, blob_id) WHERE enrollment_id IS NOT NULL` enforces business deduplication after match.
