# Asset Ownership and Rentals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development with TDD and scoped reviews. User explicitly requested direct implementation; no additional approval pause.

**Goal:** Extend the existing inventory with acquisition sources, rental terms, safe individual/bulk renewal and workbench expiry reminders.

**Architecture:** Add nullable ownership/provenance and date-only rental facts to Asset, keeping custody and warranty independent. Transactional renewal operations use optimistic rental revisions and persisted idempotency; a separate read-only reminder query feeds a permission-aware workbench panel.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL/Alembic, React, TanStack Query, existing date-only controls.

**Spec:** docs/superpowers/specs/2026-10-25-asset-ownership-rentals-design.md

## Global Constraints

- Existing isolated source: /Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3. Preserve unrelated dirty/untracked work. No stage, commit, push, reset or cleanup. Capture before snapshots for exact task review.
- User opted out of more questions: implement the spec's explicitly identified defaults. No financial/contract/notification provider scope.
- Permissions stay assets.read/manage/assign/export/read_self. No grants added. No production test writes. Publish only with this source's scripts/deploy-nas.sh after checks.
- All lease dates are inclusive date-only China dates, 4-digit years. Week7days/month1calendar/quarter3calendar; month-end clamp; renew from original expiry even overdue; reject overflow.
- Batch1..100 unique IDs, all-or-nothing, versioned and idempotent; no partial success or retries applying twice. Audit in same transaction. Actor permissions checked freshly before replay/writes.
- New form requires type. Legacy omitted new API values remain null/待确认; linked procurement migration infers only actual source data. Supplier lease is never AssetAssignment.expected_return_at or warranty_channel.
- Workbench displays global-authorized assets only. Pagination/count use the same authorized snapshot. Hide/clear stale private data when context/identity/permissions changes.
- Each DB runner owns its own new disposable Docker PostgreSQL and random loopback port. Never share/reset a DB concurrently. Root coordinates final suite; workers may only use approved owned runner after coordination.

## Shared Contract (all tasks consume exact names)

Asset management input/output additions: `ownership_type: null|'owned'|'rented'`, `acquisition_method: null|'purchase'|'other'`, `acquisition_channel: null|'online'|'offline'`, `acquisition_note: string|null` (500), `rental_started_on: YYYY-MM-DD|null`, `rental_expires_on: YYYY-MM-DD|null`, `rental_returned_on: YYYY-MM-DD|null` (output only), `rental_revision: int>=0` (output), `expected_rental_revision: int>=0` (mutation input when editing existing rental facts). All other asset fields/assignment APIs unchanged. Self/scan projections add ownership and rental dates only, not renewal history/management controls.

POST `/assets/rentals/renew`: `{operation_key:UUID, period:'week'|'month'|'quarter', assets:[{asset_id:UUID, expected_rental_revision:int}]}` -> `{operation_key,period,replayed:boolean,items:[{asset_id,previous_expires_on,rental_expires_on,rental_revision}]}`. Stable response from persisted operation on replay, permission check first. 409 stale/version/key-payload conflict,422 invalid eligibility/overflow, no mutation. Static path precedes dynamic IDs.

GET `/assets/{asset_id}/rental-renewals?page=1&page_size=20`: `{items:[{id,period,previous_expires_on,rental_expires_on,created_at,actor_name:string|null}],total,page,page_size}` under assets.read.

POST `/assets/{asset_id}/rental-return`: `{returned_on:date,expected_rental_revision:int}` -> normal Asset response. End lease only when no active custody; repeated/stale attempts409, dates between rental_started_on and today inclusive. Explicit confirmation in UI. No deletion.

GET `/assets` and export input add `ownership_type: 'owned'|'rented'|'unconfirmed'` optional; omission all. API uses null for unconfirmed records.

Model fields in `app.asset_management.models.Asset` exactly as above. Renewal data classes can live in new `rental_models.py`, imported through models.py for metadata registration. Task1 owns migration0073_asset_ownership_rentals and version manifest/test updates.

GET `/portal/rental-reminders?page=1&page_size=20` (max100) -> `{available:boolean,can_manage:boolean,observed_at:UTC datetime,business_date:date,next_refresh_at:UTC datetime,items:[{asset_id,asset_code,name,location:string|null,person_name:string|null,rental_expires_on:date,days_remaining:int,rental_revision:int}],total,page,page_size}`. Unauthorized capability returns availablefalse/empty/total0 with no asset leaks; unauthenticated remains401. Filters rented, not returned, status!=retired, expiry<=today+7; includes all overdue, no rental-start future items outside started<=today. Uses globalassets.read; can_manage reflects globalassets.manage. No hidden export/edit authority. No-store responses.

Frontend query root for rental reminders stays under `["portal",accountId,"rental-reminders"]` so existing logout cleanup applies. Asset mutations invalidate assetKeys.root and portal root (context-authorized reload). New asset UI may use separate `rentalApi.ts`, components and utility files to avoid growing AssetsPage further. Task2 owns all files under web/src/assets; Task3 owns web/src/portal and backend portal files/API router only.

## Review Focus

1. Double-click/lost response/concurrent different renewals: exactly one extension per operation; stale batch rolls back every row and audit.
2. Wrong month arithmetic/year overflow: Jan31/leapFeb29/Nov30 quarter hand-derived results and no mutation on invalid dates.
3. Legacy migration + procurement + partial update: no inferred ownership without evidence; source/warranty and custody/lease never overwrite each other.
4. Authorization revoked during workbench refresh or before write: no stale private display and no grants; actor permissions reevaluated before replay and mutation.
5. Ending lease with active custodian or retired asset: preserve assignment history, block unsafe end/renew/assignment; reminders count only active rentals including unassigned/maintenance/overdue.

### Task 1: Rental domain, API, migration and procurement/export integration

**Files:** Modify apps/api/app/asset_management/{models,schemas,service,router,export_service}.py, apps/api/app/procurement/service.py, apps/api/app/audit/service.py and existing audit label mapping only as needed. Create scoped rental_models.py, rental_service.py, rental_dates.py (and rental_schemas.py/router.py if useful), migrations/versions/0073_asset_ownership_rentals.py. Update scripts/db-schema-manifest.sh and all current-head/historical expectation tests affected, keeping guards. Tests: tests/api/test_asset_rentals.py, tests/services/test_asset_rentals.py, tests/unit/assets/test_rental_dates.py, focused migration/procurement/export tests.

**Interfaces:** Produces shared Asset fields, renew/history/return endpoints, list/export ownership filters. Task3 reads these fields; Task2 consumes shared JSON exactly.

- [ ] Write failing pure schema/calendar tests with literal dates: 2027-01-31+month=2027-02-28,2028-01-31+month=2028-02-29,2026-11-30+quarter=2027-02-28; 9999 overflow rejected; mismatched ownership/source/rental fields rejected.
- [ ] Run pure RED and minimal API RED on owned disposable database, then implement schema/defaults/model migration while preserving old calls and current/head rollback safety.
- [ ] Implement date helper `renew_expiry(expires_on:date,period:Literal['week','month','quarter'])->date`, transactional locked/revisioned/idempotent service and endpoints. Preserve original lease expiry in history and return stable saved result on exact retry; do not duplicate audit. Partial update validates merged state. Live permission/readiness changes cannot use stale actor grants.
- [ ] Write/observe failing tests for batch all-or-nothing, exact replay/payload conflict, two-connection same/different keys, stale revisions, return blocked with active assignment, returned rentals cannot assign/renew, old rented expiry edit cannot bypass renewal history, ordinary admin denial, audit rollback.
- [ ] Connect procurement snapshots, list/export filtering and management/self/scan projections; add migration backfill, table/index/guard preservation and procurement/export tests.
- [ ] Run scoped pure tests/Ruff/mypy; coordinate DB test execution with root, provide exact RED/GREEN evidence and changed-file manifest. No Git commit; before snapshots are the review boundary.

### Task 2: Asset forms, rental selection/renewal/history and projections

**Files:** web/src/assets/api.ts, AssetsPage.tsx, assets.css, MyAssetsPage.tsx, PersonAssetsPanel.tsx, AssetScanPage.tsx plus new focused OwnershipFields.tsx, RentalRenewDialog.tsx, RentalDetails.tsx/rentalApi.ts/rentalDates.ts as appropriate and matching tests. Only web/src/assets owned; no portal edits.

**Interfaces:** Consumes shared contract; exports reusable `renewAssetRentals` API helper if needed, invalidates assetKeys.root and portal root after mutation. Query handling follows existing authentication cleanup, no self/family management controls.

- [ ] Write observable failing form tests for owned purchase online/offline, other required description, rented inclusive start/end, existing unconfirmed display and independent warranty fields.
- [ ] Implement type-dependent form/projection fields and ownership filter/export. Preserve photo and current assignment actions; location still independently editable, usage-person picker remains existing assignment workflow. Only send changed rental fields/revision on edit, not stale whole-form rental snapshots.
- [ ] Write failing tests for single/bulk select eligibility, period preview, mixed expiries/monthend, operation-key reuse after network failure,409 conflict refresh, and confirmation. Selection restricted to explicit eligible current-page records; clear on page/filter/role/context changes and after success.
- [ ] Implement renewable rows, single/bulk modal with week/month/quarter choices, visible old/new expiry and warning when still overdue; calendar previews match contract. One operation key per open submission, retained on retry; after409 force refresh/reselection. History panel and separate confirmed rental-return action requiring no active assignment, preserving custody return distinction.
- [ ] Run focused tests RED/GREEN, lint/build. Root runs full frontend once integrated. Record before snapshots and report; no commits.

### Task 3: Permission-safe workbench rental reminders

**Files:** Create apps/api/app/portal/rental_reminders.py (split schemas/router if useful), add registration in apps/api/app/api/router.py. Create web/src/portal/{rentalRemindersApi,useRentalReminders,RentalRemindersPanel}.ts/tsx, scoped CSS; modify PortalDashboardPage.tsx and its tests. API tests tests/api/test_rental_reminders.py, unit/date/boundary tests and web component/hook tests. No assets files or migrations owned.

**Interfaces:** Consumes shared Asset fields and existing AuthContext/readiness/RBAC. Produces shared reminder DTO, and standalone workbench panel. Navigation to /assets only for authorized readers; workbench need not implement a second renew modal (link to management preserves one operation UI).

- [ ] Write failing API/read tests for today+7 included,+8 excluded,overdue included,returned/retired excluded,unassigned/maintenance included,China-midnight boundary, future lease not started excluded, global permission denial, independent paginated count/order and fresh auth/readiness recheck.
- [ ] Implement read-only authorized snapshot with stable sort and page count, using existing portal patterns without changing departure semantics. Return unavailable empty capability when no assets.read; can_manage is separate. Avoid per-item photo payload or N+1 people lookup. No grants or state mutation during read.
- [ ] Write failing web tests for available/empty/loading/error, metadata display, near-expiry versus overdue, permission/no-context hiding and cache purge, page reset, focus/China-midnight reauthorization; use literal server clock responses.
- [ ] Implement independent panel/hook/API and integrate once into all dashboard layouts without hiding existing workbench cards. Preserve departure reminders and all existing role behavior.
- [ ] Run scoped tests/Ruff/mypy and web lint; coordinate DB test runner with root. Report RED/GREEN and exact file manifest, preserve before snapshots; no commits.

### Task 4: Whole-feature review, final verification and NAS publication

**Files:** Task evidence under .superpowers/sdd/2026-10-25-asset-ownership-rentals; integration-only fixes assigned to original owner. Add browser spec and register complete-gate ownership if a repo browser spec is added (root ownership).

- [ ] Review each task against snapshot delta plus spec and run independent whole-feature review; fix actual findings with regression evidence.
- [ ] Run final backend suite on independent owned DB shards; never concurrently reset one DB. Run full frontend, lint/build, Ruff/mypy and migration checks. Distinguish optional skips and reruns without double-counting.
- [ ] Exercise synthetic browser create/edit/renew selection, 409 retry and workbench layouts desktop/mobile; no real NAS asset changes. Supplement with real API integration tests for persisted rental and reminder flows.
- [ ] Run `PATH='/Volumes/MacDev/Projects/new project3/.venv/bin:'"$PATH" scripts/deploy-nas.sh --source "$PWD"` from this isolated source. Failed preflight/deploy is a blocker; do not bypass. Verify active release,0073,health and encrypted backup/restore. Report delivered behavior and legacy pending ownership.
