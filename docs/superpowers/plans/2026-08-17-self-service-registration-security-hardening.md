# Self-Service Registration Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final two self-service-registration security findings by making password reset terminate all related unfinished registration credentials atomically and by preventing a failed new guardian-invitation handoff from recovering an older invitation receipt.

**Architecture:** Centralize registration identifier advisory-lock derivation so challenge issue, registration-session issue, finalization, and password-reset revocation share one deterministic lock order. Password reset expires matching unfinished applications, clears their secrets, invalidates old registration challenges, and revokes formal and registration sessions in one transaction. The Web page keeps a stripped fragment token only in a ref, distinguishes handoff failure from context failure, and retries without consulting a stale receipt.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL advisory locks, pytest, React 19, TypeScript 6, Vitest, Playwright.

---

## Working rules

- Work only in `/Users/skymoonzyj/Documents/new project3/.worktrees/self-service-registration`.
- Preserve every existing tracked and untracked change. Do not stash, reset, rebase, merge, push, or restore files.
- The shared worktree contains user-owned changes, so this plan replaces commit steps with explicit `git diff --check` and scoped status checkpoints until the user authorizes commits.
- Use `apply_patch` for edits.
- PostgreSQL tests must use a newly created database whose name ends in `_test`; delete only that database after verification.
- Run database tests serially with `PYTHONPATH="$PWD/apps/api"` and `-p no:cacheprovider`.

## File responsibility map

- `apps/api/app/registration/verification_service.py`: canonical advisory-lock key derivation plus transaction-local challenge/session invalidation.
- `apps/api/app/registration/router.py`: consume the canonical registration-application identifier lock helper.
- `apps/api/app/registration/finalization_service.py`: acquire claim locks before the application row and revalidate the pre-lock snapshot.
- `apps/api/app/auth/service.py`: collect and verify all account registration identifiers, terminate matching applications, revoke credentials, update the password, and audit atomically.
- `apps/api/tests/services/test_password_recovery.py`: deterministic semantics, multi-identifier scope, and rollback coverage.
- `apps/api/tests/services/test_registration_finalization.py`: pre-lock snapshot revalidation coverage.
- `apps/api/tests/db/test_password_reset_registration_revocation_concurrency.py`: controlled PostgreSQL interleavings.
- `apps/web/src/registration/GuardianInvitationPage.tsx`: fragment-only-in-memory retry and phase-specific receipt recovery.
- `apps/web/src/registration/GuardianInvitationPage.test.tsx`: stale receipt, retry, StrictMode, and privacy regression coverage.
- `docs/superpowers/specs/2026-08-16-self-service-registration-security-hardening-design.md`: completion status only after every gate and review passes.

### Task 0: Create and verify the isolated PostgreSQL test database

**Files:**
- Verify only: current worktree and local PostgreSQL connection

- [ ] **Step 1: Record the protected worktree baseline**

Run `git status --short --branch`, `git rev-parse --short HEAD`, and `git diff --check` before editing. Save the exact output in the execution notes; do not clean any pre-existing path.

- [ ] **Step 2: Create a run-owned database**

Derive a unique lowercase name of the form `self_service_registration_hardening_<timestamp>_test` from the already configured local PostgreSQL maintenance connection. Create that exact database with `createdb`, construct `TEST_DATABASE_URL` by changing only the database-name component, and do not print credentials. Abort if the parsed target is not PostgreSQL, is not loopback/local, or the resulting database name does not end in `_test`.

- [ ] **Step 3: Prove the safety target before any database test**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m app.cli.e2e_database_safety
```

Expected: exit 0 for the newly created database. Query `current_database()` and record only its non-secret name; it must exactly equal the run-owned `_test` name.

### Task 1: Centralize identifier locks and fix finalization lock order

**Files:**
- Modify: `apps/api/app/registration/verification_service.py:120-165`
- Modify: `apps/api/app/registration/router.py:297-304,700-725,960-985`
- Modify: `apps/api/app/registration/finalization_service.py:130-160,512-550`
- Modify: `apps/api/tests/services/test_registration_finalization.py`

- [ ] **Step 1: Write the failing finalization snapshot test**

Add a test that changes an application's digest after the non-locking snapshot but before the row lock and expects `RegistrationFinalizationUnavailable`. Use an SQLAlchemy `before_cursor_execute` listener on the finalization connection to identify the first `SELECT ... FROM registration_applications ... FOR UPDATE`. Immediately before that statement executes, update `identity_digest` from a second Session exactly once and commit it. This injection point is safe against the current implementation because the first Session has not acquired the row lock yet; observing the advisory-lock statement would hang against the current row-first implementation.

The assertion block must include:

```python
with pytest.raises(RegistrationFinalizationUnavailable):
    service.finalize(application.id, request_id="finalization-stale-snapshot")

db.expire_all()
assert db.get(RegistrationApplication, application.id).state == "ready_to_finalize"
assert db.get(RegistrationApplication, application.id).identity_digest == replacement_digest
assert db.scalar(select(func.count(UserAccount.id))) == account_count_before
assert db.scalar(select(func.count(AuditEvent.id)).where(
    AuditEvent.request_id == "finalization-stale-snapshot"
)) == 0
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_finalization.py \
  -k 'stale_snapshot' -vv
```

Expected: FAIL because current `finalize()` locks the application row before claim advisory locks and has no pre-lock snapshot revalidation.

- [ ] **Step 3: Add canonical lock helpers**

In `verification_service.py`, replace the private duplicate key algorithm with module-level helpers used by every registration path:

```python
def registration_advisory_lock_key(scope: str, digest: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{scope}\x00{digest}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )


def registration_credential_lock_keys(
    identifier_digests: set[str],
    *,
    identity_digests: set[str] | None = None,
) -> list[int]:
    keys = {
        registration_advisory_lock_key(scope, digest)
        for digest in identifier_digests
        for scope in ("identifier", "registration-application-identifier")
    }
    keys.update(
        registration_advisory_lock_key("identity", digest)
        for digest in identity_digests or set()
    )
    return sorted(keys)


def lock_registration_credential_keys(
    db: Session,
    keys: list[int],
) -> None:
    for key in keys:
        db.execute(select(func.pg_advisory_xact_lock(key)))
```

Keep `_lock_issue_limits()` on the canonical `registration_advisory_lock_key("identifier", digest)` key. In `router.py`, delete `_registration_identifier_lock_key()` and use the canonical helper for `registration-application-identifier`.

- [ ] **Step 4: Reorder finalization without trusting the pre-lock row**

Add an immutable pre-lock snapshot:

```python
@dataclass(frozen=True, slots=True)
class RegistrationClaimLockSnapshot:
    application_id: UUID
    state: str
    expires_at: datetime
    identifier_digest: str | None
    identity_digest: str | None


def _claim_lock_snapshot(self, application_id: UUID) -> RegistrationClaimLockSnapshot:
    row = self.db.execute(
        select(
            RegistrationApplication.id,
            RegistrationApplication.state,
            RegistrationApplication.expires_at,
            RegistrationApplication.verified_identifier_digest,
            RegistrationApplication.identity_digest,
        ).where(RegistrationApplication.id == application_id)
    ).one_or_none()
    if row is None:
        raise RegistrationFinalizationUnavailable(
            "registration application is unavailable"
        )
    return RegistrationClaimLockSnapshot(
        application_id=row.id,
        state=row.state,
        expires_at=row.expires_at,
        identifier_digest=row.verified_identifier_digest,
        identity_digest=row.identity_digest,
    )
```

If and only if the snapshot is `ready_to_finalize`, require both digests and acquire the identifier, registration-application-identifier, and identity keys before `SELECT ... FOR UPDATE`. For every snapshot state, lock the row and reject if its ID, state, `expires_at`, identifier digest, or identity digest differs. A non-ready snapshot that becomes ready is rejected instead of acquiring a late advisory lock. This preserves the existing idempotent `finalized` result and guardian/conflict behavior without ever taking an advisory lock after the application row. Continue the existing state machine only from the locked, revalidated row.

- [ ] **Step 5: Run finalization and registration verification tests**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_finalization.py \
  apps/api/tests/api/test_registration_finalization.py \
  apps/api/tests/api/test_registration_start.py \
  apps/api/tests/db/test_registration_claim_concurrency.py -q
```

Expected: 100% pass, with no deadlock and no database-test skip.

- [ ] **Step 6: Record a no-commit checkpoint**

Run:

```bash
git diff --check -- \
  apps/api/app/registration/verification_service.py \
  apps/api/app/registration/router.py \
  apps/api/app/registration/finalization_service.py \
  apps/api/tests/services/test_registration_finalization.py
git status --short
```

Expected: no whitespace errors; all unrelated pre-existing changes remain present.

### Task 2: Terminate all unfinished registration credentials on password reset

**Files:**
- Modify: `apps/api/app/registration/verification_service.py:315-350`
- Modify: `apps/api/app/auth/service.py:580-730`
- Modify: `apps/api/tests/services/test_password_recovery.py`

- [ ] **Step 1: Write the multi-identifier RED test**

Extend `test_password_recovery.py` with one active phone application and one active email application matching the same account's contacts. Give both applications temporary secrets, one active session, and one unconsumed `registration` challenge. Make one application an approved `ready_to_finalize` row so the test also proves review metadata is cleared before transition to `expired`. Add a finalized application for the account with a still-active legacy registration session, plus one unrelated account/application control row.

After completing reset through the phone challenge, assert:

```python
for application in (phone_application, email_application):
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None
    assert stored.state == "expired"
    assert stored.verified_identifier_kind is None
    assert stored.verified_identifier_digest is None
    assert stored.verified_identifier_ciphertext is None
    assert stored.password_hash is None
    assert stored.identity_ciphertext is None
    assert stored.identity_digest is None
    assert stored.review_decision is None
    assert stored.reviewed_by_user_id is None
    assert stored.review_reason is None
    assert stored.reviewed_at is None

for session in (phone_session, email_session):
    assert db.get(RegistrationSession, session.id).revoked_at == NOW
assert db.get(RegistrationSession, finalized_session.id).revoked_at == NOW

for challenge in (phone_registration_challenge, email_registration_challenge):
    stored = db.get(RegistrationVerificationChallenge, challenge.id)
    assert stored is not None
    assert stored.consumed_at is None
    assert stored.expires_at == NOW

assert db.get(RegistrationApplication, unrelated_application.id).state == "contact_verified"
```

- [ ] **Step 2: Run the service test and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_password_recovery.py \
  -k 'terminates_all_registration_credentials' -vv
```

Expected: FAIL because current code only revokes visible sessions and leaves applications and old registration challenges active.

- [ ] **Step 3: Add transaction-local invalidation**

Replace `revoke_account_sessions_in_transaction()` with a method that accepts the account ID, already locked unfinished application IDs, and identifier digests. The session scope must retain the existing `finalized_account_id` behavior as well as the newly expired application IDs:

```python
def invalidate_registration_credentials_in_transaction(
    self,
    *,
    account_id: UUID,
    application_ids: set[UUID],
    identifier_digests: set[str],
    invalidated_at: datetime,
) -> None:
    if identifier_digests:
        self.db.execute(
            update(RegistrationVerificationChallenge)
            .where(
                RegistrationVerificationChallenge.purpose == "registration",
                RegistrationVerificationChallenge.identifier_digest.in_(
                    identifier_digests
                ),
                RegistrationVerificationChallenge.consumed_at.is_(None),
                RegistrationVerificationChallenge.expires_at > invalidated_at,
            )
            .values(expires_at=invalidated_at)
            .execution_options(synchronize_session=False)
        )
    scoped_application_ids = select(RegistrationApplication.id).where(
        or_(
            RegistrationApplication.finalized_account_id == account_id,
            RegistrationApplication.id.in_(application_ids),
            RegistrationApplication.verified_identifier_digest.in_(
                identifier_digests
            ),
        )
    )
    self.db.execute(
        update(RegistrationSession)
        .where(
            RegistrationSession.application_id.in_(scoped_application_ids),
            RegistrationSession.revoked_at.is_(None),
        )
        .values(revoked_at=invalidated_at)
        .execution_options(synchronize_session=False)
    )
```

This method must not commit and must not change `consumed_at`.

- [ ] **Step 4: Refactor password reset around a verified identifier snapshot**

Add an immutable snapshot private to `auth/service.py`:

```python
@dataclass(frozen=True, slots=True)
class _PasswordResetRegistrationSnapshot:
    account_id: UUID
    person_id: UUID
    identifier_fingerprints: tuple[
        tuple[UUID, IdentifierKind, str, str],
        ...,
    ]
    registration_identifier_digests: frozenset[str]
```

Define `_UNFINISHED_REGISTRATION_STATES` explicitly as `draft`, `contact_verified`, `identity_completed`, `pending_guardian`, `ready_to_finalize`, and `conflict`. Build the snapshot from `UserAccount`, `Person`, and every `AccountLoginIdentifier`. Require an active account, a non-deleted Person, exactly one identifier per kind, and at least one identifier. For each kind, decrypt the corresponding Person contact, recompute both the login digest and registration digest, and store `(identifier.id, kind, login_digest, registration_digest)` sorted by identifier UUID. Require the consumed password-reset account, kind, and registration digest to be present. Any inconsistency for a non-decoy challenge raises `VerificationTargetMismatch`; only `consumed.account_id is None` returns normally as the decoy path.

In the success callback:

```python
snapshot = self._password_reset_registration_snapshot(consumed)
lock_registration_credential_keys(
    self.db,
    registration_credential_lock_keys(
        set(snapshot.registration_identifier_digests)
    ),
)
applications = list(
    self.db.scalars(
        select(RegistrationApplication)
        .where(
            RegistrationApplication.verified_identifier_digest.in_(
                snapshot.registration_identifier_digests
            ),
            RegistrationApplication.state.in_(_UNFINISHED_REGISTRATION_STATES),
        )
        .order_by(RegistrationApplication.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
)
```

Then lock exactly `Person(snapshot.person_id)`, `UserAccount(snapshot.account_id)`, and all of that account's `AccountLoginIdentifier` rows, in that order. Rebuild the full fingerprint snapshot from those locked objects and require exact dataclass equality. On mismatch raise `VerificationTargetMismatch` so the outer transaction rolls back and the API keeps its stable `verification_invalid_or_expired` response.

For every locked application, clear `review_decision`, `reviewed_by_user_id`, `review_reason`, and `reviewed_at`, set `state = "expired"`, and call `clear_registration_application_secrets(application)`. Pass the captured IDs to `invalidate_registration_credentials_in_transaction()`, revoke all active `AuthSession` rows, update the password and lock counters, then append the existing empty-metadata audit event. Do not catch or downgrade a snapshot/crypto/integrity failure inside the callback.

- [ ] **Step 5: Add rollback and decoy assertions**

Extend the existing audit-failure test to snapshot every affected application's mapped columns, challenge expiry/consumption, and session revocation before reset. After the forced audit exception, assert exact equality. Extend the decoy test to assert zero application/challenge/session changes.

- [ ] **Step 6: Run the full password recovery service file and verify GREEN**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_password_recovery.py -q
```

Expected: 100% pass.

- [ ] **Step 7: Record a no-commit checkpoint**

Run:

```bash
git diff --check -- \
  apps/api/app/auth/service.py \
  apps/api/app/registration/verification_service.py \
  apps/api/tests/services/test_password_recovery.py
git status --short
```

Expected: no whitespace errors and no unrelated file removal.

### Task 3: Prove password-reset interleavings in PostgreSQL

**Files:**
- Create: `apps/api/tests/db/test_password_reset_registration_revocation_concurrency.py`
- Modify: `apps/api/app/auth/service.py` only if the RED test exposes a real lock-order defect
- Modify: `apps/api/app/registration/finalization_service.py` only if the RED test exposes a real snapshot-order defect

- [ ] **Step 1: Write the late-session RED test**

Follow the fixture/cleanup pattern in `test_registration_claim_concurrency.py`. Create deterministic coordination with `threading.Event`, `ThreadPoolExecutor`, and two independent SQLAlchemy Sessions. Attach a scoped `before_cursor_execute` listener to the registration worker connection; when it observes the shared `registration-application-identifier` advisory key, set `registration_lock_owned` and block on `release_registration` before allowing the transaction to continue. Start reset only after `registration_lock_owned.wait(timeout=5)` succeeds, require the reset future to remain blocked on the shared key, then release registration. Every event wait and future result uses a five-second timeout; a timeout fails the test, and no `sleep()` is permitted.

Final assertions:

```python
assert outcomes == {"registration": "issued", "reset": "completed"}
assert active_registration_session_count(engine, application.id) == 0
assert application_state(engine, application.id) == "expired"
assert session_is_reusable(registration_session) is True
assert session_is_reusable(reset_session) is True
```

- [ ] **Step 2: Run the late-session test and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_password_reset_registration_revocation_concurrency.py \
  -k 'late_session' -vv
```

Expected: FAIL against the pre-fix implementation with one active late registration session.

- [ ] **Step 3: Write and run the reset-first test**

The reset worker uses the same scoped SQL listener to stop after it owns all sorted credential locks but before it updates the application. Start verification with the old registration challenge, require it to block, release reset, and assert verification returns `verification_invalid_or_expired`, the application is expired, and active session count is zero. Both worker Sessions must execute `select(1)` successfully after the expected service result without caller `rollback()`.

Run the same file with `-k 'reset_first' -vv`. Expected before the final lock implementation: FAIL because the old challenge can still create a session.

- [ ] **Step 4: Write the two-way finalization race**

Parameterize `winner` as `"finalization"` and `"reset"`. Coordinate at the canonical identifier advisory key with the same listener/events and five-second deadlines. The chosen winner owns the shared key before the other worker starts; release it only after the loser is waiting. Assert:

```python
if winner == "finalization":
    assert final_application_state == "finalized"
    assert password_reset_completed is True
else:
    assert final_application_state == "expired"
    assert finalization_result == "registration_action_unavailable"
assert no_partial_person_account_role_rows(engine)
assert both_sessions_reusable_without_caller_rollback(engine)
```

- [ ] **Step 5: Implement only the lock changes required by the RED evidence**

Keep the canonical order from the design: all numeric advisory keys sorted first, then registration applications by UUID, then Person, UserAccount, AccountLoginIdentifier, challenges/sessions/audit. Do not add retries, sleeps, a new table, or a migration.

- [ ] **Step 6: Run the complete concurrency file and adjacent suites**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_password_reset_registration_revocation_concurrency.py \
  apps/api/tests/db/test_registration_verification_concurrency.py \
  apps/api/tests/db/test_registration_claim_concurrency.py \
  apps/api/tests/services/test_password_recovery.py \
  apps/api/tests/services/test_registration_finalization.py -q
```

Expected: 100% pass, no `40P01`, no skip, and no caller rollback in the reuse assertions.

- [ ] **Step 7: Record a no-commit checkpoint**

Run `git diff --check` and `git status --short`. Preserve the temporary database until Task 5, because the complete API gate still needs it.

### Task 4: Make guardian handoff retry flow-specific

**Files:**
- Modify: `apps/web/src/registration/GuardianInvitationPage.test.tsx`
- Modify: `apps/web/src/registration/GuardianInvitationPage.tsx`

- [ ] **Step 1: Write the stale-receipt RED test**

Add this behavior to `GuardianInvitationPage.test.tsx`:

```tsx
it("never recovers an older receipt when a new handoff fails and retries the same token", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  const token = "new-invitation-token-kept-only-in-memory-0001"
  window.history.replaceState({}, "", `/guardian-invitation#${token}`)
  api.handoffGuardianInvitation
    .mockRejectedValueOnce(new TypeError("handoff request failed"))
    .mockResolvedValueOnce(undefined)
  api.getGuardianInvitationContext.mockResolvedValueOnce({
    masked_identifier: "139****0002",
    relationship: "父亲",
    is_primary: false,
    expires_at: "2026-08-24T10:00:00+08:00",
    consent_version: "2026-08",
    request_id: "new-invitation-context",
  })

  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("可以重试")
  expect(api.getGuardianInvitationAcceptance).not.toHaveBeenCalled()
  expect(screen.queryByText("监护关系已建立")).not.toBeInTheDocument()
  expect(window.location.hash).toBe("")
  expect(document.body.textContent).not.toContain(token)

  await user.click(screen.getByRole("button", { name: "重试确认邀请" }))

  expect(api.handoffGuardianInvitation).toHaveBeenNthCalledWith(2, token)
  expect(await screen.findByText("受邀联系方式：139****0002")).toBeVisible()
  expect(api.getGuardianInvitationAcceptance).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run the component test and verify RED**

Run:

```bash
npm --prefix apps/web test -- GuardianInvitationPage.test.tsx
```

Expected: FAIL because the current catch path reads the old acceptance receipt and there is no retry button.

- [ ] **Step 3: Add an in-memory handoff failure type and retryable loader**

In `GuardianInvitationPage.tsx`, add:

```tsx
class GuardianInvitationHandoffFailed extends Error {
  constructor() {
    super("guardian invitation handoff failed")
    this.name = "GuardianInvitationHandoffFailed"
  }
}

const pendingHandoffToken = useRef<string | null>(null)
const [retryVersion, setRetryVersion] = useState(0)
const [handoffRetryable, setHandoffRetryable] = useState(false)
```

On the first effect only, strip the fragment and put it in `pendingHandoffToken.current`. Build the shared promise as:

```tsx
const token = pendingHandoffToken.current
invitationLoad.current = (async () => {
  if (token) {
    try {
      await handoffGuardianInvitation(token)
    } catch {
      throw new GuardianInvitationHandoffFailed()
    }
    pendingHandoffToken.current = null
  }
  return getGuardianInvitationContext()
})()
```

In `catch`, handle `GuardianInvitationHandoffFailed` first: keep the token ref, set `handoffRetryable`, show the generic retry message, and do not call `recoverAcceptance()`. While `handoffRetryable` is true, the effect must return without starting a request, so an authentication-status rerender cannot silently retry. For context failure after successful handoff, or ordinary no-fragment refresh, preserve the current receipt recovery.

Render a button only when `handoffRetryable` is true:

```tsx
<button
  className="primaryButton registrationSubmit"
  type="button"
  onClick={() => {
    setHandoffRetryable(false)
    setError(null)
    setLoading(true)
    setRetryVersion((value) => value + 1)
  }}
>
  重试确认邀请
</button>
```

Include `retryVersion`, `handoffRetryable`, and the existing dependencies in the loading effect. Never place the token in state, text, storage, cache keys, error objects, or error messages.

- [ ] **Step 4: Add context-loss and refresh regression tests**

Add one test where handoff succeeds but context fails and the authenticated receipt succeeds; it must show accepted. Keep the existing no-fragment refresh test and StrictMode single-handoff test passing. In the failed-handoff test, rerender once with a changed authentication status and assert the handoff call count remains one until the retry button is clicked. Add assertions that `localStorage`, `sessionStorage`, `document.body.textContent`, and `window.location.hash` contain no token after both failure and retry.

- [ ] **Step 5: Run component and registration Web tests**

Run:

```bash
npm --prefix apps/web test -- \
  GuardianInvitationPage.test.tsx \
  api.test.ts \
  router.test.tsx
npm --prefix apps/web run lint
```

Expected: all selected tests and ESLint pass.

- [ ] **Step 6: Record a no-commit checkpoint**

Run:

```bash
git diff --check -- \
  apps/web/src/registration/GuardianInvitationPage.tsx \
  apps/web/src/registration/GuardianInvitationPage.test.tsx
git status --short
```

Expected: only intended additions on top of existing user changes.

### Task 4A: Replace the global review table lock with candidate-membership locks

**Files:**
- Create: `apps/api/app/identity/membership_locks.py`
- Modify: `apps/api/app/identity/service.py:166-213,602-684`
- Modify: `apps/api/app/auth/service.py:982-1088,1268-1375`
- Modify: `apps/api/app/registration/review_service.py:502-604`
- Modify: `apps/api/app/registration/finalization_service.py:245-330,666-850`
- Modify: `apps/api/tests/db/test_registration_claim_concurrency.py`
- Create: `apps/api/tests/services/test_person_membership_locks.py`
- Modify: `docs/superpowers/specs/2026-08-16-self-service-registration-security-hardening-design.md` only after all gates and both reviews pass

- [ ] **Step 1: Preserve the performance regression as a RED test**

Run the existing PostgreSQL regression before changing production code:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_registration_claim_concurrency.py \
  -k 'does_not_block_unrelated_person_update' -vv
```

Expected: FAIL with `TimeoutError` while `RegistrationReviewService.reject()` is paused after acquiring `LOCK TABLE people IN EXCLUSIVE MODE` and an unrelated `IdentityService.update_person()` cannot finish. Keep this exact test, but move its pause point to the candidate membership key after the implementation so it proves unrelated updates complete while the candidate predicate is fenced.

Add four deterministic `threading.Event` / `ThreadPoolExecutor` PostgreSQL tests, all with five-second waits and no sleep:

1. A contact update that owns the target membership key first causes reject to return the private `RegistrationReviewNotFound`, leaves application/session/audit snapshots byte-for-byte unchanged, and leaves both Sessions usable with `select(1)`.
2. Reject that owns the target membership key first completes successfully; a late matching contact update waits and then completes after the reject commits.
3. A new generic `IdentityService.create_student()` that owns the target phone/email membership key first is discovered by reject after it commits, with the same zero registration-write assertions.
4. Reject holding a target key does not block an update whose old and new identity/phone/email membership sets are disjoint.

Parameterize the contact branch over `phone` and `email`. When testing a generic writer, pause only after the SQL statement containing `pg_advisory_xact_lock` for the expected membership key is issued; do not pause after `UPDATE people`, because that does not prove the predicate fence.

- [ ] **Step 2: Write a RED unit test for domain-isolated, sorted membership keys**

Create `apps/api/tests/services/test_person_membership_locks.py` before its implementation. It must pass duplicated values in a different order and assert deterministic numeric ordering, de-duplication, and domain separation:

```python
def test_person_membership_lock_keys_are_sorted_deduplicated_and_domain_isolated() -> None:
    keys = person_membership_lock_keys(
        identity_digests={"identity-a", "identity-a"},
        phone_digests={"contact-a", "contact-b"},
        email_digests={"contact-a"},
    )

    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))
    assert person_membership_advisory_lock_key("phone", "contact-a") in keys
    assert person_membership_advisory_lock_key("email", "contact-a") in keys
    assert (
        person_membership_advisory_lock_key("phone", "contact-a")
        != person_membership_advisory_lock_key("email", "contact-a")
    )
```

Run it before implementation. Expected: import failure because the helper does not yet exist.

- [ ] **Step 3: Implement one shared, transaction-local membership-lock helper**

Create `apps/api/app/identity/membership_locks.py` with no registration-service imports:

```python
from collections.abc import Iterable
import hashlib
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

PersonMembershipScope = Literal["identity", "phone", "email"]


def person_membership_advisory_lock_key(
    scope: PersonMembershipScope,
    digest: str,
) -> int:
    return int.from_bytes(
        hashlib.sha256(
            f"person-membership:{scope}\\x00{digest}".encode()
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )


def person_membership_lock_keys(
    *,
    identity_digests: Iterable[str] = (),
    phone_digests: Iterable[str] = (),
    email_digests: Iterable[str] = (),
) -> list[int]:
    return sorted(
        {
            *(
                person_membership_advisory_lock_key("identity", digest)
                for digest in identity_digests
                if digest
            ),
            *(
                person_membership_advisory_lock_key("phone", digest)
                for digest in phone_digests
                if digest
            ),
            *(
                person_membership_advisory_lock_key("email", digest)
                for digest in email_digests
                if digest
            ),
        }
    )


def lock_person_memberships(db: Session, keys: Iterable[int]) -> None:
    for key in sorted(set(keys)):
        db.execute(select(func.pg_advisory_xact_lock(key)))
```

The hash input deliberately separates field domains. A collision only serializes unrelated writers. The helper must not commit, flush, query `Person`, or log a digest.

Run the new unit test and the existing `test_people.py` target. Expected: green.

- [ ] **Step 4: Put every supported `Person` digest writer behind the same protocol**

For every existing-Person path, lock all current `Person` rows first, calculate the normalized destination values without further row locks, acquire one sorted membership key set for old union new non-null digests, then perform the availability checks and assignments. No path may request a new `Person FOR UPDATE` after membership keys are held.

1. In `IdentityService._new_person()`, calculate all three destination digests first, acquire their membership keys before `_ensure_identity_available()` and `INSERT people`, then flush. This covers student, guardian, staff, and visitor creation through their shared helper.
2. In `IdentityService.update_person()`, retain `GuardianReadinessPolicy.lock_students()` as the UUID-sorted Person-row barrier. Calculate current and new identity/phone/email digests, acquire the union key set before `_ensure_identity_available()` and assignments.
3. In `AuthService.verify_identifier_addition()`, retain the account-identifier advisory lock, then the existing Person lock; before locking account/login-identifier rows or changing contacts, acquire old plus destination phone/email membership keys.
4. In `AccountService.create_account()`, keep its Person lock first; after contacts are normalized and before readiness/account mutation, acquire old plus contacts-to-fill membership keys. It must not acquire any extra Person row after this point.

Do not change CLI/E2E fixture writers in this task. They are not production service entry points; document that they must not run against live concurrent production data unless they later use the same helper.

- [ ] **Step 5: Fence reject and finalization at the candidate predicate, without a table lock**

In `RegistrationReviewService.reject()` retain the existing registration credential advisory locks and application `FOR UPDATE`. Before the membership lock, read candidate IDs and take the currently discovered candidate `Person` rows with `FOR UPDATE` in UUID order. Then derive the application identity key plus exactly one verified contact key (phone or email), acquire those membership keys, perform a non-locking candidate-ID requery, re-run `candidate_allowed`, and continue only if the exact set is unchanged. Delete `LOCK TABLE people IN EXCLUSIVE MODE` completely. The requery must occur before clearing application secrets, revoking sessions, or appending audit.

In `RegistrationFinalizationService._finalize_once()` for a `ready_to_finalize` application, after the application row is locked and before any Person/account/role/audit mutation:

1. calculate the current candidate ID set and include `reviewed_candidate_person_id` if present;
2. lock those existing Person rows in UUID order;
3. acquire the application identity and verified-contact membership keys;
4. run a non-locking candidate-ID requery;
5. if its current-candidate subset changed, transition through the existing `concurrent_claim` conflict outcome and return `RegistrationConflictPendingReview` with no formal Person/account/role/audit writes;
6. classify only from the already locked candidate rows (a repeat `FOR UPDATE` may address only exactly those already locked IDs, never a newly discovered ID).

For a new-person claim, there is no existing candidate row: membership keys precede the `INSERT people`. For a claim-existing path, `_claim_person()` must address a row already locked in step 2. Preserve the pinned-review behavior: a pinned but now nonmatching candidate is locked before the membership key and remains a `reviewed_candidate_changed` conflict, not a new-person fallback.

- [ ] **Step 6: Add finalization-drift and session-reuse coverage, then run green gates**

Add a controlled PostgreSQL finalization test where a generic contact writer owns the application contact membership key before it commits. Start finalization only after that lock is held; it must wait, requery candidates, produce the existing pending-review conflict, leave formal `Person`, `UserAccount`, `UserRole`, and `registration.finalize` audit counts at their baseline, and leave both Sessions usable with `select(1)`.

Run focused green gates:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_person_membership_locks.py \
  apps/api/tests/services/test_people.py \
  apps/api/tests/services/test_accounts.py \
  apps/api/tests/services/test_registration_finalization.py \
  apps/api/tests/db/test_registration_claim_concurrency.py -q
```

Expected: all pass with no `40P01`, no timeout, and no PostgreSQL skip. Then run `git diff --check` on every file listed in this task. Do not mark the design complete until Task 5/6 gates and two independent reviews pass.

### Task 5: Run complete local gates and clean test resources

**Files:**
- Modify only if a gate proves a defect in this plan's scope
- Verify: entire worktree

- [ ] **Step 1: Run focused API and Web gates**

Run the Task 3 command, then:

```bash
PYTHONPATH="$PWD/apps/api" ../../.venv/bin/ruff check --no-cache apps/api
PYTHONPATH="$PWD/apps/api" ../../.venv/bin/mypy --no-incremental apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: Ruff passes, mypy reports zero issues, 102 Web files/1063-or-more tests pass, ESLint exits 0, and the production build exits 0. A large-chunk warning is non-blocking.

- [ ] **Step 2: Run the full API/PostgreSQL suite**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest apps/api -p no:cacheprovider
```

Expected: 3725-or-more tests pass, zero failures, and no database/concurrency skip.

- [ ] **Step 3: Run the official four-viewport registration E2E**

Set the documented local-only secrets and trusted Chrome path, then run:

```bash
E2E_PYTHON_BIN='/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
bash scripts/run-self-service-registration-e2e.sh
```

Expected: main and privacy journeys pass at desktop-1440, desktop-1024, tablet-768, and mobile-360, for 8/8 total.

- [ ] **Step 4: Run final Git integrity checks**

Run:

```bash
git diff --check
git diff --exit-code HEAD -- \
  apps/api/migrations/versions/0017_self_service_registration.py
git status --short
git log --oneline --decorate -20
```

Expected: no whitespace errors, migration 0017 remains byte-for-byte unchanged, and all expected uncommitted work remains visible.

- [ ] **Step 5: Delete only the test database created for this plan**

Stop only processes started by this plan, confirm no remaining connection uses the Task 0 database, then use `dropdb` with that exact run-owned `_test` name. Query `pg_database` afterward and require count 0. Do not touch development, E2E, shared test, or production databases.

### Task 6: Independent review and completion status

**Files:**
- Modify: `docs/superpowers/specs/2026-08-16-self-service-registration-security-hardening-design.md`
- Modify: `README.md` only if completion wording is currently inaccurate

- [ ] **Step 1: Request the original quality/security reviewer**

Ask the reviewer to verify both prior Important findings against the current dirty tree. The review is read-only: no edits, stage, commit, push, stash, rebase, merge, or reset. Require explicit Critical/Important/Minor counts and `Ready to merge: Yes/No`.

- [ ] **Step 2: Request an independent specification review**

Provide the confirmed design, this plan, base SHA, current HEAD, and current dirty-tree scope. Require line-by-line coverage of credential termination, lock ordering, stale-receipt prevention, privacy, rollback, and both concurrency directions.

- [ ] **Step 3: Fix every Critical or Important using a new RED/GREEN cycle**

For each valid finding, add a focused failing test, record the expected failure, make the smallest scoped implementation, and rerun the focused plus adjacent suites. Do not bundle unrelated refactors.

- [ ] **Step 4: Mark the design complete only after both reviews pass**

Change the design status to:

```markdown
- **状态：** 本地实现、完整门禁与独立复审已完成
```

Do not claim main-branch integration, Docker/NAS, payment, real messaging, or production readiness.

- [ ] **Step 5: Present the no-commit integration checkpoint**

Report exact changed files, RED/GREEN evidence, full gate counts, E2E 8/8, both review verdicts, branch name, worktree path, and remaining main-worktree conflicts. Wait for explicit user authorization before any commit or merge.
