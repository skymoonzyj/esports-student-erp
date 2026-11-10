# Desktop FPS Triad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace new desktop FPS rounds with three one-minute tasks while preserving all historical versions and deploying safely to NAS.

**Architecture:** Add pc-fps-v4 with new IDs/raw kinds, exact version-aware membership and server scoring. Keep legacy engines/contracts immutable; wire independent v4 engines and report/rank presentation through existing authenticated workflows.

**Tech Stack:** Python/FastAPI/Pydantic/SQLAlchemy/PostgreSQL; TypeScript/React/Zod/Three.js/Vitest/Playwright.

**Spec:** docs/superpowers/specs/2026-11-08-pc-fps-triad-design.md

## Global Constraints

- Only pc-fps defaults to v4; other suites remain v3. Old v1-v3 pending, sessions, reports and configs remain valid and unchanged.
- New IDs gridshot/sixshot/spheretrack90; all four rule versions pc-fps-v4; 60000ms formal duration.
- Existing permissions and super-admin-only preview availability stay unchanged.
- Preserve unrelated dirty worktree edits. Do not commit, reset, stage unrelated files, deploy, or run DB tests from workers. Controller serializes database tests and deploys through scripts/deploy-nas.sh with SSH port 202.
- Formal grades/scoring derive on server, practice not ranked, quality-invalid rounds excluded, old/new rules isolated.

## Review Focus

1. Old four-item pending cache must not be cleared by new three-item validation (Task 2).
2. Rapid mouse input/held buttons/timestamps at 60000ms must not duplicate shots or vary with rendering cadence (Task 3).
3. Edge-pitch and extreme-aspect spawns must stay visible/nonoverlapping or fail preflight clearly (Task 3).
4. Third-result save must finish exactly once, preserve old four-result completion, and not expose cross-user IDs in top-three values (Task 1).
5. Recovery of old attempts must use frozen server config rather than current task menu; new UI must not say four items (Task 2).

## Shared interfaces

- Backend `members_for_catalog(suite_id, catalog_version=None)` returns frozen exact tuple; versionless recovery uses separately named all-supported membership.
- Frontend `suiteMembersForCatalog(suiteId, catalogVersion)` exact members; `isSupportedSuiteTest(suiteId,testId)` accepts historical IDs for versionless recovery. `suiteMembers` may remain historical map for fixture compatibility; live menus use server suite.test_ids.
- `multi_target` config/raw and `spheretrack90` config/raw exactly as spec. `spheretrack90` params include existing TrackingParams fields (kind differs): horizontal_fov_deg=90, pitch_limit_deg=70, fire_interval_ms=200, target_diameter_deg=3*size, speed_deg_s=30*speed, horizontal_bound_deg=40, vertical_bound_deg=15, direction_interval_ms=2000, turn_ms=200.
- Ranking adds optional/default-empty `top_scores` to overall view and each RankingItem: array max3 of `{rank:number, score:number, gap:number}` (gap=competitor score minus displayed subject score). For new v4 fill from real eligible participants; old response shape still accepted by frontend. No identity fields. Order descending score with deterministic tie order, preserve competition ranks.

### Task 1: Backend versioned contracts, scoring, completion and anonymous ranking

**Files:** apps/api/app/esports_assessments/{config_schemas,rule_versions,catalog,raw_schemas,schemas,query_schemas,scoring,reporting,formal_service,practice_service,ranking,ranking_schemas}.py; app/audit/service.py; related tests under tests/unit/esports_assessments and tests/api or tests/services.
**Interfaces:** Produces canonical get_suite/get_task pc-fps-v4 and JSON fixtures for frontend. Add helper members_for_catalog; keep legacy MEMBERS consumers correct.

- [ ] Write failing tests: current pc-fps has only three new IDs; explicit v1-v3 unchanged; other suites unchanged; illegal ID/version combos rejected; multi_target empty accepted, duplicate/negative/60000/too-fast shots rejected; score 150 hits/150 shots grid=50, 150hits/300shots grid=25, zero=0, all60000ms tracking=100.
- [ ] Run pure tests RED. Add strict raw and frozen config contracts; use exact rational rounding and version-specific report labels/advice.
- [ ] Replace fixed-four completion/report validators with exact frozen-member counts, including query/recovery and audit safelist. Add DB tests (collect only initially) for third-save report/idempotency, old fourth-save still valid, mixed catalog never ranks.
- [ ] Add bounded SQL anonymous top3 for v4 grouped as existing best complete round; tests for ties, <3 people, excluded warned/incomplete/other-version rows and no identities. Other suites' ranking behavior unchanged.
- [ ] Run scoped pure tests/Ruff/mypy; produce canonical v4 fixture at apps/web/src/esportsAssessments/testSupport/catalog-v4.json (coordinate with Task 2). Report changed files and precise test results. No commit in dirty checkout.

### Task 2: Frontend contracts, menus, recovery, report/rank UI

**Files:** apps/web/src/esportsAssessments/{contracts,versionRules,apiContracts,hostState,reportPresentation,AssessmentHome,AssessmentSessionPage,PracticePage,AssessmentHost}.* and ranking UI/contracts shared module as needed; corresponding tests and testSupport v4 helper.
**Interfaces:** Consumes canonical v4 JSON, new raw kinds/config and helpers listed above. Do not edit fps engines/suites or engineRuntime/types (Task 3 ownership).

- [ ] Add failing tests for v4 contract validation, old v1-v3 pending recovery, legacy practice recovery and new three-item completion UI.
- [ ] Implement strict mirrored schema/parameter checks, new labels/instructions and server-members-driven task choices. Keep historical fixtureCurrentConfig frozen, add fixtureV4Config rather than changing old fixtures.
- [ ] Add version/metric notes and three-item progress; display anonymous top3 scores/gaps with insufficient-participant state only for v4; old ranking responses remain accepted.
- [ ] Add new-mode HUD instructions as needed without editing stage/renderer: gridshot/sixshot click once per shot, tracking no-fire coverage; score disclaimer, old/new history note.
- [ ] Run scoped Vitest and ESLint. Report tests and changes; do not deploy/commit.

### Task 3: Independent v4 FPS engines and visual feedback

**Files:** apps/web/src/esportsAssessments/fps/{multiTarget,multiTarget.test,spheretrack90,spheretrack90.test}.ts; fps/{schedule,FpsStage,renderer,index} and tests only as necessary; suites/pcFps.ts and tests; engineRuntime/types.ts if adding feedback.
**Interfaces:** Consumes TaskConfig union with multi_target/spheretrack90 and existing GameEngine/FpsSceneSnapshot/InputAction. Export createMultiTargetEngine and createSpheretrack90Engine; route only new IDs. Do not alter frozen legacy engines' behavior.

- [ ] Add RED tests with real new-mode configs for initial 3/6 targets, hit replaces exactly one, miss preserves targets, hold does not autofire, 50ms minimum gap and strict finish cutoff, frozen repeatable finish.
- [ ] Implement current-view deterministic grid candidate selection and visible/nonoverlap filtering with full sphere frustum checks and pitch constraints; tests rotated ±70pitch/wrapped yaw, 16:9, ultrawide and insufficient viewport fail-closed.
- [ ] Implement deterministic 120Hz bounded tracking with per-input integration, 60s coverage and reacquisition; green/red plus text on-target indication, no fire required. Tests chunk/render-rate invariance and stationary/no-input result.
- [ ] Preserve v1-v3 routing and mobile unchanged, add preflight branches and minimal existing visual integration. Run FPS suite and ESLint; report no DB/deploy/commit.

### Task 4: Integration, independent review, browser acceptance and release

**Files:** Tests/e2e as necessary; docs/superpowers/reports/2026-11-08-pc-fps-triad.md. Controller ownership.

- [ ] Review each task against spec and code, resolve integration failures without erasing unrelated changes.
- [ ] Run full frontend tests/build/lint, affected backend suite then broader/full backend tests serially, Ruff/mypy and schema checks.
- [ ] Run isolated browser desktop FPS checks (not production score writes): real Pointer Lock, all three scenes, completion and historical compatibility. Report any hardware/focus blocker honestly.
- [ ] Independent whole-change review; address important findings.
- [ ] Run mandatory scripts/deploy-nas.sh --source this source directory with NAS_SSH_PORT=202. Verify active release/health and public asset response; no manual deploy bypass.
- [ ] Record exact verification and limitations, then hand off for super-admin test; keep availability unchanged.
