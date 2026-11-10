# Housing Property, Cleaning and Room Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. User expressly requested direct implementation; do not wait for another plan approval.

**Goal:** Deliver the approved housing counters, cleaning frequencies, property approval/export role, and directly editable room-card dates with seven-day reminders.

**Architecture:** Keep the existing room projection and RBAC. Cleaning extends rooms compatibly; room cards and property proposals use separate tables/services/endpoints. New UI panels have separate API modules; integration owns shared mounts.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL/Alembic, React/TypeScript, Vitest/Playwright.

**Spec:** docs/superpowers/specs/2026-11-05-housing-property-keycards-design.md

## Global Constraints

- Existing dirty detached worktree P=/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3; preserve unrelated edits, no git stage/commit/push/reset. Use apply_patch.
- User explicitly says directly implement all previously confirmed scope. New schema after 0080, no production business test writes.
- Property room changes need administration/super_admin review; property room-card edits directly save. Property gains no checkin/transfer/checkout/catalog delete/sensitive report powers.
- Preserve scoped permissions and explicit denies, current account/session, version/conflict handling, current-occupancy and cleaning blocking invariants.
- Timezone Asia/Shanghai, dates only, reminders include D+7 and overdue but not D+8; audit keeps times.
- One disposable DB process at a time; root coordinates. Workers may run pure tests independently; DB runs require assigned slot. Root runs combined suites. No NAS access/deployment by workers.
- Snapshot each owned file's current bytes before edit (not HEAD); record RED/GREEN commands and file hashes in task report. No helper subagents; root provides review.
- Production release only scripts/deploy-nas.sh with NAS_SSH_PORT=202, after applicable local checks. Backup/restore failure blocks release.

## Review Focus

1. Old Boolean false must remain unclassified, not silently weekly/on-demand; both new and old filters must agree.
2. Property/coach mixed roles must neither accidentally lose granted room read nor bypass write denial/approval; explicit floor deny wins.
3. Pending approval sees stale room or revoked proposer permissions: never overwrite or activate it.
4. Card reminder count must not disclose unrelated people; checkout/transfer and date renewal remove stale reminders.
5. Export queries must include all permitted rows, not current page, with no identity/phone leak for property.

## Shared Interfaces and ownership

- cleaning_frequency: unspecified | daily | weekly | on_demand; query cleaning_frequency repeated; export cleaning_frequencies list. Summary daily_cleaning_rooms/weekly_cleaning_rooms/on_demand_cleaning_rooms; quick filters cleaning_daily/cleaning_weekly/cleaning_on_demand. Existing Boolean compatibility retained.
- New role property. Permissions: housing.changes.submit, housing.changes.review, housing.keycards.manage, housing.exports.basic. Property: read+submit+keycards+basic; administration: review+keycards+basic; super_admin gains all via existing seed. Role identity gates still apply. Task 2 owns seed/access.py changes for all new permissions.
- Task 3 APIs: GET /housing/rooms/{room_id}/room-cards => {items:[{stay_id,person_id,person_name,nickname,issued_on,expires_on,version,can_edit}]}; PUT /housing/stays/{stay_id}/room-card {issued_on,expires_on,expected_version} => item. Missing record version=0. GET /portal/room-card-reminders?page=&page_size= => {available,observed_at,business_date,next_refresh_at,items:[{stay_id,person_id,person_name,nickname,room_number,building_name,issued_on,expires_on,days_remaining}],total,page,page_size}. Root mounts routers named router exported by room_card_router.py and portal/room_card_reminders.py.
- Task 2 APIs: GET /housing/property/capabilities => {can_submit,can_review,can_export}; GET /housing/property/changes?status=pending|approved|rejected|withdrawn&page=&page_size=&room_id= => {items,total,page,page_size}, item {id,room_id,room_number,building_name,base_version,changes,before,reason,status,submitted_by_name,submitted_at,reviewed_at,review_note,can_review,can_withdraw}. POST /housing/rooms/{room_id}/change-requests {expected_version,changes:{service_status?,room_type?,capacity?,notes?},reason}; POST /housing/change-requests/{id}/review {decision:approve|reject,note?}; POST /housing/change-requests/{id}/withdraw. Responses item. Same author cannot review. POST /housing/property/exports {kind:occupants|problems,format:xlsx|csv,purpose,building_ids:[]} streams file. Root mounts property_router.router, property_export_router.router.
- Task 5 produces HousingRoomCardsPanel({roomId}), RoomCardRemindersPanel(); Task 6 produces HousingPropertyChangesPanel({room?: HousingRoomDetail, onChanged?:()=>void}) and HousingPropertyExportsPanel(). Components self fetch capabilities/authorized APIs, no broadened guards.
- Task 4 owns all existing housing frontend files, including mounting the Task 5/6 components in overview/detail. Tasks 5/6 only new files and their own tests/CSS; coordinate API mismatches early.
- Task 7/root owns shared router registration, migrations/env.py metadata imports, audit names/whitelist integration, schema manifest/head tests, portal dashboard mounts and frontend role labels/nav eligibility. Other tasks send exact integration requirements instead of editing these files.

### Task 1: Cleaning frequency backend and projections

**Files:** housing/models.py, schemas.py, inventory_service.py, router.py, room_status_export_schemas.py, occupancy_export_service.py, room_status_export_service.py, room_status_export_renderer.py; migration 0081_housing_cleaning_frequency.py; new tests/api/test_housing_cleaning_frequency.py plus existing summary/export unit tests as needed.
**Produces:** shared cleaning interface, stable 0081 revision; metadata remains in existing housing.models.
- [ ] Baseline focused schema/unit test run, snapshot owned files.
- [ ] RED tests: true->daily false->unspecified; three frequencies independent of cleaning_status; counts scoped before quick filter; Boolean/new disagreement rejected; both export forms preserve filters; property cannot use direct room mutation.
- [ ] Implement minimal nullable-safe enum/default/backfill/check and compatibility service writes; quick predicates and summaries; exports Chinese frequency, no broad refactor.
- [ ] GREEN pure tests/Ruff/mypy then request DB slot. Report command/output and changed paths.

### Task 2: Property role, approval and safe exports backend

**Files:** rbac/seed.py, housing/access.py; NEW housing/property_models.py, property_schemas.py, property_service.py, property_router.py, property_export_service.py, property_export_router.py; migration 0083_housing_property_changes.py (downrevision 0082_housing_room_cards); tests/api/test_housing_property.py and tests/services/test_housing_property.py.
**Consumes:** existing HousingInventoryService and issue lifecycle methods, cleaning fields if projection needed. **Produces:** shared property APIs, role/permissions, property model metadata, notes audit integration list.
- [ ] Snapshot and RED API assertions: property nonexistent/forbidden before feature, pending does not mutate room, staff cannot review, admin no new review, own review forbidden, inactive/revoked proposer denied, stale room conflicts, approve only once, reject/withdraw do not change room.
- [ ] Implement role/explicit scoped actions without assigning rooms.manage or stays.manage. Gate submit actual property (or review-capable for admin UI testing but no self approve); review actual administration/super_admin. Permit property mixed roles correctly without losing owner-only safeguards.
- [ ] Implement proposal unique pending room, transactional locked review and inherited domain checks. Avoid erasing unresolved problem evidence when service status changes; reuse appropriate domain methods, report uncertainties before coding inconsistent behavior.
- [ ] Basic export purposes/authorization/audit/CSV protection: current occupants with only spec fields, all problem/maintenance/disabled rooms including notes/open-issue summaries, no personal sensitive data.
- [ ] GREEN pure tests, then coordinated DB tests incl explicit deny and export rows; report exact model tables, audit actions, APIs and hashes.

### Task 3: Room card dates and reminder backend

**Files:** NEW housing/room_card_models.py, room_card_schemas.py, room_card_service.py, room_card_router.py, portal/room_card_reminders.py; migration 0082_housing_room_cards.py (downrevision 0081_housing_cleaning_frequency); tests/api/test_housing_room_cards.py, tests/api/test_room_card_reminders.py and date unit tests.
**Produces:** shared room-card APIs; HousingRoomCardRecord (table housing_room_cards) with stay_id unique, dates nullable together, version; no edits to housing/models.py required. Uses task2 seed permission, existing scoped HousingAccessService.can.
- [ ] RED tests: property/adminstration/superadmin dates allowed, admin/coach/self/other scoped rooms forbidden, invalid/partial dates rejected, missing expected_version conflicts, terminal stay rejected, concurrent edit exactly one succeeds.
- [ ] Implement direct audited versioned save with room/person/stay lock ordering consistent with lifecycle. Reading room card list requires room view; authorized non-editors receive read-only.
- [ ] RED reminder tests China date D+7 included/D+8 excluded, past included, inactive/void/checkout excluded, self only vs scoped administration/superadmin, people without accounts manager-visible, no identity/phone; pagination count matches rows.
- [ ] Implement bounded DB query/permissions and response next-midnight refresh; no recurring email/SMS or data mutations.
- [ ] GREEN pure tests and coordinated DB tests, report integration/audit requirements and hashes.

### Task 4: Existing housing UI cleaning/counts/integration

**Files:** existing apps/web/src/housing/*.tsx/ts/css excluding new Task5/6 files; focused existing tests as needed; existing housing browser specs text/filter adaptations (do not add new specs here).
**Consumes:** Task1 fields, Task5/6 panels. **Produces:** complete existing screens wired to new functionality.
- [ ] RED Vitest counts: mixed partly occupied rooms count rooms, current occupants count people; full/turnover labels; three frequency quicks preserve building and summaries; left frequency resets quick; exports inherit/clear frequency filters.
- [ ] Implement frequency edit/select filters/cards/labels and renderer type changes retaining legacy response compatibility; no unknown->on_demand inference. Rename turnover labels throughout UI without changing lifecycle actions.
- [ ] Mount room-card panel and property room request panel in room detail; property approvals/export panels in housing overview (components hide themselves when unavailable). Do not modify original occupancy controls for property.
- [ ] GREEN focused frontend tests + lint/build after all components arrive; report paths, RED/GREEN and integration concerns.

### Task 5: Room-card editing and workbench reminder UI

**Files:** NEW apps/web/src/housing/HousingRoomCardsPanel.tsx + tests + roomCardsApi.ts + CSS; portal/RoomCardRemindersPanel.tsx + tests + roomCardRemindersApi.ts/useRoomCardReminders.ts as needed.
**Consumes:** Task3 APIs. **Produces:** named components in shared contract; root mounts workbench and Task4 mounts room detail.
- [ ] RED Vitest: per-resident dates, edit only can_edit, versioned payload, four-digit year/date ordering, conflicts refetch not silent overwrite, failed saves retain inputs; expired/7-day/empty/error reminder display.
- [ ] Implement existing request/session/error conventions, date inputs with max 9999, no actual physical card controls. Query context isolates account/revision, hide previous data on transition; focus/visibility and midnight refresh.
- [ ] GREEN focused tests and ESLint; report component/API contract and integration requirements.

### Task 6: Property approvals and exports UI

**Files:** NEW apps/web/src/housing/HousingPropertyChangesPanel.tsx, HousingPropertyExportsPanel.tsx, propertyApi.ts, tests and CSS.
**Consumes:** Task2 APIs; existing HousingRoomDetail type. **Produces:** components in shared contract for Task4 mounts.
- [ ] RED Vitest submit valid proposed changes with reason/version, pending leaves current room unchanged; approved/rejected/withdrawn status; review only can_review; own withdrawal, reject reason; conflict refresh; export type/buildings/purpose.
- [ ] Implement scoped capability-aware panel; real before/after preview + reason, explicit approve confirmation; native date-free forms; no accidental default target values on partial request. Basic export safe filenames and downloaded response, no hidden sensitive data.
- [ ] GREEN focused tests + ESLint; coordinate one contract with backend, report paths and command evidence.

### Task 7: Integration, migration validation, review and release (root)

**Files:** api/router.py, migrations/env.py, audit/service.py/labels, migration schema manifest and tests; existing role labels/assignment enums/navigation and PortalDashboardPage mounts; new integration tests/browser scenario if needed.
- [ ] Run baseline affected backend/front-end suites before shared production edits; RED route/role/schema integration assertions.
- [ ] Register all new routes and metadata; update role display/assignability enumerations WITHOUT adding property to unrelated admission/revenue/finance privileges. Audit all allowed new payload metadata explicitly and never log sensitive export content.
- [ ] Update manifest/head/table tests to 0083; verify old->new data preserves rooms/stays, false unclassified, all new constraints; destructive downgrade refuses populated new data.
- [ ] Serialize full backend and migration tests in disposable DB, full frontend tests/type/build; run real focused browser acceptance in isolated environment including A/B desktop and mobile, no real residents.
- [ ] Independent task reviews and whole-change review with current-byte diffs; fix material findings with regression tests. Preserve warnings/failures truthfully.
- [ ] Run NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD" from P, wait for complete plus encrypted backup+isolated restore. Read-only verify health/revision/assets, do not create production test records.
- [ ] Write final report with exact evidence and known limits; no unsupported all-project or real-device acceptance claims.
