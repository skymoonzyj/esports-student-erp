# Housing Occupancy Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing static housing catalog into a locally complete occupancy lifecycle with account-backed resident selection, reservation and check-in, capacity and gender enforcement, room issues, transfers, two-step checkout, due-checkout reconciliation, and privacy-bounded self/family viewing.

**Architecture:** Keep the existing building/floor/room inventory as the catalog authority and add append-preserving `HousingStay`, `HousingRoomIssue`, and `HousingTransfer` records. Put all locking, interval conflict checks, identity readiness, and state transitions in focused housing services; internal and portal routers only perform authentication, object authorization, safe audit mapping, and response serialization. Extend the current React housing area with revision-gated room details and mutation dialogs, while `/me/housing` receives a separate minimal family projection.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16 range/exclusion constraints, Alembic, Pydantic 2, Pytest, React 19, TypeScript 6, TanStack Query 5, Vitest, Playwright.

---

## Scope and file map

This plan implements only implementation unit 2 from `docs/superpowers/specs/2026-08-03-housing-room-status-design.md`.

Included:

- account-backed resident options and camp defaults;
- planned reservations, direct check-in, explicit existing-occupant backfill, reservation edits, confirmation and cancellation;
- real room projections, resident summaries, independent filters and structural room guards;
- same-sex, capacity and person-time conflict enforcement under PostgreSQL locks;
- issue-room reporting/resolution, atomic transfer and two-step checkout;
- due/camp-end/cancelled-enrollment checkout-pending reconciliation;
- internal room detail plus self/family housing pages;
- responsive component and four-viewport browser coverage.

Excluded:

- Excel/CSV real-name export, report batches and police API integration;
- online payment, screenshot/OCR evaluation, video/GPU and talent assessment;
- Docker/NAS deployment, production backup or restore claims.

Backend responsibilities:

- `apps/api/app/housing/models.py`: persistence entities and database invariants only.
- `apps/api/app/housing/schemas.py`: strict request/response contracts and allowed-action projections.
- `apps/api/app/housing/access.py`: all housing RBAC tokens, revision, room and person decisions.
- `apps/api/app/housing/occupancy_service.py`: resident directory, interval validation, stays, projections, transfer and checkout.
- `apps/api/app/housing/issue_service.py`: open/resolve issue workflow only.
- `apps/api/app/housing/reconciliation.py`: idempotent transition to `checkout_pending`; never checks anyone out.
- `apps/api/app/housing/api_support.py`: private response class, public error mapping and safe audit helpers shared by housing routers.
- `apps/api/app/housing/occupancy_router.py`: internal management and room-detail API.
- `apps/api/app/housing/portal_service.py` and `portal_router.py`: minimal self/family projection.

Frontend responsibilities:

- `apps/web/src/housing/api.ts`: canonical housing DTOs and API functions.
- `apps/web/src/housing/housingKeys.ts`: account- and revision-scoped private query keys.
- `HousingOverviewPage.tsx`, `HousingFilters.tsx`, `RoomCard.tsx`: live projection and navigation to details.
- `HousingRoomDetailPage.tsx`: revision-gated current, future and historical room detail.
- `HousingStayDialog.tsx`, `HousingStayActionDialogs.tsx`, `HousingIssueDialogs.tsx`: one workflow per focused dialog.
- `HousingDialogShell.tsx`: shared focus trap, scroll lock and safe focus restoration for catalog and lifecycle dialogs.
- `MyHousingPage.tsx`: self/family-only housing projection.
- `housing.css`: shared responsive and accessible housing styles.

## Task 1: Add lifecycle tables, permission seeds and downgrade safety

**Files:**

- Create: `apps/api/migrations/versions/0010_housing_occupancy_lifecycle.py`
- Modify: `apps/api/app/housing/models.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`

- [ ] **Step 1: Write failing migration and model-parity tests**

Add tests named:

```python
def test_housing_occupancy_upgrade_downgrade_upgrade_restores_clean_schema(
    postgres_engine: Engine,
) -> None:
    assert {
        "housing_room_issues",
        "housing_stays",
        "housing_transfers",
    } <= set(inspect(connection).get_table_names())

def test_housing_occupancy_constraints_match_models(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        assert _model_constraint_names(HousingStay) <= _database_constraint_names(
            connection,
            "housing_stays",
        )

def test_housing_occupancy_downgrade_refuses_business_rows_before_ddl(
    postgres_engine: Engine,
) -> None:
    with pytest.raises(RuntimeError, match="cannot downgrade housing occupancy with business data"):
        command.downgrade(config, "0009_housing_room_status_foundation")

def test_housing_occupancy_downgrade_refuses_customized_permissions_before_ddl(
    postgres_engine: Engine,
) -> None:
    with pytest.raises(RuntimeError, match="cannot downgrade housing occupancy with customized grants"):
        command.downgrade(config, "0009_housing_room_status_foundation")
```

Extend the RBAC seed expectations with these stable IDs:

```python
HOUSING_OCCUPANCY_PERMISSION_IDS = {
    "housing.stays.manage": UUID("20000000-0000-4000-8000-000000000039"),
    "housing.transfers.manage": UUID("20000000-0000-4000-8000-000000000040"),
    "housing.checkouts.manage": UUID("20000000-0000-4000-8000-000000000041"),
    "housing.issues.manage": UUID("20000000-0000-4000-8000-000000000042"),
}
```

Assert administrator global grants plus `student/housing.read/self` and `guardian/housing.read/family`. Assert no other role receives a lifecycle operation by default.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
```

Expected: failures for missing revision `0010_housing_occupancy_lifecycle`, missing tables and missing permission seeds. If `TEST_DATABASE_URL` is absent, the database cases will skip; obtain the isolated PostgreSQL URL before marking this task complete.

- [ ] **Step 3: Add the SQLAlchemy entities and invariants**

Add these models to `models.py` with UUID keys, `TimestampMixin`, restrictive foreign keys and the listed constraints:

```python
class HousingRoomIssue(TimestampMixin, Base):
    __tablename__ = "housing_room_issues"
    id: Mapped[UUID]
    room_id: Mapped[UUID]
    description: Mapped[str]
    previous_service_status: Mapped[str]
    reported_at: Mapped[datetime]
    reported_by_user_id: Mapped[UUID]
    status: Mapped[str]
    resolved_at: Mapped[datetime | None]
    resolved_by_user_id: Mapped[UUID | None]
    resolution_note: Mapped[str | None]

class HousingStay(TimestampMixin, Base):
    __tablename__ = "housing_stays"
    id: Mapped[UUID]
    person_id: Mapped[UUID]
    room_id: Mapped[UUID]
    stay_purpose: Mapped[str]
    duration_mode: Mapped[str]
    cohort_id: Mapped[UUID | None]
    visit_reason: Mapped[str | None]
    planned_check_in_at: Mapped[datetime]
    planned_check_out_at: Mapped[datetime | None]
    actual_check_in_at: Mapped[datetime | None]
    actual_check_out_at: Mapped[datetime | None]
    status: Mapped[str]
    room_number_snapshot: Mapped[str]
    floor_name_snapshot: Mapped[str]
    building_name_snapshot: Mapped[str]
    notes: Mapped[str | None]
    cancellation_reason: Mapped[str | None]
    checkout_requested_actual_at: Mapped[datetime | None]
    checkout_requested_at: Mapped[datetime | None]
    checkout_requested_by_user_id: Mapped[UUID | None]
    created_by_user_id: Mapped[UUID]
    checked_in_by_user_id: Mapped[UUID | None]
    checked_out_by_user_id: Mapped[UUID | None]
    cancelled_by_user_id: Mapped[UUID | None]
    version: Mapped[int]

class HousingTransfer(TimestampMixin, Base):
    __tablename__ = "housing_transfers"
    id: Mapped[UUID]
    person_id: Mapped[UUID]
    from_stay_id: Mapped[UUID]
    to_stay_id: Mapped[UUID]
    transferred_at: Mapped[datetime]
    reason: Mapped[str]
    transferred_by_user_id: Mapped[UUID]
```

Use checks that enforce:

- issue status is `open|resolved`, description is nonblank, and resolution fields are all null for open/all populated for resolved;
- stay purpose is `permanent|camp|temporary`, duration is `long_term|fixed_term`, and status is `planned|checked_in|checkout_pending|checked_out|cancelled`;
- fixed-term has `planned_check_out_at > planned_check_in_at`; long-term has no planned checkout;
- camp requires `cohort_id`; temporary requires fixed-term plus a nonblank visit reason;
- actual/check-out/cancellation fields agree with the stay status;
- version is at least one;
- transfer reason is nonblank and from/to stays differ.

Create:

- partial unique index on `housing_room_issues(room_id) WHERE status = 'open'`;
- GiST exclusion on active `housing_stays` preventing overlap for the same person using `tstzrange(COALESCE(actual_check_in_at, planned_check_in_at), COALESCE(planned_check_out_at, 'infinity'), '[)')` for `planned|checked_in|checkout_pending`;
- room/status/time, person/status/time and cohort/status indexes;
- unique constraints for transfer `from_stay_id` and `to_stay_id`.

- [ ] **Step 4: Implement the Alembic revision and guarded downgrade**

The revision must set:

```python
revision = "0010_housing_occupancy_lifecycle"
down_revision = "0009_housing_room_status_foundation"
```

Create the three tables and four permissions, add global administrator grants, then add the two existing-read grants:

```python
DEFAULT_GRANTS = (
    ("admin", "housing.stays.manage", "global"),
    ("admin", "housing.transfers.manage", "global"),
    ("admin", "housing.checkouts.manage", "global"),
    ("admin", "housing.issues.manage", "global"),
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
)
```

Before any downgrade DDL, lock the three business tables, the four permission rows, their role grants, permission overrides and cohort staff assignment permission rows. Refuse downgrade if a business row exists, a seeded permission differs, a default grant is missing, or any non-default reference uses a new permission. Only after all checks pass may downgrade remove the two read grants, four administrator grants, four permissions and three tables.

- [ ] **Step 5: Update runtime RBAC seeds and run GREEN**

Add the four `PermissionSeed` entries and the six grants shown above to `rbac/seed.py`. Run the focused tests again with isolated PostgreSQL and expect PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/migrations/versions/0010_housing_occupancy_lifecycle.py \
  apps/api/app/housing/models.py apps/api/app/rbac/seed.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py
git commit -m "feat: add housing occupancy schema"
```

## Task 2: Freeze lifecycle contracts, errors, access revision and audit vocabulary

**Files:**

- Modify: `apps/api/app/housing/schemas.py`
- Modify: `apps/api/app/housing/errors.py`
- Modify: `apps/api/app/housing/access.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/unit/housing/test_schemas.py`
- Modify: `apps/api/tests/unit/housing/test_access.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing schema/access/audit tests**

Cover aware datetimes, fixed/long-term rules, temporary visit reason, camp cohort requirement, checkout actual-time validation, allowed-action serialization and strict extra-field rejection. Extend access tests so changing any of the six housing permissions changes `access_revision`, while unrelated permission changes do not.

Use this permission set:

```python
HOUSING_PERMISSIONS = frozenset({
    "housing.read",
    "housing.rooms.manage",
    "housing.stays.manage",
    "housing.transfers.manage",
    "housing.checkouts.manage",
    "housing.issues.manage",
})
```

Audit tests must accept UUID-only `stay_id`, `issue_id`, `transfer_id`, `resident_person_id`, and the new public error codes, while rejecting names, phones, identity numbers, visit reasons, issue descriptions and transfer reasons.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing/test_schemas.py \
  apps/api/tests/unit/housing/test_access.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: missing lifecycle contracts and audit vocabulary failures.

- [ ] **Step 3: Add strict request and response models**

Define these canonical input models with `AwareDatetime`, stripped strings and `extra="forbid"`:

```python
class StayCreate(HousingInputModel):
    person_id: UUID
    stay_purpose: Literal["permanent", "camp", "temporary"]
    duration_mode: Literal["long_term", "fixed_term"]
    cohort_id: UUID | None = None
    visit_reason: str | None = Field(default=None, max_length=500)
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=500)

class StayUpdate(HousingInputModel):
    version: StrictInt = Field(ge=1)
    planned_check_in_at: AwareDatetime
    planned_check_out_at: AwareDatetime | None = None
    notes: str | None = Field(default=None, max_length=500)

class ConfirmCheckInRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    actual_check_in_at: AwareDatetime

class CancelReservationRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)

class TransferStayRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    target_room_id: UUID
    transferred_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=500)

class CheckoutRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)
    actual_check_out_at: AwareDatetime

class CheckoutConfirmation(HousingInputModel):
    version: StrictInt = Field(ge=1)

class RoomIssueCreate(HousingInputModel):
    room_version: StrictInt = Field(ge=1)
    description: str = Field(min_length=1, max_length=1000)

class RoomIssueResolve(HousingInputModel):
    room_version: StrictInt = Field(ge=1)
    resolution_note: str = Field(min_length=1, max_length=1000)
```

Add `ResidentOption`, `ResidentOptionsResponse`, `CohortStayOption`, `CohortOptionsResponse`, `TransferRoomOption`, `TransferRoomOptionsResponse`, `StaySummary`, `RoomIssueSummary`, `TransferSummary`, `HousingRoomDetail`, `HousingPortalSubject`, and `HousingPortalResponse`. Every internal directory/detail wrapper carries `access_revision`. Internal resident summaries contain name, person type, sex and stay times; portal summaries contain only subject name, room label, stay period and roommate names.

Widen `HousingRoomCard.current_occupants`, `current_gender`, `has_future_reservation`, and `occupancy_status`; add `occupants`, `open_issue_summary`, `pending_transfer_count`, `future_reservation_count`, and allowed actions `create_stay|report_issue|view_detail`.

- [ ] **Step 4: Add typed service errors and safe public mappings**

Add subclasses using exactly these codes:

```python
housing_account_required
housing_profile_incomplete
minor_guardian_required
housing_room_not_verified
housing_room_unavailable
housing_capacity_conflict
housing_gender_conflict
housing_person_time_conflict
housing_stale_version
housing_checkout_confirmation_required
housing_room_has_active_stays
housing_invalid_stay_transition
housing_open_issue_exists
housing_issue_not_open
```

Do not include a resident name, room label, phone or reason in exception text returned by routers.

- [ ] **Step 5: Expand object decisions and revision**

In `HousingAccessService`, add `can_manage_stays`, `can_manage_transfers`, `can_manage_checkouts`, and `can_manage_issues`, all using `room_tokens`. Add `can_read_person(actor_person_id, target_person_id, reference_date)` using `RbacService.person_resource_tokens`; family access must continue while the relationship is valid and must not require the learner to remain a minor.

- [ ] **Step 6: Expand the audit allowlist and run GREEN**

Add only safe UUID keys and public lifecycle codes. Keep all free-form lifecycle reasons solely in domain records. Run the focused suite and expect PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/housing/schemas.py apps/api/app/housing/errors.py \
  apps/api/app/housing/access.py apps/api/app/audit/service.py \
  apps/api/tests/unit/housing/test_schemas.py \
  apps/api/tests/unit/housing/test_access.py apps/api/tests/api/test_audit.py
git commit -m "feat: define housing lifecycle contracts"
```

## Task 3: Build real occupancy projections and protect catalog mutations

**Files:**

- Create: `apps/api/app/housing/occupancy_service.py`
- Modify: `apps/api/app/housing/inventory_service.py`
- Modify: `apps/api/app/housing/schemas.py`
- Modify: `apps/api/tests/db/test_housing_inventory_service.py`
- Create: `apps/api/tests/db/test_housing_occupancy_projection.py`
- Modify: `apps/api/tests/api/test_housing.py`

- [ ] **Step 1: Write failing projection and catalog-guard tests**

Create fixtures with:

- one checked-in male resident, one checkout-pending male resident and one future reservation in a verified double room;
- an empty verified room, an occupied problem room and an expired checked-out record;
- overlapping current/future records across two cohorts and person types.

Assert:

```python
assert card.current_occupants == 2
assert card.remaining_capacity == 0
assert card.current_gender == "male"
assert card.occupancy_status == "occupied"
assert card.has_future_reservation is True
assert card.future_reservation_count == 1
assert summary.occupancy_rate == Decimal("100.00")
```

Also assert independent `occupied/unoccupied` and `assignable/unassignable` filters, plus cohort/person-type filtering. Verify checked-out/cancelled rows do not occupy. Verify capacity reduction below the maximum target-period occupancy, conversion to functional, floor movement, maintenance/disable and deletion are rejected when current/future stays exist; notes and safe room-label edits remain possible with historical snapshots unchanged.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/db/test_housing_occupancy_projection.py \
  apps/api/tests/db/test_housing_inventory_service.py \
  apps/api/tests/api/test_housing.py -q
```

- [ ] **Step 3: Implement batched projection**

Add immutable service values:

```python
@dataclass(frozen=True)
class RoomOccupancyProjection:
    current_stays: tuple[StaySummary, ...]
    future_reservation_count: int
    open_issue: RoomIssueSummary | None

    @property
    def current_gender(self) -> Literal["male", "female"] | None:
        return cast(Literal["male", "female"], self.current_stays[0].sex) if self.current_stays else None
```

`room_projections(room_ids, as_of)` must load all people/profile labels, current stays, future planned rows and open issues in bounded batched queries. Current occupancy is only `checked_in|checkout_pending`; a future `planned` record never turns the card red.

- [ ] **Step 4: Replace static inventory values and add filters**

`HousingInventoryService.overview` must take one `as_of`, use one projection map for cards and summary, and compute:

```python
is_assignable = base_assignable and current_occupants < room.capacity
remaining_capacity = max(0, room.capacity - current_occupants)
occupancy_status = "occupied" if current_occupants else "unoccupied"
```

Partially occupied rooms must match both occupied and assignable. Add `cohort_id` and `person_type` to `HousingOverviewFilters`; match rooms with a current or future active stay in that cohort/type.

- [ ] **Step 5: Enforce active-record structural guards**

Inside the existing room lock, load relevant stays ordered by UUID. Reject dangerous room edits before mutation. Capacity changes compare against the maximum simultaneous planned/actual occupancy, not only the current count. `delete_room` rejects active/future stays with `housing_room_has_active_stays`; historical rows remain protected by the restrictive foreign key.

- [ ] **Step 6: Run GREEN and commit**

```bash
git add apps/api/app/housing/occupancy_service.py \
  apps/api/app/housing/inventory_service.py apps/api/app/housing/schemas.py \
  apps/api/tests/db/test_housing_occupancy_projection.py \
  apps/api/tests/db/test_housing_inventory_service.py apps/api/tests/api/test_housing.py
git commit -m "feat: project live housing occupancy"
```

## Task 4: Add authorized resident and cohort option directories

**Files:**

- Create: `apps/api/app/housing/dependencies.py`
- Modify: `apps/api/app/housing/occupancy_service.py`
- Create: `apps/api/tests/services/test_housing_resident_options.py`
- Modify: `apps/api/tests/api/test_housing.py`

- [ ] **Step 1: Write failing option-directory tests**

Assert room-scoped authorization occurs before candidate search. Missing, hidden and denied rooms must not execute person/profile/decryption queries and must return the private housing 404/403 contract. Authorized results include only active accounts with nondeleted people and a student, staff or visitor profile; raw phone, email, identity and account status must never serialize.

Search behavior:

- trimmed name substring, minimum two characters;
- exact normalized mobile phone by lookup digest;
- at most 20 deterministic results ordered by name then UUID;
- masked phone format `138****8000` after authorized decryption;
- each result returns server-derived `person_type`, registered sex and readiness flags.

Camp options return cohort UUID, name and default check-in/out timestamps without requiring unrelated `teaching.read`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/services/test_housing_resident_options.py \
  apps/api/tests/api/test_housing.py -q
```

- [ ] **Step 3: Add housing encryption dependencies**

Create request-scoped providers equivalent to identity/admissions configuration:

```python
def get_housing_cipher(request: Request) -> EnvelopeCipher:
    settings = request.app.state.settings
    return EnvelopeCipher(_decode_secret(settings.field_encryption_master_key.get_secret_value()))

def get_housing_lookup(request: Request) -> LookupDigest:
    settings = request.app.state.settings
    return LookupDigest(_decode_secret(settings.field_lookup_key.get_secret_value()))
```

Do not import another business domain's dependency module.

- [ ] **Step 4: Implement bounded directories**

Add `list_resident_options` and `list_cohort_options` to `HousingOccupancyService`. Derive person type in this order: student, staff type, visitor. Guardians without a visitor/staff/student profile are not accommodation candidates. Decryption failures produce a generic failed request and safe audit code; they must not return partially decrypted records.

- [ ] **Step 5: Run GREEN and commit**

```bash
git add apps/api/app/housing/dependencies.py \
  apps/api/app/housing/occupancy_service.py \
  apps/api/tests/services/test_housing_resident_options.py \
  apps/api/tests/api/test_housing.py
git commit -m "feat: add housing resident options"
```

## Task 5: Implement reservation, direct check-in, edit, confirm and cancel

**Files:**

- Modify: `apps/api/app/housing/occupancy_service.py`
- Create: `apps/api/tests/unit/housing/test_intervals.py`
- Create: `apps/api/tests/db/test_housing_stay_lifecycle.py`
- Create: `apps/api/tests/db/test_housing_concurrency.py`

- [ ] **Step 1: Write pure interval tests**

Freeze half-open behavior with examples where `[08:00, 10:00)` and `[10:00, 12:00)` do not overlap. Add sweep-line cases proving a target interval is accepted when two existing stays touch different nonoverlapping halves at capacity two, and rejected only when simultaneous occupancy exceeds capacity. End events must sort before start events at the same instant.

- [ ] **Step 2: Write failing database lifecycle tests**

Cover:

- future input creates `planned`; present/past input creates `checked_in` with actor and actual timestamp;
- future actual check-in is rejected;
- planned confirmation rechecks current account, room, guardian, capacity and gender;
- only planned rows can be edited/cancelled; cancellation reason and actor persist;
- fixed/long-term, permanent/camp/temporary and visitor restrictions;
- inactive/missing account, incomplete profile and minor guardian failures;
- pending rooms reject ordinary allocation; a separate existing-occupant backfill path accepts only current/past actual check-in and requires both room-catalog and stay-management authority;
- functional, problem, maintenance, disabled or inactive-parent rooms reject all allocation and backfill;
- same-person overlap, opposite-sex overlap and capacity overflow;
- nonoverlapping future periods may use different sexes in the same room;
- clearing a room after the final checkout later yields unspecified gender;
- stale versions fail without partial writes.

- [ ] **Step 3: Write concurrent transaction tests**

Use two independent PostgreSQL sessions and a barrier after each transaction has observed the initial room. For a single room, run male/male at final capacity and male/female races. The room row lock must serialize writers; exactly one conflicting transaction commits, the losing transaction receives a public housing conflict, and its session remains usable after rollback. Also race two different rooms for the same person; the person row lock/exclusion constraint permits exactly one active stay.

- [ ] **Step 4: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing/test_intervals.py \
  apps/api/tests/db/test_housing_stay_lifecycle.py \
  apps/api/tests/db/test_housing_concurrency.py -q
```

- [ ] **Step 5: Implement stable locking and conflict checks**

Every mutation acquires locks in this order:

```python
people = lock_people(sorted(person_ids))
rooms = lock_rooms(sorted(room_ids))
stays = lock_active_stays(sorted(person_ids), sorted(room_ids))
```

Re-authorize the locked room inside the transaction. Validate account/profile and call `GuardianReadinessPolicy.evaluate_one(person_id, business_date_at(reference_at))`; only a minor student with non-ready guardian state maps to `minor_guardian_required`.

Load active intervals that overlap the target. Reject same-person overlap first, then opposite-sex overlap, then capacity peak. The room lock is the serialization boundary for capacity/gender; the person lock plus GiST exclusion protects cross-room conflicts.

- [ ] **Step 6: Implement the lifecycle methods**

Use these service signatures:

```python
create_stay(room_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
backfill_existing_stay(room_id, input, *, actor_user_id, occurred_at, authorize_room_and_stay) -> HousingStay
update_reservation(stay_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
confirm_check_in(stay_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
cancel_reservation(stay_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
```

Snapshot building/floor/room labels when each stay is created. Never mutate or delete an existing history row to repair another operation. Translate exclusion/integrity races to the same public person-time code and roll back the transaction before reuse.

- [ ] **Step 7: Run GREEN and commit**

```bash
git add apps/api/app/housing/occupancy_service.py \
  apps/api/tests/unit/housing/test_intervals.py \
  apps/api/tests/db/test_housing_stay_lifecycle.py \
  apps/api/tests/db/test_housing_concurrency.py
git commit -m "feat: implement housing stay lifecycle"
```

## Task 6: Implement problem-room reporting and resolution

**Files:**

- Create: `apps/api/app/housing/issue_service.py`
- Create: `apps/api/tests/db/test_housing_issue_service.py`
- Modify: `apps/api/tests/db/test_housing_inventory_service.py`

- [ ] **Step 1: Write failing issue tests**

Assert a nonblank description is required, one room has at most one open issue, and a stale room version fails. Reporting on an occupied available room changes service status to problem, increments room version, preserves current residents, prevents new stay/reservation, and projects `pending_transfer_count == current_occupants`. Resolution requires a note, closes the same issue, records resolver/time, increments the room version and restores the saved pre-issue service status. It never moves or checks out residents.

- [ ] **Step 2: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/db/test_housing_issue_service.py \
  apps/api/tests/db/test_housing_inventory_service.py -q
```

- [ ] **Step 3: Implement the focused issue service**

Use room locking and these methods:

```python
report(room_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingRoomIssue
resolve(issue_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingRoomIssue
```

The partial unique index is the concurrency backstop. Map a concurrent second report to `housing_open_issue_exists`. Do not put issue description or resolution note in audit metadata.

- [ ] **Step 4: Run GREEN and commit**

```bash
git add apps/api/app/housing/issue_service.py \
  apps/api/tests/db/test_housing_issue_service.py \
  apps/api/tests/db/test_housing_inventory_service.py
git commit -m "feat: add housing issue workflow"
```

## Task 7: Implement atomic transfer, two-step checkout and reconciliation

**Files:**

- Modify: `apps/api/migrations/versions/0010_housing_occupancy_lifecycle.py`
- Modify: `apps/api/app/housing/models.py`
- Modify: `apps/api/app/housing/occupancy_service.py`
- Create: `apps/api/app/housing/reconciliation.py`
- Create: `apps/api/app/cli/reconcile_housing_checkouts.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Create: `apps/api/tests/db/test_housing_transfer_checkout.py`
- Create: `apps/api/tests/services/test_housing_reconciliation.py`
- Create: `apps/api/tests/cli/test_reconcile_housing_checkouts.py`

- [ ] **Step 1: Write failing transfer and checkout tests**

Cover checked-in and checkout-pending source stays. A successful transfer locks person, source and target rooms in UUID order; ends the source stay, creates the destination stay with copied purpose/cohort/remaining fixed-term end and label snapshots, and inserts one transfer row in the same transaction. Target failure leaves the source untouched and no transfer row exists.

Checkout tests must prove:

1. request stores the proposed actual checkout and changes `checked_in` to `checkout_pending` without releasing capacity;
2. confirmation requires the stored request plus matching version, writes actual checkout and actor, and then releases capacity;
3. repeating either operation is rejected without a second history row;
4. actual checkout is aware, not future, and not before actual check-in.

Freeze the two valid sources of `checkout_pending` before implementing the service. A manual
request has all three `checkout_requested_*` fields populated. A reconciler-created reminder has
all three fields null. Partial triplets remain invalid; `planned`, `checked_in` and `cancelled`
always have a null triplet; `checked_out` may retain a complete manual triplet or a null system
triplet. Add a migration/model parity regression proving both complete and null pending forms are
accepted and partial forms are rejected.

`request_checkout` accepts either `checked_in` or a system-created `checkout_pending` row whose
request triplet is still null. It populates the triplet and increments the version; it rejects an
already-requested row. `confirm_checkout` requires the complete stored triplet. This preserves the
two-step confirmation rule while allowing an automatic due reminder to enter the first manual
step instead of becoming permanently stuck.

Transfer-option tests load only target rooms inside the caller's readable and `housing.transfers.manage` scopes, exclude the source room and unavailable rooms, and return advisory capacity/gender compatibility for the source stay's remaining interval. A hidden target is absent from the directory and remains privately rejected if submitted directly.

- [ ] **Step 2: Write failing reconciliation tests**

At a fixed `now`, mark still-checked-in rows pending when fixed-term planned checkout has passed, the linked camp cohort has ended, or the student's enrollment in that cohort is cancelled. Do not change planned, already pending, checked-out or cancelled rows. Run twice and assert the second run changes zero rows. Explicitly assert no reconciliation path writes `actual_check_out_at`.

- [ ] **Step 3: Run tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/db/test_migrations.py::test_housing_stay_constraints_and_active_overlap_are_enforced \
  apps/api/tests/db/test_housing_transfer_checkout.py \
  apps/api/tests/services/test_housing_reconciliation.py \
  apps/api/tests/cli/test_reconcile_housing_checkouts.py -q
```

- [ ] **Step 4: Implement transfer and checkout**

Add:

```python
transfer(stay_id, input, *, actor_user_id, occurred_at, authorize_source, authorize_target) -> tuple[HousingStay, HousingStay, HousingTransfer]
request_checkout(stay_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
confirm_checkout(stay_id, input, *, actor_user_id, occurred_at, authorize_room) -> HousingStay
list_transfer_options(stay_id, *, occurred_at, access) -> TransferRoomOptionsResponse
```

Preserve `checkout_pending` on a transfer so moving rooms cannot bypass an existing due-checkout reminder. A normal checked-in source creates a normal checked-in destination.

For an automatic pending reminder, transfer the null request triplet and keep the destination
`checkout_pending`. A manually requested pending checkout is not transferable: its proposed actual
checkout cannot be later than the request time and would therefore precede any later destination
check-in. It must complete second confirmation instead. Reject future transfer times, times not
after actual check-in, same-room targets and fixed-term transfers with no remaining interval using
typed housing errors before flush; never expose a raw database constraint failure.

- [ ] **Step 5: Implement idempotent reconciliation and CLI**

`HousingCheckoutReconciler.run(now)` returns a result count and only updates status/version. It
leaves the three request fields null and never writes `actual_check_out_at`. A later authorized
`request_checkout` supplies the proposed actual time and actor. The CLI uses normal
settings/session wiring, prints counts only, and exits nonzero on failure. It performs no Docker or
daemon setup; final deployment will schedule it.

- [ ] **Step 6: Run GREEN and commit**

```bash
git add apps/api/migrations/versions/0010_housing_occupancy_lifecycle.py \
  apps/api/app/housing/models.py apps/api/app/housing/occupancy_service.py \
  apps/api/app/housing/reconciliation.py \
  apps/api/app/cli/reconcile_housing_checkouts.py \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_housing_transfer_checkout.py \
  apps/api/tests/services/test_housing_reconciliation.py \
  apps/api/tests/cli/test_reconcile_housing_checkouts.py
git commit -m "feat: add housing transfer and checkout"
```

## Task 8: Expose private internal lifecycle APIs with locked reauthorization and audit

**Files:**

- Create: `apps/api/app/housing/api_support.py`
- Create: `apps/api/app/housing/occupancy_router.py`
- Modify: `apps/api/app/housing/router.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/tests/api/test_housing.py`
- Create: `apps/api/tests/api/test_housing_occupancy.py`

- [ ] **Step 1: Write failing API tests**

Exercise these endpoints:

```text
GET    /api/v1/housing/rooms/{room_id}/detail
GET    /api/v1/housing/rooms/{room_id}/resident-options?query=张
GET    /api/v1/housing/rooms/{room_id}/cohort-options
GET    /api/v1/housing/stays/{stay_id}/transfer-options
POST   /api/v1/housing/rooms/{room_id}/stays
POST   /api/v1/housing/rooms/{room_id}/stays/backfill
PATCH  /api/v1/housing/stays/{stay_id}
POST   /api/v1/housing/stays/{stay_id}/confirm-check-in
POST   /api/v1/housing/stays/{stay_id}/cancel
POST   /api/v1/housing/stays/{stay_id}/transfer
POST   /api/v1/housing/stays/{stay_id}/request-checkout
POST   /api/v1/housing/stays/{stay_id}/confirm-checkout
POST   /api/v1/housing/rooms/{room_id}/issues
POST   /api/v1/housing/issues/{issue_id}/resolve
```

Every response, including 4xx, must be `Cache-Control: no-store, private`. Catalog, overview, detail, resident-option and cohort-option responses carry the same `access_revision`; a response generated under an older revision is not accepted by the Web client. Test global/building/floor allow, more-specific deny, missing/deleted/disabled resources, permission revocation between precheck and lock, stale version, and safe error equivalence. Denied/missing/failed writes create only one safe audit event and zero domain writes; success audit is in the same transaction as the mutation. Backfill additionally requires `housing.rooms.manage` and `housing.stays.manage` on the exact room.

- [ ] **Step 2: Run API tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/api/test_housing.py \
  apps/api/tests/api/test_housing_occupancy.py -q
```

- [ ] **Step 3: Extract shared private API support**

Move `PrivateHousingRoute`, safe `_audit`, standalone denied/failed audit and public error mapping from the existing router to `api_support.py`; update the catalog router without behavior change. Centralize the lifecycle mapping so every unrecognized `HousingServiceError` fails closed rather than leaking `str(exc)`.

- [ ] **Step 4: Implement internal routes**

Pre-check the visible room/stay before opening a write transaction, then pass an authorization callback so the service rechecks the locked current room. Return server-derived `allowed_actions` on room, stay and issue objects. Candidate endpoints require lifecycle management for that exact room; `housing.read` alone never exposes the masked-phone directory.

- [ ] **Step 5: Run GREEN and commit**

```bash
git add apps/api/app/housing/api_support.py \
  apps/api/app/housing/occupancy_router.py apps/api/app/housing/router.py \
  apps/api/app/api/router.py apps/api/tests/api/test_housing.py \
  apps/api/tests/api/test_housing_occupancy.py
git commit -m "feat: expose housing occupancy APIs"
```

## Task 9: Add self/family housing capability and minimal portal API

**Files:**

- Create: `apps/api/app/housing/portal_service.py`
- Create: `apps/api/app/housing/portal_router.py`
- Modify: `apps/api/app/portal/context_service.py`
- Modify: `apps/api/app/portal/schemas.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/services/test_housing_portal.py`
- Modify: `apps/api/tests/api/test_portal.py`

- [ ] **Step 1: Write failing portal tests**

Extend `PortalContextResponse` with:

```python
housing_management_available: bool
housing_available: bool
```

`housing_management_available` is true only when the account can read at least one global/building/floor catalog scope. `housing_available` is true for an authorized student self target or guardian with at least one currently valid relationship; an administrator without student/guardian identity does not get a personal card merely because of global access.

Test `GET /api/v1/me/housing` for student self, guardian with multiple learners, adult learner whose relationship remains valid, combined self+guardian, ended/expired relation, explicit deny and account/session revocation. Hidden and nonexistent family targets must be indistinguishable. The JSON must not contain phone, email, identity, birthday, sex or account status for subjects or roommates.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/services/test_housing_portal.py \
  apps/api/tests/api/test_portal.py -q
```

- [ ] **Step 3: Implement the portal projection**

Use `RbacService.person_resource_tokens_many` at `business_date_at(as_of)` to derive authorized targets. Return a subject even when they have no current stay so the page can display “暂无住宿”. For a current stay return only room/building/floor labels, stay purpose, planned/actual time, status and roommate full names. Roommates are people sharing the same current room; never include their IDs or other profile fields.

- [ ] **Step 4: Expose the private route and capabilities**

Mount `portal_router` separately and apply the shared private route class. Recompute capability on every context request; do not infer it from role strings in the browser.

- [ ] **Step 5: Run GREEN and commit**

```bash
git add apps/api/app/housing/portal_service.py \
  apps/api/app/housing/portal_router.py \
  apps/api/app/portal/context_service.py apps/api/app/portal/schemas.py \
  apps/api/app/api/router.py apps/api/tests/services/test_housing_portal.py \
  apps/api/tests/api/test_portal.py
git commit -m "feat: add family housing portal"
```

## Task 10: Add typed Web contracts, live room cards and read-only room details

**Files:**

- Modify: `apps/web/src/housing/api.ts`
- Modify: `apps/web/src/housing/api.test.ts`
- Create: `apps/web/src/housing/housingKeys.ts`
- Modify: `apps/web/src/housing/HousingFilters.tsx`
- Modify: `apps/web/src/housing/RoomCard.tsx`
- Modify: `apps/web/src/housing/RoomCard.test.tsx`
- Create: `apps/web/src/housing/HousingRoomDetailPage.tsx`
- Create: `apps/web/src/housing/HousingRoomDetailPage.test.tsx`
- Modify: `apps/web/src/housing/HousingOverviewPage.tsx`
- Modify: `apps/web/src/housing/HousingOverviewPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write failing client and component tests**

Assert exact query/body contracts for all Task 8 endpoints. Card tests cover red occupied, red plus green remaining-capacity label, green available, orange problem overlay, resident names/periods, future count and accessible text independent of color. Filter tests add cohort and person type while preserving independent occupancy/assignability axes.

Room detail tests cover current residents, future reservations, history, transfers, issues and server `allowed_actions`. A detail response whose `access_revision` differs from the current catalog revision must never render.

- [ ] **Step 2: Run Web tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/housing/api.test.ts \
  src/housing/RoomCard.test.tsx \
  src/housing/HousingOverviewPage.test.tsx \
  src/housing/HousingRoomDetailPage.test.tsx \
  src/app/router.test.tsx
```

- [ ] **Step 3: Add canonical TypeScript DTOs and functions**

Mirror backend literals exactly. Add query keys that include account ID where personal data is involved and `access_revision` for internal detail. Keep all calls through `apiFetch`; no component calls raw `fetch`.

- [ ] **Step 4: Render live projection and detail**

`RoomCard` navigates to `/housing/rooms/:roomId`. `HousingRoomDetailPage` fetches catalog plus room detail, renders only when revisions match, and clears/cancels all housing detail queries on 401/403/404, revision change or unmount settlement. `housingKeys.root(accountId)` owns catalog, overview, detail, resident-option and room-option cache trees; every private projection key also includes `accessRevision`. Reuse the existing client epoch/mutation coordinator rather than creating a weaker parallel cache path.

- [ ] **Step 5: Run GREEN and commit**

```bash
git add apps/web/src/housing/api.ts apps/web/src/housing/api.test.ts \
  apps/web/src/housing/housingKeys.ts \
  apps/web/src/housing/HousingFilters.tsx \
  apps/web/src/housing/RoomCard.tsx apps/web/src/housing/RoomCard.test.tsx \
  apps/web/src/housing/HousingOverviewPage.tsx \
  apps/web/src/housing/HousingOverviewPage.test.tsx \
  apps/web/src/housing/HousingRoomDetailPage.tsx \
  apps/web/src/housing/HousingRoomDetailPage.test.tsx \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx
git commit -m "feat: show live housing room details"
```

## Task 11: Build responsive stay, issue, transfer and checkout interactions

**Files:**

- Create: `apps/web/src/housing/HousingStayDialog.tsx`
- Create: `apps/web/src/housing/HousingStayDialog.test.tsx`
- Create: `apps/web/src/housing/HousingStayActionDialogs.tsx`
- Create: `apps/web/src/housing/HousingStayActionDialogs.test.tsx`
- Create: `apps/web/src/housing/HousingIssueDialogs.tsx`
- Create: `apps/web/src/housing/HousingIssueDialogs.test.tsx`
- Create: `apps/web/src/housing/HousingDialogShell.tsx`
- Modify: `apps/web/src/housing/HousingCatalogDialogs.tsx`
- Modify: `apps/web/src/housing/HousingRoomDetailPage.tsx`
- Modify: `apps/web/src/housing/HousingRoomDetailPage.test.tsx`
- Modify: `apps/web/src/housing/housing.css`

- [ ] **Step 1: Write failing workflow tests**

Test:

- debounced resident option search, masked phone, registered sex and person type;
- permanent/camp/temporary and long/fixed form switching;
- cohort defaults copied into editable aware Shanghai timestamps;
- future date labels the action “创建预约”; current/past labels it “确认入住”;
- planned edit, confirmation and cancellation with required reason;
- target room selection and required transfer reason;
- issue description/resolution note required;
- checkout first dialog captures actual departure, second confirmation repeats person/room/time and is the only call that releases;
- stale/conflict errors keep the dialog open, refetch detail and never replay the mutation;
- Escape, focus trap, scroll lock, pending lock and safe focus restoration on desktop/mobile.

- [ ] **Step 2: Run tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/housing/HousingStayDialog.test.tsx \
  src/housing/HousingStayActionDialogs.test.tsx \
  src/housing/HousingIssueDialogs.test.tsx \
  src/housing/HousingRoomDetailPage.test.tsx
```

- [ ] **Step 3: Implement focused dialogs**

Move `HousingDialogShell` from the catalog-dialog file into its own module and reuse it from catalog and lifecycle dialogs; do not place all workflows in one component. Disable buttons from server `allowed_actions`, not cached login permissions. Each mutation captures the current client epoch and room/stay version; ignore stale settlements after revocation, navigation or a newer mutation.

- [ ] **Step 4: Refresh all dependent projections safely**

On success invalidate catalog only when room versions/actions changed, and invalidate overview plus the two affected room details for stays/issues/transfers/checkouts. Cancel and purge private queries before showing a permission-revoked state. Do not use optimistic occupancy updates because capacity/gender are server-serialized.

- [ ] **Step 5: Complete responsive CSS, run GREEN and commit**

At 1440/1024 use a detail side panel or centered sheet, at 768 use full-width content, and at 360 use a single-column form with 44px touch targets and no horizontal overflow. All status colors retain icon/text labels.

```bash
git add apps/web/src/housing/HousingStayDialog.tsx \
  apps/web/src/housing/HousingStayDialog.test.tsx \
  apps/web/src/housing/HousingStayActionDialogs.tsx \
  apps/web/src/housing/HousingStayActionDialogs.test.tsx \
  apps/web/src/housing/HousingIssueDialogs.tsx \
  apps/web/src/housing/HousingIssueDialogs.test.tsx \
  apps/web/src/housing/HousingDialogShell.tsx \
  apps/web/src/housing/HousingCatalogDialogs.tsx \
  apps/web/src/housing/HousingRoomDetailPage.tsx \
  apps/web/src/housing/HousingRoomDetailPage.test.tsx \
  apps/web/src/housing/housing.css
git commit -m "feat: add housing lifecycle workflows"
```

## Task 12: Add the self/family housing page and server-capability navigation

**Files:**

- Modify: `apps/web/src/portal/api.ts`
- Modify: `apps/web/src/portal/api.test.ts`
- Create: `apps/web/src/housing/MyHousingPage.tsx`
- Create: `apps/web/src/housing/MyHousingPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.test.tsx`
- Modify: `apps/web/src/housing/housing.css`

- [ ] **Step 1: Write failing capability, route and privacy tests**

Add `housing_management_available` and `housing_available` to the TypeScript portal context. Assert the internal “房态管理” navigation uses the management capability, while “住宿信息” uses the personal capability. A student/guardian with only self/family housing access must reach `/me/housing` and must not be sent to `/housing`; a building/floor operator reaches `/housing` without being promoted to global permission.

Page tests cover multi-learner switching, adult learner relationship retention, current/none/pending states, roommate names and private error cache clearing. Assert forbidden field names and fixture values never enter the DOM or query cache.

- [ ] **Step 2: Run tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/portal/api.test.ts \
  src/housing/MyHousingPage.test.tsx \
  src/app/router.test.tsx \
  src/layout/AppShell.test.tsx \
  src/portal/PortalDashboardPage.test.tsx
```

- [ ] **Step 3: Implement the portal client and page**

Add `portalKeys.housing(accountId)` and `getMyHousing()`. Cache keys must include the authenticated account ID; logout, 401/403/404, relationship/context capability removal and account switch must cancel and remove the complete personal housing query.

- [ ] **Step 4: Wire server capabilities through navigation and dashboard**

Guard both routes with `PortalCapabilityRoute`. Keep `/housing` and `/me/housing` as separate pages and labels. Do not inspect role names or guess from `scoped_permissions` in the browser.

- [ ] **Step 5: Run GREEN and commit**

```bash
git add apps/web/src/portal/api.ts apps/web/src/portal/api.test.ts \
  apps/web/src/housing/MyHousingPage.tsx \
  apps/web/src/housing/MyHousingPage.test.tsx \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx \
  apps/web/src/portal/PortalDashboardPage.tsx \
  apps/web/src/portal/PortalDashboardPage.test.tsx \
  apps/web/src/housing/housing.css
git commit -m "feat: add personal housing portal"
```

## Task 13: Seed deterministic actors, verify four viewports and update truthful boundaries

**Files:**

- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Create: `apps/web/tests/housing-occupancy-lifecycle.spec.ts`
- Create: `apps/web/tests/housing-occupancy-revocation.spec.ts`
- Modify: `apps/web/tests/helpers.ts`
- Create: `scripts/run-housing-occupancy-e2e.sh`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-03-housing-room-status-design.md`

- [ ] **Step 1: Write failing deterministic seed tests**

Keep the canonical catalog at exactly 4 buildings, 11 floors, 188 entries and capacity 223. Reuse existing E2E people where possible and ensure the reset contains active accounts for a male student with ready guardian, female student, coach/life teacher and temporary visitor profile. Do not pre-create stays that would break first-unit static E2E; the lifecycle browser test creates its own reservations/stays through the API/UI after reset.

- [ ] **Step 2: Write the Playwright journey**

For each viewport `desktop-1440`, `desktop-1024`, `tablet-768`, and `mobile-360`, the lifecycle spec covers one deterministic journey:

1. administrator verifies a double room;
2. creates a male current stay and sees red room plus one remaining place;
3. opposite-sex allocation fails without changing occupancy;
4. creates a second male future reservation and confirms it only when eligible;
5. reports a room issue and sees current resident plus pending-transfer marker;
6. transfers the resident atomically to another verified room and resolves the issue;
7. requests checkout, verifies capacity remains occupied, then confirms checkout and sees gender reset;
8. student/guardian logs in and sees only family housing and roommate names.

Assert no horizontal page overflow and verify focus returns after every mobile dialog.

The revocation spec opens a room detail, resident search or transfer dialog as an account with a building/floor grant, revokes that grant through an administrator API request, triggers focus revalidation, and asserts that names, form drafts, actions and old query data disappear. A delayed response or mutation settlement from the old revision must not repopulate the page.

- [ ] **Step 3: Add the isolated local launcher**

Clone the safety structure of `run-housing-room-status-e2e.sh`: require `TEST_DATABASE_URL`, encryption secrets and free owned ports; run database safety, migrate, start only the local API/Web processes, re-seed before every `(viewport, spec)` pair, show bounded logs on failure and terminate only owned PIDs. Default to different ports, for example API `8002` and Web `5177`.

- [ ] **Step 4: Run focused E2E and then the complete local gate**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
scripts/run-housing-occupancy-e2e.sh

TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider apps/api/tests -q
.venv/bin/ruff check apps/api
PYTHONPATH=apps/api .venv/bin/mypy apps/api/app
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
npm --prefix apps/web run lint
git diff --check
```

Expected: all database tests execute rather than skip; all commands exit zero.

- [ ] **Step 5: Update status without overstating completion**

README and design section 17 may say “入住生命周期本地实现与非 Docker 门禁已完成” only after the complete gate passes. They must continue to state that real-name export/reporting is unit 3, police online API is deferred, and Docker/NAS/backup/production deployment remain unverified.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/api/tests/cli/test_operations_docs.py apps/web/tests/helpers.ts \
  apps/web/tests/housing-occupancy-lifecycle.spec.ts \
  apps/web/tests/housing-occupancy-revocation.spec.ts \
  scripts/run-housing-occupancy-e2e.sh README.md \
  docs/superpowers/specs/2026-08-03-housing-room-status-design.md
git commit -m "test: verify housing occupancy lifecycle"
```

## Plan self-review result

- Spec coverage: all implementation-unit-2 acceptance points are assigned to Tasks 1–13; real-name export remains explicitly outside this plan.
- Persistence consistency: model, migration, schemas and TypeScript literals use the same five stay statuses, three purposes and two duration modes.
- Privacy consistency: internal options are room-operation scoped and masked; family responses never serialize roommate identifiers or sensitive profile fields.
- Concurrency consistency: every write reauthorizes after stable person/room/stay locks; PostgreSQL exclusion is a backstop for cross-room person overlap.
- Checkout consistency: due/cancelled/ended conditions only produce `checkout_pending`; only second confirmation writes actual checkout and releases capacity.
- Deployment consistency: no Docker/NAS change occurs in this plan.
