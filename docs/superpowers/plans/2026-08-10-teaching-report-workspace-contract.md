# Teaching Report Workspace Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐教学评语作者目标目录与版本时间线契约，并完成作者、审核、家庭三类响应式 Web 体验和即时撤权边界。

**Architecture:** 新建只读 `TeachingReportWorkspaceQueryService`，复用现有作者 slot 与审核私有选择器，向两个新增 GET 路由提供严格投影；现有状态机和数据库不变。React 以账号级 report query root、实时 capability guard 和 AbortSignal 隔离私有缓存，作者编辑使用一次性 opaque payload，版本比较、审核和家庭投影保持分离。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL 16、Pydantic 2、Pytest、React 19、TypeScript 6、TanStack Query、Zod、Vitest

---

## Execution context and frozen boundaries

- Worktree: `/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing`
- Branch: `codex/teaching-feedback-surveys`
- Approved spec: `docs/superpowers/specs/2026-08-10-teaching-report-workspace-contract-design.md`
- Parent plan: `docs/superpowers/plans/2026-08-09-teaching-reports.md`, Tasks 5–6
- Preserve the existing uncommitted Task 6 files. They are partial work, not disposable scaffolding.
- Do not modify Docker, NAS, GPU, video, payment, police-reporting, score-tool, migrations, or Task 7 Playwright files.
- Every production behavior follows RED → verify RED → minimal GREEN → verify GREEN → refactor.
- Every task receives spec review before code-quality review. Fix every Critical/Important issue and request re-review before continuing.
- PostgreSQL tests require an isolated `TEST_DATABASE_URL`; a skipped database test is not a passing gate.

## File responsibility map

### Backend

- Create `apps/api/app/evaluations/report_workspace_query_service.py`: authorized target directory and immutable version timeline only.
- Create `apps/api/tests/services/test_teaching_report_workspace_queries.py`: service authorization, projection, filtering, pagination, and zero-write matrix.
- Modify `apps/api/app/evaluations/report_api_schemas.py`: strict targets/facets/version response models.
- Modify `apps/api/app/evaluations/dependencies.py`: construct the new read service.
- Modify `apps/api/app/evaluations/report_router.py`: register `targets` before dynamic detail and expose `versions`.
- Modify `apps/api/tests/api/test_teaching_reports.py`: exact 14-route OpenAPI and HTTP privacy contract.

### Web

- Modify existing partial `apps/web/src/evaluations/reportApi.ts` and test: targets/versions, AbortSignal, allowlisted decoding.
- Modify existing partial `apps/web/src/evaluations/reportKeys.ts` and test: account-isolated target/version keys.
- Create `apps/web/src/evaluations/useTeachingReportContext.ts`: live report capability query.
- Create `apps/web/src/evaluations/ReportVersionTimeline.tsx` and test: immutable history and two-version comparison.
- Create `apps/web/src/evaluations/TeachingReportEditorDialog.tsx` and test: draft/submit/correction/revision actions with opaque payloads.
- Create `apps/web/src/evaluations/TeachingReportsPage.tsx` and test: authorized filters, target selection, slots, timeline composition.
- Modify existing partial review/family components and tests; complete routing, entry card, CSS, and dialog locking.

## Specification coverage map

| Approved requirement | Implemented and verified in |
| --- | --- |
| Authorized targets, facets, filters, stable pagination | Tasks 1–3, 7 |
| Author/reviewer immutable versions and rejection reason | Tasks 1–3, 5, 8 |
| 403 capability vs private 404 target split and zero writes | Tasks 1–2, 9 |
| Zod allowlisting, AbortSignal, account-isolated cache | Tasks 3–4, 8 |
| Server-only `allowed_actions` and opaque mutation payloads | Tasks 6–7 |
| Evaluation entry, author/reviewer/family route behavior | Tasks 4, 8 |
| Family published-only panel | Task 8 |
| 360px, 44px, safe area, focus and body scroll lock | Task 8 |
| Full backend/Web gates and dual review | Task 9 |
| No Docker/NAS/GPU/video/Task 7 changes | Tasks 1–9 scope checks |

## Task 1: Add authorized workspace query projections

**Files:**

- Create: `apps/api/app/evaluations/report_workspace_query_service.py`
- Create: `apps/api/tests/services/test_teaching_report_workspace_queries.py`
- Read: `apps/api/app/evaluations/report_access.py`
- Read: `apps/api/app/evaluations/report_query_service.py`

- [ ] **Step 1: Write failing service tests for target membership and facets**

Create the test file with the existing `ReportAccessScenario` fixture and these concrete assertions:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.report_models import (
    StudentReport,
    StudentReportReview,
    StudentReportVersion,
)
from app.evaluations.report_service import TeachingReportService
from app.evaluations.report_workspace_query_service import (
    TeachingReportTargetFilters,
    TeachingReportWorkspaceQueryService,
)
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import Person, StudentProfile
from app.teaching.models import GameProject
from tests.services.test_teaching_report_access import ReportAccessScenario
from tests.services.test_teaching_reports import draft_input, report_service

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def test_author_targets_reuse_real_author_slots_and_return_complete_facets(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportWorkspaceQueryService(db)

    primary = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    )
    assert primary.total == 1
    assert primary.items[0].enrollment_id == scenario.enrollment.id
    assert primary.items[0].student_name == scenario.people["student"].full_name
    assert primary.facets.cohorts[0].id == scenario.cohort.id
    assert primary.facets.projects[0].cohort_ids == (scenario.cohort.id,)
    assert primary.facets.classes[0].project_id == scenario.teaching_class.project_id

    scenario.allow_account("assistant")
    assert service.author_targets(
        account_id=scenario.accounts["assistant"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    ).total == 1

    scenario.attach_staff_permission()
    assert service.author_targets(
        account_id=scenario.accounts["staff"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(page=1, page_size=20),
    ).total == 1


def test_author_target_filters_are_literal_authorized_and_paginated(
    db: Session,
    report_access_scenario: ReportAccessScenario,
) -> None:
    scenario = report_access_scenario
    service = TeachingReportWorkspaceQueryService(db)
    profile = db.get(StudentProfile, scenario.people["student"].id)
    assert profile is not None

    matching = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(
            cohort_id=scenario.cohort.id,
            project_id=scenario.teaching_class.project_id,
            class_id=scenario.teaching_class.id,
            student_id=scenario.people["student"].id,
            query=profile.student_number,
            page=1,
            page_size=1,
        ),
    )
    assert matching.total == 1
    assert len(matching.items) == 1
    assert matching.page == matching.page_size == 1

    for literal in ("%", "_", "not-present"):
        empty = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(query=literal, page=1, page_size=20),
        )
        assert empty.items == ()
        assert empty.total == 0
        assert empty.facets.cohorts

    wrong_scope = service.author_targets(
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        filters=TeachingReportTargetFilters(class_id=uuid4(), page=1, page_size=20),
    )
    assert wrong_scope.items == ()
    assert wrong_scope.total == 0
```

Freeze service-level validation independently of FastAPI:

```python
@pytest.mark.parametrize(
    "filters",
    (
        TeachingReportTargetFilters(page=0, page_size=20),
        TeachingReportTargetFilters(page=1, page_size=0),
        TeachingReportTargetFilters(page=1, page_size=101),
        TeachingReportTargetFilters(query="x" * 81, page=1, page_size=20),
    ),
)
def test_author_targets_reject_invalid_time_and_filters(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    filters: TeachingReportTargetFilters,
) -> None:
    service = TeachingReportWorkspaceQueryService(db)
    with pytest.raises(ValueError):
        service.author_targets(
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=filters,
        )
    with pytest.raises(ValueError):
        service.author_targets(
            account_id=report_access_scenario.accounts["primary"].id,
            occurred_at=NOW.replace(tzinfo=None),
            filters=TeachingReportTargetFilters(page=1, page_size=20),
        )
```

Add this complete failure matrix:

```python
@pytest.mark.parametrize(
    "failure",
    (
        "future_assignment",
        "deny",
        "disabled_account",
        "deleted_student",
        "inactive_student",
        "cancelled_enrollment",
        "inactive_project",
        "inactive_class",
    ),
)
def test_author_targets_hide_ineligible_resources_without_writes(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    failure: str,
) -> None:
    scenario = report_access_scenario
    if failure == "future_assignment":
        scenario.primary_assignment.valid_from = NOW + timedelta(hours=1)
    elif failure == "deny":
        scenario.deny_account("primary")
    elif failure == "disabled_account":
        scenario.accounts["primary"].status = "disabled"
    elif failure == "deleted_student":
        scenario.people["student"].deleted_at = NOW
    elif failure == "inactive_student":
        profile = db.get(StudentProfile, scenario.people["student"].id)
        assert profile is not None
        profile.status = "inactive"
    elif failure == "cancelled_enrollment":
        scenario.enrollment.status = "cancelled"
        scenario.enrollment.cancelled_at = NOW
    elif failure == "inactive_project":
        project = db.get(GameProject, scenario.teaching_class.project_id)
        assert project is not None
        project.is_active = False
    else:
        scenario.teaching_class.is_active = False
    db.flush()
    before = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
        db.scalar(select(func.count(AuditEvent.id))),
    )

    service = TeachingReportWorkspaceQueryService(db)
    if failure in {"future_assignment", "disabled_account"}:
        with pytest.raises(EvaluationOperationDenied):
            service.author_targets(
                account_id=scenario.accounts["primary"].id,
                occurred_at=NOW,
                filters=TeachingReportTargetFilters(page=1, page_size=20),
            )
    else:
        page = service.author_targets(
            account_id=scenario.accounts["primary"].id,
            occurred_at=NOW,
            filters=TeachingReportTargetFilters(page=1, page_size=20),
        )
        assert page.items == ()
        assert page.total == 0

    after = (
        db.scalar(select(func.count(StudentReport.id))),
        db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
        db.scalar(select(func.count(AuditEvent.id))),
    )
    assert after == before
```

- [ ] **Step 2: Write failing service tests for immutable version visibility**

Use `TeachingReportService.save_draft` to create the report, then insert two immutable versions and their reviews without adding a new fixture layer:

```python
def test_report_versions_allow_author_or_reviewer_and_expose_no_internal_identity(
    db: Session,
    report_access_scenario: ReportAccessScenario,
    report_service: TeachingReportService,
) -> None:
    scenario = report_access_scenario
    created = report_service.save_draft(
        draft_input(scenario, body="版本一正文"),
        actor_user_id=scenario.accounts["primary"].id,
        occurred_at=NOW,
        idempotency_key=f"workspace-draft-{uuid4()}",
        request_id=f"workspace-draft-{uuid4()}",
    )
    first = StudentReportVersion(
        report_id=created.report.id,
        version=1,
        source="initial",
        body="版本一正文",
        dimension_values={},
        submitted_by_user_id=scenario.accounts["primary"].id,
        submitted_at=NOW,
    )
    db.add(first)
    db.flush()
    second = StudentReportVersion(
        report_id=created.report.id,
        version=2,
        source="revision",
        body="版本二正文",
        dimension_values={},
        submitted_by_user_id=scenario.accounts["primary"].id,
        submitted_at=NOW + timedelta(hours=2),
        previous_version_id=first.id,
    )
    db.add(second)
    db.flush()
    db.delete(created.draft)
    created.report.status = "rejected"
    created.report.current_version_id = second.id
    created.report.published_version_id = first.id
    created.report.submitted_at = second.submitted_at
    created.report.published_at = NOW + timedelta(hours=1)
    db.add_all([
        StudentReportReview(
            report_id=created.report.id,
            version_id=first.id,
            decision="approved",
            reason=None,
            reviewed_by_user_id=scenario.accounts["admin"].id,
            reviewed_at=NOW + timedelta(hours=1),
        ),
        StudentReportReview(
            report_id=created.report.id,
            version_id=second.id,
            decision="rejected",
            reason="需要补充复盘结论",
            reviewed_by_user_id=scenario.accounts["admin"].id,
            reviewed_at=NOW + timedelta(hours=3),
        ),
    ])
    db.flush()

    service = TeachingReportWorkspaceQueryService(db)
    author_page = service.report_versions(
        created.report.id,
        account_id=scenario.accounts["primary"].id,
        occurred_at=NOW + timedelta(hours=4),
        page=1,
        page_size=20,
    )
    reviewer_page = service.report_versions(
        created.report.id,
        account_id=scenario.accounts["admin"].id,
        occurred_at=NOW + timedelta(hours=4),
        page=1,
        page_size=20,
    )

    assert [item.version for item in author_page.items] == [2, 1]
    assert reviewer_page.items == author_page.items
    assert author_page.items[0].review is not None
    assert author_page.items[0].review.reason == "需要补充复盘结论"
    assert author_page.items[0].is_current is True
    assert author_page.items[0].is_published is False
    assert author_page.items[1].is_published is True
    assert not hasattr(author_page.items[0], "reviewed_by_user_id")
    assert not hasattr(author_page.items[0], "window_grant_reason")
```

Also assert: outsider with neither capability raises `EvaluationOperationDenied`; an allowed assistant targeting another author's report and a random UUID both raise `EvaluationResourceNotFound`; revoking the admin review permission hides the report on the next call; all failures leave report state and the three write-table counts unchanged.

- [ ] **Step 3: Run the new service tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_teaching_report_workspace_queries.py -q
```

Expected: collection fails because `app.evaluations.report_workspace_query_service` does not exist.

- [ ] **Step 4: Define the immutable service types and input guards**

Create these immutable public types and signatures:

```python
@dataclass(frozen=True)
class TeachingReportTargetFilters:
    cohort_id: UUID | None = None
    project_id: UUID | None = None
    class_id: UUID | None = None
    student_id: UUID | None = None
    query: str | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class TeachingReportTargetItem:
    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: datetime
    cohort_ends_at: datetime
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str


@dataclass(frozen=True)
class TeachingReportCohortFacet:
    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class TeachingReportProjectFacet:
    id: UUID
    name: str
    cohort_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class TeachingReportClassFacet:
    id: UUID
    name: str
    cohort_id: UUID
    project_id: UUID


@dataclass(frozen=True)
class TeachingReportTargetFacets:
    cohorts: tuple[TeachingReportCohortFacet, ...]
    projects: tuple[TeachingReportProjectFacet, ...]
    classes: tuple[TeachingReportClassFacet, ...]


@dataclass(frozen=True)
class TeachingReportTargetPage:
    items: tuple[TeachingReportTargetItem, ...]
    facets: TeachingReportTargetFacets
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class TeachingReportVersionReviewItem:
    decision: str
    reason: str | None
    reviewed_at: datetime


@dataclass(frozen=True)
class TeachingReportVersionItem:
    report_id: UUID
    version_id: UUID
    version: int
    source: str
    body: str
    dimension_values: dict[str, Any]
    submitted_at: datetime
    is_current: bool
    is_published: bool
    review: TeachingReportVersionReviewItem | None


@dataclass(frozen=True)
class TeachingReportVersionPage:
    items: tuple[TeachingReportVersionItem, ...]
    total: int
    page: int
    page_size: int
```

Add `_require_aware()` and pagination/query validation. Reject a naive datetime, `page < 1`, a page size outside 1–100, or a trimmed query longer than 80. Add direct unit assertions for each rejection before continuing.

- [ ] **Step 5: Implement the authorized target directory**

Implement `TeachingReportWorkspaceQueryService.author_targets()` with this exact authorization order:

1. reject naive `occurred_at` and invalid pagination;
2. call `TeachingReportQueryService.author_capable()` before selecting targets;
3. select only live candidate `Enrollment + Cohort + TeachingClass + GameProject + StudentProfile + Person` rows;
4. for each candidate call the existing `TeachingReportAccessService.author_slots()` once, catch only `EvaluationResourceNotFound`, and include the row only when at least one slot is returned;
5. build complete facets from all authorized rows before request filters;
6. normalize query with `strip().casefold()` and use literal substring matching so `%` and `_` never become wildcards;
7. keep `GameProject.sort_order` as internal sort metadata but never project it to the response;
8. apply ID/query filters, sort by `(-cohort_starts_at.timestamp(), cohort_id, project_sort_order, project_name, project_id, class_name, class_id, student_name, student_number, student_person_id, enrollment_id)`, then compute total, offset, and limit.

Sort facet tuples deterministically: cohorts by descending start time then ID, projects by `sort_order`/name/ID with sorted unique `cohort_ids`, and classes by cohort/project/name/ID.

- [ ] **Step 6: Implement the author/reviewer version projection**

Implement `report_versions()` by checking author/reviewer capability before report lookup, accepting the report when either `require_author_report()` or `require_review_report()` succeeds, then outer-joining `StudentReportVersion` to its single `StudentReportReview`. Never return reviewer IDs, window grants, or ORM models.

- [ ] **Step 7: Run service tests GREEN and static checks**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_teaching_report_workspace_queries.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check \
  apps/api/app/evaluations/report_workspace_query_service.py \
  apps/api/tests/services/test_teaching_report_workspace_queries.py
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy \
  --config-file apps/api/pyproject.toml \
  apps/api/app/evaluations/report_workspace_query_service.py
```

Expected: all service tests pass; Ruff and mypy report no errors.

- [ ] **Step 8: Commit the service boundary**

```bash
git add \
  apps/api/app/evaluations/report_workspace_query_service.py \
  apps/api/tests/services/test_teaching_report_workspace_queries.py
git commit -m "feat: add teaching report workspace queries"
```

## Task 2: Expose targets and versions APIs

**Files:**

- Modify: `apps/api/app/evaluations/report_api_schemas.py`
- Modify: `apps/api/app/evaluations/dependencies.py`
- Modify: `apps/api/app/evaluations/report_router.py`
- Modify: `apps/api/tests/api/test_teaching_reports.py`

- [ ] **Step 1: Extend the exact OpenAPI and HTTP tests first**

Update `test_openapi_exposes_the_frozen_teaching_report_routes` so the expected set contains exactly:

```python
"/api/v1/evaluations/reports/targets": {"get"},
"/api/v1/evaluations/reports/{report_id}/versions": {"get"},
```

Add `targets` to the static paths asserted before `/reports/{report_id}`. Extend typed response assertions with
`TeachingReportTargetPageResponse` and `TeachingReportVersionPageResponse`.

Add these HTTP tests using `report_api_scenario`:

```python
def test_author_targets_api_filters_facets_and_private_projection(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_api_scenario.login(db_client, "primary")
    response = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={
            "cohort_id": str(scenario.cohort.id),
            "project_id": str(scenario.teaching_class.project_id),
            "class_id": str(scenario.teaching_class.id),
            "student_id": str(scenario.people["student"].id),
            "query": scenario.people["student"].full_name,
            "page": 1,
            "page_size": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["enrollment_id"] == str(scenario.enrollment.id)
    assert payload["facets"]["cohorts"][0]["id"] == str(scenario.cohort.id)
    serialized = response.text
    for forbidden in (
        "phone", "email", "identity", "payment", "guardian",
        "assignment_id", "permission_id", "audit", "game_id",
    ):
        assert forbidden not in serialized

    empty = db_client.get(
        "/api/v1/evaluations/reports/targets",
        params={"class_id": str(uuid4())},
    )
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0


def test_report_versions_api_masks_missing_and_denied_targets_identically(
    db_client: TestClient,
    report_api_scenario: ReportApiScenario,
) -> None:
    scenario = report_api_scenario.report
    report_id, _ = _create_pending_report(
        db_client,
        report_api_scenario,
        starts_on=NOW.date(),
        body="版本接口正文",
    )
    report_api_scenario.login(db_client, "primary")
    author = db_client.get(
        f"/api/v1/evaluations/reports/{report_id}/versions",
        params={"page": 1, "page_size": 20},
    )
    assert author.status_code == 200
    assert author.json()["items"][0]["body"] == "版本接口正文"

    scenario.allow_account("assistant")
    report_api_scenario.login(db_client, "assistant")
    denied = db_client.get(f"/api/v1/evaluations/reports/{report_id}/versions")
    missing = db_client.get(f"/api/v1/evaluations/reports/{uuid4()}/versions")
    assert denied.status_code == missing.status_code == 404
    assert denied.json() == missing.json()
```

Add parameterized bounds (`page=0`, `page_size=0`, `page_size=101`), no-capability 403, reviewer access, immediate reviewer revocation, inactive/deleted/cancelled target equivalence, response-field allowlist, and before/after counts for report/draft/version/review/window-grant/idempotency/audit tables.

- [ ] **Step 2: Run focused API tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider \
  apps/api/tests/api/test_teaching_reports.py::test_openapi_exposes_the_frozen_teaching_report_routes \
  apps/api/tests/api/test_teaching_reports.py::test_author_targets_api_filters_facets_and_private_projection \
  apps/api/tests/api/test_teaching_reports.py::test_report_versions_api_masks_missing_and_denied_targets_identically -q
```

Expected: tests fail because both routes and response models are absent.

- [ ] **Step 3: Add strict Pydantic response models**

Add closed models matching the approved JSON names:

```python
class TeachingReportTargetItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: datetime
    cohort_ends_at: datetime
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str


class TeachingReportProjectFacetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: UUID
    name: str
    cohort_ids: list[UUID]


class TeachingReportVersionReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=2000)
    reviewed_at: datetime


class TeachingReportVersionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    report_id: UUID
    version_id: UUID
    version: int = Field(ge=1)
    source: TeachingReportVersionSource
    body: str = Field(min_length=1, max_length=5000)
    dimension_values: dict[str, Any]
    submitted_at: datetime
    is_current: bool
    is_published: bool
    review: TeachingReportVersionReviewResponse | None

    @field_validator("dimension_values")
    @classmethod
    def require_empty_dimensions(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value:
            raise ValueError("dimension_values must be empty")
        return value
```

Add the corresponding `TeachingReportCohortFacetResponse`, `TeachingReportClassFacetResponse`, `TeachingReportTargetFacetsResponse`, `TeachingReportTargetPageResponse`, and `TeachingReportVersionPageResponse` models with `extra="forbid"`, `page >= 1`, and `page_size <= 100`. Add `field_validator` to the Pydantic imports.

- [ ] **Step 4: Wire the read-service dependency and Problem response helper**

Add `get_teaching_report_workspace_query_service(db)`. Add a read-response helper so OpenAPI describes the same Problem Details shape produced by the global handlers:

```python
def _read_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    return {
        status: {"model": TeachingReportProblemResponse}
        for status in statuses
    }
```

Add `get_teaching_report_workspace_query_service(db)` to dependencies.

- [ ] **Step 5: Register the static target-directory route**

Register `/reports/targets` before `/reports/{report_id}`:

```python
@router.get(
    "/reports/targets",
    response_model=TeachingReportTargetPageResponse,
    responses=_read_responses(403, 422),
)
def get_report_targets(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    context_service: Annotated[EvaluationContextService, Depends(get_evaluation_context_service)],
    service: Annotated[
        TeachingReportWorkspaceQueryService,
        Depends(get_teaching_report_workspace_query_service),
    ],
    cohort_id: Annotated[UUID | None, Query()] = None,
    project_id: Annotated[UUID | None, Query()] = None,
    class_id: Annotated[UUID | None, Query()] = None,
    student_id: Annotated[UUID | None, Query()] = None,
    query: Annotated[str | None, Query(max_length=80)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TeachingReportTargetPageResponse:
    require_evaluation_center(context=context, service=context_service)
    try:
        result = service.author_targets(
            account_id=context.account.id,
            occurred_at=_now(),
            filters=TeachingReportTargetFilters(
                cohort_id=cohort_id,
                project_id=project_id,
                class_id=class_id,
                student_id=student_id,
                query=query,
                page=page,
                page_size=page_size,
            ),
        )
    except EvaluationOperationDenied as exc:
        raise _author_operation_denied() from exc
    return TeachingReportTargetPageResponse.model_validate(result, from_attributes=True)
```

- [ ] **Step 6: Register the version-history route**

The versions route declares `_read_responses(403, 404, 422)`, catches `EvaluationOperationDenied` as account-level 403 and `EvaluationResourceNotFound` as `private_not_found()`. It uses one `_now()` value and returns only `TeachingReportVersionPageResponse`.

- [ ] **Step 7: Run the complete new backend contract GREEN**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider \
  apps/api/tests/services/test_teaching_report_workspace_queries.py \
  apps/api/tests/api/test_teaching_reports.py -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check \
  apps/api/app/evaluations/report_api_schemas.py \
  apps/api/app/evaluations/dependencies.py \
  apps/api/app/evaluations/report_router.py \
  apps/api/app/evaluations/report_workspace_query_service.py \
  apps/api/tests/services/test_teaching_report_workspace_queries.py \
  apps/api/tests/api/test_teaching_reports.py
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy \
  --config-file apps/api/pyproject.toml \
  apps/api/app/evaluations/report_api_schemas.py \
  apps/api/app/evaluations/report_workspace_query_service.py \
  apps/api/app/evaluations/report_router.py
```

Expected: all selected tests pass; Ruff and mypy report no errors.

- [ ] **Step 8: Commit the API contract**

```bash
git add \
  apps/api/app/evaluations/report_api_schemas.py \
  apps/api/app/evaluations/dependencies.py \
  apps/api/app/evaluations/report_router.py \
  apps/api/tests/api/test_teaching_reports.py
git commit -m "feat: expose teaching report workspace api"
```

## Task 3: Extend the Web transport and cache contract

**Files:**

- Modify: `apps/web/src/evaluations/reportApi.ts`
- Modify: `apps/web/src/evaluations/reportApi.test.ts`
- Modify: `apps/web/src/evaluations/reportKeys.ts`
- Modify: `apps/web/src/evaluations/reportKeys.test.ts`

- [ ] **Step 1: Write RED tests for targets, versions, allowlisting, and cancellation**

Add fixtures matching the backend schema and assert exact URL order:

```typescript
const controller = new AbortController()
await listTeachingReportTargets(
  {
    cohortId,
    projectId,
    classId,
    studentId,
    query: " 学员_% ",
    page: 2,
    pageSize: 10,
  },
  controller.signal,
)
await listTeachingReportVersions(
  { reportId, page: 1, pageSize: 20 },
  controller.signal,
)

expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
  `/api/v1/evaluations/reports/targets?cohort_id=${cohortId}&project_id=${projectId}&class_id=${classId}&student_id=${studentId}&query=${encodeURIComponent("学员_%")}&page=2&page_size=10`,
  `/api/v1/evaluations/reports/${reportId}/versions?page=1&page_size=20`,
])
expect(fetchMock.mock.calls.every(([, init]) => init?.signal === controller.signal)).toBe(true)
```

Put `phone`, `identity_number`, `reviewed_by_user_id`, `reopen_reason`, and `audit_request_id` in mocked server responses; assert the returned parsed values contain none of them. Feed a non-empty `dimension_values` and an unknown decision/action and assert the generic invalid-response error without echoing the private value.

Extend key tests:

```typescript
const targetFilters = {
  cohortId,
  projectId,
  classId,
  studentId,
  query: "学员_%",
  page: 2,
  pageSize: 10,
}
expect(reportKeys.targets("account-a", targetFilters)).toEqual([
  "teaching-reports",
  "account-a",
  "targets",
  normalizeReportTargetFilters(targetFilters),
])
expect(reportKeys.versions("account-a", { reportId, page: 1, pageSize: 20 })).toEqual([
  "teaching-reports",
  "account-a",
  "versions",
  { reportId, page: 1, pageSize: 20 },
])
```

- [ ] **Step 2: Run the two files and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/reportApi.test.ts \
  src/evaluations/reportKeys.test.ts
```

Expected: tests fail because targets/versions types, functions, and keys are absent and existing GET functions do not forward signals.

- [ ] **Step 3: Implement normalized filters and account-isolated query keys**

Add exact filter types:

```typescript
export type ReportTargetFilters = {
  cohortId?: string | null
  projectId?: string | null
  classId?: string | null
  studentId?: string | null
  query?: string | null
  page?: number
  pageSize?: number
}

export type ReportVersionFilters = {
  reportId: string
  page?: number
  pageSize?: number
}
```

Implement and export `normalizeReportTargetFilters()` and `normalizeReportVersionFilters()`. Normalize missing IDs to `null`, trim `query` and convert an empty result to `null`, and default pagination to 1/20 before query-key construction. Add `reportKeys.targets()` and `reportKeys.versions()` using only the normalized public filters.

- [ ] **Step 4: Add Zod allowlist schemas for targets and versions**

Add Zod schemas for target items, cohort/project/class facets, target pages, version reviews, version items, and version pages. Keep `EMPTY_DIMENSIONS` for versions. Use `.min(1).max(5000)` for bodies, the existing enum schemas for source/decision, and generic invalid-response errors that never include raw private values.

- [ ] **Step 5: Implement cancellable GET functions**

Change every report GET function to accept an optional `AbortSignal` and pass it through `fetchDecoded(path, schema, { signal })`.

Implement:

```typescript
export function listTeachingReportTargets(
  input: ReportTargetFilters = {},
  signal?: AbortSignal,
): Promise<TeachingReportTargetPage>

export function listTeachingReportVersions(
  input: ReportVersionFilters,
  signal?: AbortSignal,
): Promise<TeachingReportVersionPage>
```

Do not add bodies, rejection reasons, or reopen reasons to any query key. Query keys may contain only account ID, public object IDs, normalized filters, search text, and pagination.

- [ ] **Step 6: Run transport tests GREEN and the existing component tests**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/reportApi.test.ts \
  src/evaluations/reportKeys.test.ts \
  src/evaluations/PublishedReportsPanel.test.tsx \
  src/evaluations/TeachingReportReviewPage.test.tsx
```

Expected: all selected tests pass with no console warnings.

- [ ] **Step 7: Commit the Web contract**

```bash
git add \
  apps/web/src/evaluations/reportApi.ts \
  apps/web/src/evaluations/reportApi.test.ts \
  apps/web/src/evaluations/reportKeys.ts \
  apps/web/src/evaluations/reportKeys.test.ts
git commit -m "feat: add teaching report web contract"
```

## Task 4: Add live report context and evaluation entry

**Files:**

- Create: `apps/web/src/evaluations/useTeachingReportContext.ts`
- Create: `apps/web/src/evaluations/useTeachingReportContext.test.tsx`
- Modify: `apps/web/src/evaluations/foundationApi.ts`
- Modify: `apps/web/src/evaluations/foundationApi.test.ts`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`

- [ ] **Step 1: Write RED tests for the live context hook and entry card**

Test that the hook calls `/evaluations/reports/context` with the query `AbortSignal`, uses `reportKeys.context(accountId)`, and has `staleTime: 0`, `gcTime: 0`, `refetchOnMount: "always"`, `refetchOnWindowFocus: true`.

Extend center-page tests:

```typescript
renderEvaluationCenter({
  contextResponse: () => jsonResponse({
    ...evaluationContext,
    reports_available: true,
  }),
})
expect(await screen.findByRole("link", { name: "进入教学评语" })).toHaveAttribute(
  "href",
  "/evaluations/reports",
)
```

Keep the existing false case and assert it has no teaching-report link and exactly one disabled teaching-report card.

- [ ] **Step 2: Verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/useTeachingReportContext.test.tsx \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/evaluations/foundationApi.test.ts
```

Expected: the hook is absent, `reports_available` is still typed as literal false, and the card is always disabled.

- [ ] **Step 3: Implement the hook and active card**

Use this hook boundary:

```typescript
export function useTeachingReportContext() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: reportKeys.context(accountId),
    queryFn: ({ signal }) => getTeachingReportContext(signal),
    enabled: Boolean(user?.account_id),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    retry: false,
  })
}
```

Change only `reports_available` from `false` to `boolean`; keep video and survey literals unchanged. Render an active `MessageSquareText` card when true and retain `DisabledEvaluationBranch` when false.

- [ ] **Step 4: Run tests GREEN and commit**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/useTeachingReportContext.test.tsx \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/evaluations/foundationApi.test.ts
git add \
  apps/web/src/evaluations/useTeachingReportContext.ts \
  apps/web/src/evaluations/useTeachingReportContext.test.tsx \
  apps/web/src/evaluations/foundationApi.ts \
  apps/web/src/evaluations/foundationApi.test.ts \
  apps/web/src/evaluations/EvaluationCenterPage.tsx \
  apps/web/src/evaluations/EvaluationCenterPage.test.tsx
git commit -m "feat: open teaching report entry"
```

## Task 5: Build the immutable version timeline component

**Files:**

- Create: `apps/web/src/evaluations/ReportVersionTimeline.tsx`
- Create: `apps/web/src/evaluations/ReportVersionTimeline.test.tsx`

- [ ] **Step 1: Write component RED tests**

Render with AuthProvider mocked to the current account. Mock a two-version page with version 2 rejected/current and version 1 approved/published. Assert:

```typescript
expect(await screen.findByRole("heading", { name: "版本记录" })).toBeVisible()
expect(screen.getByText("版本 2")).toBeVisible()
expect(screen.getByText("需要补充复盘结论")).toBeVisible()
expect(screen.getByText("当前版本")).toBeVisible()
expect(screen.getByText("已发布版本")).toBeVisible()
expect(screen.getByRole("region", { name: "版本 2 与版本 1 对比" })).toBeVisible()
```

Assert pagination, a recoverable non-disclosing error, abort-signal forwarding, and that switching `reportId` releases the previous `gcTime: 0` private query after unmount/remount.

- [ ] **Step 2: Verify RED**

```bash
npm --prefix apps/web test -- --run src/evaluations/ReportVersionTimeline.test.tsx
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the timeline**

Use props:

```typescript
export type ReportVersionTimelineProps = {
  reportId: string
  compareCurrent?: boolean
}
```

Read `pending` and `revision` from `useEvaluationAuthorization()`. Query with `[...reportKeys.versions(accountId, filters), { authorizationRevision: revision }]`, pass `signal`, set `enabled: Boolean(user?.account_id && reportId && !pending)`, `staleTime: 0`, `gcTime: 0`, and `retry: false`. Render nothing sensitive while pending. Render newest first. When `compareCurrent` and two versions exist, render two complete panels inside `role="region"` with an accessible comparison label; do not add a diff dependency. Show review reason only when the API returned a rejected review.

- [ ] **Step 4: Run GREEN and commit**

```bash
npm --prefix apps/web test -- --run src/evaluations/ReportVersionTimeline.test.tsx
git add \
  apps/web/src/evaluations/ReportVersionTimeline.tsx \
  apps/web/src/evaluations/ReportVersionTimeline.test.tsx
git commit -m "feat: show teaching report versions"
```

## Task 6: Build the secure author editor dialog

**Files:**

- Create: `apps/web/src/evaluations/TeachingReportEditorDialog.tsx`
- Create: `apps/web/src/evaluations/TeachingReportEditorDialog.test.tsx`

- [ ] **Step 1: Write RED tests for allowed actions and stable retries**

Cover four slot states with an explicit table test; define complete fixtures and render each case through a real QueryClient:

```typescript
const baseSlot: TeachingReportSlot = {
  enrollment_id: "20000000-0000-4000-8000-000000000001",
  student_person_id: "30000000-0000-4000-8000-000000000001",
  cohort_id: "40000000-0000-4000-8000-000000000001",
  cohort_name: "2026 夏季营",
  project_id: "50000000-0000-4000-8000-000000000001",
  project_name: "英雄联盟",
  class_id: "60000000-0000-4000-8000-000000000001",
  class_name: "一班",
  report_kind: "daily",
  period_sequence: 9,
  period_start_date: "2026-08-09",
  period_end_date: "2026-08-09",
  submission_opens_at: "2026-08-08T16:00:00Z",
  initial_submission_deadline: "2026-08-24T15:59:59.999999Z",
  report_id: null,
  status: null,
  lock_version: null,
  draft_body: null,
  draft_dimension_values: null,
  draft_revision: null,
  allowed_actions: ["save_draft"],
}
const newSlot = baseSlot
const savedDraftSlot: TeachingReportSlot = {
  ...baseSlot,
  report_id: "10000000-0000-4000-8000-000000000001",
  status: "draft",
  lock_version: 1,
  draft_body: "已保存草稿",
  draft_dimension_values: {},
  draft_revision: 1,
  allowed_actions: ["save_draft", "submit"],
}
const rejectedSlot: TeachingReportSlot = {
  ...savedDraftSlot,
  status: "rejected",
  draft_body: null,
  draft_dimension_values: null,
  draft_revision: null,
  allowed_actions: ["start_correction"],
}
const publishedSlot: TeachingReportSlot = {
  ...rejectedSlot,
  status: "published",
  allowed_actions: ["start_revision"],
}

function renderEditor(slot: TeachingReportSlot) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <TeachingReportEditorDialog
        slot={slot}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onConflict={vi.fn()}
        idempotencyKeyFactory={() => "editor-idempotency-key"}
        reportIdFactory={() => "90000000-0000-4000-8000-000000000001"}
      />
    </QueryClientProvider>,
  )
}

it.each([
  [newSlot, ["保存草稿"]],
  [savedDraftSlot, ["保存草稿", "提交审核"]],
  [rejectedSlot, ["开始整改"]],
  [publishedSlot, ["发起修订"]],
] as const)("renders only server-allowed actions", (slot, expectedNames) => {
  renderEditor(slot)
  const expected = new Set<string>(expectedNames)
  const actionNames = screen
    .getAllByRole("button")
    .map((button) => button.textContent?.trim())
    .filter((name): name is string => Boolean(name && expected.has(name)))
  expect(actionNames).toEqual([...expectedNames])
})
```

For a new slot, mock the first draft request as a rejected network promise and the retry as success. Assert both calls use the same client-generated report UUID and `Idempotency-Key`, while the request bodies contain the typed body and the mutation cache variables serialize without it.

For a typed `ProblemDetailsError`, assert the next deliberate attempt gets a new idempotency key. For a 409, keep the dialog and body visible, call `onConflict`, and show the refresh/compare message. Assert localStorage/sessionStorage remain empty.

- [ ] **Step 2: Verify RED**

```bash
npm --prefix apps/web test -- --run src/evaluations/TeachingReportEditorDialog.test.tsx
```

Expected: module-not-found failure.

- [ ] **Step 3: Define the editor boundary and retry-attempt lifecycle**

Use this public boundary:

```typescript
export type TeachingReportEditorDialogProps = {
  slot: TeachingReportSlot
  onClose: () => void
  onChanged: (reportId: string) => Promise<void> | void
  onConflict: (reportId: string) => Promise<void> | void
  idempotencyKeyFactory?: () => string
  reportIdFactory?: () => string
}
```

Maintain a ref containing `{ operation, reportId, idempotencyKey }`. Reuse it only after an unknown network failure for the same operation and slot. Clear it on success, typed Problem Details, explicit cancel, or slot change. Build a fresh `captureTeachingReportWrite(...)` for each HTTP attempt so the one-shot WeakMap contract is preserved. Every mutation sets `gcTime: 0`; variables contain only IDs, opaque write shells, and idempotency keys.

- [ ] **Step 4: Implement server-driven actions and dirty-draft behavior**

Render buttons exclusively from `slot.allowed_actions`; unknown actions are impossible after Zod parsing. Reuse `EvaluationDialogShell`, an uncontrolled textarea with `maxLength=5000`, and server values for lock/draft revisions. A new slot can only save. Starting correction/revision succeeds, closes the dialog, and refreshes the slot before editing. For an existing editable draft, track only a `dirty` boolean from textarea input; disable “提交审核” while dirty and display “请先保存草稿”. A successful save closes and refreshes, so a later submit always targets the server-saved draft revision.

- [ ] **Step 5: Run GREEN and commit**

```bash
npm --prefix apps/web test -- --run src/evaluations/TeachingReportEditorDialog.test.tsx
git add \
  apps/web/src/evaluations/TeachingReportEditorDialog.tsx \
  apps/web/src/evaluations/TeachingReportEditorDialog.test.tsx
git commit -m "feat: add teaching report editor"
```

## Task 7: Compose the author workspace

**Files:**

- Create: `apps/web/src/evaluations/TeachingReportsPage.tsx`
- Create: `apps/web/src/evaluations/TeachingReportsPage.test.tsx`

- [ ] **Step 1: Write RED tests for directory filters, slots, and composition**

Use a fetch mock for `targets`, `slots`, and `versions`. Assert the initial target page renders authorized student identity; selecting cohort filters project options using `cohort_ids`, selecting project filters classes, and student search serializes the trimmed query. Selecting a target must request only its enrollment slots.

Assert daily/weekly and status controls filter rendered cards without fabricating actions. Open a draft editor, complete a successful mutation, then verify targets, current enrollment slots, detail, and version query prefixes are invalidated. Open an existing report and assert `ReportVersionTimeline` receives the selected report ID.

Add empty, directory-error, slot-private-404, and retry tests. A private 404 must remove the selected target, close the dialog, refetch targets, and keep the page inside the report capability boundary.

- [ ] **Step 2: Verify RED**

```bash
npm --prefix apps/web test -- --run src/evaluations/TeachingReportsPage.test.tsx
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement authorized target filters and selection**

Use page state containing only public filters and selected IDs:

```typescript
const [targetFilters, setTargetFilters] = useState<ReportTargetFilters>({
  cohortId: null,
  projectId: null,
  classId: null,
  studentId: null,
  query: null,
  page: 1,
  pageSize: 20,
})
const [selectedEnrollmentId, setSelectedEnrollmentId] = useState<string | null>(null)
const [kind, setKind] = useState<TeachingReportKind | "all">("all")
const [status, setStatus] = useState<TeachingReportStatus | "all">("all")
```

Read `pending` and `revision` from `useEvaluationAuthorization()`. Targets and slots query functions must pass AbortSignal, append `{ authorizationRevision: revision }` to their account-rooted query keys, use `enabled: !pending`, and use `gcTime: 0`. Derive project/class options from facets, resetting incompatible descendants when a parent changes. Keep report bodies inside API query results and editor DOM; never copy them into filters or route state.

- [ ] **Step 4: Compose slots, server actions, and version children**

Filter the selected target's slot list by local kind/status controls without altering `allowed_actions`. Render `TeachingReportEditorDialog` only for a selected slot and `ReportVersionTimeline` only when the slot has a report ID. Use stable public IDs for React keys and accessible task-card labels.

- [ ] **Step 5: Implement invalidation and private-target recovery**

On mutation success invalidate current target slots, target directory, report detail/list, and versions by public prefixes. On private target 404 clear only the selected enrollment, close editor/timeline children, and refetch targets. Initial directory/server failures retain a retry action and never echo upstream private detail.

- [ ] **Step 6: Run GREEN and commit**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/TeachingReportsPage.test.tsx \
  src/evaluations/TeachingReportEditorDialog.test.tsx \
  src/evaluations/ReportVersionTimeline.test.tsx
git add \
  apps/web/src/evaluations/TeachingReportsPage.tsx \
  apps/web/src/evaluations/TeachingReportsPage.test.tsx
git commit -m "feat: build teaching report workspace"
```

## Task 8: Integrate review, family, routes, dialogs, and responsive styles

**Files:**

- Modify: `apps/web/src/evaluations/TeachingReportReviewPage.tsx`
- Modify: `apps/web/src/evaluations/TeachingReportReviewPage.test.tsx`
- Modify: `apps/web/src/evaluations/PublishedReportsPanel.tsx`
- Modify: `apps/web/src/evaluations/PublishedReportsPanel.test.tsx`
- Create: `apps/web/src/evaluations/teaching-reports.css`
- Create: `apps/web/src/evaluations/teaching-reports-responsive.test.ts`
- Modify: `apps/web/src/evaluations/EvaluationDialogShell.tsx`
- Modify: `apps/web/src/evaluations/EvaluationDialogShell.test.tsx`
- Modify: `apps/web/src/portal/LearningPage.tsx`
- Modify: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Add RED tests for review comparison and authorization pending**

Extend the existing review tests so selecting a queue item loads `/versions`, displays the current/previous comparison, and an approved/rejected mutation invalidates every filtered review queue plus the report's version key.

Wrap the page in `EvaluationAuthorizationProvider`; switch `pending` to true while a rejection Dialog contains a typed reason. Assert the portaled dialog and reason disappear during revalidation, the queue query is disabled, and the page state returns after authorization succeeds.

- [ ] **Step 2: Add RED route tests for author/reviewer/family and revocation**

Extend the evaluation child fetch helper with `/evaluations/reports/context`. Cover:

```text
author=true   -> /evaluations/reports renders 教学评语工作台
reviewer=true, author=false -> /evaluations/reports redirects /evaluations/reports/review
family=true only -> /evaluations/reports redirects /learning
reviewer=false -> direct /evaluations/reports/review returns /evaluations
```

For revocation, seed current and other-account report roots, start a delayed private report request, change report context from true to false on focus, and assert: current root removed immediately and after cancellation; other root preserved; no logout; delayed response never repopulates; route returns `/evaluations`. Keep the existing global `evaluation_access_denied` behavior returning `/`.

- [ ] **Step 3: Add RED dialog-lock and responsive CSS tests**

Extend `EvaluationDialogShell.test.tsx` with one and two simultaneous dialogs:

```typescript
expect(document.body.style.overflow).toBe("hidden")
first.unmount()
expect(document.body.style.overflow).toBe("hidden")
second.unmount()
expect(document.body.style.overflow).toBe(previousOverflow)
```

Create a CSS test that parses `teaching-reports.css`, finds an inclusive `(max-width: 768px)` rule setting author/review/comparison grids to `1fr`, verifies page padding contains `env(safe-area-inset-bottom)`, and verifies buttons/inputs/selects/textareas/action labels have `min-height: 44px` or equivalent block sizing.

- [ ] **Step 4: Run the integration files and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/TeachingReportReviewPage.test.tsx \
  src/evaluations/PublishedReportsPanel.test.tsx \
  src/evaluations/EvaluationDialogShell.test.tsx \
  src/evaluations/teaching-reports-responsive.test.ts \
  src/portal/LearningPage.test.tsx \
  src/app/router.test.tsx
```

Expected: failures identify missing versions integration, guard/routes, body lock, and CSS.

- [ ] **Step 5: Implement the report capability route and request cancellation**

Add a `TeachingReportCapabilityRoute` in `router.tsx` using `useTeachingReportContext`. It must:

- preserve children under `EvaluationAuthorizationProvider` while hiding them during `context.isFetching`;
- distinguish initial capability mismatch from a true → false transition;
- on true → false cancel and remove only `reportKeys.root(accountId)`, then navigate `/evaluations`;
- for an initial author mismatch redirect reviewer-only to `/evaluations/reports/review`, family-only to `/learning`, otherwise `/evaluations`;
- guard review route strictly with `reviewer_available`;
- retain global evaluation-entry handling and never treat every 403 as a global revocation.

Register both routes under the existing `PermissionRoute permission="evaluation_center.access"`.

- [ ] **Step 6: Complete review/family integration and safe queries**

Pass Query `signal` from `TeachingReportReviewPage` and `PublishedReportsPanel`. In the review page, use `useEvaluationAuthorization()` to append the authorization revision to private keys, disable private queries, and remove portaled dialogs while pending. Add `ReportVersionTimeline compareCurrent` to selected review detail. The family panel remains under the portal capability guard and does not invent a report authorization revision.

Preserve the already completed `LearningPage` embed. `PublishedReportsPanel` imports `teaching-reports.css` itself so family routes receive styles without mounting the author page.

- [ ] **Step 7: Add reference-counted body locking and responsive CSS**

Port the proven lock shape into `EvaluationDialogShell.tsx`:

```typescript
let dialogLocks = 0
let previousOverflow = ""

function lockBody(): () => void {
  if (dialogLocks === 0) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
  }
  dialogLocks += 1
  return () => {
    dialogLocks = Math.max(0, dialogLocks - 1)
    if (dialogLocks === 0) document.body.style.overflow = previousOverflow
  }
}
```

Call it once in the existing mount effect and release in cleanup without changing focus/inert behavior.

Create prefixed desktop grids and an inclusive `@media (max-width: 768px)` section. Apply `min-width: 0`, `overflow-wrap: anywhere`, 44px controls, full-width mobile actions, and bottom padding `calc(96px + env(safe-area-inset-bottom))`.

- [ ] **Step 8: Run integration GREEN and commit**

```bash
npm --prefix apps/web test -- --run \
  src/evaluations/TeachingReportReviewPage.test.tsx \
  src/evaluations/PublishedReportsPanel.test.tsx \
  src/evaluations/EvaluationDialogShell.test.tsx \
  src/evaluations/teaching-reports-responsive.test.ts \
  src/portal/LearningPage.test.tsx \
  src/app/router.test.tsx
git add \
  apps/web/src/evaluations/TeachingReportReviewPage.tsx \
  apps/web/src/evaluations/TeachingReportReviewPage.test.tsx \
  apps/web/src/evaluations/PublishedReportsPanel.tsx \
  apps/web/src/evaluations/PublishedReportsPanel.test.tsx \
  apps/web/src/evaluations/teaching-reports.css \
  apps/web/src/evaluations/teaching-reports-responsive.test.ts \
  apps/web/src/evaluations/EvaluationDialogShell.tsx \
  apps/web/src/evaluations/EvaluationDialogShell.test.tsx \
  apps/web/src/portal/LearningPage.tsx \
  apps/web/src/portal/LearningPage.test.tsx \
  apps/web/src/app/router.tsx \
  apps/web/src/app/router.test.tsx
git commit -m "feat: integrate teaching report experiences"
```

## Task 9: Run final gates and review checkpoints

**Files:**

- Verify: all files changed by Tasks 1–8
- Do not create: Docker, NAS, GPU, video, or Task 7 E2E files

- [ ] **Step 1: Run the full API suite with the isolated PostgreSQL database**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests -q
```

Expected: zero failed and zero skipped PostgreSQL-required tests.

- [ ] **Step 2: Run complete backend static gates**

```bash
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check \
  apps/api/app apps/api/tests
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy \
  --config-file apps/api/pyproject.toml apps/api/app
```

Expected: Ruff and strict mypy report no errors.

- [ ] **Step 3: Run complete Web gates**

```bash
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all Vitest files pass, ESLint exits 0, TypeScript is strict-clean, and the production build exits 0. Existing non-failing Vite chunk-size warnings may remain documented but cannot hide an error.

- [ ] **Step 4: Verify repository scope and whitespace**

```bash
git status --short
git diff --check
git diff --stat 22d5911..HEAD
git diff --name-only 22d5911..HEAD
```

Expected: no unstaged implementation residue and no whitespace errors. Inspect the final name list and confirm it contains no Dockerfile, Compose, NAS, video, GPU, or Playwright files.

- [ ] **Step 5: Request final specification review**

Give the reviewer the approved spec, parent Task 6, `BASE_SHA=22d5911`, and current `HEAD_SHA`. Require explicit coverage of all 14 routes, target membership, 403/404 split, zero writes, body/reason privacy, allowed actions, cancellation, and responsive requirements. Fix and re-review every mismatch.

- [ ] **Step 6: Request final code-quality and security review**

Only after the specification review passes, review transaction/read side effects, query count/scalability, Zod projection, AbortSignal wiring, retry idempotency, cache/account isolation, portal cleanup, dialog locking, accessibility, and test quality. Fix and re-review all Critical/Important findings.

- [ ] **Step 7: Record the Task 6 completion commit if reviews required fixes**

```bash
git add -u apps/api apps/web
git commit -m "fix: complete teaching report workspace review"
```

Skip this commit when reviews required no file changes. Do not push, merge, start Task 7, or claim Docker readiness in this task.
