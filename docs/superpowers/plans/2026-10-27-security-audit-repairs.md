# ERP Security and Reliability Audit Repairs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce and repair the confirmed audit findings without expanding business permissions or changing production business records.

**Architecture:** Retain existing session, RBAC, transaction and response contracts. Revalidate identity and operation scope after blocking locks, invalidate obsolete recovery challenges, and bind asynchronous UI updates to the originating entity. Use existing isolated PostgreSQL/browser test infrastructure and the standard NAS release script.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, React/TypeScript, pytest and Vitest.

**Spec:** User's 2026-10-27 instruction to audit and directly repair this ERP; scoped findings and acceptance criteria below are the repair specification.

## Global Constraints

- Preserve all preexisting dirty changes; do not stage, commit, reset, or push.
- Do not change account roles, production financial/personnel records, or existing product permission policy.
- No production brute-force, stress, destructive tests, or real personal data in regression fixtures.
- Root schedules PostgreSQL tests serially against a newly created disposable database; workers must request a DB test slot.
- Use apply_patch for source edits. No dependency upgrades without a verified need.
- Complete applicable checks and invoke scripts/deploy-nas.sh --source with the current release worktree; failed release gates are blockers.

## Review Focus

- Revoked sessions, disabled accounts and expired grants during lock waits must prevent writes and successful audit entries.
- Old password-reset codes must not regain control after a newer password change, including concurrent issuance.
- Concurrent enrollment creation or movement must not bypass scope or invert established financial locks.
- Read-denied users must not obtain protected details through a successful write response.
- Late UI responses and partially successful multi-request saves must not leak credentials or duplicate records.

---

### Task 1: Password recovery lifecycle and CPU cost

**Files:** apps/api/app/auth/service.py, apps/api/app/registration/verification_service.py and focused password recovery/account service tests.
**Interfaces:** Existing PasswordService.hash, password-change/reset services and verification challenge lifecycle; no response schema changes.

- [x] Add failing tests proving invalid, expired, exhausted, wrong-purpose and wrong-code completion requests do not hash a password; a valid completion hashes once.
- [x] Add failing regressions for older reset codes after ordinary change, admin reset, and a newer successful recovery, including issuance/change serialization.
- [x] Run focused tests RED through root; move expensive hashing after verified authorization and invalidate obsolete reset challenges atomically using existing lock order.
- [x] Run focused tests GREEN, static checks, and provide the changed-file list and evidence for review.

### Task 2: Housing authorization and legacy visitor student identity

**Files:** apps/api/app/housing/occupancy_router.py, apps/api/app/identity/assignments/service.py, tests/api/test_security_housing_boundaries.py and tests/api/test_person_assignments.py (new focused files allowed).
**Interfaces:** Existing AuthContext and locked room authorization; existing legacy-student readiness policy.

- [x] Add real transaction tests for session revocation/account disable while a stay/transfer/checkout request waits for the room lock; assert no write/version/success audit.
- [x] Add a complete legacy visitor identity regression equivalent to the supported complete legacy staff path; incomplete or minor cases must retain readiness protections.
- [x] Run RED; revalidate the live account/session at the established locked authorization boundary. Apply legacy readiness consistently without auto activation or privilege grants.
- [x] Run GREEN and static checks; review all occupancy mutations using the helper.

### Task 3: Financial scope and lock-context consistency

**Files:** apps/api/app/admissions/payment_router.py, payment_service.py and focused payment/concurrency tests.
**Interfaces:** Existing permission policy, operation idempotency, cohort/enrollment/case lock order.

- [x] Add regression for class movement from permitted A to denied B while payment/refund waits; expect denial and no money/audit/ledger mutation.
- [x] Add deterministic independent-session regression for first enrollment appearing while a payment waits for case lock; forbid deadlock or uncaught 500.
- [x] Run RED; validate authorization against refreshed locked context and detect changed lock context before taking out-of-order locks. Use a safe retry or explicit existing-style conflict, never retry inside an aborted transaction.
- [x] Run GREEN plus payment/refund/revenue-share regressions; preserve idempotency and exact monetary behavior.

### Task 4: Asset mutation confidentiality and live authorization

**Files:** apps/api/app/asset_management/router.py, service.py and focused asset API/service tests.
**Interfaces:** Existing assets.read/manage/assign, live actor locking and lease-operation authorization helpers.

- [x] Add regression: assets.manage with explicit assets.read deny cannot receive full details from no-op PATCH or equivalent full-detail mutation.
- [x] Add session/account/grant revocation after asset-lock wait tests for ordinary update, assignment and return.
- [x] Run RED; require existing read permission for protected full-detail responses before mutation and apply consistent live reauthorization without lock inversion.
- [x] Run GREEN, photo/custody/lease/export regression and static checks; do not broaden administrator asset rights.

### Task 5: Frontend stale credentials and partial asset save

**Files:** apps/web/src/people/PersonDetailPage.tsx, apps/web/src/assets/AssetsPage.tsx and their focused tests.
**Interfaces:** Existing routeVersion/entity identifiers and create/upload API calls.

- [x] Test delayed account creation success and failure after navigating from A to B; no A credential or stale state may appear on B.
- [x] Test asset create success plus first photo upload failure; retry uploads to the existing ID and never calls create twice, with clear partial-success UI.
- [x] Run Vitest RED, implement scoped response guards and recoverable partial save, run GREEN and ESLint.
- [x] Verify edit/cancel/reopen paths after partial save and unchanged normal success behavior.

### Task 6: Permission override and private download revocation

**Files:** apps/api/app/rbac/service.py, apps/api/app/travel_reports/router.py and targeted permission/travel tests.
**Interfaces:** Existing live session authentication and permission policy; unchanged download response format.

- [x] Add ordinary permission override deletion regression when manager authority is revoked during target-lock wait; no deletion or allowed audit.
- [x] Add converted/original report download regressions for export/session revocation during file retrieval/render; no bytes or success audit.
- [x] Run RED; reauthenticate and reauthorize at final locked/data-return boundaries with current time and refreshed identity state.
- [x] Run GREEN and related API regressions, static checks.

### Task 7: Integration, independent review and NAS release

**Files:** verification report and task-local evidence only, plus any review-directed corrections in the owned modules.

- [x] Capture pre-edit source baseline and independently review each task diff for scope and correctness.
- [x] Audit exact runtime Python packages and npm lock against current public advisories; distinguish tool failures from clean results.
- [x] Run combined focused regressions, full backend owned PostgreSQL gate, frontend test/lint/build and relevant browser checks.
- [x] Resolve material review findings, retain honest skipped/unverified limitations, and run the mandatory NAS deployment script.
- [x] Verify deployed release identity, service health, encrypted backup/restore evidence and public HTTPS without production mutation tests.
