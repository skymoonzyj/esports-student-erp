# Housing Task 8 Quality Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three Important Task 8 quality findings without widening the 14-route API surface or starting Task 9.

**Architecture:** Keep lifecycle invariants in `HousingOccupancyService`, derive UI actions from the same request-time boundary, and keep every mutation plus its allowed audit event in one transaction. Add a request-scoped sensitive-read audit context to `PrivateHousingRoute` so response serialization, denial, validation, mapped service failure, database failure, and unexpected failure each produce exactly one safe event. Successful mutation audit identity is selected from the committed operation result, so newly created stays/issues and transfers have canonical domain correlation.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, PostgreSQL 16, Pydantic 2, pytest, Ruff, mypy.

---

## Task 1: Reject backdated transfers after a fixed-term stay has expired

**Files:**

- Modify: `apps/api/app/housing/occupancy_service.py`
- Modify: `apps/api/app/housing/occupancy_router.py`
- Modify: `apps/api/tests/db/test_housing_transfer_checkout.py`
- Modify: `apps/api/tests/api/test_housing_occupancy.py`

- [ ] **Step 1: Write the failing service regression**

Add a dedicated database test that creates a checked-in fixed-term source whose `planned_check_out_at` is one minute before `NOW`, calls `transfer()` with `occurred_at=NOW` but `transferred_at=NOW - 30 minutes`, and expects `HousingInvalidStayTransition`. Capture the source row and counts before the call, then assert the source status/time/version is unchanged, no destination stay exists, and no `HousingTransfer` row exists.

```python
with pytest.raises(HousingInvalidStayTransition):
    HousingOccupancyService(db).transfer(
        source.id,
        TransferStayRequest(
            version=1,
            target_room_id=target_room.id,
            transferred_at=NOW - timedelta(minutes=30),
            reason="不得通过回填时间绕过到期限制",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_source=_allow,
        authorize_target=_allow,
    )
```

- [ ] **Step 2: Write the failing direct-API and action regression**

Seed an expired fixed-term checked-in stay directly, request room detail with a fixed request ID, and assert `allowed_actions.transfer is False`. Then post the same backdated transfer through `/api/v1/housing/stays/{stay_id}/transfer`; assert `409 housing_invalid_stay_transition`, private cache headers, exactly one failed audit, and unchanged source/destination/transfer counts.

- [ ] **Step 3: Run RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/db/test_housing_transfer_checkout.py \
  apps/api/tests/api/test_housing_occupancy.py \
  -k 'backdated or expired_fixed_term' -q
```

Expected: the service/API accepts the backdated transfer or the detail response incorrectly enables transfer.

- [ ] **Step 4: Add the minimum invariant and shared action boundary**

In `HousingOccupancyService.transfer`, reject a fixed-term transfer unless both the operation time and requested transfer time precede the planned checkout:

```python
if (
    source.duration_mode == "fixed_term"
    and (
        source.planned_check_out_at is None
        or occurred_at >= source.planned_check_out_at
        or input.transferred_at >= source.planned_check_out_at
    )
):
    raise HousingInvalidStayTransition(
        "housing fixed-term stay has no remaining transfer interval"
    )
```

Pass `as_of` into `_stay_actions` and enable transfer only when the stay is long-term or its fixed-term planned checkout is strictly later than `as_of`:

```python
has_remaining_transfer_interval = (
    stay.duration_mode == "long_term"
    or (
        stay.planned_check_out_at is not None
        and as_of < stay.planned_check_out_at
    )
)
transfer = manages_transfers and current_without_manual_checkout and has_remaining_transfer_interval
```

- [ ] **Step 5: Run GREEN**

Run the Step 3 command and the complete two touched test modules. Both must pass with PostgreSQL enabled.

## Task 2: Audit every sensitive detail and resident-directory read outcome

**Files:**

- Modify: `apps/api/app/housing/api_support.py`
- Modify: `apps/api/app/housing/occupancy_router.py`
- Modify: `apps/api/tests/api/test_housing_occupancy.py`

- [ ] **Step 1: Write failing allowed/denied/failed audit tests**

For `room detail` and `resident-options`, send explicit safe request IDs and assert exactly one event per request:

```python
assert event.action in {
    "housing.room.detail.read",
    "housing.resident_options.read",
}
assert event.resource_type == "housing_room"
assert event.resource_id == occupancy_room.id
assert event.event_metadata["room_id"] == str(occupancy_room.id)
```

Cover an allowed result, hidden/missing private-404 equivalence, `housing.read`-only resident-directory denial, a mapped `HousingServiceError`, a raw `DBAPIError`, and an unexpected exception. Allowed resident-directory metadata contains only `room_id` and `result_count`; denied/failed metadata adds only the public `error_code`. Assert no resident name, masked/full phone, issue description, transfer reason, or other free text appears in the event.

- [ ] **Step 2: Write the failing validation audit test**

Send an authenticated valid room ID with an invalid resident query and an explicit request ID. Assert private `422 validation_error` and exactly one failed `housing.resident_options.read` event. It may use `housing_request`/`resource_id=None` if endpoint parameter validation prevents resource registration, but its metadata must be exactly `{"error_code": "validation_error"}`.

- [ ] **Step 3: Run RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/api/test_housing_occupancy.py \
  -k 'sensitive_read_audit or resident_query_validation_audit' -q
```

Expected: zero audit events for current GET routes.

- [ ] **Step 4: Add request-scoped sensitive-read auditing**

Add a small context object in `api_support.py`:

```python
@dataclass
class SensitiveHousingReadAudit:
    db: Session
    context: AuthContext
    action: str
    resource_type: str = "housing_request"
    resource_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

Register it from a `_sensitive_read_context(action)` dependency after standard-account authentication. At the start of each valid route, set `resource_type="housing_room"`, `resource_id=room_id`, and `metadata={"room_id": str(room_id)}`; resident-options adds `result_count=len(items)` only after successful listing.

Extend `PrivateHousingRoute` so it:

```python
response = await original(request)
_audit_sensitive_read(request, outcome="allowed")
```

and, before re-raising, emits `denied` for private 403/404 `ProblemError` and `failed` for validation, mapped service errors, database errors, and unexpected errors. Reuse `_standalone_audit`, check `_mark_request_audited`, and never retry an audit that itself failed. Routes without a registered sensitive-read context retain current behavior.

- [ ] **Step 5: Run GREEN**

Run the Step 3 command, then all of `test_housing_occupancy.py` and `test_audit.py` serially against PostgreSQL.

## Task 3: Derive successful mutation audit identity from operation results

**Files:**

- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/app/housing/occupancy_router.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `apps/api/tests/api/test_housing_occupancy.py`

- [ ] **Step 1: Write failing audit vocabulary tests**

Extend the exact housing UUID allowlist test with `source_room_id` and `target_room_id`, verify canonicalization, and verify non-UUID/PII-shaped values are rejected.

```python
"source_room_id": "52000000-0000-4000-8000-000000000080",
"target_room_id": "52000000-0000-4000-8000-000000000090",
```

- [ ] **Step 2: Write failing exact success-identity assertions**

For create and backfill, assert `resource_type == "housing_stay"`, `resource_id == created stay ID`, and exact safe metadata including `stay_id`, `room_id`, and `resident_person_id`. For issue reporting, assert `resource_type == "housing_room_issue"`, `resource_id == created issue ID`, and exact `issue_id`/`room_id` metadata. For transfer, assert:

```python
assert event.resource_type == "housing_transfer"
assert event.resource_id == transfer.id
assert event.event_metadata == {
    "stay_id": str(source.id),
    "transfer_id": str(transfer.id),
    "source_room_id": str(source_room.id),
    "target_room_id": str(target_room.id),
}
```

Also keep denial/failure assertions exact and free of names, phones, issue descriptions, notes, and transfer reasons.

- [ ] **Step 3: Run RED**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/api/test_audit.py \
  apps/api/tests/api/test_housing_occupancy.py \
  -k 'housing_audit_metadata or complete_lifecycle or backfill' -q
```

Expected: new keys are rejected and successful create/backfill/report events still have `resource_id=None`; transfer metadata cannot distinguish both rooms.

- [ ] **Step 4: Add result-aware success identity**

Allow `source_room_id` and `target_room_id` as canonical UUID metadata. Extend `_execute_mutation` with an optional callback:

```python
success_audit: Callable[
    [MutationResult], tuple[str, UUID | None, dict[str, Any]]
] | None = None
```

After `operation()` and serialization, resolve the success resource type, ID, and metadata from the result inside the same transaction before `_audit`. Failures and denials continue using the precheck identity.

Use the callback for create/backfill/report and transfer. Start transfer metadata with distinct `target_room_id`; teach `_precheck_stay_mutation` to write the source room under `source_room_id` for this call, and make the allowed transfer event use the new `HousingTransfer.id` as its resource ID.

- [ ] **Step 5: Run GREEN and focused regression**

Run the Step 3 command, all Task 8 API tests, all housing database tests, Ruff on touched files, strict mypy on production files, and `git diff --check`.

## Task 4: Independent review and final serial verification

**Files:**

- Review: all changes since `1306f365a73b18861197dceb7e021d97990e65ce`

- [ ] **Step 1: Request independent specification review**

Verify the 14-route surface, private-404 equivalence, zero domain writes on failure, exact one-event auditing, no sensitive audit content, and no Task 9/Web/Docker/NAS scope.

- [ ] **Step 2: Request independent code-quality/security review**

Recheck transaction atomicity, audit failure behavior, request validation, response serialization failure, backdated transfer boundaries, source/target identity, and PostgreSQL concurrency behavior.

- [ ] **Step 3: Run fresh serial verification**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider apps/api/tests/api/test_housing_occupancy.py -q
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider \
  apps/api/tests/unit/housing \
  apps/api/tests/services/test_housing_reconciliation.py \
  apps/api/tests/services/test_housing_resident_options.py \
  apps/api/tests/cli/test_reconcile_housing_checkouts.py \
  apps/api/tests/db/test_housing_*.py \
  apps/api/tests/api/test_housing.py \
  apps/api/tests/api/test_housing_occupancy.py -q
TEST_DATABASE_URL="$TEST_DATABASE_URL" PYTHONPATH=apps/api \
  .venv/bin/pytest -p no:cacheprovider apps/api/tests/api -q
.venv/bin/ruff check apps/api/app apps/api/tests
PYTHONPATH=apps/api .venv/bin/mypy apps/api/app
git diff --check
git status --short --branch
```

Do not start Task 9 until both independent reviews have no Critical/Important findings and every fresh command exits zero.
