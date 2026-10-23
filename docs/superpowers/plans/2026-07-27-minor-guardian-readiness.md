# Minor Guardian Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在人员中心显示成年/未成年和监护就绪状态，强制有效成年监护人账号规则，保留监护关系历史，并在最后一名有效监护人失效时安全停用未成年学员账号。

**Architecture:** 新建独立的 `GuardianReadinessPolicy`，集中计算年龄、监护关系有效性、管理员诊断原因和受影响学员账号，现有身份、账号、认证及权限服务只调用这一判定源。监护关系改用独立 UUID 和结束记录保存历史；所有列表状态均按 `Asia/Shanghai` 业务日期动态计算，不保存易过期的年龄状态。React 人员名单和详情页只渲染服务端派生状态，并沿用现有对象权限、脱敏和响应式布局。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、Docker Compose

---

## Scope guardrails

- 本计划只完成阶段 1A 的人员、监护关系、账号和界面增强。
- 阶段 1B 的报名、阶段 2 的评价、阶段 3 的住宿只复用本次建立的 `reference_date` 判定接口；本计划不创建这些业务模块。
- 单项权限覆盖规则不参与监护人实名有效性判断；只有成年、实名资料、启用账号、监护人角色和有效关系参与。
- 成年学员可以继续或新建监护关系，家庭访问持续到关系结束或到期。
- 所有服务端日期测试必须注入固定日期，不使用测试运行当天。

## File map

**Create:**

- `apps/api/migrations/versions/0003_guardian_relationship_history.py` — 监护关系历史迁移、独立主键和重叠日期约束。
- `apps/api/app/identity/readiness.py` — 年龄、监护人有效性、批量状态投影和账号自动停用的唯一判定源。
- `apps/api/tests/services/test_readiness_policy.py` — 判定矩阵、批量查询和失效原因测试。

**Modify:**

- `apps/api/app/identity/models.py` — 历史关系字段。
- `apps/api/app/identity/schemas.py` — 成年状态、监护状态、关系状态和诊断类型。
- `apps/api/app/identity/service.py` — 使用统一判定、结束关系而非删除、批量列表投影。
- `apps/api/app/identity/router.py` — 新响应字段、管理员诊断、关系结束接口及审计。
- `apps/api/app/auth/service.py` — 开通/启用阻断、登录和会话到期校验、关联账号自动停用。
- `apps/api/app/auth/dependencies.py`、`apps/api/app/auth/router.py` — 将请求编号传入认证校验。
- `apps/api/app/auth/admin_router.py` — 启用错误反馈和监护人账号停用联动审计。
- `apps/api/app/rbac/service.py`、`apps/api/app/rbac/router.py` — 监护人角色移除联动；单项权限变更不联动。
- `apps/api/app/core/time.py` — 任意时间转换为上海业务日期。
- `apps/api/app/audit/service.py` — 安全放行监护关系 UUID 和阻断原因元数据。
- `apps/api/tests/db/test_migrations.py` — 新迁移结构和约束。
- `apps/api/tests/services/test_guardians.py`、`apps/api/tests/services/test_accounts.py` — 服务行为回归。
- `apps/api/tests/api/conftest.py` — 测试监护人账号和角色先决条件。
- `apps/api/tests/api/test_people.py`、`apps/api/tests/api/test_guardians.py`、`apps/api/tests/api/test_accounts.py`、`apps/api/tests/api/test_auth_sessions.py`、`apps/api/tests/api/test_permissions.py`、`apps/api/tests/api/test_audit.py` — API、会话、权限和审计验收。
- `apps/api/app/cli/seed_e2e.py`、`apps/api/tests/cli/test_seed_e2e.py` — 可用监护人种子和待绑定学员种子。
- `apps/web/src/people/api.ts` — 新契约类型和关系结束请求。
- `apps/web/src/people/PeoplePage.tsx`、`apps/web/src/people/PeoplePage.test.tsx` — 名单状态标签。
- `apps/web/src/people/GuardianPanel.tsx`、`apps/web/src/people/GuardianPanel.test.tsx` — 诊断、修复入口、历史和结束关系。
- `apps/web/src/people/PersonDetailPage.tsx`、`apps/web/src/people/PersonDetailPage.test.tsx` — 成年状态和管理能力下发。
- `apps/web/src/people/people.css` — 桌面/手机状态样式。
- `apps/web/src/settings/AccountsPage.tsx`、`apps/web/src/settings/AccountsPage.test.tsx` — 监护就绪错误提示。
- `apps/web/tests/helpers.ts`、`apps/web/tests/platform-foundation.spec.ts` — 管理员和客服响应式旅程。
- `README.md` — 阶段 1A 可用能力和边界。

## Test environment

在首次执行测试前启动隔离数据库：

```bash
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml up -d --wait db
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

如果 Docker daemon 不可用，不得声称数据库或 E2E 测试通过；可以先完成不依赖 Docker 的 Web 测试、lint、build 和静态检查，并在最终验证时明确记录阻塞项。

### Task 1: Migrate guardian relationships to immutable history records

**Files:**

- Create: `apps/api/migrations/versions/0003_guardian_relationship_history.py`
- Modify: `apps/api/app/identity/models.py:88-114`
- Modify: `apps/api/app/identity/service.py:571-580`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/api/test_guardians.py:109-126`

- [ ] **Step 1: Write the failing migration assertions**

Add the following test to `apps/api/tests/db/test_migrations.py`:

```python
def test_guardian_relationships_have_history_identity_and_overlap_guard(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    columns = {item["name"]: item for item in inspector.get_columns("guardian_relationships")}
    primary_key = inspector.get_pk_constraint("guardian_relationships")
    foreign_keys = {
        tuple(item["constrained_columns"]): tuple(item["referred_columns"])
        for item in inspector.get_foreign_keys("guardian_relationships")
    }

    assert primary_key["constrained_columns"] == ["id"]
    assert {
        "id",
        "guardian_person_id",
        "student_person_id",
        "valid_from",
        "valid_until",
        "ended_at",
        "ended_by_user_id",
        "end_reason",
        "created_at",
        "updated_at",
    } <= columns.keys()
    assert ("ended_by_user_id",) in foreign_keys
    with postgres_engine.connect() as connection:
        exclusion = connection.scalar(
            text(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'guardian_relationships'::regclass
                  AND contype = 'x'
                """
            )
        )
    assert exclusion == "excl_guardian_relationships_overlapping_dates"
```

- [ ] **Step 2: Run the migration test and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/db/test_migrations.py::test_guardian_relationships_have_history_identity_and_overlap_guard -q
```

Expected: FAIL because the current primary key is `(guardian_person_id, student_person_id)` and history columns do not exist.

- [ ] **Step 3: Add the Alembic migration**

Create `apps/api/migrations/versions/0003_guardian_relationship_history.py` with this migration shape:

```python
"""Preserve guardian relationship history.

Revision ID: 0003_guardian_relationship_history
Revises: 0002_seed_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_guardian_relationship_history"
down_revision: str | Sequence[str] | None = "0002_seed_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.add_column("guardian_relationships", sa.Column("id", sa.UUID(), nullable=True))
    op.add_column(
        "guardian_relationships",
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("ended_by_user_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("end_reason", sa.String(length=200), nullable=True),
    )
    op.execute("UPDATE guardian_relationships SET id = gen_random_uuid() WHERE id IS NULL")
    op.alter_column("guardian_relationships", "id", nullable=False)
    op.drop_constraint("guardian_relationships_pkey", "guardian_relationships", type_="primary")
    op.create_primary_key("pk_guardian_relationships", "guardian_relationships", ["id"])
    op.create_foreign_key(
        "fk_guardian_relationships_ended_by_user_id",
        "guardian_relationships",
        "user_accounts",
        ["ended_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_guardian_relationships_pair",
        "guardian_relationships",
        ["guardian_person_id", "student_person_id"],
    )
    op.execute(
        """
        ALTER TABLE guardian_relationships
        ADD CONSTRAINT excl_guardian_relationships_overlapping_dates
        EXCLUDE USING gist (
            guardian_person_id WITH =,
            student_person_id WITH =,
            daterange(valid_from, COALESCE(valid_until, 'infinity'::date), '[]') WITH &&
        ) WHERE (ended_at IS NULL)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM guardian_relationships
                GROUP BY guardian_person_id, student_person_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade guardian history with repeated pairs';
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE guardian_relationships "
        "DROP CONSTRAINT excl_guardian_relationships_overlapping_dates"
    )
    op.drop_index("ix_guardian_relationships_pair", table_name="guardian_relationships")
    op.drop_constraint(
        "fk_guardian_relationships_ended_by_user_id",
        "guardian_relationships",
        type_="foreignkey",
    )
    op.drop_constraint("pk_guardian_relationships", "guardian_relationships", type_="primary")
    op.create_primary_key(
        "guardian_relationships_pkey",
        "guardian_relationships",
        ["guardian_person_id", "student_person_id"],
    )
    for column in (
        "end_reason",
        "ended_by_user_id",
        "ended_at",
        "updated_at",
        "created_at",
        "id",
    ):
        op.drop_column("guardian_relationships", column)
```

- [ ] **Step 4: Update the SQLAlchemy model**

Replace `GuardianRelationship` with a `TimestampMixin` model whose identity is the relationship record:

```python
class GuardianRelationship(TimestampMixin, Base):
    __tablename__ = "guardian_relationships"
    __table_args__ = (
        CheckConstraint(
            "guardian_person_id <> student_person_id",
            name="ck_guardian_relationships_distinct_people",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from",
            name="ck_guardian_relationships_valid_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    guardian_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("people.id", ondelete="RESTRICT"), nullable=False
    )
    relationship: Mapped[str] = mapped_column(String(40), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date | None] = mapped_column(Date)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT")
    )
    end_reason: Mapped[str | None] = mapped_column(String(200))
```

Add `DateTime` to the SQLAlchemy imports in `identity/models.py`.

Replace the pair-primary-key lookup in `IdentityService.add_guardian()` with a normal query so the application remains functional immediately after this task:

```python
existing = self.db.scalar(
    select(GuardianRelationship.id).where(
        GuardianRelationship.guardian_person_id == guardian_id,
        GuardianRelationship.student_person_id == student_id,
        GuardianRelationship.ended_at.is_(None),
    )
)
if existing is not None:
    raise GuardianRelationshipAlreadyExists("guardian relationship already exists")
```

In `test_guardians.py`, replace `db.get(GuardianRelationship, (guardian.id, student.id))` with the same pair-column `select()` pattern. This is a compatibility change only; relationship ending and rebinding arrive in Task 3.

- [ ] **Step 5: Run migration and model tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/db/test_migrations.py apps/api/tests/api/test_guardians.py -q
```

Expected: PASS, including the new primary key, foreign key, exclusion constraint and pair-query compatibility assertions.

- [ ] **Step 6: Commit the schema unit**

```bash
git add apps/api/migrations/versions/0003_guardian_relationship_history.py apps/api/app/identity/models.py apps/api/app/identity/service.py apps/api/tests/db/test_migrations.py apps/api/tests/api/test_guardians.py
git commit -m "feat: preserve guardian relationship history"
```

### Task 2: Build the single guardian-readiness policy

**Files:**

- Create: `apps/api/app/identity/readiness.py`
- Create: `apps/api/tests/services/test_readiness_policy.py`
- Modify: `apps/api/app/identity/service.py:49-58,661-713`
- Modify: `apps/api/tests/unit/identity/test_guardians.py`
- Modify: `apps/api/tests/api/conftest.py:27-151`

- [ ] **Step 1: Write the failing policy matrix tests**

Create a local `ReadinessFixture` in `test_readiness_policy.py`; it must create real people, accounts and role rows so the policy is never tested with mocks. Use this public helper shape in the test file:

```python
@dataclass
class ReadinessFixture:
    db: Session
    identity: IdentityService
    accounts: AccountService
    minor_student: PersonSummary
    adult_student: PersonSummary
    sequence: int = 200

    def link_guardian_for(
        self,
        student: PersonSummary,
        *,
        birth_date: date,
        account_status: str | None,
        role_key: str | None,
        deny_people_read: bool = False,
    ) -> GuardianRelationship:
        self.sequence += 1
        guardian = self.identity.create_guardian(
            GuardianCreate(
                full_name=f"判定监护人 {self.sequence}",
                identity_number=make_cn_id(birth_date.strftime("%Y%m%d"), self.sequence),
                sex="female" if self.sequence % 2 == 0 else "male",
                date_of_birth=birth_date,
                phone=f"139{self.sequence:08d}",
                email=f"guardian-{self.sequence}@example.com",
            )
        )
        self.identity.add_guardian(
            student.id,
            guardian.id,
            relationship="mother",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            valid_until=None,
        )
        if account_status is not None:
            created = self.accounts.create_account(AccountCreate(person_id=guardian.id))
            account = self.db.get(UserAccount, created.account.id)
            assert account is not None
            account.status = account_status
            if role_key is not None:
                self.db.add(UserRole(user_id=account.id, role_id=ROLES[role_key].id))
            if deny_people_read:
                self.db.add(
                    UserPermissionOverride(
                        user_id=account.id,
                        permission_id=PERMISSIONS["people.read"].id,
                        effect="deny",
                        scope_kind="family",
                        scope_id=None,
                    )
                )
            self.db.commit()
        relationship = self.db.scalar(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == guardian.id,
                GuardianRelationship.student_person_id == student.id,
                GuardianRelationship.ended_at.is_(None),
            )
        )
        assert relationship is not None
        return relationship

    def link_guardian(self, **kwargs: object) -> GuardianRelationship:
        return self.link_guardian_for(self.minor_student, **kwargs)  # type: ignore[arg-type]
```

Define the fixture immediately below the helper:

```python
@pytest.fixture
def readiness_fixture(db: Session) -> ReadinessFixture:
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    accounts = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 27),
    )
    minor = identity.create_student(
        StudentCreate(
            full_name="待判定未成年学员",
            identity_number=make_cn_id("20120102", 190),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            student_number="READY-MINOR-001",
        )
    )
    adult = identity.create_student(
        StudentCreate(
            full_name="待判定成年学员",
            identity_number=make_cn_id("19900102", 191),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            student_number="READY-ADULT-001",
        )
    )
    return ReadinessFixture(db, identity, accounts, minor, adult)
```

Then add tests with fixed `reference_date=date(2026, 7, 27)` for these exact cases:

```python
def test_minor_requires_an_adult_guardian_with_active_account_and_role(
    db: Session,
    readiness_fixture,
) -> None:
    student = readiness_fixture.minor_student
    policy = GuardianReadinessPolicy(db)

    assert policy.evaluate_one(student.id, date(2026, 7, 27)).guardian_status == "pending"

    relationship = readiness_fixture.link_guardian(
        birth_date=date(2010, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    result = policy.evaluate_one(student.id, date(2026, 7, 27), include_issues=True)

    assert result.guardian_status == "pending"
    assert result.issues[0].relationship_id == relationship.id
    assert result.issues[0].reason_codes == ("guardian_underage",)


def test_permission_override_does_not_change_guardian_readiness(
    db: Session,
    readiness_fixture,
) -> None:
    readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
        deny_people_read=True,
    )

    result = GuardianReadinessPolicy(db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 7, 27),
    )

    assert result.guardian_status == "ready"
    assert result.missing == ()


def test_adult_student_does_not_require_but_can_keep_guardian_relationship(
    db: Session,
    readiness_fixture,
) -> None:
    relationship = readiness_fixture.link_guardian_for(
        readiness_fixture.adult_student,
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )

    result = GuardianReadinessPolicy(db).evaluate_one(
        readiness_fixture.adult_student.id,
        date(2026, 7, 27),
        include_issues=True,
    )

    assert result.age_status == "adult"
    assert result.guardian_status == "not_applicable"
    assert result.active_relationship_ids == (relationship.id,)
```

Add the remaining matrix as parameterized tests:

```python
@pytest.mark.parametrize(
    ("account_status", "role_key", "reason"),
    [
        (None, None, "guardian_account_missing"),
        ("disabled", "guardian", "guardian_account_disabled"),
        ("active", None, "guardian_role_missing"),
    ],
)
def test_guardian_account_matrix(
    readiness_fixture: ReadinessFixture,
    account_status: str | None,
    role_key: str | None,
    reason: GuardianIssueCode,
) -> None:
    readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status=account_status,
        role_key=role_key,
    )
    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 7, 27),
        include_issues=True,
    )
    assert result.guardian_status == "pending"
    assert reason in result.issues[0].reason_codes


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("future", "relationship_not_started"),
        ("expired", "relationship_expired"),
        ("ended", "relationship_ended"),
    ],
)
def test_guardian_relationship_date_matrix(
    readiness_fixture: ReadinessFixture,
    state: str,
    reason: GuardianIssueCode,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    if state == "future":
        relationship.valid_from = date(2026, 7, 28)
    elif state == "expired":
        relationship.valid_until = date(2026, 7, 26)
    else:
        relationship.ended_at = datetime(2026, 7, 27, tzinfo=UTC)
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 7, 27),
        include_issues=True,
    )
    assert result.guardian_status == "pending"
    assert reason in result.issues[0].reason_codes


def test_incomplete_guardian_profile_is_not_valid(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    guardian = readiness_fixture.db.get(Person, relationship.guardian_person_id)
    assert guardian is not None
    guardian.email = None
    readiness_fixture.db.commit()

    result = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 7, 27),
        include_issues=True,
    )
    assert result.issues[0].reason_codes == ("guardian_profile_incomplete",)


def test_reference_date_can_be_the_future_planned_checkin_date(
    readiness_fixture: ReadinessFixture,
) -> None:
    relationship = readiness_fixture.link_guardian(
        birth_date=date(1980, 1, 2),
        account_status="active",
        role_key="guardian",
    )
    relationship.valid_until = date(2026, 8, 1)
    readiness_fixture.db.commit()

    before_checkout = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 8, 1),
    )
    after_checkout = GuardianReadinessPolicy(readiness_fixture.db).evaluate_one(
        readiness_fixture.minor_student.id,
        date(2026, 8, 2),
    )

    assert before_checkout.guardian_status == "ready"
    assert after_checkout.guardian_status == "pending"
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_readiness_policy.py apps/api/tests/unit/identity/test_guardians.py -q
```

Expected: FAIL because `GuardianReadinessPolicy` and the structured reason codes do not exist.

- [ ] **Step 3: Create the policy types and batch evaluator**

Create `apps/api/app/identity/readiness.py` with these public contracts and implement `evaluate_many` with one relationship query, one account query and one guardian-role query for the whole page:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Role, UserRole

AgeStatus = Literal["adult", "minor"]
GuardianStatus = Literal["ready", "pending", "not_applicable"]
GuardianIssueCode = Literal[
    "guardian_underage",
    "guardian_profile_incomplete",
    "guardian_account_missing",
    "guardian_account_disabled",
    "guardian_role_missing",
    "relationship_not_started",
    "relationship_expired",
    "relationship_ended",
]


def is_minor(birth_date: date, reference_date: date) -> bool:
    try:
        eighteenth = birth_date.replace(year=birth_date.year + 18)
    except ValueError:
        eighteenth = date(birth_date.year + 18, 2, 28)
    return reference_date < eighteenth


@dataclass(frozen=True)
class GuardianIssue:
    relationship_id: UUID
    guardian_person_id: UUID
    account_id: UUID | None
    reason_codes: tuple[GuardianIssueCode, ...]


@dataclass(frozen=True)
class PersonReadinessProjection:
    age_status: AgeStatus
    guardian_status: GuardianStatus
    missing: tuple[str, ...]
    issues: tuple[GuardianIssue, ...] = ()
    active_relationship_ids: tuple[UUID, ...] = ()


class GuardianReadinessPolicy:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _profile_complete(person: Person) -> bool:
        return bool(
            person.full_name.strip()
            and person.identity_ciphertext
            and person.identity_digest
            and person.phone_ciphertext
            and person.phone_digest
            and person.email
        )

    def evaluate_one(
        self,
        person_id: UUID,
        reference_date: date,
        *,
        include_issues: bool = False,
    ) -> PersonReadinessProjection:
        person = self.db.get(Person, person_id)
        if person is None or person.deleted_at is not None:
            raise ValueError("person not found")
        is_student = self.db.get(StudentProfile, person_id) is not None
        return self.evaluate_many(
            [person],
            student_ids={person_id} if is_student else set(),
            reference_date=reference_date,
            include_issues=include_issues,
        )[person_id]

    def evaluate_many(
        self,
        people: list[Person],
        *,
        student_ids: set[UUID],
        reference_date: date,
        include_issues: bool = False,
    ) -> dict[UUID, PersonReadinessProjection]:
        ids = {person.id for person in people}
        relationship_rows = list(
            self.db.execute(
                select(GuardianRelationship, Person)
                .join(Person, Person.id == GuardianRelationship.guardian_person_id)
                .where(GuardianRelationship.student_person_id.in_(ids & student_ids))
            ).all()
        )
        guardian_ids = {person.id for _, person in relationship_rows}
        accounts = {
            account.person_id: account
            for account in self.db.scalars(
                select(UserAccount).where(UserAccount.person_id.in_(guardian_ids))
            )
        }
        guardian_role_users = set(
            self.db.scalars(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "guardian", UserRole.user_id.in_({a.id for a in accounts.values()}))
            )
        )
        relations_by_student: dict[UUID, list[tuple[GuardianRelationship, Person]]] = {}
        for relationship, guardian in relationship_rows:
            relations_by_student.setdefault(relationship.student_person_id, []).append(
                (relationship, guardian)
            )

        output: dict[UUID, PersonReadinessProjection] = {}
        for person in people:
            age_status: AgeStatus = "minor" if is_minor(person.date_of_birth, reference_date) else "adult"
            if person.id not in student_ids or age_status == "adult":
                active_ids = tuple(
                    relationship.id
                    for relationship, _ in relations_by_student.get(person.id, [])
                    if relationship.ended_at is None
                    and relationship.valid_from <= reference_date
                    and (relationship.valid_until is None or relationship.valid_until >= reference_date)
                )
                output[person.id] = PersonReadinessProjection(
                    age_status=age_status,
                    guardian_status="not_applicable",
                    missing=(),
                    active_relationship_ids=active_ids,
                )
                continue

            issues: list[GuardianIssue] = []
            valid_ids: list[UUID] = []
            for relationship, guardian in relations_by_student.get(person.id, []):
                account = accounts.get(guardian.id)
                reasons: list[GuardianIssueCode] = []
                if relationship.ended_at is not None:
                    reasons.append("relationship_ended")
                elif relationship.valid_from > reference_date:
                    reasons.append("relationship_not_started")
                elif relationship.valid_until is not None and relationship.valid_until < reference_date:
                    reasons.append("relationship_expired")
                if is_minor(guardian.date_of_birth, reference_date):
                    reasons.append("guardian_underage")
                if guardian.deleted_at is not None or not self._profile_complete(guardian):
                    reasons.append("guardian_profile_incomplete")
                if account is None:
                    reasons.append("guardian_account_missing")
                elif account.status != "active":
                    reasons.append("guardian_account_disabled")
                elif account.id not in guardian_role_users:
                    reasons.append("guardian_role_missing")
                if reasons:
                    issues.append(
                        GuardianIssue(
                            relationship_id=relationship.id,
                            guardian_person_id=guardian.id,
                            account_id=account.id if account else None,
                            reason_codes=tuple(reasons),
                        )
                    )
                else:
                    valid_ids.append(relationship.id)

            output[person.id] = PersonReadinessProjection(
                age_status="minor",
                guardian_status="ready" if valid_ids else "pending",
                missing=() if valid_ids else ("guardian",),
                issues=tuple(issues) if include_issues else (),
                active_relationship_ids=tuple(valid_ids),
            )
        return output
```

If `guardian_ids` or account IDs are empty, skip the corresponding `IN` query instead of emitting an invalid or noisy empty query. Keep the return types exactly as declared above.

- [ ] **Step 4: Make existing identity readiness delegate to the policy**

In `identity/service.py`, import `GuardianReadinessPolicy` and `is_minor`; remove the local `is_minor` implementation. Replace the guardian SQL inside `readiness()` with:

```python
        projection = GuardianReadinessPolicy(self.db).evaluate_one(
            person_id,
            reference_date,
        )
        if "guardian" in projection.missing:
            missing.append("guardian")
```

Keep account candidate phone/email checks unchanged.

- [ ] **Step 5: Make API fixtures create a truly valid guardian**

In `apps/api/tests/api/conftest.py`, ensure the guardian account and `guardian` role exist before any assertion expects a minor to be ready. Do not add permission overrides to make readiness pass; only the role matters. Preserve the existing family-scope permission granted by the role seed.

Update the `valid_guardian` service fixture in `test_guardians.py` so it creates the account and role before returning the person:

```python
@pytest.fixture
def valid_guardian(identity_service: IdentityService, db: Session):
    guardian = identity_service.create_guardian(
        GuardianCreate(
            full_name="有效监护人",
            identity_number=make_cn_id("19800102", 101),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            phone="13900139001",
            email="guardian@example.com",
        )
    )
    accounts = AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )
    created = accounts.create_account(AccountCreate(person_id=guardian.id))
    db.add(UserRole(user_id=created.account.id, role_id=ROLES["guardian"].id))
    db.commit()
    return guardian
```

- [ ] **Step 6: Run policy and existing guardian tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_readiness_policy.py apps/api/tests/services/test_guardians.py apps/api/tests/unit/identity/test_guardians.py -q
```

Expected: PASS for the full validity matrix, birthday boundaries and “permission override does not change readiness” case.

- [ ] **Step 7: Commit the policy unit**

```bash
git add apps/api/app/identity/readiness.py apps/api/app/identity/service.py apps/api/tests/services/test_readiness_policy.py apps/api/tests/services/test_guardians.py apps/api/tests/unit/identity/test_guardians.py apps/api/tests/api/conftest.py
git commit -m "feat: centralize guardian readiness policy"
```

### Task 3: Replace relationship deletion with audited end/history APIs

**Files:**

- Modify: `apps/api/app/identity/schemas.py:63-88`
- Modify: `apps/api/app/identity/service.py:539-660`
- Modify: `apps/api/app/identity/router.py:100-148,198-202,251-273,409-494`
- Modify: `apps/api/app/audit/service.py:15-30,120-154`
- Modify: `apps/api/tests/services/test_guardians.py`
- Modify: `apps/api/tests/api/test_guardians.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing history, overlap and audit tests**

Add exact assertions for these flows:

```python
def test_ending_and_rebinding_preserves_guardian_history(
    identity_service: IdentityService,
    minor_student,
    valid_guardian,
    db: Session,
) -> None:
    first = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    identity_service.end_guardian_relationship(
        minor_student.id,
        first.relationship_id,
        ended_by_user_id=None,
        reason="监护安排变更",
        ended_at=datetime(2026, 7, 27, 2, tzinfo=UTC),
    )
    second = identity_service.add_guardian(
        minor_student.id,
        valid_guardian.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2026, 7, 28),
        valid_until=None,
    )

    history = identity_service.list_guardians(minor_student.id, reference_date=None)
    assert [item.relationship_id for item in history] == [second.relationship_id, first.relationship_id]
    assert next(item for item in history if item.relationship_id == first.relationship_id).status == "ended"
    assert first.relationship_id != second.relationship_id
```

Add a PostgreSQL-backed test that overlapping non-ended ranges raise `GuardianRelationshipAlreadyExists`, while a new range after an ended record succeeds. Add an API test asserting `guardian.relationship.end` and `guardian.relationship.create` audit events contain only canonical UUIDs and the safe reason code/count metadata.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_audit.py -q
```

Expected: FAIL because relationships still use pair identity and `DELETE` physically removes rows.

- [ ] **Step 3: Extend schemas with stable relationship identity**

Use these schema contracts:

```python
GuardianRelationshipStatus = Literal["scheduled", "active", "expired", "ended"]


class GuardianSummary(PersonSummary):
    relationship_id: UUID
    relationship: str
    is_primary: bool
    valid_from: date
    valid_until: date | None
    status: GuardianRelationshipStatus
    ended_at: datetime | None
    end_reason: str | None


class GuardianEndRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str = Field(min_length=2, max_length=200)
```

- [ ] **Step 4: Implement add/list/end semantics**

Update `IdentityService` so:

- new relationships receive a new UUID;
- a proposed guardian must be at least 18 on `valid_from`, otherwise raise a new `GuardianUnderage` error;
- `list_guardians(..., reference_date=today)` filters `ended_at IS NULL` and the date interval;
- `list_guardians(..., reference_date=None)` returns all history newest first;
- `end_guardian_relationship()` locks the relationship row, verifies it belongs to the student, writes `ended_at`, `ended_by_user_id`, `end_reason`, and never deletes it;
- the PostgreSQL exclusion constraint maps to `GuardianRelationshipAlreadyExists` with code `guardian_relationship_overlaps`.

The status function must be deterministic:

```python
def relationship_status(
    relationship: GuardianRelationship,
    reference_date: date,
) -> GuardianRelationshipStatus:
    if relationship.ended_at is not None:
        return "ended"
    if relationship.valid_from > reference_date:
        return "scheduled"
    if relationship.valid_until is not None and relationship.valid_until < reference_date:
        return "expired"
    return "active"
```

- [ ] **Step 5: Replace the delete route with an explicit end route**

Expose:

```python
@router.post(
    "/people/{student_id}/guardians/{relationship_id}/end",
    response_model=GuardianResponse,
)
def end_guardian_relationship(
    student_id: UUID,
    relationship_id: UUID,
    input: GuardianEndRequest,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[IdentityService, Depends(get_identity_service)],
) -> GuardianResponse:
    _require_permission(context, db, "people.update", _person_tokens(context, db, student_id))
    db.commit()
    with db.begin():
        result = service.end_guardian_relationship(
            student_id,
            relationship_id,
            ended_by_user_id=context.account.id,
            reason=input.reason,
            ended_at=datetime.now(UTC),
        )
        AuditService(db).append(
            actor_user_id=context.account.id,
            action="guardian.relationship.end",
            resource_type="guardian_relationship",
            resource_id=relationship_id,
            purpose=None,
            outcome="allowed",
            request_id=str(request.state.request_id),
            metadata={"student_person_id": str(student_id)},
        )
    return _guardian_response(result)
```

Add the matching create audit in the existing POST route. Extend `AuditService` with UUID-only metadata keys `student_person_id`, `guardian_person_id`, `relationship_id` and non-negative `affected_account_count`; never allow names, phone numbers, identity numbers or free-text end reasons into metadata.

- [ ] **Step 6: Run relationship and audit tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_audit.py -q
```

Expected: PASS; history remains queryable, rebind uses a new ID, overlap is rejected, and audit output is PII-free.

- [ ] **Step 7: Commit the lifecycle API**

```bash
git add apps/api/app/identity/schemas.py apps/api/app/identity/service.py apps/api/app/identity/router.py apps/api/app/audit/service.py apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_audit.py
git commit -m "feat: retain audited guardian relationship history"
```

### Task 4: Add server-derived roster and administrator diagnostic fields

**Files:**

- Modify: `apps/api/app/identity/schemas.py:63-88`
- Modify: `apps/api/app/identity/service.py:340-454,661-713`
- Modify: `apps/api/app/identity/router.py:100-148,177-202,332-382,497-510`
- Modify: `apps/api/app/rbac/service.py:262-293`
- Modify: `apps/api/tests/services/test_people.py`
- Modify: `apps/api/tests/api/test_people.py`
- Modify: `apps/api/tests/api/test_guardians.py`

- [ ] **Step 1: Write failing roster projection and privacy tests**

Add API assertions equivalent to:

```python
def test_create_minor_without_guardian_returns_pending_draft(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.post(
        "/api/v1/people/students",
        headers={"X-CSRF-Token": csrf},
        json={
            "full_name": "待绑定新学员",
            "identity_number": make_cn_id("20140102", 171),
            "sex": "female",
            "date_of_birth": "2014-01-02",
            "student_number": "PENDING-NEW-001",
        },
    )

    assert response.status_code == 201
    assert response.json()["age_status"] == "minor"
    assert response.json()["guardian_status"] == "pending"


def test_people_list_exposes_age_and_guardian_status(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")
    response = db_client.get("/api/v1/people", params={"type": "student"})

    assert response.status_code == 200
    rows = {item["full_name"]: item for item in response.json()["items"]}
    assert rows["已绑定学员"]["age_status"] == "minor"
    assert rows["已绑定学员"]["guardian_status"] == "ready"
    assert rows["其他学员"]["guardian_status"] == "pending"
    assert rows["待开通账号人员"]["age_status"] == "adult"
    assert rows["待开通账号人员"]["guardian_status"] == "not_applicable"


def test_only_admin_receives_guardian_issue_diagnostics(
    db_client: TestClient,
    platform_users,
) -> None:
    student_id = platform_users["people"]["other_student"].id
    sign_in(db_client, platform_users, "admin")
    admin = db_client.get(
        f"/api/v1/people/{student_id}/readiness",
        params={"reference_date": "2026-07-27"},
    )
    assert admin.json()["guardian_issues"] == []

    db_client.cookies.clear()
    sign_in(db_client, platform_users, "customer_service")
    support = db_client.get(
        f"/api/v1/people/{student_id}/readiness",
        params={"reference_date": "2026-07-27"},
    )
    assert "guardian_issues" not in support.json()
```

Also add a service test that lists 100 people and uses SQLAlchemy query instrumentation to assert the number of readiness queries stays constant rather than growing per person.

```python
def test_people_readiness_projection_has_constant_query_count(
    identity_service: IdentityService,
    db: Session,
) -> None:
    for index in range(100):
        identity_service.create_student(
            StudentCreate(
                full_name=f"批量学员 {index:03d}",
                identity_number=make_cn_id("20120102", 300 + index),
                sex="female" if index % 2 == 0 else "male",
                date_of_birth=date(2012, 1, 2),
                student_number=f"BATCH-{index:03d}",
            )
        )

    def count_queries(page_size: int) -> int:
        count = 0

        def before_cursor_execute(*_: object) -> None:
            nonlocal count
            count += 1

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", before_cursor_execute)
        try:
            identity_service.search_people(
                phone=None,
                name="批量学员",
                person_type="student",
                page=1,
                page_size=page_size,
            )
        finally:
            event.remove(bind, "before_cursor_execute", before_cursor_execute)
        return count

    assert count_queries(100) == count_queries(1)
```


- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_people.py apps/api/tests/api/test_people.py apps/api/tests/api/test_guardians.py -q
```

Expected: FAIL because `age_status`, `guardian_status` and administrator diagnostics are absent.

- [ ] **Step 3: Extend response models**

Add the exact fields:

```python
class PersonSummary(BaseModel):
    id: UUID
    full_name: str
    person_types: list[PersonType]
    masked_identity: str
    masked_phone: str | None
    email: EmailStr | None
    sex: Sex
    date_of_birth: date
    created_at: datetime
    age_status: AgeStatus
    guardian_status: GuardianStatus


class GuardianIssueSummary(BaseModel):
    relationship_id: UUID
    guardian_person_id: UUID
    account_id: UUID | None
    reason_codes: list[GuardianIssueCode]


class ReadinessResponse(BaseModel):
    ready: bool
    missing: list[str]
    age_status: AgeStatus
    guardian_status: GuardianStatus
    guardian_issues: list[GuardianIssueSummary] | None = None
```

Configure the router response with `response_model_exclude_none=True` so non-admin responses omit `guardian_issues` entirely.

- [ ] **Step 4: Add an explicit role check for diagnostics**

Add to `RbacService`:

```python
def account_has_role(self, account_id: UUID, role_key: str) -> bool:
    return self.db.scalar(
        select(UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == account_id, Role.key == role_key)
        .limit(1)
    ) is not None
```

The readiness route sets `include_issues=True` only when `account_has_role(..., "admin")` is true. Custom permissions alone must not expose internal account/role diagnostics.

- [ ] **Step 5: Project statuses in one batch**

In `search_people()`, after loading the page and person types, call `GuardianReadinessPolicy.evaluate_many()` once with `reference_date=business_today()`. Pass each projection into `_summary()`; do the same for create/get/update responses with a single-person evaluation. Do not calculate age in React.

Extend the service result so the router does not perform a second independent calculation:

```python
@dataclass(frozen=True)
class IdentityReadiness:
    ready: bool
    missing: list[str]
    age_status: AgeStatus
    guardian_status: GuardianStatus
    guardian_issues: tuple[GuardianIssue, ...] = ()
```

`IdentityService.readiness(..., include_guardian_issues: bool = False)` obtains one policy projection, combines its `missing` values with contact-field checks, and returns the projection fields above. The router maps those exact fields into `ReadinessResponse` and only requests issues for an administrator.

Update family relationship queries in both `identity/router.py` and `rbac/service.py` to require `GuardianRelationship.ended_at.is_(None)` while retaining adult students in family scope.

- [ ] **Step 6: Run API projection tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_people.py apps/api/tests/api/test_people.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_permissions.py -q
```

Expected: PASS, including fixed-query-count, adult family scope and diagnostic privacy cases.

- [ ] **Step 7: Commit the API projection**

```bash
git add apps/api/app/identity/schemas.py apps/api/app/identity/service.py apps/api/app/identity/router.py apps/api/app/rbac/service.py apps/api/tests/services/test_people.py apps/api/tests/api/test_people.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_permissions.py
git commit -m "feat: expose guardian readiness on people records"
```

### Task 5: Enforce readiness on account opening, enabling, login and sessions

**Files:**

- Modify: `apps/api/app/core/time.py`
- Modify: `apps/api/app/identity/readiness.py`
- Modify: `apps/api/app/auth/service.py:189-333,431-571`
- Modify: `apps/api/app/auth/dependencies.py:36-68`
- Modify: `apps/api/app/auth/router.py:51-85`
- Modify: `apps/api/app/auth/admin_router.py:124-141,281-304`
- Modify: `apps/api/tests/api/conftest.py:27-151`
- Modify: `apps/api/tests/services/test_accounts.py`
- Modify: `apps/api/tests/api/test_accounts.py`
- Modify: `apps/api/tests/api/test_auth_sessions.py`

- [ ] **Step 1: Write failing enable, login and live-session tests**

Extend `apps/api/tests/api/conftest.py` with a contact-complete `minor_student`, an active relationship to the existing guardian, and credentials using the `student` role. Preserve dictionary insertion order so the guardian account and guardian role are committed before `AccountService.create_account()` runs for `minor_student`.

Add this shared fixture to `apps/api/tests/api/conftest.py` so both account and session API tests can use it:

```python
@dataclass(frozen=True)
class MinorLoginScenario:
    account_id: UUID
    phone: str
    password: str
    relationship: GuardianRelationship

    def login(self, client: TestClient) -> Response:
        return client.post(
            "/api/v1/auth/login",
            json={"phone": self.phone, "password": self.password},
        )


@pytest.fixture
def minor_login(platform_users, db: Session) -> MinorLoginScenario:
    account = platform_users["accounts"]["minor_student"]
    phone, password, _ = platform_users["credentials"]["minor_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id
            == platform_users["people"]["minor_student"].id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    return MinorLoginScenario(account.id, phone, password, relationship)
```

Import `Response` from `httpx`, plus `GuardianRelationship`, `select`, `UUID` and `TestClient` in that conftest. Then add these behavioral tests with an already-active minor account whose last guardian expires:

```python
# apps/api/tests/api/test_accounts.py
def test_enable_rejects_pending_minor(
    db_client: TestClient,
    platform_users,
    minor_login: MinorLoginScenario,
    db: Session,
) -> None:
    account = platform_users["accounts"]["minor_student"]
    account.status = "disabled"
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/accounts/{account.id}/enable",
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "guardian", "message": "required"}]
    assert account.status == "disabled"


# apps/api/tests/api/test_auth_sessions.py
def test_expired_guardian_disables_login_without_revealing_reason(
    db_client: TestClient,
    minor_login,
    db: Session,
) -> None:
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()

    response = db_client.post(
        "/api/v1/auth/login",
        json={"phone": minor_login.phone, "password": minor_login.password},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"
    assert db.get(UserAccount, minor_login.account_id).status == "disabled"


def test_expired_guardian_revokes_existing_session_before_returning_data(
    db_client: TestClient,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    minor_login.relationship.valid_until = date(2026, 7, 26)
    db.commit()

    response = db_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert db.scalar(
        select(AuthSession.revoked_at).where(AuthSession.user_id == minor_login.account_id)
    ) is not None
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_accounts.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_auth_sessions.py -q
```

Expected: FAIL because `enable_account()` does not check readiness and authentication only checks stored `active` status.

- [ ] **Step 3: Add deterministic Shanghai date conversion**

Update `core/time.py`:

```python
def business_date_at(value: datetime) -> date:
    if value.tzinfo is None:
        raise ValueError("business datetime must be timezone-aware")
    return value.astimezone(BUSINESS_TIME_ZONE).date()


def business_today() -> date:
    return business_date_at(datetime.now(BUSINESS_TIME_ZONE))
```

- [ ] **Step 4: Add account disabling primitive to the policy**

Implement a method that locks the student account, sets `status="disabled"`, and revokes every open session only when the student is currently minor and `guardian_status="pending"`:

```python
def disable_student_account_if_pending(
    self,
    student_person_id: UUID,
    *,
    reference_date: date,
    revoked_at: datetime,
    audit_actor_user_id: UUID | None,
    audit_request_id: str,
) -> UUID | None:
    projection = self.evaluate_one(student_person_id, reference_date)
    if projection.guardian_status != "pending":
        return None
    account = self.db.scalar(
        select(UserAccount)
        .where(UserAccount.person_id == student_person_id)
        .with_for_update()
    )
    if account is None:
        return None
    account.status = "disabled"
    self.db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=revoked_at)
    )
    AuditService(self.db).append(
        actor_user_id=audit_actor_user_id,
        action="account.auto_disable.guardian_missing",
        resource_type="account",
        resource_id=account.id,
        purpose=None,
        outcome="allowed",
        request_id=audit_request_id,
        metadata={"error_code": "minor_guardian_required"},
    )
    return account.id
```

- [ ] **Step 5: Guard account opening and enabling**

Keep the existing `create_account()` readiness call. Change `enable_account()` to evaluate the account’s person with `reference_date_provider()` before writing active status, raising `PersonNotReady(["guardian"])` when pending. Preserve the existing structured 422 response from `_raise_account_error()`.

In `auth/admin_router.py`, handle `PersonNotReady` separately for create and enable: roll back the failed business transaction, append a committed audit event named `account.create.guardian_missing` or `account.enable.guardian_missing` with `outcome="denied"`, the target person/account ID, and `error_code="minor_guardian_required"`, then return the existing 422 problem. Add API assertions that both denied events exist and contain no PII.

- [ ] **Step 6: Guard both authentication entry points**

Add `request_id: str` to `AuthService.login()` and `authenticate()`. Before creating a session or returning an existing context:

1. derive the Shanghai date from the injected UTC clock;
2. call `disable_student_account_if_pending()` inside a committed write transaction and pass the account actor plus request ID;
3. let the policy append `account.auto_disable.guardian_missing` with `error_code="minor_guardian_required"` and no PII;
4. append `auth.login.guardian_missing` or `auth.session.guardian_missing` with `outcome="denied"` and the same safe error code;
5. return the existing generic `invalid_credentials` or `invalid_session` error.

Add `minor_guardian_required` to the exact `_PUBLIC_ERROR_CODES` allowlist in `audit/service.py`. Update `auth/router.py` and `auth/dependencies.py` to pass `str(request.state.request_id)`. Do not expose `guardian` in the login error body.

Update every direct `AuthService.login()` call in `test_auth_sessions.py` to pass a stable value such as `request_id="auth-service-test"`; there must be no optional fallback that silently loses blocked-login audit attribution.

- [ ] **Step 7: Run account and auth tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_accounts.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_auth_sessions.py apps/api/tests/api/test_audit.py -q
```

Expected: PASS; pending minors cannot be opened/enabled, expired guardians invalidate login and live sessions, and authentication errors remain indistinguishable.

- [ ] **Step 8: Commit authentication enforcement**

```bash
git add apps/api/app/core/time.py apps/api/app/identity/readiness.py apps/api/app/auth/service.py apps/api/app/auth/dependencies.py apps/api/app/auth/router.py apps/api/app/auth/admin_router.py apps/api/tests/api/conftest.py apps/api/tests/services/test_accounts.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_auth_sessions.py apps/api/tests/api/test_audit.py
git commit -m "feat: enforce guardian readiness on student accounts"
```

### Task 6: Cascade synchronous guardian invalidations and audit them

**Files:**

- Modify: `apps/api/app/identity/readiness.py`
- Modify: `apps/api/app/identity/service.py:456-520,651-660`
- Modify: `apps/api/app/identity/router.py:385-494`
- Modify: `apps/api/app/auth/service.py:550-567`
- Modify: `apps/api/app/auth/admin_router.py:255-278`
- Modify: `apps/api/app/rbac/service.py:349-372`
- Modify: `apps/api/app/rbac/router.py:266-295`
- Modify: `apps/api/tests/services/test_guardians.py`
- Modify: `apps/api/tests/api/test_guardians.py`
- Modify: `apps/api/tests/api/test_accounts.py`
- Modify: `apps/api/tests/api/test_permissions.py`
- Modify: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1: Write failing cascade tests for every approved trigger**

Add this assertion helper to each affected API test module:

```python
def assert_minor_account_was_disabled(
    db: Session,
    account_id: UUID,
) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.status == "disabled"
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0
```

Use the shared `minor_login`/`platform_users` data and add these exact named cases:

| Test | Mutation through public API | Required assertion |
| --- | --- | --- |
| `test_ending_last_relationship_disables_minor` | `POST /people/{student}/guardians/{relationship}/end` with `{"reason":"监护安排结束"}` | helper passes; end audit exists |
| `test_disabling_last_guardian_account_disables_minor` | `POST /accounts/{guardian_account}/disable` | helper passes; both account audits exist |
| `test_removing_guardian_role_disables_minor` | `DELETE /accounts/{guardian_account}/roles/{guardian_role}` | helper passes; role-removal audit exists |
| `test_clearing_guardian_email_disables_minor` | `PATCH /people/{guardian}` with `{"email":null}` | helper passes; no email appears in audit |
| `test_correcting_guardian_birth_date_to_minor_disables_minor` | PATCH matching身份证、出生日期和性别为未成年资料 | helper passes; no身份证 appears in response or audit |

Before each mutation, call `minor_login.login(db_client)` and assert 200 so a live session exists. Add separate control tests proving that a granular family permission override does **not** change readiness, and invalidating one of two valid guardians does not disable the student account.

- [ ] **Step 2: Run cascade tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_permissions.py -q
```

Expected: FAIL because management mutations do not currently inspect dependent minors.

- [ ] **Step 3: Add one cascade method**

Implement one policy method and call it from every trigger; do not duplicate relationship queries in services:

```python
def disable_unready_dependents(
    self,
    guardian_person_id: UUID,
    *,
    reference_date: date,
    revoked_at: datetime,
    audit_actor_user_id: UUID | None,
    audit_request_id: str,
) -> tuple[UUID, ...]:
    student_ids = set(
        self.db.scalars(
            select(GuardianRelationship.student_person_id).where(
                GuardianRelationship.guardian_person_id == guardian_person_id,
            )
        )
    )
    disabled: list[UUID] = []
    for student_id in student_ids:
        account_id = self.disable_student_account_if_pending(
            student_id,
            reference_date=reference_date,
            revoked_at=revoked_at,
            audit_actor_user_id=audit_actor_user_id,
            audit_request_id=audit_request_id,
        )
        if account_id is not None:
            disabled.append(account_id)
    return tuple(disabled)
```

Call it after relationship end, guardian profile update, guardian account disable and guardian-role removal, while the same outer transaction is still open. Do not call it for permission override add/remove.

- [ ] **Step 4: Carry audit context through management mutations**

Each router passes `actor_user_id` and `request_id` into its service method, and the service forwards both to the single policy cascade. The policy code in Task 5 appends one safe event per automatic disable, so relationship, account and RBAC services do not duplicate audit construction. Use this call shape in all four services:

```python
GuardianReadinessPolicy(self.db).disable_unready_dependents(
    guardian_person_id,
    reference_date=business_today(),
    revoked_at=datetime.now(UTC),
    audit_actor_user_id=actor_user_id,
    audit_request_id=request_id,
)
```

Use these exact signature additions so later calls stay consistent: `update_person()` receives required keyword-only `actor_user_id: UUID | None` and `request_id: str`; `end_guardian_relationship()` receives required `request_id: str` alongside its existing end actor, reason and timestamp; `disable_account()` receives required keyword-only `actor_user_id: UUID | None` and `request_id: str`; `remove_role()` receives required keyword-only `request_id: str` alongside its existing actor, account and role IDs.

Update direct service tests to pass stable request IDs such as `service-test`; API routers always pass the real request ID.

Keep existing business return types (`PersonDetail`, `GuardianSummary`, `None`, and `Role`) unchanged. The relationship, account or role mutation, child account/session updates and policy-generated audit events must commit atomically. If audit metadata validation fails, roll back the whole operation.

- [ ] **Step 5: Run cascade and audit tests**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py -q
```

Expected: PASS for all five invalidation triggers, two-guardian control, permission-override control, session revocation and audit atomicity.

- [ ] **Step 6: Commit lifecycle cascades**

```bash
git add apps/api/app/identity/readiness.py apps/api/app/identity/service.py apps/api/app/identity/router.py apps/api/app/auth/service.py apps/api/app/auth/admin_router.py apps/api/app/rbac/service.py apps/api/app/rbac/router.py apps/api/tests/services/test_guardians.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py
git commit -m "feat: disable minors when guardians become invalid"
```

### Task 7: Show adult/minor and guardian readiness in the responsive roster

**Files:**

- Modify: `apps/web/src/people/api.ts:11-30`
- Modify: `apps/web/src/people/PeoplePage.tsx:151-189`
- Modify: `apps/web/src/people/PeoplePage.test.tsx`
- Modify: `apps/web/src/people/people.css:147-224,760-864`

- [ ] **Step 1: Write the failing desktop/mobile-friendly component test**

Update the list fixture with:

```typescript
age_status: "minor",
guardian_status: "pending",
```

Then assert:

```typescript
expect(await screen.findByText("未成年")).toBeVisible()
expect(screen.getByRole("link", { name: "待绑定监护人" })).toHaveAttribute(
  "href",
  `/people/${person.id}`,
)
expect(screen.getByText("2012-01-02")).toBeVisible()
```

Add a second adult row asserting `成年` is shown and no guardian warning appears.

- [ ] **Step 2: Run the Web test and verify RED**

Run:

```bash
npm --prefix apps/web test -- PeoplePage.test.tsx
```

Expected: FAIL because the API type and roster badges do not exist.

- [ ] **Step 3: Extend the TypeScript contract**

Add:

```typescript
export type AgeStatus = "adult" | "minor"
export type GuardianStatus = "ready" | "pending" | "not_applicable"

export type PersonSummary = {
  id: string
  full_name: string
  person_types: PersonType[]
  identity_number: string
  phone: string | null
  email: string | null
  sex: "male" | "female"
  date_of_birth: string
  created_at: string
  age_status: AgeStatus
  guardian_status: GuardianStatus
}
```

- [ ] **Step 4: Render server-provided status badges**

Replace the birth-date cell content with:

```tsx
<div className="peopleAgeCell" role="cell" data-label="出生日期 / 成年状态">
  <span>{person.date_of_birth}</span>
  <span className={`ageBadge ${person.age_status}`}>
    {person.age_status === "adult" ? "成年" : "未成年"}
  </span>
  {person.guardian_status === "pending" ? (
    <Link className="guardianPendingBadge" to={`/people/${person.id}`}>
      待绑定监护人
    </Link>
  ) : person.guardian_status === "ready" ? (
    <span className="guardianReadyBadge">监护人已绑定</span>
  ) : null}
</div>
```

Change the column header and `data-label` to `出生日期 / 成年状态`.

- [ ] **Step 5: Add red pending styling without breaking responsive cards**

Add compact pill styles using existing color tokens. The pending link must use `--color-danger-700` and `--color-danger-soft`; ready uses success tokens; age badges remain neutral. At `max-width: 1180px`, keep this column visible because it is now operational status. If space is tight, hide the identity-number column instead. At `max-width: 767px`, keep all three status lines inside the existing card and verify no `white-space: nowrap` forces overflow.

```css
.peopleAgeCell {
  align-content: center;
  justify-items: start;
}

.ageBadge,
.guardianPendingBadge,
.guardianReadyBadge {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 9px;
  font-weight: 750;
  line-height: 1.35;
}

.ageBadge {
  color: var(--color-text-muted);
  background: var(--color-canvas);
}

.guardianPendingBadge {
  color: var(--color-danger-700);
  background: var(--color-danger-soft);
  text-decoration: none;
}

.guardianReadyBadge {
  color: var(--color-success-900);
  background: var(--color-success-soft);
}

@media (max-width: 1180px) {
  .peopleTableHeader > :nth-child(3),
  .peopleTableRow > :nth-child(3) {
    display: none;
  }

  .peopleTableHeader > :nth-child(4),
  .peopleTableRow > :nth-child(4) {
    display: grid;
  }
}

@media (max-width: 767px) {
  .peopleAgeCell {
    min-width: 0;
  }

  .guardianPendingBadge,
  .guardianReadyBadge {
    max-width: 100%;
    white-space: normal;
  }
}
```

- [ ] **Step 6: Run Web unit, build and lint**

Run:

```bash
npm --prefix apps/web test -- PeoplePage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all commands PASS; TypeScript accepts the new contract and no responsive CSS lint errors appear.

- [ ] **Step 7: Commit roster status UI**

```bash
git add apps/web/src/people/api.ts apps/web/src/people/PeoplePage.tsx apps/web/src/people/PeoplePage.test.tsx apps/web/src/people/people.css
git commit -m "feat: show guardian readiness in people roster"
```

### Task 8: Add guardian diagnostics, repair links and relationship history UI

**Files:**

- Modify: `apps/web/src/people/api.ts:60-147`
- Modify: `apps/web/src/people/GuardianPanel.tsx`
- Modify: `apps/web/src/people/GuardianPanel.test.tsx`
- Modify: `apps/web/src/people/PersonDetailPage.tsx:148-277`
- Modify: `apps/web/src/people/PersonDetailPage.test.tsx`
- Modify: `apps/web/src/people/people.css:531-759,892-912`
- Modify: `apps/web/src/settings/AccountsPage.tsx`
- Modify: `apps/web/src/settings/AccountsPage.test.tsx`

- [ ] **Step 1: Write failing diagnostics and end-history tests**

Use a readiness response containing:

```typescript
{
  ready: false,
  missing: ["guardian"],
  age_status: "minor",
  guardian_status: "pending",
  guardian_issues: [
    {
      relationship_id: relationshipId,
      guardian_person_id: guardianId,
      account_id: null,
      reason_codes: ["guardian_account_missing", "guardian_role_missing"],
    },
  ],
}
```

Assert an administrator sees `尚未开通独立账号`, `缺少监护人角色`, `前往账号管理`, and `前往权限管理`. Render the same panel without diagnostics and assert those internal reasons are absent.

Add an interaction test that clicks `结束监护关系`, requires a 2-character reason, POSTs to `/guardians/{relationshipId}/end`, retains the row with `已结束`, and refreshes `guardians`, `readiness`, `people`, and `person` query keys.

- [ ] **Step 2: Run focused Web tests and verify RED**

Run:

```bash
npm --prefix apps/web test -- GuardianPanel.test.tsx PersonDetailPage.test.tsx AccountsPage.test.tsx
```

Expected: FAIL because relationship IDs, issue codes and the end endpoint are absent.

- [ ] **Step 3: Extend Web API contracts and replace delete**

Use these types:

```typescript
export type GuardianIssueCode =
  | "guardian_underage"
  | "guardian_profile_incomplete"
  | "guardian_account_missing"
  | "guardian_account_disabled"
  | "guardian_role_missing"
  | "relationship_not_started"
  | "relationship_expired"
  | "relationship_ended"

export type GuardianIssue = {
  relationship_id: string
  guardian_person_id: string
  account_id: string | null
  reason_codes: GuardianIssueCode[]
}

export type GuardianRelationship = PersonSummary & {
  relationship_id: string
  relationship: string
  is_primary: boolean
  valid_from: string
  valid_until: string | null
  status: "scheduled" | "active" | "expired" | "ended"
  ended_at: string | null
  end_reason: string | null
}

export type Readiness = {
  ready: boolean
  missing: string[]
  age_status: AgeStatus
  guardian_status: GuardianStatus
  guardian_issues?: GuardianIssue[]
}

export function endGuardianRelationship(
  studentId: string,
  relationshipId: string,
  reason: string,
): Promise<GuardianRelationship> {
  return apiFetch<GuardianRelationship>(
    `/people/${studentId}/guardians/${relationshipId}/end`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  )
}
```

- [ ] **Step 4: Render state, history and repair actions**

In `GuardianPanel`:

- show `未成年 · 待绑定监护人` in danger styling when pending;
- show `未成年 · 监护人已绑定` when ready;
- show `成年 · 监护关系非强制` when `not_applicable`, while still listing active/history relationships and allowing a new binding;
- map every approved issue code to a Chinese label;
- link profile issues to `/people/{guardian_person_id}`;
- link missing/disabled accounts to `/settings/accounts`;
- link missing role to `/settings/permissions`;
- show scheduled, expired and ended status chips;
- require an end reason in a confirmation dialog and never physically remove the row when `includeInactive` is enabled.

Do not show account IDs or role diagnostics when `guardian_issues` is omitted.

- [ ] **Step 5: Improve account readiness errors**

When `AccountsPage` or the detail-page account dialog catches `ProblemDetailsError` with an error field `guardian`, display `请先绑定已成年、已实名且账号启用的监护人` and link back to the target person record. Other error paths retain the current server detail.

- [ ] **Step 6: Add responsive diagnostics styling**

Add status chips, a stacked issue list and repair-link styles. On mobile, end-reason actions and repair links must use full-width wrapping; no dialog may exceed `90dvh`, and the history row must not force horizontal scrolling.

```css
.guardianIssueList {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.guardianIssue {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  color: var(--color-danger-700);
  background: var(--color-danger-soft);
  font-size: 10px;
}

.guardianRepairLinks {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.guardianHistoryStatus {
  width: fit-content;
  padding: 3px 7px;
  border-radius: 999px;
  color: var(--color-text-muted);
  background: var(--color-canvas);
  font-size: 9px;
  font-weight: 740;
}

@media (max-width: 767px) {
  .guardianIssue,
  .guardianRepairLinks,
  .guardianEndActions {
    width: 100%;
  }

  .guardianRepairLinks > *,
  .guardianEndActions > * {
    flex: 1 1 100%;
  }
}
```

- [ ] **Step 7: Run Web tests, build and lint**

Run:

```bash
npm --prefix apps/web test -- GuardianPanel.test.tsx PersonDetailPage.test.tsx AccountsPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all commands PASS; admin diagnostics, non-admin privacy, adult bindings and end-history interactions are covered.

- [ ] **Step 8: Commit guardian management UI**

```bash
git add apps/web/src/people/api.ts apps/web/src/people/GuardianPanel.tsx apps/web/src/people/GuardianPanel.test.tsx apps/web/src/people/PersonDetailPage.tsx apps/web/src/people/PersonDetailPage.test.tsx apps/web/src/people/people.css apps/web/src/settings/AccountsPage.tsx apps/web/src/settings/AccountsPage.test.tsx
git commit -m "feat: manage guardian readiness and history"
```

### Task 9: Complete seeds, end-to-end journeys, documentation and full verification

**Files:**

- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/tests/helpers.ts`
- Modify: `apps/web/tests/platform-foundation.spec.ts`
- Modify: `README.md`

- [ ] **Step 1: Write the failing seed and E2E expectations**

Extend seed assertions so the fixed E2E guardian has an active account plus guardian role, the bound minor is `ready`, and a second minor remains `pending` without an account.

Extend the admin Playwright journey to perform this order:

1. create an adult guardian person;
2. open the guardian account from the detail page;
3. assign the guardian role in permission management;
4. create a minor student and observe the red `待绑定监护人` roster state;
5. bind the guardian and observe `监护人已绑定`;
6. open the now-ready minor student account, then close the one-time password dialog;
7. end the relationship with a reason and observe both retained history and the student account changing to disabled;
8. verify no horizontal overflow at desktop 1440 and mobile 390.

Extend the customer-service journey to assert the roster status is visible but internal issue reasons and repair links are absent.

- [ ] **Step 2: Run seed/E2E targets and verify RED**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests/cli/test_seed_e2e.py -q
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml up -d --build --wait
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml exec -T api alembic upgrade head
docker compose -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml exec -T api python -m app.cli.seed_e2e
E2E_BASE_URL='http://127.0.0.1:18080' npm --prefix apps/web run test:e2e
```

Expected: the seed assertion or new Playwright expectations FAIL until helpers and seed data follow the new account/role rule.

- [ ] **Step 3: Update seed data and Playwright helpers**

Change helper return values from only a name to a stable object:

```typescript
export type CreatedPerson = { id: string; name: string }

export async function createPerson(
  page: Page,
  type: "guardian" | "student" | "staff",
  staffType: "coach" | "life_teacher" | "customer_service" | "administration" | "other" =
    "life_teacher",
): Promise<CreatedPerson> {
  const sequence = nextPersonSequence(page)
  const minor = type === "student"
  const birth = minor ? "20120102" : "19900102"
  const namespace = projectNamespace(page)
  const name = `E2E-${namespace}-${type}-${sequence}`
  await page.goto("/people/new")
  const typeLabel = { guardian: "监护人", student: "学员", staff: "工作人员" }[type]
  await selectRadioCard(page, new RegExp(`^${typeLabel}`))
  await page.getByLabel("姓名").fill(name)
  await page.getByLabel("身份证").fill(makeCnId(birth, sequence))
  await page.getByLabel("性别").selectOption(sequence % 2 === 0 ? "female" : "male")
  await page.getByLabel("出生日期").fill(minor ? "2012-01-02" : "1990-01-02")
  await page.getByLabel("手机号").fill(`139${String(sequence).padStart(8, "0")}`)
  await page.getByLabel("邮箱").fill(`${namespace}-${type}-${sequence}@example.com`)
  if (type === "student") {
    await page.getByLabel("学员编号", { exact: true }).fill(`E2E-${namespace}-S-${sequence}`)
  }
  if (type === "staff") {
    await page.getByLabel("工作人员类型").selectOption(staffType)
  }
  await page.getByRole("button", { name: "保存人员" }).click()
  await page.getByRole("heading", { name }).waitFor()
  const match = page.url().match(/\/people\/([0-9a-f-]+)$/)
  if (!match) throw new Error(`Person detail URL missing id: ${page.url()}`)
  return { id: match[1], name }
}
```

Add helpers that open the account, close the one-time password dialog, select the created account in permission management, assign the `监护人` role, and bind by name. Use visible labels and exact role names; do not bypass the UI with direct database writes.

- [ ] **Step 4: Update README boundaries**

Move the following into “当前可用”:

- 名单成年/未成年状态；
- 待绑定监护人红色提醒；
- 有效成年监护人独立账号和角色校验；
- 监护关系历史及成年学员继续绑定；
- 最后一名有效监护人失效后的账号自动停用和会话撤销。

Keep project/payment/evaluation/housing/video in the existing future-stage section.

- [ ] **Step 5: Run the focused full suite**

Run:

```bash
.venv/bin/python -m pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all API and Web checks PASS with no skipped test that belongs to the configured PostgreSQL test database.

- [ ] **Step 6: Run real migration and responsive E2E verification**

Run:

```bash
scripts/check.sh
```

Expected: Compose config validation, PostgreSQL migrations, API tests, Web tests/build/lint, desktop E2E and mobile E2E all PASS. If Docker daemon is unavailable, record this as an unverified environment dependency rather than replacing the check with a success claim.

- [ ] **Step 7: Check repository cleanliness and commit**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints nothing; status lists only the intended seed, E2E and README changes before commit.

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py apps/web/tests/helpers.ts apps/web/tests/platform-foundation.spec.ts README.md
git commit -m "test: verify minor guardian readiness journeys"
```

## Final acceptance checklist

- [ ] 人员名单在服务器业务日期下正确显示成年/未成年。
- [ ] 未绑定有效监护人的未成年学员可以保存，但显示红色待绑定状态。
- [ ] 监护人必须成年、实名完整、账号启用且拥有监护人角色；单项功能拒绝不影响实名有效性。
- [ ] 创建和重新启用待绑定学员账号均被阻止。
- [ ] 最后一名有效监护人失效时，未成年学员账号自动停用且全部会话撤销。
- [ ] 关系到期后的首次登录或会话请求在返回任何业务数据前被拒绝。
- [ ] 管理员看到结构化无效原因和修复入口；客服和家庭端看不到内部原因。
- [ ] 一个监护人账号可以绑定并切换多个未成年或成年学员；一个学员可拥有多个监护人。
- [ ] 监护关系结束后历史保留，同一双方可以新建非重叠关系。
- [ ] 成年学员可以继续或新建监护关系，家庭访问持续到关系结束或到期。
- [ ] 家庭范围仍受拒绝优先权限控制，不能读取未绑定或已结束关系的学员。
- [ ] 住宿未来按计划入住日期复用同一判定接口，当前阶段不创建房态模块。
- [ ] 桌面 1440、手机 390 均无横向溢出。
- [ ] API、Web、静态检查、真实迁移和 E2E 均有可核对的通过证据。
