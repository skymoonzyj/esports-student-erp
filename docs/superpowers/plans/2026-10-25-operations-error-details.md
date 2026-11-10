# Operations Error Details Implementation Plan

> **For agentic workers:** Use test-driven development and independent review. User chose direct implementation; no further design approval gate.

**Goal:** Let super administrators identify which authenticated account and network source encountered an HTTP error, with 30-day history.

**Architecture:** Extend the existing bounded metrics buffer and idempotent background persistence with error-only events. Link authenticated account UUIDs through server-side request state, use the existing trusted-proxy source-IP parser, and expose a paginated read-only panel next to existing summaries.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/Alembic, React, TanStack Query.

**Spec:** User conversation: account ID (not merely request ID), source IP, Chinese reasons, super-admin only, retain 30 days, directly implement and deploy to NAS.

## Global Constraints

- Source is the existing isolated profile-housing-edit worktree; preserve unrelated dirty files. No Git stage/commit/push for this task.
- Record HTTP 400–599 only, without request/response bodies, raw URLs/query strings, authentication headers, passwords or verification codes.
- Authenticated account only; failed authentication must not attribute a submitted username to a real account.
- Template routes only; unmatched routes use the existing placeholder. Source IP is a network source, not proof of identity.
- Error detail retention is exactly 30 days, independent of configurable aggregate retention. Expired details are filtered from reads and purged in background.
- Keep existing super_admin plus operations.monitor.read authorization and no-store responses; add no permissions for other roles.
- Retain existing aggregate metrics; bound pending error memory, surface dropped-detail counts if capacity exceeded; do not fail business requests when collection fails.
- No production test account or housing/finance mutation. Standard scripts/deploy-nas.sh only after checks.

## Shared API Contract

GET /api/v1/operations/request-errors?days=1|7|30&page=1&page_size=20&account_id=<optional UUID>&source_ip=<optional IP>&status_code=<optional 400..599>.
Default days=1, page=1, page_size=20 (max 100). Invalid filters return validation error.
Response: {items,total,page,page_size,retention_days:30}; item: {id,occurred_at,account_id:string|null,account_name:string|null,source_ip:string|null,request_id:string|null,method,route,status_code,reason}.
Names are current joined display names, not retained snapshots; missing/deleted account falls back to recorded UUID. Request ID is supplemental correlation only. New internal event UUID is the persistence identity.

## Review Focus

- Forged X-Forwarded-For and login inputs cannot impersonate an account or source.
- Error events survive database retry without duplication; overload is bounded and visible.
- 30-day boundary is applied even before hourly cleanup and across restarts.
- Account deletion/name absence does not hide the recorded event; no identity identifier decryption.
- UI filter changes reset page and remove prior-filter data; role loss/logout cannot leak cached details.

### Task 1: Backend capture, retention and read API

**Files:** app/operations/{metrics,models,persistence,monitor,schemas,router}.py, new focused query module if useful, app/auth/dependencies.py, migration 0072_operations_request_errors.py; operations unit/API/service tests and migration tests/manifest head assertions.

- [x] Write failing middleware/auth tests: known account attached before CSRF denial; anonymous/invalid session null account; normalized routes and safe proxy IP; only errors buffered; bounded overflow observable.
- [x] Observe RED in existing unit harness, implement bounded events in existing batch/receipt lifecycle; safe fixed Chinese status explanations.
- [x] Write failing API/persistence tests for exact filters/pagination, ordinary admin denial, no-store, retry idempotency, 30-day cutoff/purge and nullable/deleted accounts.
- [x] Implement query and migration with indexes on time and account/time and source/time. Update deployment schema manifest and head tests without weakening downgrade/restore checks.
- [x] Verify focused unit suite/Ruff/mypy; controller runs database tests serially within each independently owned database.

### Task 2: Error detail panel

**Files:** apps/web/src/operations/api.ts, OperationsMonitorPage.tsx, new OperationsRequestErrorsPanel.tsx and test, scoped styling if needed.

- [x] Write and observe failing tests for account/name/IP, unconfirmed identity, HTTP/Chinese reason, range/filter pagination and expired permissions.
- [x] Add API helper matching shared contract and a Chinese panel with time, account, source IP, endpoint/method, code/reason, supplementary request ID. Use existing audit date format and responsive patterns.
- [x] Default recent day; allow 7/30 days, account UUID/IP/status filters, explicit search/reset and paging. No export; explain history starts after enablement and delayed batch persistence.
- [x] Verify focused tests, full frontend suite, lint and build.

### Task 3: Integration and release

- [x] Independent task review and integrated review of privacy, attribution, buffer retry and API/UI contract.
- [x] Run full backend on newly owned disposable PostgreSQL, no concurrent schema-reset test process sharing a database; run full frontend.
- [x] Exercise new panel using controlled synthetic responses/browser tests if available; no live error injection.
- [x] Run standard NAS deployment from this source, verify active release/schema and public health; record exact evidence and any limitations.

## Verification and release outcome

- Backend: 9,149 passed, 8 opt-in Docker/recovery drills skipped; 9,157 cases collected across 430 modules. Coverage combines disjoint isolated-database shards and complete affected-module reruns after test-contract corrections, without double-counting interrupted tests.
- Frontend: 2,983 tests, lint and build passed. Synthetic browser: 15 checks across five viewports passed. Backend Ruff and mypy (457 modules) passed.
- Independent implementation, privacy/authorization, migration and integration reviews approved. Existing single-process monitoring and pre-flush in-memory durability boundaries remain unchanged.
- Standard NAS script completed successfully with pre-migration database backup, 0071 to 0072 migration, container health checks, encrypted backup and isolated restore verification. Active release: `20261025T105342Z-62cb1a29b325-b27b8f41cba3`.
- Read-only production checks confirmed deployed panel assets, Alembic `0072_operations_request_errors` and successful public HTTPS health. No production error injection or business record mutation was used for testing.
- Error details start at enablement; historical aggregates cannot be backfilled into individual account/IP events. Details expire after 30 days and only super administrators can read them.
