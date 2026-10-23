# Task 2 and Task 7 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans`, `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and `superpowers:verification-before-completion`. This shared worktree is already dirty: do not stage, commit, reset, clean, rebase, merge, or overwrite unrelated edits.

**Goal:** Produce fresh evidence that the existing daily/weekly teaching-report and family-published-report feature is accepted locally, and complete the missing Stage 2 plus local Docker/Compose delivery gates without deploying to NAS.

**Architecture:** Preserve the approved report data model and routes. Add an umbrella Stage 2 launcher that invokes the existing protected system-evaluation, teaching-report, and camp-survey four-viewport journeys with one safe interface and a machine-readable redacted summary. Make production API image contents explicit and testable against every runtime package imported by `app.main`/`app.api.router`; add a protected local container acceptance launcher that validates only a disposable Compose E2E stack. NAS deployment remains documentation-only.

**Tech Stack:** Bash, Python/Pytest, Alembic/PostgreSQL, Node/Vitest/Playwright, Docker Compose, Nginx, Ruff, mypy, ESLint, TypeScript.

---

## Frozen requirements and boundaries

- Task 2 already exists in `app.evaluations.report_*`, `TeachingReportsPage`, `TeachingReportReviewPage`, and `PublishedReportsPanel`; do not redesign dates, permissions, state transitions, or family visibility.
- Task 7 has three distinct claims: local non-container Stage 2 accepted, local disposable Compose stack accepted, and NAS/production deployed. This plan can complete only the first two.
- Every data-writing command requires `app.cli.e2e_database_safety` and an isolated PostgreSQL URL ending in `_test` or `erp_e2e`; no real NAS/production data may be read, migrated, seeded, or deleted.
- Do not inspect, migrate, or delete old score-tool `data/`, `uploads/`, `runtime/`, personal screenshots, or video files.

## Task 1: Reconcile the report acceptance contract

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-10-teaching-report-workspace-contract-design.md`
- Modify: `docs/superpowers/plans/2026-08-09-teaching-reports.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write a failing documentation truthfulness assertion.** Require README to say that daily/weekly reports are locally implemented but awaiting fresh Stage 2 evidence; forbid current-tense statements that reports are unavailable; require the workspace contract to use the same state.

```python
assert "教学日报、周报、审核发布与家庭已发布查看已本地实现" in readme
assert "每日/每周评语或录像分析" not in current_unavailable_section
assert "- **状态：** 本地实现完成，等待本轮完整验收" in workspace_contract
```

- [ ] **Step 2: Observe RED.**

```bash
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_operations_docs.py -q
```

- [ ] **Step 3: Apply only the status corrections.** Update the three stale README references, replace the workspace-contract pending-review status, and change the old teaching-report plan’s fixed worktree path into an example rather than an authority.

- [ ] **Step 4: Verify GREEN.** Repeat the focused documentation test and require exit 0.

## Task 2: Add the Stage 2 umbrella acceptance launcher

**Files:**

- Create: `scripts/run-stage2-local-acceptance.sh`
- Create: `scripts/seed-stage2-acceptance-e2e.py`
- Create: `apps/api/app/cli/seed_stage2_acceptance_e2e.py`
- Create: `apps/api/tests/cli/test_seed_stage2_acceptance_e2e.py`
- Create: `apps/api/tests/cli/test_stage2_acceptance_launcher.py`
- Create: `apps/web/tests/stage2-local-acceptance.spec.ts`
- Create: `docs/operations/stage2-local-acceptance.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write failing seed and launcher tests.** Test a deterministic combined seed that composes the canonical system-evaluation, teaching-report, and camp-survey fixtures without emitting credentials or media paths. Test strict Bash mode, database safety before all mutations, all secret requirements, one service lifecycle, a `0700` temporary directory, a redacted JSON summary, and `trap cleanup EXIT`.

```python
assert "set -euo pipefail" in source
assert "-m app.cli.e2e_database_safety" in source
assert "system-evaluation-processing.spec.ts" in source
assert "teaching-reports.spec.ts" in source
assert "camp-evaluation-surveys.spec.ts" in source
assert "stage2-local-acceptance.spec.ts" in source
assert "stage2-local-acceptance-summary.json" in source
assert "trap cleanup EXIT" in source
```

- [ ] **Step 2: Observe RED.**

```bash
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_seed_stage2_acceptance_e2e.py apps/api/tests/cli/test_stage2_acceptance_launcher.py -q
```

- [ ] **Step 3: Implement the combined seed, cross-module spec, and protected launcher.** In each viewport run the existing system-evaluation, teaching-report, and camp-survey specs with their own fresh canonical fixture, then rebuild a single database containing all three domain fixtures and run the cross-module workspace/privacy smoke. Keep the score-tool Web and Worker alive for the whole runner. Write only spec names, timestamps, Git SHA, exit status, and cleanup status to the summary.

- [ ] **Step 4: Document it.** Include the exact command, inherited four-viewport coverage, test-data boundaries, summary schema, cleanup behavior, and Docker/NAS/GPU/data-migration deferrals.

- [ ] **Step 5: Verify GREEN.**

```bash
bash -n scripts/run-stage2-local-acceptance.sh
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_stage2_acceptance_launcher.py apps/api/tests/cli/test_operations_docs.py -q
```

## Task 3: Make the production API image contain the actual runtime

**Files:**

- Modify: `apps/api/Dockerfile`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Extend the existing production-image test.** Require `evaluations`, `food_preferences`, `housing`, and `registration`, plus every package imported by `app.api.router`; continue forbidding broad `COPY app ./app` and E2E-only seed modules in the production stage.

```python
for package in ("evaluations", "food_preferences", "housing", "registration"):
    assert f"COPY app/{package} ./app/{package}" in production
assert "COPY app ./app" not in production
assert "seed_system_evaluation_e2e.py" not in production
```

- [ ] **Step 2: Observe RED.**

```bash
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_operations_docs.py::test_production_image_contains_admissions_runtime_but_not_e2e_seed -q
```

- [ ] **Step 3: Add only explicit production COPY directives.** Copy the runtime packages and required CLI modules directly. Keep E2E-only seed modules excluded and leave base/development/e2e stages unchanged.

- [ ] **Step 4: Verify GREEN.** Repeat the focused image-content test and require exit 0.

## Task 4: Add a disposable local Compose acceptance launcher and guide

**Files:**

- Create: `scripts/run-local-container-acceptance.sh`
- Create: `apps/api/tests/cli/test_local_container_acceptance_launcher.py`
- Create: `docs/operations/local-container-acceptance.md`
- Modify: `README.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write failing launcher tests.** Freeze support for `docker compose` or `docker-compose`; require client/daemon checks before mutation, a unique `esports-erp-container-acceptance-*` project name, free loopback-only HTTP/PostgreSQL ports, `config --quiet` before `up`, `up --build --wait`, migration inside API, proxy/API health checks, schema manifest, and `down --volumes --remove-orphans` in cleanup.

- [ ] **Step 2: Observe RED.**

```bash
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_local_container_acceptance_launcher.py -q
```

- [ ] **Step 3: Implement the launcher.** Use `compose.yaml` with `compose.e2e.yaml`, never the production overlay; pass only E2E keys; refuse non-loopback bind values; collect redacted health/migration/schema output; do not run browser tests inside containers.

- [ ] **Step 4: Document local-only delivery.** State Docker Desktop/daemon prerequisite, full command, cleanup guarantee, and explicit deferral of NAS deployment, external TLS, production secret rotation, real backup/restore, and data migration.

- [ ] **Step 5: Verify GREEN.**

```bash
bash -n scripts/run-local-container-acceptance.sh
docker-compose -f compose.yaml -f compose.e2e.yaml config --quiet
PYTHONPATH="$PWD/apps/api" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/cli/test_local_container_acceptance_launcher.py apps/api/tests/cli/test_operations_docs.py -q
```

## Task 5: Run the report and final acceptance gates

**Files:**

- Modify only for an observed failure: the smallest relevant source/test file.

- [ ] **Step 1: Run the complete report PostgreSQL gate.**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" "$PWD/.venv/bin/python" -m pytest -p no:cacheprovider apps/api/tests/unit/evaluations/test_report_periods.py apps/api/tests/unit/evaluations/test_report_state.py apps/api/tests/db/test_teaching_report_models.py apps/api/tests/db/test_teaching_report_concurrency.py apps/api/tests/services/test_teaching_report_access.py apps/api/tests/services/test_teaching_report_queries.py apps/api/tests/services/test_teaching_report_workspace_queries.py apps/api/tests/services/test_teaching_reports.py apps/api/tests/api/test_teaching_reports.py apps/api/tests/cli/test_seed_teaching_reports_e2e.py -q
```

- [ ] **Step 2: Run fresh four-viewport report evidence.**

```bash
bash scripts/run-teaching-reports-e2e.sh
```

- [ ] **Step 3: Run the umbrella Stage 2 launcher.**

```bash
bash scripts/run-stage2-local-acceptance.sh
```

- [ ] **Step 4: Run local container acceptance only after Docker daemon is reachable.**

```bash
bash scripts/run-local-container-acceptance.sh
```

- [ ] **Step 5: Run final static and diff checks.**

```bash
"$PWD/.venv/bin/python" -m ruff check apps/api
"$PWD/.venv/bin/python" -m mypy apps/api/app
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
git diff --check
git status --short
```

**Completion standard:** fresh report database gate, teaching-report four-viewports, umbrella Stage 2 runner, local container acceptance, static gates, documentation checks, and independent reviews all pass. If Docker daemon evidence is absent, only report local non-container acceptance; never call the system NAS or production ready.
