# ERP security audit repairs implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. This is an authorized repair of existing behavior, not a new-feature design.

**Goal:** Reproduce and repair confirmed authorization, input-domain, housing eligibility and dependency-security defects, then verify and deploy through the standard NAS release gate.

**Architecture:** Preserve existing route/service boundaries, DTOs, role policy, transactional locks and audit semantics. Add local regressions before minimal fixes; use the existing owner-verified disposable database gate, never the NAS or shared test database for destructive tests.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, React, Zod, pytest, Vitest, Playwright.

**Spec:** User instruction on 2026-10-25: 检查系统是否有漏洞并修复 我先睡觉了. The concrete defects below were traced in the current deployed source. No new business rules, accounts, live grants or business data are authorized.

## Global Constraints

- Worktree: `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`; HEAD `62cb1a29b325112af62f164e52477c29f9e1b69d`; preserve existing mixed dirty baseline. No stage/commit/reset/clean/push.
- Only verified bugs and required compatibility changes. No new roles, MFA policy, password changes or financial recalculation.
- Use apply_patch. Keep file ownership disjoint; root serializes ALL database tests and standard deployment. Workers never spawn subagents or touch NAS.
- Python: `/Volumes/MacDev/Projects/new project3/.venv/bin/python` initially. Force current worktree PYTHONPATH for pure tests; remove ambient DATABASE_URL and TEST_DATABASE_URL. Task 5 supplies a separate upgraded test environment, not changes to the shared venv.
- Database tests: existing owned gate only. Never use shared port55433, ambient DSNs, or bypass ledger/live-container/bind validation. Tasks1-3 first write tests, then wait for root RED evidence before production edits.
- Root may run Tasks1-5 concurrently only with disjoint ownership; tests that reset schema are always serialized. This follows the user's existing parallel-development preference.
- Keep authorized super-admin workflows, update-only guardian relationship removal, audit transactions, exact-cent money and half-up procurement rounding working.
- After all local checks and review pass, run this worktree's paired `scripts/deploy-nas.sh --source "$PWD"`; any preflight/backup/restore failure blocks release. Never bypass with direct copies or manual Docker deployment.

## Review Focus

- A delegated global permission manager is not an actual super-admin; two roots must not hide a removal bypass behind last-admin protection (Task1).
- Empty or ordinary-field mutations must not disclose a target denied by people.read; write-only guardian relationship removal remains allowed (Task2).
- Revocation while waiting for a room lock or while rendering a sensitive export must be honored before mutation/return (Task3).
- Pending-cleaning rooms stay unavailable through every entrance, while ready rooms remain selectable (Task3).
- Huge exponents, bigint boundaries and multiplication overflow must be rejected without 500s or writes; package upgrades must not break legitimate forms/images (Tasks4-5).

## Task 1: Protect super-admin role removal

**Files:** Modify `apps/api/app/rbac/service.py`; create `apps/api/tests/api/test_security_rbac_boundaries.py`.
**Interfaces:** Existing `RbacService.remove_role`, same errors and transaction; existing role-assignment API unchanged.

- [x] Write tests: ordinary admin + global permissions.manage, two effective roots; DELETE target super_admin role ->403 permission_delegation_denied, role preserved, reset still403 super_admin_account_protected. Real root removing another with recovery remaining succeeds; self/last protections remain.
- [x] Notify root tests ready; root records owned API RED before production changes.
- [x] Add actual-super-admin check after participant locks/refreshed role lookup for super_admin removal. Preserve other roles, self/last protection and audits. Reject non-root even if target assignment is absent; avoid exposing assignment existence through permission behavior.
- [x] Scoped Ruff/mypy; root runs GREEN and full suite. Report exact paths and evidence; no commits.

## Task 2: Authorize personal data returned by mutations

**Files:** Modify `apps/api/app/identity/router.py`; create `apps/api/tests/api/test_security_people_mutations.py`. Additional service edits only if needed to enforce the same boundary, notify root first.
**Interfaces:** Existing `PersonDetailResponse` and `GuardianResponse` stay unchanged. Endpoints returning these readable projections require their subject's people.read in addition to existing update rights.

- [x] Tests: explicit target people.read deny with update allow; GET403 and PATCH {} / ordinary field patch403 without mutation or allowed audit. For guardian add, deny guardian read ->403 before link/audit; authorized add succeeds. Existing write-only guardian removal regression must stay green.
- [x] Notify root tests ready and await owned RED.
- [x] Enforce target people.read for every PATCH response and guardian subject read before add_guardian returns its profile. Do not add a read requirement to relationship removal, which returns no protected profile.
- [x] Scoped static checks, root GREEN/full suite, report; no unrelated profile/claim changes.

## Task 3: Housing reauthorization and cleaning eligibility

**Files:** Modify `apps/api/app/housing/router.py`, `apps/api/app/housing/person_router.py`; create `apps/api/tests/api/test_security_housing_boundaries.py`. Existing occupancy/export services only if indispensable; notify root before adding ownership.
**Interfaces:** Existing HousingAccessService and live auth recheck patterns; legacy occupancy export response and room-management API unchanged.

- [x] Regressions for single verify, batch verify and delete: permission allowed at precheck then revoked/denied by a separate committed connection during lock wait; after release, no room mutation or allowed audit. Add real independent-connection coverage, not only mocked access decisions.
- [x] Legacy /housing/exports: revoke export permission or disable account during real renderer hook; must not return document bytes or successful export audit. Normal permitted export still succeeds. Reuse the newer room-status export's live auth/recheck pattern.
- [x] Person housing choices exclude pending-cleaning rooms; a sole pending room yields no create_stay choice; after cleaning ready it becomes selectable. Actual occupancy write guard remains unchanged.
- [x] Notify root tests ready and await RED; then replace frozen callbacks with freshly loaded access under locks and recheck legacy export before returning sensitive bytes; filter ready rooms.
- [x] Scoped static checks, root GREEN/full suite, report; no cleanup status or historical occupancy rewriting.

## Task 4: Bound financial amounts safely

**Files:** Modify `apps/api/app/admissions/money.py`, `apps/api/app/procurement/schemas.py` and the existing procurement amount helper if required; create `apps/api/tests/unit/test_security_money_bounds.py` and `apps/api/tests/api/test_security_money_bounds.py`.
**Interfaces:** `parse_cny(str)->int` returns positive signed-bigint cents or InvalidMoney. Procurement computes total with its existing half-up convention; no accepted amount may exceed 9223372036854775807 cents.

- [x] Pure RED: `1e1000000`, nonfinite, oversized decimal strings, 92233720368547758.07 versus .08, preserved normal exact-cent/scientific notation semantics; quantity-times-price beyond bigint rejected with ValidationError, normal rounding unchanged. Bound processing before potentially huge arithmetic/int conversion.
- [x] API RED for malformed amounts must return422, zero payment/purchase/audit writes; root serializes DB portion.
- [x] Minimal finite/bound validation and DecimalException handling; catch at proper domain boundary, not blanket route exceptions. No arbitrary lower business monetary ceiling or data migration.
- [x] Run pure GREEN, scoped static checks, root API/full suite; report.

## Task 5: Security dependency upgrades

**Files:** Modify `apps/web/package.json`, `apps/web/package-lock.json`, `apps/api/pyproject.toml`; compatibility tests only when a real application break appears. Own task-local dependency environment and manifest artifacts, not shared venv or global npm config.
**Interfaces:** Same production API/runtime behavior; Python3.12 remains supported. Root passes upgraded interpreter to owned gates.

- [x] Record npm audit RED for Zod GHSA-mrhx-c7xc-m3w5 (<4.6.6), and Pillow12.3 security advisories from official Pillow13 release notes. Distinguish browser-side dependency risk from server RCE claims; no exploit against NAS.
- [x] Upgrade Zod to fixed compatible4.x (minimum4.6.6) with lockfile; Pillow to >=13.0,<14. Review official removal notes against actual usage. Update direct dev httpx2 minimum to2.12 if upstream advisory confirms, preserving <3. No unrelated blanket upgrades.
- [x] Create separate Python3.12 venv under task workspace and install project dev/runtime requirements from official packages; include fixed audit-tool transitives where needed without putting audit-only dependencies into production. Existing shared .venv remains untouched.
- [x] npm production audit must show0; audit actual NAS production inventory separately to identify production-only affected packages. Rerun image validation/normal JPEG PNG WebP tests, full frontend2955+ tests/lint/build and new dependency compatibility. Report remaining development-tool warnings honestly.
- [x] Hand root upgraded interpreter path; no DB tests or deployment by worker.

## Task 6: Integration, independent review and NAS publication

**Files:** `apps/api/app/cli/departure_e2e_runtime.py` fixed API_TESTS may permanently include the new audit test files plus existing regression files; preserve all ownership guards. Verification report `docs/superpowers/verification/2026-10-25-security-audit-repairs.md`.

- [x] Capture each file's pre-task bytes for scoped review; record existing baseline9031 backend+2955 frontend+12browser from2026-10-24, fresh fullRuff baseline passed. No old green is final evidence for new code.
- [x] Run owned API RED with test-only changes for Tasks1-3, then authorize fixes; Task4 pure RED can proceed separately. API_TESTS fixed extension is permanent, not a temporary selector or guard bypass.
- [x] Read per-task reports and obtain independent spec/quality reviews from baseline diffs. Fix real findings with original worker, no review of unrelated dirty baseline.
- [x] Run fresh owned all gate (full backend+real browser), full frontend/lint/build, Ruff/mypy. No DB missing skips accepted; report existing opt-in drills separately.
- [x] Standard pairedNAS deployment with source snapshot, encrypted backup+restore; verify final complete state, matching active release, schema head, service/scheduler health and external HTTPS. Only low-risk readonly production checks, no test accounts/room mutations.
- [x] Report fixed issues, actual counts, residual risks and release status. Retain task evidence since no commits or mixed-worktree cleanup are authorized.
