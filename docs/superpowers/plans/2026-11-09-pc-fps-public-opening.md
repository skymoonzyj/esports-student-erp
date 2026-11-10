# PC FPS Public Opening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the already verified desktop FPS suite to accounts with existing participation permissions.

**Architecture:** Change the existing server and client release gates together. Preserve all authorization, catalog versions and assessment engines.

**Tech Stack:** FastAPI, PostgreSQL, React, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-pc-fps-public-opening-design.md`

## Global Constraints

- Open only `pc-fps`; leave `mobile-fps`, `pc-moba`, `mobile-moba` closed to ordinary users.
- Preserve self read plus self take authorization, explicit denies, live account checks and superadmin preview.
- No role grants, migrations, scoring changes or production assessment writes.
- Preserve all pre-existing dirty changes; no commits, staging, reset or worktree cleanup.
- NAS publication must use `scripts/deploy-nas.sh --source "$PWD"`, SSH port 202.

## Review Focus

- Default student can see and start the released v4 suite without a test-only availability override.
- Closed suites remain unavailable to ordinary users but superadmin preview remains usable.
- Accounts without participation permission and explicit self denies still cannot write.
- Frontend uses actual release configuration, and a server refusal still wins over local availability.
- Existing fullscreen/score/report behavior and historical catalog handling remain untouched.

### Task 1: Open PC FPS and verify release boundaries

**Files:**
- Modify: `apps/api/app/esports_assessments/catalog.py`
- Modify: `apps/web/src/esportsAssessments/availability.ts`
- Modify: `apps/web/src/esportsAssessments/AssessmentHome.tsx`
- Test: `apps/api/tests/unit/test_esports_catalog.py`
- Create: `apps/api/tests/api/test_esports_public_opening.py`
- Test: `apps/web/src/esportsAssessments/pending.test.ts`
- Test: `apps/web/src/esportsAssessments/AssessmentHome.test.tsx` or a separate real-availability home test if existing mocks obscure the gate.

**Interfaces:**
- Consumes: existing `AVAILABLE_SUITES`, `ASSESSMENT_SUITE_AVAILABILITY`, context/suites/create/practice routes and existing test helpers.
- Produces: existing APIs and UI expose only `pc-fps` to ordinary authorized users, with no new interfaces.

- [x] Write behavioral release regressions using real availability (no `esports_enabled` fixture), covering student context/catalog and formal/practice creation, other suites denied, missing permission and explicit deny, superadmin preview. Replace outdated all-closed assertions without removing closed-suite regressions.
- [x] Run new regressions before implementation; record expected closed-gate failures, not fixture/import errors.
- [x] Set backend `AVAILABLE_SUITES = frozenset({"pc-fps"})`, frontend `pc-fps: true`, and clarify that only unopened suites are superadmin preview. Keep other behavior unchanged.
- [x] Run focused backend/frontend regressions, lint/type checks and build. Root runs broader checks once after the implementer freezes files; only one process may use the isolated test database.
- [x] Independent task and final review of this incremental change, followed by standard NAS deployment and read-only live verification.

Execution is authorized by the user's direct request to open the existing feature; no additional design choice or role expansion is being introduced.
