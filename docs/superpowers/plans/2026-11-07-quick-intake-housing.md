# Quick Intake and Housing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 管理人员粘贴表格，经识别预览后安全地逐行建档并入住。

**Architecture:** 确定性服务端解析、只读预检、账号绑定加密预览票据；逐行显式事务调用现有建档和房态服务，成功回执保证重试幂等。前端只持有当前页面草稿，逐行提交并显示结果。

**Tech Stack:** FastAPI / Pydantic / SQLAlchemy / PostgreSQL / Alembic，React / TypeScript / Vitest。

**Spec:** docs/superpowers/specs/2026-11-07-quick-intake-housing-design.md

## Global Constraints

- 表格最多 100 行、200000 字符；加密票据 15 分钟；数据库冲突最多整行重试 3 次。
- 不调用外部 AI、不上传 Excel 文件、不写真实示例、不自动修改已有人员或房间配置。
- 授权/证件/监护/房态校验复用；所有 PII 不进 URL、浏览器持久存储、日志或遥测。
- 每行建档、入住、审计、成功回执原子提交；其他行独立。不按同名或手机号合并。
- 保留外部管理脏工作树；不 stage/commit/reset/push；仅一个数据库测试进程。
- NAS 发布只能 scripts/deploy-nas.sh --source "$PWD"，SSH 202；失败为发布阻断。

## Review Focus

- 空列、Markdown 分隔、全角及额外说明不得错位或丢弃证件/手机号。
- 预览后房间被占用、角色/队伍权限被撤销，提交必须重新拒绝而无半份档案。
- 同名不同证件、重复票据/标识及网络结果不明，不能合并、重复入住或泄漏受限人员。
- 跨中国午夜、过去退宿日与未来入住不能产生错误当前房态。
- 并发普通房态操作与导入可能形成 actor FK 锁环；整行回滚重试必须保留原子性。

### Task 1: 解析与只读识别契约

**Files:** create personnel_intake/quick_import_parser.py, quick_import_schemas.py; tests/unit/test_quick_intake_parser.py.
**Interfaces:** parse_table(text: str, *, import_date: date) -> list[ParsedImportRow]; rows carry source row number, room_code, room_type?, full_name, arrival/departure dates, job_text, identity_number, phone, parsing errors; no DB writes. Header aliases and 7/8-column layouts only, no guessed columns.

- [x] Write literal table tests for TSV/Markdown/blank dates and cells/fullwidth/invalid date/year/overlimit; run RED.
- [x] Implement bounded deterministic parser and DTOs; run tests GREEN.

### Task 2: 私有预览、原子建档入住与回执

**Files:** create personnel_intake/quick_import_service.py, quick_import_models.py, quick_import_router.py; migration 0089_quick_intake_housing.py; integrate router/model registration; schema manifest/head/recovery guards; tests/api/test_quick_intake_housing.py; tests/services/test_quick_intake_transactions.py.
**Interfaces:** POST /personnel-intake/quick-import/preview {text, default_arrival_date?, team_choices?} -> {import_date, rows:[row_number,full_name,room_label,arrival_date,departure_date,identity_label,project_label,team_options,selected_team_id?,needs_team_choice,birth_date?,sex?,masked_identity,masked_phone,errors,warnings,ticket?]}; POST /personnel-intake/quick-import/commit {ticket,idempotency_key} -> {person_id,stay_id,stay_status,role_pending}. Actual DTO details pinned before frontend task.

- [x] Write real API RED for administration preview and committed adult staff row, no email, no activated account, role pending; public/coach/property/room-denied refused.
- [x] Implement read-only resolution and cipher-bound ticket, actor+room scope/availability checks; preserve project/role as normal housing note when unassigned.
- [x] Add additive receipt schema and atomic single-row orchestration; same operation exact replay, key conflict, expired/tampered/other-account ticket refusal.
- [x] Test capacity/gender/readiness failure rollback and prior-row success retained; duplicate IDs and existing person deferred without rewriting.
- [x] Test lost response, concurrent duplicate submission and forced retryable whole-transaction failure; verify legacy locks unchanged and final no orphan profiles.
- [x] Run affected real DB, migrations and schemas GREEN.

### Task 3: 一键导入界面

**Files:** create apps/web/src/assistedPeople/QuickImportPanel.tsx, quickImportApi.ts and tests; modify AssistedDirectCreatePage.tsx plus scoped CSS.
**Interfaces:** consume exact preview/commit DTOs above using existing authenticated client; private in-memory state bound to account, abort on unmount/permission loss; do not persist input or tickets.

- [x] RED: paste + preview does not commit; confirmation required; invalid rows remain unsubmitted; batch successes and failures separate; ambiguous team selection triggers new preview.
- [x] Add lazy import entry and accessible preview/results, editable source, team choice, final confirm and stable per-row retry IDs; no auto-resubmit on focus.
- [x] Test old response/authority/account switch discards PII and aborts work, uncertain network preserves exact operation; run frontend suite/lint/build GREEN.

### Task 4: 复核、回归和 NAS 发布

- [x] Run full default backend (report skips), affected real PostgreSQL suite, full web suite, mypy/Ruff/lint/build.
- [x] Fresh independent whole-change review using actual pre-edit snapshot, reproduce/fix important findings and rerun relevant/full checks.
- [x] Run standard NAS deployment, verify active release/head/service health and encrypted backup/isolated restore; no production fixture writes.

Self-review: native execution chosen for the tightly coupled preview/transaction contract. User explicitly opted out of further design questions by selecting recommended direct development. Existing worktree is already isolated; keep a baseline of actual files and evidence instead of commits. Per-row conflict deferral and existing-person no-update boundary are explicit in spec.
