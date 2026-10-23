# Password Policy Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change the ERP password policy to 8–16 characters with at least one ASCII uppercase letter, lowercase letter, and digit, while keeping existing password hashes usable and creating the first NAS administrator.

**Architecture:** Keep one authoritative validator in `PasswordService`. All account creation, password change/reset, and the first-admin CLI already flow through that service; update the temporary-password generator so it produces values inside the new bound. Synchronize the API error text and the forced-change page constraints, then verify the built deployment against the existing NAS database without changing its PostgreSQL volume.

**Tech Stack:** Python 3.12, pytest, FastAPI, Argon2, React/TypeScript, Vitest, Docker Compose, PostgreSQL 16.

---

### Task 1: Add failing backend policy tests

**Files:**
- Modify: `apps/api/tests/unit/auth/test_passwords.py`
- Modify: `apps/api/tests/api/test_auth_sessions.py:1170-1188`

- [ ] **Step 1: Replace the unit policy cases with explicit boundary and character-class cases**

Add these cases to `apps/api/tests/unit/auth/test_passwords.py`:

```python
import re


@pytest.mark.parametrize("password", ["Aa1xxxxx", "Aa1" + "x" * 13])
def test_new_password_policy_accepts_eight_to_sixteen_characters(
    passwords: PasswordService,
    password: str,
) -> None:
    assert passwords.verify(password, passwords.hash(password)) is True


@pytest.mark.parametrize(
    "password",
    [
        "Aa1xxx",                 # fewer than 8 characters
        "aa1xxxxx",                # no uppercase letter
        "AA1XXXXX",                # no lowercase letter
        "Aaxxxxxx",                # no digit
        "Aa1" + "x" * 14,         # 17 characters
    ],
)
def test_new_password_policy_rejects_invalid_length_or_character_class(
    passwords: PasswordService,
    password: str,
) -> None:
    with pytest.raises(PasswordPolicyError):
        passwords.hash(password)


def test_temporary_password_is_eight_to_sixteen_and_has_all_required_classes(
    passwords: PasswordService,
) -> None:
    password = passwords.temporary_password()

    assert 8 <= len(password) <= 16
    assert re.search(r"[A-Z]", password)
    assert re.search(r"[a-z]", password)
    assert re.search(r"[0-9]", password)
    assert passwords.verify(password, passwords.hash(password)) is True
```

Update the existing invalid fixture so it no longer treats a 16-character digit-only value as a length failure; the explicit character-class test above covers that case.

- [ ] **Step 2: Assert the API exposes the new policy text**

In `test_change_password_reports_password_policy_error`, keep the existing request and add:

```python
assert response.json()["detail"] == "新密码需为 8–16 位，并同时包含大写、小写字母和数字"
```

- [ ] **Step 3: Run the focused tests and confirm the expected RED failure**

Run:

```bash
cd apps/api
pytest tests/unit/auth/test_passwords.py tests/api/test_auth_sessions.py::test_change_password_reports_password_policy_error -q
```

Expected: the new 8-character acceptance and temporary-password assertions fail, and the API detail assertion fails because production code still implements the 12–128 letter/digit policy.

### Task 2: Implement the centralized backend policy

**Files:**
- Modify: `apps/api/app/security/passwords.py:1-60`
- Modify: `apps/api/app/auth/router.py:108-120`
- Modify: `apps/api/app/auth/schemas.py:43-46`

- [ ] **Step 1: Replace the validator with the approved 8–16 ASCII-class policy**

Add module constants and replace the existing `validate` and `temporary_password` methods with explicit ASCII checks so non-Latin letters cannot accidentally satisfy the uppercase/lowercase requirement:

```python
import secrets
import string

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 16


@staticmethod
def validate(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("password must be between 8 and 16 characters")
    if not any("A" <= character <= "Z" for character in password):
        raise PasswordPolicyError("password must contain an uppercase letter")
    if not any("a" <= character <= "z" for character in password):
        raise PasswordPolicyError("password must contain a lowercase letter")
    if not any("0" <= character <= "9" for character in password):
        raise PasswordPolicyError("password must contain a digit")

def temporary_password(self) -> str:
    alphabet = string.ascii_letters + string.digits
    characters = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        *(secrets.choice(alphabet) for _ in range(MAX_PASSWORD_LENGTH - 3)),
    ]
    secrets.SystemRandom().shuffle(characters)
    password = "".join(characters)
    self.validate(password)
    return password
```

Retain the existing Argon2 configuration and verification behavior; `verify` must not call `validate`, preserving compatibility for old hashes.

- [ ] **Step 2: Update the API error detail**

Change the `ProblemError` detail in `apps/api/app/auth/router.py` to:

```python
detail="新密码需为 8–16 位，并同时包含大写、小写字母和数字",
```

Change `ChangePasswordRequest.new_password` to `Field(min_length=8, max_length=16)` so Pydantic accepts the same boundary that the service validates. Keep `LoginRequest.password` and `current_password` at their existing 128-character maximum so legacy passwords can still be submitted for login or replacement.

- [ ] **Step 3: Run the backend RED tests until GREEN**

Run:

```bash
cd apps/api
pytest tests/unit/auth/test_passwords.py tests/api/test_auth_sessions.py::test_change_password_reports_password_policy_error -q
```

Expected: all focused tests pass, including both length boundaries and the temporary-password invariant.

### Task 3: Synchronize the forced-password-change web page

**Files:**
- Modify: `apps/web/src/auth/LoginPage.tsx:150-178`
- Modify: `apps/web/src/auth/LoginPage.test.tsx` (forced-password-change journey)
- Modify: `apps/web/src/auth/LoginPage.test.tsx` (forced-password-change journey)

- [ ] **Step 1: Add a UI assertion before changing the password**

In the existing forced-password-change test, assert:

```typescript
expect(screen.getByText("新密码需为 8–16 位，并同时包含大写、小写字母和数字。"))
  .toBeVisible()
const newPassword = screen.getByLabelText("新密码")
expect(newPassword).toHaveAttribute("minLength", "8")
expect(newPassword).toHaveAttribute("maxLength", "16")
```

Run the focused test first and confirm it fails against the old hint and `minLength={12}`.

- [ ] **Step 2: Update the page constraints and text**

Replace the hint and apply the same HTML limits to both new-password fields:

```tsx
<p className="loginHint">新密码需为 8–16 位，并同时包含大写、小写字母和数字。</p>
<input
  id="new-password"
  type="password"
  autoComplete="new-password"
  minLength={8}
  maxLength={16}
  value={newPassword}
  onChange={(event) => setNewPassword(event.target.value)}
  required
/>
<input
  id="confirm-password"
  type="password"
  autoComplete="new-password"
  minLength={8}
  maxLength={16}
  value={confirmation}
  onChange={(event) => setConfirmation(event.target.value)}
  required
/>
```

- [ ] **Step 3: Run the focused web test**

Run:

```bash
cd apps/web
npm test -- src/auth/LoginPage.test.tsx
```

Expected: the full `LoginPage.test.tsx` suite passes with the new visible guidance and native input limits.

### Task 4: Run local regression and static checks

**Files:** No new files; inspect only the changed files and existing user changes.

- [ ] **Step 1: Run backend focused account flows**

Run:

```bash
cd apps/api
pytest tests/unit/auth/test_passwords.py tests/services/test_accounts.py tests/api/test_accounts.py tests/api/test_auth_sessions.py -q
ruff check app/security/passwords.py app/auth/router.py tests/unit/auth/test_passwords.py tests/api/test_auth_sessions.py
```

Expected: all selected tests and Ruff pass. If PostgreSQL tests require `TEST_DATABASE_URL`, use an isolated database and report any skipped database coverage rather than calling it complete.

Update test-only newly-created credential fixtures and E2E seed constants to 8–16-character values before running the suite; these hashes are created through `PasswordService` and must follow the production policy. Preserve the existing untracked `apps/api/tests/api/test_camp_surveys.py` file unchanged if it remains blocked by its missing survey model.

- [ ] **Step 2: Run web checks and build**

Run:

```bash
cd apps/web
npm test -- src/auth/LoginPage.test.tsx
npm run build
npm run lint -- --quiet
```

Expected: focused tests, TypeScript/Vite build, and lint pass.

- [ ] **Step 3: Check the diff boundary**

Run from the repository root:

```bash
git diff --check
git status --short
```

Only the password-policy files and the committed design/plan documents may be changed by this work; the pre-existing food-preference and camp-survey files must remain untouched.

### Task 5: Build the NAS staging release and create the administrator

**Files/Systems:** NAS `192.168.31.151`, active project `esports-erp-staging`; no database migration.

- [ ] **Step 1: Build and validate the release locally**

From the repository root, run:

```bash
docker compose --env-file .env build api web
docker compose --env-file .env config --quiet
```

If the local Docker daemon is unavailable, do not claim the local image gate passed; continue only after the source/tests above pass and perform the image build on the NAS daemon.

- [ ] **Step 2: Copy only the committed source release to a new NAS release directory**

Preserve the existing NAS `.env`, `secrets/`, and `esports-erp-staging_postgres_data` volume. On NAS, validate the merged Compose files before replacing containers:

```bash
docker compose -p esports-erp-staging \
  -f compose.yaml -f compose.prod.yaml -f compose.nas-staging.yaml -f compose.nas-lan-staging.yaml \
  config --quiet
docker compose -p esports-erp-staging \
  -f compose.yaml -f compose.prod.yaml -f compose.nas-staging.yaml -f compose.nas-lan-staging.yaml \
  build api web
```

- [ ] **Step 3: Restart application services without touching PostgreSQL data**

Run a database count before and after the restart, then apply the existing head migration command only if the release requires it:

```bash
docker exec esports-erp-staging-db-1 psql -U erp -d erp -Atc "select count(*) from user_accounts;"
docker compose -p esports-erp-staging \
  -f compose.yaml -f compose.prod.yaml -f compose.nas-staging.yaml -f compose.nas-lan-staging.yaml \
  up -d api web scheduler proxy
docker ps --filter name=esports-erp-staging --format '{{.Names}} {{.Status}}'
```

- [ ] **Step 4: Create and verify the first admin interactively**

Only after the preflight count is `0`, invoke the existing CLI with the confirmed name, phone, email, identity number, sex, and `1985-02-20` date. Enter the user-provided replacement password at both hidden prompts; never put it in a shell argument or log. Then verify only non-secret fields:

```bash
docker exec esports-erp-staging-db-1 psql -U erp -d erp -P pager=off -c \
  "select p.full_name, ua.email_normalized, ua.status, ua.force_password_change,
          string_agg(r.key, ',' order by r.key)
     from user_accounts ua
     join people p on p.id = ua.person_id
     join user_roles ur on ur.user_id = ua.id
     join roles r on r.id = ur.role_id
    group by p.full_name, ua.email_normalized, ua.status, ua.force_password_change;"
```

Verify the account can log in through the LAN ERP URL, then report the HTTPS/domain status separately if the router still has no 443 forwarding. Do not expose the password in the final report.

### Task 6: Commit the implementation and hand off verification evidence

**Files:** The changed backend/frontend/test files plus the already committed design and plan documents.

- [ ] **Step 1: Review the final diff**

Run:

```bash
git diff --check
git diff -- apps/api/app/security/passwords.py apps/api/app/auth/router.py \
  apps/api/tests/unit/auth/test_passwords.py apps/api/tests/api/test_auth_sessions.py \
  apps/web/src/auth/LoginPage.tsx apps/web/src/auth/LoginPage.test.tsx
```

- [ ] **Step 2: Commit only the policy implementation**

```bash
git add apps/api/app/security/passwords.py apps/api/app/auth/router.py \
  apps/api/tests/unit/auth/test_passwords.py apps/api/tests/api/test_auth_sessions.py \
  apps/web/src/auth/LoginPage.tsx apps/web/src/auth/LoginPage.test.tsx
git commit -m "feat: tighten password complexity policy"
```

- [ ] **Step 3: Report exact evidence**

Include the focused test/build commands and outcomes, the NAS container health status, the non-secret admin role verification, and any checks that were skipped. State explicitly that existing old hashes remain valid and that the password was never logged.
