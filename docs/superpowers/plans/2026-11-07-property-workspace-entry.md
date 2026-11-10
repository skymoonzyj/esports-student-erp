# Property Workspace Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the property-specific export UI to property-role accounts and move approval to the dashboard while retaining property submission in room details.

**Architecture:** Extend existing capability/list projections, preserve all mutation/export authorization, and reuse the existing panel in explicit submit/review modes. No migration, dependency, role-seed or permission grant changes.

**Tech Stack:** FastAPI/SQLAlchemy/PostgreSQL, React/TypeScript/TanStack Query, Vitest/Playwright.

**Spec:** docs/superpowers/specs/2026-11-07-property-workspace-entry-design.md

## Global Constraints

- 用户追加：行政变更直接保存，不审核；复用现有roomPATCH与问题处理流程，不自审。新建物业申请资格单独判断，旧申请生命周期继续保留，行政/超管兼任物业也不走新申请，无有效管理权限不得变更。

- 专用基础导出要求 property 角色且有效 can_export；不改其他导出入口、接口授权或角色种子。
- 工作台审批须服务端 can_review 和逐条 can_review；房间详情仅本人提交/状态/撤回，不审批。
- 列表按房间权限、显式拒绝、身份与视图在 SQL 分页前筛选；默认 all 保持兼容。
- 不扩权、不改真实数据；无迁移/新依赖；维持 A/B 与移动适配、缓存身份隔离、权限核验期间隐藏私有数据。
- 保留外部托管 dirty worktree，不提交/暂存/重置；apply_patch 编辑。NAS 发布仅标准脚本 SSH202，失败阻断交付。
- 共享测试数据库仅本机55433专用实例且严格单进程；API源码冻结后，可与使用独立随机端口/专属容器的一个 housing-care owned gate 并行。gate拒绝55433，18144/15144须独占；其他DB/恢复任务不得并行。Root 持有调度权。

## Review Focus

- 物业兼任行政但某房间 review 被拒绝：review 列表不能从本人 submit 分支泄漏该房间申请；Task1 SQL 分页/总数测试。
- 超管或行政无物业角色仍有导出权限：专用 UI 隐藏而原接口可用；Task1 API/UI 测试。
- 工作台学员/普通未授权账号：不触发房态目录或申请请求、不出现权限错误；Task1 dashboard 集成测试。
- 权限复核/账号切换与迟到列表或导出响应：不得恢复旧列表或下载；Task1 deferred-response 测试。
- 审批后返回房态：版本/列表缓存刷新，房间实际状态正确；Task2 真实浏览器流程。

---

### Task 1: Capability/view contracts and UI relocation

**In-flight amendment (explicit user instruction):**
- Extend ownership to apps/api/app/housing/access.py and existing RoomDetail direct-edit integration; no new direct mutation endpoint. Keep existing `can_submit_changes` lifecycle unchanged; add `can_create_property_request(room)` requiring property+effective submit, excluding administration/super_admin and room-direct-managers. Global/room capabilities add optional-TS `can_create_request`, missing fails closed.
- RED API cases: administration and mixed administration/property proposal POST refused with clear direct-edit message and zero new row/room change; property-only still pending; scoped/explicit direct denial never gains mutation; legacy administration pending rows remain submitted-readable/withdrawable (seed historical row, not new prohibited POST). Existing default all union unchanged.
- Implement new-create guard inside locked submit service, reuse existing audit/error handling; keep reviewer self guard and historical submitter validation unchanged.
- RED UI: administration room detail has existing RoomDialog direct-edit via allowed_actions.edit; save PATCH immediately updates room, no proposal POST. Proposal form requires fresh can_create_request, while old history/withdraw uses can_submit. Property-only has form and no edit; admin/mixed new-request false keeps history. RoomDialog uses existing onSubmit/request-version/refresh/error conventions; new issue status continues existing issue flow.
- GREEN affected backend/property-role/direct-room and UI tests, Ruff/mypy/lint/build; no fixture semantic weakening, no migration/seed changes. Replace the just-added obsolete administration-new-proposal lifecycle test with historical-row lifecycle coverage. Root full frontend pre-amendment3757passed is checkpoint only, rerun after final frontend freeze.

**Files:**
- Modify: apps/api/app/housing/property_service.py, property_router.py (property_schemas.py only for shared view type if needed).
- Test: apps/api/tests/api/test_housing_property.py, tests/services/test_housing_property.py.
- Modify: apps/web/src/housing/propertyApi.ts, propertyAccess.ts, HousingPropertyChangesPanel.tsx, HousingPropertyExportsPanel.tsx, HousingOverviewPage.tsx, HousingRoomDetailPage.tsx; apps/web/src/portal/PortalDashboardPage.tsx.
- Test: corresponding propertyApi/HousingPropertyPanels/HousingOverviewPage/HousingRoomDetailPage/PortalDashboardPage tests; update only complete capability fixture fields in directly affected tests (including app/housingFocus) when required by TypeScript.

**Interfaces:**
- Consumes existing HousingAccessService.roles, room_permissions_criteria, can_review_changes, can_submit_changes, can_export_basic and current actor/capability/cache boundaries.
- Produces `HousingPropertyCapabilities.show_basic_export: boolean` on server; TS may mark optional for older-response fail-closed compatibility, consumers require `=== true`.
- Produces list `view: Literal["all", "submitted", "review"] = "all"` query/service kwarg; all retains union, submitted actor+submit-visible, review review-visible only. Unauthorized view must return no unauthorized rows; existing route private/error conventions retained. Do not add role bypasses.
- Panel consumes explicit `mode: "submit" | "review"`; keys include mode. Only can_submit mounts submit data/actions; only can_review mounts review data/actions. Submit requires room, uses submitted view; review uses review view and label “物业变更审批”. No default that silently restores mixed mode.
- useHousingPropertyAccess accepts an optional enabled gate (existing roomId call compatible). Gate prevents catalog/capability queries and hides stale errors/data when disabled; keep hook order stable and latest auth/capability guards.

- [x] Write backend RED cases: property-only and mixed show_basic_export true; administration/super_admin without property false while can_export remains true; deny/missing rooms false; missing room ID false. Assert HTTP and service values, not source text.
- [x] Write list RED cases with >one page, mixed-role submit ownership and review room deny: submitted returns only owned allowed rows; review excludes denied rows with exact total/order across pages; default all unchanged; forged view invalid422; self can_review false and existing review mutation refuses self.
- [x] Run targeted pytest to observe behavior failures; implement minimal server projections and route enum.
- [x] Write UI RED cases: role-only export hide/show; delayed download after show_basic_export revocation suppressed; overview no approval panel; detail submit/own status/withdraw no approval even mixed; dashboard reviewer panel and approval actions, nonreviewer/nohousing no private requests, capability revocation during stale response hides content.
- [x] Implement typed view serialization and explicit panel modes. Reuse original mutations and previews. Add dashboard wrapper/coarse housing-read gate plus authoritative can_review gate; preserve errors for entitled reviewers, no unauthorized noise. On successful approval invalidate housing lists/details/catalog plus proposal queries through existing keys.
- [x] Run focused tests, full frontend lint/build, Ruff/mypy affected backend. Root runs full suites after final freeze; do not run database commands in parallel. Record RED/GREEN commands/results and files; no commits.

### Task 2: Real browser integration and release evidence

**Files:**
- Modify: apps/web/tests/housing-cleaning-photos-export.spec.ts only for changed approval entry and added role-entry/own-submission checks; related exact fixture fields only if necessary.
- Create: docs/superpowers/verification/2026-11-07-property-workspace-entry.md; task-local evidence under current plan workspace.

**Interfaces:** Consumes Task1 reviewed capability/view/panel behavior. Uses existing isolated housing-care seed and owned gate, no live accounts/NAS business writes.

- [x] Update current actual property submit→administration approve→property persisted-status test to navigate reviewer to `/`, require no approval on `/housing`/room detail, property export shown and nonproperty export hidden. Keep existing mutation/persistence/anti-self/financial assertions.
- [x] Add real administration detail direct-save check: no pending-submit form, room change immediately visible and no new proposal; property-only proposal remains pending until workspace review. Existing safe direct room fields/issue workflows unchanged.
- [x] Exercise clear/command appearance using the actual isolated appearance endpoint and DOM theme confirmation (not a mocked API or DOM-only theme override). Preserve original theme with fresh revision if another test follows. Assert scoped panels visible and within viewport after normal scrolling; do not retain login/credential screenshots or introduce an artifact-export pipeline for this small UI relocation.
- [x] Run existing owned housing-care browser gate with private temporary credentials and exact owner cleanup. Keep original viewport coverage; document screenshot/physical-device boundaries. Do not reuse earlier SDD artifact paths or bypass ownership guards.
- [x] Root run complete frontend `npm test -- --maxWorkers=4`; backend run all affected housing API/service/db/unit modules plus property-role integration, RBAC/permissions, portal context/dashboard and housing security regressions. Complete static checks and diffcheck. This replaces an all-project API rerun: latest completed full baseline is context only, not current evidence; current change has no migrations, schema/seed or financial changes. Report exact current scope, do not claim whole-backend pass. After Task1 review/API freeze, affected API may run concurrently with Task2's single dedicated owned browser gate; never share55433 or mutate API sources in flight. Independently review task and final integrated diff; retain every skip/failure reason.
- [x] Deploy from actual worktree with `PATH="/Volumes/MacDev/Projects/new project3/.venv/bin:$PATH" NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`; no shortcut. Confirm active/current/complete state, unchanged0087, services/scheduler/publicHTTPS/current assets and authenticated encrypted restore metadata using nested revision rows.
- [x] Save final evidence, preserve external worktree and uncommitted task artifacts. No remaining gates may be marked passed without actual results.
