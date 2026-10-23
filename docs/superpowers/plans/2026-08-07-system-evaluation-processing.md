# System Evaluation Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付本地可完整运行的系统评价截图流程：获权用户上传 1–3 张截图，ERP 原子保存任务和私有媒体，评分 Worker 处理或转人工，独立审核人发布不可变结果，学员、家庭和工作人员按对象范围查看历史。

**Architecture:** ERP PostgreSQL 是任务、媒体归属、租约、结果、审核、发布和版本历史的唯一主记录；ERP API 负责授权、状态机与全部写入。`game-score-tool` 仅提供独立/内嵌 Web 页面和无状态 Worker，分别使用 Web 与 Worker 服务身份调用 ERP v2 契约；三个已有算法适配项目首版都强制人工审核，其他项目进入人工录入。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic 2、Pillow、httpx、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright

---

## Plan orientation

本计划执行时遵守以下边界：

- ERP 工作树固定为 `/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing`，分支为 `codex/system-evaluation-processing`。
- 评分工具必须从 `/Users/skymoonzyj/Documents/game-score-tool@20e1a38` 建立独立工作树和 `codex/system-evaluation-processing` 分支后再改代码。
- 不修改 Docker、Compose、NAS、生产媒体卷、备份恢复或 GPU 配置。
- 不读取、不迁移、不改写、不删除评分工具旧 `data/`、`uploads/`、`runtime/`、SQLite、旧截图或旧录像。
- 录像入口保持禁用；日报、周报、营期问卷和打印继续延期。
- 所有新行为先写失败测试并观察预期失败，再写最小实现；每个任务单独提交。
- PostgreSQL 测试显式使用 `PYTHONPATH=/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing/apps/api`、隔离库 `postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test` 和 `-p no:cacheprovider`。
- 私有对象的不存在、已删除、停用、不合格、拒绝和错误范围统一为相同 404；账号完全没有相应能力时返回 403。授权选择器必须先于媒体、任务、幂等和审计写入。
- 审计元数据只记录安全 UUID、状态、版本、数量、动作和公开错误码，不记录手机号、身份证、完整游戏 ID、图片、OCR 原文、备注、驳回原因或建议正文。

## Responsibility map and dependency graph

| Task | Boundary | Depends on | Repository |
| --- | --- | --- | --- |
| 1 | 模型、迁移、RBAC、状态机 | 已确认规格 | ERP |
| 2 | 上传、原子媒体归属、匹配、任务创建 | Task 1 | ERP |
| 3 | Worker 租约、结果模式、重试、并发 | Task 1 | ERP |
| 4 | 人工录入、审核、发布、修订、查询投影 | Tasks 1–3 | ERP |
| 5 | 评分工具 v2 Web 客户端和上传页 | Task 2 的冻结契约 | score tool |
| 6 | 评分 Worker、三个适配器和黄金测试 | Task 3 的冻结契约 | score tool |
| 7 | ERP 响应式任务、审核和历史页面 | Task 4 | ERP |
| 8 | 双仓契约、启动器、四视口 E2E、总门禁 | Tasks 1–7 | 两仓库 |

实现任务按依赖推进；只读探索和不同阶段的审查可以并发，写实现的子任务不得同时修改共享契约或共享文件。

## Frozen route summary

### ERP account and staff routes

```text
GET    /api/v1/evaluations/system/context
POST   /api/v1/evaluations/system/upload-sessions
PUT    /api/v1/evaluations/system/upload-sessions/{session_id}/slots/{slot_key}
DELETE /api/v1/evaluations/system/upload-sessions/{session_id}/slots/{slot_key}
POST   /api/v1/evaluations/system/upload-sessions/{session_id}/tasks
GET    /api/v1/evaluations/system/tasks?status=&cohort_id=&project_id=&class_id=&student_id=&page=&page_size=
GET    /api/v1/evaluations/system/tasks/{task_id}
GET    /api/v1/evaluations/system/tasks/{task_id}/media/{media_id}/preview
POST   /api/v1/evaluations/system/tasks/{task_id}/match
PUT    /api/v1/evaluations/system/tasks/{task_id}/manual-draft
POST   /api/v1/evaluations/system/tasks/{task_id}/submit-review
POST   /api/v1/evaluations/system/tasks/{task_id}/corrections
POST   /api/v1/evaluations/system/tasks/{task_id}/revisions
```

### ERP administrator and reviewer routes

```text
GET    /api/v1/evaluations/admin/system-tasks?queue=&cohort_id=&project_id=&class_id=&student_id=&page=&page_size=
GET    /api/v1/evaluations/admin/system-tasks/{task_id}
POST   /api/v1/evaluations/admin/system-tasks/{task_id}/match
PUT    /api/v1/evaluations/admin/system-tasks/{task_id}/manual-draft
POST   /api/v1/evaluations/admin/system-tasks/{task_id}/reviews
POST   /api/v1/evaluations/admin/system-tasks/{task_id}/archive
POST   /api/v1/evaluations/admin/system-tasks/{task_id}/reopen
GET    /api/v1/evaluations/admin/adapter-certifications
POST   /api/v1/evaluations/admin/adapter-certifications
```

### Integration v2 routes

```text
GET    /api/v1/integrations/evaluations/v2/capabilities
POST   /api/v1/integrations/evaluations/v2/web/session-context
GET    /api/v1/integrations/evaluations/v2/web/upload-context
POST   /api/v1/integrations/evaluations/v2/web/upload-sessions
PUT    /api/v1/integrations/evaluations/v2/web/upload-sessions/{session_id}/slots/{slot_key}
DELETE /api/v1/integrations/evaluations/v2/web/upload-sessions/{session_id}/slots/{slot_key}
POST   /api/v1/integrations/evaluations/v2/web/upload-sessions/{session_id}/tasks
GET    /api/v1/integrations/evaluations/v2/web/tasks
GET    /api/v1/integrations/evaluations/v2/web/tasks/{task_id}
POST   /api/v1/integrations/evaluations/v2/worker/claims
POST   /api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/renew
GET    /api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/media/{media_id}
POST   /api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/results
POST   /api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/failures
```

v1 登录、票据交换与 `game-score-tool.ready` 消息保持完全兼容。v2 Web 路由只接受 Web 服务身份和短期委派会话；v2 Worker 路由只接受 Worker 服务身份，二者凭据不可互换。

---

### Task 1: Add the system-evaluation schema, permissions, and state machine

**Files:**

- Create: `apps/api/app/evaluations/task_models.py`
- Create: `apps/api/app/evaluations/processing_models.py`
- Create: `apps/api/app/evaluations/review_models.py`
- Create: `apps/api/app/evaluations/task_state.py`
- Create: `apps/api/app/evaluations/adapter_registry.py`
- Create: `apps/api/migrations/versions/0012_system_evaluation_processing.py`
- Create: `apps/api/tests/unit/evaluations/test_task_state.py`
- Create: `apps/api/tests/unit/evaluations/test_adapter_registry.py`
- Create: `apps/api/tests/db/test_system_evaluation_models.py`
- Modify: `apps/api/app/evaluations/models.py`
- Modify: `apps/api/app/evaluations/analysis_models.py`
- Modify: `apps/api/app/evaluations/analysis_profile_service.py`
- Modify: `apps/api/app/evaluations/staff_service.py`
- Modify: `apps/api/app/admissions/models.py`
- Modify: `apps/api/app/teaching/models.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/services/test_analysis_profiles.py`
- Modify: `apps/api/tests/services/test_evaluation_staff.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing state-machine tests**

Create parameterized tests that freeze every allowed edge and reject direct automatic publication:

```python
ALLOWED = {
    ("uploaded", "identifying"),
    ("identifying", "pending_match"),
    ("identifying", "queued"),
    ("identifying", "manual_input"),
    ("pending_match", "queued"),
    ("pending_match", "manual_input"),
    ("queued", "processing"),
    ("processing", "needs_review"),
    ("processing", "failed"),
    ("failed", "queued"),
    ("failed", "manual_input"),
    ("failed", "archived"),
    ("manual_input", "needs_review"),
    ("needs_review", "published"),
    ("needs_review", "rejected"),
    ("rejected", "correction_pending"),
    ("rejected", "archived"),
    ("correction_pending", "needs_review"),
    ("correction_pending", "queued"),
    ("archived", "correction_pending"),
    ("archived", "queued"),
    ("published", "revision_draft"),
    ("revision_draft", "queued"),
    ("revision_draft", "manual_input"),
}

@pytest.mark.parametrize(("source", "target"), sorted(ALLOWED))
def test_all_documented_task_transitions_are_allowed(source: str, target: str) -> None:
    assert require_transition(source, target) == target

def test_automatic_processing_cannot_publish_in_the_first_release() -> None:
    with pytest.raises(InvalidEvaluationTransition):
        require_transition("processing", "published")
```

- [ ] **Step 2: Write failing migration, model, and permission tests**

Freeze these nine new tables:

```python
SYSTEM_EVALUATION_TABLES = {
    "system_evaluation_upload_sessions",
    "system_evaluation_upload_session_media",
    "system_evaluation_tasks",
    "system_evaluation_task_media",
    "system_evaluation_attempts",
    "system_evaluation_result_versions",
    "system_evaluation_events",
    "evaluation_adapter_certifications",
    "evaluation_idempotency_keys",
}

SYSTEM_EVALUATION_PERMISSION_IDS = {
    "evaluation_jobs.review": UUID("20000000-0000-4000-8000-000000000029"),
    "evaluation_jobs.match": UUID("20000000-0000-4000-8000-000000000044"),
    "evaluation_jobs.manual_input": UUID("20000000-0000-4000-8000-000000000045"),
    "evaluation_adapters.certify": UUID("20000000-0000-4000-8000-000000000046"),
}
```

Assert admin/global is the only role grant for all four permissions; scoped staff permissions remain explicit assignment/override records, not broad role grants. Assert upload media `(session_id, position)` and `(session_id, slot_key)`, task `(public_number)`, task media `(task_id, position)` and `(task_id, slot_key)`, attempt `(task_id, attempt)`, result `(task_id, version)`, event `(task_id, sequence)` and idempotency `(scope, actor_key, key_digest)` are unique. Assert positions are 1–3; status/source/review decision/severity/score/range checks exist; all business foreign keys use `RESTRICT`; no cascade can delete a task, result, event, certification or media association.

- [ ] **Step 3: Run RED tests**

```bash
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing'
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/unit/evaluations/test_task_state.py \
  apps/api/tests/unit/evaluations/test_adapter_registry.py \
  apps/api/tests/db/test_system_evaluation_models.py \
  apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_evaluation_staff.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: FAIL because `task_state`, revision `0012`, the nine tables, permission 029 and permissions 44–46 do not exist.

- [ ] **Step 4: Implement focused immutable models and transition validation**

`task_models.py` defines upload sessions, upload-session media, tasks, task media, events and idempotency. `SystemEvaluationUploadSessionMedia` stores only opaque quarantine keys and validated metadata until final confirmation; successful task creation promotes all slots in one caller-owned transaction, and expiry cleanup removes abandoned quarantine rows/files. `SystemEvaluationTask` holds selected student/enrollment/cohort/project/class, upload/config snapshot, optimistic `lock_version`, queue counters, current/published result pointers and timestamps. `SystemEvaluationTaskMedia` binds one existing `EvaluationMediaAsset` to one named slot.

`processing_models.py` defines attempts and certifications. `SystemEvaluationAttempt` stores only the lease-token HMAC digest. `EvaluationAdapterCertification` freezes manifest digest, project, adapter/rule/schema versions, certifier and certification time. `review_models.py` defines immutable result versions with JSONB structured fields/confidences/warnings/components, score, grade, advice, author/reviewer/publication fields and `previous_version_id`. `SystemEvaluationEvent` is append-only, and `EvaluationIdempotencyKey` stores only a domain-separated payload digest and response resource UUID.

Extend `Enrollment` with a unique `(id, student_person_id, cohort_id, class_id)` target and `TeachingClass` with unique `(id, cohort_id, project_id)` so task composite foreign keys prove enrollment/class/project consistency in PostgreSQL. Extend `ProjectAnalysisProfile` with `media_slots` and `result_schema_version`; relax only the old `manual_only -> required_fields=[]` restriction so manual-only projects may define strict result fields while adapter/rule remain null and publish mode remains `review_required`. Freeze the registry tuples `lol/1.0.0`, `valorant/1.0.0`, `peace_elite/1.0.0`, scoring rule `1` and result schema `1`.

Implement the transition API exactly as:

```python
def require_transition(source: str, target: str) -> str:
    if (source, target) not in ALLOWED_TRANSITIONS:
        raise InvalidEvaluationTransition(source=source, target=target)
    return target
```

All result versions and events are immutable after insertion. Tasks are never physically deleted. A database trigger rejects a second rewrite of target student/enrollment/project after a pending-match target is confirmed.

- [ ] **Step 5: Implement migration 0012, RBAC seed, and safe downgrade refusal**

The migration creates the nine tables, fixed checks/indexes/triggers, permission 029 plus permissions 44–46 and only admin/global role grants. It adds the analysis-profile and composite-key changes without rewriting existing history. Downgrade refuses when any task, upload session/media, attempt, result, event, certification, idempotency row, task media row, changed profile, changed permission or changed grant exists. Empty upgrade → downgrade → upgrade remains valid. Register every table in `scripts/db-schema-manifest.sh`.

Add match/manual/certify to the existing sensitive-evaluation permission set; review is already reserved there. Add manual-input and review to staff-assignment permission validation, leaving match/certify administrator-only. Extend the audit allowlist only with UUIDs, bounded versions/counts/status/action/error enums; free text remains rejected. Tests must prove no broad coach/assistant role grant is created.

- [ ] **Step 6: Run GREEN tests and static checks**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/unit/evaluations/test_task_state.py \
  apps/api/tests/unit/evaluations/test_adapter_registry.py \
  apps/api/tests/db/test_system_evaluation_models.py \
  apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_evaluation_staff.py \
  apps/api/tests/api/test_audit.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api/app/evaluations/task_models.py \
  apps/api/app/evaluations/processing_models.py \
  apps/api/app/evaluations/review_models.py \
  apps/api/app/evaluations/task_state.py \
  apps/api/app/evaluations/adapter_registry.py \
  apps/api/migrations/versions/0012_system_evaluation_processing.py
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app/evaluations/task_models.py \
  apps/api/app/evaluations/processing_models.py \
  apps/api/app/evaluations/review_models.py \
  apps/api/app/evaluations/task_state.py
bash -n scripts/db-schema-manifest.sh
```

Expected: all selected tests pass and every static command exits 0.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/evaluations/task_models.py \
  apps/api/app/evaluations/processing_models.py \
  apps/api/app/evaluations/review_models.py \
  apps/api/app/evaluations/task_state.py \
  apps/api/app/evaluations/adapter_registry.py \
  apps/api/app/evaluations/models.py \
  apps/api/app/evaluations/analysis_models.py \
  apps/api/app/evaluations/analysis_profile_service.py \
  apps/api/app/evaluations/staff_service.py \
  apps/api/app/admissions/models.py apps/api/app/teaching/models.py \
  apps/api/migrations/versions/0012_system_evaluation_processing.py \
  apps/api/app/rbac/seed.py apps/api/app/rbac/service.py \
  apps/api/app/audit/service.py \
  apps/api/tests/unit/evaluations/test_task_state.py \
  apps/api/tests/unit/evaluations/test_adapter_registry.py \
  apps/api/tests/db/test_system_evaluation_models.py \
  apps/api/tests/db/test_rbac_seed.py apps/api/tests/db/test_migrations.py \
  apps/api/tests/services/test_analysis_profiles.py \
  apps/api/tests/services/test_evaluation_staff.py \
  apps/api/tests/api/test_audit.py \
  scripts/db-schema-manifest.sh
git commit -m "feat: add system evaluation workflow schema"
```

---

### Task 2: Add atomic upload, matching, and task creation

**Files:**

- Create: `apps/api/app/evaluations/upload_service.py`
- Create: `apps/api/app/evaluations/task_access.py`
- Create: `apps/api/app/evaluations/task_service.py`
- Create: `apps/api/app/evaluations/matching_service.py`
- Create: `apps/api/app/evaluations/idempotency.py`
- Create: `apps/api/app/evaluations/task_api_support.py`
- Create: `apps/api/app/evaluations/task_router.py`
- Create: `apps/api/app/evaluations/integration_v2_web_router.py`
- Create: `apps/api/app/evaluations/task_schemas.py`
- Create: `apps/api/tests/services/test_evaluation_uploads.py`
- Create: `apps/api/tests/services/test_evaluation_task_access.py`
- Create: `apps/api/tests/services/test_evaluation_matching.py`
- Create: `apps/api/tests/api/test_system_evaluation_tasks.py`
- Create: `apps/api/tests/api/test_evaluation_integration_v2_web.py`
- Modify: `apps/api/app/evaluations/media_service.py`
- Modify: `apps/api/app/evaluations/media_store.py`
- Modify: `apps/api/app/evaluations/dependencies.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/context_service.py`
- Modify: `apps/api/app/evaluations/schemas.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `apps/api/tests/api/test_evaluation_foundation.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing atomic-media and cleanup tests**

Create tests that stage three validated images, inject a failure after the second asset insert, and assert zero assets, zero task-media rows, zero tasks and zero promoted files remain. Then assert a successful three-slot request commits all files and rows together. Freeze the transaction-safe service interface:

```python
registered = media_service.register_validated_images_in_transaction(
    images,
    uploaded_by_user_id=actor_id,
    project_id=project_id,
)
assert [asset.slot_key for asset in registered] == [
    "profile",
    "match_result",
    "team_detail",
]
```

The method requires an existing service-owned transaction and must not call `commit()` or open an independent `write_transaction`; it reuses the current media file compensation hooks so rollback discards every staged/promoted file.

- [ ] **Step 2: Write failing authorization, window, matching, and API tests**

Cover student/self, active guardian/family, overlapping assigned coach/staff, admin with required business reason, inactive account, explicit deny, expired grant, ended family relation, non-overlapping staff, informal enrollment and the exact China-time window endpoints. Verify selection and mutation use the same `EvaluationTaskResource` predicate.

Cover explicit target selection and restricted matching order: same-project active game ID, then primary phone, then auxiliary phone. Unique authorized matches may queue; zero or multiple authorized matches return only `pending_match` without candidate identity or counts. Requests that point to missing, disabled, denied, wrong-project or out-of-scope targets must all produce the same body:

```json
{"type":"about:blank","title":"Not Found","status":404,"detail":"评价资源不存在","code":"evaluation_resource_not_found"}
```

Assert no media, task, idempotency or audit rows are written on every rejected selector path.

Freeze v2 Web service tests at the same time. Only `EVALUATION_TOOL_WEB_SERVICE_TOKEN` may call `/integrations/evaluations/v2/web/*`; the existing v1 token and future Worker token fail with 401. The delegated session travels only in the server-to-server `X-Evaluation-Delegated-Session` header, is validated before target selection and is never returned in JSON, audit, logs or browser HTML.

- [ ] **Step 3: Run RED tests**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_uploads.py \
  apps/api/tests/services/test_evaluation_task_access.py \
  apps/api/tests/services/test_evaluation_matching.py \
  apps/api/tests/api/test_system_evaluation_tasks.py \
  apps/api/tests/api/test_evaluation_integration_v2_web.py \
  apps/api/tests/api/test_evaluation_foundation.py -q
```

Expected: FAIL because upload sessions, task authorization and system task routes are absent.

- [ ] **Step 4: Implement upload sessions and file validation**

`PUT /api/v1/evaluations/system/upload-sessions/{session_id}/slots/{slot_key}` accepts one multipart file and verifies filename extension, declared MIME, sniffed MIME, Pillow decode, JPEG/PNG/WebP format, 20 MiB size and 50 million pixel limit. Server-side preview generation strips metadata. Upload sessions expire after 30 minutes and keep only opaque quarantine keys; replacement removes the previous staged files through the compensation queue. The project analysis snapshot supplies slot keys and required status; the browser never hard-codes project slots.

- [ ] **Step 5: Implement shared task authorization and matching selectors**

`task_access.py` exposes one object predicate used by list/detail/media/action paths:

```python
@dataclass(frozen=True)
class EvaluationTaskResource:
    student_person_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID | None
    project_id: UUID
    class_id: UUID | None

    def as_evaluation_resource(self) -> EvaluationResource:
        return EvaluationResource(
            student_person_id=self.student_person_id,
            enrollment_id=self.enrollment_id,
            cohort_id=self.cohort_id,
            project_id=self.project_id,
            class_id=self.class_id,
        )

def task_allowed(
    self,
    *,
    account_id: UUID,
    permission: str,
    resource: EvaluationTaskResource,
    occurred_at: datetime,
) -> bool:
    return self.access.decide(
        account_id=account_id,
        permission=permission,
        resource=resource.as_evaluation_resource(),
        occurred_at=occurred_at,
    ).allowed
```

The implementation delegates scope resolution to the existing evaluation access service, applies explicit deny first, checks active account and upload window on every write, and returns private 404 after capability has been established. Matching SQL is pre-filtered to resources the actor may target before any digest comparison.

`task_access.py` owns operation-specific time semantics rather than changing the global foundation access service: upload/action permissions use current account, current relationship and current service overlap; published-history visibility uses the stored enrollment/service interval; original/preview access is revoked when the current family/service relationship ends. List count/page SQL and detail/action selectors call the same predicate.

- [ ] **Step 6: Atomically confirm media and create the task**

Within one `write_transaction`, lock the upload session, re-run authorization/window/enrollment/profile checks, register every staged image in the same transaction, insert task/media/event/idempotency rows and create the task as `uploaded`. Record `uploaded -> identifying` and the final `identifying -> queued|manual_input|pending_match` transition events before commit, leaving the task in the final state. Capture the complete immutable profile and window snapshot. The same idempotency key with the same canonical non-sensitive payload returns the existing task; the same key with a different digest returns 409. Business duplicate checks never reveal another user's task.

- [ ] **Step 7: Add routes, schemas, dependencies, and private cache middleware coverage**

Register `task_router` under `/evaluations/system`. Add a separate `integration_v2_web_router` under `/integrations/evaluations/v2/web`; it authenticates the Web service token, resolves the delegated session server-side and invokes the same task/upload services rather than duplicating authorization. Add `evaluation_tool_web_service_token` runtime configuration while preserving the existing v1 token unchanged. `task_schemas.py` returns only masked identity summaries and opaque UUIDs. Previews use the existing protected file response and require the same task predicate as detail. Verify the existing evaluation private response middleware already covers both route prefixes and returns `Cache-Control: no-store, private`, `Pragma: no-cache`, and `X-Content-Type-Options: nosn`; do not modify the middleware when coverage passes.

- [ ] **Step 8: Run GREEN tests, security search, and static checks**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_media.py \
  apps/api/tests/services/test_evaluation_uploads.py \
  apps/api/tests/services/test_evaluation_task_access.py \
  apps/api/tests/services/test_evaluation_matching.py \
  apps/api/tests/api/test_system_evaluation_tasks.py \
  apps/api/tests/api/test_evaluation_integration_v2_web.py \
  apps/api/tests/api/test_evaluation_foundation.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api/app/evaluations apps/api/tests/services/test_evaluation_uploads.py \
  apps/api/tests/services/test_evaluation_task_access.py \
  apps/api/tests/api/test_system_evaluation_tasks.py
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app/evaluations/upload_service.py \
  apps/api/app/evaluations/task_access.py \
  apps/api/app/evaluations/task_service.py \
  apps/api/app/evaluations/matching_service.py \
  apps/api/app/evaluations/idempotency.py \
  apps/api/app/evaluations/task_api_support.py
rg -n "phone|identity|game_id|ocr|advice|reject" apps/api/app/evaluations/task_*.py \
  apps/api/app/evaluations/upload_service.py
```

Expected: tests and static checks pass; search hits are limited to encrypted/digested business fields and DTO names, not audit metadata or logs.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/evaluations/upload_service.py \
  apps/api/app/evaluations/task_access.py apps/api/app/evaluations/task_service.py \
  apps/api/app/evaluations/matching_service.py \
  apps/api/app/evaluations/idempotency.py \
  apps/api/app/evaluations/task_api_support.py \
  apps/api/app/evaluations/task_router.py \
  apps/api/app/evaluations/integration_v2_web_router.py \
  apps/api/app/evaluations/task_schemas.py \
  apps/api/app/evaluations/media_service.py apps/api/app/evaluations/media_store.py \
  apps/api/app/evaluations/dependencies.py apps/api/app/evaluations/errors.py \
  apps/api/app/evaluations/context_service.py apps/api/app/evaluations/schemas.py \
  apps/api/app/core/config.py apps/api/app/api/router.py .env.example \
  apps/api/tests/conftest.py apps/api/tests/services/test_evaluation_uploads.py \
  apps/api/tests/services/test_evaluation_task_access.py \
  apps/api/tests/services/test_evaluation_matching.py \
  apps/api/tests/api/test_system_evaluation_tasks.py \
  apps/api/tests/api/test_evaluation_integration_v2_web.py \
  apps/api/tests/api/test_evaluation_foundation.py
git commit -m "feat: create system evaluation tasks from private uploads"
```

---

### Task 3: Add Worker leases, strict results, retries, and v2 service identities

**Files:**

- Create: `apps/api/app/evaluations/processing_service.py`
- Create: `apps/api/app/evaluations/processing_router.py`
- Create: `apps/api/app/evaluations/integration_v2_router.py`
- Create: `apps/api/app/evaluations/processing_schemas.py`
- Create: `apps/api/app/evaluations/worker_ticket.py`
- Create: `apps/api/tests/services/test_evaluation_processing.py`
- Create: `apps/api/tests/db/test_evaluation_processing_concurrency.py`
- Create: `apps/api/tests/api/test_evaluation_integration_v2_worker.py`
- Create: `apps/api/tests/contracts/evaluation_integration_v2.json`
- Modify: `apps/api/app/evaluations/dependencies.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing lease and concurrency tests**

Use two independent PostgreSQL sessions and barriers to claim the same queue. Assert exactly one worker receives the task, the other receives no claim, and `(task_id, attempt)` remains unique. Freeze five-minute leases and monotonic attempts:

```python
claim = service.claim_next(
    worker_identity="score-worker-local",
    supported_adapters={"lol.v1", "valorant.v1", "peace_elite.v1"},
    occurred_at=now,
)
assert claim.attempt == 1
assert claim.lease_expires_at == now + timedelta(minutes=5)
assert claim.lease_token
assert db_attempt.lease_token_digest != claim.lease_token
```

Also test stale attempt, wrong Worker identity, wrong task version, replaced lease, expired lease and state change for renew/result/failure. Every case must reject without result, event or audit writes and leave the SQLAlchemy session usable after the conflict.

- [ ] **Step 2: Write failing strict-result, media-ticket, and retry tests**

Freeze the v2 result body:

```python
SystemEvaluationResultInput(
    contract_version="v2",
    adapter_id="lol",
    adapter_version="1.0.0",
    scoring_rule_version="1",
    result_schema_version="1",
    fields={"kills": 12, "deaths": 3, "assists": 8},
    confidences={"kills": 0.98, "deaths": 0.97, "assists": 0.96},
    missing_fields=[],
    warnings=[],
    score=86,
    grade="A",
    components=[{"key": "combat", "score": 86, "weight": 1.0, "summary": "作战数据稳定"}],
    advice="继续保持团战前的信息确认。",
    duration_ms=1200,
    idempotency_key="result-task-1-attempt-1",
)
```

Test score outside 0–100, confidence outside 0–1, unknown required fields, duplicate component keys, warning severity outside `info|warning|blocking`, schema/profile mismatch, missing required fields and blocking warnings. First-release automatic submissions always land in `needs_review`, including a configured and certified `auto_publish` profile.

Media tickets must bind service identity, task, attempt, media, task version and expiry. A ticket for another media/task/attempt or an expired/replaced lease returns the same private 404 and no file bytes.

Classify `network`, `worker_interrupted`, `ocr_timeout`, and `temporary_service` as retryable. Attempts 1 and 2 requeue with `available_at`; attempt 3 and permanent classes `adapter_incompatible`, `media_invalid`, and `result_schema_invalid` move to `manual_input` with a safe public failure code.

- [ ] **Step 3: Write failing v2 service-identity and contract tests**

Use the Web token established in Task 2 and add a distinct Worker test secret. Prove the v1 generic token cannot call v2, the Web token cannot call Worker routes, and the Worker token cannot call Web routes. Duplicate, missing and comma-folded `Authorization` headers fail closed. Assert the ERP v2 contract file contains exact paths, request/response schema versions, error codes, media types, 300-second lease and maximum three attempts.

- [ ] **Step 4: Run RED tests**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_processing.py \
  apps/api/tests/db/test_evaluation_processing_concurrency.py \
  apps/api/tests/api/test_evaluation_integration_v2_worker.py -q
```

Expected: FAIL because processing service, Worker routes, split service tokens and v2 contract do not exist.

- [ ] **Step 5: Implement atomic claims and lease renewal**

`claim_next` selects compatible due tasks with `FOR UPDATE SKIP LOCKED`, increments `current_attempt`, inserts an attempt, moves the task to `processing`, increments `lock_version`, issues a 256-bit random token and stores only `HMAC-SHA256(domain || token)`. Claim response contains the profile snapshot, required slot manifest, opaque task/media UUIDs and signed media tickets; it contains no person name, phone, identity number or full game ID. Renewal locks task and attempt and extends the lease to exactly five minutes from the accepted request time.

- [ ] **Step 6: Implement result/failure submission and stale-write protection**

Result submission validates service identity, current state/version/attempt/lease, exact profile adapter/rule/schema versions and Pydantic numeric limits inside one transaction. It inserts an immutable automatic result version and event, completes the attempt and transitions to `needs_review`. Idempotent replay with the same canonical payload returns the same result; payload mismatch returns 409. Failure submission schedules retry with deterministic `30 * 2 ** (attempt - 1)` seconds or moves to `manual_input`; an old attempt can never overwrite a newer attempt.

- [ ] **Step 7: Implement signed media tickets and split dependencies**

`worker_ticket.py` serializes only UUIDs, attempt, task version and expiry, authenticates them with the session pepper and validates the current database lease before returning a protected file. Add `evaluation_tool_worker_service_token` with production secret validation; keep Task 2's Web token and the v1 login token as separate identities.

- [ ] **Step 8: Register v2 Worker routes and generate the ERP contract fixture**

Mount a separate `processing_router` under `/integrations/evaluations/v2/worker` and a minimal `integration_v2_router` for `/integrations/evaluations/v2/capabilities`; do not modify or broaden v1 router dependencies. Serialize the frozen route summary and result/claim/error envelopes into `apps/api/tests/contracts/evaluation_integration_v2.json` with stable sorted keys.

- [ ] **Step 9: Run GREEN tests and static checks**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_processing.py \
  apps/api/tests/db/test_evaluation_processing_concurrency.py \
  apps/api/tests/api/test_evaluation_integration_v2_worker.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api/app/evaluations/processing_service.py \
  apps/api/app/evaluations/processing_router.py \
  apps/api/app/evaluations/processing_schemas.py \
  apps/api/app/evaluations/worker_ticket.py \
  apps/api/tests/services/test_evaluation_processing.py \
  apps/api/tests/db/test_evaluation_processing_concurrency.py \
  apps/api/tests/api/test_evaluation_integration_v2_worker.py
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app/evaluations/processing_service.py \
  apps/api/app/evaluations/processing_schemas.py \
  apps/api/app/evaluations/worker_ticket.py
```

Expected: all selected tests pass; Ruff and mypy exit 0.

- [ ] **Step 10: Commit**

```bash
git add .env.example apps/api/app/core/config.py \
  apps/api/app/evaluations/processing_service.py \
  apps/api/app/evaluations/processing_router.py \
  apps/api/app/evaluations/integration_v2_router.py \
  apps/api/app/evaluations/processing_schemas.py \
  apps/api/app/evaluations/worker_ticket.py \
  apps/api/app/evaluations/dependencies.py \
  apps/api/app/api/router.py \
  apps/api/tests/conftest.py apps/api/tests/services/test_evaluation_processing.py \
  apps/api/tests/db/test_evaluation_processing_concurrency.py \
  apps/api/tests/api/test_evaluation_integration_v2_worker.py \
  apps/api/tests/contracts/evaluation_integration_v2.json
git commit -m "feat: process evaluation tasks with leased workers"
```

---

### Task 4: Add manual input, review, publication, revision, and read projections

**Files:**

- Create: `apps/api/app/evaluations/result_service.py`
- Create: `apps/api/app/evaluations/review_service.py`
- Create: `apps/api/app/evaluations/query_service.py`
- Create: `apps/api/app/evaluations/review_router.py`
- Create: `apps/api/app/evaluations/review_schemas.py`
- Create: `apps/api/tests/services/test_evaluation_results.py`
- Create: `apps/api/tests/services/test_evaluation_reviews.py`
- Create: `apps/api/tests/services/test_evaluation_queries.py`
- Create: `apps/api/tests/api/test_system_evaluation_reviews.py`
- Create: `apps/api/tests/api/test_system_evaluation_portal.py`
- Modify: `apps/api/app/evaluations/task_router.py`
- Modify: `apps/api/app/evaluations/task_schemas.py`
- Modify: `apps/api/app/evaluations/dependencies.py`
- Modify: `apps/api/app/api/router.py`

- [ ] **Step 1: Write failing manual-result and separation-of-duty tests**

Test draft create/update by an account with `evaluation_jobs.manual_input`; enforce task profile required fields, score 0–100, unique component keys and a non-empty grade. Saving a draft keeps `manual_input`; submission creates an immutable version and moves to `needs_review`.

Freeze author rules with these assertions:

```python
assert uploaded_only_version.created_by_user_id == manual_editor_id
assert uploaded_only_version.created_by_user_id != uploader_id
with pytest.raises(SelfReviewForbidden):
    review_service.approve(
        task_id=task.id,
        expected_lock_version=task.lock_version,
        reviewer_user_id=manual_editor_id,
        idempotency_key="review-self",
        occurred_at=now,
    )
```

Editing any OCR field, score, grade, component, warning or advice creates a new version authored by the editor. An administrator is not exempt from self-review prohibition.

- [ ] **Step 2: Write failing review, correction, publication, and revision tests**

Approve atomically sets reviewer fields, `published_at`, task `published`, current published version and append-only event/audit rows. Reject requires a non-blank reason of at most 1000 characters and moves to `rejected`; reason is stored only in the result/event business field and absent from generic audit metadata. Correction creates the next version from rejected data and transitions through `correction_pending`. Revision starts from the currently published version; the old version remains current until the replacement is approved.

Test optimistic-lock conflicts, repeated idempotency, payload mismatch, wrong task state, missing/deleted/disabled/non-eligible/denied/wrong-scope reviewers and author/reviewer races. Conflict responses must leave the database session usable.

- [ ] **Step 3: Write failing family, staff, administrator, and media visibility tests**

Students and active guardians see only published versions in their current self/family scope. Staff see tasks and results only where their service assignment overlaps the enrollment/project/class and requested time. Administrators with capability use the same object predicate. Raw/previews require current media permission and task scope; losing service or family scope immediately removes media access. Every list result must equal the set of individually allowed detail resources.

Test aggregate counts for `upload_available`, `processing`, `pending_match`, `manual_input`, `needs_review`, `rejected`, and `published`. Queries are paginated with deterministic `(created_at DESC, id DESC)` ordering and never serialize phone, identity number, full game ID, raw OCR or physical media paths.

- [ ] **Step 4: Run RED tests**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_results.py \
  apps/api/tests/services/test_evaluation_reviews.py \
  apps/api/tests/services/test_evaluation_queries.py \
  apps/api/tests/api/test_system_evaluation_reviews.py \
  apps/api/tests/api/test_system_evaluation_portal.py -q
```

Expected: FAIL because result editing, review actions and task projections are absent.

- [ ] **Step 5: Implement manual/correction/revision result services**

Use a single canonical result validator shared with automatic results. A saved draft may remain incomplete; submit-review revalidates every required field. Never update an already submitted or published result row. Corrections and revisions copy structured values into a new row with `previous_version_id`, a new sequential version and the editing account as author.

- [ ] **Step 6: Implement independent review and publication**

Review locks task/current result, checks `evaluation_jobs.review` and object scope, rejects `created_by_user_id == reviewer_user_id`, validates `expected_lock_version`, then appends the review event. `approve` publishes without permitting field changes. `reject` stores the reason in the protected business history. `archive` and `reopen` preserve every version/media/event. Adapter certification requires `evaluation_adapters.certify`, immutable manifest digest and exact project/adapter/rule/schema tuple; the three first-release adapters are not seeded as certified.

- [ ] **Step 7: Implement shared query projections and routes**

Expose account/staff routes through `task_router` and administrator/reviewer routes through `review_router`. Capability checks happen before selector queries; private resources use identical 404 bodies and no side effects. Student/family DTOs expose only published versions. Staff/admin DTOs expose working state only when the actor holds the relevant object capability. The context response changes `upload_available` from a literal false to the computed per-account boolean and reports queue counts.

- [ ] **Step 8: Run GREEN tests, audit leak scan, and static checks**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_evaluation_results.py \
  apps/api/tests/services/test_evaluation_reviews.py \
  apps/api/tests/services/test_evaluation_queries.py \
  apps/api/tests/api/test_system_evaluation_reviews.py \
  apps/api/tests/api/test_system_evaluation_portal.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api/app/evaluations/result_service.py \
  apps/api/app/evaluations/review_service.py \
  apps/api/app/evaluations/query_service.py \
  apps/api/app/evaluations/review_router.py \
  apps/api/app/evaluations/review_schemas.py
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app/evaluations/result_service.py \
  apps/api/app/evaluations/review_service.py \
  apps/api/app/evaluations/query_service.py
rg -n "metadata=.*(reason|advice|ocr|phone|game|identity)|logger.*(reason|advice|ocr|phone|game|identity)" \
  apps/api/app/evaluations apps/api/app/audit
```

Expected: tests and static checks pass; leak scan has no production matches.

- [ ] **Step 9: Commit**

```bash
git add apps/api/app/evaluations/result_service.py \
  apps/api/app/evaluations/review_service.py \
  apps/api/app/evaluations/query_service.py \
  apps/api/app/evaluations/review_router.py \
  apps/api/app/evaluations/review_schemas.py \
  apps/api/app/evaluations/task_router.py \
  apps/api/app/evaluations/task_schemas.py \
  apps/api/app/evaluations/dependencies.py apps/api/app/api/router.py \
  apps/api/tests/services/test_evaluation_results.py \
  apps/api/tests/services/test_evaluation_reviews.py \
  apps/api/tests/services/test_evaluation_queries.py \
  apps/api/tests/api/test_system_evaluation_reviews.py \
  apps/api/tests/api/test_system_evaluation_portal.py
git commit -m "feat: review and publish system evaluations"
```

---

### Task 5: Add the score-tool v2 Web client and responsive upload page

**Files:**

- Create: `v2_web_client.py`
- Create: `system_evaluation_web.py`
- Create: `templates/system_evaluation.html`
- Create: `static/system_evaluation.js`
- Create: `static/system_evaluation.css`
- Create: `tests/test_v2_web_client.py`
- Create: `tests/test_system_evaluation_web.py`
- Create: `tests/contracts/evaluation_integration_v2.json`
- Modify: `config.py`
- Modify: `integration.py`
- Modify: `main.py`
- Modify: `templates/index.html`
- Modify: `templates/_integration_banner.html`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Establish the isolated score-tool worktree and clean baseline**

```bash
cd '/Users/skymoonzyj/Documents/game-score-tool'
git worktree add \
  '/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing' \
  -b codex/system-evaluation-processing 20e1a38
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing'
PYTHONDONTWRITEBYTECODE=1 \
  '/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python' \
  -m pytest -p no:cacheprovider -q
```

Expected: worktree is on `codex/system-evaluation-processing`; baseline reports `105 passed` before any source change. The worktree lives under ERP's already-ignored `.worktrees/` directory, and the shared score-tool virtual environment is used without creating or modifying old data directories.

- [ ] **Step 2: Write failing Web-client contract and token-separation tests**

Test that v1 methods continue using `ERP_SERVICE_TOKEN`, while every v2 Web call uses only `ERP_WEB_SERVICE_TOKEN`. Verify fixed `/api/v1/integrations/evaluations/v2/web/*` paths, multipart streaming, JSON schema validation, generic network errors and no ERP response body/token/session leakage. Compare `tests/contracts/evaluation_integration_v2.json` byte-for-byte with the ERP fixture supplied by Task 3.

Freeze a narrow protocol:

```python
class EvaluationWebClient(Protocol):
    async def upload_context(self, session_id: str) -> UploadContext:
        raise NotImplementedError

    async def create_upload_session(
        self, session_id: str, request: UploadSessionCreate
    ) -> UploadSession:
        raise NotImplementedError

    async def put_slot(
        self,
        session_id: str,
        upload_id: str,
        slot_key: str,
        metadata: UploadMetadata,
        body: AsyncIterator[bytes],
    ) -> UploadSlot:
        raise NotImplementedError

    async def create_task(
        self, session_id: str, upload_id: str, idempotency_key: str
    ) -> EvaluationTask:
        raise NotImplementedError

    async def tasks(self, session_id: str) -> list[EvaluationTask]:
        raise NotImplementedError
```

- [ ] **Step 3: Write failing page and browser-proxy tests**

Authenticated standalone and embedded sessions must show the same ERP-derived targets, slots and task history. Test five steps: target/project selection, dynamic slots, local preview, task confirmation and current status/history. A user without upload ability sees published history and a disabled upload action. Video remains disabled with “GPU 服务尚未启用”. Invalid session redirects through the existing mode-specific login. HTML contains no account UUID, delegated session, service token, physical path or raw ERP error body.

Test filename/MIME/size errors before proxying and confirm the Web process never creates `data/`, `uploads/` or `runtime/`. Multipart content is streamed through to ERP and not buffered into a persistent local file.

- [ ] **Step 4: Run RED tests**

```bash
SCORE_PY='/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python'
"$SCORE_PY" -m pytest tests/test_v2_web_client.py \
  tests/test_system_evaluation_web.py tests/test_contract.py -q
```

Expected: FAIL because the v2 client, contract and page router do not exist.

- [ ] **Step 5: Split Web configuration from the Worker secret**

Extend `ScoreToolSettings` with `erp_web_service_token` loaded from `ERP_WEB_SERVICE_TOKEN`; keep `erp_service_token` exclusively for v1 login compatibility. Do not add the Worker token to `ScoreToolSettings`, template context, browser JavaScript or application state. Validate both Web secrets as at least 32 bytes and preserve all production HTTPS/cookie checks.

- [ ] **Step 6: Implement validated v2 Web client and router**

`v2_web_client.py` validates every required field and enum before returning dataclasses. `system_evaluation_web.py` exposes only `/api/evaluations/v2/context`, `/upload-sessions`, `/upload-sessions/{session_id}/slots/{slot_key}`, `/upload-sessions/{session_id}/tasks`, `/tasks` and `/tasks/{task_id}` to the authenticated browser. Update the existing integration middleware allowlist for exactly those paths while keeping every legacy `/api/*` write at its stable 503 response. The router derives the delegated session only from the signed HttpOnly cookie, calls the ERP v2 Web service with the separate Web token, streams `request.stream()` to ERP without first materializing an `UploadFile`, applies existing standalone/embedded path handling and private response headers, and maps ERP 401/403/404/409/422/5xx to generic Chinese UI errors without reflecting upstream bodies.

- [ ] **Step 7: Implement the responsive upload/history page**

Use the existing design tokens but new page-specific CSS/JS. Render target and slot metadata as escaped JSON in a nonce-free `data-*` bootstrap limited to display-safe fields. JavaScript creates object-URL previews, revokes them on replace/navigation, sends one slot at a time, disables double submission and polls only active tasks with bounded backoff. Desktop uses a two-column upload/history layout; widths at or below 768 px use one column, 44 px controls and no horizontal overflow. Keep existing MOBA/FPS pages reachable but route their write controls to the new system-evaluation flow instead of legacy APIs.

- [ ] **Step 8: Run GREEN tests, legacy-isolation checks, and static syntax checks**

```bash
"$SCORE_PY" -m pytest tests/test_v2_web_client.py \
  tests/test_system_evaluation_web.py tests/test_contract.py \
  tests/test_read_only_shell.py tests/test_integration_flow.py -q
"$SCORE_PY" -m compileall -q v2_web_client.py system_evaluation_web.py \
  config.py integration.py main.py
node --check static/system_evaluation.js
rg -n "ERP_WORKER_SERVICE_TOKEN|worker_service_token" templates static \
  main.py integration.py system_evaluation_web.py v2_web_client.py
```

Expected: tests and syntax checks pass; the Worker-secret search has no matches.

- [ ] **Step 9: Commit in the score-tool worktree**

```bash
git add config.py integration.py main.py v2_web_client.py system_evaluation_web.py \
  templates/system_evaluation.html templates/index.html \
  templates/_integration_banner.html static/system_evaluation.js \
  static/system_evaluation.css tests/conftest.py tests/test_v2_web_client.py \
  tests/test_system_evaluation_web.py tests/contracts/evaluation_integration_v2.json
git commit -m "feat: add ERP-backed evaluation upload page"
```

---

### Task 6: Add the score Worker and three un-certified adapters

**Files:**

- Create: `worker_config.py`
- Create: `worker_client.py`
- Create: `worker.py`
- Create: `adapters/__init__.py`
- Create: `adapters/base.py`
- Create: `adapters/lol.py`
- Create: `adapters/valorant.py`
- Create: `adapters/peace_elite.py`
- Create: `adapters/synthetic.py`
- Create: `adapters/registry.py`
- Create: `tests/test_worker_client.py`
- Create: `tests/test_worker.py`
- Create: `tests/test_adapters.py`
- Create: `tests/golden/lol-v1.json`
- Create: `tests/golden/valorant-v1.json`
- Create: `tests/golden/peace-elite-v1.json`
- Create: `pytest.ini`
- Modify: `scorer.py`
- Modify: `ocr_utils.py`
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`

- [ ] **Step 1: Write failing pure-adapter golden tests**

Each golden JSON contains sanitized OCR text, expected normalized structured fields, confidence keys, score, grade, component keys, warnings and advice fragment. Tests call only adapter functions and never touch SQLite or legacy upload helpers; tests that need decoded images generate small non-personal PNGs under pytest `tmp_path` with Pillow and remove them automatically:

```python
result = adapter.evaluate(
    [AdapterImage(slot_key=item.slot_key, path=fixture_path(item.fixture))]
)
assert 0 <= result.score <= 100
assert set(result.confidences) == set(result.fields)
assert result.to_contract()["result_schema_version"] == "1"
assert result.to_contract()["adapter_version"] == manifest["adapter_version"]
```

The League adapter wraps existing MOBA OCR/scoring, Valorant wraps existing FPS parsing/scoring, and Peace Elite consumes the configured personal-data/match-result/team-detail slots by name rather than upload order. Personal data supplies season/profile metrics, match result supplies current-match metrics, and team detail supplies identity-consistency and team warnings; missing scoring inputs are reported instead of silently defaulted. These fixtures are regression samples only and must not create adapter certification records.

- [ ] **Step 2: Write failing Worker-client and runtime tests**

`WorkerSettings` loads only `ERP_WORKER_SERVICE_TOKEN`, ERP API URL, worker identity, poll interval and temporary root. Prove it cannot read the Web token as fallback. Client tests freeze claim/renew/media/result/failure routes, Authorization header uniqueness, strict schema validation, generic error mapping and same-idempotency-key retries.

Runtime tests cover: no claim, successful claim, renewal when remaining lease is below two minutes, no early renewal, network retry with the same result idempotency key, permanent adapter error, OCR timeout, SIGTERM between media download and result, and stale lease rejection. All downloaded media live under `TemporaryDirectory` and are gone after success/failure/interruption; `data/`, `uploads/`, `runtime/` and SQLite are untouched.

- [ ] **Step 3: Run RED tests**

```bash
SCORE_PY='/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python'
"$SCORE_PY" -m pytest tests/test_adapters.py tests/test_worker_client.py \
  tests/test_worker.py -q
```

Expected: FAIL because adapters, Worker client and runtime are absent.

- [ ] **Step 4: Extract pure algorithm seams without changing legacy behavior**

Move no legacy persistence code. Add narrow pure entry points in `scorer.py` and `ocr_utils.py` that accept decoded image paths or sanitized OCR text and return Python values. Existing legacy functions continue to call the same logic so prior tests remain green. Adapter modules normalize those values into `AdapterResult`; they never import `database.py`, call `save_upload`, open SQLite or return raw OCR text.

- [ ] **Step 5: Implement adapter registry and strict result normalization**

`registry.py` contains exact production mappings for `lol/1.0.0`, `valorant/1.0.0`, and `peace_elite/1.0.0`. Reject unknown versions instead of falling back. A `synthetic/1.0.0` adapter may register only when `SCORE_TOOL_ENV=test`; it emits deterministic non-personal fixture data for cross-service E2E and is never eligible for certification. `AdapterResult` validates 0–100 score, 0–1 confidences, unique component/warning keys and bounded display text. Missing critical fields and blocking warnings are explicit contract values.

Refactor each scoring function so total score and component breakdown come from one calculation. Preserve the frozen regression values `90.6/A+` for League, `78.5/B+` for Valorant and `99.7/S` for the Peace Elite golden inputs; component contributions plus documented modifiers must reproduce the returned total exactly.

- [ ] **Step 6: Implement Worker client, lease heartbeat, and processing loop**

Use a dedicated HTTP client with the Worker token. After a claim, download only ticketed media into a per-attempt temporary directory, start a heartbeat that renews when lease remaining is under 120 seconds, execute the exact adapter version and submit the strict v2 result. Network retries reuse one result idempotency key. Map timeouts and process interruption to retryable safe failure classes; map schema/media/adapter incompatibility to permanent classes. Never print tokens, tickets, response bodies, image content or OCR text.

- [ ] **Step 7: Add deterministic readiness and one-shot modes**

Expose `python -m worker --check` to validate configuration, v2 capabilities and adapter registry, print exactly `score-worker ready`, then exit 0. `python -m worker --once` claims at most one task and exits after the terminal submission. Default mode polls continuously with bounded backoff and clean SIGINT/SIGTERM handling.

- [ ] **Step 8: Run GREEN tests and legacy-data safety checks**

```bash
"$SCORE_PY" -m pytest tests/test_adapters.py tests/test_worker_client.py \
  tests/test_worker.py tests/test_read_only_shell.py -q
"$SCORE_PY" -m compileall -q worker_config.py worker_client.py worker.py adapters \
  scorer.py ocr_utils.py
rg -n "import database|save_upload|sqlite|uploads/|runtime/|data/" \
  worker.py worker_client.py worker_config.py adapters
```

Expected: tests and compile checks pass; safety search has no matches in Worker/adapter production code.

Run the real OCR suite only as an explicit optional gate:

```bash
if command -v tesseract >/dev/null 2>&1; then
  SCORE_TOOL_REAL_OCR=1 TESSERACT_CMD="$(command -v tesseract)" \
    "$SCORE_PY" -m pytest -p no:cacheprovider -m real_ocr -q
else
  echo 'real OCR gate skipped: tesseract is not installed'
fi
```

Expected on the current machine: the command prints the explicit skip message. This does not certify production OCR or auto-publication.

- [ ] **Step 9: Commit in the score-tool worktree**

```bash
git add worker_config.py worker_client.py worker.py adapters scorer.py ocr_utils.py \
  requirements.txt requirements-dev.txt pytest.ini tests/test_worker_client.py \
  tests/test_worker.py tests/test_adapters.py tests/golden
git commit -m "feat: process ERP evaluation jobs with score adapters"
```

---

### Task 7: Add ERP responsive task, review, and published-history pages

**Files:**

- Create: `apps/web/src/evaluations/systemEvaluationApi.ts`
- Create: `apps/web/src/evaluations/systemEvaluationApi.test.ts`
- Create: `apps/web/src/evaluations/systemEvaluationKeys.ts`
- Create: `apps/web/src/evaluations/systemEvaluationKeys.test.ts`
- Create: `apps/web/src/evaluations/useSystemEvaluationContext.ts`
- Create: `apps/web/src/evaluations/EvaluationDialogShell.tsx`
- Create: `apps/web/src/evaluations/EvaluationDialogShell.test.tsx`
- Create: `apps/web/src/evaluations/SystemEvaluationDashboard.tsx`
- Create: `apps/web/src/evaluations/SystemEvaluationDashboard.test.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskList.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskList.test.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskDetail.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskDetail.test.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskActions.tsx`
- Create: `apps/web/src/evaluations/EvaluationTaskActions.test.tsx`
- Create: `apps/web/src/evaluations/EvaluationReviewPanel.tsx`
- Create: `apps/web/src/evaluations/EvaluationReviewPanel.test.tsx`
- Create: `apps/web/src/evaluations/PublishedEvaluationPanel.tsx`
- Create: `apps/web/src/evaluations/PublishedEvaluationPanel.test.tsx`
- Create: `apps/web/src/evaluations/AdapterCertificationsPanel.tsx`
- Create: `apps/web/src/evaluations/AdapterCertificationsPanel.test.tsx`
- Create: `apps/web/src/evaluations/system-evaluation.css`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`
- Modify: `apps/web/src/evaluations/EvaluationAdminPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationAdminPage.test.tsx`
- Modify: `apps/web/src/evaluations/AnalysisProfilesPanel.tsx`
- Modify: `apps/web/src/evaluations/AnalysisProfilesPanel.test.tsx`
- Modify: `apps/web/src/evaluations/CohortStaffPanel.tsx`
- Modify: `apps/web/src/evaluations/CohortStaffPanel.test.tsx`
- Modify: `apps/web/src/evaluations/api.ts`
- Modify: `apps/web/src/evaluations/api.test.ts`
- Modify: `apps/web/src/evaluations/foundationApi.ts`
- Modify: `apps/web/src/evaluations/foundationApi.test.ts`
- Modify: `apps/web/src/evaluations/evaluations.css`
- Modify: `apps/web/src/portal/LearningPage.tsx`
- Modify: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/portal/portal.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write failing API-client and cache-key tests**

Freeze exact request paths, query normalization, CSRF handling, idempotency headers, 404/403/409 mappings and response decoding. Parse API responses from `unknown` through an allowlist schema so extra OCR, path or identity fields cannot enter the cache. Query keys live under the existing `evaluationKeys.root(accountId)` and include actor mode, filters and task UUID; login/account change invalidates all evaluation data. Sensitive manual text, advice, rejection and revision bodies use opaque branded write-only objects backed by `WeakMap`, are not placed in TanStack query keys, error objects or browser storage, and mutations use `gcTime: 0` plus `reset()` after settle.

- [ ] **Step 2: Write failing dashboard, list, detail, and review tests**

Test seven count cards, staff/admin filters, deterministic pagination, mobile cards and desktop table. Student/guardian fixtures render only published history; coach/staff/admin fixtures render only server-authorized queues. Task detail renders sanitized previews, fields, confidence, warnings, score, grade, components, advice and version history without recalculating the score.

Review tests enforce a read-only decision screen: approve/reject buttons, required rejection reason, no editable result inputs, disabled submit during request, 409 refresh prompt and self-review error. Match, manual draft, correction, revision, archive and reopen controls appear only from server capability flags.

Add focused tests for published student/family history in the existing multi-student `LearningPage`, adapter certification list/create, automatic analysis-profile configuration, service-assignment permission labels for `evaluation_jobs.manual_input` and `evaluation_jobs.review`, and direct-route capability guards. `evaluation_jobs.match` and `evaluation_adapters.certify` remain administrator/global capabilities rather than staff-assignment labels.

- [ ] **Step 3: Write failing responsive/accessibility tests**

At 1440, 1024, 768 and 360 CSS pixels assert no horizontal overflow, minimum 44×44 key actions, correct table-to-card switch, bottom-navigation clearance and full-screen mobile form. Dialogs trap initial focus, Escape/cancel close them and restore focus to the trigger. Poll active statuses only; stop polling published/archived tasks and when the page is hidden.

- [ ] **Step 4: Run RED tests**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/systemEvaluationApi.test.ts \
  src/evaluations/systemEvaluationKeys.test.ts \
  src/evaluations/EvaluationDialogShell.test.tsx \
  src/evaluations/SystemEvaluationDashboard.test.tsx \
  src/evaluations/EvaluationTaskList.test.tsx \
  src/evaluations/EvaluationTaskDetail.test.tsx \
  src/evaluations/EvaluationTaskActions.test.tsx \
  src/evaluations/EvaluationReviewPanel.test.tsx \
  src/evaluations/PublishedEvaluationPanel.test.tsx \
  src/evaluations/AdapterCertificationsPanel.test.tsx \
  src/evaluations/AnalysisProfilesPanel.test.tsx \
  src/evaluations/CohortStaffPanel.test.tsx \
  src/portal/LearningPage.test.tsx src/app/router.test.tsx
```

Expected: FAIL because the system-evaluation client and components do not exist.

- [ ] **Step 5: Implement typed API client and server-authoritative capability rendering**

Define discriminated task/result/status types matching the v2 account DTO and parse them through strict allowlists. Validate unexpected enums by surfacing a generic reload state. Components render action buttons only from per-resource `allowed_actions`; they do not infer authorization from role names. Use separate query keys for account, staff and administrator projections, all nested under the account-specific evaluation root.

- [ ] **Step 6: Implement responsive dashboard, task list/detail, and history**

Add count cards and filters to evaluation center, keep the score-tool iframe/open-new-page entry and its frozen `game-score-tool.ready/v1` message unchanged, and provide ERP-native `/evaluations/system/tasks` and `/evaluations/system/tasks/:taskId` routes. Extract the existing accessible dialog behavior into `EvaluationDialogShell`. Desktop detail uses preview/result columns; tablet/mobile uses a single reading order. Images use protected preview URLs with `referrerPolicy="same-origin"`, no data-URL persistence and explicit loading/error alt text.

- [ ] **Step 7: Implement manual, match, review, correction, and revision panels**

Render project-required fields dynamically from the task snapshot. Draft writes remain explicit; submit-review rechecks required UI inputs but trusts server validation. Review never shares editable controls. On mutation success invalidate counts/list/detail/history; on 409 keep entered text in component state, refetch server state and ask the operator to compare before retry.

Connect `PublishedEvaluationPanel` to the existing multi-student `LearningPage`. Add administrator queues and `AdapterCertificationsPanel` to `EvaluationAdminPage`, enable automatic analysis-profile fields in `AnalysisProfilesPanel`, and expose only the two assignable evaluation-job permissions in `CohortStaffPanel`. Route guards revalidate server capability and remove cached task/detail data immediately on revocation or private 404.

- [ ] **Step 8: Run GREEN tests, lint, and production build**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/systemEvaluationApi.test.ts \
  src/evaluations/systemEvaluationKeys.test.ts \
  src/evaluations/EvaluationDialogShell.test.tsx \
  src/evaluations/SystemEvaluationDashboard.test.tsx \
  src/evaluations/EvaluationTaskList.test.tsx \
  src/evaluations/EvaluationTaskDetail.test.tsx \
  src/evaluations/EvaluationTaskActions.test.tsx \
  src/evaluations/EvaluationReviewPanel.test.tsx \
  src/evaluations/PublishedEvaluationPanel.test.tsx \
  src/evaluations/AdapterCertificationsPanel.test.tsx \
  src/evaluations/SystemEvaluationPage.test.tsx \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/evaluations/EvaluationAdminPage.test.tsx \
  src/evaluations/AnalysisProfilesPanel.test.tsx \
  src/evaluations/CohortStaffPanel.test.tsx \
  src/portal/LearningPage.test.tsx src/app/router.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all selected tests pass; ESLint and Vite production build exit 0.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/evaluations/systemEvaluationApi.ts \
  apps/web/src/evaluations/systemEvaluationApi.test.ts \
  apps/web/src/evaluations/systemEvaluationKeys.ts \
  apps/web/src/evaluations/systemEvaluationKeys.test.ts \
  apps/web/src/evaluations/useSystemEvaluationContext.ts \
  apps/web/src/evaluations/EvaluationDialogShell.tsx \
  apps/web/src/evaluations/EvaluationDialogShell.test.tsx \
  apps/web/src/evaluations/SystemEvaluationDashboard.tsx \
  apps/web/src/evaluations/SystemEvaluationDashboard.test.tsx \
  apps/web/src/evaluations/EvaluationTaskList.tsx \
  apps/web/src/evaluations/EvaluationTaskList.test.tsx \
  apps/web/src/evaluations/EvaluationTaskDetail.tsx \
  apps/web/src/evaluations/EvaluationTaskDetail.test.tsx \
  apps/web/src/evaluations/EvaluationTaskActions.tsx \
  apps/web/src/evaluations/EvaluationTaskActions.test.tsx \
  apps/web/src/evaluations/EvaluationReviewPanel.tsx \
  apps/web/src/evaluations/EvaluationReviewPanel.test.tsx \
  apps/web/src/evaluations/PublishedEvaluationPanel.tsx \
  apps/web/src/evaluations/PublishedEvaluationPanel.test.tsx \
  apps/web/src/evaluations/AdapterCertificationsPanel.tsx \
  apps/web/src/evaluations/AdapterCertificationsPanel.test.tsx \
  apps/web/src/evaluations/system-evaluation.css \
  apps/web/src/evaluations/EvaluationCenterPage.tsx \
  apps/web/src/evaluations/EvaluationCenterPage.test.tsx \
  apps/web/src/evaluations/EvaluationAdminPage.tsx \
  apps/web/src/evaluations/EvaluationAdminPage.test.tsx \
  apps/web/src/evaluations/AnalysisProfilesPanel.tsx \
  apps/web/src/evaluations/AnalysisProfilesPanel.test.tsx \
  apps/web/src/evaluations/CohortStaffPanel.tsx \
  apps/web/src/evaluations/CohortStaffPanel.test.tsx \
  apps/web/src/evaluations/api.ts \
  apps/web/src/evaluations/api.test.ts \
  apps/web/src/evaluations/foundationApi.ts \
  apps/web/src/evaluations/foundationApi.test.ts \
  apps/web/src/evaluations/evaluations.css \
  apps/web/src/portal/LearningPage.tsx apps/web/src/portal/LearningPage.test.tsx \
  apps/web/src/portal/portal.css \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx
git commit -m "feat: add system evaluation operations UI"
```

---

### Task 8: Lock the dual-repository contract and run local end-to-end gates

**Files:**

- Create: `apps/api/app/cli/seed_system_evaluation_e2e.py`
- Create: `apps/api/tests/cli/test_seed_system_evaluation_e2e.py`
- Create: `apps/web/tests/system-evaluation-processing.spec.ts`
- Create: `apps/web/tests/system-evaluation-privacy.spec.ts`
- Create: `scripts/check-evaluation-v2-contract.sh`
- Create: `scripts/run-system-evaluation-e2e.sh`
- Modify: `apps/api/tests/contracts/evaluation_integration_v2.json`
- Modify: `apps/api/tests/cli/test_e2e_launchers.py`
- Modify: `apps/web/package.json`
- Modify: `README.md`
- Modify in score tool: `tests/contracts/evaluation_integration_v2.json`
- Modify in score tool: `README.txt`
- Modify in score tool: `ARCHITECTURE.md`

- [ ] **Step 1: Write failing contract and launcher tests**

`check-evaluation-v2-contract.sh` requires both absolute repository roots, verifies both are Git worktrees, then uses `cmp` on the v2 JSON fixtures. Launcher tests assert fail-closed database safety, port checks, separate v1/Web/Worker secrets, API/Web/score-Web/score-Worker process tracking, bounded readiness and cleanup. Worker readiness must require a live process plus the exact `score-worker ready` marker after a successful capabilities check; a stale log line cannot satisfy readiness.

- [ ] **Step 2: Write failing deterministic seed tests**

Seed two students, one active guardian relation, two staff users, one automatic League enrollment, one manual-only project enrollment, project staff assignments, required permission overrides and analysis snapshots. Use generated synthetic PNGs containing no personal information. Re-running the seed produces the same business rows and replaces only E2E-owned quarantine/media fixtures. Assert the command refuses a non-test database.

- [ ] **Step 3: Write failing four-viewport E2E journeys**

`system-evaluation-processing.spec.ts` executes login → target/project selection → slot upload → task creation → Worker completion → independent reviewer approval → student and guardian published history. It also covers manual-only input and correction after rejection. `system-evaluation-privacy.spec.ts` checks missing/disabled/denied/wrong-scope private 404 equivalence, self-review rejection, stale-version 409, hidden candidate identity, immediate access loss and absence of horizontal overflow at all four configured Playwright projects.

- [ ] **Step 4: Run RED tests**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/cli/test_seed_system_evaluation_e2e.py \
  apps/api/tests/cli/test_e2e_launchers.py -q
bash scripts/check-evaluation-v2-contract.sh \
  "$PWD" \
  '/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing'
```

Expected: FAIL because the seed command, v2 comparison script and full launcher do not exist.

- [ ] **Step 5: Implement seed, exact contract comparison, and five-process launcher**

Start ERP API, ERP Web, score Web and score Worker alongside PostgreSQL. Use unique configurable ports, separate log files, PID liveness checks and bounded cleanup. Pass `ERP_SERVICE_TOKEN` only for v1, `ERP_WEB_SERVICE_TOKEN` only to score Web, and `ERP_WORKER_SERVICE_TOKEN` only to Worker. Configure the E2E analysis profile with the test-only synthetic adapter; production adapter golden tests remain a separate gate. Do not add Docker files. Run `cmp` before starting browsers and refuse mismatched contract bytes.

- [ ] **Step 6: Implement the two E2E specifications and local operator docs**

Use role-specific storage states created through real login. Poll task status with bounded Playwright assertions, not fixed sleeps. Assert the result score comes from the API and that first-release automatic results remain in review until a different account approves. Document local start commands, secret separation, media directory safety, Worker readiness, E2E prerequisites, deferred Docker/NAS/video scope and the explicit statement that golden tests do not certify production auto-publish.

- [ ] **Step 7: Run both repository focused suites and four-viewport E2E**

```bash
export PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
export TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test'
: "${FIELD_ENCRYPTION_MASTER_KEY:?set the existing local test key}"
: "${FIELD_LOOKUP_KEY:?set the existing local test key}"
: "${SESSION_TOKEN_PEPPER:?set the existing local test key}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?set the local v1 test token}"
: "${EVALUATION_TOOL_WEB_SERVICE_TOKEN:?set a distinct local Web test token}"
: "${EVALUATION_TOOL_WORKER_SERVICE_TOKEN:?set a distinct local Worker test token}"
bash scripts/run-system-evaluation-e2e.sh
```

Expected: `system-evaluation-processing.spec.ts` and `system-evaluation-privacy.spec.ts` pass in desktop-1440, desktop-1024, tablet-768 and mobile-360; score Worker is confirmed live before the tests begin.

- [ ] **Step 8: Run final ERP gates**

```bash
PYTHONPATH="$PWD/apps/api" \
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test' \
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider apps/api/tests -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
PYTHONPATH="$PWD/apps/api" DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m alembic -c apps/api/alembic.ini check
git diff --check
```

Expected: complete PostgreSQL suite, Web suite, Ruff, mypy, ESLint and build pass; Alembic reports no new upgrade operations and `git diff --check` is clean.

- [ ] **Step 9: Run final score-tool gates**

```bash
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing'
SCORE_PY='/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python'
"$SCORE_PY" -m pytest -q
"$SCORE_PY" -m compileall -q .
git diff --check
```

Expected: all score-tool tests pass, compileall exits 0 and the worktree has no whitespace errors.

- [ ] **Step 10: Commit E2E and documentation in each repository**

ERP:

```bash
git add apps/api/app/cli/seed_system_evaluation_e2e.py \
  apps/api/tests/cli/test_seed_system_evaluation_e2e.py \
  apps/api/tests/cli/test_e2e_launchers.py \
  apps/api/tests/contracts/evaluation_integration_v2.json \
  apps/web/tests/system-evaluation-processing.spec.ts \
  apps/web/tests/system-evaluation-privacy.spec.ts apps/web/package.json \
  scripts/check-evaluation-v2-contract.sh scripts/run-system-evaluation-e2e.sh \
  README.md
git commit -m "test: verify system evaluation journeys"
```

Score tool:

```bash
git add tests/contracts/evaluation_integration_v2.json README.txt ARCHITECTURE.md
git commit -m "docs: explain system evaluation v2 operation"
```

## Final acceptance checklist

- ERP is the only formal store for upload ownership, tasks, leases, results, review, publication and versions.
- New screenshots exist only in ERP private media storage; no score-tool legacy directory is read or written.
- Three adapters run locally but cannot auto-publish in this release; unsupported projects complete through manual input.
- Five-minute leases, renewal below two minutes, maximum three automatic attempts and stale-attempt rejection are covered by real PostgreSQL tests.
- Result authors cannot review their own versions; uploaded-only actors are not treated as authors.
- Students and guardians read published results only; staff/admin object scope, explicit deny and private 404 behavior are consistent across list/detail/media/action.
- Both repositories carry byte-identical v2 contracts and distinct Web/Worker service identities.
- Desktop 1440/1024, tablet 768 and mobile 360 journeys pass without horizontal overflow.
- Docker/NAS/video remain untouched and deferred.
