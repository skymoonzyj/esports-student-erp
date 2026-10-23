# 房态保洁、筛选导出及房间照片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加每日保洁设置、退房后的整房待保洁阻断、包含空房的当前房态筛选导出及每房最多三张受权限保护的照片。

**Architecture:** 保洁状态独立于服务状态，由既有退房／调房事务触发，所有新增入住统一检查。照片独立存储与按需读取；当前房态导出单独提供一致性快照，保留历史入住导出及实名报送语义。前端复用房态权限版本、筛选内存及写入协调器。

**Tech Stack:** Python/FastAPI/SQLAlchemy/PostgreSQL/Alembic/Pillow/openpyxl；React/TypeScript/TanStack Query/Vitest/Playwright；现有 NAS 部署与加密备份脚本，不新增云服务或依赖。

**Spec:** `docs/superpowers/specs/2026-10-23-housing-cleaning-export-photos-design.md`（用户于 2026-10-23 确认）。

## Global Constraints

- 执行源为 `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`（下称 P）；主目录 `/Users/skymoonzyj/Documents/new project3`（L）不是替代源码。执行前按 using-git-worktrees 核实并复用此合适工作区，保留其已有未提交产品代码。
- 计划已于 2026-10-23 获用户明确批准执行；采用子任务实现及独立审查，不将计划批准或分项通过视为发布完成。
- 已有房间默认 `daily_cleaning_enabled=false`、`cleaning_status=ready`，相关事件时间和操作者为空；不扫描旧退房、不伪造保洁、不改变真实住客。
- `pending` 时禁止新建入住／预约、确认入住、迁入；不阻止退房、调出、取消预约或资料修改。多人房退一人同样置 pending，已住人员保持。
- 只有实际角色 admin/administration/super_admin 且具备目标 `housing.rooms.manage` 的人员可管理保洁和照片，scope/deny 持续有效；照片只对完整房态详情权限开放。
- 照片 0–3 张；JPEG/PNG/WebP；单张上传 10 MiB；去 EXIF、校正方向；不声称支持 HEIC；不扩展个人/邀请摘要入口。
- 新快照 `POST /api/v1/housing/room-status/exports`；旧 `/housing/exports` 默认、列和历史范围保留。日期为中国时间年月日，审计保留时分。
- 每个任务先有失败测试、最小实现、通过测试和审查。严禁并行运行使用同一 PostgreSQL schema 的测试；fixture 会重置数据库。
- 使用任务独占且通过 `app.cli.e2e_database_safety` 的本地隔离数据库与空闲端口；不得把 NAS、正式账号或默认已占用的 55433 当测试环境，不停止其他项目。
- 仅提交本任务准确文件/补丁；若共享脏文件无法与原改动安全分开，保留补丁证据、不提交无关内容。中间提交以 `ERP_NAS_AUTO_DEPLOY=0` 明确暂停半成品发布；最终必须标准脚本发布，失败即阻断。

命令约定：`L='/Users/skymoonzyj/Documents/new project3'`，`P='/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3'`，`PY="$L/.venv/bin/python"`。pytest 命令在 `P/apps/api` 运行；npm 和本地脚本在 P 运行。`TEST_DATABASE_URL` 必须由本轮隔离环境的已验证配置提供，本文不硬编码一个可能被别的任务使用的端口。

## Review Focus

1. 同一房间连续两人退房与旧保洁完成请求交错：不得丢失新的 pending（任务 2 的双连接并发测试）。
2. 仅变更每日保洁或完成保洁：不得把维修/问题/待核对房变可入住；从 pending 房迁出仍应成功（任务 2）。
3. 有全局允许又有楼栋拒绝、预检后撤角色、照片删除后重建同槽：不能越权或用旧版本覆盖新照片（任务 2、4）。
4. 一边导出一边退房、调房或改人名：行数、房间人数、身份字段必须来自同一快照，空房仍有行（任务 3）。
5. 图片 EXIF 旋转、透明图、动图伪装或异常解码尺寸，及手机切后台/换号后的旧请求：不得崩溃、泄露缓存或丢表单（任务 4–6）。

## 文件分工与接口冻结

新增 `housing/cleaning_service.py`、`cleaning_router.py` 管保洁；`room_photo_service.py`、`room_photo_router.py`、`room_photo_images.py` 管照片；`room_status_export_service.py`、`room_status_export_renderer.py`、`room_status_export_router.py` 管快照。

共享 `housing/models.py`、`schemas.py`、`access.py`、`inventory_service.py`、`occupancy_service.py`、`api/router.py` 与审计白名单由主集成者所有。各实现者不得同时改共享文件，需先提交接口需求给主集成者。前端保洁由一个实现者管理 `api.ts`、`housingKeys.ts` 等契约；导出/照片实现者拥有各自新增组件，接线由主集成者完成。

全局 DTO：`CleaningStatus = Literal['ready','pending']`；房间投影增加 `daily_cleaning_enabled: bool`、`cleaning_status: CleaningStatus`、`cleaning_pending_at: datetime | None`、`cleaning_completed_at: datetime | None`、`photo_count: int`；allowed_actions 增加 `manage_care`、`complete_cleaning`、`manage_photos`。照片元数据 `RoomPhotoSummary(id: UUID, slot: int, mime_type: str, byte_size: int, updated_at: datetime, url: str, thumbnail_url: str)`；列表只显示 count，详情才返回元数据。

所有房间新写入采用 `version: int`（请求预期房间版本），成功递增并返回新房间投影；每一次真实迁出事件也递增。照片槽位 1–3，删除及重建同样递增 room.version，防止旧请求覆盖新对象。

## Task 1: 持久化模型、迁移与兼容契约

**Files:** 修改 `apps/api/app/housing/models.py`、`schemas.py`、`scripts/db-schema-manifest.sh`；新增 `apps/api/migrations/versions/0071_housing_room_care.py`；测试 `apps/api/tests/db/test_housing_cleaning_photos_migration.py`、`tests/db/test_migrations.py`、`tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`。

**Interfaces:** `HousingRoom` 增加全局 DTO 的保洁字段及 `cleaning_completed_by_user_id: UUID | None`；`HousingRoomPhoto` 增加 room_id、slot、content、thumbnail_content、mime_type、thumbnail_mime_type、byte_size、thumbnail_byte_size、sha256、uploaded_by_user_id 及时间字段。图片两种内容分别 `deferred=True`。`HousingRoomPhoto` 从 `housing.models` 导出，供任务 4 使用。

- [ ] 写迁移测试：`test_upgrade_preserves_existing_room_and_stays` 逐字段比较旧房、人员、住宿，断言默认 false/ready/null；`test_room_photo_slot_constraints` 断言 1/2/3 成功、0/4/同房重复槽失败；`test_downgrade_rejects_existing_care_facts_or_photos` 断言存在照片、每日保洁 true 或任何保洁事件时拒绝有损降级。

```python
# test_upgrade_preserves_existing_room_and_stays 的关键断言
assert room.daily_cleaning_enabled is False
assert room.cleaning_status == "ready"
assert room.cleaning_pending_at is room.cleaning_completed_at is None
assert original_person_and_stay_fields_after == original_person_and_stay_fields_before
```
- [ ] 在独占隔离库运行 `"$PY" -m pytest tests/db/test_housing_cleaning_photos_migration.py -q`，确认因新模型/迁移缺失而失败，不把环境失败当 RED。
- [ ] 核对实际唯一 Alembic head 仍是 `0070_housing_history_void`，否则停下协调迁移编号。新增 `0071_housing_room_care`；给状态、slot、字节上限、事件事实成对完整性增加 DB 约束；房间照片 FK 不绕过房间删除权限，房间依法删除时清理其照片。
- [ ] 更新 manifest 的 head/chain/required table 与三处硬编码测试；保留现有旧迁移和版本表宽度处理。GET 契约新增字段；旧 RoomUpdate 未传每日保洁字段时保持原值，新 RoomCreate 默认 false。
- [ ] 重跑本任务测试、`tests/db/test_migrations.py` 及两个 CLI 测试；通过后独立审查并提交本任务补丁，提交消息 `feat: persist room care and photo metadata`，不发布半成品。

## Task 2: 保洁事务、准入和授权

**Files:** 新增 `apps/api/app/housing/cleaning_service.py`、`cleaning_router.py`；修改 `housing/access.py`、`schemas.py`、`errors.py`、`api_support.py`、`inventory_service.py`、`occupancy_service.py`、`router.py`、`occupancy_router.py`、`app/api/router.py`、`app/audit/service.py`；测试新建 `tests/api/test_housing_cleaning.py`、`tests/db/test_housing_cleaning_concurrency.py`，扩展现有 `test_housing_inventory_service.py`、`test_housing_transfer_checkout.py`、`test_housing_stay_lifecycle.py`。

**Interfaces:**

- `mark_room_pending_cleaning(room: HousingRoom, *, occurred_at: datetime) -> None`：仅在已持有房间行锁和调用者事务中使用，不自行 commit；每次事件设置 pending/time 并 version += 1。
- `HousingCleaningService.complete(room_id: UUID, *, version: int, actor_user_id: UUID, occurred_at: datetime, authorize_room: Callable[[HousingRoom], None]) -> HousingRoom`。
- `HousingAccessService.can_manage_room_care(room: HousingRoom) -> bool`：实际三角色且原房间管理权限；锁后授权回调重新构建 access/角色而非捕获旧对象。
- `POST /housing/rooms/{room_id}/cleaning/complete` body `{version}` 返回 `HousingRoomCard`。每日保洁沿用新建/编辑房间请求的可选 `daily_cleaning_enabled`，不允许 generic edit 覆盖 cleaning_status。
- `HousingRoomPendingCleaning` 映射为 409、code `housing_room_pending_cleaning`、detail `该房间待保洁，请完成保洁后再安排入住`。

- [ ] 写 API/服务失败测试：普通 admin、真正 administration、super_admin 成功；coach/manager、只有档案 staff_type=administration、scope deny、预检后撤角色拒绝；`test_daily_cleaning_keeps_verification_and_service_status` 断言只改每日选项不改 verified/problem/maintenance。
- [ ] 写真实双连接测试：两个提交顺序的 complete vs checkout、pending 再退一人令旧 version 完成失败、入住确认 vs checkout、审计异常整体回滚。使用 committed 独立 fixture 与 Event 屏障，不以 savepoint 单 Session 冒充并发。

```python
# test_second_checkout_invalidates_stale_cleaning_completion：三人房退两人
assert room_after_second_checkout.version > version_seen_by_cleaner
assert stale_completion_response.status_code == 409
assert reloaded_room.cleaning_status == "pending"
assert remaining_stay.status == "checked_in"
```
- [ ] 运行 `"$PY" -m pytest tests/api/test_housing_cleaning.py tests/db/test_housing_cleaning_concurrency.py -q`，确认业务缺失导致失败。
- [ ] 在 `confirm_checkout` 与 `transfer` 成功路径同事务调用 mark；统一 `_require_allocatable_room` 检查 pending，覆盖 create/backfill/confirm/transfer-destination。核实源房退房及迁出不误用准入检查，取消预约/计划日期/历史作废不触发 mark。
- [ ] 实现 complete 的版本校验、锁后当前授权、审计同事务；已 ready 的重复请求不写第二条完成审计。加入每日保洁 role gate：旧请求未带该字段仍按原行为处理，不能借可选字段绕过角色限制。权限查询/事务锁遵循现有人员→房间→住宿顺序。
- [ ] 扩展 `_is_assignable`、`_room_card`、统计、`_matches_filters`、调房候选及 API GET 的 `cleaning_statuses`、`daily_cleaning_enabled` 筛选；共享谓词用于后续导出，不更改旧历史导出的默认范围。
- [ ] 重跑本任务与全部住房生命周期/调房/问题房/历史作废/人员房态相关 API 和 DB 测试，断言既有出房能力保持。通过后审查并提交 `feat: enforce room cleaning lifecycle`。

## Task 3: 当前房态一致性快照导出

**Files:** 新增 `apps/api/app/housing/room_status_export_service.py`、`room_status_export_renderer.py`、`room_status_export_router.py`；修改 `housing/schemas.py`、`dependencies.py`（仅确有需要的适配）、`app/api/router.py`、`app/audit/service.py`；新建 `tests/api/test_housing_room_status_export.py`、`tests/unit/housing/test_room_status_export.py`、`tests/db/test_housing_room_status_export_concurrency.py`。旧 `occupancy_export_service.py` 仅允许提取等价公共格式函数，不改语义。

**Interfaces:** `HousingRoomStatusExportRequest` 含 format(xlsx/csv)、purpose(2–200 字)、building_ids/floor_ids(UUID 数组，默认空表示全部有权限范围)、query、room_types、verification_statuses、service_statuses、current_gender、occupancy、assignability、cleaning_statuses、daily_cleaning_enabled；数组去重，楼层必须属于所选楼栋；不得用 page 参数截断导出。

`HousingRoomStatusExportService(db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest, actor_user_id: UUID).rows(request: HousingRoomStatusExportRequest, *, as_of: datetime) -> list[RoomStatusExportRow]`；`render_room_status_export(rows: Sequence[RoomStatusExportRow], *, format: Literal['xlsx','csv'], occurred_at: datetime) -> tuple[bytes,str,str]`，文件名 `housing-room-status-YYYYMMDD.xlsx/csv`。Row 字段按规格第 5 节固定 21 列；空房的人员/日期字段全部空。

`RoomStatusExportRow` 的21列字段依次为 building_name/floor_name/room_number/room_type（str）、capacity/current_occupants（int）、occupancy_status/verification_status/service_status（str）、is_assignable/daily_cleaning_enabled（bool）、cleaning_status（CleaningStatus）、person_name/sex/identity_number/phone（str | None）、actual_check_in_at/planned_check_out_at（datetime | None）、person_type/account_status/profile_source（str | None）。原始枚举由 renderer 转为中文，不混用已翻译标签与业务判断。

- [ ] 写失败测试：空房 1 行、两位现住者 2 行、checkout_pending 仍现住、旧 checked_out/voided/planned 不出现；多栋 OR 与维度 AND、跨页全部结果、显式越权楼栋/楼层拒绝且不泄漏存在性、空匹配返回中文 422 `housing_export_empty`。
- [ ] 写渲染测试：21 列顺序、长期离开日期空、日期中国边界、公式前缀转义、原历史导出仍包含 checked_out 且列不变。写双连接并发测试 `test_snapshot_remains_consistent_during_transfer_and_person_edit`，同时核对人数与姓名来自一个快照。

```python
# test_snapshot_includes_empty_rooms_and_only_current_people
assert len(empty_room_rows) == 1 and empty_room_rows[0].person_name is None
assert len(double_room_rows) == 2
assert {row.person_name for row in double_room_rows} == {"虚拟住客甲", "虚拟住客乙"}
assert all(row.current_occupants == 2 for row in double_room_rows)
assert "虚拟历史住客" not in {row.person_name for row in rows}
```
- [ ] 运行 `"$PY" -m pytest tests/unit/housing/test_room_status_export.py tests/api/test_housing_room_status_export.py tests/db/test_housing_room_status_export_concurrency.py -q` 验证 RED。
- [ ] 使用现有 `get_housing_reporting_db` 的新独立 Session 模式，首次 SQL 前设 REPEATABLE READ 并断言隔离级别，参考 reporting_service 的 `_repeatable_read_connection`；不在请求鉴权已开启的事务里后切隔离级别。同一 snapshot 内读范围、房间投影、当前有效住宿及身份，不从不同 Session 混入人员字段。
- [ ] 请求开始及返回文件前重新核验当前账号/会话与房态导出授权；权限版本变动直接拒绝本次结果，不把旧 snapshot 的授权当永久许可。导出审计成功提交后才能返回文件，审计失败不返回字节；快照 Session 单独结束。上限明确为 10,000 匹配房间、50,000 输出行，超限要求缩小范围，不静默截断。
- [ ] 重跑本任务与旧 `tests/unit/housing/test_occupancy_export.py`、`tests/api/test_housing.py`、`tests/api/test_housing_reporting.py`；通过后审查提交 `feat: export current housing snapshots`。

## Task 4: 三槽照片、图像处理及受限读取

**Files:** 新增 `apps/api/app/housing/room_photo_images.py`、`room_photo_service.py`、`room_photo_router.py`；主集成者修改 `housing/schemas.py`、`occupancy_router.py` 的元数据投影、`app/api/router.py`、`app/audit/service.py`；新建 `tests/unit/housing/test_room_photo_images.py`、`tests/api/test_housing_room_photos.py`、`tests/db/test_housing_room_photos_concurrency.py`。

**Interfaces:** `prepare_room_photo(content: bytes, declared_mime_type: str | None) -> PreparedRoomPhoto`，dataclass 字段 content/mime_type/byte_size/sha256/thumbnail_content/thumbnail_mime_type/thumbnail_byte_size。`HousingRoomPhotoService.put(room_id: UUID, slot: int, prepared: PreparedRoomPhoto, *, version: int, actor_user_id: UUID, occurred_at: datetime, authorize_room: Callable[[HousingRoom], None]) -> HousingRoomPhoto`；`delete(room_id: UUID, photo_id: UUID, *, version: int, actor_user_id: UUID, occurred_at: datetime, authorize_room: Callable[[HousingRoom], None]) -> None`。

API：`PUT /housing/rooms/{room_id}/photos/{slot}`，multipart file+version；`DELETE /housing/rooms/{room_id}/photos/{photo_id}`，JSON version；两者返回刷新后的 `HousingRoomDetail`（包含当前 room.version、照片元数据）。`GET /housing/rooms/{room_id}/photos/{photo_id}?variant=original|thumbnail` 返回鉴权后的图片，默认 original。不同 HTTP method 的路径解析必须有 slot 1–3 与 UUID 校验。

- [ ] 写图像失败测试：10 MiB+1、空/假 MIME/损坏图、像素超过 50,000,000、多帧图拒绝；EXIF 方向 6 校正、GPS/EXIF 移除、透明 PNG 保留透明；`test_thumbnail_long_edge_at_most_480` 断言尺寸与可解码性。
- [ ] 写 API/并发失败测试：0–3 张、slot4拒绝、同时抢同槽一人成功另一人409、替换验证失败旧 sha不变、删除重建后旧 room.version 拒绝、第三张限制由DB约束支持；权限撤销/跨楼栋/owner-summary-only/CSRF拒绝；列表查询不选 content/thumbnail_content，详情只批量读元数据。

```python
# test_invalid_replacement_preserves_original_photo
assert invalid_response.status_code == 422
assert photo_after.sha256 == original_sha256
assert room_after.version == original_room_version
# test_three_slots_have_no_fourth_slot
assert sorted(photo.slot for photo in photos) == [1, 2, 3]
assert fourth_slot_response.status_code == 422
```
- [ ] 运行 `"$PY" -m pytest tests/unit/housing/test_room_photo_images.py tests/api/test_housing_room_photos.py tests/db/test_housing_room_photos_concurrency.py -q` 验证 RED。
- [ ] 对上传流仅读取到上限+1，校验真实格式/尺寸/帧数，重新打开 load 后 `ImageOps.exif_transpose`，在新像素图中编码 JPEG/PNG/WebP，不携带 EXIF/ICC/XMP；缩略图最长边480，不放大。重新编码后的原图仍不得超10 MiB；验证与编码在事务外，失败绝不删除旧图。不修改资产照片的旧接口行为。
- [ ] 实现锁后当前角色/scope/deny与 version 复核，同事务保存两种图像/元信息/审计并递增 room.version；删除审计保留目标标识但不保留图像内容。GET 使用完整房态详情的授权路径，不以个人/邀请摘要的可见性代替；返回 private,no-store 和 nosniff，未授权统一私有404。
- [ ] 重跑本任务及资产照片原测试防回归；通过后审查提交 `feat: manage private room photos`。

## Task 5: 保洁页面、权限状态及筛选持久化

**Files:** 修改 `apps/web/src/housing/api.ts`、`housingKeys.ts`、`HousingOverviewPage.tsx`、`HousingFilters.tsx`、`RoomCard.tsx`、`HousingRoomDetailPage.tsx`、`HousingCatalogDialogs.tsx`、`useHousingRoomLifecycle.tsx`、`housing.css`、`apps/web/src/settings/AuditPage.tsx`；新增 `HousingCleaningDialog.tsx`。扩展相应 `.test.tsx`、`api.test.ts` 和 `apps/web/src/app/housingFocus.test.tsx`。

**Interfaces:** TS 字段按全局 DTO；`completeRoomCleaning(roomId: string, input: {version: number}): Promise<HousingRoom>`。`HousingFiltersValue` 新增 `cleaningStatuses?: CleaningStatus[]`、`dailyCleaningEnabled?: boolean`；false 必须保留，不当作未筛选。所有 query key 仍位于 account/accessRevision 下；`HousingCleaningDialog` 使用通用 DialogShell、当前房号和预期版本及现有 pending/error/onClose/onSubmit 模式。

- [ ] 先写失败测试：occupied+pending 仍红色且保洁标签可见；unoccupied+pending 非绿色且不能补住；每日保洁 false 序列化不丢；完成失败保留提示，409刷新状态不自动重试；每日保洁编辑不覆盖草稿；隐藏操作不替代API权限。
- [ ] 添加切后台恢复保洁筛选、换号清空、旧 permission revision 的迟到响应不能覆盖新状态、coordinator 防重复点击测试。运行 `npm --prefix apps/web test -- src/housing src/app/housingFocus.test.tsx --maxWorkers=2` 验证 RED。

```tsx
// keeps occupied pending rooms occupied and serializes the false filter
expect(screen.getByText("待保洁")).toBeVisible()
expect(card).toHaveClass("housingRoomCard--occupied")
expect(serializedQuery.get("daily_cleaning_enabled")).toBe("false")
```
- [ ] 扩展规范化筛选、统计点击、卡片/详情及房间编辑；通过 allowed_actions 决定入口。每日保洁只读旧值与草稿分离；照片功能此任务只接 count 与接口类型，不实现新组件。
- [ ] 完成保洁共用 mutation coordinator 与 permission/epoch guard，成功失效 overview/detail/transfer-options/person summaries；保留调房对源/目标房失效。审计动作添加中文标签，不显示二进制或敏感请求体。
- [ ] 重跑房态相关 Vitest、`npm --prefix apps/web run build`、`npm --prefix apps/web run lint`；通过后审查提交 `feat: show room cleaning controls and filters`。

## Task 6: 导出弹窗与照片前端

**Files:** 新增 `apps/web/src/housing/HousingExportDialog.tsx`、`HousingRoomStatusExportDialog.tsx`、`HousingRoomPhotos.tsx` 及各自 `.test.tsx`；修改既有 `HousingOccupancyExportDialog.tsx` 仅兼容容器/旧类型显示、主集成者接线 `HousingOverviewPage.tsx`、`HousingRoomDetailPage.tsx`、`api.ts`、`housingKeys.ts`、`housing.css`。

**Interfaces:** `exportHousingRoomStatus(input: HousingRoomStatusExportInput): Promise<{blob: Blob; filename: string}>`；input字段与任务3一致，UI楼栋/楼层数组独立于概览单栋filter。`uploadRoomPhoto(roomId: string, slot: 1|2|3, file: File, version: number): Promise<HousingRoomDetail>`、`deleteRoomPhoto(roomId: string, photoId: string, version: number): Promise<HousingRoomDetail>`、`fetchRoomPhoto(roomId: string, photoId: string, variant: 'original'|'thumbnail'): Promise<Blob>`。

- [ ] 写导出失败测试：默认快照、带入页面筛选、导出draft改变不改页面、切换类型调用精确新/旧端点、选多栋清理不匹配楼层、空结果中文提示、用途2–200字、两种文件名严格校验、权限变更后迟到文件不下载。
- [ ] 写照片失败测试：3槽、第四张不能选、加载/替换失败保持原图、409保留原草稿并提示重新核对、无权限只读、未点击不请求原图、放大/关闭焦点恢复、手机尺寸。读取以受控 Blob/object URL 展示；卸载/撤权/换号/被替换后 revoke，丢弃迟到响应。选图前清晰提示支持格式和10 MiB上限。
- [ ] 运行 `npm --prefix apps/web test -- src/housing/HousingExportDialog.test.tsx src/housing/HousingRoomStatusExportDialog.test.tsx src/housing/HousingRoomPhotos.test.tsx --maxWorkers=2` 验证 RED。

```tsx
// exports with an independent draft and waits to fetch full-size photos
expect(exportHousingRoomStatus).toHaveBeenCalledWith(expect.objectContaining({building_ids: [buildingA, buildingB]}))
expect(pageFilters).toEqual(originalPageFilters)
expect(fetchRoomPhoto).not.toHaveBeenCalledWith(roomId, photoId, "original")
```
- [ ] 实现独立导出draft与旧类型入口，不修改 MyHousingPage 或邀请摘要；照片只接完整房间详情，缩略图按需加载，原图点击后取。上传/delete使用详情现有 coordinator，成功更新room版本并失效对应列表/详情，所有临时URL均释放。
- [ ] 重跑本任务、房态 Vitest、AuditPage 测试、build/lint；通过后审查提交 `feat: add housing snapshot and photo dialogs`。

## Task 7: 集成、迁移恢复、真实浏览器与 NAS 发布

**Files:** 新增 `apps/web/tests/housing-cleaning-photos-export.spec.ts`、`apps/api/app/cli/seed_housing_care_e2e.py`、`scripts/run-housing-care-e2e.sh`；新增 `apps/api/tests/cli/housing_care_restore_probe.py`、`test_housing_care_backup_docker.py`，参照现有 history_void 的严格容器身份/独占volume验证。脱敏证据放 `docs/superpowers/verification/2026-10-23-housing-care/`。

**Interfaces:** seed 仅用于经安全检测的独占本地库，提供真正 admin/administration/super_admin/coach 和跨栋deny账号、空房/双人房/维修/待核对/长期住宿场景；不复用线上个人资料。浏览器脚本沿用本地launchers并精确追踪自身 PID/端口，清理不得影响其他项目。

- [ ] 为launcher/restore probe先写 fail-closed 测试：指向未知库、远程Docker、非本任务volume或恢复后照片/保洁hash缺失时失败；对真正隔离实例执行加密备份→恢复，比对原图/缩略图hash、保洁状态/版本/审计及原有人员住宿字段。

```python
# test_restore_preserves_room_care_and_photo_content
assert restored_photo_sha256s == expected_photo_sha256s
assert restored_thumbnail_sha256s == expected_thumbnail_sha256s
assert restored_room_care == expected_room_care
assert restored_person_and_stays == expected_person_and_stays
```

- [ ] 在 `P/apps/api` 先跑 `"$PY" -m pytest tests/cli/test_housing_care_backup_docker.py -q` 验证新增纯逻辑测试的 RED/GREEN；真正恢复演练由新增明确 opt-in `RUN_HOUSING_CARE_RESTORE_TESTS=1` 开启，必须检查独占容器/volume ledger，未开启的跳过不能算恢复通过。
- [ ] 新e2e覆盖实际退房→待保洁→新入住拒绝→完成→允许、多人房退一人/再退一人、调房源待保洁、照片上传替换删除/放大、导出下载解析空房和当前人员、历史类型兼容；通过桌面1440/1024、平板768、手机360与touch390，不把窄屏模拟当iPhone真机验收。
- [ ] 串行跑适用全后端回归（含迁移、住房、人员、邀请摘要、资产照片、备份CLI）；同时可运行有限worker的前端全量suite。运行 Ruff、Mypy、前端build/lint、`git diff --check`；每条命令留退出码/数量，已知基线异常单列，任何本改动回归必须修复。
- [ ] 在工作区 P、设置仅指向独占测试库的环境后：`E2E_PYTHON_BIN="$PY" bash scripts/run-housing-care-e2e.sh`；相关旧房态/导出/筛选focus场景继续运行。launcher新增测试凭据只保存在受限临时目录，验收后撤销与清理。
- [ ] 独立全改动审查：重点 5 项 Review Focus、权限绕过、并发锁/版本、快照一致性、图片查询与内存上限；修复后重跑对应测试，不用忽略断言或延长超时掩盖错误。主代理自行完成规格覆盖复核。
- [ ] 部署前记录将发布的源tree摘要、实际NAS当前迁移和备份状况；不重启或重建数据库卷。在 P 执行 `PATH="$L/.venv/bin:$PATH" bash scripts/deploy-nas.sh --source "$P"`，不得 ad hoc 文件复制或 Docker 发布。预检/备份/迁移/health任一失败即停止并报告，不声称上线成功。执行时发现 L 中另一版本启动器与 P 守卫不兼容；P 的标准脚本与守卫 SHA-256 已只读核对为 NAS 当前部署的相同版本，配套预检已通过，未降低任何发布门禁。
- [ ] 发布成功后只读核对外网HTTPS18080、实际新head、容器健康、授权入口；读取旧房间默认值/人员与入住未被初始化改写。不要在 NAS 上替真实人员退房或上传试验照片。清理本任务独占测试资料后给出功能入口、测试/发布证据及真实设备尚未验收等限制。

## 执行顺序与协调

任务1先冻结模型/接口 → 任务2保洁后端 → 任务3快照后端与任务4照片后端可独立并行（共享文件交主集成者）→ 任务5/6前端接线及联合回归 → 任务7审查、恢复演练、发布。界面隔离组件可以按已冻结接口提早并行，但不能提前宣称联调通过。

采用子任务驱动执行，共享文件串行交接，独立模块并行；同一数据库绝不同时重置。每项有实现者与独立审查，最后全改动审查。用户已确认本计划。

## 计划自查与交接

规格覆盖：第3节→任务1/2/5；第4节→任务2/4/5；第5节→任务3/6；第6节→任务1/4/6；第7节→任务1/7；第8节→各任务RED/GREEN及任务7。五项 Review Focus 均分配到明确测试。新接口、字段名和权限条件在本文统一；老导出不修改语义；未把已有资产照片校验误当作去EXIF处理。

实施已在本会话中完成；分项实现、测试及审查证据记录在本计划独占执行账本。2026-10-23 已完成完整目录分组回归及受影响整文件复跑、实际浏览器检查、加密恢复演练和标准脚本 NAS 部署。发布 `20261023T104116Z-62cb1a29b325-b45cb60ae312`、迁移 `0071_housing_room_care`、部署状态 `complete`。原始计划中的提交步骤未执行：共享脏工作区及未提交改动保留，任务相对补丁证据保留；不提交无关改动。完整实际结果与初次失败、修正和复测均见 `docs/superpowers/verification/2026-10-23-housing-care/task-7-browser-and-restore.md`，不将计划复选框视为独立证据。
