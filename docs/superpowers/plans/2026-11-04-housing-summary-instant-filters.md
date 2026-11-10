# Housing summary and instant filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct room/person/bed counters and make sidebar filters immediate while summary shortcuts affect only the room list.

**Architecture:** Preserve the authorized room projection. Compute summary after base/sidebar filters, then apply an independent quick_filter before pagination. Retain legacy filter semantics and permission boundaries; React keeps filter controls mounted while safely refreshing private results.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, React/TanStack Query, Vitest, Playwright.

**Spec:** docs/superpowers/specs/2026-11-04-housing-summary-instant-filters-design.md

## Global Constraints

- 左侧条件改变统计范围；顶部点击仅改变列表，不改左侧条件或其他数字。
- All counts are authorization scoped. No occupancy/role/capacity mutations, no database migration.
- Preserve legacy occupied_rooms as a room count and unassignable_rooms/unassignable as broad non-assignable semantics. Add full_rooms and assignability=full instead of redefining old fields.
- Shared quick_filter wire values: occupied, vacant, assignable, full, problem, pending_verification, pending_cleaning, usable, remaining, single, double, triple, custom, functional. Omission means no shortcut.
- Frontend property quickFilter serializes as quick_filter. Both export request types also accept optional quick_filter and full assignability; existing omitted requests retain prior behavior.
- No commit/push/reset. Preserve existing dirty source and snapshot pre-edit bytes. Only root deploys via scripts/deploy-nas.sh, SSH202, after local checks.
- Only one disposable database consumer at a time. No production test records, real account manipulation or new data imports.

## Review Focus

- Old shortcut conditions must not accumulate, especially vacant→assignable losing partly occupied rooms; Task2 real request assertions.
- Permission/account revisions and out-of-order responses must not leak stale private summaries or actions; Tasks1/2 scoped authorization and deferred-response tests.
- Instant search must preserve focus, whitespace, IME, and mobile sheet state while queries resolve; Tasks2/3 real typing flows.
- Bed numerator and denominator must use the same valid-room set while total people retain anomalous problem-room residents; Task1 literal fixture assertions.
- Export must not silently ignore active shortcuts or reinterpret old unassignable; Tasks1/2 request/filter regressions.

### Task 1: Backend statistics and two-stage filtering

**Files:** Modify apps/api/app/housing/{inventory_service.py,schemas.py,router.py,occupancy_export_service.py,room_status_export_schemas.py,room_status_export_service.py}. Test apps/api/tests/api/{test_housing.py,test_housing_cleaning.py,test_housing_room_status_export.py} and tests/db/test_housing_occupancy_projection.py; a focused new test file may be added under existing directories if clearer.

**Interfaces:** Produce HousingQuickFilter Literal with the exact Global Constraints values, HousingOverviewFilters.quick_filter, query quick_filter, HousingSummary.full_rooms:int. Exports accept the same optional quick_filter. Add assignability full while preserving old unassignable.

- [ ] Snapshot owned paths. Run focused current baseline then add RED tests proving summary remains base-filtered while quick-filter list/count/pagination change; same sidebar building affects summary; forbidden rooms remain excluded.
- [ ] Add literal mixed-room fixture assertions: total includes all; current people differs from occupied room count; partly filled verified/clean room is assignable; zero-capacity functional is not full; full is distinct from pending verification/cleaning; problem includes three statuses.
- [ ] Add bed/type/rate assertions excluding problem/maintenance/disabled/functional, including anomalous current resident in total people but not eligible-bed numerator; zero-denominator safe.
- [ ] Implement two-stage filtering using shared predicates so card counts and shortcut membership agree. Preserve current projection and legacy export behavior when no new filters supplied.
- [ ] Cover both exports with new full/quick filters and old broad unassignable. Reject invalid quick_filter with 422; no mutation API changes.
- [ ] Run focused housing API/projection/cleaning/issue/export/security suites on assigned disposable DB only, plus Ruff/mypy. Report exact RED/GREEN and test totals; release DB explicitly. Save scoped snapshot review/report, no commit.

### Task 2: Frontend counters and immediate controls

**Files:** Own apps/web/src/housing/{api.ts,api.test.ts,HousingOverviewPage.tsx,HousingOverviewPage.test.tsx,HousingFilters.tsx,HousingFilterState.tsx,housingKeys.ts,housingKeys.test.ts,HousingExportDialog.tsx,HousingExportDialog.test.tsx,HousingRoomStatusExportDialog.tsx,HousingRoomStatusExportDialog.test.tsx,HousingOccupancyExportDialog.tsx,housing.css} and apps/web/src/app/housingFocus.test.tsx. Related typed mock fixtures may be adjusted with report. Do not edit apps/web/tests (Task3).

**Interfaces:** Consume Task1 quick_filter/full_rooms/full assignability. Keep quickFilter outside displayed sidebar value; include in query key and saved filter state; reset shortcut on sidebar changes. Unique card id separate from metric key because two cards display current_occupants.

- [ ] Snapshot files, add RED behavioral tests for people counter, stable summaries, left-selected building preserved, shortcut replacement, left changes clearing shortcut, first-page/bulk selection reset.
- [ ] Add delayed-query tests that actual filter input stays mounted/focused, supports successive selections/raw search/IME with 250ms debounce, sheet closing retains filters. Retain private-data hiding on revision/403/account changes.
- [ ] Implement card metric/shortcut mapping: total_rooms→none, both people→occupied, empty→vacant, available→assignable, unavailable→full, problem→problem, bed/rate→usable, remaining→remaining, types→matching quick type.
- [ ] Replace Apply with immediate select/checkbox callbacks; preserve search input and focus, mobile dismiss and reset. Ordinary refresh must not remount controls; pending room actions remain safe. Avoid duplicate React keys/DOM ids.
- [ ] Serialize quickFilter and new full field; export defaults retain active shortcut and base conditions. Keep legacy broad unassignable compatible and distinctly labeled when exposed.
- [ ] Run full frontend tests once after focused GREEN, eslint and production build. Save report/scoped review; no commit. Coordinate any typed-interface change with Task1 before altering.

### Task 3: Browser contract updates and real acceptance

**Files:** Own existing apps/web/tests/{housing-room-status-foundation.spec.ts,housing-occupancy-lifecycle.spec.ts,housing-occupancy-revocation.spec.ts,housing-cleaning-photos-export.spec.ts,housing-filter-focus.spec.ts,assisted-personnel.spec.ts,profile-claims.spec.ts,ui-themes.spec.ts}; additional existing housing specs only if Apply-button references require adaptation. Root owns launcher changes if actually needed.

**Interfaces:** Consume immediate controls/quick-filter behavior from Task2. Keep assessment, registration and reporting-page Apply buttons unchanged.

- [ ] Snapshot paths, replace housing-only Apply workflows with assertions waiting for real post-selection data. Preserve all mutation/security assertions.
- [ ] Extend existing housing focus/foundation spec with same-base stable counters through vacant→available→full, current building persistence, immediate sidebar update, rapid typing and mobile closing. Use real current app API data, not mocked response-only acceptance.
- [ ] Run Playwright collection/typecheck first; coordinate root for sole disposable DB and isolated web/API. Execute relevant actual housing foundation/focus suite after Tasks1/2 green, retain persisted-data verifiers and no skip/relaxed assertions.
- [ ] Save exact tests passed/failed/deferred with source hashes and scoped report. No NAS, no production data, no foreground desktop aim tests.

### Task 4: Review, integration and NAS publication (root)

- [ ] Review task reports against source-specific snapshots; independent scoped spec+quality gates, then a whole-change review. No broad review of unrelated dirty history.
- [ ] Verify combined affected tests/lint/build and real browser flows; resolve actual failures without weakening safety. Freeze final task hashes.
- [ ] Run scripts/deploy-nas.sh --source "$PWD" with NAS_SSH_PORT=202 from approved source. Preflight/backup/deploy failure blocks release; no direct file-copy deployment.
- [ ] Verify status complete, unchanged schema0080, healthy services, public HTTPS and matching frontend entry asset, backup/restore success. Use no production test writes. Report remaining acceptance limits honestly.
