# ERP confirmed audit repairs

> Execute the approved repair request using TDD, independently reviewed module work, and the mandatory NAS deployment script.

**Goal:** Repair the eleven confirmed findings in the September 14 audit without changing established business policy or historical financial data.
**Architecture:** Keep existing FastAPI/SQLAlchemy service boundaries, React Query UI, PostgreSQL persistence and NAS Compose deployment. Strengthen authorization, write atomicity, retry identity and runtime verification within those boundaries.
**Tech stack:** Python, PostgreSQL, FastAPI, SQLAlchemy, pytest, React/TypeScript, Vitest, Bash, Docker Compose.
**Spec:** `/Users/skymoonzyj/Documents/new project3/outputs/erp-audit-2026-09-14.md`, confirmed findings 1–11 only; user approved repair.

## Global Constraints

- Source root: `/Users/skymoonzyj/Documents/new project3/.nas-release-sync`. Main deployment launcher is `/Users/skymoonzyj/Documents/new project3/scripts/deploy-nas.sh`.
- Preserve all preexisting dirty/untracked changes. Use apply_patch; no resets, broad reverts, commits or deployment by workers. Root deploys only after checks through `scripts/deploy-nas.sh --source SOURCE`.
- Do not change production records, historical ledger entries, domains, HTTPS port 18080, secret values, or product policies in the audit's separate confirmation section.
- Workers share the codebase and must not revert one another. No worker subagents. Each task owns only its listed files; ask root before changing a shared file.
- TDD: add repaired-behavior regressions and observe the expected RED before production edits. Existing `/tmp/erp-audit-20260914-db` and `/tmp/erp-audit-20260914-frontend` diagnostics assert broken behavior; use them as reproduction references, not repaired expectations.
- Only root runs PostgreSQL tests, serially. Workers send root exact RED/GREEN pytest commands when ready and may run pure unit/frontend tests themselves. Python tools are under `/Users/skymoonzyj/Documents/new project3/.venv/bin/`.
- Reports include changed paths, RED/GREEN evidence, remaining concerns. Root supplies DB evidence. No success claim without actual passing output.
- All new client failures use clear Chinese explanations and established private-404/403 conventions. Ordinary transient network errors are not logout events.

## Task 1: Close role-assignment delegation bypass

**Owner/files:** `apps/api/app/rbac/{router,service}.py`, focused RBAC tests under `apps/api/tests/api/` and `tests/services/`. No auth/frontend writes.

- [ ] Read role mutation call sites and `_require_delegation_authority`. Add a real API regression using a non-super actor with only delegated global permissions.manage: direct audit.read grant remains denied and assigning super_admin must also be denied. Test permitted ordinary grants, excessive scope/time windows, and actor role/permission revoked after route precheck.
- [ ] Send root focused RED command before implementation.
- [ ] Pass authenticated actor into role writes; require actual active superadmin for superadmin assignment. Within the write transaction refresh/lock actor and target consistently with people→account ordering and revalidate current authority. Role bundles cannot grant any permission/scope/time window the actor cannot delegate. Preserve documented internal maintenance calls where no HTTP path can omit actor. Inspect role-definition mutation for the same reachable bypass and fix only related authority paths.
- [ ] Run focused Ruff/mypy, provide root GREEN command, self-review and report.

## Task 2: Enforce evaluation-center deny at each resource boundary

**Owner/files:** `apps/api/app/evaluations/` access/context/query/report/survey/workspace services and routers, corresponding evaluation API/service tests. Do NOT edit media_store.py, upload_cleanup.py, upload_service.py, task_service.py, task_models.py (Task 7).

- [ ] Use the three known API diagnostics as formal repaired assertions: enrollment/class explicit center deny blocks report draft; ordinary admin center deny blocks real-name survey data. Extend to scoped lists/history/review/print/mutations where the same permission contract applies.
- [ ] Send root RED command before implementation.
- [ ] Reuse resource-correct EvaluationAccessService.decide and deny precedence; combine center access with operation permission at actual target, not only global navigation. Filter denied records out of lists; direct denied resources follow existing private404/403 behavior, without sensitive response/audit side effects. Preserve guardian history access and existing role permissions absent deny.
- [ ] Verify tasks, reports, survey review and print entry points consistently. Coordinate Task 7 if a shared task service edit becomes necessary, rather than editing it concurrently.
- [ ] Focused lint/type checks, root GREEN tests, self-review and report.

## Task 3: Atomic, retry-safe procurement and lean invoice queries

**Owner/files:** `apps/api/app/procurement/`, procurement frontend module/API and tests, new migration `apps/api/migrations/versions/0057_procurement_idempotency.py`, procurement tests. Root owns migration head manifests and cross-module transaction helper; Task 9 owns asset router.

- [ ] Add independent committed-connection API tests: injected audit failure must leave zero purchase/reserve/center/invoice/reimbursement changes. Add retry/concurrency tests: same actor/key/payload produces one purchase or reserve entry and one audit; different payload same key is409; legitimate new key can repeat purchase. Add SQL capture ensuring list/export do not select invoice content while download returns exact bytes.
- [ ] Send root RED command before implementation. Frontend test failed retry preserves key; new dialog uses new key.
- [ ] Wrap each procurement business write plus required audit (and optional linked asset creation) in one top-level transaction, retaining service compatibility. Do not change shared write_transaction silently; coordinate root if needed.
- [ ] Require UUID operation key for reserve addition and purchase creation, normalized complete payload fingerprint, actor/kind/key serialization, paired nullable historical columns and unique actor/key constraints. Migration0057 downrev0056_evaluation_upload_cleanup. Existing rows unchanged. Replay rechecks route permissions, returns existing business result without duplicate audit/balance/asset writes. Old clients without key receive clear Chinese422 instructing refresh/check prior entry.
- [ ] Keep one key across failed network retries; new intended operation gets new key. Existing public service tests may use optional internally generated keys only if HTTP remains strict; document decision.
- [ ] Defer invoice binary by default, batch metadata in lists/export, explicitly load bytes only for download. Do not introduce per-row metadata queries.
- [ ] Update affected existing test fixtures mechanically with required keys; run frontend/pure tests, scoped lint/types, root GREEN and concurrency tests, report.

## Task 4: Serialize and validate revenue rules; deterministic cents

**Owner/files:** `apps/api/app/revenue_share/` and revenue-share tests. No migration, procurement or admissions writes.

- [ ] Add repaired regressions: concurrent first automatic coach-rule insertion admits only one; overlapping global60%+project60% effective combination is rejected; two50% rules allocate a101-cent payment into deterministic51/50 (sum101) instead of pending.
- [ ] Send root RED command before implementation.
- [ ] Serialize relevant global/project configuration changes before checking rows, including initially empty scope; maintain consistent lock ordering. Validate final effective global/project combinations across affected projects and overlapping effective dates, including replacement-by-role semantics, rather than merely same-layer total.
- [ ] Compute percentage pool in integer cents as a group with stable remainder ordering. Preserve intended partial-percentage pool size, fixed cap behavior, refund adjustments and genuine over-allocation pending protection. Historical entries are not rewritten automatically.
- [ ] Cover date boundaries, stable ties, mixed fixed/percent, <100% pools, refunds and existing reconciliation. Focused lint/types, root GREEN, self-review/report.

## Task 5: Login/logout serialization and sensitive UI consistency

**Owner/files:** `apps/web/src/auth/`, `apps/web/src/admissions/PaymentsPage.tsx` and its tests, `apps/web/src/people/PeoplePage.tsx`, audit page and focused tests. Shared frontend API/client/router changes only if root approves. No procurement frontend writes.

- [ ] Convert real-component diagnostics to repaired tests. New login must not send auth/login until old logout settles. Refund success must refresh admission cases and displayed totals. 200→403 must hide and remove sensitive cached rows for People and Audit, while ordinary network errors remain distinct.
- [ ] Run and record RED locally before production edits.
- [ ] Clear local private state immediately on logout, but serialize any later login behind pending logout request; preserve auth generation protection, handle logout errors without unhandled rejection, and avoid allowing pending cookie deletion to race a new session.
- [ ] Invalidate admission-cases on refund success alongside payment and relevant summaries.
- [ ] On explicit authorization denial hide prior snapshots, evict appropriate sensitive query caches and refresh permission state without retry loops or logging out on every403. Use established hooks where available; coordinate shared auth effects carefully.
- [ ] Run focused component suites, ESLint/type/build as applicable, self-review and report actual results.

## Task 6: Stable NAS release/configuration and deployment gates

**Owner/files:** main `scripts/deploy-nas.sh`, corresponding source launcher if present, release `compose.nas-staging.yaml`, deploy-specific helper scripts and tests. Do NOT edit backup container/runner (Task8) or media cleanup code (Task7). No live NAS writes or deployment by worker.

- [ ] Add local mocked-script tests reproducing stale current pointer/missing scheduler media or API status mount, and rejecting health/config mismatch. Record RED before edits.
- [ ] Make current stable/atomic and consistent with .active-release and fnOS working directory on successful release. Ensure failed gates do not advertise a healthy active release; preserve diagnostic state and previous pointer, do not blindly downgrade DB. Use per-release image identity so old configuration cannot silently consume new mutable tags.
- [ ] Validate actual API/scheduler shared media/quarantine mount identities, API operations status mount, required secret presence without output, current Alembic head, readiness and scheduler heartbeat. Public TLS check must validate certificate for skymoonzyj.l.cd:18080; never change ports/domain.
- [ ] Integrate Task7 storage identity preflight/activation contract. Avoid unlinking records on missing/incorrect mounted storage. This deploy is required to repair existing drift, so compatible initialization must be possible without exposing data.
- [ ] Preserve preflight/backups before migration and existing secrets/volumes. Check source content including untracked actual files if changing release identity, using a stable release snapshot; no mutable partially copied source.
- [ ] Test dry/mocked failure paths locally, shell syntax checks, describe precise read-only runtime verification and failure behavior in report. Root performs actual deploy.

## Task 7: Reject cleanup against the wrong media store

**Owner/files:** `apps/api/app/evaluations/{media_store,upload_cleanup,upload_service,task_service,task_models}.py` only as needed, `app/core/settings.py`, evaluation cleanup/upload tests; coordinate migration with root if needed. Do not edit access/query/report/survey (Task2), Compose/deploy (Task6).

- [ ] Add regression using storeA with uploaded file and empty storeB: global expiry through B must not delete DB slot/queue or report cleanup successful while A still owns file. Preserve normal absent-file idempotent cleanup and rollback-safe durable queue tests.
- [ ] Send root RED command before edits; determine explicit shared storage identity mechanism, fail closed before destructive DB retirement and communicate initialization/activation contract to Task6/root.
- [ ] Ensure actual deployments can safely initialize existing mounted volumes; an empty image directory must not automatically claim the valid identity. Validate correct media/quarantine pair before cleaning, including scheduler with read-only root and missing volumes. Keep attempts/backoff and missing-file success only after store identity is verified.
- [ ] Preserve active uploads, caller-owned/external transaction rollback behavior, global/account quotas and existing owned-file safety. Do not bulk-delete orphan files or rewrite media history.
- [ ] Focused pure tests/lint/types, root DB GREEN, self-review/report.

## Task 8: Verify restored sequence state from backup evidence

**Owner/files:** `scripts/erp_backup_container.py`, `scripts/erp-backup.py`, backup-specific tests/docs. No deployment launcher/Compose edits.

- [ ] Add regression proving position/is_called loss or rollback is detected during isolated restore verification; definitions alone must not pass. Add sequence quoting/multiple sequences and compatibility cases.
- [ ] Record pure RED locally or ask root for DB RED. Never invoke snapshot helper to terminal because it outputs secrets.
- [ ] Include sequence state in verification, sourced from actual dump evidence or an equally sound stable backup contract. PostgreSQL sequences are not MVCC transactional: do not naively compare a live before/after sample to a concurrently generated dump and reject valid live writes. Verify the restored next IDs are compatible with existing order/student identifiers where deterministically testable, without consuming/resetting production sequences.
- [ ] Preserve encryption, existing restore/decryption canaries and old-backup behavior deliberately; communicate any format/version change and Chinese error mapping. No production backup execution by worker.
- [ ] Pure tests and lint, root isolated DB/restore tests, self-review/report.

## Task 9: Atomic asset writes and final integration (root)

**Files:** `apps/api/app/asset_management/router.py`, asset atomicity tests; migration manifests/tests including head0057 or coordinated next revision; main outputs repair report.

- [ ] Add independent committed-session audit-failure regressions for asset create/update/assign/return, showing all domain changes roll back. Run RED then wrap required business/audit in a single top-level transaction, preserving audit metadata and photo behavior.
- [ ] Coordinate shared transaction helper contract with Task3. Update migration manifests/current-head expectations once migration owners finish; test upgrade/downgrade preserves existing data and constraints.
- [ ] Independently review each task against brief and before/after diff, fix important findings, then broad final review. Use snapshots of dirty baseline, not HEAD diff which contains previous work.
- [ ] Run serial focused DB RED/GREEN gates, all backend tests, frontend suite/lint/build, Ruff/mypy, diff checks. No simultaneous DB suites.
- [ ] Execute mandatory main deployment script with release source; on any gate failure stop release and report without bypass. Confirm public valid HTTPS18080, head, current/active/config/image/mount consistency, scheduler and backup status; do not test with real money/accounts.
- [ ] Save repair/tests/deploy evidence, distinguish unchanged historical data and deferred optional optimizations in final response.
