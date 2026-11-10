# Housing Resident Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; inline execution with one independent final review.

**Goal:** 搜索当前入住人员的姓名/昵称并定位房间。
**Architecture:** 扩展现有批量内部投影与overview查询；外部API形状不变。
**Tech Stack:** FastAPI/SQLAlchemy/PostgreSQL, React/Vitest。
**Spec:** docs/superpowers/specs/2026-11-07-housing-resident-search-design.md

## Global Constraints

- 当前入住与待确认退房参与匹配；预约、取消、已退房、作废不参与。保留原房间字段匹配、权限、统计/分页和筛选合同。
- 不增加逐房间查询、响应字段、迁移、依赖；不修改真实人员住宿数据。
- 使用当前已发布的外部托管dirty worktree，保留用户改动，不提交/暂存/重置；apply_patch编辑。发布只用标准NAS脚本、SSH202及明确source。

## Review Focus

- 不可见房间的同名或同昵称不得改变结果/统计（API拒绝范围回归）。
- 已退房/预约人物不能把空房误匹配成人名结果（API状态矩阵）。
- 空昵称、部分姓名、大小写/全角、字面通配字符（API参数矩阵）。
- 多人同房或同名跨房不能重复计数；搜索发生在分页前（API分页/统计回归）。
- 左侧楼栋、IME、防抖与顶部快捷卡片保持原规则（前端集成及原回归）。

### Task 1: Implement and verify resident search

**Files:** apps/api/app/housing/occupancy_service.py, inventory_service.py; new apps/api/tests/api/test_housing_resident_search.py; apps/web/src/housing/HousingFilters.tsx and HousingOverviewPage.test.tsx.
**Interfaces:** RoomOccupancyProjection gains optional internal current_resident_nicknames tuple; existing current_stays provides person_name. room_projections loads nickname in its current batchedjoin, only current statuses enter nickname search tuple. _matches_filters ORs roomfields/currentnames/nicknames; remaining predicates unchanged. No new API shape orfrontendstate.

- [x] Capture actual-file baseline and run focused existing backend/UI baseline. No commits of existing dirty work.
- [x] Write realHTTP red tests for full/partial name+nickname, literal normalization, current-only states, permissiondeny, pagination/summary/filterintersection and no extra current resident response fields. Run and observe expected absentfeature failures.
- [x] Add minimal internal projection/matcher. Re-run new API tests plus housing overview/occupancy/summary/projection regressions.
- [x] Add input integration coverage that finds the expanded accessible hint and verifies name/nickname requests preserve building/floor, applies debounce/IME and returns server-filtered cards; observe RED, updatehint, GREEN.
- [x] Run frontend fullsuite/lint/build; backend defaultfullcommand plus explicit isolatedDBaffectedregression. Record every skip/failure and do not call skippedDBtests verified. Ruff/mypy affectedsource; independent finalactualdelta review.
- [x] Standard NAS deploy with explicitsource, verifycomplete/active/0087/health/HTTPS/currentassets and encryptedbackuprestore. Save measured evidence; preserve external worktree/artifacts. Release/evidence: .superpowers/sdd/2026-11-07-housing-resident-search/progress.md.
