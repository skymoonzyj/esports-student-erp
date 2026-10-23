# Self-Service Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Docker-free local self-service registration system with phone/email verification, safe identity claiming, minor guardian completion, staff identity applications, password recovery, and responsive administration without weakening existing ERP authorization.

**Architecture:** Public registration uses separate application and session tables; it never creates a formal ERP session until identity and guardian readiness are satisfied. A canonical login-identifier table supports verified phone or email login, while existing identity, guardian, RBAC, audit, food-onboarding, admissions, and housing services remain the business sources of truth.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL 16, Argon2, AES-GCM envelope encryption, React 19, TypeScript, TanStack Query, React Router, Vitest, Playwright, Ruff, mypy

---

## Preconditions and execution boundary

- The implementation base must contain committed migration `0016_camp_evaluation_surveys` and its focused gates. The current planning worktree contains uncommitted camp-survey files; do not start `0017` from that dirty state.
- At execution time invoke `superpowers:using-git-worktrees` and create `codex/self-service-registration` from the reviewed commit containing `0016`. Do not copy uncommitted files between worktrees.
- Use an isolated PostgreSQL database whose name is `erp_e2e` or ends in `_test`. Do not run database or concurrency tests against development data.
- Keep Docker, Compose, NAS, real SMS/email providers, payment providers, police APIs, GPU, video, and talent assessment out of this plan.
- Every behavior change follows RED -> verify RED -> GREEN -> verify GREEN -> commit. A test that passes before production code is added must be corrected before continuing.
- Use `PYTHONPATH="$PWD/apps/api"` for API tests so imports resolve to the execution worktree, not another checkout.

## File map

### Backend persistence and authentication

- Create `apps/api/app/registration/models.py`: registration applications, verification challenges, registration sessions, invitations, staff applications, and consent acceptances.
- Create `apps/api/app/registration/identifiers.py`: phone/email normalization and domain-separated lookup digests.
- Create `apps/api/app/registration/delivery.py`: SMS/email sender protocols and safe local test sender.
- Create `apps/api/app/registration/verification_service.py`: issue, rate-limit, consume, and expire verification codes.
- Create `apps/api/app/registration/session_service.py`: registration-only token and CSRF lifecycle.
- Create `apps/api/app/registration/matching_service.py`: deterministic existing-person match and conflict classification.
- Create `apps/api/app/registration/finalization_service.py`: atomic person claim/create and formal-account opening.
- Create `apps/api/app/registration/review_service.py`: masked conflict queue, sensitive detail, resolution, and rejection.
- Create `apps/api/app/registration/guardian_service.py`: guardian invitation and confirmation workflow.
- Create `apps/api/app/registration/staff_service.py`: staff application submission and review.
- Create `apps/api/app/registration/cleanup_service.py`: bounded expiry and sensitive-payload clearing.
- Create `apps/api/app/registration/schemas.py`: strict public and administrative request/response contracts.
- Create `apps/api/app/registration/dependencies.py`: service wiring and isolated registration cookie/CSRF dependencies.
- Create `apps/api/app/registration/router.py`: public registration and invitation endpoints.
- Create `apps/api/app/registration/admin_router.py`: conflict and staff-review endpoints.
- Create `apps/api/app/auth/identifiers.py`: canonical account-identifier lookup and verified-phone policy.
- Modify `apps/api/app/auth/models.py`, `schemas.py`, `service.py`, `router.py`, and `dependencies.py`: generic identifier login, secondary identifiers, password recovery, and optional email output.
- Modify `apps/api/app/identity/models.py`, `schemas.py`, `service.py`, and `normalization.py`: encrypted email fields and explicit guardian profile.
- Modify `apps/api/app/portal/context_service.py` and `apps/api/app/food_preferences/reporting.py`: explicit guardian type and self-registration food-onboarding semantics.
- Modify `apps/api/app/core/config.py`: independent registration session and verification peppers.
- Modify `apps/api/app/rbac/seed.py`: `registered_user` role and registration review permissions.
- Modify `apps/api/app/api/router.py` and `apps/api/migrations/env.py`: register modules and metadata.
- Create `apps/api/migrations/versions/0017_self_service_registration.py`: schema, backfills, protected downgrade, and seed data.

### Cross-module readiness

- Modify `apps/api/app/admissions/case_service.py`: verified-phone enrollment gate.
- Modify `apps/api/app/admissions/payment_service.py`: verified-phone manual payment gate.
- Modify `apps/api/app/housing/occupancy_service.py`: verified-phone resident selection and mutation gate.
- Modify `apps/api/app/housing/reporting_projection.py`: retain explicit missing-phone reporting outcome.

### Web application

- Create `apps/web/src/registration/api.ts`: runtime-validated registration and administration API client.
- Create `apps/web/src/registration/keys.ts`: registration query-key factories.
- Create `apps/web/src/registration/RegistrationStartPage.tsx`: purpose and contact selection.
- Create `apps/web/src/registration/RegistrationVerifyPage.tsx`: verification code step.
- Create `apps/web/src/registration/RegistrationProfilePage.tsx`: password, identity, purpose fields, and consent.
- Create `apps/web/src/registration/RegistrationGuardianPage.tsx`: minor completion and invitation management.
- Create `apps/web/src/registration/RegistrationStatusPage.tsx`: finalized, conflict, and staff status.
- Create `apps/web/src/registration/ForgotPasswordPage.tsx`: phone/email password recovery.
- Create `apps/web/src/registration/RegistrationAdminPage.tsx`: registration conflict queue.
- Create `apps/web/src/registration/StaffApplicationsAdminPage.tsx`: staff identity review.
- Create `apps/web/src/registration/registration.css`: four-viewport layouts and safe form states.
- Modify `apps/web/src/auth/AuthProvider.tsx`, `LoginPage.tsx`, and `LoginPage.css`: generic identifier login and public links.
- Create `apps/web/src/auth/AccountIdentifiersPanel.tsx`: list verified login methods and verify a secondary phone/email.
- Modify `apps/web/src/foodPreferences/MyFoodProfilePage.tsx`: expose account contact management from the existing personal-profile route.
- Modify `apps/web/src/app/router.tsx` and `apps/web/src/layout/AppShell.tsx`: public routes and capability-only admin navigation.

### Verification and operations

- Create focused tests under `apps/api/tests/unit/registration`, `apps/api/tests/services`, `apps/api/tests/api`, and `apps/api/tests/db`.
- Create focused Web tests beside each new component and API client.
- Create `apps/api/app/cli/seed_registration_e2e.py` and `scripts/seed-registration-e2e.py`.
- Create `apps/web/tests/self-service-registration.spec.ts` and `apps/web/tests/self-service-registration-privacy.spec.ts`.
- Create `scripts/run-self-service-registration-e2e.sh` and its launcher test.
- Create `docs/operations/self-service-registration-local.md` and update `README.md` and `scripts/db-schema-manifest.sh`.

## Task 1: Freeze `0017` persistence, backfill, and downgrade contract

**Files:**

- Create: `apps/api/migrations/versions/0017_self_service_registration.py`
- Create: `apps/api/app/registration/__init__.py`
- Create: `apps/api/app/registration/models.py`
- Modify: `apps/api/app/auth/models.py`
- Modify: `apps/api/app/identity/models.py`
- Modify: `apps/api/app/portal/context_service.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Create: `apps/api/tests/db/test_registration_models.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing model and migration tests**

Add `REGISTRATION_TABLES`, set `CURRENT_HEAD = "0017_self_service_registration"`, and add these concrete assertions:

```python
REGISTRATION_TABLES = {
    "account_login_identifiers",
    "guardian_profiles",
    "registration_applications",
    "registration_verification_challenges",
    "registration_sessions",
    "guardian_registration_invitations",
    "staff_identity_applications",
    "registration_consent_acceptances",
}
EXPECTED_TABLES |= REGISTRATION_TABLES


def test_registration_models_expose_fixed_state_constraints() -> None:
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in RegistrationApplication.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ready_to_finalize" in checks["ck_registration_applications_state"]
    assert RegistrationVerificationChallenge.__table__.c.code_digest.nullable is False
    identifier_uniques = {
        tuple(column.name for column in constraint.columns)
        for constraint in AccountLoginIdentifier.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("identifier_digest",) in identifier_uniques
    assert GuardianProfile.__table__.c.person_id.primary_key is True


def test_0017_backfills_only_legacy_phone_login(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0016_camp_evaluation_surveys")
    person_id, account_id = _insert_test_account(postgres_engine)
    command.upgrade(config, "0017_self_service_registration")
    with postgres_engine.connect() as connection:
        identifiers = list(connection.execute(text(
            "SELECT kind, verification_source FROM account_login_identifiers "
            "WHERE account_id = :account_id ORDER BY kind"
        ), {"account_id": account_id}))
        assert identifiers == [("phone", "legacy_phone_login")]
        assert connection.scalar(text(
            "SELECT email_normalized FROM user_accounts WHERE id = :account_id"
        ), {"account_id": account_id}) is None
        assert connection.scalar(text(
            "SELECT email_ciphertext IS NOT NULL FROM people WHERE id = :person_id"
        ), {"person_id": person_id}) is True
        assert connection.scalar(text(
            "SELECT EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
            "WHERE ur.user_id = :account_id AND r.key = 'registered_user')"
        ), {"account_id": account_id}) is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_registration_models.py \
  apps/api/tests/db/test_migrations.py::test_0017_backfills_only_legacy_phone_login -q
```

Expected: collection fails because `registration.models`, `AccountLoginIdentifier`, `GuardianProfile`, and revision `0017` do not exist.

- [ ] **Step 3: Add the persistence models and migration**

Use fixed literals shared by models and schemas:

```python
RegistrationPurpose = Literal["student", "guardian", "staff_applicant", "visitor"]
RegistrationState = Literal[
    "draft",
    "contact_verified",
    "identity_completed",
    "pending_guardian",
    "ready_to_finalize",
    "finalized",
    "conflict",
    "rejected",
    "expired",
]
IdentifierKind = Literal["phone", "email"]
ChallengePurpose = Literal[
    "registration",
    "password_reset",
    "guardian_invitation",
    "add_login_identifier",
]
```

Implement every table and field frozen in the design. Add database checks for all literal states, nonblank staff reasons, aware expiry timestamps, attempts between 0 and 5, accepted invitation uniqueness, one open staff application per `(person_id, requested_staff_type)`, and one primary identifier per account. Use partial PostgreSQL unique indexes for active application identifiers and pending invitations.

Add nullable `UserAccount.self_service_primary_purpose` with the same four-value check. Existing accounts remain `NULL`; self-service finalization sets it once so non-registration modules can apply onboarding rules without reading registration application internals.

The migration must:

```python
revision = "0017_self_service_registration"
down_revision = "0016_camp_evaluation_surveys"

REGISTRATION_PERMISSION_KEYS = (
    "registration.review",
    "registration.sensitive.read",
    "staff_identity_applications.review",
)

REGISTERED_USER_ROLE_ID = UUID("10000000-0000-4000-8000-000000000008")
REGISTRATION_PERMISSION_IDS = {
    "registration.review": UUID("20000000-0000-4000-8000-000000000051"),
    "registration.sensitive.read": UUID("20000000-0000-4000-8000-000000000052"),
    "staff_identity_applications.review": UUID("20000000-0000-4000-8000-000000000053"),
}
```

Create tables before backfill and lock `people`, `user_accounts`, `roles`, `permissions`, and `role_permissions` in that order. A `_migration_cipher()` helper must read `FIELD_ENCRYPTION_MASTER_KEY`, validate URL-safe Base64 and exactly 32 decoded bytes, and construct `EnvelopeCipher` before the first DDL statement. Use it to encrypt migrated emails, insert legacy phone identifiers, assert one identifier per existing account, clear both legacy email plaintext columns, and then seed the fixed role and permission UUIDs above. Mark all three review permissions sensitive and grant them only to `admin:global`; grant `registered_user` only `food_preferences.read:self` and `food_preferences.update:self`. Backfill `registered_user` onto every existing personal `user_accounts` row so staff, visitors, students, and guardians share the same self-profile baseline; service accounts are a different table and receive nothing. Downgrade must refuse when any registration business row, email-only account, new identifier, new role assignment, permission override, or customized grant exists.

- [ ] **Step 4: Register metadata and update the schema manifest**

Import registration models in `migrations/env.py`. Add the eight new tables, unique indexes, new encrypted email columns, nullable legacy account columns, permissions, `registered_user` role, and migration head to `scripts/db-schema-manifest.sh`.

- [ ] **Step 5: Run focused migration tests and verify GREEN**

Run the Step 2 command plus:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_migrations.py::test_alembic_head_creates_platform_tables \
  apps/api/tests/db/test_migrations.py::test_registration_migration_round_trip_restores_schema_and_grants -q
```

Expected: all selected tests pass with no PostgreSQL skip.

- [ ] **Step 6: Commit the persistence foundation**

```bash
git add apps/api/migrations/versions/0017_self_service_registration.py \
  apps/api/app/registration apps/api/app/auth/models.py \
  apps/api/app/identity/models.py apps/api/migrations/env.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_registration_models.py \
  scripts/db-schema-manifest.sh
git commit -m "feat: add registration persistence foundation"
```

## Task 2: Encrypt email access and make guardian identity explicit

**Files:**

- Modify: `apps/api/app/identity/normalization.py`
- Modify: `apps/api/app/identity/schemas.py`
- Modify: `apps/api/app/identity/service.py`
- Modify: `apps/api/app/identity/router.py`
- Modify: `apps/api/app/portal/context_service.py`
- Modify: `apps/api/app/food_preferences/reporting.py`
- Modify: `apps/api/tests/unit/identity/test_normalization.py`
- Modify: `apps/api/tests/services/test_people.py`
- Modify: `apps/api/tests/api/test_people.py`
- Modify: `apps/api/tests/api/conftest.py`
- Modify: `apps/api/tests/services/test_portal_context.py`
- Modify: `apps/api/tests/services/test_food_preference_reporting.py`

- [ ] **Step 1: Write failing identity privacy tests**

```python
def test_normalize_email_uses_nfkc_casefold_and_strips_space() -> None:
    assert normalize_email(" User@Example.COM ") == "user@example.com"


def test_unprofiled_person_is_not_implicitly_a_guardian(identity_service, db) -> None:
    person = identity_service.create_base_person(
        PersonBaseInput(
            full_name="基础账号申请人",
            identity_number=make_cn_id("19900102", 121),
            sex="female",
            date_of_birth=date(1990, 1, 2),
            phone=None,
            email="base@example.com",
        )
    )
    assert identity_service.get_person(person.id).person_types == []


def test_guardian_profile_preserves_type_without_relationship(identity_service) -> None:
    guardian = identity_service.create_guardian(guardian_input())
    assert identity_service.get_person(guardian.id).person_types == ["guardian"]


def test_staff_applicant_requires_food_onboarding_without_staff_profile(portal_service, staff_applicant) -> None:
    context = portal_service.get_context(
        user_id=staff_applicant.account_id,
        actor_person_id=staff_applicant.person_id,
    )
    assert context.food_profile_available is True
    assert context.food_preferences_onboarding_required is True
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/unit/identity/test_normalization.py \
  apps/api/tests/services/test_people.py -q
```

Expected: failures show missing `normalize_email`, plaintext email access, and implicit guardian fallback.

- [ ] **Step 3: Implement encrypted email and explicit guardian projection**

Add:

```python
def normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return str(TypeAdapter(EmailStr).validate_python(normalized))
```

Make `_new_person` encrypt normalized email with field name `email`, store `email_digest = lookup.digest_domain("person-email", normalized)`, and never assign `Person.email`. Add a public service-only `create_base_person(PersonBaseInput)` used by registration finalization; it creates no student/staff/visitor/guardian profile. Add a `_decrypt_email` helper used only by authorized response projection. `_types_for` must use `GuardianProfile` and return an empty list for a person with no explicit profile. `create_guardian` inserts `GuardianProfile(person_id=person.id)` in the same transaction.

Update food reporting to join `GuardianProfile` and remove both relationship-only and “no profile means guardian” fallbacks. Update portal food onboarding so a self-service purpose of `staff_applicant`, `student`, or `visitor` requires onboarding even before a staff profile exists; `guardian` is excluded only while no student, staff, or visitor profile has been added. Update API fixtures to decrypt or construct email through the service, not direct plaintext columns. Keep `PersonSummary.email` optional and ensure unauthorized list projections either omit it or retain their existing permission-bound behavior.

- [ ] **Step 4: Run identity/API tests and verify GREEN**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/unit/identity/test_normalization.py \
  apps/api/tests/services/test_people.py apps/api/tests/api/test_people.py \
  apps/api/tests/api/test_guardians.py apps/api/tests/services/test_portal_context.py \
  apps/api/tests/services/test_food_preference_reporting.py -q
```

Expected: all selected tests pass and no response fixture reads `people.email` directly.

- [ ] **Step 5: Commit identity privacy changes**

```bash
git add apps/api/app/identity apps/api/tests/unit/identity \
  apps/api/tests/services/test_people.py apps/api/tests/api/test_people.py \
  apps/api/tests/api/test_guardians.py apps/api/tests/api/conftest.py \
  apps/api/app/portal/context_service.py apps/api/app/food_preferences/reporting.py \
  apps/api/tests/services/test_portal_context.py \
  apps/api/tests/services/test_food_preference_reporting.py
git commit -m "refactor: encrypt email and expose guardian profiles"
```

## Task 3: Switch formal authentication to verified phone/email identifiers

**Files:**

- Create: `apps/api/app/auth/identifiers.py`
- Modify: `apps/api/app/auth/schemas.py`
- Modify: `apps/api/app/auth/service.py`
- Modify: `apps/api/app/auth/router.py`
- Modify: `apps/api/app/auth/admin_router.py`
- Modify: `apps/api/tests/api/test_auth_sessions.py`
- Modify: `apps/api/tests/services/test_accounts.py`
- Create: `apps/api/tests/services/test_login_identifiers.py`

- [ ] **Step 1: Write failing generic-login tests**

```python
@pytest.mark.parametrize("identifier", ["19990000001", "admin@example.com"])
def test_login_accepts_each_verified_identifier(auth_service, verified_account, identifier) -> None:
    result = auth_service.login(
        identifier=identifier,
        password="Valid-password-2026",
        ip_address="127.0.0.1",
        request_id="generic-login",
    )
    assert result.context.account.id == verified_account.id


def test_unverified_legacy_email_cannot_login(auth_service) -> None:
    with pytest.raises(InvalidCredentials):
        auth_service.login(
            identifier="legacy@example.com",
            password="Valid-password-2026",
            ip_address="127.0.0.1",
            request_id="legacy-email",
        )
```

API tests must submit `{"identifier": value, "password": value}` and assert `AuthUser.email` may be `null` for phone-only accounts.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_login_identifiers.py \
  apps/api/tests/api/test_auth_sessions.py -q
```

Expected: schema rejects `identifier` and `AuthService.login` still requires `phone`.

- [ ] **Step 3: Implement canonical identifier lookup**

```python
@dataclass(frozen=True)
class NormalizedLoginIdentifier:
    kind: Literal["phone", "email"]
    value: str


def normalize_login_identifier(raw: str) -> NormalizedLoginIdentifier:
    value = raw.strip()
    if "@" in value:
        return NormalizedLoginIdentifier("email", normalize_email(value))
    return NormalizedLoginIdentifier("phone", normalize_phone(value))


def login_identifier_digest(lookup: LookupDigest, value: NormalizedLoginIdentifier) -> str:
    return lookup.digest_domain(f"account-login-{value.kind}", value.value)
```

Change `LoginRequest.phone` to `identifier`. `AuthService.login` must query `AccountLoginIdentifier`, preserve dummy Argon2 verification, rate-limit with the identifier digest, lock the matched account, retain guardian readiness enforcement, and issue the same session shape. `_auth_user` decrypts an available authorized email or returns `None`; do not return identifier digests.

Update `AccountService.create_account` so administrator-created accounts add a verified phone identifier with source `admin_created`; keep the temporary-password behavior and require a phone for this legacy administrative endpoint until its UI is separately changed. Replace the account-list assumption of a mandatory phone/email with this response shape:

```python
class AccountSummary(BaseModel):
    id: UUID
    person_id: UUID
    full_name: str
    masked_primary_identifier: str
    primary_identifier_kind: Literal["phone", "email"]
    masked_phone: str | None
    masked_email: str | None
    verified_identifier_kinds: list[Literal["phone", "email"]]
    status: AccountStatus
    force_password_change: bool
    created_at: datetime
```

Admin list/detail projection must batch-load identifiers, decrypt contact only through identity service, and never expose identifier digests.

- [ ] **Step 4: Run authentication regression tests and verify GREEN**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_login_identifiers.py \
  apps/api/tests/services/test_accounts.py \
  apps/api/tests/services/test_auth_rate_limit.py \
  apps/api/tests/api/test_auth_sessions.py apps/api/tests/api/test_accounts.py -q
```

Expected: phone and verified email login pass; unknown, disabled, locked, and unverified identifiers remain indistinguishable.

- [ ] **Step 5: Commit identifier authentication**

```bash
git add apps/api/app/auth apps/api/tests/services/test_login_identifiers.py \
  apps/api/tests/services/test_accounts.py apps/api/tests/api/test_auth_sessions.py \
  apps/api/tests/api/test_accounts.py
git commit -m "feat: authenticate verified phone and email identifiers"
```

## Task 4: Build verification delivery, rate limits, and single-use codes

**Files:**

- Create: `apps/api/app/registration/identifiers.py`
- Create: `apps/api/app/registration/delivery.py`
- Create: `apps/api/app/registration/verification_service.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `.env.example`
- Modify: `apps/api/tests/api/test_health.py`
- Modify: `apps/api/tests/conftest.py`
- Create: `apps/api/tests/unit/registration/test_identifiers.py`
- Create: `apps/api/tests/services/test_registration_verification.py`
- Create: `apps/api/tests/db/test_registration_verification_concurrency.py`

- [ ] **Step 1: Write failing verification tests**

```python
def test_code_expires_at_ten_minutes(verification_service, captured_sender, clock) -> None:
    issued = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009999",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)
    clock.advance(minutes=10)
    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume(issued.challenge_id, code)


def test_fifth_wrong_attempt_closes_challenge(verification_service, captured_sender) -> None:
    issued = verification_service.issue(
        identifier_kind="email",
        identifier="new@example.com",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)
    for _ in range(5):
        with pytest.raises(VerificationInvalidOrExpired):
            verification_service.consume(issued.challenge_id, "000000")
    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume(issued.challenge_id, code)
```

The concurrency test starts two sessions consuming one valid code and asserts exactly one success and one stable `VerificationInvalidOrExpired`.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/unit/registration/test_identifiers.py \
  apps/api/tests/services/test_registration_verification.py \
  apps/api/tests/db/test_registration_verification_concurrency.py -q
```

Expected: imports fail because verification services do not exist.

- [ ] **Step 3: Implement safe senders and code lifecycle**

Define:

```python
class VerificationSender(Protocol):
    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class CapturedVerification:
    challenge_id: UUID
    destination: str
    code: str
    purpose: ChallengePurpose


@dataclass
class CapturingVerificationSender:
    messages: list[CapturedVerification]

    def send_code(
        self,
        *,
        challenge_id: UUID,
        destination: str,
        code: str,
        purpose: ChallengePurpose,
    ) -> None:
        self.messages.append(CapturedVerification(challenge_id, destination, code, purpose))

    def code_for(self, challenge_id: UUID) -> str:
        return next(item.code for item in reversed(self.messages) if item.challenge_id == challenge_id)
```

Production settings must reject `CapturingVerificationSender`. Add independent 32-byte Base64 settings `registration_verification_pepper` and `registration_session_token_pepper`; validate that those two and `session_token_pepper` are pairwise distinct. Generate six-digit codes with `secrets.randbelow`, store only an HMAC digest, set a 10-minute expiry, enforce 60-second resend spacing, maximum five attempts, at most five sends per identifier in 24 hours, and at most 20 sends per request-source digest in one hour. Expose the captured code only through the injected test sender object, never through the service result or HTTP response.

The two new settings may be `None` in development/test so unrelated existing E2E launchers keep working; registration dependencies return a stable 503 `registration_unavailable` until both are configured. Production `validate_runtime_secrets()` requires both. Add `test_health.py` cases for missing production secrets, one missing member, invalid Base64, non-32-byte values, equality with each other, and equality with the formal session pepper. The registration test fixture supplies two distinct values.

- [ ] **Step 4: Run verification tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass, including the PostgreSQL race with no skip.

- [ ] **Step 5: Commit verification foundation**

```bash
git add apps/api/app/registration apps/api/app/core/config.py .env.example \
  apps/api/tests/api/test_health.py apps/api/tests/conftest.py \
  apps/api/tests/unit/registration apps/api/tests/services/test_registration_verification.py \
  apps/api/tests/db/test_registration_verification_concurrency.py
git commit -m "feat: add registration verification challenges"
```

## Task 5: Add isolated registration applications and sessions

**Files:**

- Create: `apps/api/app/registration/session_service.py`
- Create: `apps/api/app/registration/schemas.py`
- Create: `apps/api/app/registration/dependencies.py`
- Create: `apps/api/app/registration/router.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/services/test_registration_sessions.py`
- Create: `apps/api/tests/api/test_registration_start.py`

- [ ] **Step 1: Write failing application/session tests**

```python
def test_verified_code_creates_registration_session_only(db_client, captured_sender) -> None:
    application_id = start_registration(db_client, purpose="student", email="new@example.com")
    challenge_id = send_registration_code(db_client, application_id)
    response = db_client.post(
        f"/api/v1/registration/applications/{application_id}/verify",
        json={"challenge_id": str(challenge_id), "code": captured_sender.code_for(challenge_id)},
    )
    assert response.status_code == 200
    assert response.cookies.get("erp_registration_session")
    assert response.cookies.get("erp_session") is None
    assert response.json()["state"] == "contact_verified"


def test_registration_cookie_cannot_authenticate_formal_api(db_client, registration_cookie) -> None:
    response = db_client.get("/api/v1/auth/me", cookies=registration_cookie)
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_sessions.py \
  apps/api/tests/api/test_registration_start.py -q
```

Expected: 404 for registration routes and missing registration cookie service.

- [ ] **Step 3: Implement application start, verification, and isolated CSRF**

Freeze these public routes:

```text
POST /api/v1/registration/applications
POST /api/v1/registration/applications/{application_id}/challenge
POST /api/v1/registration/applications/{application_id}/verify
GET  /api/v1/registration/me
PATCH /api/v1/registration/me
```

`POST applications` accepts exactly one contact kind/value and a primary purpose. Challenge send returns only `challenge_id`, `expires_at`, `retry_after_seconds`, and a masked destination. Verify consumes the code, transitions `draft -> contact_verified`, sets `erp_registration_session` with path `/api/v1/registration`, and returns an in-memory registration CSRF token. All later unsafe registration calls require `X-Registration-CSRF-Token`.

Use these dependency names consistently:

```python
REGISTRATION_COOKIE_NAME = "erp_registration_session"
REGISTRATION_COOKIE_PATH = "/api/v1/registration"


def require_registration_session(
    request: Request,
    service: Annotated[RegistrationSessionService, Depends(get_registration_session_service)],
) -> RegistrationContext:
    cookie_token = request.cookies.get(REGISTRATION_COOKIE_NAME)
    context = service.authenticate(cookie_token)
    if request.method not in SAFE_METHODS:
        service.verify_csrf(context, request.headers.get("X-Registration-CSRF-Token"))
    return context
```

- [ ] **Step 4: Run application/session tests and verify GREEN**

Run the Step 2 command. Expected: all selected tests pass; formal and registration cookies are not interchangeable.

- [ ] **Step 5: Commit isolated registration sessions**

```bash
git add apps/api/app/registration apps/api/app/api/router.py \
  apps/api/tests/services/test_registration_sessions.py \
  apps/api/tests/api/test_registration_start.py
git commit -m "feat: open isolated registration sessions"
```

## Task 6: Implement identity matching and atomic formal-account finalization

**Files:**

- Create: `apps/api/app/registration/matching_service.py`
- Create: `apps/api/app/registration/finalization_service.py`
- Modify: `apps/api/app/registration/schemas.py`
- Modify: `apps/api/app/registration/router.py`
- Modify: `apps/api/app/rbac/seed.py`
- Create: `apps/api/tests/services/test_registration_matching.py`
- Create: `apps/api/tests/services/test_registration_finalization.py`
- Create: `apps/api/tests/db/test_registration_claim_concurrency.py`
- Create: `apps/api/tests/api/test_registration_finalization.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`

- [ ] **Step 1: Write failing match/finalization tests**

```python
def test_exact_unowned_person_is_claimed_without_duplicate(finalization_service, existing_person) -> None:
    result = finalization_service.finalize(complete_adult_student_application(existing_person))
    assert result.person_id == existing_person.id
    assert result.account_id is not None
    assert count_people_with_identity(existing_person.identity_digest) == 1


def test_cross_person_identifier_match_enters_conflict(finalization_service, split_match_application) -> None:
    with pytest.raises(RegistrationConflictPendingReview):
        finalization_service.finalize(split_match_application.id)
    assert load_application(split_match_application.id).state == "conflict"
    assert formal_account_count(split_match_application.id) == 0


def test_adult_student_finalization_is_atomic(finalization_service, complete_application) -> None:
    result = finalization_service.finalize(complete_application.id)
    assert assigned_role_keys(result.account_id) == {"registered_user", "student"}
    assert load_student(result.person_id).status == "draft"
    assert load_application(complete_application.id).password_hash is None
```

Concurrency coverage must race two complete applications with one identity digest and assert one finalized account, one conflict, one person, and usable sessions afterward.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_matching.py \
  apps/api/tests/services/test_registration_finalization.py \
  apps/api/tests/db/test_registration_claim_concurrency.py \
  apps/api/tests/api/test_registration_finalization.py -q
```

Expected: matching and finalization services are missing.

- [ ] **Step 3: Implement deterministic match classification**

Return one of these exact values:

```python
MatchKind = Literal[
    "new_person",
    "claim_existing",
    "existing_account",
    "conflict",
]


@dataclass(frozen=True)
class RegistrationMatch:
    kind: MatchKind
    person_id: UUID | None
    conflict_code: str | None
```

Lock candidates in `Person.id` order. `claim_existing` requires one nondeleted identity match, exact normalized name/birth/sex, compatible purpose, no formal account, and same verified contact or an empty contact not used elsewhere. Never auto-claim deleted people or combine identifiers from different people.

- [ ] **Step 4: Implement atomic finalization**

Create `registered_user` with only `food_preferences.read:self` and `food_preferences.update:self`. Finalization assigns:

```python
PURPOSE_ROLES = {
    "student": ("registered_user", "student"),
    "guardian": ("registered_user", "guardian"),
    "staff_applicant": ("registered_user",),
    "visitor": ("registered_user",),
}
```

Generate student numbers from a PostgreSQL sequence as `REG-{year:04d}{sequence:08d}`. Create `GuardianProfile` for guardians, `VisitorProfile` with a required visit reason, and no `StaffProfile` for staff applicants. Set `UserAccount.self_service_primary_purpose`, move the Argon2 hash into `UserAccount`, insert the verified login identifier, persist consent, clear application secrets, revoke registration sessions, and append one allowed audit event in the same transaction.

Minor students transition to `pending_guardian` and return `guardian_confirmation_required`; they must not reach this finalization branch until Task 8 confirms a guardian.

- [ ] **Step 5: Run matching/finalization tests and verify GREEN**

Run the Step 2 command plus:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_rbac_seed.py apps/api/tests/services/test_accounts.py -q
```

Expected: all pass with one person/account under races and no password/contact plaintext in application rows.

- [ ] **Step 6: Commit account finalization**

```bash
git add apps/api/app/registration apps/api/app/rbac/seed.py \
  apps/api/tests/services/test_registration_matching.py \
  apps/api/tests/services/test_registration_finalization.py \
  apps/api/tests/db/test_registration_claim_concurrency.py \
  apps/api/tests/api/test_registration_finalization.py \
  apps/api/tests/db/test_rbac_seed.py
git commit -m "feat: finalize safe self-service accounts"
```

## Task 7: Complete adult, guardian, visitor, and conflict public APIs

**Files:**

- Modify: `apps/api/app/registration/schemas.py`
- Modify: `apps/api/app/registration/router.py`
- Create: `apps/api/app/registration/review_service.py`
- Create: `apps/api/app/registration/admin_router.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/api/test_registration_profiles.py`
- Create: `apps/api/tests/api/test_registration_privacy.py`
- Create: `apps/api/tests/api/test_registration_admin.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing API contract and privacy tests**

```python
@pytest.mark.parametrize("purpose", ["student", "guardian", "staff_applicant", "visitor"])
def test_profile_requires_exact_purpose_fields(db_client, registration_session, purpose) -> None:
    payload = complete_profile_payload(purpose)
    response = db_client.patch(
        "/api/v1/registration/me",
        headers=registration_session.csrf_header,
        json=payload,
    )
    assert response.status_code == 200
    assert response.json()["primary_purpose"] == purpose


@pytest.mark.parametrize("fixture_name", ["missing", "existing", "disabled", "conflict"])
def test_public_finalization_does_not_disclose_account_state(db_client, request, fixture_name) -> None:
    scenario = request.getfixturevalue(fixture_name)
    response = scenario.finalize(db_client)
    assert set(response.json()) <= {"state", "next_action", "request_id"}
    assert "person_id" not in response.text
    assert "account_id" not in response.text


def test_conflict_reviewer_without_sensitive_read_gets_masked_detail(
    db_client,
    login_as_registration_reviewer,
    conflict_application,
) -> None:
    response = db_client.get(
        f"/api/v1/registration-admin/applications/{conflict_application.id}"
    )
    assert response.status_code == 200
    assert response.json()["masked_identifier"]
    assert "identity_number" not in response.text
    assert "candidate_person_id" not in response.text
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/api/test_registration_profiles.py \
  apps/api/tests/api/test_registration_privacy.py \
  apps/api/tests/api/test_registration_admin.py -q
```

Expected: missing profile/finalize contracts or responses disclose internal errors.

- [ ] **Step 3: Freeze strict schemas and public response allowlists**

`RegistrationProfileInput` must use `extra="forbid"`, require a 12–128 character password with letters and digits, require full name/identity/sex/birth/consent version, and require only the purpose-specific field:

```python
class PurposeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    requested_staff_type: StaffType | None = None
    staff_application_reason: str | None = Field(default=None, min_length=2, max_length=500)
    visit_reason: str | None = Field(default=None, min_length=2, max_length=200)
```

Validate exact combinations in a model validator: staff fields only for `staff_applicant`, visit reason only for `visitor`, and neither for student/guardian. Check identity birth and sex server-side. Public state responses contain only `state`, `masked_identifier`, `next_action`, `expires_at`, and `request_id`.

- [ ] **Step 4: Implement profile completion and consent persistence**

Add `POST /api/v1/registration/me/finalize`. Adult students, guardians, staff applicants, and visitors finalize as Task 6 defines. Existing-account matches return `next_action="login_or_reset"`; conflicts return `next_action="await_review"`. Neither path returns candidate IDs or matched fields.

Add formal-session administrative routes:

```text
GET  /api/v1/registration-admin/applications
GET  /api/v1/registration-admin/applications/{id}
POST /api/v1/registration-admin/applications/{id}/resolve
POST /api/v1/registration-admin/applications/{id}/reject
```

Masked list/detail require `registration.review`; decrypted contact and identity require the additional `registration.sensitive.read`. Resolve accepts a candidate `person_id` and 2–500 character reason, locks and reruns the exact Task 6 matching rules, and refuses mismatched identity rather than force-merging. Reject requires a reason, clears temporary secrets, and leaves all existing people/accounts unchanged. Missing, deleted, disabled, denied, and wrong-scope candidates are private 404 with zero business and success-audit writes.

- [ ] **Step 5: Run API/privacy tests and verify GREEN**

Run the Step 2 command plus `apps/api/tests/api/test_audit.py`. Expected: all pass; the response body and audit metadata contain no password, code, identity, full phone, or full email, and a resolved conflict creates at most one formal account.

- [ ] **Step 6: Commit the public registration API**

```bash
git add apps/api/app/registration apps/api/app/api/router.py \
  apps/api/tests/api/test_registration_profiles.py \
  apps/api/tests/api/test_registration_privacy.py apps/api/tests/api/test_audit.py \
  apps/api/tests/api/test_registration_admin.py
git commit -m "feat: expose private self-registration APIs"
```

## Task 8: Add minor completion and guardian invitation confirmation

**Files:**

- Create: `apps/api/app/registration/guardian_service.py`
- Modify: `apps/api/app/registration/schemas.py`
- Modify: `apps/api/app/registration/router.py`
- Create: `apps/api/tests/services/test_registration_guardians.py`
- Create: `apps/api/tests/db/test_registration_guardian_concurrency.py`
- Create: `apps/api/tests/api/test_registration_guardians.py`
- Modify: `apps/api/tests/services/test_readiness_policy.py`

- [ ] **Step 1: Write failing guardian-flow tests**

```python
def test_minor_stays_registration_only_until_guardian_accepts(guardian_service, minor_application) -> None:
    invitation = guardian_service.invite(
        minor_application.id,
        identifier_kind="phone",
        identifier="13800008888",
        relationship="mother",
        is_primary=True,
    )
    assert load_application(minor_application.id).state == "pending_guardian"
    assert formal_account_for_application(minor_application.id) is None
    guardian_service.accept(invitation.id, completed_guardian_account_id())
    assert load_application(minor_application.id).state == "finalized"


def test_adult_invitation_never_blocks_account(guardian_service, adult_student_account) -> None:
    guardian_service.invite_for_account(
        adult_student_account.id,
        identifier_kind="email",
        identifier="family@example.com",
        relationship="parent",
        is_primary=True,
    )
    assert load_account(adult_student_account.id).status == "active"
```

The concurrency test accepts one invitation in two database sessions and asserts one relationship, one finalized student account, and one idempotent result.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_guardians.py \
  apps/api/tests/db/test_registration_guardian_concurrency.py \
  apps/api/tests/api/test_registration_guardians.py -q
```

Expected: guardian invitation service and routes are missing.

- [ ] **Step 3: Implement invitations with exact lifecycle**

Add registration-session routes:

```text
GET    /api/v1/registration/me/guardian-invitations
POST   /api/v1/registration/me/guardian-invitations
POST   /api/v1/registration/me/guardian-invitations/{id}/resend
DELETE /api/v1/registration/me/guardian-invitations/{id}
GET    /api/v1/registration/guardian-invitations
POST   /api/v1/registration/guardian-invitations
POST   /api/v1/registration/guardian-invitations/handoff
GET    /api/v1/registration/guardian-invitations/context
POST   /api/v1/registration/guardian-invitations/start-registration
POST   /api/v1/registration/guardian-invitations/accept
GET    /api/v1/registration/guardian-invitations/acceptance
```

Invitation expiry is seven days. The `/me` routes require the minor's registration session; the bare list/create routes require a formal adult-student session and self scope. Resend creates a new 256-bit opaque handoff token and marks the previous row `revoked`. The handoff route exchanges that delivered token for a signed HttpOnly flow cookie; context and start-registration read only that cookie, and the latter locks the new account purpose to `guardian`. Accept and acceptance require both the same flow cookie and the logged-in guardian's formal account. Accept also requires the invited identifier to be verified on that guardian account plus the current guardian-relationship consent version. Persist that acceptance before creating the relationship. No public accept route takes `{id}`: the management UUID must never serve as an invitation credential.

Lock the student application, invitation, guardian person, and related relationship rows in that order. Call existing `IdentityService.add_guardian` and `GuardianReadinessPolicy`; never insert relationship rows directly. After the first valid guardian, call finalization exactly once. Additional invitations may still be accepted after finalization through the adult/formal-account path.

- [ ] **Step 4: Preserve private errors and registration-only access**

Missing, expired, revoked, wrong-identifier, underage-guardian, incomplete-profile, and already-used invitations return the same public `registration_action_unavailable` shape. Only authorized admin diagnostics may see reason codes. A pending minor's registration cookie must still receive 401 from `/auth/me` and private 404 from unrelated registration IDs.

- [ ] **Step 5: Run guardian tests and verify GREEN**

Run the Step 2 command plus `apps/api/tests/services/test_readiness_policy.py` and `apps/api/tests/api/test_guardians.py`. Expected: all pass and no duplicate relationship under concurrency.

- [ ] **Step 6: Commit guardian registration**

```bash
git add apps/api/app/registration apps/api/tests/services/test_registration_guardians.py \
  apps/api/tests/db/test_registration_guardian_concurrency.py \
  apps/api/tests/api/test_registration_guardians.py \
  apps/api/tests/services/test_readiness_policy.py
git commit -m "feat: complete minor registration with guardians"
```

## Task 9: Add staff identity applications and delegated review

**Files:**

- Create: `apps/api/app/registration/staff_service.py`
- Create: `apps/api/app/registration/access.py`
- Modify: `apps/api/app/registration/admin_router.py`
- Modify: `apps/api/app/registration/router.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/services/test_staff_identity_applications.py`
- Create: `apps/api/tests/api/test_staff_identity_applications.py`
- Create: `apps/api/tests/db/test_staff_identity_application_concurrency.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`

- [ ] **Step 1: Write failing staff application and authorization tests**

```python
def test_staff_applicant_has_no_staff_profile_or_role_before_review(db, finalized_staff_applicant) -> None:
    assert db.get(StaffProfile, finalized_staff_applicant.person_id) is None
    assert assigned_role_keys(finalized_staff_applicant.account_id) == {"registered_user"}


@pytest.mark.parametrize("actor", ["customer_service", "coach", "guardian", "student"])
def test_unprivileged_actor_cannot_review_staff_application(db_client, login, actor, pending_staff_application) -> None:
    session = login(actor)
    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{pending_staff_application.id}/approve",
        headers=session.csrf_header,
        json={"reason": "审核通过"},
    )
    assert response.status_code == 404
    assert load_application(pending_staff_application.id).status == "pending"
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_staff_identity_applications.py \
  apps/api/tests/api/test_staff_identity_applications.py \
  apps/api/tests/db/test_staff_identity_application_concurrency.py -q
```

Expected: staff service and review routes do not exist.

- [ ] **Step 3: Implement self status and review service**

Expose:

```text
GET  /api/v1/registration/staff-application
GET  /api/v1/registration-admin/staff-applications
GET  /api/v1/registration-admin/staff-applications/{id}
POST /api/v1/registration-admin/staff-applications/{id}/approve
POST /api/v1/registration-admin/staff-applications/{id}/reject
```

List access requires `staff_identity_applications.review`; full identity fields additionally require `registration.sensitive.read`. Approval mapping is fixed:

```python
STAFF_ROLE_BY_TYPE = {
    "coach": "coach",
    "life_teacher": "life_teacher",
    "customer_service": "customer_service",
    "administration": "staff",
    "other": "staff",
}
```

An `administration` application never grants `admin`. Approval locks application, person, existing staff profile, account, and role rows; it creates `StaffProfile`, assigns only the mapped baseline role, and writes review reason/audit. Rejection preserves the base account. Two concurrent approvals return one transition and one idempotent representation.

- [ ] **Step 4: Run staff tests and verify GREEN**

Run the Step 2 command plus `apps/api/tests/db/test_rbac_seed.py` and `apps/api/tests/api/test_permissions.py`. Expected: all pass; unprivileged selectors are private 404 with zero writes.

- [ ] **Step 5: Commit staff application review**

```bash
git add apps/api/app/registration apps/api/app/api/router.py \
  apps/api/tests/services/test_staff_identity_applications.py \
  apps/api/tests/api/test_staff_identity_applications.py \
  apps/api/tests/db/test_staff_identity_application_concurrency.py \
  apps/api/tests/db/test_rbac_seed.py
git commit -m "feat: review self-service staff applications"
```

## Task 10: Add secondary identifiers and password recovery

**Files:**

- Modify: `apps/api/app/auth/schemas.py`
- Modify: `apps/api/app/auth/service.py`
- Modify: `apps/api/app/auth/router.py`
- Modify: `apps/api/app/registration/verification_service.py`
- Create: `apps/api/tests/services/test_password_recovery.py`
- Create: `apps/api/tests/api/test_password_recovery.py`
- Create: `apps/api/tests/api/test_account_identifiers.py`
- Create: `apps/api/tests/db/test_account_identifier_concurrency.py`

- [ ] **Step 1: Write failing recovery and identifier tests**

```python
def test_password_recovery_revokes_every_old_session(
    password_recovery,
    captured_sender,
    account_with_sessions,
) -> None:
    challenge = password_recovery.issue("admin@example.com", "127.0.0.1")
    code = captured_sender.code_for(challenge.id)
    password_recovery.complete(challenge.id, code, "New-password-2026")
    assert active_session_count(account_with_sessions.id) == 0
    assert password_matches(account_with_sessions.id, "New-password-2026")


def test_add_verified_phone_unlocks_phone_readiness(
    identifier_service,
    captured_sender,
    email_only_account,
) -> None:
    challenge = identifier_service.issue_addition(email_only_account.id, "13800007777")
    code = captured_sender.code_for(challenge.id)
    identifier_service.verify_addition(email_only_account.id, challenge.id, code)
    assert verified_identifier_kinds(email_only_account.id) == {"email", "phone"}
```

The concurrency test races two accounts adding one phone and asserts one winner, one `registration_action_unavailable`, and no duplicate digest.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_password_recovery.py \
  apps/api/tests/api/test_password_recovery.py \
  apps/api/tests/api/test_account_identifiers.py \
  apps/api/tests/db/test_account_identifier_concurrency.py -q
```

Expected: routes and services are missing.

- [ ] **Step 3: Implement nonenumerating recovery and contact addition**

Freeze routes:

```text
POST /api/v1/auth/password-reset/challenges
POST /api/v1/auth/password-reset/complete
POST /api/v1/auth/identifiers/challenges
POST /api/v1/auth/identifiers/verify
GET  /api/v1/auth/identifiers
```

Password challenge responses are identical for existing, missing, disabled, and unverified identifiers. Only a challenge bound to an active identifier may mutate. Completion locks account and sessions, hashes the new password, clears lock counters, revokes all formal and registration sessions, consumes the challenge, and audits without identifier plaintext.

Adding an identifier requires a formal session, matching `person_id`, a fresh code, and global digest uniqueness. The transaction updates encrypted person contact, inserts `AccountLoginIdentifier`, and leaves the prior primary identifier unchanged unless the request explicitly sets `make_primary=true`.

- [ ] **Step 4: Run recovery tests and verify GREEN**

Run the Step 2 command plus `apps/api/tests/api/test_auth_sessions.py`. Expected: all pass; old cookies receive 401 immediately after reset.

- [ ] **Step 5: Commit password recovery**

```bash
git add apps/api/app/auth apps/api/app/registration/verification_service.py \
  apps/api/tests/services/test_password_recovery.py \
  apps/api/tests/api/test_password_recovery.py \
  apps/api/tests/api/test_account_identifiers.py \
  apps/api/tests/db/test_account_identifier_concurrency.py
git commit -m "feat: recover passwords and add login identifiers"
```

## Task 11: Enforce verified-phone readiness in admissions, payments, and housing

**Files:**

- Create: `apps/api/app/auth/contact_readiness.py`
- Modify: `apps/api/app/admissions/errors.py`
- Modify: `apps/api/app/admissions/api_support.py`
- Modify: `apps/api/app/admissions/case_service.py`
- Modify: `apps/api/app/admissions/payment_service.py`
- Modify: `apps/api/app/housing/errors.py`
- Modify: `apps/api/app/housing/occupancy_service.py`
- Modify: `apps/api/app/housing/reporting_projection.py`
- Modify: `apps/api/tests/services/test_admission_cases.py`
- Modify: `apps/api/tests/services/test_payments.py`
- Modify: `apps/api/tests/db/test_housing_stay_lifecycle.py`
- Modify: `apps/api/tests/api/test_housing_occupancy.py`

- [ ] **Step 1: Write failing zero-write readiness tests**

```python
def test_email_only_student_cannot_confirm_enrollment(admission_service, email_only_case) -> None:
    before = enrollment_count()
    with pytest.raises(VerifiedPhoneRequired):
        admission_service.confirm_enrollment(
            email_only_case.id,
            confirm_input(email_only_case),
            actor_user_id=ADMIN_ID,
            request_id="phone-gate-enrollment",
        )
    assert enrollment_count() == before


def test_email_only_person_cannot_receive_stay(occupancy_service, email_only_person, room) -> None:
    with pytest.raises(HousingVerifiedPhoneRequired):
        occupancy_service.create_stay(
            room.id,
            stay_input(email_only_person.id),
            actor_user_id=ADMIN_ID,
            occurred_at=NOW,
            authorize_room=allow_room,
        )
    assert stay_count(email_only_person.id) == 0
```

Add a manual-payment test with the same zero-row and zero-success-audit assertions.

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_admission_cases.py \
  apps/api/tests/services/test_payments.py \
  apps/api/tests/db/test_housing_stay_lifecycle.py \
  apps/api/tests/api/test_housing_occupancy.py -q
```

Expected: email-only people currently pass at least one mutation boundary.

- [ ] **Step 3: Implement one shared phone policy**

```python
class VerifiedPhoneRequired(ValueError):
    code = "verified_phone_required"


class VerifiedContactPolicy:
    def __init__(self, db: Session) -> None:
        self.db = db

    def require_phone_for_person(self, person_id: UUID) -> AccountLoginIdentifier:
        row = self.db.scalar(
            select(AccountLoginIdentifier)
            .join(UserAccount, UserAccount.id == AccountLoginIdentifier.account_id)
            .where(
                UserAccount.person_id == person_id,
                UserAccount.status == "active",
                AccountLoginIdentifier.kind == "phone",
            )
            .with_for_update(of=AccountLoginIdentifier)
        )
        if row is None:
            raise VerifiedPhoneRequired("verified phone is required")
        return row
```

Call it only after the existing person/cohort/room lock order is established and before the first business insert. Manual payment imports already require a source phone; preserve that path and apply the explicit policy to manual payment creation/attachment. Resident option queries exclude email-only accounts, while `_require_resident_ready` rechecks under lock to prevent stale selection.

Map the error to HTTP 422 with stable code `verified_phone_required`. Do not expose whether a different account owns the phone.

- [ ] **Step 4: Run cross-module tests and verify GREEN**

Run the Step 2 command. Expected: all pass; adding a verified phone in Task 10 makes the same operations pass.

- [ ] **Step 5: Commit shared readiness gates**

```bash
git add apps/api/app/auth/contact_readiness.py apps/api/app/admissions \
  apps/api/app/housing apps/api/tests/services/test_admission_cases.py \
  apps/api/tests/services/test_payments.py \
  apps/api/tests/db/test_housing_stay_lifecycle.py \
  apps/api/tests/api/test_housing_occupancy.py
git commit -m "feat: require verified phones for camp operations"
```

## Task 12: Add expiry cleanup, audit redaction, and privacy regression gates

**Files:**

- Create: `apps/api/app/registration/cleanup_service.py`
- Create: `apps/api/app/cli/cleanup_registration_applications.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/app/registration/router.py`
- Create: `apps/api/tests/services/test_registration_cleanup.py`
- Create: `apps/api/tests/cli/test_cleanup_registration_applications.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `apps/api/tests/api/test_registration_privacy.py`

- [ ] **Step 1: Write failing retention and redaction tests**

```python
def test_thirty_day_cleanup_clears_sensitive_fields(cleanup_service, stale_application) -> None:
    cleanup_service.prune(limit=100)
    row = load_application(stale_application.id)
    assert row.state == "expired"
    assert row.identity_ciphertext is None
    assert row.verified_identifier_ciphertext is None
    assert row.password_hash is None


def test_registration_audit_rejects_sensitive_metadata(audit_service) -> None:
    with pytest.raises(ValueError):
        audit_service.append(
            actor_user_id=None,
            action="registration.denied",
            resource_type="registration_application",
            resource_id=uuid4(),
            purpose=None,
            outcome="denied",
            request_id="sensitive-audit",
            metadata={"verification_code": "123456"},
        )
```

- [ ] **Step 2: Run tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/services/test_registration_cleanup.py \
  apps/api/tests/cli/test_cleanup_registration_applications.py \
  apps/api/tests/api/test_registration_privacy.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: cleanup service is missing and audit metadata accepts at least one forbidden key.

- [ ] **Step 3: Implement bounded cleanup and redaction**

Expire at most `limit` rows ordered by `(expires_at, id)` using `FOR UPDATE SKIP LOCKED`. Clear application and finalized temporary secrets, revoke sessions, mark seven-day invitations expired, and retain only UUIDs, timestamps, state, conflict code, and audit references. Invoke `prune(limit=25)` before creating a new application; provide CLI `python -m app.cli.cleanup_registration_applications --limit 500` for operations.

Extend audit sensitive-key and sensitive-value checks with `verification_code`, `password`, `identity_number`, `identifier`, `phone`, `email`, registration cookies, and registration CSRF. Permit only opaque UUIDs, stable reason codes, row counts, purpose, state, and identifier kind.

- [ ] **Step 4: Run retention/privacy tests and verify GREEN**

Run the Step 2 command. Expected: all pass and the CLI output contains counts only.

- [ ] **Step 5: Commit cleanup and privacy gates**

```bash
git add apps/api/app/registration/cleanup_service.py \
  apps/api/app/cli/cleanup_registration_applications.py \
  apps/api/app/audit/service.py apps/api/app/registration/router.py \
  apps/api/tests/services/test_registration_cleanup.py \
  apps/api/tests/cli/test_cleanup_registration_applications.py \
  apps/api/tests/api/test_registration_privacy.py apps/api/tests/api/test_audit.py
git commit -m "feat: expire private registration applications"
```

## Task 13: Update Web authentication and add runtime-validated registration API

**Files:**

- Create: `apps/web/src/registration/api.ts`
- Create: `apps/web/src/registration/api.test.ts`
- Create: `apps/web/src/registration/keys.ts`
- Create: `apps/web/src/registration/keys.test.ts`
- Modify: `apps/web/src/auth/AuthProvider.tsx`
- Modify: `apps/web/src/auth/AuthProvider.test.tsx`
- Modify: `apps/web/src/auth/LoginPage.tsx`
- Modify: `apps/web/src/auth/LoginPage.test.tsx`
- Modify: `apps/web/src/auth/LoginPage.css`
- Modify: `apps/web/src/settings/AccountsPage.tsx`
- Modify: `apps/web/src/settings/AccountsPage.test.tsx`
- Modify: `apps/web/src/settings/api.ts`
- Create: `apps/web/src/settings/api.test.ts`

- [ ] **Step 1: Write failing Web contract tests**

```typescript
it("logs in with a generic phone or email identifier", async () => {
  renderAuthHarness()
  await auth.login("student@example.com", "Valid-password-2026")
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/auth/login",
    expect.objectContaining({
      body: JSON.stringify({
        identifier: "student@example.com",
        password: "Valid-password-2026",
      }),
    }),
  )
})

it("rejects extra sensitive fields in a registration response", async () => {
  fetchMock.mockResolvedValue(jsonResponse({
    state: "contact_verified",
    next_action: "complete_profile",
    identity_number: "110105199001021234",
  }))
  await expect(getRegistrationMe()).rejects.toThrow("Invalid registration response")
})

it("renders an email-only account without a fake phone", async () => {
  renderAccountsPage(emailOnlyAccountFixture())
  expect(await screen.findByText("n***@example.com")).toBeVisible()
  expect(screen.queryByText(/000\*\*\*\*0000/)).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/auth/AuthProvider.test.tsx src/auth/LoginPage.test.tsx \
  src/settings/AccountsPage.test.tsx src/settings/api.test.ts \
  src/registration/api.test.ts src/registration/keys.test.ts
```

Expected: registration modules are missing and login still sends `phone`.

- [ ] **Step 3: Implement strict clients and generic login UI**

Change `AuthUser.email` to `string | null` and `login(phone, password)` to `login(identifier, password)`. The login page label becomes “手机号或邮箱”, uses `type="text"` and `autoComplete="username"`, and adds links to `/register` and `/forgot-password`.

Update `AccountsPage` and its API types to consume `masked_primary_identifier`, `primary_identifier_kind`, optional masked contacts, and verified kinds. Do not synthesize placeholder phone/email values for sorting or display.

In `registration/api.ts`, define explicit parsers that reject unknown keys before returning values. Keep registration CSRF in module memory separate from the normal API client's CSRF:

```typescript
let registrationCsrfToken: string | null = null

export function setRegistrationCsrfToken(value: string | null): void {
  registrationCsrfToken = value
}

export async function registrationFetch<T>(path: string, init: RequestInit, parse: Parser<T>): Promise<T> {
  const headers = new Headers(init.headers)
  if (registrationCsrfToken && !["GET", "HEAD", "OPTIONS"].includes((init.method ?? "GET").toUpperCase())) {
    headers.set("X-Registration-CSRF-Token", registrationCsrfToken)
  }
  const response = await fetch(`/api/v1/registration${path}`, { ...init, headers, credentials: "include" })
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  return parse(await response.json())
}
```

Clear registration CSRF on finalization, logout, invalid registration session, and application expiry.

- [ ] **Step 4: Run Web contract tests and verify GREEN**

Run the Step 2 command. Expected: all tests pass, including unknown-key rejection.

- [ ] **Step 5: Commit Web authentication contracts**

```bash
git add apps/web/src/auth apps/web/src/registration/api.ts \
  apps/web/src/registration/api.test.ts apps/web/src/registration/keys.ts \
  apps/web/src/registration/keys.test.ts apps/web/src/settings/AccountsPage.tsx \
  apps/web/src/settings/AccountsPage.test.tsx apps/web/src/settings/api.ts \
  apps/web/src/settings/api.test.ts
git commit -m "feat: add Web registration contracts"
```

## Task 14: Build the public responsive registration and recovery pages

**Files:**

- Create: `apps/web/src/registration/RegistrationStartPage.tsx`
- Create: `apps/web/src/registration/RegistrationStartPage.test.tsx`
- Create: `apps/web/src/registration/RegistrationVerifyPage.tsx`
- Create: `apps/web/src/registration/RegistrationVerifyPage.test.tsx`
- Create: `apps/web/src/registration/RegistrationProfilePage.tsx`
- Create: `apps/web/src/registration/RegistrationProfilePage.test.tsx`
- Create: `apps/web/src/registration/ForgotPasswordPage.tsx`
- Create: `apps/web/src/registration/ForgotPasswordPage.test.tsx`
- Create: `apps/web/src/auth/AccountIdentifiersPanel.tsx`
- Create: `apps/web/src/auth/AccountIdentifiersPanel.test.tsx`
- Modify: `apps/web/src/foodPreferences/MyFoodProfilePage.tsx`
- Modify: `apps/web/src/foodPreferences/MyFoodProfilePage.test.tsx`
- Create: `apps/web/src/registration/registration.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write failing route and form tests**

```typescript
it("offers four single-select registration purposes", () => {
  renderRegistrationStart()
  expect(screen.getAllByRole("radio")).toHaveLength(4)
  expect(screen.getByRole("radio", { name: /学员/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /监护人/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /工作人员申请/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /临时访客/ })).toBeVisible()
})

it("does not persist identity or password in browser storage", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem")
  renderRegistrationProfile()
  await user.type(screen.getByLabelText("身份证"), "110105199001021234")
  await user.type(screen.getByLabelText("密码"), "Valid-password-2026")
  expect(setItem).not.toHaveBeenCalled()
})

it("verifies a secondary phone from the personal profile", async () => {
  renderAccountIdentifiersPanel()
  await user.click(screen.getByRole("button", { name: "添加手机号" }))
  await user.type(screen.getByLabelText("新手机号"), "13800007777")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  expect(await screen.findByText("138****7777 · 已验证")).toBeVisible()
})
```

Profile tests cover identity-derived birth/sex confirmation, staff-only fields, visitor reason, password confirmation, consent version, private errors, and disabled double submit. Router tests prove all public routes render while anonymous and remain outside `AppShell`.

- [ ] **Step 2: Run component tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/registration/RegistrationStartPage.test.tsx \
  src/registration/RegistrationVerifyPage.test.tsx \
  src/registration/RegistrationProfilePage.test.tsx \
  src/registration/ForgotPasswordPage.test.tsx \
  src/auth/AccountIdentifiersPanel.test.tsx \
  src/foodPreferences/MyFoodProfilePage.test.tsx \
  src/app/router.test.tsx
```

Expected: components and routes are missing.

- [ ] **Step 3: Implement the four public steps and password recovery**

Use semantic form controls and server state as the source of truth. The start page posts purpose plus one contact kind/value. Verify page displays only masked destination and bounded resend time. Profile page never stores secrets outside React component state, clears password/code on unmount, and submits the exact purpose-specific payload. Forgot-password always displays the same completion message whether the identifier exists or not.

`AccountIdentifiersPanel` lists masked verified identifiers, marks the primary one, and implements the Task 10 challenge/verify flow. Mount it on the existing `/me/profile` page above food preferences. It never preloads full phone/email values and removes its query cache plus closes the verification form after private 404 or account revocation.

Register public routes:

```tsx
<Route path="/register" element={<RegistrationStartPage />} />
<Route path="/register/verify" element={<RegistrationVerifyPage />} />
<Route path="/register/profile" element={<RegistrationProfilePage />} />
<Route path="/forgot-password" element={<ForgotPasswordPage />} />
```

CSS must use a single column at 360/768, a centered card at 1024/1440, `min-width: 0` on form children, visible focus rings, and no fixed pixel width wider than the viewport.

- [ ] **Step 4: Run component tests and verify GREEN**

Run the Step 2 command. Expected: all pass with no React act warnings or unhandled requests.

- [ ] **Step 5: Commit public pages**

```bash
git add apps/web/src/registration apps/web/src/auth/AccountIdentifiersPanel.tsx \
  apps/web/src/auth/AccountIdentifiersPanel.test.tsx \
  apps/web/src/foodPreferences/MyFoodProfilePage.tsx \
  apps/web/src/foodPreferences/MyFoodProfilePage.test.tsx \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx
git commit -m "feat: build responsive self-registration pages"
```

## Task 15: Build guardian completion, status, and administrator workspaces

**Files:**

- Create: `apps/web/src/registration/RegistrationGuardianPage.tsx`
- Create: `apps/web/src/registration/RegistrationGuardianPage.test.tsx`
- Create: `apps/web/src/registration/RegistrationStatusPage.tsx`
- Create: `apps/web/src/registration/RegistrationStatusPage.test.tsx`
- Create: `apps/web/src/registration/RegistrationAdminPage.tsx`
- Create: `apps/web/src/registration/RegistrationAdminPage.test.tsx`
- Create: `apps/web/src/registration/StaffApplicationsAdminPage.tsx`
- Create: `apps/web/src/registration/StaffApplicationsAdminPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1: Write failing capability, privacy, and revocation tests**

```typescript
it("shows only masked guardian invitation targets", async () => {
  renderGuardianCompletion()
  expect(await screen.findByText("138****8888")).toBeVisible()
  expect(screen.queryByText("13800008888")).not.toBeInTheDocument()
})

it("removes registration administration immediately after revocation", async () => {
  const queryClient = renderRegistrationAdmin({ permissions: ["registration.review"] })
  revokePermissionOnNextRequest()
  await user.click(screen.getByRole("button", { name: "刷新" }))
  expect(queryClient.getQueryData(registrationKeys.adminRoot())).toBeUndefined()
  expect(screen.queryByRole("heading", { name: "注册处理" })).not.toBeInTheDocument()
})
```

Tests also prove `registration.review` cannot reveal full identity without `registration.sensitive.read`, staff approval requires its separate capability, and reviewer reasons are required.

- [ ] **Step 2: Run tests and verify RED**

```bash
npm --prefix apps/web test -- --run \
  src/registration/RegistrationGuardianPage.test.tsx \
  src/registration/RegistrationStatusPage.test.tsx \
  src/registration/RegistrationAdminPage.test.tsx \
  src/registration/StaffApplicationsAdminPage.test.tsx \
  src/layout/AppShell.test.tsx
```

Expected: pages and navigation capabilities are missing.

- [ ] **Step 3: Implement completion and status pages**

Guardian page lists masked pending invitations, supports invite/resend/revoke, and never renders a candidate person/account ID. Status page maps only server `next_action` values to approved user text: complete profile, complete guardian, log in/reset, await review, or finalized. Staff applicants see requested type and public status, not reviewer identity or internal notes.

- [ ] **Step 4: Implement capability-only admin pages**

Add routes `/settings/registrations` and `/settings/staff-applications` behind independent permission guards. Lists are masked by default. The full-detail action is absent, not disabled, without `registration.sensitive.read`. On 403/private 404, cancel active queries, remove the corresponding query-key root, close dialogs, and navigate to `/` without logging out the ERP session.

Add navigation entries only when capabilities are present:

```typescript
{ to: "/settings/registrations", label: "注册处理", permission: "registration.review" }
{ to: "/settings/staff-applications", label: "身份申请", permission: "staff_identity_applications.review" }
```

- [ ] **Step 5: Run UI tests and verify GREEN**

Run the Step 2 command. Expected: all pass across permission combinations and revocation paths.

- [ ] **Step 6: Commit guardian and administration UI**

```bash
git add apps/web/src/registration apps/web/src/app/router.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx
git commit -m "feat: manage registration and staff applications"
```

## Task 16: Add deterministic four-viewport registration E2E

**Files:**

- Create: `apps/api/app/cli/seed_registration_e2e.py`
- Create: `scripts/seed-registration-e2e.py`
- Create: `apps/api/tests/cli/test_seed_registration_e2e.py`
- Create: `apps/web/tests/self-service-registration.spec.ts`
- Create: `apps/web/tests/self-service-registration-privacy.spec.ts`
- Modify: `apps/web/tests/helpers.ts`
- Create: `scripts/run-self-service-registration-e2e.sh`
- Create: `apps/api/tests/cli/test_self_service_registration_launcher.py`

- [ ] **Step 1: Write failing seed and launcher safety tests**

```python
def test_registration_launcher_is_local_serial_and_secret_safe() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert '-m app.cli.e2e_database_safety' in source
    assert 'projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)' in source
    assert 'self-service-registration.spec.ts' in source
    assert 'self-service-registration-privacy.spec.ts' in source
    assert 'docker' not in source.casefold()
    assert 'captured-code.json' not in source
    assert stat.S_IMODE(LAUNCHER.stat().st_mode) == 0o755
```

Seed tests require idempotent creation of an exact-claim person, existing-account person, split-conflict people, email-only account, minor applicant, adult applicant, guardian, staff applicant, review accounts, and test sender destinations. JSON output contains opaque UUIDs and fixture aliases only.

- [ ] **Step 2: Run seed/launcher tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/cli/test_seed_registration_e2e.py \
  apps/api/tests/cli/test_self_service_registration_launcher.py -q
```

Expected: seed and launcher files are missing.

- [ ] **Step 3: Implement deterministic test-code retrieval without HTTP exposure**

The E2E sender writes a mode-`0600` JSON mailbox under a new empty temporary directory supplied by `REGISTRATION_TEST_MAILBOX_DIR`; each row contains challenge UUID, destination alias, purpose, and code. The API only enables this sender when `APP_ENV=test`, host is loopback, and the directory contains a runner-created sentinel. Browser tests read codes through a helper process invoked by Playwright, never through DOM, HTTP, logs, or committed files.

- [ ] **Step 4: Implement the two browser journeys**

`self-service-registration.spec.ts` covers phone adult registration, email registration/login, exact claim, minor guardian invitation and activation, adult optional guardian, staff application review, password reset, verified-phone gate, food onboarding distinction, and logout/login.

`self-service-registration-privacy.spec.ts` captures every ERP JSON response and recursively rejects identity numbers, complete fixture phones/emails, password/code values, cookie names, CSRF tokens, internal candidate IDs, reviewer-only notes, absolute paths, and database URLs. It also tests private 404 after permission revocation without refreshing the token.

Each file must run under all four existing Playwright projects and call `expectNoHorizontalOverflow(page)` on every public and admin page.

- [ ] **Step 5: Implement the safe local runner**

Follow the existing teaching-report runner structure. Require isolated `TEST_DATABASE_URL`, two distinct registration peppers plus existing secrets, free loopback API/Web ports, empty test mailbox directory, trap cleanup, exact readiness markers, and serial viewport runs. Do not print service logs on success; on failure redact Authorization, Cookie, CSRF, codes, phones, emails, identities, passwords, and ciphertext before showing bounded tails.

- [ ] **Step 6: Run seed, launcher, and four-view E2E**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/cli/test_seed_registration_e2e.py \
  apps/api/tests/cli/test_self_service_registration_launcher.py -q

E2E_PYTHON_BIN="$PYTHON_BIN" \
  TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
  FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
  SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
  REGISTRATION_VERIFICATION_PEPPER="$REGISTRATION_VERIFICATION_PEPPER" \
  REGISTRATION_SESSION_TOKEN_PEPPER="$REGISTRATION_SESSION_TOKEN_PEPPER" \
  EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  EVALUATION_TOOL_WEB_SERVICE_TOKEN="$EVALUATION_TOOL_WEB_SERVICE_TOKEN" \
  EVALUATION_TOOL_WORKER_SERVICE_TOKEN="$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" \
  bash scripts/run-self-service-registration-e2e.sh
```

Expected: focused tests pass and Playwright reports both journeys passing at 1440, 1024, 768, and 360.

- [ ] **Step 7: Commit the browser gate**

```bash
git add apps/api/app/cli/seed_registration_e2e.py scripts/seed-registration-e2e.py \
  apps/api/tests/cli/test_seed_registration_e2e.py \
  apps/api/tests/cli/test_self_service_registration_launcher.py \
  apps/web/tests/self-service-registration.spec.ts \
  apps/web/tests/self-service-registration-privacy.spec.ts \
  apps/web/tests/helpers.ts scripts/run-self-service-registration-e2e.sh
git commit -m "test: verify self-service registration journeys"
```

## Task 17: Run complete local gates, document operations, and review

**Files:**

- Create: `docs/operations/self-service-registration-local.md`
- Modify: `README.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Modify: `docs/superpowers/specs/2026-08-14-self-service-registration-design.md`

- [ ] **Step 1: Write failing truthful-documentation tests**

```python
def test_self_service_registration_operations_document_is_truthful() -> None:
    text = REGISTRATION_OPERATIONS.read_text(encoding="utf-8")
    for required in (
        "scripts/run-self-service-registration-e2e.sh",
        "本地测试发送器",
        "0017_self_service_registration",
        "项目选择与支付仍未开放",
        "Docker/NAS 仍未验证",
    ):
        assert required in text
    for forbidden in ("真实短信已接入", "生产可用", "支付已完成"):
        assert forbidden not in text
```

- [ ] **Step 2: Run documentation tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" "$PYTHON_BIN" -m pytest -p no:cacheprovider \
  apps/api/tests/cli/test_operations_docs.py -q
```

Expected: missing operations document and stale README boundaries.

- [ ] **Step 3: Write exact local operations and status documentation**

Document required secrets, safe test sender, isolated database, start/stop commands, cleanup behavior, account/registration cookie separation, 30-day retention, seven-day invitations, recovery behavior, migration/refusal conditions, and the official runner command. Update README to mark local self-registration complete only after all gates pass, while explicitly leaving project selection, orders, payment providers, real SMS/email, third-party identity, police API, GPU, Docker/NAS, backup/restore, and production pending.

Change the design status to `本地实现与完整门禁已完成` only after Step 6 succeeds; before then use `实施中`.

- [ ] **Step 4: Run all API, database, migration, and static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  "$PYTHON_BIN" -m pytest -p no:cacheprovider apps/api/tests -q

"$PYTHON_BIN" -m ruff check apps/api
"$PYTHON_BIN" -m mypy apps/api/app
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: every command exits 0. Inspect the pytest summary and reject any PostgreSQL, concurrency, authorization, migration, or privacy skip.

- [ ] **Step 5: Run the official four-viewport runner again**

Run the full command from Task 16 Step 6 against a freshly reset isolated database and empty mailbox directory. Expected: all eight Playwright project/spec combinations pass and cleanup removes services and temporary files.

- [ ] **Step 6: Request independent specification and quality/security reviews**

Invoke `superpowers:requesting-code-review`. The specification review must map every confirmed rule to code/tests. The quality/security review must cover public enumeration resistance, private 404, registration/formal session separation, CSRF, code single-use, cross-table lock order, conflict zero-write behavior, email plaintext removal, audit/log redaction, staff approval mapping, immediate revocation, and browser cache clearing. Fix every Important-or-higher finding through a new RED/GREEN cycle and request re-review.

- [ ] **Step 7: Perform final repository verification**

```bash
git diff --check
git status --short
git log --oneline --decorate -20
```

Expected: no whitespace errors; status contains only intentional reviewed changes; commit history shows one focused commit per task and no Docker/NAS or real-provider changes.

- [ ] **Step 8: Commit truthful documentation**

```bash
git add docs/operations/self-service-registration-local.md README.md \
  apps/api/tests/cli/test_operations_docs.py \
  docs/superpowers/specs/2026-08-14-self-service-registration-design.md
git commit -m "docs: complete local registration acceptance"
```

## Spec coverage index

| Confirmed design rule | Implemented and verified by |
| --- | --- |
| Adult student opens without admin preapproval | Tasks 6, 7, 14, 16 |
| Minor requires one valid independent guardian | Tasks 5, 8, 15, 16 |
| Payment remains pending-allocation and is not built here | Tasks 11, 17 |
| Student, guardian, staff applicant, and visitor can self-apply | Tasks 6, 7, 14, 16 |
| One primary purpose; later identities are admin-managed | Tasks 6, 9 |
| Staff application grants no staff permission before review | Tasks 6, 9, 15 |
| Phone or email registration with full required identity | Tasks 3, 4, 5, 7, 14 |
| Email-only basic access; phone required for operations | Tasks 10, 11, 14, 16 |
| Exact safe claim of existing unowned person | Tasks 6, 7, 16 |
| Existing account redirects to login/password recovery | Tasks 7, 10, 14, 16 |
| Split/mismatched identity enters delegated conflict review | Tasks 6, 7, 15, 16 |
| Pending minor receives registration-only session | Tasks 5, 8, 15, 16 |
| Guardian verifies and confirms own account/relationship | Tasks 4, 8, 15, 16 |
| Adult optional guardian invitation does not block account | Tasks 8, 15, 16 |
| Applicant cannot set guardian password or confirm for them | Tasks 5, 8, 16 |
| Structural Chinese ID validation without image upload | Tasks 2, 7, 14 |
| Versioned identity/privacy/relationship consent | Tasks 1, 6, 7, 8 |
| Phone/email password reset revokes old sessions | Tasks 4, 10, 14, 16 |
| Guardian-only skips food onboarding; others receive it | Tasks 1, 2, 6, 16 |
| Sensitive retention, redaction, enumeration resistance | Tasks 4, 5, 7, 8, 9, 10, 12, 16 |
| Four responsive viewports and non-Docker local boundary | Tasks 14, 15, 16, 17 |

## Completion definition

The work is complete only when the official local runner passes all four viewports, full API/Web/static gates exit 0, PostgreSQL and concurrency tests are not skipped, independent reviews return PASS after fixes, and the final status contains only expected files. The completion statement must be exactly scoped to local self-service registration; it must not claim project selection, payment, real messaging providers, third-party identity verification, Docker/NAS, data migration, or production readiness.
