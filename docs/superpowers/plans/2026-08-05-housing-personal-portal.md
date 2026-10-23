# Housing Personal Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the read-only `/api/v1/me/housing` and `/me/housing` experience for students and guardians, including future-room visibility, post-check-in roommate visibility, server-derived capabilities, private caching, audit, and local four-viewport acceptance.

**Architecture:** Add a focused `HousingPortalService` that resolves self/family targets through deny-first RBAC before reading stays, then emits a strict current-plus-planned projection. Expose it through the existing private housing route/audit boundary and add two independent portal capabilities. The Web client parses the response through a Zod allowlist before TanStack Query can cache it, renders a separate responsive page, and relies only on server capabilities for `/housing` and `/me/housing` navigation.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, Pydantic 2, Pytest, React 19, TypeScript 6, TanStack Query 5, Zod 4, Vitest, Playwright.

---

## Execution constraints

- Work in `/Users/skymoonzyj/Documents/new project3/.worktrees/housing-occupancy-lifecycle` on `codex/housing-occupancy-lifecycle`.
- Use `docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md` as the accepted source of truth.
- Run PostgreSQL-backed tests serially with an isolated `TEST_DATABASE_URL`; do not run two database test commands concurrently.
- Use `PYTHONPATH=apps/api` so tests import this worktree.
- Follow red-green-refactor for every production change and commit after each task.
- Do not modify Docker, NAS, police-reporting, export, video, or GPU scope.
- Do not claim the administrator occupancy UI is complete; this plan delivers the personal portal on top of the completed lifecycle API.

## File responsibility map

| File | Responsibility |
|---|---|
| `apps/api/app/housing/schemas.py` | Strict personal-housing response DTOs |
| `apps/api/app/housing/portal_service.py` | Authorized subject resolution and current/planned safe projection |
| `apps/api/app/housing/portal_router.py` | Private `/me/housing` route and sensitive-read audit registration |
| `apps/api/app/housing/dependencies.py` | FastAPI dependency for the portal service |
| `apps/api/app/portal/context_service.py` | Server-derived personal and management housing capabilities |
| `apps/api/app/portal/schemas.py` | Portal context contract |
| `apps/api/app/portal/routing.py` | Private headers for exact `/api/v1/me/housing`, including 405 paths |
| `apps/web/src/portal/api.ts` | Zod allowlist, canonical DTOs, account-scoped query key and fetcher |
| `apps/web/src/housing/MyHousingPage.tsx` | Read-only current and planned housing UI |
| `apps/web/src/housing/housing.css` | Responsive personal-housing layout |
| `apps/web/src/app/router.tsx` | Capability-guarded `/housing` and `/me/housing` routes |
| `apps/web/src/layout/AppShell.tsx` | Separate management and personal navigation items |
| `apps/web/src/portal/PortalDashboardPage.tsx` | Personal housing task card without prefetching housing data |
| `apps/api/app/cli/seed_housing_portal_e2e.py` | Opt-in portal-only stay fixture layered after the canonical E2E seed |
| `apps/web/tests/housing-personal-portal.spec.ts` | Student, guardian, future-room and revocation browser journey |
| `scripts/run-housing-personal-portal-e2e.sh` | Local non-Docker four-viewport launcher |

## Task 1: Lock the strict backend response contract

**Files:**

- Modify: `apps/api/app/housing/schemas.py`
- Modify: `apps/api/tests/unit/housing/test_schemas.py`

- [ ] **Step 1: Replace the old portal-field assertions with the accepted contract**

Update `test_resident_and_portal_contracts_expose_only_intended_identity_fields` and add an extra-field rejection assertion:

```python
assert set(portal_stay.model_fields) == {
    "building_name",
    "floor_name",
    "room_name",
    "stay_purpose",
    "planned_check_in_at",
    "planned_check_out_at",
    "actual_check_in_at",
    "actual_check_out_at",
    "status",
    "roommates",
}
assert set(portal_subject.model_fields) == {
    "subject_person_id",
    "subject_name",
    "current_stay",
    "planned_stays",
}
assert set(portal_response.model_fields) == {"as_of", "subjects"}

with pytest.raises(ValidationError):
    portal_stay(
        building_name="4幢",
        floor_name="二楼",
        room_name="4-201",
        stay_purpose="permanent",
        planned_check_in_at=NOW,
        planned_check_out_at=None,
        actual_check_in_at=NOW,
        actual_check_out_at=None,
        status="checked_in",
        roommates=[],
        phone="13800138000",
    )
```

- [ ] **Step 2: Run the schema test and verify RED**

Run:

```bash
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing/test_schemas.py::test_resident_and_portal_contracts_expose_only_intended_identity_fields -q
```

Expected: FAIL because the old DTO lacks `subject_person_id`, `stay_purpose`, `status`, `planned_stays`, and strict extra rejection.

- [ ] **Step 3: Implement the strict DTOs**

Replace the old portal models with:

```python
PortalStayStatus = Literal["planned", "checked_in", "checkout_pending"]


class HousingPortalResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HousingPortalStay(HousingPortalResponseModel):
    building_name: str
    floor_name: str
    room_name: str
    stay_purpose: StayPurpose
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None
    actual_check_in_at: AwareDatetime | None
    actual_check_out_at: AwareDatetime | None
    status: PortalStayStatus
    roommates: list[str] | None


class HousingPortalSubject(HousingPortalResponseModel):
    subject_person_id: UUID
    subject_name: str
    current_stay: HousingPortalStay | None = None
    planned_stays: list[HousingPortalStay] = Field(default_factory=list)


class HousingPortalResponse(HousingPortalResponseModel):
    as_of: AwareDatetime
    subjects: list[HousingPortalSubject]
```

- [ ] **Step 4: Run the full housing schema file and verify GREEN**

Run:

```bash
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing/test_schemas.py -q
```

Expected: all housing schema tests PASS.

- [ ] **Step 5: Commit the contract**

```bash
git add apps/api/app/housing/schemas.py \
  apps/api/tests/unit/housing/test_schemas.py
git commit -m "feat: define personal housing contract"
```

## Task 2: Resolve self and family subjects and compute capabilities

**Files:**

- Create: `apps/api/app/housing/portal_service.py`
- Create: `apps/api/tests/services/test_housing_portal.py`
- Modify: `apps/api/app/portal/context_service.py`
- Modify: `apps/api/app/portal/schemas.py`
- Modify: `apps/api/app/portal/router.py`
- Modify: `apps/api/tests/services/test_portal_context.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Create deterministic subject-authorization tests**

Build `HousingPortalScenario` in `test_housing_portal.py` with one guardian, one minor student, one adult student, one combined student/guardian account, and one pure admin. Use `UserRole`, `ROLES`, and current `GuardianRelationship` rows. Add these tests:

```python
NOW = datetime(2026, 8, 5, 8, tzinfo=UTC)


def test_guardian_keeps_minor_and_adult_subjects_while_relationships_are_valid(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    subjects = scenario.service.list_authorized_subjects(
        user_id=scenario.guardian_account.id,
        actor_person_id=scenario.guardian.id,
        as_of=NOW,
    )

    assert [item.person_id for item in subjects] == [
        scenario.adult_student.id,
        scenario.minor_student.id,
    ]


def test_combined_student_guardian_lists_self_first_and_deduplicates(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    subjects = scenario.service.list_authorized_subjects(
        user_id=scenario.combined_account.id,
        actor_person_id=scenario.adult_student.id,
        as_of=NOW,
    )

    assert subjects[0].person_id == scenario.adult_student.id
    assert {item.person_id for item in subjects} == {
        scenario.adult_student.id,
        scenario.minor_student.id,
    }


def test_ended_deleted_and_explicitly_denied_subjects_are_filtered(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    scenario.minor_relationship.ended_at = NOW
    scenario.adult_student.deleted_at = NOW
    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.guardian_account.id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="person",
            scope_id=scenario.adult_student.id,
        )
    )
    scenario.db.flush()

    assert scenario.service.list_authorized_subjects(
        user_id=scenario.guardian_account.id,
        actor_person_id=scenario.guardian.id,
        as_of=NOW,
    ) == []


def test_global_admin_does_not_gain_a_personal_subject(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    assert scenario.service.has_authorized_subjects(
        user_id=scenario.admin_account.id,
        actor_person_id=scenario.admin.id,
        as_of=NOW,
    ) is False
```

The fixture must use unique identity/login digests, active `StudentProfile` rows, a guardian-to-adult relationship whose `valid_from` predates 2026-08-05, and a combined-account guardian-to-minor relationship. Do not create stays in this step.

- [ ] **Step 2: Extend context tests with independent housing capability assertions**

Add `housing_management_available` and `housing_available` to every `PortalContextResponse` expectation. Add a current guardian relationship to the existing context fixture and use an actual seeded building ID for a scoped read override. Add these focused assertions:

```python
def test_context_separates_personal_housing_from_management(
    portal_context_scenario: PortalContextScenario,
) -> None:
    scenario = portal_context_scenario

    assert scenario.context_for("guardian").housing_available is True
    assert scenario.context_for("guardian").housing_management_available is False
    assert scenario.context_for("student").housing_available is True
    assert scenario.context_for("admin").housing_available is False
    assert scenario.context_for("admin").housing_management_available is True


def test_building_reader_gets_management_without_personal_housing(
    portal_context_scenario: PortalContextScenario,
) -> None:
    scenario = portal_context_scenario
    building_id = scenario.db.scalar(select(HousingBuilding.id))
    assert building_id is not None
    scenario.allow(
        "staff",
        "housing.read",
        "building",
        scope_id=building_id,
    )

    result = scenario.context_for("staff")

    assert result.housing_management_available is True
    assert result.housing_available is False
```

- [ ] **Step 3: Run the authorization and context tests and verify RED**

Run serially:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/services/test_housing_portal.py \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py -q
```

Expected: collection or assertion FAIL because `HousingPortalService` and both context booleans do not exist.

- [ ] **Step 4: Implement authorized subject resolution**

Create `portal_service.py` with this boundary:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.time import business_date_at
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.service import RbacService


@dataclass(frozen=True)
class HousingPortalAuthorizedSubject:
    person_id: UUID
    name: str
    is_self: bool


class HousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def list_authorized_subjects(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> list[HousingPortalAuthorizedSubject]:
        reference_date = business_date_at(as_of)
        candidate_ids = set(
            self.db.scalars(
                select(GuardianRelationship.student_person_id).where(
                    GuardianRelationship.guardian_person_id == actor_person_id,
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.valid_from <= reference_date,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= reference_date,
                    ),
                )
            )
        )
        if self.db.get(StudentProfile, actor_person_id) is not None:
            candidate_ids.add(actor_person_id)
        if not candidate_ids:
            return []

        people = {
            person.id: person
            for person in self.db.scalars(
                select(Person)
                .join(StudentProfile, StudentProfile.person_id == Person.id)
                .where(
                    Person.id.in_(candidate_ids),
                    Person.deleted_at.is_(None),
                )
            )
        }
        tokens = self.rbac.person_resource_tokens_many(
            actor_person_id=actor_person_id,
            target_person_ids=set(people),
            reference_date=reference_date,
        )
        decisions = self.rbac.decision_context(user_id, occurred_at=as_of)
        subjects = [
            HousingPortalAuthorizedSubject(
                person_id=person.id,
                name=person.full_name,
                is_self=person.id == actor_person_id,
            )
            for person in people.values()
            if decisions.decide("housing.read", tokens[person.id]).allowed
        ]
        subjects.sort(
            key=lambda item: (
                not item.is_self,
                item.name,
                item.person_id.int,
            )
        )
        return subjects

    def has_authorized_subjects(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> bool:
        return bool(
            self.list_authorized_subjects(
                user_id=user_id,
                actor_person_id=actor_person_id,
                as_of=as_of,
            )
        )
```

- [ ] **Step 5: Implement context capability calculation**

Extend `PortalContextResponse` with two required booleans. Add optional `occurred_at` to `PortalContextService.get_context`, use one UTC instant for the entire calculation, and compute:

```python
observed_at = occurred_at or datetime.now(UTC)
housing_portal = HousingPortalService(self.db)
housing_available = housing_portal.has_authorized_subjects(
    user_id=user_id,
    actor_person_id=actor_person_id,
    as_of=observed_at,
)
housing_access = HousingAccessService(self.db, user_id, observed_at)
buildings = list(self.db.scalars(select(HousingBuilding)))
floors = list(self.db.scalars(select(HousingFloor)))
housing_management_available = bool(
    housing_access.filter_buildings(buildings, floors)
)
```

Return both values in `PortalContextResponse`. Pass `occurred_at=datetime.now(UTC)` from the context router so one request uses one time. Management visibility must come only from global/building/floor catalog tokens; self/family targets must not make a catalog building readable.

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run the Step 3 command again.

Expected: all authorization, context service and portal API tests PASS.

- [ ] **Step 7: Commit target resolution and capabilities**

```bash
git add apps/api/app/housing/portal_service.py \
  apps/api/app/portal/context_service.py apps/api/app/portal/schemas.py \
  apps/api/app/portal/router.py \
  apps/api/tests/services/test_housing_portal.py \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: resolve personal housing access"
```

## Task 3: Project current stays, planned stays, and current roommates

**Files:**

- Modify: `apps/api/app/housing/portal_service.py`
- Modify: `apps/api/tests/services/test_housing_portal.py`

- [ ] **Step 1: Add projection fixtures and failing tests**

Add a `HousingStay` helper that fills room/floor/building snapshots from seeded rooms. Create a current `checked_in` stay for the minor, a current roommate in the same room, an overdue `planned` stay and a future `planned` stay for the adult, plus checked-out and cancelled noise. Assert the complete projection:

```python
def test_projection_separates_current_and_planned_and_hides_future_roommates(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    scenario.seed_projection_stays()

    result = scenario.service.get_housing(
        user_id=scenario.guardian_account.id,
        actor_person_id=scenario.guardian.id,
        as_of=NOW,
    )

    subjects = {item.subject_person_id: item for item in result.subjects}
    current = subjects[scenario.minor_student.id].current_stay
    assert current is not None
    assert current.status == "checked_in"
    assert current.roommates == [scenario.roommate.full_name]

    planned = subjects[scenario.adult_student.id].planned_stays
    assert [item.status for item in planned] == ["planned", "planned"]
    assert [item.planned_check_in_at for item in planned] == sorted(
        item.planned_check_in_at for item in planned
    )
    assert all(item.roommates is None for item in planned)
    assert "checked_out" not in result.model_dump_json()
    assert "cancelled" not in result.model_dump_json()


def test_authorized_subject_without_stay_is_still_returned(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    result = scenario.service.get_housing(
        user_id=scenario.guardian_account.id,
        actor_person_id=scenario.guardian.id,
        as_of=NOW,
    )

    assert all(item.current_stay is None for item in result.subjects)
    assert all(item.planned_stays == [] for item in result.subjects)


def test_projection_rejects_multiple_logically_current_stays(
    housing_portal_scenario: HousingPortalScenario,
) -> None:
    scenario = housing_portal_scenario
    scenario.seed_non_overlapping_but_still_current_rows()

    with pytest.raises(HousingServiceError):
        scenario.service.get_housing(
            user_id=scenario.guardian_account.id,
            actor_person_id=scenario.guardian.id,
            as_of=NOW,
        )
```

Also assert the serialized response contains no phone, email, identity, birthday, sex, account status, notes, problem description, room ID, stay ID, roommate ID, or `access_source` keys and no corresponding fixture values.

- [ ] **Step 2: Run projection tests and verify RED**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/services/test_housing_portal.py -q
```

Expected: FAIL because `get_housing` and roommate projection do not exist.

- [ ] **Step 3: Implement one batched stay projection**

Add `get_housing` to `HousingPortalService`. It must:

1. Resolve subjects first and raise `HousingResourceNotFound` when the authorized set is empty.
2. Fetch only `planned`, `checked_in`, and `checkout_pending` stays for the authorized person IDs.
3. Reject a second non-planned row for the same person with `HousingServiceError`.
4. Fetch current occupants only for the current room IDs, using `actual_check_in_at <= as_of`, `actual_check_out_at IS NULL`, and `Person.deleted_at IS NULL`.
5. Build `planned_stays` with `roommates=None` without querying future co-occupants.

Use this projection shape:

```python
def _portal_stay(
    stay: HousingStay,
    *,
    roommates: list[str] | None,
) -> HousingPortalStay:
    return HousingPortalStay(
        building_name=stay.building_name_snapshot,
        floor_name=stay.floor_name_snapshot,
        room_name=stay.room_name_snapshot,
        stay_purpose=cast(StayPurpose, stay.stay_purpose),
        planned_check_in_at=stay.planned_check_in_at,
        planned_check_out_at=stay.planned_check_out_at,
        actual_check_in_at=stay.actual_check_in_at,
        actual_check_out_at=stay.actual_check_out_at,
        status=cast(PortalStayStatus, stay.status),
        roommates=roommates,
    )
```

Group roommate rows by room, de-duplicate by person ID, exclude the subject, and sort by `(full_name, person_id.int)`. Sort planned stays by `(planned_check_in_at, id.int)`.

- [ ] **Step 4: Run projection tests and verify GREEN**

Run the Step 2 command again.

Expected: all housing portal service tests PASS.

- [ ] **Step 5: Commit the projection**

```bash
git add apps/api/app/housing/portal_service.py \
  apps/api/tests/services/test_housing_portal.py
git commit -m "feat: project personal housing stays"
```

## Task 4: Expose the private audited API

**Files:**

- Create: `apps/api/app/housing/portal_router.py`
- Modify: `apps/api/app/housing/dependencies.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/portal/routing.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Add failing route, privacy, and audit tests**

Create a fake dependency returning a response with one subject, one current stay, and one planned stay. Test:

```python
def test_personal_housing_route_is_private_minimal_and_audited_once(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    db_client.app.dependency_overrides[get_housing_portal_service] = (
        FakeHousingPortalService
    )
    request_id = "personal-housing-allowed"

    response = db_client.get(
        "/api/v1/me/housing",
        headers={"X-Request-ID": request_id},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["subjects"][0]["planned_stays"][0]["roommates"] is None
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id == request_id))
    )
    assert len(events) == 1
    assert events[0].action == "housing.portal.read"
    assert events[0].outcome == "allowed"
    assert events[0].event_metadata == {"result_count": 1}
```

Add tests for:

- no authorized subjects and a missing-equivalent fake both return the same 404 body and one `denied` audit;
- unexpected service failure returns sanitized 500 and one `failed` audit;
- `AuditService.append` rejection returns `housing_audit_failed`, no response data, and one attempt;
- invalid session is 401 private, forced password change is 403 private, and POST to the GET-only path is 405 private;
- the response text and audit metadata exclude forbidden field names and fixture values;
- domain-table row counts do not change after allowed, denied, and failed reads.

- [ ] **Step 2: Run the API tests and verify RED**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/api/test_portal.py -q
```

Expected: FAIL because `/api/v1/me/housing` is not mounted.

- [ ] **Step 3: Add the service dependency and private router**

Add to `housing/dependencies.py`:

```python
def get_housing_portal_service(
    db: Annotated[Session, Depends(get_db)],
) -> HousingPortalService:
    return HousingPortalService(db)
```

Create `portal_router.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.housing.api_support import (
    PrivateHousingRoute,
    _register_sensitive_read_audit,
    _set_sensitive_read_result_count,
)
from app.housing.dependencies import get_housing_portal_service
from app.housing.portal_service import HousingPortalService
from app.housing.schemas import HousingPortalResponse

router = APIRouter(tags=["housing"], route_class=PrivateHousingRoute)


def _housing_portal_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[HousingPortalService, Depends(get_housing_portal_service)],
) -> AuthContext:
    _register_sensitive_read_audit(
        request,
        service.db,
        context,
        action="housing.portal.read",
    )
    return context


@router.get("/me/housing", response_model=HousingPortalResponse)
def get_my_housing(
    request: Request,
    context: Annotated[AuthContext, Depends(_housing_portal_context)],
    service: Annotated[HousingPortalService, Depends(get_housing_portal_service)],
) -> HousingPortalResponse:
    result = service.get_housing(
        user_id=context.account.id,
        actor_person_id=context.person.id,
        as_of=datetime.now(UTC),
    )
    _set_sensitive_read_result_count(request, len(result.subjects))
    return result
```

FastAPI caches identical dependencies per request, so `_housing_portal_context` and the route receive the same service instance and database session.

- [ ] **Step 4: Mount the route and exact private path**

Include `housing_portal_router` in `api/router.py`. Add `"/api/v1/me/housing"` to `_PORTAL_EXACT_PATHS` in `portal/routing.py` so unmatched-method and dependency failures also receive `no-store, private`.

- [ ] **Step 5: Run backend portal and housing tests and verify GREEN**

Run serially:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing/test_schemas.py \
  apps/api/tests/services/test_housing_portal.py \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py \
  apps/api/tests/api/test_housing_occupancy.py -q
```

Expected: all focused backend tests PASS and PostgreSQL tests are not skipped.

- [ ] **Step 6: Commit the API**

```bash
git add apps/api/app/housing/portal_router.py \
  apps/api/app/housing/dependencies.py apps/api/app/api/router.py \
  apps/api/app/portal/routing.py apps/api/tests/api/test_portal.py
git commit -m "feat: expose audited personal housing API"
```

## Task 5: Add the Web allowlist contract and account-scoped cache key

**Files:**

- Modify: `apps/web/src/portal/api.ts`
- Modify: `apps/web/src/portal/api.test.ts`
- Modify: `apps/web/src/portal/usePortalAccessError.ts`

- [ ] **Step 1: Add failing API parsing and query-key tests**

Import `getMyHousing` and assert:

```typescript
it("requests and allowlists personal housing before it reaches query cache", async () => {
  const forbiddenPhone = "13800138000"
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          as_of: "2026-08-05T16:00:00+08:00",
          subjects: [
            {
              subject_person_id: "e1000000-0000-4000-8000-000000000004",
              subject_name: "个人住宿学员",
              phone: forbiddenPhone,
              current_stay: null,
              planned_stays: [],
            },
          ],
          identity_number: "11010520120102106X",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  const result = await getMyHousing()

  expect(result).toEqual({
    as_of: "2026-08-05T16:00:00+08:00",
    subjects: [
      {
        subject_person_id: "e1000000-0000-4000-8000-000000000004",
        subject_name: "个人住宿学员",
        current_stay: null,
        planned_stays: [],
      },
    ],
  })
  expect(JSON.stringify(result)).not.toContain(forbiddenPhone)
  expect(portalKeys.housing("account-1")).toEqual([
    "portal",
    "account-1",
    "housing",
  ])
})
```

Add a second test where `planned_stays[0].roommates` is `null`, and a malformed response test that expects the generic `个人住宿响应格式无效` error without echoing the malformed value.

- [ ] **Step 2: Run portal API tests and verify RED**

Run:

```bash
npm --prefix apps/web test -- --run src/portal/api.test.ts
```

Expected: FAIL because the housing client and key do not exist.

- [ ] **Step 3: Implement Zod stripping and typed fetch**

Add required housing booleans to `PortalContext`, extend `PortalCapability`, and define:

```typescript
import { z } from "zod"

const housingPortalStaySchema = z.object({
  building_name: z.string(),
  floor_name: z.string(),
  room_name: z.string(),
  stay_purpose: z.enum(["permanent", "camp", "temporary"]),
  planned_check_in_at: z.string(),
  planned_check_out_at: z.string().nullable(),
  actual_check_in_at: z.string().nullable(),
  actual_check_out_at: z.string().nullable(),
  status: z.enum(["planned", "checked_in", "checkout_pending"]),
  roommates: z.array(z.string()).nullable(),
})

const housingPortalSubjectSchema = z.object({
  subject_person_id: z.uuid(),
  subject_name: z.string(),
  current_stay: housingPortalStaySchema.nullable(),
  planned_stays: z.array(housingPortalStaySchema),
})

const myHousingSchema = z.object({
  as_of: z.string(),
  subjects: z.array(housingPortalSubjectSchema),
})

export type HousingPortalStay = z.infer<typeof housingPortalStaySchema>
export type HousingPortalSubject = z.infer<typeof housingPortalSubjectSchema>
export type MyHousing = z.infer<typeof myHousingSchema>

export async function getMyHousing(signal?: AbortSignal): Promise<MyHousing> {
  const raw = await apiFetch<unknown>("/me/housing", { signal })
  const parsed = myHousingSchema.safeParse(raw)
  if (!parsed.success) throw new Error("个人住宿响应格式无效")
  return parsed.data
}
```

Add `housing: (accountId) => ["portal", accountId, "housing"]` to `portalKeys`. Zod object parsing must strip unrecognized keys before returning.

- [ ] **Step 4: Treat private 404 as revoked portal scope**

Change `usePortalAccessError` so `permissionRevoked` is true for Problem Details status 403 or 404. Keep 401 handling in `AuthProvider`. Cancel and remove the complete account portal branch before navigation, then remove it once more after cancellation settles so a late response cannot repopulate old housing, learning, payment, or coach projections:

```typescript
useEffect(() => {
  if (!permissionRevoked || !user) return
  const root = portalKeys.root(user.account_id)
  const cancellation = queryClient.cancelQueries({ queryKey: root })
  queryClient.removeQueries({ queryKey: root })
  void cancellation.finally(() => {
    queryClient.removeQueries({ queryKey: root })
  })
  navigate("/", { replace: true })
}, [navigate, permissionRevoked, queryClient, user])
```

- [ ] **Step 5: Run portal API and access-error tests and verify GREEN**

Run:

```bash
npm --prefix apps/web test -- --run \
  src/portal/api.test.ts \
  src/portal/LearningPage.test.tsx \
  src/portal/MyPaymentsPage.test.tsx
```

Expected: all listed tests PASS and existing private 404 behavior remains safe.

- [ ] **Step 6: Commit the client contract**

```bash
git add apps/web/src/portal/api.ts apps/web/src/portal/api.test.ts \
  apps/web/src/portal/usePortalAccessError.ts \
  apps/web/src/portal/LearningPage.test.tsx \
  apps/web/src/portal/MyPaymentsPage.test.tsx
git commit -m "feat: add personal housing client"
```

## Task 6: Build the responsive personal housing page

**Files:**

- Create: `apps/web/src/housing/MyHousingPage.tsx`
- Create: `apps/web/src/housing/MyHousingPage.test.tsx`
- Modify: `apps/web/src/housing/housing.css`

- [ ] **Step 1: Write failing page-state and privacy tests**

Use a real `QueryClient`, `AuthProvider`, and memory router. Cover:

```typescript
it("switches students and distinguishes hidden future roommates from no roommate", async () => {
  const { queryClient } = renderHousingPage(personalHousingResponse)
  const user = userEvent.setup()

  expect(await screen.findByRole("heading", { name: "当前住宿" })).toBeVisible()
  expect(screen.getByText("当前室友甲")).toBeVisible()
  await user.selectOptions(screen.getByLabelText("当前学员"), adultStudentId)
  expect(screen.getByRole("heading", { name: "成年后仍绑定学员" })).toBeVisible()
  expect(screen.getByText("实际入住后可查看")).toBeVisible()
  expect(screen.queryByText("未来室友机密姓名")).not.toBeInTheDocument()
  expect(JSON.stringify(queryClient.getQueryData(portalKeys.housing(accountId))))
    .not.toContain("13800138000")
})


it("shows no-stay, overdue planned, checked-in and checkout-pending labels", async () => {
  const user = userEvent.setup()
  renderHousingPage(statusMatrixResponse)

  await user.selectOptions(screen.getByLabelText("当前学员"), noStayStudentId)
  expect(await screen.findByText("暂无住宿安排")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), overdueStudentId)
  expect(screen.getByText("待确认入住")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), checkedInStudentId)
  expect(screen.getByText("已入住")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), pendingStudentId)
  expect(screen.getByText("待确认退房")).toBeVisible()
})


it("removes cached names and navigates home on private 404", async () => {
  const { queryClient, router } = renderHousingPage(private404Response, {
    seed: personalHousingResponse,
  })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(screen.queryByText("旧缓存学员")).not.toBeInTheDocument()
  expect(queryClient.getQueryData(portalKeys.housing(accountId))).toBeUndefined()
})
```

Also assert single-subject responses do not render a selector, null times do not render fabricated labels, long names wrap, visible controls are at least 44px, and forbidden key names/fixture values never enter the DOM.

- [ ] **Step 2: Run page tests and verify RED**

Run:

```bash
npm --prefix apps/web test -- --run src/housing/MyHousingPage.test.tsx
```

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement the read-only page**

Use one query with an abort-aware query function and hide cached data during refetch:

```typescript
const housing = useQuery({
  queryKey: portalKeys.housing(accountId),
  queryFn: ({ signal }) => getMyHousing(signal),
  enabled: Boolean(user?.account_id),
  ...guardedPortalQueryOptions,
})
```

Keep selected subject state only when its ID remains authorized:

```typescript
useEffect(() => {
  const subjects = housing.data?.subjects ?? []
  if (subjects.length === 0) {
    setSelectedSubjectId(null)
    return
  }
  if (!subjects.some((item) => item.subject_person_id === selectedSubjectId)) {
    setSelectedSubjectId(subjects[0].subject_person_id)
  }
}, [housing.data?.subjects, selectedSubjectId])
```

Implement these fixed labels:

```typescript
const PURPOSE_LABELS = {
  permanent: "常住",
  camp: "随营",
  temporary: "临时住宿",
} as const

function statusLabel(stay: HousingPortalStay, asOf: string): string {
  if (stay.status === "checked_in") return "已入住"
  if (stay.status === "checkout_pending") return "待确认退房"
  return Date.parse(stay.planned_check_in_at) <= Date.parse(asOf)
    ? "待确认入住"
    : "已预约"
}
```

Render:

- page heading `住宿信息`;
- a `当前学员` select only when `subjects.length > 1`;
- subject name, current stay card, and planned reservation list;
- current `roommates=[]` as `暂无室友`;
- current roommate names as a plain list;
- planned `roommates=null` as `实际入住后可查看`;
- only non-null planned/actual time rows formatted with `formatShanghaiDateTime`;
- loading, retryable service error, and `暂无住宿安排` states.

When `usePortalAccessError` reports scope loss, render no old body. Do not put `subject_person_id` in headings, text, links, URL parameters, test IDs, or ARIA labels; it may remain the native select option value required for stable selection.

- [ ] **Step 4: Add responsive CSS**

Add `.myHousingPage`, `.myHousingSubjectPicker`, `.myHousingCurrent`, `.myHousingPlannedGrid`, `.myHousingStayCard`, `.myHousingTimeGrid`, and `.myHousingRoommates` rules. Use:

- two columns at 1024 and 1440;
- one column at 768 and 360;
- `overflow-wrap:anywhere` for names and room labels;
- full-width select at 360;
- 44px minimum controls;
- bottom spacing above `.shellMobileNavigation` at 360;
- no page-level `overflow-x:hidden` workaround.

- [ ] **Step 5: Run the page and housing component tests and verify GREEN**

Run:

```bash
npm --prefix apps/web test -- --run \
  src/housing/MyHousingPage.test.tsx \
  src/housing/HousingOverviewPage.test.tsx \
  src/housing/RoomCard.test.tsx
```

Expected: all listed tests PASS.

- [ ] **Step 6: Commit the page**

```bash
git add apps/web/src/housing/MyHousingPage.tsx \
  apps/web/src/housing/MyHousingPage.test.tsx \
  apps/web/src/housing/housing.css
git commit -m "feat: add personal housing page"
```

## Task 7: Wire server capabilities through routes, navigation, and dashboard

**Files:**

- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.test.tsx`
- Modify: `apps/web/src/auth/AuthProvider.test.tsx`
- Modify: `apps/web/src/auth/LoginPage.test.tsx`

- [ ] **Step 1: Update every context fixture and write failing capability tests**

Every `PortalContext` mock must explicitly include both new booleans. Add route tests proving:

```typescript
expectManagementContext({
  housing_management_available: true,
  housing_available: false,
})
expectPersonalContext({
  housing_management_available: false,
  housing_available: true,
})
```

Assert a personal-only account reaches `/me/housing` and is redirected from `/housing`; a building reader reaches `/housing` and is redirected from `/me/housing`; pure raw `housing.read` arrays do not bypass a false server capability.

Add a focus-reauthorization test that seeds `portalKeys.housing(accountId)` with an old student name, changes the second `/portal/context` response from `housing_available: true` to `false`, leaves an old `/me/housing` request pending, and asserts the route returns to `/`, the old name disappears, the account housing key is absent, and resolving the delayed response cannot repopulate it.

Add AppShell tests that `住宿信息` uses `housing_available` and `房态管理` uses `housing_management_available`. Add a dashboard test that the personal card links to `/me/housing` but no `/me/housing` API request is made merely to render the card.

- [ ] **Step 2: Run the capability tests and verify RED**

Run:

```bash
npm --prefix apps/web test -- --run \
  src/app/router.test.tsx \
  src/layout/AppShell.test.tsx \
  src/portal/PortalDashboardPage.test.tsx \
  src/auth/AuthProvider.test.tsx \
  src/auth/LoginPage.test.tsx
```

Expected: type-check or assertion FAIL because the new capabilities and route are not wired.

- [ ] **Step 3: Guard both routes with `PortalCapabilityRoute`**

Import `MyHousingPage`. Keep `HousingRoute` only as the component-prop adapter and define routes:

```tsx
{
  path: "/me/housing",
  element: (
    <PortalCapabilityRoute capability="housing_available">
      <MyHousingPage />
    </PortalCapabilityRoute>
  ),
},
{
  path: "/housing",
  element: (
    <PortalCapabilityRoute capability="housing_management_available">
      <HousingRoute />
    </PortalCapabilityRoute>
  ),
},
```

Remove the raw permission redirect from `HousingRoute`; the API still authorizes every catalog request and the server context controls route availability.

Inside `PortalCapabilityRoute`, add a housing-specific effect before any conditional return. When a completed context response changes `housing_available` to false, cancel and remove `portalKeys.housing(user.account_id)`, repeat removal when cancellation settles, and then let the existing capability redirect run. This covers capability removal without requiring a failing personal-housing response and prevents a delayed old response from repopulating the cache.

- [ ] **Step 4: Separate both navigation items**

Import `BedDouble`. Add:

```typescript
{
  to: "/me/housing",
  label: "住宿信息",
  shortLabel: "住宿",
  icon: BedDouble,
  mobilePriority: 3,
  portalCapability: "housing_available",
}
```

Move payment to the next mobile priority. Change the existing `/housing` item from `permission: "housing.read"` to `portalCapability: "housing_management_available"`.

- [ ] **Step 5: Add the dashboard card without a housing prefetch**

Include `housing_available` in the personal `hasPortalCapability` expression, but exclude `housing_management_available`. Render a `住宿信息` card linking to `/me/housing` with text `当前住宿与未来预约`. Add `housing.read` to the back-office permission set and evaluate both global and scoped permission arrays so management-only accounts retain the existing back-office dashboard.

- [ ] **Step 6: Run all capability tests and verify GREEN**

Run the Step 2 command again.

Expected: all listed tests PASS.

- [ ] **Step 7: Run the complete Web unit suite and build**

Run:

```bash
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all unit tests PASS, TypeScript/Vite build exits zero, ESLint exits zero.

- [ ] **Step 8: Commit route and navigation wiring**

```bash
git add apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx \
  apps/web/src/portal/PortalDashboardPage.tsx \
  apps/web/src/portal/PortalDashboardPage.test.tsx \
  apps/web/src/auth/AuthProvider.test.tsx \
  apps/web/src/auth/LoginPage.test.tsx
git commit -m "feat: gate housing portals by server capability"
```

## Task 8: Add an opt-in E2E housing scenario and four-viewport journey

**Files:**

- Create: `apps/api/app/cli/seed_housing_portal_e2e.py`
- Create: `apps/api/tests/cli/test_seed_housing_portal_e2e.py`
- Create: `apps/web/tests/housing-personal-portal.spec.ts`
- Create: `scripts/run-housing-personal-portal-e2e.sh`

- [ ] **Step 1: Write a failing opt-in seed test**

The canonical `seed_e2e` must still create zero stays. After calling the new scenario seeder twice, assert fixed counts and relationships:

```python
TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=(
            "postgresql+psycopg://erp:erp@db:5432/"
            "housing_personal_portal_test"
        ),
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token=(
            "evaluation-local-service-token-2026-change-me"
        ),
    )


def seed_kwargs() -> dict[str, object]:
    return {
        "settings": settings(),
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def test_housing_portal_scenario_is_opt_in_and_idempotent(db: Session) -> None:
    seed_e2e(db, **seed_kwargs())
    assert db.scalar(select(func.count(HousingStay.id))) == 0

    first = seed_housing_portal_e2e(db, settings=settings())
    second = seed_housing_portal_e2e(db, settings=settings())

    assert first == second
    assert first.current_stays == 3
    assert first.planned_stays == 8
    assert db.scalar(select(func.count(HousingStay.id))) == 11
    assert db.scalar(
        select(func.count(HousingRoom.id)).where(
            HousingRoom.id.in_(first.room_ids),
            HousingRoom.verification_status == "verified",
        )
    ) == len(first.room_ids)
```

The three current rows are two female guarded students sharing `4-201` plus the male self student in `4-210`. For each viewport adult student, create one future planned stay and one same-period male coach stay in a separate double room so a future roommate exists but must remain hidden. Use fixed UUIDs, fixed aware times beginning in August, September, October, and November 2030, and fixed room numbers `4-202`, `4-203`, `4-205`, and `4-206`.

- [ ] **Step 2: Run the seed test and verify RED**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/cli/test_seed_housing_portal_e2e.py -q
```

Expected: FAIL because the scenario seeder does not exist.

- [ ] **Step 3: Implement the guarded scenario seeder**

Create a result dataclass:

```python
CURRENT_STAY_IDS = (
    UUID("e2700000-0000-4000-8000-000000000001"),
    UUID("e2700000-0000-4000-8000-000000000002"),
    UUID("e2700000-0000-4000-8000-000000000003"),
)
FUTURE_STAY_IDS_BY_VIEWPORT = {
    1440: (
        UUID("e2710000-0000-4000-8000-000000000001"),
        UUID("e2720000-0000-4000-8000-000000000001"),
    ),
    1024: (
        UUID("e2710000-0000-4000-8000-000000000002"),
        UUID("e2720000-0000-4000-8000-000000000002"),
    ),
    768: (
        UUID("e2710000-0000-4000-8000-000000000003"),
        UUID("e2720000-0000-4000-8000-000000000003"),
    ),
    360: (
        UUID("e2710000-0000-4000-8000-000000000004"),
        UUID("e2720000-0000-4000-8000-000000000004"),
    ),
}
FUTURE_ROOM_BY_VIEWPORT = {
    1440: "4-202",
    1024: "4-203",
    768: "4-205",
    360: "4-206",
}


@dataclass(frozen=True)
class HousingPortalE2ESeedResult:
    current_stays: int
    planned_stays: int
    room_ids: tuple[UUID, ...]
```

`seed_housing_portal_e2e` must call `_require_e2e_environment(settings)`, delete only its eleven fixed stay IDs, verify its six selected rooms, fetch all fixed people/accounts, insert snapshot-backed `HousingStay` rows, and commit through `write_transaction`. It must refuse missing canonical people, rooms, or the admin account rather than creating partial rows. The module CLI must use `get_settings()` and `SessionLocal()` and print only safe counts.

- [ ] **Step 4: Run the seed test and verify GREEN**

Run the Step 2 command again.

Expected: PASS with the canonical seed still containing zero default stays.

- [ ] **Step 5: Write the personal housing Playwright journey**

Define the complete private test mapping and helpers before the test:

```typescript
import { expect, request, test } from "@playwright/test"
import type { APIRequestContext, Page } from "@playwright/test"

import {
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsGuardian,
  loginAsStudent,
  logout,
} from "./helpers"

const API_BASE_URL =
  process.env.E2E_API_URL ??
  process.env.E2E_BASE_URL ??
  "http://127.0.0.1:18080"

const REVOCATION_STUDENTS = {
  1440: {
    id: "e2000000-0000-4000-8000-000000000010",
    name: "E2E招生学员1440",
    relationshipId: "e2400000-0000-4000-8000-000000000001",
    room: "4-202",
  },
  1024: {
    id: "e2000000-0000-4000-8000-000000000020",
    name: "E2E招生学员1024",
    relationshipId: "e2400000-0000-4000-8000-000000000002",
    room: "4-203",
  },
  768: {
    id: "e2000000-0000-4000-8000-000000000030",
    name: "E2E招生学员768",
    relationshipId: "e2400000-0000-4000-8000-000000000003",
    room: "4-205",
  },
  360: {
    id: "e2000000-0000-4000-8000-000000000040",
    name: "E2E招生学员360",
    relationshipId: "e2400000-0000-4000-8000-000000000004",
    room: "4-206",
  },
} as const

async function createAdminRequest(): Promise<{
  admin: APIRequestContext
  csrfToken: string
}> {
  const admin = await request.newContext({ baseURL: API_BASE_URL })
  const response = await admin.post("/api/v1/auth/login", {
    data: {
      phone: "19990000001",
      password: "E2EAdm1n26",
    },
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
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")))
}
```

For each configured viewport:

```typescript
test("guardian and student see only authorized personal housing", async ({ page }, testInfo) => {
  const width = page.viewportSize()?.width ?? 1440
  const adult = REVOCATION_STUDENTS[width as keyof typeof REVOCATION_STUDENTS]
  if (!adult) throw new Error(`Missing housing fixture for ${width}`)

  await loginAsGuardian(page)
  await page.getByRole("link", { name: "住宿信息" }).click()
  await expect(page.getByRole("heading", { name: "住宿信息" })).toBeVisible()
  await page.getByLabel("当前学员").selectOption({ label: "E2E已绑定学员" })
  await expect(page.getByText("4-201", { exact: true })).toBeVisible()
  await expect(page.getByText("E2E监护人第二学员", { exact: true })).toBeVisible()

  await page.getByLabel("当前学员").selectOption({ label: adult.name })
  await expect(page.getByText(adult.room, { exact: true })).toBeVisible()
  await expect(page.getByText("实际入住后可查看")).toBeVisible()
  await expect(page.getByText("E2E教学教练", { exact: true })).toHaveCount(0)

  const { admin, csrfToken } = await createAdminRequest()
  try {
    const ended = await admin.post(
      `/api/v1/people/${adult.id}/guardians/${adult.relationshipId}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: `住宿门户四视口撤权 ${testInfo.project.name}` },
      },
    )
    expect(ended.status()).toBe(200)
  } finally {
    await admin.dispose()
  }

  await refocus(page)
  await expect(page.getByText(adult.name, { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".myHousingStayCard")

  await logout(page)
  await loginAsStudent(page)
  await page.goto("/me/housing")
  await expect(page.getByText("E2E自助学员", { exact: true })).toBeVisible()
  await expect(page.getByText("4-210", { exact: true })).toBeVisible()
  await expect(page.getByLabel("当前学员")).toHaveCount(0)
})
```

Also assert no phone, email, identity, sex, problem note, or UUID label is visible.

- [ ] **Step 6: Create the isolated non-Docker launcher**

Copy the safety structure of `run-housing-room-status-e2e.sh`, but use different default ports, for example API `8003` and Web `5178`. For every viewport project:

```bash
cd "$repo_root/apps/api"
"$python_bin" -m app.cli.seed_e2e
"$python_bin" -m app.cli.seed_housing_portal_e2e
cd "$repo_root"
E2E_API_URL="$api_origin" E2E_BASE_URL="$web_origin" \
  npm --prefix apps/web run test:e2e -- \
  housing-personal-portal.spec.ts --project="$project"
```

The launcher must require the isolated database and secrets, verify free ports, start only owned local processes, emit bounded logs on failure, and never invoke Docker.

Make the launcher executable:

```bash
chmod +x scripts/run-housing-personal-portal-e2e.sh
```

- [ ] **Step 7: Run all four viewports**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
scripts/run-housing-personal-portal-e2e.sh
```

Expected: the guardian/student/revocation journey passes at 1440, 1024, 768, and 360 with no Docker process.

- [ ] **Step 8: Commit E2E coverage**

```bash
git add apps/api/app/cli/seed_housing_portal_e2e.py \
  apps/api/tests/cli/test_seed_housing_portal_e2e.py \
  apps/web/tests/housing-personal-portal.spec.ts \
  scripts/run-housing-personal-portal-e2e.sh
git commit -m "test: verify personal housing portal"
```

## Task 9: Run the complete local gate and update truthful boundaries

**Files:**

- Modify: `docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md`
- Modify: `docs/superpowers/specs/2026-08-03-housing-room-status-design.md`
- Modify: `README.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Run the complete backend gate serially**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider apps/api/tests -q
.venv/bin/ruff check apps/api
PYTHONPATH=apps/api .venv/bin/mypy apps/api/app
```

Expected: every backend test executes and passes, PostgreSQL tests are not skipped, Ruff exits zero, and mypy reports success for all production files.

- [ ] **Step 2: Run the complete Web gate**

Run:

```bash
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all Web tests PASS, build exits zero, and lint exits zero.

- [ ] **Step 3: Re-run the personal-housing four-viewport gate**

Run `scripts/run-housing-personal-portal-e2e.sh` with the isolated database and required secrets after the complete unit suites.

Expected: all four viewport projects PASS again.

- [ ] **Step 4: Write truthful status tests first**

Update `test_operations_docs.py` to require wording that distinguishes:

- lifecycle backend API implemented locally;
- personal student/guardian housing portal implemented locally;
- administrator lifecycle dialogs and full dynamic room-management UI still pending;
- real-name export/reporting, Docker/NAS, police API, backup/restore, and production still unverified.

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/cli/test_operations_docs.py -q
```

Expected: FAIL until the documents are updated.

- [ ] **Step 5: Update the accepted specs and README without overclaiming**

Set the personal portal design status to locally implemented only after all gates pass. Update the parent housing status and README to state that the read-only personal portal and lifecycle API exist, while administrator lifecycle UI, export/reporting, Docker/NAS, police integration, and production remain pending. Add the personal E2E launcher command. Do not call Stage 3 complete.

- [ ] **Step 6: Verify documentation and diff cleanliness**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/cli/test_operations_docs.py -q
git diff --check
git status --short
```

Expected: documentation test PASS, `git diff --check` prints nothing, and status lists only the intended final documentation changes.

- [ ] **Step 7: Commit truthful completion status**

```bash
git add README.md \
  docs/superpowers/specs/2026-08-03-housing-room-status-design.md \
  docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md \
  apps/api/tests/cli/test_operations_docs.py
git commit -m "docs: record personal housing portal gate"
```

- [ ] **Step 8: Perform a final clean verification**

Run:

```bash
git status --short --branch
git log -10 --oneline
git diff HEAD^ --check
```

Expected: the worktree is clean, the task commits are visible, and the final commit has no whitespace errors.

## Plan self-review result

- **Spec coverage:** Tasks 1–4 cover strict DTOs, self/family authorization, current/planned projection, private errors and one-event audit. Tasks 5–7 cover the client allowlist, account cache, responsive page, capability-only navigation and dashboard. Task 8 covers opt-in fixtures, actual/future roommate privacy, revocation and all four viewports. Task 9 covers complete local gates and truthful boundaries.
- **Scope isolation:** The plan does not require the unfinished administrator occupancy dialogs and does not touch export/reporting or deployment.
- **Privacy consistency:** Only `subject_person_id` is retained for switching. Future roommates are `null`; current roommates are names only. The API, audit, Zod parser, DOM, and query-cache assertions all use allowlists.
- **Type consistency:** Python and TypeScript use `current_stay`, `planned_stays`, the same three purposes, the same three portal statuses, and `roommates: list/string[] | null`.
- **Authorization consistency:** `housing_available` derives from an authorized self/family subject; `housing_management_available` derives from readable catalog scope. Neither capability promotes the other.
- **Time consistency:** One server UTC instant drives business-date authorization and response `as_of`; the browser formats it in `Asia/Shanghai` and does not infer actual check-in from planned time.
- **Deployment consistency:** All commands are local and non-Docker. Docker/NAS and production status remain unchanged.
