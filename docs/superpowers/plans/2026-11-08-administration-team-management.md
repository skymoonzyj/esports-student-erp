# Administration Team Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give administration, including Gu Yan through her existing administration role, the confirmed team-business management scope.

**Architecture:** Seed and migrate five existing global permissions; retain live role checks and deny-first resource authorization while replacing superadmin-only business gates with administration-or-superadmin capability gates. Reuse existing team and catalog UI without granting broad teaching/project/financial access.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL, Alembic, React, TanStack Query, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-08-administration-team-management-design.md`

## Global Constraints

- Only five new administration global grants: `professional_teams.manage`, `team_memberships.read`, `team_memberships.manage`, `team_memberships.transfer`, `team_manager_assignments.manage`.
- Never grant `projects.manage`, `teaching.read`, finance, commission, superadmin, or account-role management as a side effect.
- Preserve explicit denies, live role/account checks, existing manager/staff team scope, staff role-source validation, inactive-team rules, locks, idempotency and audit.
- No real business data writes, no hard-coded Gu Yan ID/name, no direct production DB permission updates.
- Preserve the dirty detached worktree, no commit/reset/checkout. Deploy only through `scripts/deploy-nas.sh --source` with SSH 202 after verification.
- Only one disposable PostgreSQL test process at a time; workers coordinate the database lease with root. No NAS/browser work by workers.

## Review Focus

- An administrative team-scoped deny hides both list/detail and blocks source/destination mutations (Task 1).
- Removing administration or disabling account during a stale request must deny further writes (Task 1).
- Staff assignment must not manufacture account roles or activation authority (Task 1).
- Upgrade/downgrade preserves user overrides and unrelated grants, including explicit finance denies (Task 2).
- Catalog-only administration cannot reach broad teaching/project editor functions or accidentally call inaccessible teaching endpoints (Task 3).

### Task 1: Team business authorization and catalog API

**Files:** `apps/api/app/team_management/access.py`, `person_staff_service.py`, necessary team query/services; `apps/api/app/teaching/router.py`; focused `tests/services/test_team_access.py`, `test_team_memberships.py`, `test_team_staff.py`, `test_team_queries.py`, API team/catalog tests.

**Interfaces:** Keep `TeamContext`, `TeamAction`, existing API payloads stable. Administration with explicit test grants can use global team operations. Only project-list and professional-team-list GET routes may accept `professional_teams.manage` as an alternative to `teaching.read`.

- [ ] Add failing actual DB tests for administration read/manage/transfer/assign managers and catalog read/write, with explicit five grants before Task 2 exists.
- [ ] Prove RED, then implement administration-or-superadmin live actor scope with per-action permission filtering and deny precedence. Keep ordinary manager/staff gates unchanged.
- [ ] Cover scoped/global denies, removed role, disabled account, unassigned person visibility, existing staff role source and no automatic account role grants.
- [ ] Run focused regression, Ruff/Mypy; report exact changed files and TDD evidence. Do not commit.

### Task 2: Administration grants and release migration

**Files:** `apps/api/app/rbac/seed.py`; new `apps/api/migrations/versions/0090_administration_team_management.py`; new `tests/db/test_administration_team_migration.py`; `tests/db/test_rbac_seed.py`, `test_migrations.py`; `scripts/db-schema-manifest.sh` and existing current-head tests.

**Interfaces:** Migration revision `0090_administration_team_mgmt` follows `0089_quick_intake_housing`. No new tables. Exactly the five global role permissions from Global Constraints. Keep historical migration semantics stable if current seeds are imported.

- [ ] Add a failing seed/upgrade behavioral regression before changing grants; coordinate DB test lease.
- [ ] Add minimal idempotent role grants to current seed and migration. Preserve existing overrides, scope-specific permissions and unrelated roles; downgrade only reverses migration-owned changes where existing provenance patterns allow.
- [ ] Update deployment manifest/head expectations and prove upgrade/downgrade behavior, seeded behavior and unrelated financial deny preservation.
- [ ] Run focused regression and lint; report exact files. Do not commit.

### Task 3: Reachable frontend team catalog

**Files:** `apps/web/src/teams/TeamManagementPage.tsx`, related tests; `apps/web/src/teaching/TeachingCatalogPage.tsx`, related tests; `apps/web/src/app/router.tsx`, related tests; other directly related navigation files only if necessary.

**Interfaces:** Existing `TeamContext.can_manage_catalog` enables catalog link; user with global `professional_teams.manage` can access catalog without `teaching.read`. Default catalog-only users to team view, preserve project mutation and division restrictions.

- [ ] Write failing tests for non-superadmin administration catalog entry, actual new/edit team dialog availability, no project/division actions and unchanged coach restrictions.
- [ ] Implement minimal capability-driven entry and catalog-only view. Do not leak broad teaching navigation or fetch unnecessary teaching data.
- [ ] Run focused tests, frontend full suite/lint/build; report evidence. Do not commit.

### Task 4: Integration, review and release

- [ ] Root runs combined database tests serially including team, staff, intake, RBAC, teaching and migrations; full default backend suite plus full Ruff/Mypy.
- [ ] Independent review covers spec and quality using per-task baseline diffs (not distant HEAD), fix material findings.
- [ ] Run mandatory NAS deployment, wait for encrypted backup and isolated restore verification, verify head and container health.
- [ ] Read-only normal UI recheck Gu Yan's live effective team grants and unchanged payment/refund/commission denies. No production account impersonation or demo mutations.
- [ ] Record actual verification outcomes and report deployment status separately from code completion.
