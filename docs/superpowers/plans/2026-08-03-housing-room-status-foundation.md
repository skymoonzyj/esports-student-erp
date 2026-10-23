# Housing Room Status Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first housing unit: a canonical four-building room catalog, pending-verification workflow, building/floor-scoped RBAC, server-derived empty-room status, responsive room cards, filters, and catalog administration.

**Architecture:** Add a focused `app.housing` domain. PostgreSQL stores stable building, floor, room, and import-batch records; the API computes assignability from verification, service status, room type, and capacity. This unit deliberately returns zero current occupants because stay records belong to the next unit, while freezing the response fields that the next unit will populate.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, PostgreSQL, Alembic, pytest, React 19, TypeScript 6, TanStack Query, Vitest, Testing Library, Playwright, CSS.

---

## Scope and execution boundary

Implement only design unit 1 from
`docs/superpowers/specs/2026-08-03-housing-room-status-design.md`:

- canonical 4/A/9/5 building inventory and source fingerprint;
- `housing_buildings`, `housing_floors`, `housing_rooms`, and `housing_import_batches`;
- `housing.read` and `housing.rooms.manage`;
- new `building` and `floor` RBAC scope kinds and permission pickers;
- room CRUD, single/batch verification, static availability projection, summary, and filters;
- responsive internal room-status page and catalog-management dialogs;
- local two-service E2E without Docker.

Do not add people selection, stays, reservations, current occupants, gender assignment, issues, transfers, checkout,
family housing pages, sensitive export, police integration, Docker, or NAS work. The room-card contract includes
`current_occupants`, `current_gender`, and future-reservation fields with truthful empty defaults so the next unit can
populate them without changing the client shape. Do not claim the housing module is operational after this unit.

## File map

| Area | Files | Responsibility |
|---|---|---|
| Canonical source | `apps/api/app/housing/{seed_inventory,seed}.py` | Immutable normalized 188-row inventory, deterministic IDs, and non-overwriting runtime reseed |
| Persistence | `apps/api/app/housing/models.py`, `apps/api/migrations/versions/0009_housing_room_status_foundation.py`, `apps/api/migrations/env.py` | Tables, constraints, permissions, scope kinds, seed migration |
| RBAC | `apps/api/app/rbac/{models,policy,schemas,service,router,seed}.py`, `apps/api/app/evaluations/access.py`, `apps/api/app/audit/service.py` | Building/floor scopes, permission directories, audit allowlist |
| Housing backend | `apps/api/app/housing/{errors,schemas,access,inventory_service,router}.py`, `apps/api/app/api/router.py` | Catalog mutations, verification, overview, privacy, audit |
| Housing web | `apps/web/src/housing/{api,HousingOverviewPage,HousingFilters,RoomCard,HousingCatalogPanel,HousingCatalogDialogs,housing}.ts*` | Typed API, filters, cards, management dialogs, responsive styles |
| App integration | `apps/web/src/app/router.tsx`, `apps/web/src/layout/{AppShell.tsx,AppShell.test.tsx}` | Permission-gated route and navigation |
| Permission UI | `apps/web/src/settings/{api.ts,PermissionsPage.tsx,PermissionsPage.test.tsx}` | Building/floor scope selection |
| Tests and E2E | backend test files listed by task, `apps/web/tests/housing-room-status-foundation.spec.ts`, `scripts/run-housing-room-status-e2e.sh` | TDD, four viewports, local non-Docker gate |
| Status docs | `README.md`, `apps/api/tests/cli/test_operations_docs.py` | Accurate local-only status and launcher contract |

Use the existing worktree and branch:

```bash
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/evaluation-data-media-foundation'
git branch --show-current
```

Expected: `codex/housing-room-status-design`. Create the implementation branch only when execution begins, using the
`superpowers:using-git-worktrees` workflow required by the execution skill.

## Task 1: Freeze the normalized room inventory

**Files:**
- Create: `apps/api/app/housing/__init__.py`
- Create: `apps/api/app/housing/seed_inventory.py`
- Create: `apps/api/tests/unit/housing/test_seed_inventory.py`

- [ ] **Step 1: Write the failing canonical-inventory tests**

Create `apps/api/tests/unit/housing/test_seed_inventory.py`:

```python
from collections import Counter

from app.housing.seed_inventory import (
    FLOOR_SEEDS,
    INITIAL_BUILDINGS,
    INITIAL_ROOMS,
    SOURCE_SHA256,
)


def test_initial_inventory_matches_confirmed_workbook_totals() -> None:
    assert SOURCE_SHA256 == "22afc9b882a52c5e286e64c13148c0198c4568eabcb33fc482929fac9576f88c"
    assert len(INITIAL_BUILDINGS) == 4
    assert len(FLOOR_SEEDS) == 11
    assert len(INITIAL_ROOMS) == 188

    by_type = Counter(room.room_type for room in INITIAL_ROOMS)
    assert by_type == {"single": 119, "double": 52, "functional": 17}
    assert sum(room.capacity for room in INITIAL_ROOMS) == 223
    assert sum(room.room_type != "functional" for room in INITIAL_ROOMS) == 171


def test_initial_inventory_is_normalized_and_unique() -> None:
    room_numbers = [room.room_number for room in INITIAL_ROOMS]
    assert len(room_numbers) == len(set(room_numbers))
    assert {room.room_number for room in INITIAL_ROOMS} >= {
        "4-409",
        "9-305",
        "5-311",
        "A-205",
        "A-209",
        "A-323",
        "A-409",
    }
    assert {"4-4094", "9-3059", "5-31", "A- 205"}.isdisjoint(room_numbers)
    assert all(" " not in room.room_number for room in INITIAL_ROOMS)
    assert all(room.area_square_meters > 0 for room in INITIAL_ROOMS)
    assert all(
        room.capacity
        == {"single": 1, "double": 2, "functional": 0}[room.room_type]
        for room in INITIAL_ROOMS
    )


def test_initial_inventory_uses_stable_ids() -> None:
    building_ids = {item.id for item in INITIAL_BUILDINGS}
    floor_ids = {item.id for item in FLOOR_SEEDS}
    room_ids = {item.id for item in INITIAL_ROOMS}
    assert len(building_ids) == 4
    assert len(floor_ids) == 11
    assert len(room_ids) == 188
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/unit/housing/test_seed_inventory.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.housing'`.

- [ ] **Step 3: Add the immutable normalized inventory**

Create an empty `apps/api/app/housing/__init__.py`, then create
`apps/api/app/housing/seed_inventory.py` with this complete data model and floor source:

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5

RoomType = Literal["single", "double", "triple", "custom", "functional"]

SOURCE_NAME = "新建 Microsoft Excel 工作表(2).xls"
SOURCE_SHA256 = "22afc9b882a52c5e286e64c13148c0198c4568eabcb33fc482929fac9576f88c"
HOUSING_SEED_NAMESPACE = UUID("52000000-0000-4000-8000-000000000000")
IMPORT_BATCH_ID = uuid5(HOUSING_SEED_NAMESPACE, f"import:{SOURCE_SHA256}")


def seed_uuid(kind: str, key: str) -> UUID:
    return uuid5(HOUSING_SEED_NAMESPACE, f"{kind}:{key}")


@dataclass(frozen=True)
class BuildingSeed:
    id: UUID
    code: str
    name: str
    display_order: int


@dataclass(frozen=True)
class FloorSeed:
    id: UUID
    building_id: UUID
    building_code: str
    number: int
    name: str
    display_order: int
    room_numbers: tuple[str, ...]
    type_codes: tuple[str, ...]
    areas: tuple[int, ...]


@dataclass(frozen=True)
class RoomSeed:
    id: UUID
    building_id: UUID
    floor_id: UUID
    building_code: str
    floor_number: int
    room_number: str
    normalized_key: str
    room_type: RoomType
    capacity: int
    area_square_meters: Decimal


INITIAL_BUILDINGS = (
    BuildingSeed(seed_uuid("building", "4"), "4", "4幢", 10),
    BuildingSeed(seed_uuid("building", "A"), "A", "A幢", 20),
    BuildingSeed(seed_uuid("building", "9"), "9", "9幢", 30),
    BuildingSeed(seed_uuid("building", "5"), "5", "5幢", 40),
)
BUILDING_BY_CODE = {item.code: item for item in INITIAL_BUILDINGS}


def _rooms(prefix: str, first: int, last: int) -> tuple[str, ...]:
    return tuple(f"{prefix}-{number}" for number in range(first, last + 1))


def _floor(
    building_code: str,
    number: int,
    first_room: int,
    last_room: int,
    type_codes: str,
    areas: tuple[int, ...],
) -> FloorSeed:
    room_numbers = _rooms(building_code, first_room, last_room)
    codes = tuple(type_codes.split())
    if not (len(room_numbers) == len(codes) == len(areas)):
        raise ValueError(f"invalid floor inventory: {building_code}-{number}")
    building = BUILDING_BY_CODE[building_code]
    return FloorSeed(
        id=seed_uuid("floor", f"{building_code}:{number}"),
        building_id=building.id,
        building_code=building_code,
        number=number,
        name={2: "二楼", 3: "三楼", 4: "四楼"}[number],
        display_order=number * 10,
        room_numbers=room_numbers,
        type_codes=codes,
        areas=areas,
    )


FLOOR_SEEDS = (
    _floor(
        "4", 2, 201, 220,
        "D D D F D D D D D S D D D F S S F F S S",
        (35, 35, 36, 14, 33, 36, 36, 36, 37, 30, 34, 43, 28, 18, 28, 27, 16, 21, 28, 29),
    ),
    _floor(
        "4", 3, 301, 320,
        "D D D F D D D D D S D D D F S S S F S S",
        (35, 35, 36, 14, 33, 36, 36, 36, 37, 30, 34, 43, 28, 18, 28, 27, 40, 21, 28, 29),
    ),
    _floor(
        "4", 4, 401, 412,
        "S S S S S S S S S D D F",
        (24, 24, 24, 24, 24, 24, 31, 33, 30, 24, 24, 22),
    ),
    _floor(
        "A", 2, 201, 223,
        "S S S S D S S S S S S S S S S S S S S S S S F",
        (22, 27, 26, 33, 47, 32, 33, 29, 33, 34, 30, 30, 29, 26, 26, 26, 33, 26, 26, 34, 32, 27, 10),
    ),
    _floor(
        "A", 3, 301, 323,
        "S S S S D S S S S S S S S S S S S S S S S S F",
        (22, 27, 26, 33, 47, 32, 33, 29, 33, 34, 30, 30, 29, 26, 26, 26, 33, 26, 26, 34, 32, 27, 10),
    ),
    _floor(
        "A", 4, 401, 410,
        "S S S S S S S S S S",
        (33, 33, 33, 36, 35, 24, 25, 21, 33, 33),
    ),
    _floor(
        "9", 2, 201, 220,
        "S D D D D D S S S D D S F F S S S S S S",
        (25, 34, 34, 30, 31, 26, 24, 24, 22, 37, 45, 43, 4, 9, 20, 21, 23, 23, 25, 27),
    ),
    _floor(
        "9", 3, 301, 320,
        "S D D D D D S S S D D S F F S S S S S S",
        (26, 33, 34, 30, 30, 27, 24, 24, 22, 39, 45, 43, 4, 8, 20, 22, 23, 22, 25, 28),
    ),
    _floor(
        "9", 4, 401, 408,
        "S S S S S S F S",
        (31, 25, 23, 23, 20, 20, 13, 44),
    ),
    _floor(
        "5", 2, 201, 216,
        "D S S D S D D D D F S S S S S S",
        (35, 21, 26, 31, 31, 32, 30, 37, 39, 12, 31, 30, 29, 25, 24, 22),
    ),
    _floor(
        "5", 3, 301, 316,
        "D S S D S D D D D F S S S S S S",
        (35, 21, 26, 31, 31, 32, 30, 37, 39, 12, 31, 30, 29, 25, 24, 22),
    ),
)

_TYPE_BY_CODE: dict[str, RoomType] = {
    "S": "single",
    "D": "double",
    "F": "functional",
}
_CAPACITY: dict[RoomType, int] = {
    "single": 1,
    "double": 2,
    "triple": 3,
    "custom": 1,
    "functional": 0,
}


def _flatten_rooms() -> tuple[RoomSeed, ...]:
    rows: list[RoomSeed] = []
    for floor in FLOOR_SEEDS:
        for room_number, type_code, area in zip(
            floor.room_numbers,
            floor.type_codes,
            floor.areas,
            strict=True,
        ):
            room_type = _TYPE_BY_CODE[type_code]
            rows.append(
                RoomSeed(
                    id=seed_uuid("room", room_number),
                    building_id=floor.building_id,
                    floor_id=floor.id,
                    building_code=floor.building_code,
                    floor_number=floor.number,
                    room_number=room_number,
                    normalized_key=room_number.upper(),
                    room_type=room_type,
                    capacity=_CAPACITY[room_type],
                    area_square_meters=Decimal(area),
                )
            )
    return tuple(rows)


INITIAL_ROOMS = _flatten_rooms()
```

- [ ] **Step 4: Run the canonical-inventory tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/unit/housing/test_seed_inventory.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit the immutable source**

```bash
git add apps/api/app/housing/__init__.py \
  apps/api/app/housing/seed_inventory.py \
  apps/api/tests/unit/housing/test_seed_inventory.py
git commit -m "feat: freeze housing room inventory"
```

## Task 2: Add the housing schema, migration seed, and model parity

**Files:**
- Create: `apps/api/app/housing/models.py`
- Create: `apps/api/migrations/versions/0009_housing_room_status_foundation.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/db-schema-manifest.sh`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write failing migration expectations**

In `apps/api/tests/db/test_migrations.py`, add these constants near the existing table and permission constants:

```python
HOUSING_FOUNDATION_TABLES = {
    "housing_buildings",
    "housing_floors",
    "housing_rooms",
    "housing_import_batches",
}
EXPECTED_TABLES |= HOUSING_FOUNDATION_TABLES

HOUSING_PERMISSION_IDS = {
    "housing.read": UUID("20000000-0000-4000-8000-000000000037"),
    "housing.rooms.manage": UUID("20000000-0000-4000-8000-000000000038"),
}
```

Add a migration test:

```python
def test_housing_foundation_migration_seeds_catalog_and_permissions(
    postgres_engine: Engine,
) -> None:
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0009_housing_room_status_foundation"
        )
        assert connection.scalar(text("SELECT count(*) FROM housing_buildings")) == 4
        assert connection.scalar(text("SELECT count(*) FROM housing_floors")) == 11
        assert connection.scalar(text("SELECT count(*) FROM housing_rooms")) == 188
        assert connection.scalar(
            text("SELECT count(*) FROM housing_rooms WHERE room_type = 'functional'")
        ) == 17
        assert connection.scalar(
            text(
                "SELECT count(*) FROM housing_rooms "
                "WHERE room_type <> 'functional' "
                "AND verification_status = 'pending_verification'"
            )
        ) == 171
        assert connection.scalar(
            text("SELECT COALESCE(sum(capacity), 0) FROM housing_rooms")
        ) == 223
        assert set(
            connection.execute(
                text("SELECT key, id FROM permissions WHERE key LIKE 'housing.%'")
            ).tuples()
        ) == {(key, permission_id) for key, permission_id in HOUSING_PERMISSION_IDS.items()}
        assert {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT rooms.room_number FROM housing_rooms rooms "
                    "WHERE rooms.room_number IN "
                    "('4-409', '9-305', '5-311', 'A-205')"
                )
            ).tuples()
        } == {"4-409", "9-305", "5-311", "A-205"}
```

Extend `test_migration_seed_matches_code_constants` in `apps/api/tests/db/test_rbac_seed.py` so it expects the two
housing permission keys and the admin global grants.

Change only current-head expectations in `test_migrations.py` from `0008_evaluation_data_media_foundation` to
`0009_housing_room_status_foundation`, including `_assert_portal_ddl_is_intact`'s default. Keep explicit 0008
assertions that intentionally upgrade to or test the evaluation migration. Add a clean
upgrade/downgrade/upgrade test and a refusal test for each of these mutations: edited seeded room, added room, edited
housing permission, removed admin grant, building/floor override, and override using a housing permission. Every
refusal case must assert the revision and all mutated rows remain unchanged after the exception.

Update `scripts/db-schema-manifest.sh` to expected head `0009_housing_room_status_foundation`; add all four housing
tables to both `REQUIRED_TABLES` and `ROW_COUNT_TABLES`. Update the manifest test's required table count from 43 to
47 and assert the four exact names are present.

- [ ] **Step 2: Run the migration tests to verify failure**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py::test_housing_foundation_migration_seeds_catalog_and_permissions \
  apps/api/tests/db/test_rbac_seed.py::test_migration_seed_matches_code_constants -q
```

Expected: migration revision/table assertions fail because revision `0009_housing_room_status_foundation` does not exist.

- [ ] **Step 3: Add SQLAlchemy housing models**

Create `apps/api/app/housing/models.py`. Use `TimestampMixin` and the following exact model surface:

```python
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class HousingBuilding(TimestampMixin, Base):
    __tablename__ = "housing_buildings"
    __table_args__ = (
        CheckConstraint("btrim(code) <> ''", name="ck_housing_buildings_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_housing_buildings_name"),
        CheckConstraint("display_order >= 0", name="ck_housing_buildings_display_order"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )


class HousingFloor(TimestampMixin, Base):
    __tablename__ = "housing_floors"
    __table_args__ = (
        UniqueConstraint("id", "building_id", name="uq_housing_floors_id_building"),
        UniqueConstraint("building_id", "number", name="uq_housing_floors_building_number"),
        UniqueConstraint("building_id", "name", name="uq_housing_floors_building_name"),
        CheckConstraint("number >= -20 AND number <= 200", name="ck_housing_floors_number"),
        CheckConstraint("btrim(name) <> ''", name="ck_housing_floors_name"),
        CheckConstraint("display_order >= 0", name="ck_housing_floors_display_order"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    building_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_buildings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )


class HousingRoom(TimestampMixin, Base):
    __tablename__ = "housing_rooms"
    __table_args__ = (
        ForeignKeyConstraint(
            ["floor_id", "building_id"],
            ["housing_floors.id", "housing_floors.building_id"],
            name="fk_housing_rooms_floor_building",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "building_id", "normalized_key", name="uq_housing_rooms_building_key"
        ),
        CheckConstraint("btrim(room_number) <> ''", name="ck_housing_rooms_number"),
        CheckConstraint("btrim(normalized_key) <> ''", name="ck_housing_rooms_key"),
        CheckConstraint(
            "room_type IN ('single', 'double', 'triple', 'custom', 'functional')",
            name="ck_housing_rooms_type",
        ),
        CheckConstraint(
            "(room_type = 'single' AND capacity = 1) OR "
            "(room_type = 'double' AND capacity = 2) OR "
            "(room_type = 'triple' AND capacity = 3) OR "
            "(room_type = 'custom' AND capacity BETWEEN 1 AND 100) OR "
            "(room_type = 'functional' AND capacity = 0)",
            name="ck_housing_rooms_type_capacity",
        ),
        CheckConstraint(
            "area_square_meters IS NULL OR area_square_meters > 0",
            name="ck_housing_rooms_area",
        ),
        CheckConstraint(
            "verification_status IN ('pending_verification', 'verified')",
            name="ck_housing_rooms_verification",
        ),
        CheckConstraint(
            "service_status IN ('available', 'problem', 'maintenance', 'disabled')",
            name="ck_housing_rooms_service_status",
        ),
        CheckConstraint("version >= 1", name="ck_housing_rooms_version"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    building_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    floor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    room_number: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(32), nullable=False)
    room_type: Mapped[str] = mapped_column(String(24), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    area_square_meters: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_verification"
    )
    service_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="available"
    )
    notes: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )


class HousingImportBatch(TimestampMixin, Base):
    __tablename__ = "housing_import_batches"
    __table_args__ = (
        CheckConstraint("btrim(source_name) <> ''", name="ck_housing_import_source_name"),
        CheckConstraint("source_sha256 ~ '^[0-9a-f]{64}$'", name="ck_housing_import_sha"),
        CheckConstraint("row_count >= 0", name="ck_housing_import_row_count"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    imported_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
```

Import `app.housing.models` in `apps/api/migrations/env.py` next to the other domain model imports.

Add the same IDs and labels to `PERMISSIONS` in `apps/api/app/rbac/seed.py` so the migration parity test can pass:

```python
"housing.read": PermissionSeed(
    UUID("20000000-0000-4000-8000-000000000037"),
    "查看房态",
),
"housing.rooms.manage": PermissionSeed(
    UUID("20000000-0000-4000-8000-000000000038"),
    "管理房间目录",
    True,
),
```

The existing admin comprehension grants both globally. Do not add either permission to any other default role.

- [ ] **Step 4: Create migration `0009_housing_room_status_foundation`**

Create `apps/api/migrations/versions/0009_housing_room_status_foundation.py` with:

```python
"""Add housing room-status foundation.

Revision ID: 0009_housing_room_status_foundation
Revises: 0008_evaluation_data_media_foundation
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.housing.seed_inventory import (
    FLOOR_SEEDS,
    IMPORT_BATCH_ID,
    INITIAL_BUILDINGS,
    INITIAL_ROOMS,
    SOURCE_NAME,
    SOURCE_SHA256,
)

revision: str = "0009_housing_room_status_foundation"
down_revision: str | Sequence[str] | None = "0008_evaluation_data_media_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUSING_PERMISSIONS = {
    "housing.read": (
        UUID("20000000-0000-4000-8000-000000000037"),
        "查看房态",
        False,
    ),
    "housing.rooms.manage": (
        UUID("20000000-0000-4000-8000-000000000038"),
        "管理房间目录",
        True,
    ),
}

CORRECTION_SUMMARY = {
    "room_number_corrections": {
        "4-4094": "4-409",
        "9-3059": "9-305",
        "spreadsheet_date_value": "5-311",
    },
    "removed_a_building_spaces": 10,
    "normalized_floor_labels": True,
}
```

Implement `upgrade()` in this exact order:

1. Drop and recreate `ck_role_permissions_scope_kind`, `ck_permission_overrides_scope_kind`, and
   `ck_permission_overrides_scope_id` with `building` and `floor` added to the object scopes.
2. Create the four tables with DDL matching `models.py`, including indexes on building display order, floor display
   order, `(floor_id, room_number)`, verification status, service status, and room type.
3. Insert the two permissions after checking both IDs and keys for collisions.
4. Grant both permissions to `admin` with `global` scope.
5. Insert `INITIAL_BUILDINGS`, `FLOOR_SEEDS`, `INITIAL_ROOMS`, and the import batch. Use
   `pending_verification` for non-functional rooms, `verified` for functional rooms, `available` service status,
   and `None` actor IDs.

Use these value builders so the migration and canonical constants cannot disagree:

```python
def _building_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "display_order": item.display_order,
            "is_active": True,
            "notes": None,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in INITIAL_BUILDINGS
    ]


def _floor_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "building_id": item.building_id,
            "number": item.number,
            "name": item.name,
            "display_order": item.display_order,
            "is_active": True,
            "notes": None,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in FLOOR_SEEDS
    ]


def _room_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "building_id": item.building_id,
            "floor_id": item.floor_id,
            "room_number": item.room_number,
            "normalized_key": item.normalized_key,
            "room_type": item.room_type,
            "capacity": item.capacity,
            "area_square_meters": item.area_square_meters,
            "verification_status": (
                "verified" if item.room_type == "functional" else "pending_verification"
            ),
            "service_status": "available",
            "notes": None,
            "version": 1,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in INITIAL_ROOMS
    ]
```

Implement `downgrade()` as a refusal-first operation. Before changing DDL, compare every seeded building, floor,
room, import batch, permission, and admin grant with the canonical values. Also refuse if any non-seed housing row,
permission override using `building`/`floor`, or override referencing either housing permission exists. Raise
`RuntimeError("cannot downgrade housing foundation with customized housing data")` on any difference. When safe,
drop housing tables, delete the admin grants and permissions, then restore the 0008 RBAC scope constraints.

- [ ] **Step 5: Run migration, model, and downgrade tests**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/cli/test_operations_docs.py::test_schema_manifest_tracks_current_head_and_all_required_tables -q
```

Expected: all migration and seed-parity tests pass at revision `0009_housing_room_status_foundation`.

- [ ] **Step 6: Commit schema and migration**

```bash
git add apps/api/app/housing/models.py apps/api/migrations/env.py apps/api/app/rbac/seed.py \
  apps/api/migrations/versions/0009_housing_room_status_foundation.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  scripts/db-schema-manifest.sh apps/api/tests/cli/test_operations_docs.py
git commit -m "feat: add housing room catalog schema"
```

## Task 3: Extend RBAC and audit vocabulary for building and floor scopes

**Files:**
- Modify: `apps/api/app/rbac/models.py`
- Modify: `apps/api/app/rbac/policy.py`
- Modify: `apps/api/app/rbac/schemas.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/evaluations/access.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/unit/rbac/test_policy.py`
- Modify: `apps/api/tests/api/test_permissions.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing hierarchy, schema, delegation, and audit tests**

Add this policy case to `apps/api/tests/unit/rbac/test_policy.py`:

```python
def test_building_allow_and_floor_deny_follow_deny_first_hierarchy() -> None:
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    floor_id = UUID("52000000-0000-4000-8000-000000000020")
    tokens = {"global", f"building:{building_id}", f"floor:{floor_id}"}
    policy = RbacPolicy()

    allowed = policy.decide(
        "housing.read",
        tokens,
        [],
        [allow("housing.read", f"building:{building_id}")],
    )
    denied = policy.decide(
        "housing.read",
        tokens,
        [],
        [
            allow("housing.read", f"building:{building_id}"),
            deny("housing.read", f"floor:{floor_id}"),
        ],
    )

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "explicit_deny"
```

In `apps/api/tests/api/test_permissions.py`, add a parametrized test that posts and previews a real seeded building
and floor override for `housing.read`, asserts the returned `scope_kind`, `scope_id`, and label, then asserts a random
UUID receives `404 rbac_resource_not_found`. Add a second case in which an actor holding only a building-scoped
`housing.read` allow may delegate a floor in that building but receives `403 permission_delegation_denied` for a
floor in another building. Use `INITIAL_BUILDINGS[0]`, `FLOOR_SEEDS[0]`, and a floor from a different building; do not
hard-code row order from a database query.

In `apps/api/tests/api/test_audit.py`, pass this metadata through both `validate_metadata` and `public_metadata`:

```python
metadata = {
    "error_code": "housing_stale_version",
    "scope_kind": "floor",
    "building_id": "52000000-0000-4000-8000-000000000010",
    "floor_id": "52000000-0000-4000-8000-000000000020",
    "room_id": "52000000-0000-4000-8000-000000000030",
}
assert AuditService.public_metadata(metadata) == metadata
AuditService.validate_metadata(metadata)
```

- [ ] **Step 2: Run the new tests and confirm the contract is missing**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/rbac/test_policy.py \
  apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_audit.py -q
```

Expected: the new Pydantic/API cases reject `building` and `floor`, and the audit allowlist rejects the housing IDs.

- [ ] **Step 3: Add the two scope kinds everywhere they are typed**

Task 2 already added the two permission constants and the administrator's global grants. Do not add housing grants
for coach, customer service, life teacher, staff, guardian, or student in this unit. Add `building` and `floor` to
`ScopeKind`, the ORM scope checks, `PermissionOverrideCreate.scope_kind`, its object-scope validator, and
`_PUBLIC_SCOPE_KINDS`.

In `apps/api/app/audit/service.py`, add `building_id`, `floor_id`, and `room_id` to `_PUBLIC_METADATA_KEYS` and to the
UUID-only extraction loop. Add `permission_denied`, `housing_resource_not_found`,
`housing_duplicate_catalog_entry`, `housing_parent_inactive`, `housing_catalog_has_children`,
`housing_invalid_room_configuration`, `housing_issue_workflow_required`, and `housing_stale_version` to
`_PUBLIC_ERROR_CODES`. No building name, floor name, room number, notes, or free text is audit metadata.

- [ ] **Step 4: Make RBAC resolve and preview the hierarchy**

Extend `EvaluationResource` in `apps/api/app/evaluations/access.py` with these optional fields:

```python
building_id: UUID | None = None
floor_id: UUID | None = None
```

At the end of `EvaluationAccessService.resource_tokens`, before returning, add:

```python
if resource.building_id is not None:
    tokens.add(f"building:{resource.building_id}")
if resource.floor_id is not None:
    tokens.add(f"floor:{resource.floor_id}")
```

Import `HousingBuilding` and `HousingFloor` in `apps/api/app/rbac/service.py`. In `_scope_resource`, validate the
target and return the hierarchical resource exactly as follows:

```python
if input.scope_kind == "building":
    building = self.db.get(HousingBuilding, input.scope_id)
    if building is None:
        raise RbacResourceNotFound("building scope not found")
    if input.effect == "allow" and not building.is_active and not allow_existing_inactive:
        raise InactivePermissionScope("inactive building cannot receive a new allow")
    return EvaluationResource(building_id=building.id)
if input.scope_kind == "floor":
    floor = self.db.get(HousingFloor, input.scope_id)
    if floor is None:
        raise RbacResourceNotFound("floor scope not found")
    if input.effect == "allow" and not floor.is_active and not allow_existing_inactive:
        raise InactivePermissionScope("inactive floor cannot receive a new allow")
    return EvaluationResource(building_id=floor.building_id, floor_id=floor.id)
```

Add matching branches in `_resource_tokens_for_rule`: a building rule returns one resource-token set when the row
exists; a floor rule returns `global`, its parent building token, and its floor token. Missing rows return `[]` so a
stale override does not appear as an effective scoped permission. This makes login expose `housing.read` through
`scoped_permissions` without granting global access.

- [ ] **Step 5: Run RBAC, audit, seed-parity, lint, and typing checks**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/rbac/test_policy.py \
  apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_audit.py \
  apps/api/tests/db/test_rbac_seed.py -q
.venv/bin/ruff check apps/api/app/rbac apps/api/app/audit \
  apps/api/app/evaluations/access.py apps/api/tests/unit/rbac \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py
.venv/bin/mypy apps/api/app
```

Expected: all selected tests pass, Ruff reports no issues, and mypy exits successfully.

- [ ] **Step 6: Commit the RBAC extension**

```bash
git add apps/api/app/rbac apps/api/app/evaluations/access.py \
  apps/api/app/audit/service.py apps/api/tests/unit/rbac/test_policy.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py
git commit -m "feat: add housing permission scopes"
```

## Task 4: Expose building and floor scope directories to permission managers

**Files:**
- Modify: `apps/api/app/rbac/schemas.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/rbac/router.py`
- Modify: `apps/api/tests/api/test_permissions.py`

- [ ] **Step 1: Write failing permission-directory API tests**

Add tests that authenticate the global administrator and request:

```text
GET /api/v1/permission-building-directory?query=4&page=1&page_size=20
GET /api/v1/permission-floor-directory?query=二楼&page=1&page_size=20
```

Assert both responses set `Cache-Control: no-store, private`, return stable UUIDs, and contain labels `4幢` and
`4幢 · 二楼`. Assert the floor row includes its `building_id`, building name, floor number, and both active flags.
Create an account with only person-scoped `permissions.manage` and assert both organization-directory endpoints
return `403 permission_denied`.

- [ ] **Step 2: Run the directory tests to verify 404 failures**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_permissions.py -k 'building_directory or floor_directory' -q
```

Expected: both routes return `404 route_not_found`.

- [ ] **Step 3: Define the directory response contracts**

Add these models to `apps/api/app/rbac/schemas.py`:

```python
class PermissionBuildingDirectoryItem(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    label: str


class PermissionBuildingDirectoryPage(BaseModel):
    items: list[PermissionBuildingDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionFloorDirectoryItem(BaseModel):
    id: UUID
    building_id: UUID
    building_name: str
    number: int
    name: str
    building_is_active: bool
    is_active: bool
    label: str


class PermissionFloorDirectoryPage(BaseModel):
    items: list[PermissionFloorDirectoryItem]
    page: int
    page_size: int
    total: int
```

- [ ] **Step 4: Implement paginated directory queries and labels**

Add `permission_building_directory(query, page, page_size)` and
`permission_floor_directory(query, page, page_size)` to `RbacService`. Reuse the existing page validation bounds.
Building search matches `code` or `name`; floor search matches parent code/name or floor name. Order buildings by
`display_order, code, id` and floors by parent display order, floor display order, number, and ID. Include inactive
rows because an existing deny or historical allow still needs a readable label.

Extend `_scope_label` in `apps/api/app/rbac/router.py`:

```python
if override.scope_kind == "building":
    return db.scalar(select(HousingBuilding.name).where(HousingBuilding.id == override.scope_id))
if override.scope_kind == "floor":
    row = db.execute(
        select(HousingBuilding.name, HousingFloor.name)
        .join(HousingBuilding, HousingBuilding.id == HousingFloor.building_id)
        .where(HousingFloor.id == override.scope_id)
    ).one_or_none()
    return " · ".join(row) if row is not None else None
```

Add `/permission-building-directory` and `/permission-floor-directory` GET routes using
`require_global_permissions_manager`, query length at most 120, page at least 1, and page size 1–100. Map the service
rows into the schemas above and always set `Cache-Control: no-store, private`.

- [ ] **Step 5: Run backend directory and full permission tests**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_permissions.py -q
.venv/bin/ruff check apps/api/app/rbac apps/api/tests/api/test_permissions.py
.venv/bin/mypy apps/api/app
```

Expected: all permission tests pass with no lint or typing errors.

- [ ] **Step 6: Commit the permission directories**

```bash
git add apps/api/app/rbac apps/api/tests/api/test_permissions.py
git commit -m "feat: add housing permission directories"
```

## Task 5: Add building and floor selectors to the permission page

**Files:**
- Modify: `apps/web/src/settings/api.ts`
- Modify: `apps/web/src/settings/PermissionsPage.tsx`
- Modify: `apps/web/src/settings/PermissionsPage.test.tsx`

- [ ] **Step 1: Write failing selector tests**

Mock `listPermissionBuildings` and `listPermissionFloors`. Add one interaction test for each scope:

1. choose a target account and `housing.read`;
2. choose `指定楼栋` or `指定楼层`;
3. search for `4幢`;
4. select `4幢` or `4幢 · 二楼`;
5. preview and save;
6. assert the payload contains the exact `scope_kind` and UUID;
7. assert the rendered saved rule uses the API-provided label rather than a raw UUID.

Also assert a non-global permission manager does not see either option, matching the existing organization-scope
rule.

- [ ] **Step 2: Run the component test and verify the mocks/types are missing**

```bash
npm --prefix apps/web test -- PermissionsPage.test.tsx
```

Expected: TypeScript/Vitest fails because the two scope kinds and directory functions do not exist.

- [ ] **Step 3: Extend the typed settings API**

Add `building` and `floor` to `ScopeKind`, then add:

```typescript
export type PermissionBuildingDirectoryItem = {
  id: string
  code: string
  name: string
  is_active: boolean
  label: string
}

export type PermissionFloorDirectoryItem = {
  id: string
  building_id: string
  building_name: string
  number: number
  name: string
  building_is_active: boolean
  is_active: boolean
  label: string
}

export function listPermissionBuildings(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionBuildingDirectoryItem>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<PermissionBuildingDirectoryItem>>(
    `/permission-building-directory?${query.toString()}`,
  )
}

export function listPermissionFloors(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionFloorDirectoryItem>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<PermissionFloorDirectoryItem>>(
    `/permission-floor-directory?${query.toString()}`,
  )
}
```

- [ ] **Step 4: Reuse the object-scope picker for housing scopes**

Extend `scopeLabels`, `ObjectScopeKind`, `objectScopeSearchLabels`, `organizationScopeKinds`, `scopeRequiresId`,
`isObjectScope`, and `overrideScopeText` with `building` and `floor`. Add two TanStack queries keyed by search text
and page. Active allows must disable an inactive building, an inactive floor, or a floor whose building is inactive;
deny rules may still target inactive rows. Reset the selected UUID and label whenever the query, scope, or effect
changes. The visible option text must be `item.label`; do not reconstruct names from UUIDs in the browser.

- [ ] **Step 5: Run permission UI tests, typecheck, and lint**

```bash
npm --prefix apps/web test -- PermissionsPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: the selector tests pass, TypeScript builds, and ESLint reports no errors.

- [ ] **Step 6: Commit the permission UI**

```bash
git add apps/web/src/settings/api.ts apps/web/src/settings/PermissionsPage.tsx \
  apps/web/src/settings/PermissionsPage.test.tsx
git commit -m "feat: select housing permission scopes"
```

## Task 6: Build the idempotent catalog service and verification workflow

**Files:**
- Create: `apps/api/app/housing/errors.py`
- Create: `apps/api/app/housing/schemas.py`
- Create: `apps/api/app/housing/seed.py`
- Create: `apps/api/app/housing/inventory_service.py`
- Create: `apps/api/tests/unit/housing/test_schemas.py`
- Create: `apps/api/tests/db/test_housing_seed.py`
- Create: `apps/api/tests/db/test_housing_inventory_service.py`

- [ ] **Step 1: Write failing schema tests for every room type**

Create `apps/api/tests/unit/housing/test_schemas.py` with a table covering these accepted pairs:

```python
@pytest.mark.parametrize(
    ("room_type", "capacity"),
    [("single", 1), ("double", 2), ("triple", 3), ("custom", 4), ("functional", 0)],
)
def test_room_create_accepts_exact_type_capacity_pairs(
    room_type: str,
    capacity: int,
) -> None:
    value = RoomCreate(
        floor_id=UUID("52000000-0000-4000-8000-000000000001"),
        room_number=" T-201 ",
        room_type=room_type,
        capacity=capacity,
        area_square_meters=Decimal("28.50"),
    )
    assert value.capacity == capacity


@pytest.mark.parametrize(
    ("room_type", "capacity"),
    [("single", 2), ("double", 1), ("triple", 4), ("custom", 0), ("functional", 1)],
)
def test_room_create_rejects_invalid_type_capacity_pairs(
    room_type: str,
    capacity: int,
) -> None:
    with pytest.raises(ValidationError):
        RoomCreate(
            floor_id=UUID("52000000-0000-4000-8000-000000000001"),
            room_number="T-201",
            room_type=room_type,
            capacity=capacity,
        )
```

Also assert blank notes are normalized to `None`, area must be positive, unknown fields are rejected, batch verify
requires 1–500 unique `(room_id, version)` entries, and `RoomUpdate` requires a positive optimistic-lock version.

- [ ] **Step 2: Define exact catalog input and output schemas**

Create `apps/api/app/housing/schemas.py` with `ConfigDict(str_strip_whitespace=True, extra="forbid")` on all inputs
and `ConfigDict(from_attributes=True)` on persisted responses. The public surface is:

```python
RoomType = Literal["single", "double", "triple", "custom", "functional"]
VerificationStatus = Literal["pending_verification", "verified"]
ServiceStatus = Literal["available", "problem", "maintenance", "disabled"]


class BuildingCreate(HousingInputModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=80)
    display_order: StrictInt = Field(default=0, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=500)


class BuildingUpdate(HousingInputModel):
    code: str | None = Field(default=None, min_length=1, max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=80)
    display_order: StrictInt | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class FloorCreate(HousingInputModel):
    building_id: UUID
    number: StrictInt = Field(ge=-20, le=200)
    name: str = Field(min_length=1, max_length=40)
    display_order: StrictInt = Field(default=0, ge=0, le=100_000)
    notes: str | None = Field(default=None, max_length=500)


class FloorUpdate(HousingInputModel):
    number: StrictInt | None = Field(default=None, ge=-20, le=200)
    name: str | None = Field(default=None, min_length=1, max_length=40)
    display_order: StrictInt | None = Field(default=None, ge=0, le=100_000)
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=500)


class RoomCreate(HousingInputModel):
    floor_id: UUID
    room_number: str = Field(min_length=1, max_length=32)
    room_type: RoomType
    capacity: StrictInt = Field(ge=0, le=100)
    area_square_meters: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    notes: str | None = Field(default=None, max_length=500)


class RoomUpdate(HousingInputModel):
    version: StrictInt = Field(ge=1)
    floor_id: UUID | None = None
    room_number: str | None = Field(default=None, min_length=1, max_length=32)
    room_type: RoomType | None = None
    capacity: StrictInt | None = Field(default=None, ge=0, le=100)
    area_square_meters: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    service_status: Literal["available", "maintenance", "disabled"] | None = None
    notes: str | None = Field(default=None, max_length=500)


class RoomVersionInput(HousingInputModel):
    room_id: UUID
    version: StrictInt = Field(ge=1)


class RoomVerifyRequest(HousingInputModel):
    version: StrictInt = Field(ge=1)


class RoomBatchVerifyRequest(HousingInputModel):
    rooms: Annotated[list[RoomVersionInput], Field(min_length=1, max_length=500)]
```

Use model validators to enforce the room type/capacity matrix on create, require at least one mutable field in each
update, reject a room update containing only `version`, and require unique room IDs in a batch. Use a field validator
shared by all create/update models to convert an empty notes string to `None`. Add these exact attribute-backed
responses; actor IDs are audit data and are not returned:

```python
class BuildingResponse(HousingResponseModel):
    id: UUID
    code: str
    name: str
    display_order: int
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class FloorResponse(HousingResponseModel):
    id: UUID
    building_id: UUID
    number: int
    name: str
    display_order: int
    is_active: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class RoomResponse(HousingResponseModel):
    id: UUID
    building_id: UUID
    floor_id: UUID
    room_number: str
    room_type: RoomType
    capacity: int
    area_square_meters: Decimal | None
    verification_status: VerificationStatus
    service_status: ServiceStatus
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 3: Run schema tests to verify failure, then make them pass**

```bash
.venv/bin/python -m pytest apps/api/tests/unit/housing/test_schemas.py -q
```

Expected before `schemas.py`: collection failure. Expected after it is complete: all schema cases pass.

- [ ] **Step 4: Write failing idempotent-seed tests**

Create `apps/api/tests/db/test_housing_seed.py`. Call `seed_initial_housing_inventory(db)` twice and assert both calls
leave exactly 4 buildings, 11 floors, 188 rooms, and one import batch. Before the second call, change seeded room
`4-409` to notes `管理员已核对原表`; assert the second seed keeps that note and the room version. Delete all four
housing tables' rows in dependency order, call the seed again, and assert it reconstructs the canonical counts and
SHA-256 without reading `/Volumes`.

- [ ] **Step 5: Implement the transaction-local idempotent seed**

Create `apps/api/app/housing/seed.py` with:

```python
@dataclass(frozen=True)
class HousingSeedResult:
    buildings: int
    floors: int
    rooms: int
    import_batches: int


def seed_initial_housing_inventory(db: Session) -> HousingSeedResult:
    for values in building_values():
        db.execute(postgresql_insert(HousingBuilding).values(**values).on_conflict_do_nothing())
    for values in floor_values():
        db.execute(postgresql_insert(HousingFloor).values(**values).on_conflict_do_nothing())
    for values in room_values():
        db.execute(postgresql_insert(HousingRoom).values(**values).on_conflict_do_nothing())
    db.execute(
        postgresql_insert(HousingImportBatch)
        .values(
            id=IMPORT_BATCH_ID,
            source_name=SOURCE_NAME,
            source_sha256=SOURCE_SHA256,
            row_count=len(INITIAL_ROOMS),
            correction_summary=CORRECTION_SUMMARY,
            imported_by_user_id=None,
        )
        .on_conflict_do_nothing()
    )
    db.flush()
    return HousingSeedResult(
        buildings=db.scalar(select(func.count(HousingBuilding.id))) or 0,
        floors=db.scalar(select(func.count(HousingFloor.id))) or 0,
        rooms=db.scalar(select(func.count(HousingRoom.id))) or 0,
        import_batches=db.scalar(select(func.count(HousingImportBatch.id))) or 0,
    )
```

Move `CORRECTION_SUMMARY` and Task 2's three pure value builders into `seed_inventory.py`; expose the builders as
`building_values()`, `floor_values()`, and `room_values()`, and import the same four symbols from both the migration
and runtime seed. Their returned dictionaries remain byte-for-byte equivalent to Task 2, including verified
functional rooms and pending residential rooms. The helper must not commit, update, or delete existing rows.
`ON CONFLICT DO NOTHING` is deliberate: administrator edits survive a repeat call, while the E2E reset can
reconstruct an empty catalog.

- [ ] **Step 6: Write failing catalog-service tests**

Create `apps/api/tests/db/test_housing_inventory_service.py` and cover:

- building, floor, triple-room, custom-room, and functional-room creation;
- NFKC/uppercase/whitespace-free room normalization and same-building duplicate rejection;
- inactive parent rejection;
- fixed type/capacity enforcement after merging a partial update with the stored row;
- a structural edit resets a verified room to `pending_verification` and increments version;
- a notes-only edit keeps verification status and increments version;
- `problem` cannot be set through catalog editing;
- stale update and stale verify raise `HousingStaleVersion` without mutation;
- batch verify locks rows in sorted UUID order and is all-or-nothing;
- deleting a building/floor with children raises `HousingCatalogHasChildren`;
- deleting a room, then its empty floor and building succeeds.

Use canonical room UUIDs from `INITIAL_ROOMS`; do not depend on query ordering.

- [ ] **Step 7: Implement catalog errors and service methods**

Create `apps/api/app/housing/errors.py` with these stable codes:

```python
class HousingServiceError(ValueError):
    code = "housing_service_error"


class HousingResourceNotFound(HousingServiceError):
    code = "housing_resource_not_found"


class HousingDuplicateCatalogEntry(HousingServiceError):
    code = "housing_duplicate_catalog_entry"


class HousingParentInactive(HousingServiceError):
    code = "housing_parent_inactive"


class HousingCatalogHasChildren(HousingServiceError):
    code = "housing_catalog_has_children"


class HousingInvalidRoomConfiguration(HousingServiceError):
    code = "housing_invalid_room_configuration"


class HousingIssueWorkflowRequired(HousingServiceError):
    code = "housing_issue_workflow_required"


class HousingStaleVersion(HousingServiceError):
    code = "housing_stale_version"
```

Create `HousingInventoryService` with these public methods and exact return types:

```python
create_building(input: BuildingCreate, *, actor_user_id: UUID) -> HousingBuilding
update_building(building_id: UUID, input: BuildingUpdate, *, actor_user_id: UUID) -> HousingBuilding
delete_building(building_id: UUID) -> None
create_floor(input: FloorCreate, *, actor_user_id: UUID) -> HousingFloor
update_floor(floor_id: UUID, input: FloorUpdate, *, actor_user_id: UUID) -> HousingFloor
delete_floor(floor_id: UUID) -> None
create_room(input: RoomCreate, *, actor_user_id: UUID) -> HousingRoom
update_room(room_id: UUID, input: RoomUpdate, *, actor_user_id: UUID) -> HousingRoom
delete_room(room_id: UUID) -> None
verify_room(room_id: UUID, *, expected_version: int, actor_user_id: UUID) -> HousingRoom
verify_rooms(inputs: Sequence[RoomVersionInput], *, actor_user_id: UUID) -> list[HousingRoom]
```

Normalize codes, names, and room numbers with NFKC, trim outer whitespace, remove all whitespace from codes and room
numbers, and uppercase them. Validate the merged room type/capacity with one shared function. Lock mutable rows with
`SELECT ... FOR UPDATE`; for batch verification sort unique UUIDs before the query and return rows in request order.
Compare `expected_version` while locked, update `updated_by_user_id`, and increment version exactly once per mutation.
Map named unique/check constraints to the errors above. Do not catch or hide unknown database failures.

- [ ] **Step 8: Run service, seed, migration, lint, and typing checks**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/housing/test_schemas.py \
  apps/api/tests/db/test_housing_seed.py \
  apps/api/tests/db/test_housing_inventory_service.py \
  apps/api/tests/db/test_migrations.py -q
.venv/bin/ruff check apps/api/app/housing apps/api/tests/unit/housing apps/api/tests/db
.venv/bin/mypy apps/api/app
```

Expected: all selected tests pass and both static checks succeed.

- [ ] **Step 9: Commit the catalog domain service**

```bash
git add apps/api/app/housing apps/api/migrations/versions/0009_housing_room_status_foundation.py \
  apps/api/tests/unit/housing apps/api/tests/db/test_housing_seed.py \
  apps/api/tests/db/test_housing_inventory_service.py
git commit -m "feat: manage and verify housing catalog"
```

## Task 7: Add object-scoped housing APIs, static status, summary, and filters

**Files:**
- Create: `apps/api/app/housing/access.py`
- Create: `apps/api/app/housing/router.py`
- Modify: `apps/api/app/housing/schemas.py`
- Modify: `apps/api/app/housing/inventory_service.py`
- Modify: `apps/api/app/api/router.py`
- Create: `apps/api/tests/api/test_housing.py`

- [ ] **Step 1: Write failing access and privacy tests**

Create `apps/api/tests/api/test_housing.py` with accounts for global admin, no-access user, building reader, floor
reader, building manager, and a reader with a floor deny. Seed overrides using the stable inventory IDs. Assert:

- no-access collection requests return `403 permission_denied`;
- building readers see only that building and its rooms;
- floor readers see the parent building label but only their floor and rooms;
- an explicit floor deny removes that floor from a broader building allow;
- a missing room and an existing out-of-scope room both return identical `404 housing_resource_not_found` bodies;
- `housing.read` without `housing.rooms.manage` returns empty `allowed_actions` and rejects mutations;
- scoped `housing.rooms.manage` works only for matching objects;
- successful, denied, and failed mutations append exactly one safe audit event with the expected outcome;
- every success and problem response under `/housing` has `Cache-Control: no-store, private`.

- [ ] **Step 2: Write failing projection and filter tests**

Using the migrated seed, assert the initial overview is:

```python
assert summary.model_dump() == {
    "total_rooms": 188,
    "occupied_rooms": 0,
    "unoccupied_rooms": 188,
    "assignable_rooms": 0,
    "unassignable_rooms": 188,
    "problem_rooms": 0,
    "pending_verification_rooms": 171,
    "total_capacity": 223,
    "current_occupants": 0,
    "remaining_capacity": 223,
    "occupancy_rate": Decimal("0.00"),
    "single_rooms": 119,
    "double_rooms": 52,
    "triple_rooms": 0,
    "custom_rooms": 0,
    "functional_rooms": 17,
}
```

Verify all cards truthfully return `current_occupants=0`, `current_gender=null`,
`has_future_reservation=false`, and `occupancy_status="unoccupied"`. Pending residential rooms and functional rooms
are unassignable. After batch-verifying a single and double room, `assignability=assignable` returns exactly those
two, while each also remains in `occupancy=unoccupied`. Combine building, floor, search, repeated room type,
verification, service status, current gender, occupancy, and assignability filters and assert summary and pagination
are calculated from one filtered set and one `as_of` timestamp. `current_gender=male` and `female` return no rows in
this unit, while `current_gender=unspecified` retains the empty rooms.

- [ ] **Step 3: Define the read contracts**

Add these response models to `apps/api/app/housing/schemas.py`:

```python
class HousingAllowedActions(BaseModel):
    edit: bool
    delete: bool
    verify: bool


class HousingBuildingItem(BuildingResponse):
    allowed_actions: list[Literal["edit", "delete", "create_floor"]]


class HousingFloorItem(FloorResponse):
    building_name: str
    allowed_actions: list[Literal["edit", "delete", "create_room"]]


class HousingCatalogResponse(BaseModel):
    buildings: list[HousingBuildingItem]
    floors: list[HousingFloorItem]
    can_create_building: bool


class HousingRoomCard(RoomResponse):
    building_code: str
    building_name: str
    building_is_active: bool
    floor_number: int
    floor_name: str
    floor_is_active: bool
    current_occupants: Literal[0]
    remaining_capacity: int
    current_gender: None
    has_future_reservation: Literal[False]
    occupancy_status: Literal["unoccupied"]
    is_assignable: bool
    status_text: str
    allowed_actions: HousingAllowedActions


class HousingSummary(BaseModel):
    total_rooms: int
    occupied_rooms: int
    unoccupied_rooms: int
    assignable_rooms: int
    unassignable_rooms: int
    problem_rooms: int
    pending_verification_rooms: int
    total_capacity: int
    current_occupants: int
    remaining_capacity: int
    occupancy_rate: Decimal
    single_rooms: int
    double_rooms: int
    triple_rooms: int
    custom_rooms: int
    functional_rooms: int


class HousingOverviewResponse(BaseModel):
    as_of: datetime
    summary: HousingSummary
    rooms: list[HousingRoomCard]
    page: int
    page_size: int
    total: int
```

The literal empty values freeze the next unit's JSON keys without pretending occupancy exists. The next housing
unit will widen these types when `housing_stays` is introduced.

- [ ] **Step 4: Implement deny-first housing access helpers**

Create `HousingAccessService(db, account_id, occurred_at)` around one `RbacDecisionContext`. Resource tokens are:

```python
@staticmethod
def building_tokens(building_id: UUID) -> set[str]:
    return {"global", f"building:{building_id}"}

@staticmethod
def floor_tokens(building_id: UUID, floor_id: UUID) -> set[str]:
    return {"global", f"building:{building_id}", f"floor:{floor_id}"}

room_tokens = floor_tokens
```

Expose `can(permission, tokens)`, `can_read_room`, `can_manage_room`, and filtering helpers for buildings, floors,
and rooms. A building is visible if its own building token is readable or at least one non-denied child floor is
readable. A floor is visible only if its floor tokens are readable. Never authorize from a display name or room
number. Use the same helpers in collection filters and object routes so a deny cannot produce divergent behavior.
Room `edit` and `delete` are true only when `housing.rooms.manage` is allowed for the room; `verify` additionally
requires `pending_verification`. Building/floor action lists use the fixed order shown in their Literal declarations,
and `can_create_building` requires `housing.rooms.manage` on the exact `{"global"}` resource.

- [ ] **Step 5: Implement catalog, overview, and static projection queries**

Add `visible_catalog(access)` and `overview(access, filters, page, page_size, as_of)` to
`HousingInventoryService`. Load buildings/floors/rooms in deterministic display order, filter through the access
context before applying user filters, then paginate. Build each card with these exact derivations:

```python
current_occupants = 0
remaining_capacity = room.capacity
is_assignable = (
    building.is_active
    and floor.is_active
    and room.verification_status == "verified"
    and room.service_status == "available"
    and room.room_type != "functional"
    and room.capacity > 0
)
status_text = (
    "待核对"
    if room.verification_status == "pending_verification"
    else "功能间，不可入住"
    if room.room_type == "functional"
    else "楼栋或楼层已停用"
    if not building.is_active or not floor.is_active
    else "维修中"
    if room.service_status == "maintenance"
    else "已停用"
    if room.service_status == "disabled"
    else "问题房"
    if room.service_status == "problem"
    else "空闲，可入住"
)
```

The filter object uses `building_id`, `floor_id`, stripped `query`, a set of room types, verification statuses,
service statuses, `current_gender` (`male`/`female`/`unspecified`), `occupancy`
(`occupied`/`unoccupied`), and `assignability` (`assignable`/`unassignable`). In this unit `occupied`, male, and
female always produce an empty set; `unoccupied` and unspecified retain all rows. Summary counts the complete
filtered set before pagination and rounds occupancy rate to two decimal places. `as_of` is one timezone-aware UTC
instant captured by the route and returned unchanged.

- [ ] **Step 6: Implement private REST routes and atomic audit writes**

Create a private `APIRoute` that sets `request.state.private_portal_response = True` before invoking the original
handler and also sets the header on successful responses. This makes both success and handled problem responses
`no-store, private` through the existing central exception handler. Mount these endpoints under `/housing`:

```text
GET    /housing/catalog
GET    /housing/overview
POST   /housing/rooms/batch-verify
GET    /housing/rooms/{room_id}
POST   /housing/buildings
PATCH  /housing/buildings/{building_id}
DELETE /housing/buildings/{building_id}
POST   /housing/floors
PATCH  /housing/floors/{floor_id}
DELETE /housing/floors/{floor_id}
POST   /housing/rooms
PATCH  /housing/rooms/{room_id}
DELETE /housing/rooms/{room_id}
POST   /housing/rooms/{room_id}/verify
```

Declare the static `/housing/rooms/batch-verify` route before every `/housing/rooms/{room_id}` route so FastAPI
never attempts to parse `batch-verify` as a UUID.

GET overview accepts page 1+, page size 1–100, repeated room-type/status query keys, and the filters from Step 5.
Every write must call `db.commit()` before `with db.begin():`, perform object authorization before business
validation, invoke the service inside that transaction, and append its allowed audit event before the transaction
exits. On a denied or failed write, roll back first and append a separate `denied` or `failed` event without exposing
names, room numbers, notes, or request bodies. Use only `building_id`, `floor_id`, `room_id`, `scope_kind`, `scope_id`,
`result_count`, and an allowlisted `error_code` as metadata. Map not-found or out-of-scope to the same private 404;
map duplicate entry, inactive parent, non-empty parent deletion, invalid room configuration, issue-workflow misuse,
and stale version to 409 using each exception's stable code. Pydantic request validation remains 422. Include
`housing.router` from `app.api.router`.

Use the exact actions `housing.building.create`, `housing.building.update`, `housing.building.delete`,
`housing.floor.create`, `housing.floor.update`, `housing.floor.delete`, `housing.room.create`,
`housing.room.update`, `housing.room.delete`, `housing.room.verify`, and `housing.room.verify_batch`. The resource
types are `housing_building`, `housing_floor`, and `housing_room`; a batch event has no resource ID and records only
scope plus result count.

- [ ] **Step 7: Run housing API, RBAC regression, lint, and typing checks**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_housing.py \
  apps/api/tests/api/test_permissions.py \
  apps/api/tests/api/test_audit.py -q
.venv/bin/ruff check apps/api/app/housing apps/api/app/api/router.py \
  apps/api/tests/api/test_housing.py
.venv/bin/mypy apps/api/app
```

Expected: all selected tests pass, object privacy signatures match, and static checks succeed.

- [ ] **Step 8: Commit the housing API**

```bash
git add apps/api/app/housing apps/api/app/api/router.py apps/api/tests/api/test_housing.py
git commit -m "feat: expose scoped housing room status"
```

## Task 8: Build the responsive read-only room-status experience

**Files:**
- Create: `apps/web/src/housing/api.ts`
- Create: `apps/web/src/housing/api.test.ts`
- Create: `apps/web/src/housing/HousingFilters.tsx`
- Create: `apps/web/src/housing/RoomCard.tsx`
- Create: `apps/web/src/housing/RoomCard.test.tsx`
- Create: `apps/web/src/housing/HousingOverviewPage.tsx`
- Create: `apps/web/src/housing/HousingOverviewPage.test.tsx`
- Create: `apps/web/src/housing/housing.css`

- [ ] **Step 1: Write failing typed API serialization tests**

Mock `fetch` in `apps/web/src/housing/api.test.ts` and assert `listHousingOverview` serializes repeated values as:

```text
/housing/overview?building_id=...&floor_id=...&query=409&room_type=single&room_type=double&verification_status=verified&current_gender=unspecified&occupancy=unoccupied&assignability=assignable&page=2&page_size=40
```

Assert blank filters are omitted and `getHousingCatalog` calls `/housing/catalog`. Reuse `apiFetch`; do not add a
second credential, CSRF, or problem-details client.

- [ ] **Step 2: Define the complete browser contract**

Create `apps/web/src/housing/api.ts` with these unions and core types:

```typescript
export type RoomType = "single" | "double" | "triple" | "custom" | "functional"
export type VerificationStatus = "pending_verification" | "verified"
export type ServiceStatus = "available" | "problem" | "maintenance" | "disabled"
export type CurrentGender = "male" | "female" | null

export type HousingRoom = {
  id: string
  building_id: string
  floor_id: string
  building_code: string
  building_name: string
  building_is_active: boolean
  floor_number: number
  floor_name: string
  floor_is_active: boolean
  room_number: string
  room_type: RoomType
  capacity: number
  area_square_meters: string | null
  verification_status: VerificationStatus
  service_status: ServiceStatus
  notes: string | null
  version: number
  current_occupants: number
  remaining_capacity: number
  current_gender: CurrentGender
  has_future_reservation: boolean
  occupancy_status: "occupied" | "unoccupied"
  is_assignable: boolean
  status_text: string
  allowed_actions: { edit: boolean; delete: boolean; verify: boolean }
  created_at: string
  updated_at: string
}

export type HousingFiltersValue = {
  buildingId?: string
  floorId?: string
  query?: string
  roomTypes?: RoomType[]
  verificationStatuses?: VerificationStatus[]
  serviceStatuses?: ServiceStatus[]
  currentGender?: "male" | "female" | "unspecified"
  occupancy?: "occupied" | "unoccupied"
  assignability?: "assignable" | "unassignable"
  page?: number
  pageSize?: number
}
```

Mirror `HousingSummary`, `HousingOverview`, `HousingBuilding`, `HousingFloor`, and `HousingCatalog` from the backend.
Export `getHousingCatalog()` and `listHousingOverview(filters)`. Use `URLSearchParams.append` for array filters and
stable key order so tests and browser history are deterministic.

- [ ] **Step 3: Run API tests and make them pass**

```bash
npm --prefix apps/web test -- src/housing/api.test.ts
```

Expected: all serialization and response tests pass.

- [ ] **Step 4: Write failing semantic room-card tests**

Create four fixtures in `RoomCard.test.tsx` and assert:

- verified empty assignable room: class `housingRoomCard--available`, icon plus text `空闲，可入住`;
- occupied full room: class `housingRoomCard--occupied`, text `已入住 1 / 1`;
- occupied double room with one remaining place: red occupied class plus green text
  `可继续入住 · 剩余 1 位`;
- pending or functional room: class `housingRoomCard--unavailable` and explicit `待核对` or `功能间，不可入住`.

The tests must query visible text and accessible names in addition to CSS classes so color is never the only signal.
Also assert area, `男住`/`女住`/`未指定`, capacity, future reservation, notes, and permitted action buttons render
from the API fields.

- [ ] **Step 5: Implement the presentational room card**

Create `RoomCard({ room, selected, onSelectedChange, onEdit, onVerify, onDelete })`. Derive its primary tone in this
order: current occupants greater than zero means occupied/red; otherwise assignable means available/green;
otherwise unavailable/gray. Never make a problem room orange-only: when future data has occupants it remains red
and gets a separate orange problem badge. Render the partial-capacity badge only when occupied, remaining capacity
is positive, and `is_assignable` is true. Render the checkbox only for a pending room whose `verify` action is true.

Use a real `<article>` with an accessible name containing the room number and status. Buttons must be at least 44 px
high on touch layouts and include the room number in their accessible labels.

- [ ] **Step 6: Write failing overview and combined-filter tests**

Mock catalog and overview responses in `HousingOverviewPage.test.tsx`. Assert the page:

- renders all required summary values and the server's `as_of` in `Asia/Shanghai`;
- narrows floor options after selecting a building and clears a now-invalid floor;
- sends room type, current gender, occupancy, assignability, status, search, building, and floor together;
- treats `已入住` and `可入住` as independent controls;
- resets page to 1 after a filter changes;
- exposes loading, empty, private 403/404, and retry states without retaining old room cards;
- collapses filters when `window.innerWidth < 720` and restores focus to the filter trigger on close.

- [ ] **Step 7: Implement filters, summary, pagination, and responsive room grid**

`HousingFilters` owns no server state. It receives catalog, current value, `open`, `onOpenChange`, `onApply`, and
`onReset`. Render building/floor selects, search, multi-select checkbox groups for type and status, and separate
gender/occupancy/assignability selects. On mobile use an `aria-modal` filter sheet with Escape handling, focus trap,
body scroll lock, Apply, and Reset. Desktop keeps the same controls visible in a side/top filter panel.

`HousingOverviewPage` uses these query keys:

```typescript
["housing-catalog"]
["housing-overview", normalizedFilters]
```

Render summary cards, filtered count, a semantic room-card grid, and previous/next pagination. While a permission
refresh or object-access failure is active, hide cached catalog and room data before showing the error. Import one
`housing.css` file with grid breakpoints for 1440, 1024, 768, and 360 widths; no housing page may set a fixed content
width or require horizontal scrolling.

- [ ] **Step 8: Run housing component tests, full frontend tests, build, and lint**

```bash
npm --prefix apps/web test -- \
  src/housing/api.test.ts \
  src/housing/RoomCard.test.tsx \
  src/housing/HousingOverviewPage.test.tsx
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all tests pass, the production build succeeds, and ESLint reports no errors.

- [ ] **Step 9: Commit the read-only room-status UI**

```bash
git add apps/web/src/housing
git commit -m "feat: show responsive housing room status"
```

## Task 9: Add catalog dialogs, verification actions, route, and navigation

**Files:**
- Create: `apps/web/src/housing/HousingCatalogPanel.tsx`
- Create: `apps/web/src/housing/HousingCatalogPanel.test.tsx`
- Create: `apps/web/src/housing/HousingCatalogDialogs.tsx`
- Modify: `apps/web/src/housing/api.ts`
- Modify: `apps/web/src/housing/HousingOverviewPage.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1: Write failing catalog-management tests**

Mock every write method and cover these interactions in `HousingCatalogPanel.test.tsx`:

- create, edit, activate/deactivate, and delete a building when its `allowed_actions` permit it;
- create, edit, activate/deactivate, and delete a floor only under an authorized building;
- create single, double, triple, custom-capacity, and functional rooms;
- fixed room types force capacities 1/2/3/0; custom exposes a required 1–100 capacity input;
- edit keeps entered data after a 409, sends the current version, and refreshes catalog plus overview on success;
- individual verification sends `{version}`;
- batch selection includes only pending rooms with `verify=true`, sends their IDs and versions, and clears selection
  only after success;
- a 409 `housing_stale_version` keeps the dialog open, clears selected batch rows, refreshes both queries, and tells
  the user to review current data;
- buttons not present in `allowed_actions` are absent rather than merely disabled;
- delete confirmation names the exact object and cancellation performs no request.

- [ ] **Step 2: Add the mutation API contract**

Extend `apps/web/src/housing/api.ts` with input types matching Task 6 and these functions:

```typescript
createHousingBuilding(input): Promise<HousingBuilding>
updateHousingBuilding(id, input): Promise<HousingBuilding>
deleteHousingBuilding(id): Promise<void>
createHousingFloor(input): Promise<HousingFloor>
updateHousingFloor(id, input): Promise<HousingFloor>
deleteHousingFloor(id): Promise<void>
createHousingRoom(input): Promise<HousingRoom>
updateHousingRoom(id, input): Promise<HousingRoom>
deleteHousingRoom(id): Promise<void>
verifyHousingRoom(id, version): Promise<HousingRoom>
verifyHousingRooms(rooms: Array<{room_id: string; version: number}>): Promise<HousingRoom[]>
```

`verifyHousingRoom` posts `{version}` and `verifyHousingRooms` posts `{rooms}`. Use `apiFetch`, JSON content type,
and the existing CSRF behavior. Do not call `fetch` directly from components.

- [ ] **Step 3: Implement focused accessible dialogs**

Create `HousingCatalogDialogs.tsx` containing separate building, floor, and room forms plus one shared dialog shell.
Each dialog uses `role=dialog`, `aria-modal=true`, labelled title, initial focus, Escape close, backdrop close, focus
trap, body scroll lock, and a fully locked pending state. Normalize blank notes to `null` before submission. Room
edit shows a visible warning that structural changes return the room to `待核对`.

The service-status selector offers only `可用`, `维修`, and `停用`; it does not offer `问题房` because issue
records belong to the next unit. Functional rooms always submit capacity 0. Moving a room only selects among floors
whose building and floor are active.

- [ ] **Step 4: Implement the catalog panel and verification flow**

`HousingCatalogPanel` receives catalog, current rooms, pending state, and mutation callbacks. Render a hierarchical
building/floor tree with counts and action menus. `can_create_building` controls the top-level create button;
building/floor `allowed_actions` control all subordinate buttons. Individual and batch verification use server
versions from the most recent query. After every successful mutation, invalidate exactly:

```typescript
queryClient.invalidateQueries({ queryKey: ["housing-catalog"] })
queryClient.invalidateQueries({ queryKey: ["housing-overview"] })
```

Integrate the panel into `HousingOverviewPage` behind a `目录管理` button whenever at least one server-provided
management action exists. Read-only users never mount write forms.

- [ ] **Step 5: Write failing route and navigation tests**

In `AppShell.test.tsx`, assert `housing.read` shows desktop and mobile links named `房态管理`, while neither
`housing.rooms.manage` alone nor an unrelated permission shows the link. In `router.test.tsx`, assert both global
and scoped `housing.read` can render `/housing`, an unauthorized user is redirected to `/`, and the page receives
global plus scoped permission sets without treating a scoped permission as global.

- [ ] **Step 6: Mount the permission-aware route and navigation item**

Add `housing.read` to the scoped-permission visibility allowlist in `AuthenticatedLayout`. Add a `HousingRoute`
that allows `housing.read` from either `permissions` or `scoped_permissions` and renders:

```tsx
<HousingOverviewPage
  permissions={new Set(user?.permissions ?? [])}
  scopedPermissions={new Set(user?.scoped_permissions ?? [])}
/>
```

Register `{ path: "housing", element: <HousingRoute /> }` under the authenticated layout. Add an AppShell item with
the `Building2` icon, label and short label `房态管理`, permission `housing.read`, and a mobile priority after the
teaching section. The route remains hidden from customer service, coach, guardian, student, and other staff unless
an administrator explicitly grants `housing.read`.

- [ ] **Step 7: Run catalog, route, shell, and all frontend gates**

```bash
npm --prefix apps/web test -- \
  src/housing/HousingCatalogPanel.test.tsx \
  src/housing/HousingOverviewPage.test.tsx \
  src/app/router.test.tsx \
  src/layout/AppShell.test.tsx
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: mutation, route, and navigation tests pass; full frontend tests, build, and lint remain green.

- [ ] **Step 8: Commit route and catalog management**

```bash
git add apps/web/src/housing apps/web/src/app/router.tsx \
  apps/web/src/app/router.test.tsx apps/web/src/layout/AppShell.tsx \
  apps/web/src/layout/AppShell.test.tsx
git commit -m "feat: manage housing room catalog"
```

## Task 10: Re-seed housing after E2E resets and verify four responsive viewports locally

**Files:**
- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Create: `apps/web/tests/housing-room-status-foundation.spec.ts`
- Create: `scripts/run-housing-room-status-e2e.sh`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write the failing post-truncate seed regression**

Add `test_e2e_seed_rebuilds_housing_catalog_after_people_truncate` to
`apps/api/tests/cli/test_seed_e2e.py`. Run `seed_e2e` twice and after each call assert:

```python
assert db.scalar(select(func.count(HousingBuilding.id))) == 4
assert db.scalar(select(func.count(HousingFloor.id))) == 11
assert db.scalar(select(func.count(HousingRoom.id))) == 188
assert db.scalar(
    select(func.count(HousingRoom.id)).where(
        HousingRoom.verification_status == "pending_verification"
    )
) == 171
assert db.scalar(select(func.sum(HousingRoom.capacity))) == 223
```

After the first call, change a room note and commit. The second E2E reset must remove the changed catalog through the
existing cascade and rebuild the canonical value. This test is intentionally different from Task 6's ordinary
idempotency test: an E2E run is a destructive isolated reset, while a normal seed call preserves existing rows.

- [ ] **Step 2: Verify the regression fails because the cascade leaves no catalog**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py \
  -k 'housing_catalog_after_people_truncate' -q
```

Expected: the housing counts are zero after `TRUNCATE ... CASCADE`.

- [ ] **Step 3: Re-seed the catalog inside the existing isolated E2E transaction**

Import `seed_initial_housing_inventory` in `app.cli.seed_e2e`. Immediately after `db.expunge_all()` and before
creating projects or people, call:

```python
housing = seed_initial_housing_inventory(db)
if housing != HousingSeedResult(buildings=4, floors=11, rooms=188, import_batches=1):
    raise E2ESeedRefused("housing seed did not rebuild the canonical catalog")
```

Do not add a second truncate command and do not commit inside the seed helper. The complete E2E rebuild remains one
transaction.

- [ ] **Step 4: Write the four-viewport Playwright journey**

Create `apps/web/tests/housing-room-status-foundation.spec.ts` using the existing four projects in
`playwright.config.ts` and helpers from `tests/helpers.ts`. For each viewport:

1. login as admin and open `房态管理` from the visible navigation surface;
2. assert 188 total, 171 pending, 119 single, 52 double, 17 functional, and theoretical capacity 223;
3. assert `4-409`, `9-305`, `5-311`, and normalized A-building room numbers are searchable;
4. combine `未入住` plus `不可入住`, then `待核对`, and verify results without horizontal overflow;
5. verify one pending room and assert its card changes from gray `待核对` to green `空闲，可入住`;
6. select two other pending rooms, batch verify them, and assert the pending count drops by two more;
7. create building `测试楼-{width}`, a floor, a triple room, and a custom-capacity room using viewport-unique values;
8. edit one room, verify the stale-warning/reset-to-pending behavior, then verify again;
9. open and close every dialog by button and Escape, assert focus returns to its trigger, and check 44 px touch
   targets on the 360 viewport;
10. logout, login as customer service, assert the housing navigation item is absent, and direct `/housing` navigation
    returns to the workbench.

Call `expectNoHorizontalOverflow`, `expectHorizontallyInsideViewport`, and `expectAboveMobileNavigation` after the
filter sheet, room grid, and catalog dialogs. Do not assert occupied/red cards in E2E because this unit has no stay
records; that visual contract is covered by the component fixture in Task 8 and becomes browser-testable next unit.

- [ ] **Step 5: Create a two-service, non-Docker launcher**

Create executable `scripts/run-housing-room-status-e2e.sh` based on the ERP API/Web portions of the evaluation
launcher. It must:

- require `TEST_DATABASE_URL` ending `_test` or targeting `erp_e2e`;
- require the existing encryption, lookup, session-pepper, and evaluation service-token variables;
- default API to 8001 and Web to 5176, with `E2E_API_PORT`/`E2E_WEB_PORT` overrides;
- refuse occupied ports rather than killing unrelated processes;
- run `alembic upgrade head`, then rebuild E2E data before each Playwright viewport project;
- start only ERP Uvicorn and Vite local processes;
- wait for `/api/v1/health/live` and `/login`;
- loop over `desktop-1440`, `desktop-1024`, `tablet-768`, and `mobile-360`; before each project run
  `python -m app.cli.seed_e2e`, then run only `housing-room-status-foundation.spec.ts --project=<name>` so mutations
  from one viewport cannot alter the next viewport's canonical 188-room baseline;
- trap exit, print the API/Web log tails on failure, stop only its recorded PIDs, and remove its temporary directory;
- contain neither `docker` nor `docker-compose`.

Mark it executable with `chmod +x scripts/run-housing-room-status-e2e.sh`; this mechanical mode change does not edit
file contents.

- [ ] **Step 6: Add launcher contract tests and run the local browser gate**

Add a test in `test_operations_docs.py` that checks the script name, two override ports, E2E spec name, seed command,
and absence of Docker commands. Then run:

```bash
bash -n scripts/run-housing-room-status-e2e.sh
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  scripts/run-housing-room-status-e2e.sh
```

Expected: the seed test passes and Playwright passes in `desktop-1440`, `desktop-1024`, `tablet-768`, and
`mobile-360` without starting Docker.

- [ ] **Step 7: Commit the local E2E gate**

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/api/tests/cli/test_operations_docs.py \
  apps/web/tests/housing-room-status-foundation.spec.ts \
  scripts/run-housing-room-status-e2e.sh
git commit -m "test: verify housing room status locally"
```

## Task 11: Document the truthful boundary and run the complete non-Docker quality gate

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-03-housing-room-status-design.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] **Step 1: Write failing status-documentation assertions**

Add a test requiring these exact ideas in README and the design status:

```python
required = {
    "房间目录与静态房态基础",
    "4 幢楼、11 个楼层、188 个房间条目",
    "171 间住宿房需要逐间或批量核对",
    "当前人数固定为 0",
    "尚未开放入住、预约、问题房、调房、退房和实名导出",
    "scripts/run-housing-room-status-e2e.sh",
    "Docker/NAS 仍延期到全部 ERP 项目完成后",
}
assert required <= set(readme.splitlines()) or all(text in readme for text in required)
```

Also reject the completion claims `住宿模块已完成`, `入住功能已完成`, `公安接口已完成`, `Docker/NAS 已完成`,
and `生产房态已上线`.

- [ ] **Step 2: Update operational documentation without changing deployment**

Add a README section named `房间目录与静态房态基础`. State that administrators can maintain the four-level source
catalog as building/floor/room data, verify imported rooms, grant building/floor scopes, and use static summary,
filters, and responsive cards. Explicitly state that this unit has no stay records, all current counts/genders and
future reservations are empty defaults, and no room should be treated as operationally allocated yet.

Document the local launcher command and its two optional ports. Do not edit Compose files, Dockerfiles, `.env`
production keys, backup scripts, or NAS instructions. After the final gate passes, change the design status line to:

```markdown
- **状态：** 第一实施单元本地实现与非 Docker 门禁已完成；入住生命周期与实名导出待后续单元
```

- [ ] **Step 3: Run the complete backend gate against the isolated PostgreSQL database**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
```

Expected: the entire backend suite passes, Ruff reports no issues, and mypy exits successfully.

- [ ] **Step 4: Run the complete frontend gate**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all Vitest tests pass, TypeScript/Vite build succeeds, and ESLint reports no errors.

- [ ] **Step 5: Re-run the local browser gate after documentation and full-suite checks**

```bash
bash -n scripts/run-housing-room-status-e2e.sh
TEST_DATABASE_URL="$TEST_DATABASE_URL" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  scripts/run-housing-room-status-e2e.sh
git diff --check
```

Expected: all four viewports pass again and `git diff --check` prints nothing. Do not run `scripts/check.sh` in this
unit because it is the Docker/Compose production gate that the user explicitly deferred.

- [ ] **Step 6: Request independent code and spec review**

Use `superpowers:requesting-code-review` after all checks are green. The reviewer must compare the implementation
to the scope boundary, verify object-deny privacy, seed preservation versus E2E reset behavior, static zero-occupant
truthfulness, and responsive semantics. Resolve every Critical or Important finding and repeat Steps 3–5.

- [ ] **Step 7: Commit truthful completion status**

```bash
git add README.md docs/superpowers/specs/2026-08-03-housing-room-status-design.md \
  apps/api/tests/cli/test_operations_docs.py
git commit -m "docs: record housing foundation local gate"
```

## Completion boundary

After Task 11, report only that the first housing implementation unit is locally complete. The following remain
required work in later sequential plans: stays and reservations, real occupant/gender projection, account-person
selection, problem-room records and notes, transfer history, checkout confirmation, family housing view, sensitive
Excel/CSV export, police adapter, Docker/NAS packaging, production backup/restore rehearsal, and production rollout.
