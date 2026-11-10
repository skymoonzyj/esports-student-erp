# Home Return Permit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a truthful, independently validated return-permit option through managed personnel and guardian lifecycles, publish it, then resume authorized intake.

**Architecture:** Extend existing typed identity and HKID paths with one separate type; retain all current permissions, encryption, approvals and activation gates. Backend establishes the contract, frontend consumes it, controller verifies integration and performs standard NAS release.

**Tech Stack:** Python/FastAPI/Pydantic/SQLAlchemy/PostgreSQL/Alembic; TypeScript/React/Zod/Vitest/Playwright.

**Spec:** docs/superpowers/specs/2026-10-30-home-return-permit-design.md

## Global Constraints

- Type `hk_mo_permit`; label `回乡证（港澳居民来往内地通行证）`; number label `回乡证号码`.
- ASCII H/h/M/m plus8digits, optionally2renewal digits; trim outer whitespace and canonicalize first9characters uppercase. Reject internal whitespace/fullwidth/other forms. No checksum or demographic inference.
- Core `normalize_hk_mo_permit(number: str) -> str`; UI `normalizeHkMoPermit(value: string) -> string | null`; digest domain `hk-mo-permit-identity`; issuer onlyNone; passport digestNone.
- Preserve CN/HKID/passport contracts/evidence, guardian adulthood/completeness, missing-email/readiness, permissions, idempotency/version checks and approval≠activation. Public registration and legacy CN-only PersonForm are not expanded.
- Only synthetic test people/numbers. No production writes except later user-authorized intake via normal UI. No Gitstage/commit/push/reset, no dependency upgrade, preserve preexisting dirty source.
- Source is the existing managed worktree `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`. Deploy only via `scripts/deploy-nas.sh --source "$PWD"`; backup/restore failures block release.
- Controller owns exclusive DB test lane and final whole suites; workers request lane before DB tests. No shared reset processes in parallel. Workers never dispatch subagents or deploy.

## Review Focus

- Renewal suffix must not create a second person or mismatched claim evidence (Task1 lifecycle, Task2 transports).
- Non-CN field edits must preserve manually entered demographics, and type changes must clear stale inferred facts (Task2 forms).
- Same complete guardian can link two minors without granting an account or changing existing relationships (Task1 lifecycle).
- Malformed/type-confused identity cannot leak through errors, exports or audit (Task1 pure/service).
- New migration must preserve old data/evidence and refuse lossy downgrade while test/deploy head manifests remain aligned (Task1 DB/CLI).

### Task 1: Backend typed identity and lifecycle

**Files:**
- Modify: apps/api/app/identity/{document_identity.py,models.py}, apps/api/app/personnel_intake/{schemas.py,codec.py}, apps/api/app/personnel_intake/claims/{schemas.py,completion.py}, apps/api/app/housing/{occupancy_export_service.py,history_void_service.py}; other mapped call sites only if type handling requires it.
- Create: apps/api/migrations/versions/0078_hk_mo_permit_documents.py.
- Modify: scripts/db-schema-manifest.sh and current-head test expectations in apps/api/tests/{db,cli}.
- Test: new tests/unit/test_hk_mo_permit_identity.py, tests/services/test_hk_mo_permit_lifecycle.py, tests/db/test_hk_mo_permit_migration.py; extend relevant claim/privacy/old-migration tests as necessary.

**Interfaces:** consumes existing typed document/approval services; produces `DocumentType` extended by `hk_mo_permit`, canonical normalizer above, current migration0078 and API request/response acceptance.

- [ ] Write failing unit tests with independently specified H87654321, m1234567802→M12345678, invalid shapes, distinct digest, issuer rejection, no inferred facts, strict/pending/guardian, masking/ordinary-export label and typed claim evidence; watch RED.
- [ ] Implement minimal type branch/shared normalization and add constraint-only migration with lossy-downgrade refusal; update manifests without rewriting legacy evidence.
- [ ] Add real DB lifecycle tests: managed/pending create,9/11duplicate refusal, shared guardian for2minor profiles, corrections, authorized claim review→real captured-email OTP activation, missingfacts refusal; add legacy-row-preserving migration/down-revision checks. Obtain controller's DB lane; watch RED thenGREEN.
- [ ] Test known-target sensitive free-text rejection including lowercase/spaced/fullwidth aliases, preserve unsupported CN-only reporting behavior and nonrevealing validation. Run scoped Ruff/mypy plus related identity/intake/claim/activation/housing and migration/CLI tests. Report exact commands, failures and outcomes; controller runs full suite once integration freezes.
- [ ] Self-review and write task report/file manifest. Preserve dirty worktree without committing. Wait for independent task review before Task2 implementation.

### Task 2: Frontend form and claim integration

**Files:**
- Modify: apps/web/src/assistedPeople/{documentFacts.ts,AssistedPersonForm.tsx,PendingPersonForm.tsx,api.ts}, apps/web/src/profileClaims/{ClaimProfilePage.tsx,ClaimCompletionFields.tsx,api.ts,adminApi.ts}, apps/web/src/people/api.ts. Shared types/display consumers only as needed.
- Test: companion *.test.ts(x), including managed strict/pending/correction forms and claims; extend apps/web/tests/personnel-documents.spec.ts (already registered in standard gate).

**Interfaces:** consumes Task1 `hk_mo_permit`, canonical9digitvalue and issuerNone; produces same-shape typed payloads, central labels and frontendnormalizer.

- [ ] Add failing normalization, response-parser and form tests: newoption, H/M9/11, person+guardian, invalidshape, explicitCN-only auto-demographics, type-switch clearing, currentmanualfacts preserved on numberedit; watch RED.
- [ ] Implement shared type-aware identity helper and option/label/normalization use; remove HK-only fallback assumptions in managed person/guardian paths without restructuring unrelated modules.
- [ ] Extend claim person+guardian request/response enums and explicit type serialization; test canonical payload, invalidissuer, confirmation/correction version/idempotency and protectedemail unchanged; watchGREEN.
- [ ] Extend existing loopback-only personnel-documents.spec.ts for strict/pending/guardian and typedclaim transport underA/B themes. Keep all requests synthetic and no PII/traces. Run focusedunits,lint,typecheck/build; controller runs completefrontend andbrowsermatrix.
- [ ] Self-review and write taskreport/filemanifest; no commit. Independent taskreview followed by wholefeature review.

## Controller integration and release

- [ ] Freeze code, read task diffs against per-task pre-edit snapshots; complete independent task and final review.
- [ ] Run full backend pytest once on verified disposable55433 (serial), full frontend npmtest, lint/build, Ruff/mypy, migrationhead, gitdiffcheck and relevant desktop/mobile browsermatrix. Any failures recorded and fixed/reverified, no falsified green.
- [ ] Execute standarddeploy and verify statuscomplete, active-release, head0078, healthy runtime/freshscheduler, publicHTTPS/assets and encryptedbackup/isolatedrestore.
- [ ] Recheck administratorUI login and exact duplicate identities/phones, guardian completeness, A-316 state/capacity/gender/date before any liveintake. Create pending profiles only with real supplied facts; no accountactivation. Actual guardianrelationship requires missingfacts fromuser; if unavailable explicitly report pending, never fabricate. Keep artifactnotes free of fullpersonaldata.
