# Housing Bulk Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在房态页直接完成明确勾选房间的原子批量维修、可用、停用和核对。

**Architecture:** 保留批量核对接口；新增服务状态批量接口。前端选择模式复用跨页 ID/version Map 和 mutation coordinator，批量工具栏独立于折叠的目录管理。后端排序锁、锁后重新鉴权、先校验全部再写入和审计。

**Tech Stack:** React/TypeScript, FastAPI/Pydantic, SQLAlchemy/PostgreSQL, Vitest/Pytest/Playwright.

**Spec:** docs/superpowers/specs/2026-10-25-housing-bulk-status-design.md

## Global Constraints

- 最多 500 个唯一房间 ID/version；明确选择，不隐式扩展筛选结果；整批成功或整批不改。
- 不修改既有权限规则、入住/预约、核对与保洁独立性、问题处理流程；不批量完成保洁。
- 保留所有前置未提交工作，不 stage/commit/push；不使用 NAS/共享数据库做写入测试。
- 当前发布源为 /Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3；发布只用该工作树 scripts/deploy-nas.sh --source "$PWD"。

## Review Focus

- 等锁期间权限或对象范围变更：锁后新权限判定，整批无修改、无成功审计。
- 跨页选择中的旧版本：明确失败、保留可处理的选择，不自动重试写入。
- 问题房、待保洁、已有预约：不可通过设可用绕过其他独立拦截。
- 一个批次同时有不可读及可读冲突：隐私拒绝优先，不暴露房号和内部原因。
- 取消、切换操作及身份变化：不能把旧选择交给另一动作或用户，提交期间禁止重入。

### Task 1: Atomic backend service status batch

**Files:** apps/api/app/housing/{schemas,errors,inventory_service,router,api_support}.py; apps/api/tests/api/test_housing.py; apps/api/tests/api/test_security_housing_boundaries.py; apps/api/tests/db/test_housing_inventory_service.py; if needed audit action label mappings only.

**Interfaces:** POST /api/v1/housing/rooms/batch-service-status; body `{rooms: [{room_id: UUID, version: positive integer}], service_status: "available"|"maintenance"|"disabled"}`; success `RoomResponse[]` in request order. New `HousingInventoryService.update_rooms_service_status(inputs, *, service_status, actor_user_id, authorize_rooms)` with the same transaction conventions as verify_rooms. Failure 409 `housing_batch_conflict`, detail `批量修改未执行，请处理以下房间后重试`, standard ProblemDetails `errors: [{field: "rooms.<UUID>", message: "<房号>：<中文原因>"}]`; 403/404 privacy failures remain generic. Frontend consumes existing ProblemDetails errors without changing global client.

- [x] Write endpoint/service regressions first: successful multi-room statuses/order/version/audit; mixed valid + stale/occupied/planned/open-issue zero mutation; pending-cleaning and verification preserved; malformed/duplicate/over-limit rejected; existing scopes and private 404; real lock-wait permission revocation.
- [x] Root runs owned DB API gate to observe RED before production changes. Worker notifies root when tests ready; no ambient DB use.
- [x] Implement sorted row locks, full-set check, fresh post-lock authorization, all-room conflict collection before any write, unchanged service guard semantics, transaction audit using safe distinct action names or existing allowed metadata. Keep query count bounded rather than introducing per-row network/authorization reloads.
- [x] Root reruns owned gate for GREEN; worker runs scoped Ruff/mypy without ambient DB variables; report exact evidence and self-review. No commit in this dirty shared checkout.

### Task 2: Visible bulk action UI

**Files:** apps/web/src/housing/{api,RoomCard,HousingOverviewPage,HousingCatalogPanel,selection}.ts(x); new HousingBulkActions.tsx if useful; corresponding tests and existing housing styles only.

**Interfaces:** Task 1 HTTP contract. Export `updateHousingRoomsServiceStatus(rooms: HousingRoomVersion[], serviceStatus: "available"|"maintenance"|"disabled"): Promise<HousingRoom[]>` from api.ts. Reuse verifyHousingRooms for verify. Four explicit actions; default verify preserves established selection behavior. Switching action clears selection.

- [x] RED tests: visible bulk toolbar without directory expansion; already-verified editable rooms selectable for status modes; verify-only/read-only boundaries; correct IDs/version/target; cross-page 500 maximum; confirmation, cancellation, mode switch clearing; conflict details rendered without success/reset; permission failures clear private selection.
- [x] Implement action selector, room checkboxes by action and allowed_actions; shared single mutation coordinator; confirmation with target/count and all-or-nothing wording. Success refreshes room/catalog/people-housing projections and clears selection; errors safely show standard details.
- [x] Keep directory CRUD and old batch verification functional without two conflicting selection owners/toolbars. Selected snapshots survive ordinary paging; filters/identity/revision changes clear; refresh uses current visible versions consistently with existing policy.
- [x] Run focused tests then npm test -- --maxWorkers=4, npm run lint and npm run build; report RED/GREEN/self-review; no commit.

### Task 3: Integration, independent review, release

**Files:** apps/api/app/cli/departure_e2e_runtime.py fixed API_TESTS additions only; focused browser regression under existing owned gate if needed; docs/superpowers/verification/2026-10-25-housing-bulk-status.md.

- [x] Extend the existing fixed owned gate coverage with touched service/cleaning tests; preserve every ownership/DSN safety check. Run backend API and full-suite gate serially, frontend suite in parallel where independent.
- [x] Exercise real browser selection/confirmation/success/failure on isolated fixtures, desktop and mobile widths; never mutate NAS rooms to test.
- [x] Independent task reviews and whole-change review against a before-task snapshot; fix important findings and rerun affected checks.
- [x] Run standard NAS deployment, verify release completion, health, schema unchanged and deployed source/assets. Record exact tests, skips and deployment evidence. No production room mutation and no commit/push.
