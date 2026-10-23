# Housing Task 7 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the Task 7 review gaps with safe CLI startup, typed and atomic transfer persistence failures, PostgreSQL concurrency evidence, cohort-consistent reconciliation, and a complete transfer-option eligibility matrix.

**Architecture:** Keep the existing service transaction and person -> UUID-ordered rooms -> active-stays lock order. Move all CLI runtime imports inside the guarded call, map transfer database failures only after `write_transaction` has rolled back, and make reconciliation join the admissions cohort lock domain before evaluating enrollment state. Add real PostgreSQL races that control the interleaving with connection events and verify both database state and session reuse.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL 16, Psycopg 3, Pytest, Ruff, mypy.

---

### Task 1: Guard CLI initialization

**Files:**

- Modify: `apps/api/app/cli/reconcile_housing_checkouts.py`
- Modify: `apps/api/tests/cli/test_reconcile_housing_checkouts.py`

- [ ] **Step 1: Add the real-process regression**

Add `test_cli_missing_configuration_is_safe_before_runtime_initialization`, launching `sys.executable -m app.cli.reconcile_housing_checkouts` with only `PYTHONPATH=apps/api`. Assert exit code `1`, empty stdout, exact stderr `housing checkout reconciliation failed: ValidationError\n`, and absence of `Traceback`, filesystem paths, Pydantic field details, and a sentinel secret.

- [ ] **Step 2: Verify RED**

Run:

```bash
TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55432/erp_housing_task7_test' \
PYTHONPATH=apps/api .venv/bin/pytest -p no:cacheprovider \
apps/api/tests/cli/test_reconcile_housing_checkouts.py::test_cli_missing_configuration_is_safe_before_runtime_initialization -q
```

Expected: FAIL because the module-level `SessionLocal` import emits a traceback before `main()`.

- [ ] **Step 3: Put runtime initialization inside the guarded call**

Use a lazy dependency loader:

```python
def _load_runtime() -> tuple[Callable[[], AbstractContextManager[Session]], type[HousingCheckoutReconciler]]:
    from app.core.config import get_settings
    get_settings()
    from app.core.database import SessionLocal
    from app.housing.reconciliation import HousingCheckoutReconciler
    return SessionLocal, HousingCheckoutReconciler

def run_once() -> int:
    session_factory, reconciler_type = _load_runtime()
    with session_factory() as db:
        return reconciler_type(db).run(utc_now())
```

Keep `changed = run_once()` inside `main()`'s `try`. Update unit tests to inject `_load_runtime`, while the subprocess test continues to exercise real startup.

- [ ] **Step 4: Verify GREEN**

Run the whole CLI test file and expect three passes with no skips.

### Task 2: Prove transfer rollback and concurrency

**Files:**

- Modify: `apps/api/app/housing/errors.py`
- Modify: `apps/api/app/housing/occupancy_service.py`
- Modify: `apps/api/tests/db/test_housing_transfer_checkout.py`

- [ ] **Step 1: Add real database flush-failure tests**

Create PostgreSQL trigger helpers that raise:

```sql
RAISE EXCEPTION 'forced destination failure'
  USING ERRCODE = '23514', CONSTRAINT = 'test_transfer_destination_flush';
```

for a destination `housing_stays` insert and:

```sql
RAISE EXCEPTION 'forced transfer failure'
  USING ERRCODE = '40001';
```

for a `housing_transfers` insert. Each service call must raise `HousingPersistenceFailure` with only the stable message `housing transfer could not be completed`. Query afterward with the same `Session` and assert the source is still checked in at version 1, there is one stay for the resident, and there are zero transfer rows.

- [ ] **Step 2: Verify RED**

Run the two new nodes. Expected: raw `IntegrityError` and `OperationalError` escape even though the transaction rolls back.

- [ ] **Step 3: Add the narrow typed persistence boundary**

Add:

```python
class HousingPersistenceFailure(HousingServiceError):
    code = "housing_persistence_failure"
```

Catch `DBAPIError` around only `transfer`; retain the person-overlap mapping for its known exclusion constraint, otherwise raise `HousingPersistenceFailure("housing transfer could not be completed") from exc`. Do not return success or suppress the original exception cause.

- [ ] **Step 4: Add controlled target-room races**

Run two committed source stays through separate sessions toward one shared target. Pause the first worker after its UUID-ordered room lock and start the second before releasing it. Parameterize a capacity-one same-sex target and a capacity-two mixed-sex target. Assert one committed transfer, one typed `housing_capacity_conflict` or `housing_gender_conflict` loser, reusable sessions, person -> rooms -> stays query order in both workers, no room over-capacity, no mixed current gender, at most one active stay per person, and no source/destination/transfer partial history.

- [ ] **Step 5: Verify GREEN**

Run the new flush and race nodes serially against the isolated PostgreSQL URL.

### Task 3: Serialize reconciliation with lifecycle and enrollment writes

**Files:**

- Modify: `apps/api/app/housing/reconciliation.py`
- Modify: `apps/api/tests/services/test_housing_reconciliation.py`

- [ ] **Step 1: Add the enrollment interleaving RED tests**

Use committed camp stays and separate sessions. In one writer, lock the shared `Cohort` row before either cancelling the only active enrollment or inserting a replacement active enrollment, then pause before commit. Start reconciliation while that lock is held. Assert cancellation commits before reconciliation marks the stay system-pending, while replacement activation commits before reconciliation leaves the stay checked in. Assert both sessions remain queryable.

- [ ] **Step 2: Verify RED**

Run both nodes. Expected: current reconciliation reads enrollment `EXISTS` state without the cohort lock, producing `0` after concurrent cancellation and `1` during concurrent activation.

- [ ] **Step 3: Lock the common cohort domain before enrollment predicates**

Inside `write_transaction`, select the distinct cohort IDs referenced by checked-in camp stays, then lock matching cohorts in UUID order:

```python
cohort_ids = select(HousingStay.cohort_id).where(
    HousingStay.status == "checked_in",
    HousingStay.stay_purpose == "camp",
    HousingStay.cohort_id.is_not(None),
).distinct()
list(db.scalars(
    select(Cohort)
    .where(Cohort.id.in_(cohort_ids))
    .order_by(Cohort.id)
    .with_for_update()
    .execution_options(populate_existing=True)
))
```

Only after those locks are acquired may the existing candidate query evaluate enrollment and cohort predicates. Preserve `FOR UPDATE SKIP LOCKED` for stays and the system-null checkout triplet.

- [ ] **Step 4: Add checkout and transfer interleaving tests**

Pause reconciliation after it locks a due stay. Start `request_checkout` and `transfer` in separate tests, release reconciliation, then assert respectively a complete manual pending triplet and a destination system-pending stay with one complete transfer history. Verify no deadlock, no actual checkout by reconciliation, and reusable sessions.

- [ ] **Step 5: Verify GREEN**

Run the full reconciliation service test file and expect all tests to pass without skips.

### Task 4: Complete option filtering evidence and verify the branch

**Files:**

- Modify: `apps/api/tests/db/test_housing_transfer_checkout.py`

- [ ] **Step 1: Expand the option matrix**

Give `_TransferAccess` independent readable and manageable room ID sets. Add targets covering `pending_verification`, `functional`, `problem`, `disabled`, inactive floor, inactive building, readable-without-manage, and manageable-without-read. Assert every target is absent while the verified, available, active-parent, dual-authorized target remains present. Keep the existing advisory capacity/gender assertions.

- [ ] **Step 2: Run focused Task 7 tests**

Run the Task 7 migration constraint node plus transfer/checkout, reconciliation, options, and CLI files with explicit `TEST_DATABASE_URL`, `PYTHONPATH=apps/api`, and `-p no:cacheprovider`.

- [ ] **Step 3: Run regression and static verification**

Run `pytest apps/api/tests -k housing`, then all `apps/api/tests`, Ruff on changed files, mypy on `apps/api/app`, and `git diff --check`. All database suites must be serial and use the isolated PostgreSQL URL.

- [ ] **Step 4: Self-review and commit**

Confirm the diff is limited to Task 7 review closure, inspect all exception text and lock order, then commit with:

```bash
git commit -m "fix: harden housing transfer reconciliation"
```
