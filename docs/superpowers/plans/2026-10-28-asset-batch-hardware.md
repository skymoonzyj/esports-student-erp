# Asset Batch Creation and Computer Hardware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create independent rental assets in one safe batch and record computer specifications across asset workflows.

**Architecture:** Add nullable hardware fields and an actor-scoped idempotent batch operation. Preserve the existing asset ledger and authorization. Share frontend hardware fields and details components across creation, procurement, and projections.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, PostgreSQL/Alembic, React/TypeScript, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-asset-batch-hardware-design.md`

## Global Constraints

- Batch rental quantity is an integer 1–100, each asset has independent ID, code, QR and lifecycle.
- Hardware fields: `cpu_model` max 120, `memory_capacity` max 80, `gpu_model` max 120, `disk_capacity` max 120; nullable strings, whitespace stripped, blank normalized to null.
- Computer categories: `台式电脑`, `笔记本电脑`; all four fields required for new computers, including procurement and rental batches. Non-computers do not require hardware.
- Historical incomplete computers remain editable for unrelated changes; category transition to computer or hardware mutation requires all four final fields.
- Existing authorization, rental, warranty, photo and export security remain unchanged; batch requires fresh management and read authorization, also for replay.
- No commit, stage, push, destructive cleanup, secrets or production test data. Preserve preexisting changes. Deployment uses the required script only.

## Review Focus

- Timeout retry or concurrent repeated batch POST must create only one set; changed payload must conflict.
- Revoked management rights must deny even replay; audit failure must roll back the entire batch.
- Old incomplete computers must allow location edits, while new computer paths cannot bypass required specs.
- Disk descriptions may contain multiple drives and GPU may be integrated; exports must retain text without formula execution.
- Batch UI cannot duplicate one serial/photo or create again after success; hidden hardware fields must not leak stale values from non-asset procurement.

---

### Task 1: Backend batch and hardware contract

**Files:**
- Modify `apps/api/app/asset_management/{models,schemas,service,router,export_service}.py`.
- Create `apps/api/app/asset_management/{hardware,batch_service}.py` (and focused batch models/schemas if useful).
- Modify `apps/api/app/procurement/{schemas,service}.py`, audit safe metadata only if needed.
- Add migration after current head; update `scripts/db-schema-manifest.sh`, migration head expectations and affected CLI tests.
- Test `apps/api/tests/api/test_asset_batch_hardware.py`, `tests/services/test_asset_batch_hardware.py`, `tests/db/test_asset_batch_hardware_migration.py`, existing export/procurement tests.

**Interfaces:**
- Consumes existing `AssetCreate`, `AssetService.create_in_transaction`, fresh authorization helper and atomic audit patterns.
- Produces hardware fields in create/update/asset/self/scan projections.
- `POST /api/v1/assets/batch` body `{operation_key: UUID, quantity: int, asset: AssetCreate}`; asset must be rented, serial_number absent/null. Response 201 `{operation_key: UUID, items: AssetResponse[], total: int}`. Replay returns same IDs (current safe projection is acceptable), changed normalized payload 409. No photo upload in this endpoint.
- Procurement `PurchaseCreate` adds optional `asset_hardware: {cpu_model, memory_capacity, gpu_model, disk_capacity} | null`; only valid when add_to_assets, requires complete hardware for computer categories. Single-asset APIs use four flat fields.

- [ ] Write failing regression tests for three unique rental assets with one audit per created asset, same-key replay, mismatched-key payload, invalid quantities, owned batch rejection, serial rejection, permission denial/revocation, atomic audit rollback and actual independent-session concurrency.
- [ ] Run focused tests and record expected RED before implementation.
- [ ] Add nullable fields and operation persistence, indexes/checks, migration preserving existing rows. Implement `AssetBatchService.create(input, actor_user_id, reauthorize, audit)` using one transaction and per-actor operation lock, returning created assets and replay information as appropriate.
- [ ] Add shared hardware normalization/required validation; test all new paths and legacy unrelated edits. Include partial PATCH merged with current locked state, ownership changes, category transitions and whitespace.
- [ ] Propagate fields to response projections and CSV/XLSX export; tests assert literal values, appended columns, formula-safe values, and null legacy state.
- [ ] Add procurement payload validation and propagation; preserve financial behavior and purchase idempotency.
- [ ] Verify migration up/down on disposable DB and existing-row preservation. Update manifest/head tests. Run targeted API/service/schema/export/procurement tests, Ruff and scoped mypy. Report evidence; root runs final full suite serially.

### Task 2: Asset and procurement interface

**Files:**
- Modify `apps/web/src/assets/{api,AssetsPage,MyAssetsPage,PersonAssetsPanel,AssetScanPage}.tsx` or `.ts` as applicable.
- Create `apps/web/src/assets/AssetHardwareFields.tsx`, `AssetHardwareDetails.tsx`, `AssetBatchCreateDialog.tsx` and tests.
- Modify `apps/web/src/procurement/{api.ts,ProcurementPage.tsx}` and relevant tests.

**Interfaces:**
- Consumes Task 1 API contract verbatim. Extend `Asset`, `MyAsset`, `AssetScan`, `AssetInput` with optional nullable flat hardware strings. `createAssetBatch` accepts operation_key/quantity/asset and returns operation_key/items/total. Procurement uses nested asset_hardware.
- Produces reusable fields/details and a rental-only batch dialog accessible only to existing asset managers.

- [ ] Write failing UI tests: batch quantity and common fields, rental-only submission, stable operation key across network failure, changed-payload retry safety, success count and refresh, no photo/serial duplication.
- [ ] Write failing tests for owned/rented computer fields, noncomputer optional behavior, legacy missing display/edit, personal/person/scan views and procurement hardware submission.
- [ ] Run focused RED tests before code, capture evidence.
- [ ] Implement shared hardware inputs and details. Capacity is text with explicit examples/units; GPU supports 核显. Only computer categories show required inputs, but old incomplete edit allows unrelated changes; changing hardware requires completed fields.
- [ ] Add focused batch dialog with quantity 1–100, name/category/brand/model/location/rental dates/note/hardware and creation count. New dialog gets UUID; retry unchanged failed request retains it; edits after an ambiguous failure must not silently rotate key and duplicate assets. Explain checking existing batch on conflict; simplest freeze payload after first attempt with retry and explicit close warning if unresolved.
- [ ] Wire creation refresh and details to existing table without altering rental actions or management permissions. Batch has no serial/photo inputs, with clear instruction to edit individual devices afterward.
- [ ] Add procurement hardware fields and remove asset_hardware from non-asset submissions. Ensure category changes do not submit stale hidden hardware.
- [ ] Run focused tests, full frontend suite once, ESLint and production build. Report evidence and affected files.

### Task 3: Integration, independent review, deployment

**Files:**
- Create focused synthetic browser regression `apps/web/tests/asset-batch-hardware.spec.ts` if existing harness permits.
- Create `docs/superpowers/verification/2026-10-28-asset-batch-hardware.md`.

**Interfaces:** consumes Tasks 1 and 2, existing NAS release script and isolated test harness.

- [ ] Independent task reviews on actual feature-only snapshots/diffs, fix blocking findings and recheck focused tests.
- [ ] Run complete backend suite against disposable database with one process, full frontend suite/build/lint, migration checks. Diagnose all failures; report existing failures separately rather than hiding them.
- [ ] Exercise browser batch input and old/new computer views in both A/B themes with synthetic local data, no production writes.
- [ ] Whole-change review for transaction, authorization, retry, old-data compatibility and UI integration; fix important findings.
- [ ] Run `PATH="/Volumes/MacDev/Projects/new project3/.venv/bin:$PATH" scripts/deploy-nas.sh --source "$PWD"` from the release source. Verify script success, complete release state, new DB head, backup+restore verification, healthy services and public health.
- [ ] Deliver concise user-visible paths and behavior, verification and deployment result, with any remaining limitations explicitly stated.
