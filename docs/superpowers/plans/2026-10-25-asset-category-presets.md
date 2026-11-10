# 内置资产分类 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 资产、采购入库和筛选使用同一份内置目录，用户只需选择。
**Architecture:** 后端只读目录和现有字符串兼容；前端共用原生分组 Select，严格沿用权限与隔离缓存。
**Tech Stack:** FastAPI/Pydantic/SQLAlchemy、React/TypeScript/TanStack Query、Vitest/Playwright。
**Spec:** docs/superpowers/specs/2026-10-25-asset-category-presets-design.md

## Global Constraints

- 不改数据库模型或迁移，不重写已有分类，不改变资产权限、自有/租用、租期或采购逻辑。
- 分类仍可空，字符串最大80；保存中文叶子名称，分组不保存。
- 内置目录允许 global assets.read 或 assets.manage；历史分类仅 global assets.read；响应 no-store。
- 新增/采购只选内置，编辑可保留当前旧值，筛选显示全部历史类别而非本页数据；请求失败不清空当前值。
- 不使用真实 NAS 业务记录测试。保留所有既有脏改动，不 stage/commit/push；每任务前后快照作为审查边界。
- 完成本地检查后必须运行当前工作树 scripts/deploy-nas.sh --source "$PWD"，不得旁路发布。

## Review Focus

1. 仅个人资产权限不能从类别目录获知全局历史文本：任务1鉴权测试。
2. 类别在其他分页、维护或退役资产上仍可筛选，重复、空白、内置同名不污染历史列表：任务1 API测试。
3. 编辑旧资产或目录加载失败不能静默清空/改类：任务2组件和页面测试。
4. 采购未入库、无管理权限不请求目录；切换账号不显示旧缓存：任务2门控/账号缓存测试。
5. 分类选择只在查询后应用，导出使用已应用条件；手机操作可用：任务2前端及根任务浏览器验证。

---

### Task 1: 后端分类目录

**Files:** Create app/asset_management/category_catalog.py and tests/api/test_asset_categories.py under apps/api; modify app/asset_management/schemas.py and router.py only as needed.
**Interfaces:** GET `/api/v1/assets/categories` returns `{groups:[{key:string,label:string,options:string[]}],legacy_categories:string[]}`; exact 12 groups and leaf strings from spec. Existing create/list/update/export contracts unchanged.

- [ ] Baseline existing tests/api/test_assets.py in owned disposable database.
- [ ] RED tests: authorized directory returns usable grouped choices and no-store; unauthenticated/ordinary personal admin denied; manage-only receives presets but no historical values; distinct historic categories preserve exact strings beyond current page and include maintained/retired records; null/whitespace/built-in duplicates omitted. Include non-built-in create/edit compatibility plus preset category through list/export/procurement.
- [ ] Implement minimal catalog and DTOs, endpoint registered before dynamic ID routes; use existing permission/readiness requirements, ORM distinct query only if global read.
- [ ] GREEN targeted API and existing assets/procurement suites in owned DB; scoped Ruff/mypy. Report exact red/green output. Controller runs final combined/full tests.
- [ ] Self-review and write task report; no Git commit (dirty shared workspace constraint).

### Task 2: 共享分类选择与页面集成

**Files:** apps/web/src/assets/api.ts, keys.ts, new AssetCategorySelect.tsx and tests; AssetsPage.tsx/tests; procurement/ProcurementPage.tsx/tests; existing API mocks only where necessary. Browser mock fixture update in tests/asset-rentals.spec.ts if required.
**Interfaces:** Consume Task1 endpoint verbatim; exported listAssetCategories and AssetCategoryCatalog types. Shared component props carry value/onChange/id and mode create|edit|filter plus disabled as needed; categorize old edit value separately. Backend is source of all preset values, no duplicated hard-coded production lists.

- [ ] RED tests: presets grouped and selected value submitted by new asset and procurement; unknown legacy edit preserved unchanged/clearable; filter includes older page category and export matches applied filter; error/retry preserves current value; cache identity changes discard historical catalog; disabled procurement path makes no directory request.
- [ ] Implement minimal API/query integration and shared native Select. Keep existing category optional, empty option labels suitable for create/edit vs filter; no free text. Request lazily in needed authorized surface, expose load/error/retry; no unhandled request. Existing CSS already supports select.
- [ ] GREEN focused component/page/api tests and existing rental/procurement tests; full lint/build. Update deterministic mock fixtures for new endpoint, not assertions unrelated to change.
- [ ] Self-review and write task report; controller runs full web and browser scenarios, then scope review and release.

### Task 3: 综合验收、复核和发布（主任务）

- [ ] Review task-scoped before/after diffs and reports independently. Fix concrete findings with regression evidence.
- [ ] Add browser category interactions using synthetic intercepted requests; unknown endpoints fail closed. Test desktop and mobile alongside rentals.
- [ ] Run full web suite, lint/build, full API checks in owned isolated environment; retain explicit skips/failures honestly. Run final review against feature-only diff.
- [ ] Run mandatory standard NAS deployment, verify complete state/revision/health and encrypted backup+isolated restoration; no NAS business writes. Update verification document and deliver.
