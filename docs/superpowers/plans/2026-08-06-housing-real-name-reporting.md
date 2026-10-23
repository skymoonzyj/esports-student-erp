# 房态实名入住导出与人工报送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有房态领域内增加可审计、可重新下载、按楼栋/楼层授权的实名入住报送批次，同时提供响应式后台页面和真实 PostgreSQL 四视口验收。

**Architecture:** PostgreSQL 保存不可变、版本化、应用层加密的报送投影；生成服务在 `REPEATABLE READ` 一致性快照中读取住宿和实名资料，并把明确楼层范围固化到批次。Excel 和 CSV ZIP 只从同一加密快照按需在内存中生成；页面只展示批次摘要，全部敏感操作重新鉴权并写安全审计。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL、Alembic、Pydantic 2、AES-GCM envelope encryption、openpyxl 3.1、React 19、TypeScript 6、TanStack Query、Vitest、Playwright。

---

## 实施约束与文件边界

- 工作树固定为 `/Users/skymoonzyj/Documents/new project3/.worktrees/housing-admin-operations-ui`，不得修改 Docker/NAS 或公安在线 API 状态。
- 8 个工作流共享同一工作树时，工作代理不得执行 `git add`、`git commit`、`git rebase` 或 `git push`；主任务在每个集成点审查并提交。
- 新模块使用 `reporting_` 前缀，避免继续扩大现有 `housing/schemas.py`、`housing/occupancy_service.py` 和 `housing/api.ts`。
- 后端测试必须用当前工作树的 `PYTHONPATH=$PWD/apps/api`。数据库节点必须设置隔离的 `TEST_DATABASE_URL`，未设置而跳过不能算通过。
- 所有拒绝路径都要覆盖“不存在、软删除/停用、显式拒绝、错范围、撤权”及零批次写入；具体批次越权与不存在保持私有 404 等价。
- 所有前端私有请求及下载都使用 `Cache-Control: no-store, private`，下载响应同时要求 `X-Content-Type-Options: nosniff`。

## 文件责任图

| 工作流 | 独占创建/修改文件 |
| --- | --- |
| 1 数据库/RBAC | `reporting_models.py`、`0011_housing_real_name_reporting.py`、`migrations/env.py`、`rbac/seed.py`、`housing/access.py`、迁移/RBAC/access 测试 |
| 2 报送投影 | `reporting_types.py`、`reporting_projection.py`、投影单元与 PostgreSQL 查询测试 |
| 3 快照与文件 | `reporting_snapshot.py`、`reporting_renderer.py`、`pyproject.toml`、快照/文件测试 |
| 4 业务服务 | `reporting_service.py`、服务/状态机/并发测试 |
| 5 API 与审计 | `reporting_schemas.py`、`reporting_router.py`、`api/router.py`、`housing/errors.py`、`housing/api_support.py`、`audit/service.py`、API/审计测试 |
| 6 Web 数据层 | `reportingApi.ts`、`reportingKeys.ts` 及对应测试 |
| 7 响应式 UI | `HousingReportsPage.tsx`、`HousingReportDialogs.tsx`、组件测试、`housing.css` |
| 8 路由/E2E/文档 | `app/router.tsx`、路由测试、报送 E2E seeder/测试、两条 Playwright 旅程、启动器、schema manifest、README |

### Task 1: 数据模型、迁移和敏感 RBAC

**Files:**
- Create: `apps/api/app/housing/reporting_models.py`
- Create: `apps/api/migrations/versions/0011_housing_real_name_reporting.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/housing/access.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `apps/api/tests/unit/housing/test_access.py`
- Create: `apps/api/tests/db/test_housing_reporting_models.py`

- [ ] **Step 1: 先写权限和模型约束的失败测试**

```python
REPORT_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000043")


def test_real_name_report_permission_is_sensitive_and_admin_only(postgres_engine):
    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(
                Permission.key == "housing.reports.export_sensitive"
            )
        )
        assert permission is not None
        assert (permission.id, permission.sensitive) == (REPORT_PERMISSION_ID, True)
        grants = set(
            db.execute(
                select(Role.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .where(RolePermission.permission_id == REPORT_PERMISSION_ID)
            ).tuples()
        )
        assert grants == {("admin", "global")}


def test_success_batch_requires_snapshot_and_state_fields(postgres_engine):
    with pytest.raises(IntegrityError):
        with Session(postgres_engine) as db, db.begin():
            db.add(
                HousingReportBatch(
                    batch_number="HR-20260806-000001",
                    status="generated",
                    version=1,
                    range_start=UTC_START,
                    range_end=UTC_END,
                    purpose="派出所人工报送",
                    projection_schema_version=1,
                    resident_count=1,
                    stay_row_count=1,
                    guardian_row_count=0,
                    created_by_user_id=ADMIN_ID,
                )
            )
```

- [ ] **Step 2: 运行聚焦测试并确认红灯**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/db/test_housing_reporting_models.py \
  tests/db/test_rbac_seed.py::test_real_name_report_permission_is_sensitive_and_admin_only \
  tests/unit/housing/test_access.py -q -p no:cacheprovider
```

Expected: collection fails because `app.housing.reporting_models` and permission UUID `20000000-0000-4000-8000-000000000043` do not exist.

- [ ] **Step 3: 建立批次和规范化范围 ORM**

`reporting_models.py` defines exactly these persistent boundaries:

```python
class HousingReportBatch(Base):
    __tablename__ = "housing_report_batches"
    id: Mapped[UUID]
    batch_number: Mapped[str]
    status: Mapped[str]
    version: Mapped[int]
    range_start: Mapped[datetime]
    range_end: Mapped[datetime]
    cohort_filter: Mapped[list[str]]
    person_type_filter: Mapped[list[str]]
    purpose: Mapped[str]
    projection_schema_version: Mapped[int]
    snapshot_ciphertext: Mapped[str | None]
    snapshot_key_version: Mapped[int | None]
    snapshot_digest: Mapped[str | None]
    resident_count: Mapped[int]
    stay_row_count: Mapped[int]
    guardian_row_count: Mapped[int]
    created_at: Mapped[datetime]
    created_by_user_id: Mapped[UUID]
    submitted_at: Mapped[datetime | None]
    submitted_by_user_id: Mapped[UUID | None]
    submission_note: Mapped[str | None]
    voided_at: Mapped[datetime | None]
    voided_by_user_id: Mapped[UUID | None]
    void_reason: Mapped[str | None]
    corrects_batch_id: Mapped[UUID | None]
    failure_code: Mapped[str | None]
    failure_summary: Mapped[dict[str, object] | None]


class HousingReportBatchScope(Base):
    __tablename__ = "housing_report_batch_scopes"
    batch_id: Mapped[UUID]
    building_id: Mapped[UUID]
    floor_id: Mapped[UUID]
```

Use database checks for `range_start < range_end`, non-empty trimmed purpose, status membership, `version >= 1`, non-negative counts, 64-character lowercase hex digest, success/failed snapshot groups, submitted/voided field groups, unique `(batch_id, floor_id)`, and `corrects_batch_id <> id`. Use a PostgreSQL sequence for the numeric suffix and a unique `batch_number`; never use `MAX + 1`.

- [ ] **Step 4: Add revision `0011_housing_real_name_reporting`**

The migration must create the sequence, both tables, indexes for status/created time/range/corrects, self-reference and scope foreign keys, then insert permission UUID `20000000-0000-4000-8000-000000000043` plus the admin/global grant. Downgrade removes only that grant/permission and the two new tables/sequence, preserving revision `0010` data.

`migrations/env.py` must import the new metadata module:

```python
from app.housing import reporting_models  # noqa: F401
```

- [ ] **Step 5: Extend code seed and deny-first access helpers**

```python
REPORT_PERMISSION = "housing.reports.export_sensitive"


def can_export_report_floor(self, floor: HousingFloor) -> bool:
    return self.can(
        REPORT_PERMISSION,
        self.floor_tokens(floor.building_id, floor.id),
    )


def report_floor_criteria(self) -> ColumnElement[bool]:
    return self.floor_permission_criteria(REPORT_PERMISSION)
```

Add the permission to `_HOUSING_PERMISSIONS` so `access_revision` changes immediately on grant/revoke. Add floor-level SQL criteria parallel to room criteria and test global allow, building allow, floor allow, floor deny overriding building/global allow, unrelated permissions, and account overrides.

- [ ] **Step 6: Verify migration upgrade/downgrade and constraints**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/db/test_migrations.py \
  tests/db/test_rbac_seed.py \
  tests/db/test_housing_reporting_models.py \
  tests/unit/housing/test_access.py -q -p no:cacheprovider
```

Expected: all selected tests pass against the isolated PostgreSQL database; no database-marked node is skipped.

- [ ] **Step 7: 主任务审查集成点**

Run `git diff --check` and inspect `git diff -- apps/api/app/housing/reporting_models.py apps/api/migrations/versions/0011_housing_real_name_reporting.py apps/api/app/rbac/seed.py apps/api/app/housing/access.py`. Then the main task creates one commit: `feat: add housing report persistence and permission`.

### Task 2: 一致性报送投影和实名完整性

**Files:**
- Create: `apps/api/app/housing/reporting_types.py`
- Create: `apps/api/app/housing/reporting_projection.py`
- Create: `apps/api/tests/unit/housing/test_reporting_projection.py`
- Create: `apps/api/tests/db/test_housing_reporting_projection.py`

- [ ] **Step 1: 定义严格投影类型并写纯规则失败测试**

```python
def test_pending_checkout_request_does_not_cut_off_actual_stay():
    stay = candidate_stay(
        status="checkout_pending",
        actual_check_in_at=dt("2026-08-01T00:00:00+08:00"),
        actual_check_out_at=None,
        checkout_requested_actual_at=dt("2026-08-03T00:00:00+08:00"),
    )
    assert overlaps_actual_stay(
        stay,
        start=dt("2026-08-05T00:00:00+08:00"),
        end=dt("2026-08-06T00:00:00+08:00"),
    )


def test_minor_projection_exports_every_effective_adult_guardian():
    result = build_projection_from_candidates(
        request=report_request(),
        stays=[minor_student_stay()],
        guardians=[effective_guardian("母亲"), effective_guardian("父亲")],
    )
    assert [row.guardian_name for row in result.projection.guardians] == ["父亲", "母亲"]
```

- [ ] **Step 2: Run the projection tests to verify they fail**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD pytest tests/unit/housing/test_reporting_projection.py -q -p no:cacheprovider
```

Expected: import fails because `reporting_types` and `reporting_projection` are absent.

- [ ] **Step 3: Implement version-1 typed projection**

Use frozen Pydantic models with `extra="forbid"`:

```python
class ResidentReportRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    row_id: str
    person_id: UUID
    full_name: str
    phone: str
    identity_number: str
    sex: Literal["male", "female"]
    date_of_birth: date
    person_type: ResidentPersonType
    building_id: UUID
    building_name: str
    building_order: int
    floor_id: UUID
    floor_name: str
    floor_order: int
    room_id: UUID
    room_number: str
    stay_id: UUID
    stay_purpose: Literal["permanent", "camp", "temporary"]
    actual_check_in_at: datetime
    actual_check_out_at: datetime | None
    stay_status: Literal["checked_in", "checkout_pending", "checked_out"]
    cohort_id: UUID | None
    cohort_name: str | None
    visit_reason: str | None
    is_minor_at_check_in: bool
    guardian_count: int


class HousingReportProjectionV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    generated_at: datetime
    range_start: datetime
    range_end: datetime
    residents: Sequence[ResidentReportRow]
    guardians: Sequence[GuardianReportRow]
```

`ResidentPersonType` contains `student`, `coach`, `life_teacher`, `customer_service`, `administration`, `other`, and `visitor`. An assistant coach remains classified by `StaffProfile.staff_type`.

- [ ] **Step 4: Implement pure overlap, age and relationship rules**

```python
def overlaps_actual_stay(stay: StayCandidate, *, start: datetime, end: datetime) -> bool:
    if start >= end or stay.actual_check_in_at is None:
        return False
    if stay.status not in {"checked_in", "checkout_pending", "checked_out"}:
        return False
    effective_end = stay.actual_check_out_at if stay.status == "checked_out" else None
    return stay.actual_check_in_at < end and (effective_end is None or effective_end > start)


def is_minor_on(birth_date: date, reference_date: date) -> bool:
    eighteenth = birth_date.replace(year=birth_date.year + 18)
    return reference_date < eighteenth
```

Handle February 29 by mapping the eighteenth birthday to February 28 in a non-leap year, matching the existing identity age rule. Relationship validity uses the Shanghai check-in date for `valid_from/valid_until` and the absolute check-in timestamp for `ended_at`.

- [ ] **Step 5: Implement the PostgreSQL candidate query and decryption boundary**

`HousingReportProjectionBuilder.build()` accepts one `Session`, `EnvelopeCipher`, request filters and already-authorized floor IDs. Its SQL joins `HousingStay`, current room/floor/building, retained `Person`, one of `StudentProfile`/`StaffProfile`/`VisitorProfile`, optional `Cohort`, and effective guardians. It requires actual check-in, filters only `checked_in`, `checkout_pending`, `checked_out`, applies half-open overlap, filters the exact floor set and optional cohort/person types, and orders by building/floor display order, room number, actual check-in, name and generated row ID.

Decrypt using existing field names:

```python
phone = cipher.decrypt(person.phone_ciphertext, field_name="phone")
identity = cipher.decrypt(person.identity_ciphertext, field_name="identity_number")
```

Convert `EncryptedFieldError` and missing required values into safe `ProjectionIssue(person_id, stay_id, masked_phone, missing_fields)` entries. Never include full phone or identity in an exception string.

- [ ] **Step 6: Cover DB filters, history and counts**

Add PostgreSQL cases for boundary equality, cancelled/planned exclusion, transfer rows, deduplicated resident count, checkout pending after requested departure, completed checkout, building/floor/cohort/person combinations, soft-deleted people, inactive accounts, temporary visitor reason, and all guardian completeness failures.

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/unit/housing/test_reporting_projection.py \
  tests/db/test_housing_reporting_projection.py -q -p no:cacheprovider
```

Expected: all selected tests pass; PostgreSQL test count is non-zero and no selected node is skipped.

- [ ] **Step 7: 主任务审查集成点**

Review exception text and repository-search for projection data leaks:

```bash
rg -n "identity_number|phone|snapshot" apps/api/app/housing/reporting_*.py
git diff --check
```

Main task commit: `feat: project housing real-name stays`.

### Task 3: 不可变快照、Excel 和 CSV ZIP

**Files:**
- Modify: `apps/api/pyproject.toml`
- Create: `apps/api/app/housing/reporting_snapshot.py`
- Create: `apps/api/app/housing/reporting_renderer.py`
- Create: `apps/api/tests/unit/housing/test_reporting_snapshot.py`
- Create: `apps/api/tests/unit/housing/test_reporting_renderer.py`

- [ ] **Step 1: Add failing deterministic snapshot and file-parity tests**

```python
def test_snapshot_is_deterministic_and_digest_covers_ciphertext(cipher):
    first = seal_projection(PROJECTION, cipher=cipher, key_version=1)
    assert open_projection(first, cipher=cipher) == PROJECTION
    assert first.digest == hashlib.sha256(first.ciphertext.encode("utf-8")).hexdigest()


def test_xlsx_and_csv_zip_have_the_same_safe_rows():
    xlsx_rows = parse_xlsx(render_xlsx(PROJECTION))
    csv_rows = parse_csv_zip(render_csv_zip(PROJECTION))
    assert xlsx_rows == csv_rows
    assert xlsx_rows["入住名单"][0]["姓名"] == "'=危险姓名"
```

- [ ] **Step 2: Run the new unit tests and confirm red**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD pytest \
  tests/unit/housing/test_reporting_snapshot.py \
  tests/unit/housing/test_reporting_renderer.py -q -p no:cacheprovider
```

Expected: missing reporting modules and `openpyxl` dependency.

- [ ] **Step 3: Add one bounded XLSX dependency**

Add exactly `"openpyxl>=3.1.5,<4"` to project dependencies. Do not add pandas, numpy or filesystem-backed export libraries.

- [ ] **Step 4: Implement canonical JSON and encrypted snapshot integrity**

```python
SNAPSHOT_FIELD_NAME = "housing_report_snapshot"
SUPPORTED_SNAPSHOT_KEYS = frozenset({1})


def canonical_projection_json(projection: HousingReportProjectionV1) -> str:
    return json.dumps(
        projection.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def seal_projection(projection, *, cipher, key_version=1) -> SealedSnapshot:
    if key_version not in SUPPORTED_SNAPSHOT_KEYS:
        raise SnapshotVersionError("unsupported snapshot key version")
    ciphertext = cipher.encrypt(
        canonical_projection_json(projection),
        field_name=SNAPSHOT_FIELD_NAME,
    )
    return SealedSnapshot(
        ciphertext=ciphertext,
        key_version=key_version,
        digest=hashlib.sha256(ciphertext.encode("utf-8")).hexdigest(),
    )
```

`open_projection()` verifies lowercase SHA-256 with `hmac.compare_digest`, key version, AES-GCM authentication, schema version and strict Pydantic parsing in that order.

- [ ] **Step 5: Implement formula-safe common tabular rows and block 公式注入**

```python
_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_spreadsheet_text(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_DANGEROUS_PREFIXES) else text
```

Both renderers consume the same ordered `resident_table()` and `guardian_table()` output. Phone, identity, room and batch-like identifiers are always strings. Date/time cells use Shanghai display strings so CSV and Excel parsed values remain equivalent.

- [ ] **Step 6: Render XLSX and CSV ZIP entirely in memory**

Excel uses `Workbook`, removes the default sheet, creates `入住名单` and `未成年监护人`, freezes `A2`, enables `auto_filter`, applies readable bounded widths, sets all identifying cells as text and clears workbook creator/title metadata. CSV uses `io.StringIO(newline="")`, `csv.writer`, UTF-8 BOM, and `zipfile.ZipFile(io.BytesIO(), "w", ZIP_DEFLATED)` with exactly `入住名单.csv` and `未成年监护人.csv`.

- [ ] **Step 7: Verify difficult content and corruption cases**

Cover Chinese, commas, quotes, CR/LF, leading zero, long identity, every dangerous prefix, empty guardian table, stable sorting, ciphertext mutation, wrong digest, unsupported key/schema, and metadata/filenames without PII.

Run:

```bash
cd apps/api
PYTHONPATH=$PWD pytest \
  tests/unit/housing/test_reporting_snapshot.py \
  tests/unit/housing/test_reporting_renderer.py -q -p no:cacheprovider
ruff check app/housing/reporting_snapshot.py app/housing/reporting_renderer.py tests/unit/housing/test_reporting_*.py
mypy app/housing/reporting_snapshot.py app/housing/reporting_renderer.py
```

Expected: all commands pass.

- [ ] **Step 8: 主任务审查集成点**

Inspect the dependency diff and ensure no file writes occur in renderer code. Main task commit: `feat: render encrypted housing report snapshots`.

### Task 4: 批次生成、状态机、下载和更正链服务

**Files:**
- Create: `apps/api/app/housing/reporting_service.py`
- Create: `apps/api/tests/services/test_housing_reporting.py`
- Create: `apps/api/tests/db/test_housing_reporting_concurrency.py`

- [ ] **Step 1: Write service state-machine and transaction failure tests first**

```python
@pytest.mark.parametrize(
    ("status", "action", "allowed"),
    [
        ("generated", "mark_submitted", True),
        ("generated", "void", True),
        ("marked_submitted", "void", True),
        ("marked_submitted", "mark_submitted", False),
        ("voided", "download", False),
        ("failed", "download", False),
    ],
)
def test_report_state_matrix(status, action, allowed):
    assert transition_allowed(status, action) is allowed


def test_audit_failure_rolls_back_success_batch(reporting_db, monkeypatch):
    monkeypatch.setattr(AuditService, "append", reject_audit)
    with pytest.raises(HousingAuditFailure):
        service(reporting_db).generate(valid_command())
    assert reporting_db.scalar(select(func.count(HousingReportBatch.id))) == 0
```

- [ ] **Step 2: Run service tests and confirm they fail**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/services/test_housing_reporting.py \
  tests/db/test_housing_reporting_concurrency.py -q -p no:cacheprovider
```

Expected: import failure for `HousingReportingService`.

- [ ] **Step 3: Implement command/result boundaries and state transitions**

```python
def transition_allowed(status: str, action: str) -> bool:
    return (status, action) in {
        ("generated", "mark_submitted"),
        ("generated", "void"),
        ("marked_submitted", "void"),
        ("generated", "download"),
        ("marked_submitted", "download"),
    }
```

`HousingReportingService` is constructed with the dedicated `Session`, cipher, actor account ID, `occurred_at` and request ID. It exposes exact methods `generate(command: GenerateReportCommand) -> ReportBatchSummary`, `list(query: ReportBatchQuery) -> ReportBatchPage`, `get(batch_id: UUID) -> ReportBatchDetail`, `download(batch_id: UUID, format: ReportFormat) -> ReportDownload`, `mark_submitted(batch_id: UUID, command: MarkSubmittedCommand) -> ReportBatchSummary`, and `void(batch_id: UUID, command: VoidReportCommand) -> ReportBatchSummary`. State-changing commands carry expected `version`; `mark_submitted` rejects future Shanghai/UTC instants, `void` requires a trimmed reason, and `generate` accepts `corrects_batch_id` only when the predecessor is already voided and all predecessor scopes are still authorized.

- [ ] **Step 4: Implement dedicated repeatable-read generation**

Before the dedicated `Session` issues its first SQL statement:

```python
connection = db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
assert connection.get_isolation_level() == "REPEATABLE READ"
```

Within one transaction: expand and verify every requested building/floor, build projection, write either one successful encrypted batch plus scopes or one safe `failed` batch plus scopes, append allowed/failed audit, then commit. Authentication/scope denial appends a denial audit in the route session and creates no batch. Empty projection uses `housing_report_empty`; incomplete projection uses `housing_export_incomplete` with only safe issue summaries.

- [ ] **Step 5: Implement private visibility, stable download and state locks**

List SQL returns a batch only when every persisted floor scope is currently allowed; it also returns status counts over the same visible filter. Get/download/actions first query with the exact authorized-scope predicate so missing and denied batches have the same not-found result. Download verifies status, ciphertext digest/key/schema, renders bytes, appends format-specific audit, commits, then returns `ReportDownload`.

State operations use `select(HousingReportBatch).with_for_update()`, compare expected version, update only state fields and increment version. Correction generation locks the direct predecessor; unique/locking rules make concurrent correction attempts deterministic.

- [ ] **Step 6: Cover concurrency and consistent-snapshot behavior**

Use two PostgreSQL sessions and barriers to prove:

- concurrent person/stay/guardian changes produce one complete before-or-after projection;
- concurrent submit/void has one success and one `housing_report_state_conflict`;
- concurrent corrections cannot both correct the same predecessor state/version;
- session remains usable after a caught conflict and explicit rollback;
- download after business-data mutation still parses the original snapshot;
- permission revoke between list and download blocks the download.

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/services/test_housing_reporting.py \
  tests/db/test_housing_reporting_concurrency.py -q -p no:cacheprovider
```

Expected: all selected PostgreSQL cases pass serially; none are skipped.

- [ ] **Step 7: 主任务审查集成点**

Inspect every `commit`, `rollback`, `begin`, `with_for_update` and audit call. Main task commit: `feat: manage housing report batches`.

### Task 5: FastAPI、私有 404、附件响应和审计白名单

**Files:**
- Create: `apps/api/app/housing/reporting_schemas.py`
- Create: `apps/api/app/housing/reporting_router.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/housing/errors.py`
- Modify: `apps/api/app/housing/api_support.py`
- Modify: `apps/api/app/audit/service.py`
- Create: `apps/api/tests/api/test_housing_reporting.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write API privacy, cache and audit allowlist failures**

```python
def test_denied_and_missing_batch_are_private_404_with_no_batch_write(client, db):
    before = db.scalar(select(func.count(HousingReportBatch.id)))
    denied = client.get(f"/api/v1/housing/report-batches/{OUT_OF_SCOPE_ID}")
    missing = client.get(f"/api/v1/housing/report-batches/{uuid4()}")
    assert (denied.status_code, denied.json()["code"]) == (404, "housing_resource_not_found")
    assert (missing.status_code, missing.json()["code"]) == (404, "housing_resource_not_found")
    assert db.scalar(select(func.count(HousingReportBatch.id))) == before


def test_xlsx_download_has_private_attachment_headers(client, batch):
    response = client.get(
        f"/api/v1/housing/report-batches/{batch.id}/download?format=xlsx"
    )
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
```

- [ ] **Step 2: Run focused API/audit tests and confirm red**

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/api/test_housing_reporting.py tests/api/test_audit.py -q -p no:cacheprovider
```

Expected: reporting routes and audit keys/codes are absent.

- [ ] **Step 3: Define strict request/response schemas**

```python
class GenerateHousingReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    range_start: datetime
    range_end: datetime
    scope_mode: Literal["authorized", "buildings", "floors"]
    building_ids: list[UUID] = Field(default_factory=list, max_length=100)
    floor_ids: list[UUID] = Field(default_factory=list, max_length=500)
    cohort_ids: list[UUID] = Field(default_factory=list, max_length=100)
    person_types: list[ResidentPersonType] = Field(default_factory=list)
    purpose: str = Field(min_length=2, max_length=200)
    corrects_batch_id: UUID | None = None


class MarkHousingReportSubmittedRequest(BaseModel):
    submitted_at: datetime
    note: str | None = Field(default=None, max_length=500)
    version: int = Field(ge=1)


class VoidHousingReportRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    version: int = Field(ge=1)
```

Validators require timezone-aware timestamps, a strict half-open range, and scope IDs consistent with `scope_mode`.

- [ ] **Step 4: Add public errors and typed audit metadata**

Add reporting service error subclasses for the eight public codes in the specification. Extend `_KNOWN_HOUSING_CONFLICT_CODES` and the problem mapping with exact statuses: empty/incomplete `422`, scope denied `403` only before a concrete private resource is selected, state conflict `409`, correction invalid `409`, snapshot unavailable/render failed `503`.

Extend audit allowlists with canonical UUID keys `batch_id`, `building_id`, `floor_id`, list key `scope_ids`, integer count keys, `format in {xlsx,csv}`, and every reporting public error code. Purpose, note, reason, name, phone, identity, issues and file bytes must remain rejected.

- [ ] **Step 5: Add private routes and dedicated reporting sessions**

```python
router = APIRouter(
    prefix="/housing/report-batches",
    tags=["housing-reporting"],
    route_class=PrivateHousingRoute,
)

@router.post("", response_model=HousingReportBatchSummaryResponse)
def generate_report(
    payload: GenerateHousingReportRequest,
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> HousingReportBatchSummaryResponse:
    result = service.generate(
        GenerateReportCommand(
            range_start=payload.range_start,
            range_end=payload.range_end,
            scope_mode=payload.scope_mode,
            building_ids=tuple(payload.building_ids),
            floor_ids=tuple(payload.floor_ids),
            cohort_ids=tuple(payload.cohort_ids),
            person_types=tuple(payload.person_types),
            purpose=payload.purpose,
            corrects_batch_id=payload.corrects_batch_id,
        )
    )
    return HousingReportBatchSummaryResponse.model_validate(
        result,
        from_attributes=True,
    )

@router.get("/{batch_id}/download")
def download_report(
    batch_id: UUID,
    format: Literal["xlsx", "csv"],
    service: Annotated[
        HousingReportingService,
        Depends(get_housing_reporting_service),
    ],
) -> Response:
    result = service.download(batch_id, format)
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

Register POST/list/detail/download/mark-submitted/void routes under `/api/v1`. Generation obtains a dedicated report `Session` for repeatable read; denial audit uses the ordinary request session. Do not serialize snapshot fields in any response model.

- [ ] **Step 6: Exercise every selector and side effect**

API tests cover unauthenticated, no capability, global/building/floor allow, explicit floor deny, mixed scope, deleted/disabled account, missing batch, wrong scope, revoked permission, validation failure, empty/incomplete, both downloads, repeated download audit, submit, void, correction, stale version, render/decrypt/audit failures, cache headers, and repository-wide absence of PII from audit/log/API JSON.

Run:

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/api/test_housing_reporting.py tests/api/test_audit.py -q -p no:cacheprovider
ruff check app/housing/reporting_router.py app/housing/reporting_schemas.py \
  app/housing/api_support.py app/audit/service.py tests/api/test_housing_reporting.py
mypy app/housing/reporting_router.py app/housing/reporting_schemas.py
```

Expected: tests, Ruff and mypy pass.

- [ ] **Step 7: 主任务审查集成点**

Search for accidental snapshot serialization and unbounded free-text audit values, then commit: `feat: expose audited housing report API`.

### Task 6: Web 报送 DTO、查询键和安全附件下载

**Files:**
- Create: `apps/web/src/housing/reportingApi.ts`
- Create: `apps/web/src/housing/reportingApi.test.ts`
- Create: `apps/web/src/housing/reportingKeys.ts`
- Create: `apps/web/src/housing/reportingKeys.test.ts`

- [ ] **Step 1: Write failing API and query-key tests**

```typescript
it("downloads a private attachment using the server filename", async () => {
  mockFetch(new Response(XLSX_BYTES, {
    status: 200,
    headers: {
      "Content-Disposition": 'attachment; filename="housing-report-HR-20260806-000001.xlsx"',
      "Cache-Control": "no-store, private",
    },
  }))
  const result = await downloadHousingReport(BATCH_ID, "xlsx")
  expect(result.filename).toBe("housing-report-HR-20260806-000001.xlsx")
  expect(await result.blob.arrayBuffer()).toEqual(XLSX_BYTES.buffer)
})


it("normalizes list filters into a stable query key", () => {
  expect(reportingKeys.list({ status: ["voided", "generated"], page: 1 }))
    .toEqual(["housing", "reports", "list", { page: 1, status: ["generated", "voided"] }])
})
```

- [ ] **Step 2: Run focused Vitest and confirm red**

Run:

```bash
npm --prefix apps/web test -- src/housing/reportingApi.test.ts src/housing/reportingKeys.test.ts
```

Expected: modules cannot be resolved.

- [ ] **Step 3: Define exact frontend contracts**

`reportingApi.ts` exports `HousingReportBatchSummary`, `HousingReportBatchDetail`, `HousingReportListResponse`, `GenerateHousingReportInput`, `MarkSubmittedInput`, `VoidReportInput`, `HousingReportIssue`, `HousingReportFormat`, plus six request functions matching the API routes. DTOs contain only summary, filter, scope, counts, status, operators/times and correction chain; no resident row, phone or identity field exists.

- [ ] **Step 4: Implement JSON requests and attachment parsing**

```typescript
export async function downloadHousingReport(
  batchId: string,
  format: HousingReportFormat,
): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(
    `/api/v1/housing/report-batches/${encodeURIComponent(batchId)}/download?format=${format}`,
    { credentials: "include", headers: { Accept: format === "xlsx" ? XLSX_MIME : ZIP_MIME } },
  )
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const fallback = `housing-report-${batchId}.${format === "xlsx" ? "xlsx" : "zip"}`
  return { blob: await response.blob(), filename: safeAttachmentFilename(disposition, fallback) }
}
```

`safeAttachmentFilename` accepts only the basename characters `[A-Za-z0-9._-]`, removes path separators/control characters, and falls back when the header is missing or malformed. `saveDownloadedBlob` uses an object URL, a temporary anchor, and always revokes/removes it in `finally`.

- [ ] **Step 5: Implement deterministic query keys and invalidation helpers**

Keys use `['housing','reports']` root, canonical sorted/deduplicated UUID/status arrays and ISO timestamps. Successful generation/action invalidates list root and exact detail; downloads never invalidate.

- [ ] **Step 6: Verify data layer**

Run:

```bash
npm --prefix apps/web test -- src/housing/reportingApi.test.ts src/housing/reportingKeys.test.ts
npm --prefix apps/web run lint -- src/housing/reportingApi.ts src/housing/reportingKeys.ts
npm --prefix apps/web run build
```

Expected: selected tests, lint and TypeScript production build pass.

- [ ] **Step 7: 主任务审查集成点**

Inspect that downloads send credentials, surface problem details, sanitize filenames and revoke object URLs. Main task commit: `feat: add housing report web client`.

### Task 7: 响应式报送页面、对话框和焦点恢复

**Files:**
- Create: `apps/web/src/housing/HousingReportsPage.tsx`
- Create: `apps/web/src/housing/HousingReportsPage.test.tsx`
- Create: `apps/web/src/housing/HousingReportDialogs.tsx`
- Create: `apps/web/src/housing/HousingReportDialogs.test.tsx`
- Modify: `apps/web/src/housing/housing.css`

- [ ] **Step 1: Write desktop/mobile and dialog behavior tests first**

```tsx
it("renders desktop rows and mobile cards from the same batch data", async () => {
  renderReports({ width: 360, batches: [GENERATED_BATCH] })
  expect(await screen.findByRole("article", { name: /HR-20260806-000001/ })).toBeVisible()
  expect(screen.queryByRole("table")).not.toBeVisible()

  setViewport(1024)
  renderReports({ width: 1024, batches: [GENERATED_BATCH] })
  expect(await screen.findByRole("table", { name: "实名报送批次" })).toBeVisible()
})


it("preserves generation input after an incomplete-data response", async () => {
  renderReports({ generateError: incompleteProblem() })
  await user.click(screen.getByRole("button", { name: "生成实名名单" }))
  await fillGenerationForm(user)
  await user.click(screen.getByRole("button", { name: "确认生成" }))
  expect(await screen.findByText("资料不完整，已生成失败批次")).toBeVisible()
  expect(screen.getByLabelText("导出用途")).toHaveValue("派出所人工报送")
})
```

- [ ] **Step 2: Run component tests and confirm red**

Run:

```bash
npm --prefix apps/web test -- \
  src/housing/HousingReportsPage.test.tsx \
  src/housing/HousingReportDialogs.test.tsx
```

Expected: components do not exist.

- [ ] **Step 3: Implement the page query and responsive projections**

The page owns filters for Shanghai range, scope, cohorts, person types, status and creator. It renders four summary cards, a desktop table at `min-width: 720px`, and one-column accessible report cards below that breakpoint. Both projections expose batch number, created time, explicit scope summary, purpose, resident/stay counts, status and allowed actions.

Action rules are exact: generated offers XLSX, CSV, submit and void; marked-submitted offers XLSX, CSV and void; voided/failed offer detail only; voided detail offers “创建更正批次” which opens generation with `corrects_batch_id` and copied filters but requires current purpose confirmation.

- [ ] **Step 4: Implement four accessible dialogs on the shared shell pattern**

`HousingReportDialogs.tsx` exports generation, submitted, void and detail dialogs. Generation requires range, explicit scope mode/selections and purpose; submitted requires an exact local datetime and optional note; void requires reason and displays the permanent download warning. Issue rows show only name, masked phone, stay ID and missing-field labels with a personnel-center repair link.

Use `HousingDialogShell` focus capture/restore. On success/cancel/Escape, focus returns to the trigger; if the row action vanished after a status change, focus falls back to the page heading (`tabIndex={-1}`), then the primary generation button.

- [ ] **Step 5: Add bounded responsive CSS**

Add `.housingReports*` rules with `min-width: 0`, `overflow-wrap: anywhere`, desktop table wrapper only at desktop, no horizontal scrolling on mobile, 44×44 controls, a collapsible mobile filter sheet, and bottom padding that clears mobile navigation. Do not add global selectors outside the housing namespace.

- [ ] **Step 6: Cover loading/error/empty and interaction paths**

Tests cover filters retained across errors, pagination, both download calls, object URL cleanup, submit future-time validation, void reason validation, correction prefill, details without PII, Escape, successful status action focus fallback, mobile touch target class contract, and hidden actions for unsupported statuses.

Run:

```bash
npm --prefix apps/web test -- \
  src/housing/HousingReportsPage.test.tsx \
  src/housing/HousingReportDialogs.test.tsx \
  src/housing/HousingDialogShell.test.tsx
npm --prefix apps/web run lint -- \
  src/housing/HousingReportsPage.tsx src/housing/HousingReportDialogs.tsx
npm --prefix apps/web run build
```

Expected: tests, lint and build pass.

- [ ] **Step 7: 主任务审查集成点**

Render at 360, 768, 1024 and 1440 widths in component/E2E preview, inspect focus and overflow, then commit: `feat: add responsive housing reports page`.

### Task 8: 范围权限路由、E2E 夹具、四视口旅程和状态文档

**Files:**
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Create: `apps/api/app/cli/seed_housing_reporting_e2e.py`
- Create: `apps/api/tests/cli/test_seed_housing_reporting_e2e.py`
- Create: `apps/web/tests/support/inspect-housing-report.py`
- Create: `apps/web/tests/housing-reporting-lifecycle.spec.ts`
- Create: `apps/web/tests/housing-reporting-authorization.spec.ts`
- Create: `scripts/run-housing-reporting-e2e.sh`
- Modify: `scripts/db-schema-manifest.sh`
- Modify: `apps/api/tests/cli/test_e2e_launchers.py`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Modify: `README.md`

- [ ] **Step 1: Write route and seeder failures first**

```tsx
it("allows a scoped sensitive permission to enter housing reports", async () => {
  renderRouter({ permissions: [], scoped_permissions: ["housing.reports.export_sensitive"] })
  await navigateTo("/housing/reports")
  expect(await screen.findByRole("heading", { name: "实名入住报送" })).toBeVisible()
})
```

```python
def test_reporting_seed_is_idempotent_and_isolated(postgres_engine):
    first = seed_housing_reporting_e2e(postgres_engine)
    second = seed_housing_reporting_e2e(postgres_engine)
    assert first == second
    assert count_reporting_fixture_stays(postgres_engine) == EXPECTED_STAY_COUNT
```

- [ ] **Step 2: Run focused route/seeder/launcher tests and confirm red**

Run:

```bash
npm --prefix apps/web test -- src/app/router.test.tsx
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/cli/test_seed_housing_reporting_e2e.py \
  tests/cli/test_e2e_launchers.py tests/cli/test_operations_docs.py -q -p no:cacheprovider
```

Expected: scoped route and reporting seeder/launcher are absent.

- [ ] **Step 3: Make PermissionRoute understand effective scoped capability**

```tsx
function PermissionRoute({ permission, children }: PermissionRouteProps) {
  const { user } = useAuth()
  const allowed = user?.permissions.includes(permission)
    || user?.scoped_permissions?.includes(permission)
  return allowed ? children : <Navigate to="/" replace />
}
```

Add `/housing/reports` and a navigation item only when global or scoped `housing.reports.export_sensitive` is present. Keep server authorization authoritative and clear cached report queries during auth permission refresh.

- [ ] **Step 4: Create deterministic reporting E2E fixtures**

The opt-in reporting seeder first asserts the database name is `erp_e2e` or ends `_test`, then creates: verified rooms/floors; checked-in adult, checked-in minor with two adult guardians, completed transferred stays, checkout-pending stay whose requested departure is earlier than report range, temporary visitor; admin; building-scoped staff; floor-scoped staff with one explicit deny; and no-permission staff. It creates the reporting-only accounts, roles and overrides itself, and creates no report batch so each browser journey owns its data. The existing base `seed_e2e` remains unchanged and continues to create no active reporting stays.

- [ ] **Step 5: Implement the lifecycle E2E journey for four viewports**

`housing-reporting-lifecycle.spec.ts` logs in as admin, opens the navigation entry, checks no overflow/touch/focus, generates a range, captures and parses both XLSX and CSV ZIP downloads, compares their two logical tables, changes live person data through a test API fixture, re-downloads unchanged snapshot, marks submitted, voids with reason, creates correction, and verifies the displayed chain plus allowed audit events.

`inspect-housing-report.py` accepts `--format xlsx|csv` and one downloaded path, parses the two sheets or ZIP members with openpyxl/standard-library CSV, and prints one canonical JSON object. The Playwright test invokes it with `execFile` and `E2E_PYTHON_BIN`; no shell interpolation is used. This keeps E2E parsing on the same pinned XLSX implementation as the API file tests without adding a second JavaScript spreadsheet dependency.

Use response/download/visible-state waits only; no fixed sleep and no `networkidle`.

- [ ] **Step 6: Implement authorization/revocation E2E for four viewports**

`housing-reporting-authorization.spec.ts` proves building/floor users see only fully authorized batches, mixed allowed/denied generation fails atomically, missing and private-denied details are identical, explicit floor deny wins, permission removal hides navigation after auth refresh, and an already-open page cannot list/download/action after revoke. Validate no PII appears in page text or audit metadata.

- [ ] **Step 7: Add the local non-Docker launcher and schema manifest**

Clone the safety structure of `scripts/run-housing-occupancy-e2e.sh`: verify Python/Vite binaries and database safety, reserve unique default ports, create a temp directory with cleanup trap, migrate to `0011_housing_real_name_reporting`, run base plus reporting seeders, launch API/Web locally, then run each spec once per `desktop-1440`, `desktop-1024`, `tablet-768`, and `mobile-360` project.

Update `db-schema-manifest.sh` head to `0011_housing_real_name_reporting` and add `housing_report_batches` plus `housing_report_batch_scopes` to required and row-count tables.

- [ ] **Step 8: Update README with verified status and explicit deferrals**

Only after the focused and four-viewport gates pass, replace “实名导出和人工报送仍未完成” with the exact local capabilities and command. Retain clear statements that Docker/NAS,公安在线 API, production key rotation/backup and GPU/video remain unverified or deferred.

- [ ] **Step 9: Run Task 8 focused verification**

Run:

```bash
npm --prefix apps/web test -- src/app/router.test.tsx
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest \
  tests/cli/test_seed_housing_reporting_e2e.py \
  tests/cli/test_e2e_launchers.py tests/cli/test_operations_docs.py -q -p no:cacheprovider
cd ../..
bash -n scripts/run-housing-reporting-e2e.sh scripts/db-schema-manifest.sh
npm --prefix apps/web run test:e2e -- --list \
  housing-reporting-lifecycle.spec.ts housing-reporting-authorization.spec.ts
```

Expected: all unit/CLI checks pass; Playwright lists 8 tests total (2 specs × 4 viewports, one journey in each spec).

- [ ] **Step 10: Run the real four-viewport reporting gate**

Run:

```bash
E2E_PYTHON_BIN="/Users/skymoonzyj/Documents/new project3/.venv/bin/python" \
  scripts/run-housing-reporting-e2e.sh
```

Expected: both reporting specs pass at 1440, 1024, 768 and 360 widths against isolated PostgreSQL; downloads are actually parsed and no database-safety guard is bypassed.

- [ ] **Step 11: 主任务审查集成点**

Review seed isolation, launcher cleanup, route permission semantics, README claims and E2E absence of fixed waits. Main task commit: `test: verify housing real-name reporting journeys`.

## 集成验证与完成门禁

- [ ] **Step 1: Run backend unit/service/API checks from the inspected worktree**

```bash
cd apps/api
PYTHONPATH=$PWD TEST_DATABASE_URL="$TEST_DATABASE_URL" pytest -q -p no:cacheprovider
ruff check .
mypy app
```

Expected: the full backend suite passes with no reporting/PostgreSQL nodes skipped; Ruff and mypy pass.

- [ ] **Step 2: Run Web tests, lint, type/build**

```bash
npm --prefix apps/web test
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all commands exit 0.

- [ ] **Step 3: Re-run all four local housing browser gates**

```bash
scripts/run-housing-room-status-e2e.sh
scripts/run-housing-personal-portal-e2e.sh
scripts/run-housing-occupancy-e2e.sh
scripts/run-housing-reporting-e2e.sh
```

Expected: catalog, personal portal, occupancy lifecycle/revocation and reporting lifecycle/authorization all pass at their configured four viewports without Docker.

- [ ] **Step 4: Run privacy and repository hygiene checks**

```bash
rg -n "snapshot_ciphertext|identity_number|phone" apps/api/app/housing/reporting_router.py apps/web/src/housing/HousingReportsPage.tsx
git diff --check
git status --short
```

Expected: no snapshot/PII response rendering, no whitespace errors, and only intended source/test/doc changes remain.

- [ ] **Step 5: Request two-stage review before final integration**

Use `superpowers:requesting-code-review` for specification compliance first and code quality/privacy second. Resolve every confirmed issue with TDD and rerun the affected focused gate plus the full non-Docker gate.

- [ ] **Step 6: Final integration commit**

After verification evidence is current, the main task creates `feat: complete housing real-name reporting` and reports exact tests, skipped checks, and the continuing Docker/NAS/公安 API deferrals.
