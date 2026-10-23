# Evaluation Center Page Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付评价中心第一实施单元，使获权 ERP 账号可以从响应式评价中心打开独立或内嵌的 `game-score-tool` 页面，并通过 60 秒单次票据无感建立受 ERP 会话约束的评分工具会话。

**Architecture:** ERP 新增 `app.evaluations` 边界、基础入口权限、两张短期桥接表和版本化内部接口；ERP Web 新增评价中心、系统评价 iframe 外壳和安全表单回跳页。`game-score-tool` 继续作为独立 FastAPI/Jinja 仓库，通过服务端 HTTP 调用 ERP、签名无状态登录 `state`、模式独立的 HttpOnly 会话 Cookie 和固定回调地址桥接身份；本单元把旧匿名 SQLite/公开媒体路径全部封闭，只展示只读页面壳，不启用截图、评分、日报、周报或营期问卷。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic、httpx、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、Jinja2

---

## Plan orientation

本计划在任何任务开始前冻结以下边界：

- ERP 仓库只新增评价入口、短期登录桥接、页面外壳与本地测试；不修改 Docker、Compose 或 NAS 文件。
- 评分工具保持独立仓库，仅复用页面和后续可用的算法代码；本单元不读写旧 SQLite，不公开旧 `uploads/`。
- `evaluation_center.access` 只控制入口，所有上传、评价内容和审核能力保持关闭。
- 账户浏览器只调用 ERP 账户接口；评分工具后端只调用带 Bearer 服务凭据的 `/integrations/evaluations/v1/*`。
- 登录请求有效 5 分钟，票据有效 60 秒且单次消费；评分工具每个受保护页面请求重新向 ERP 验证会话。
- 本地浏览器只用 `127.0.0.1` 打开 ERP、只用 `localhost:8010` 打开独立评分页；ERP `erp_session` 仅发送到 `/api`，内嵌和独立评分会话不会收到彼此的 Cookie。

本地主机矩阵固定如下；不能为了文本统一而把四项机械替换成同一主机名：

| 用途 | 固定地址 |
| --- | --- |
| 浏览器 ERP（开发/E2E） | `http://127.0.0.1:5173` / `http://127.0.0.1:5175` |
| 浏览器独立评分页 | `http://localhost:8010` |
| Vite 内部代理目标 | `http://127.0.0.1:8010` |
| 评分工具监听与健康检查 | `127.0.0.1:8010` / `http://127.0.0.1:8010/health/live` |

### Repository and responsibility map

| 边界 | 主要文件 | 单一职责 |
| --- | --- | --- |
| ERP 数据库/RBAC | `apps/api/app/evaluations/models.py`、`0007_evaluation_page_integration.py`、`app/rbac/seed.py` | 入口权限、登录请求、单次票据 |
| ERP 领域/API | `apps/api/app/evaluations/{config,bridge_service,schemas,router,integration_router}.py` | 固定 URL、票据状态、账户/服务接口 |
| ERP Web | `apps/web/src/evaluations/*`、`app/router.tsx`、`layout/AppShell.tsx` | 评价中心、iframe、form-post、导航 |
| 评分工具会话 | `config.py`、`session_codec.py`、`erp_client.py`、`integration.py` | 配置、签名 state/会话 Cookie、ERP 后端调用、即时撤权 |
| 评分工具页面 | `main.py`、`templates/*`、`static/{app,embed,style}.*` | 基路径、只读壳、ready 消息、封闭旧接口 |
| 跨仓库验证 | 两端 `evaluation_integration_v1.json`、`evaluation-page-integration.spec.ts`、`run-evaluation-page-e2e.sh` | 契约一致与三服务本地 E2E |

### Frozen route summary

```text
GET  /api/v1/evaluations/context
POST /api/v1/evaluations/bridge/tickets
GET  /api/v1/integrations/evaluations/v1/capabilities
POST /api/v1/integrations/evaluations/v1/login-requests
POST /api/v1/integrations/evaluations/v1/login-tickets/exchange
POST /api/v1/integrations/evaluations/v1/session-context
```

物理文件清单、完整 DTO、Cookie 载荷、本地变量和更细的保护规则位于文末附录 A–E；它们是本计划的规范部分，执行 Task 1 前必须通读。

---

### Task 1: Add the evaluation entry permission and bridge schema

**Files:**

- Create: `apps/api/app/evaluations/__init__.py`
- Create: `apps/api/app/evaluations/models.py`
- Create: `apps/api/migrations/versions/0007_evaluation_page_integration.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing migration and RBAC tests**

Add the fixed permission and grants to `test_rbac_seed.py`:

```python
EVALUATION_ACCESS_PERMISSION_ID = UUID(
    "20000000-0000-4000-8000-000000000026"
)
EVALUATION_ACCESS_GRANTS = {
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
}


def test_evaluation_entry_permission_uses_least_privilege_defaults(
    postgres_engine: Engine,
) -> None:
    with Session(postgres_engine) as db:
        permission = db.scalar(
            select(Permission).where(Permission.key == "evaluation_center.access")
        )
        assert permission is not None
        assert permission.id == EVALUATION_ACCESS_PERMISSION_ID
        assert permission.sensitive is False
        grants = {
            (role_key, permission_key, scope_kind)
            for role_key, permission_key, scope_kind in db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(Permission.key == "evaluation_center.access")
            )
        }
        assert grants == EVALUATION_ACCESS_GRANTS
        assert ("customer_service", "evaluation_center.access", "global") not in grants
```

Extend `test_migrations.py`:

```python
EVALUATION_TABLES = {
    "evaluation_bridge_requests",
    "evaluation_bridge_tickets",
}
EXPECTED_TABLES |= EVALUATION_TABLES


def test_evaluation_bridge_schema_has_fixed_constraints(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert EVALUATION_TABLES <= set(inspector.get_table_names())
    request_checks = {
        check["name"] for check in inspector.get_check_constraints(
            "evaluation_bridge_requests"
        )
    }
    ticket_checks = {
        check["name"] for check in inspector.get_check_constraints(
            "evaluation_bridge_tickets"
        )
    }
    assert "ck_evaluation_bridge_requests_mode" in request_checks
    assert "ck_evaluation_bridge_requests_expiry" in request_checks
    assert "ck_evaluation_bridge_tickets_expiry" in ticket_checks
    assert {
        index["name"] for index in inspector.get_indexes("evaluation_bridge_requests")
    } >= {"ix_evaluation_bridge_requests_expiry"}
    assert {
        index["name"] for index in inspector.get_indexes("evaluation_bridge_tickets")
    } >= {"ix_evaluation_bridge_tickets_expiry"}
```

Update the migration head assertion from `0006_coach_family_portal` to `0007_evaluation_page_integration`, and add an upgrade/downgrade/upgrade assertion proving `0007` restores both tables and the exact grants.

- [ ] **Step 2: Run the database tests and verify they fail**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
```

Expected: FAIL because revision `0007`, the permission and the bridge tables do not exist.

- [ ] **Step 3: Add the SQLAlchemy models**

Create `apps/api/app/evaluations/models.py`:

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class EvaluationBridgeRequest(TimestampMixin, Base):
    __tablename__ = "evaluation_bridge_requests"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('standalone', 'embedded')",
            name="ck_evaluation_bridge_requests_mode",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_requests_expiry",
        ),
        Index("ix_evaluation_bridge_requests_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationBridgeTicket(TimestampMixin, Base):
    __tablename__ = "evaluation_bridge_tickets"
    __table_args__ = (
        CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_tickets_expiry",
        ),
        Index("ix_evaluation_bridge_tickets_expiry", "expires_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    login_request_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_bridge_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    auth_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

Create an empty `apps/api/app/evaluations/__init__.py` and import `app.evaluations.models` in `migrations/env.py` so Alembic sees the tables.

- [ ] **Step 4: Implement the permission seed and migration**

Add to `PERMISSIONS`:

```python
"evaluation_center.access": PermissionSeed(
    UUID("20000000-0000-4000-8000-000000000026"),
    "进入评价中心",
),
```

Add only these non-admin grants; the existing admin comprehension grants every seeded permission:

```python
EVALUATION_ACCESS_ROLE_GRANTS = [
    (role_key, "evaluation_center.access", "global")
    for role_key in ("coach", "life_teacher", "staff", "guardian", "student")
]

ROLE_PERMISSION_GRANTS.extend(EVALUATION_ACCESS_ROLE_GRANTS)
```

Create `0007_evaluation_page_integration.py` with `down_revision = "0006_coach_family_portal"`. Its `upgrade()` must:

1. Fail if UUID/key `evaluation_center.access` already exists.
2. Insert the fixed permission.
3. Insert exactly the six global grants, including admin.
4. Create `evaluation_bridge_requests` and `evaluation_bridge_tickets` matching the models.
5. Create the two expiry indexes.

Its `downgrade()` must refuse to continue when account overrides reference the permission or the fixed grants/permission row were customized, then drop ticket before request, remove exact grants, and remove the permission.

Use this exact table order and columns in the migration:

```python
op.create_table(
    "evaluation_bridge_requests",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("client_id", sa.String(length=64), nullable=False),
    sa.Column("mode", sa.String(length=16), nullable=False),
    sa.Column("state_ciphertext", sa.Text(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("authorized_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint(
        "mode IN ('standalone', 'embedded')",
        name="ck_evaluation_bridge_requests_mode",
    ),
    sa.CheckConstraint(
        "expires_at > created_at",
        name="ck_evaluation_bridge_requests_expiry",
    ),
)
op.create_index(
    "ix_evaluation_bridge_requests_expiry",
    "evaluation_bridge_requests",
    ["expires_at", "id"],
)
op.create_table(
    "evaluation_bridge_tickets",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column(
        "login_request_id",
        sa.Uuid(),
        sa.ForeignKey("evaluation_bridge_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    sa.Column(
        "auth_session_id",
        sa.Uuid(),
        sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column(
        "account_id",
        sa.Uuid(),
        sa.ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("token_digest", sa.String(length=64), nullable=False, unique=True),
    sa.Column("token_ciphertext", sa.Text()),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("consumed_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint(
        "expires_at > created_at",
        name="ck_evaluation_bridge_tickets_expiry",
    ),
)
op.create_index(
    "ix_evaluation_bridge_tickets_expiry",
    "evaluation_bridge_tickets",
    ["expires_at", "id"],
)
```

Update `scripts/db-schema-manifest.sh`:

```bash
EXPECTED_ALEMBIC_HEAD=0007_evaluation_page_integration
```

and append both bridge table names to `REQUIRED_TABLES` and `ROW_COUNT_TABLES`.

- [ ] **Step 5: Run migration, model and RBAC checks**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
.venv/bin/ruff check apps/api/app/evaluations apps/api/migrations/versions/0007_evaluation_page_integration.py
.venv/bin/mypy apps/api/app/evaluations
bash -n scripts/db-schema-manifest.sh
```

Expected: database tests PASS; Ruff and mypy exit 0; shell syntax check exits 0.

- [ ] **Step 6: Commit the schema boundary**

```bash
git add apps/api/app/evaluations/__init__.py \
  apps/api/app/evaluations/models.py apps/api/app/rbac/seed.py \
  apps/api/migrations/env.py \
  apps/api/migrations/versions/0007_evaluation_page_integration.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  scripts/db-schema-manifest.sh
git commit -m "feat: add evaluation bridge schema"
```

### Task 2: Implement the ERP bridge domain service

**Files:**

- Create: `apps/api/app/evaluations/schemas.py`
- Create: `apps/api/app/evaluations/errors.py`
- Create: `apps/api/app/evaluations/config.py`
- Create: `apps/api/app/evaluations/bridge_service.py`
- Create: `apps/api/tests/services/test_evaluation_bridge.py`
- Modify: `apps/api/app/core/config.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `.env.example`

- [ ] **Step 1: Write failing bridge service tests**

Create `test_evaluation_bridge.py` with a fixed aware clock and real database rows. The first test must prove encrypted state, idempotent issue and one-time exchange:

```python
NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)
STATE = (
    "eyJleHBpcmVzX2F0IjoxNzg1Mzg5MTAwLCJtb2RlIjoic3RhbmRhbG9uZSIs"
    "Im5vbmNlIjoiYWJjIiwicmV0dXJuX3BhdGgiOiIvIiwidiI6MX0."
    "signature_abcdefghijklmnopqrstuvwxyz0123456789"
)


def test_bridge_issues_idempotent_ticket_and_consumes_it_once(
    db: Session,
    evaluation_bridge_service: EvaluationBridgeService,
    evaluation_auth_context: AuthContext,
) -> None:
    created = evaluation_bridge_service.create_login_request(
        EvaluationLoginRequestCreate(
            client_id="game-score-tool",
            mode="standalone",
            state=STATE,
        )
    )
    request_row = db.get(EvaluationBridgeRequest, created.request_id)
    assert request_row is not None
    assert STATE not in request_row.state_ciphertext

    first = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-issue-1",
    )
    repeated = evaluation_bridge_service.issue_ticket(
        request_id=created.request_id,
        context=evaluation_auth_context,
        request_id_for_audit="bridge-issue-2",
    )
    assert repeated == first
    assert first.state == STATE
    assert first.callback_url == "http://localhost:8010/auth/erp/callback"

    session = evaluation_bridge_service.exchange_ticket(
        first.ticket,
        request_id_for_audit="bridge-exchange-1",
    )
    assert session.account_id == evaluation_auth_context.account.id
    assert session.session_id == evaluation_auth_context.session.id
    ticket_row = db.scalar(
        select(EvaluationBridgeTicket).where(
            EvaluationBridgeTicket.login_request_id == created.request_id
        )
    )
    assert ticket_row is not None
    assert ticket_row.consumed_at == NOW
    assert ticket_row.token_ciphertext is None

    with pytest.raises(EvaluationTicketInvalid):
        evaluation_bridge_service.exchange_ticket(
            first.ticket,
            request_id_for_audit="bridge-exchange-replay",
        )
```

Add this explicit matrix; each row needs a separate assertion of the stable exception type:

```python
@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("expire_request", EvaluationBridgeRequestExpired),
        ("expire_ticket", EvaluationTicketInvalid),
        ("revoke_auth_session", EvaluationDelegatedSessionInvalid),
        ("disable_account", EvaluationDelegatedSessionInvalid),
        ("force_password_change", EvaluationDelegatedSessionInvalid),
        ("deny_entry_permission", EvaluationAccessDenied),
    ],
)
def test_bridge_rechecks_time_session_and_permission(
    mutation: str,
    expected: type[EvaluationBridgeError],
    bridge_scenario: BridgeScenario,
) -> None:
    bridge_scenario.apply(mutation)
    with pytest.raises(expected):
        bridge_scenario.perform_guarded_operation()
```

Also test:

- an embedded request receives `http://127.0.0.1:5175/evaluation-tool/auth/erp/callback`;
- a second account cannot issue a ticket for an already authorized request;
- `session_context()` rejects a random UUID without exposing whether an account exists;
- audit rows contain request/ticket UUIDs and mode but not `STATE`, plaintext ticket, email or phone.
- creating a new login request prunes at most 500 requests whose expiry is older than 24 hours, while retaining recent expired rows for retry diagnostics.

- [ ] **Step 2: Run the service test and verify it fails**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_bridge.py -q
```

Expected: FAIL because the schemas, errors, config and service do not exist.

- [ ] **Step 3: Add configuration with fixed callback derivation**

Extend `Settings`:

```python
evaluation_tool_service_token: SecretStr
erp_web_base_url: str = "http://127.0.0.1:5173"
evaluation_tool_standalone_base_url: str = "http://localhost:8010"
evaluation_tool_embedded_path: str = "/evaluation-tool"
evaluation_login_request_ttl_seconds: int = Field(default=300, ge=60, le=900)
evaluation_login_ticket_ttl_seconds: int = Field(default=60, ge=30, le=120)
```

In `validate_runtime_secrets()`, require the service token to be at least 32 characters and call `EvaluationBridgeConfig.from_settings(self)` so invalid URL/path configuration fails at startup. Add `EVALUATION_TOOL_SERVICE_TOKEN` to `tests/conftest.py` module environment and `test_settings`, and append these local-only examples to `.env.example`:

```dotenv
EVALUATION_TOOL_SERVICE_TOKEN=
ERP_WEB_BASE_URL=http://127.0.0.1:5173
EVALUATION_TOOL_STANDALONE_BASE_URL=http://localhost:8010
EVALUATION_TOOL_EMBEDDED_PATH=/evaluation-tool
EVALUATION_LOGIN_REQUEST_TTL_SECONDS=300
EVALUATION_LOGIN_TICKET_TTL_SECONDS=60
```

Create `config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from uuid import UUID

from app.core.config import Settings


def _validated_origin(value: str, *, production: bool) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("evaluation URL must be absolute HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("evaluation URL cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("evaluation base URL cannot contain a path")
    if production and parsed.scheme != "https":
        raise ValueError("production evaluation URLs require HTTPS")
    return value.rstrip("/")


def _validated_embedded_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or ".." in value:
        raise ValueError("evaluation embedded path must be one absolute local path")
    normalized = value.rstrip("/")
    if not normalized or "?" in normalized or "#" in normalized or "\\" in normalized:
        raise ValueError("invalid evaluation embedded path")
    return normalized


@dataclass(frozen=True)
class EvaluationBridgeConfig:
    erp_web_base_url: str
    standalone_base_url: str
    embedded_path: str
    request_ttl_seconds: int
    ticket_ttl_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "EvaluationBridgeConfig":
        production = settings.app_env == "production"
        return cls(
            erp_web_base_url=_validated_origin(
                settings.erp_web_base_url, production=production
            ),
            standalone_base_url=_validated_origin(
                settings.evaluation_tool_standalone_base_url,
                production=production,
            ),
            embedded_path=_validated_embedded_path(
                settings.evaluation_tool_embedded_path
            ),
            request_ttl_seconds=settings.evaluation_login_request_ttl_seconds,
            ticket_ttl_seconds=settings.evaluation_login_ticket_ttl_seconds,
        )

    def authorize_url(self, request_id: UUID) -> str:
        return f"{self.erp_web_base_url}/evaluation-connect/{request_id}"

    def callback_url(self, mode: str) -> str:
        if mode == "standalone":
            return f"{self.standalone_base_url}/auth/erp/callback"
        if mode == "embedded":
            return (
                f"{self.erp_web_base_url}{self.embedded_path}/auth/erp/callback"
            )
        raise ValueError("unsupported evaluation tool mode")

    def start_url(self, mode: str) -> str:
        query = "mode=" + mode + "&return_path=" + quote("/", safe="")
        if mode == "standalone":
            return f"{self.standalone_base_url}/auth/erp/start?{query}"
        return f"{self.embedded_path}/auth/erp/start?{query}"
```

- [ ] **Step 4: Add frozen schemas and stable errors**

Copy the Pydantic contracts from this plan into `schemas.py`. Create `errors.py`:

```python
class EvaluationBridgeError(ValueError):
    code = "evaluation_bridge_error"


class EvaluationBridgeRequestNotFound(EvaluationBridgeError):
    code = "bridge_request_not_found"


class EvaluationBridgeRequestExpired(EvaluationBridgeError):
    code = "bridge_request_expired"


class EvaluationBridgeRequestConflict(EvaluationBridgeError):
    code = "bridge_request_conflict"


class EvaluationTicketInvalid(EvaluationBridgeError):
    code = "bridge_ticket_invalid"


class EvaluationDelegatedSessionInvalid(EvaluationBridgeError):
    code = "delegated_session_invalid"


class EvaluationAccessDenied(EvaluationBridgeError):
    code = "evaluation_access_denied"
```

Do not put ticket/state values into exception strings.

- [ ] **Step 5: Implement token, permission and session primitives**

In `bridge_service.py`, define the service with dependency-injected clock:

```python
from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.transactions import write_transaction
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.models import EvaluationBridgeRequest, EvaluationBridgeTicket
from app.evaluations.schemas import (
    EvaluationLoginRequestCreate,
    EvaluationLoginRequestCreated,
    EvaluationTicketIssued,
    EvaluationToolSessionContext,
)
from app.identity.models import Person
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher

_STATE_FIELD = "evaluation_bridge.state"
_TICKET_FIELD = "evaluation_bridge.ticket"


class EvaluationBridgeService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        token_pepper: bytes,
        config: EvaluationBridgeConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(token_pepper) != 32:
            raise ValueError("evaluation token pepper must be 32 bytes")
        self.db = db
        self.cipher = cipher
        self.token_pepper = token_pepper
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("evaluation bridge clock must be timezone-aware")
        return value

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    def _token_digest(self, value: str) -> str:
        material = b"evaluation-ticket-v1\x00" + value.encode()
        return hmac.new(self.token_pepper, material, hashlib.sha256).hexdigest()

    def _has_access(self, account_id: UUID) -> bool:
        return RbacService(self.db).decision_context(account_id).decide(
            "evaluation_center.access", {"global"}
        ).allowed

    def _live_session_context(
        self,
        session_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[AuthSession, UserAccount, Person]:
        query = (
            select(AuthSession, UserAccount, Person)
            .join(UserAccount, UserAccount.id == AuthSession.user_id)
            .join(Person, Person.id == UserAccount.person_id)
            .where(AuthSession.id == session_id)
        )
        if lock:
            query = query.with_for_update(of=AuthSession)
        row = self.db.execute(query).one_or_none()
        now = self._now()
        if row is None:
            raise EvaluationDelegatedSessionInvalid("delegated session is invalid")
        session, account, person = row
        if (
            session.revoked_at is not None
            or session.expires_at <= now
            or account.status != "active"
            or account.force_password_change
            or person.deleted_at is not None
        ):
            raise EvaluationDelegatedSessionInvalid("delegated session is invalid")
        if not self._has_access(account.id):
            raise EvaluationAccessDenied("evaluation access is denied")
        return session, account, person

    @staticmethod
    def _context(
        session: AuthSession,
        account: UserAccount,
        person: Person,
    ) -> EvaluationToolSessionContext:
        return EvaluationToolSessionContext(
            account_id=account.id,
            person_id=person.id,
            display_name=person.full_name,
            session_id=session.id,
            expires_at=session.expires_at,
        )
```

- [ ] **Step 6: Implement the four service operations**

Add methods with these exact transaction rules:

```python
def create_login_request(
    self, input: EvaluationLoginRequestCreate
) -> EvaluationLoginRequestCreated:
    now = self._now()
    with write_transaction(self.db):
        stale_ids = (
            select(EvaluationBridgeRequest.id)
            .where(
                EvaluationBridgeRequest.expires_at
                < now - timedelta(hours=24)
            )
            .order_by(EvaluationBridgeRequest.expires_at, EvaluationBridgeRequest.id)
            .limit(500)
        )
        self.db.execute(
            delete(EvaluationBridgeRequest).where(
                EvaluationBridgeRequest.id.in_(stale_ids)
            )
        )
        row = EvaluationBridgeRequest(
            client_id=input.client_id,
            mode=input.mode,
            state_ciphertext=self.cipher.encrypt(input.state, field_name=_STATE_FIELD),
            expires_at=now + timedelta(seconds=self.config.request_ttl_seconds),
        )
        self.db.add(row)
        self.db.flush()
        result = EvaluationLoginRequestCreated(
            request_id=row.id,
            authorize_url=self.config.authorize_url(row.id),
            expires_at=row.expires_at,
        )
    return result


def issue_ticket(
    self,
    *,
    request_id: UUID,
    context: AuthContext,
    request_id_for_audit: str,
) -> EvaluationTicketIssued:
    now = self._now()
    with write_transaction(self.db):
        live_session, live_account, _ = self._live_session_context(
            context.session.id,
            lock=True,
        )
        if live_account.id != context.account.id:
            raise EvaluationDelegatedSessionInvalid(
                "delegated session is invalid"
            )
        request_row = self.db.scalar(
            select(EvaluationBridgeRequest)
            .where(EvaluationBridgeRequest.id == request_id)
            .with_for_update()
        )
        if request_row is None:
            raise EvaluationBridgeRequestNotFound("bridge request was not found")
        if request_row.expires_at <= now:
            raise EvaluationBridgeRequestExpired("bridge request expired")
        if not self._has_access(context.account.id):
            raise EvaluationAccessDenied("evaluation access is denied")
        existing = self.db.scalar(
            select(EvaluationBridgeTicket)
            .where(EvaluationBridgeTicket.login_request_id == request_id)
            .with_for_update()
        )
        if existing is not None and (
            existing.account_id != context.account.id
            or existing.auth_session_id != context.session.id
        ):
            raise EvaluationBridgeRequestConflict("bridge request already authorized")
        if existing is not None and existing.consumed_at is not None:
            raise EvaluationBridgeRequestConflict("bridge request already consumed")
        if (
            existing is not None
            and existing.expires_at > now
            and existing.token_ciphertext is not None
        ):
            ticket = self.cipher.decrypt(
                existing.token_ciphertext, field_name=_TICKET_FIELD
            )
            ticket_row = existing
        else:
            ticket = self._new_token()
            ticket_expires_at = min(
                request_row.expires_at,
                now + timedelta(seconds=self.config.ticket_ttl_seconds),
            )
            if existing is None:
                ticket_row = EvaluationBridgeTicket(
                    login_request_id=request_id,
                    auth_session_id=live_session.id,
                    account_id=live_account.id,
                    token_digest=self._token_digest(ticket),
                    token_ciphertext=self.cipher.encrypt(
                        ticket, field_name=_TICKET_FIELD
                    ),
                    expires_at=ticket_expires_at,
                )
                self.db.add(ticket_row)
            else:
                ticket_row = existing
                ticket_row.token_digest = self._token_digest(ticket)
                ticket_row.token_ciphertext = self.cipher.encrypt(
                    ticket, field_name=_TICKET_FIELD
                )
                ticket_row.expires_at = ticket_expires_at
            self.db.flush()
        request_row.authorized_at = now
        AuditService(self.db).append(
            actor_user_id=live_account.id,
            action="evaluation.bridge.ticket_issued",
            resource_type="evaluation_bridge_request",
            resource_id=request_row.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id_for_audit,
            metadata={"mode": request_row.mode, "client_id": request_row.client_id},
        )
        result = EvaluationTicketIssued(
            callback_url=self.config.callback_url(request_row.mode),
            ticket=ticket,
            state=self.cipher.decrypt(
                request_row.state_ciphertext, field_name=_STATE_FIELD
            ),
            expires_at=ticket_row.expires_at,
        )
    return result
```

Implement the remaining methods exactly as follows:

```python
def exchange_ticket(
    self,
    ticket: str,
    *,
    request_id_for_audit: str,
) -> EvaluationToolSessionContext:
    now = self._now()
    with write_transaction(self.db):
        ticket_row = self.db.scalar(
            select(EvaluationBridgeTicket)
            .where(
                EvaluationBridgeTicket.token_digest == self._token_digest(ticket)
            )
            .with_for_update()
        )
        if (
            ticket_row is None
            or ticket_row.expires_at <= now
            or ticket_row.consumed_at is not None
        ):
            raise EvaluationTicketInvalid("bridge ticket is invalid")
        session, account, person = self._live_session_context(
            ticket_row.auth_session_id,
            lock=True,
        )
        if account.id != ticket_row.account_id:
            raise EvaluationTicketInvalid("bridge ticket is invalid")
        ticket_row.consumed_at = now
        ticket_row.token_ciphertext = None
        AuditService(self.db).append(
            actor_user_id=account.id,
            action="evaluation.bridge.ticket_exchanged",
            resource_type="evaluation_bridge_ticket",
            resource_id=ticket_row.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id_for_audit,
            metadata={"login_request_id": str(ticket_row.login_request_id)},
        )
        result = self._context(session, account, person)
    return result


def session_context(self, session_id: UUID) -> EvaluationToolSessionContext:
    session, account, person = self._live_session_context(session_id)
    return self._context(session, account, person)
```

`session_context()` never accepts account/person IDs from the scoring tool.

- [ ] **Step 7: Run bridge service tests and static checks**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/services/test_evaluation_bridge.py -q
.venv/bin/ruff check apps/api/app/evaluations apps/api/tests/services/test_evaluation_bridge.py
.venv/bin/mypy apps/api/app/evaluations
```

Expected: service tests PASS; Ruff and mypy exit 0.

- [ ] **Step 8: Commit the bridge domain**

```bash
git add .env.example apps/api/app/core/config.py apps/api/tests/conftest.py \
  apps/api/app/evaluations/schemas.py apps/api/app/evaluations/errors.py \
  apps/api/app/evaluations/config.py apps/api/app/evaluations/bridge_service.py \
  apps/api/tests/services/test_evaluation_bridge.py
git commit -m "feat: implement evaluation login bridge"
```

### Task 3: Expose the ERP evaluation and integration API v1

**Files:**

- Create: `apps/api/app/evaluations/dependencies.py`
- Create: `apps/api/app/evaluations/router.py`
- Create: `apps/api/app/evaluations/integration_router.py`
- Create: `apps/api/app/evaluations/routing.py`
- Create: `apps/api/tests/api/test_evaluation_integration.py`
- Modify: `apps/api/app/auth/dependencies.py`
- Modify: `apps/api/app/auth/router.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/api/conftest.py`
- Modify: `apps/api/tests/api/test_auth_sessions.py`

- [ ] **Step 1: Write failing account and service API tests**

Create `test_evaluation_integration.py`. Use the existing login helper to obtain ERP Cookie and CSRF. Cover exact paths and cache headers:

```python
SERVICE_HEADERS = {
    "Authorization": "Bearer evaluation-local-service-token-2026-change-me"
}
SIGNED_STATE = (
    "eyJleHBpcmVzX2F0IjoxNzg1Mzg5MTAwLCJtb2RlIjoic3RhbmRhbG9uZSIs"
    "Im5vbmNlIjoiYWJjIiwicmV0dXJuX3BhdGgiOiIvIiwidiI6MX0."
    "signature_abcdefghijklmnopqrstuvwxyz0123456789"
)


def test_evaluation_context_is_private_and_contains_fixed_start_urls(
    db_client: TestClient,
    login_as_admin: LoginResult,
) -> None:
    response = db_client.get("/api/v1/evaluations/context")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.json() == {
        "accessible": True,
        "system_evaluation_available": True,
        "embedded_start_url": (
            "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F"
        ),
        "standalone_start_url": (
            "http://localhost:8010/auth/erp/start?"
            "mode=standalone&return_path=%2F"
        ),
        "upload_available": False,
        "video_available": False,
        "reports_available": False,
        "surveys_available": False,
        "integration_api_version": "v1",
    }


def test_service_registers_then_user_issues_and_service_exchanges_ticket(
    db_client: TestClient,
    login_as_admin: LoginResult,
) -> None:
    registered = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=SERVICE_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": "standalone",
            "state": SIGNED_STATE,
        },
    )
    assert registered.status_code == 201
    request_id = registered.json()["request_id"]

    issued = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": request_id},
    )
    assert issued.status_code == 201
    assert "ticket" not in issued.request.url.query
    assert issued.json()["state"] == SIGNED_STATE

    exchanged = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=SERVICE_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert exchanged.status_code == 200
    assert exchanged.json()["account_id"] == str(login_as_admin.account_id)
    assert "email" not in exchanged.json()
    assert "permissions" not in exchanged.json()

    replay = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=SERVICE_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert replay.status_code == 401
    assert replay.json()["code"] == "bridge_ticket_invalid"
```

Add tests proving:

```python
@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Bearer wrong-service-token"],
)
def test_service_api_rejects_missing_or_wrong_bearer(
    db_client: TestClient,
    authorization: str | None,
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = db_client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=headers,
    )
    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_ticket_issue_requires_csrf(
    db_client: TestClient,
    login_as_admin: LoginResult,
) -> None:
    del login_as_admin
    registered = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=SERVICE_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": "embedded",
            "state": SIGNED_STATE,
        },
    )
    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        json={"request_id": registered.json()["request_id"]},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"


def test_customer_service_has_no_evaluation_context_by_default(
    db_client: TestClient,
    login_as_customer_service: LoginResult,
) -> None:
    del login_as_customer_service
    response = db_client.get("/api/v1/evaluations/context")
    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_access_denied"


def test_unknown_bridge_object_uses_stable_masked_error(
    db_client: TestClient,
    login_as_admin: LoginResult,
) -> None:
    plaintext_ticket = "ticket_abcdefghijklmnopqrstuvwxyz0123456789"
    plaintext_state = "state_abcdefghijklmnopqrstuvwxyz0123456789"
    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "bridge_request_not_found"
    assert response.json()["detail"] not in {plaintext_ticket, plaintext_state}
```

Also parametrize `/login-requests` with a 31-character state, a 1025-character state and a state containing `+`, `/` or
whitespace; each must return 422. The signed `SIGNED_STATE` above contains a `.` and must be accepted unchanged, proving ERP
treats it as an opaque Base64URL/HMAC token instead of parsing its payload.

The capabilities response must be exactly:

```json
{
  "api_version": "v1",
  "features": ["auth_bridge", "page_shell"],
  "legacy_writes_enabled": false
}
```

Extend `test_auth_sessions.py` with a regression proving the ERP Cookie cannot reach the embedded score-tool path:

```python
def test_erp_session_cookie_is_scoped_to_api_and_logout_uses_same_path(
    db_client: TestClient,
    active_account,
) -> None:
    signed_in = login(db_client, active_account)
    assert signed_in.status_code == 200
    assert "Path=/api" in signed_in.headers["set-cookie"]

    api_cookie = db_client.build_request(
        "GET", "/api/v1/auth/me"
    ).headers.get("cookie", "")
    embedded_cookie = db_client.build_request(
        "GET", "/evaluation-tool/"
    ).headers.get("cookie", "")
    assert "erp_session=" in api_cookie
    assert "erp_session=" not in embedded_cookie

    logged_out = db_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": signed_in.json()["csrf_token"]},
    )
    assert logged_out.status_code == 204
    assert "Path=/api" in logged_out.headers["set-cookie"]
```

- [ ] **Step 2: Run the API test and verify it fails**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/api/test_evaluation_integration.py \
  apps/api/tests/api/test_auth_sessions.py::test_erp_session_cookie_is_scoped_to_api_and_logout_uses_same_path -q
```

Expected: FAIL with route-not-found responses and the ERP Cookie still using `Path=/`.

- [ ] **Step 3: Add service authentication and bridge dependencies**

Create `dependencies.py`:

```python
from __future__ import annotations

import base64
import hmac
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.evaluations.bridge_service import EvaluationBridgeService
from app.evaluations.config import EvaluationBridgeConfig
from app.security.encryption import EnvelopeCipher


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def require_evaluation_tool_service(request: Request) -> None:
    provided = request.headers.get("Authorization", "")
    expected = (
        "Bearer "
        + request.app.state.settings.evaluation_tool_service_token.get_secret_value()
    )
    if not hmac.compare_digest(provided, expected):
        raise ProblemError(
            status=401,
            code="integration_auth_invalid",
            detail="集成服务身份无效",
        )


def get_evaluation_bridge_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationBridgeService:
    settings: Settings = request.app.state.settings
    return EvaluationBridgeService(
        db,
        cipher=EnvelopeCipher(
            _secret_bytes(settings, "field_encryption_master_key")
        ),
        token_pepper=_secret_bytes(settings, "session_token_pepper"),
        config=EvaluationBridgeConfig.from_settings(settings),
    )
```

Do not accept the service token from query, form data or cookies.

- [ ] **Step 4: Implement account-facing routes**

Create `router.py` with prefix `/evaluations`. Both routes require `require_standard_account`; context also executes the RBAC decision with `{"global"}` on every request:

```python
router = APIRouter(prefix="/evaluations", tags=["evaluations"])


def _require_evaluation_access(db: Session, account_id: UUID) -> None:
    allowed = RbacService(db).decision_context(account_id).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    if not allowed:
        raise ProblemError(
            status=403,
            code="evaluation_access_denied",
            detail="没有进入评价中心的权限",
        )


@router.get("/context", response_model=EvaluationCenterContextResponse)
def get_evaluation_context(
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> EvaluationCenterContextResponse:
    _require_evaluation_access(db, context.account.id)
    config = EvaluationBridgeConfig.from_settings(request.app.state.settings)
    return EvaluationCenterContextResponse(
        embedded_start_url=config.start_url("embedded"),
        standalone_start_url=config.start_url("standalone"),
    )


@router.post(
    "/bridge/tickets",
    response_model=EvaluationTicketIssued,
    status_code=201,
)
def issue_evaluation_ticket(
    input: EvaluationTicketCreate,
    request: Request,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        EvaluationBridgeService, Depends(get_evaluation_bridge_service)
    ],
) -> EvaluationTicketIssued:
    try:
        return service.issue_ticket(
            request_id=input.request_id,
            context=context,
            request_id_for_audit=str(request.state.request_id),
        )
    except EvaluationBridgeError as exc:
        _raise_evaluation_error(exc)
```

Map missing request to 404, expired request to 410, conflict to 409, access denied to 403, and never place the exception's internal string in the public detail.

- [ ] **Step 5: Implement service-facing v1 routes**

Create `integration_router.py`:

```python
router = APIRouter(
    prefix="/integrations/evaluations/v1",
    tags=["evaluation-integration"],
    dependencies=[Depends(require_evaluation_tool_service)],
)


@router.get("/capabilities", response_model=EvaluationIntegrationCapabilities)
def capabilities() -> EvaluationIntegrationCapabilities:
    return EvaluationIntegrationCapabilities(
        features=["auth_bridge", "page_shell"]
    )


@router.post(
    "/login-requests",
    response_model=EvaluationLoginRequestCreated,
    status_code=201,
)
def create_login_request(
    input: EvaluationLoginRequestCreate,
    service: Annotated[
        EvaluationBridgeService, Depends(get_evaluation_bridge_service)
    ],
) -> EvaluationLoginRequestCreated:
    return service.create_login_request(input)


@router.post(
    "/login-tickets/exchange",
    response_model=EvaluationToolSessionContext,
)
def exchange_login_ticket(
    input: EvaluationTicketExchange,
    request: Request,
    service: Annotated[
        EvaluationBridgeService, Depends(get_evaluation_bridge_service)
    ],
) -> EvaluationToolSessionContext:
    try:
        return service.exchange_ticket(
            input.ticket,
            request_id_for_audit=str(request.state.request_id),
        )
    except EvaluationBridgeError as exc:
        _raise_integration_error(exc)


@router.post(
    "/session-context",
    response_model=EvaluationToolSessionContext,
)
def delegated_session_context(
    input: EvaluationDelegatedSessionRequest,
    service: Annotated[
        EvaluationBridgeService, Depends(get_evaluation_bridge_service)
    ],
) -> EvaluationToolSessionContext:
    try:
        return service.session_context(input.session_id)
    except EvaluationBridgeError as exc:
        _raise_integration_error(exc)
```

Map invalid/expired/consumed ticket and invalid delegated session to 401. Map explicit permission loss to 403. Both return generic Chinese details without identifying an account.

- [ ] **Step 6: Isolate the ERP Cookie and protect evaluation responses from storage**

In `auth/dependencies.py`, define the Cookie path next to its name:

```python
SESSION_COOKIE_NAME = "erp_session"
SESSION_COOKIE_PATH = "/api"
```

Import `SESSION_COOKIE_PATH` in `auth/router.py`. Use `path=SESSION_COOKIE_PATH` in both `_set_session_cookie()` and
`logout()`'s `delete_cookie()` call. Login and deletion must always use the same path; do not set a second compatibility
Cookie at `/`.

Create `routing.py` following the existing portal middleware:

```python
_EVALUATION_PREFIXES = (
    "/api/v1/evaluations/",
    "/api/v1/integrations/evaluations/",
)


class PrivateEvaluationResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(
            _EVALUATION_PREFIXES
        ):
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["private_portal_response"] = True

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store, private"
            await send(message)

        await self.app(scope, receive, send_private)
```

Python `str.startswith()` accepts the tuple above. Register the middleware in `main.py`, and include both routers in `api/router.py`.

- [ ] **Step 7: Run API, regression and static checks**

```bash
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/api/test_evaluation_integration.py \
  apps/api/tests/api/test_auth_sessions.py \
  apps/api/tests/api/test_permissions.py -q
.venv/bin/ruff check apps/api/app/evaluations \
  apps/api/app/auth/dependencies.py apps/api/app/auth/router.py \
  apps/api/tests/api/test_auth_sessions.py \
  apps/api/tests/api/test_evaluation_integration.py
.venv/bin/mypy apps/api/app
```

Expected: selected API tests PASS; Ruff and full API mypy exit 0.

- [ ] **Step 8: Commit the ERP integration API**

```bash
git add apps/api/app/evaluations/dependencies.py \
  apps/api/app/evaluations/router.py \
  apps/api/app/evaluations/integration_router.py \
  apps/api/app/evaluations/routing.py apps/api/app/api/router.py \
  apps/api/app/main.py apps/api/app/auth/dependencies.py \
  apps/api/app/auth/router.py apps/api/tests/api/conftest.py \
  apps/api/tests/api/test_auth_sessions.py \
  apps/api/tests/api/test_evaluation_integration.py
git commit -m "feat: expose evaluation integration api"
```

### Task 4: Add the ERP evaluation center, client and navigation

**Files:**

- Create: `apps/web/src/evaluations/api.ts`
- Create: `apps/web/src/evaluations/api.test.ts`
- Create: `apps/web/src/evaluations/useEvaluationAccessError.ts`
- Create: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Create: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`
- Create: `apps/web/src/evaluations/evaluations.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.tsx`
- Modify: `apps/web/src/portal/PortalDashboardPage.test.tsx`
- Modify: `apps/web/src/portal/portal.css`
- Modify: `apps/web/vite.config.ts`

- [ ] **Step 1: Write failing typed-client tests**

Create `api.test.ts`:

```ts
import { afterEach, expect, it, vi } from "vitest"

import { createEvaluationTicket, getEvaluationContext } from "./api"

afterEach(() => vi.unstubAllGlobals())

it("uses the fixed evaluation account endpoints", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)

  await getEvaluationContext()
  await createEvaluationTicket("90000000-0000-4000-8000-000000000001")

  expect(fetchMock.mock.calls.map(([request]) => String(request))).toEqual([
    "/api/v1/evaluations/context",
    "/api/v1/evaluations/bridge/tickets",
  ])
  expect(fetchMock.mock.calls[1][1]).toMatchObject({
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request_id: "90000000-0000-4000-8000-000000000001",
    }),
  })
})
```

- [ ] **Step 2: Write failing page, route, navigation and dashboard tests**

`EvaluationCenterPage.test.tsx` must assert:

```tsx
it("shows only the working system page and explicit disabled later branches", async () => {
  renderEvaluationCenter({ permission: true })
  expect(await screen.findByRole("link", { name: "打开系统评价" })).toBeVisible()
  expect(screen.getByText("教学评语")).toBeVisible()
  expect(screen.getByText("营期评价")).toBeVisible()
  expect(screen.getAllByText("尚未开放")).toHaveLength(2)
  expect(screen.queryByRole("button", { name: /上传/ })).not.toBeInTheDocument()
})
```

Extend `AppShell.test.tsx`:

```tsx
it("shows evaluation navigation only with the entry permission", async () => {
  const { rerender } = render(
    <MemoryRouter>
      <AppShell permissions={new Set(["evaluation_center.access"])} />
    </MemoryRouter>,
  )
  expect(screen.getAllByRole("link", { name: "评价中心" })).toHaveLength(2)

  rerender(
    <MemoryRouter>
      <AppShell permissions={new Set(["people.list"])} />
    </MemoryRouter>,
  )
  expect(screen.queryByRole("link", { name: "评价中心" })).not.toBeInTheDocument()
})
```

Extend route tests to prove `/evaluations` redirects a customer-service-only user to `/`, while an account with `evaluation_center.access` renders heading “评价中心”. Extend the dashboard test so an evaluation-only account sees `进入评价中心`, and prove no `/evaluations/context` request is made just to render that card.

- [ ] **Step 3: Run Web tests and verify they fail**

```bash
npm --prefix apps/web test -- \
  src/evaluations/api.test.ts \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx \
  src/portal/PortalDashboardPage.test.tsx
```

Expected: FAIL because the evaluation client, pages, route and navigation do not exist.

- [ ] **Step 4: Implement the exact typed client**

Create `api.ts`:

```ts
import { apiFetch } from "../api/client"

export type EvaluationCenterContext = {
  accessible: true
  system_evaluation_available: true
  embedded_start_url: string
  standalone_start_url: string
  upload_available: false
  video_available: false
  reports_available: false
  surveys_available: false
  integration_api_version: "v1"
}

export type EvaluationTicketIssued = {
  callback_url: string
  ticket: string
  state: string
  expires_at: string
}

export const evaluationKeys = {
  root: (accountId: string) => ["evaluations", accountId] as const,
  context: (accountId: string) => ["evaluations", accountId, "context"] as const,
}

export function getEvaluationContext(): Promise<EvaluationCenterContext> {
  return apiFetch<EvaluationCenterContext>("/evaluations/context")
}

export function createEvaluationTicket(
  requestId: string,
): Promise<EvaluationTicketIssued> {
  return apiFetch<EvaluationTicketIssued>("/evaluations/bridge/tickets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  })
}
```

- [ ] **Step 5: Build the evaluation center page**

Create `useEvaluationAccessError.ts` so an account-level 403 removes only that account's evaluation queries and returns to `/`; 401 remains handled globally by `AuthProvider`, while 500/network errors remain on the page for retry:

```tsx
export function useEvaluationAccessError(error: unknown): {
  permissionRevoked: boolean
  message: string
} {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const permissionRevoked =
    error instanceof ProblemDetailsError && error.problem.status === 403

  useEffect(() => {
    if (!permissionRevoked || !user) return
    queryClient.removeQueries({ queryKey: evaluationKeys.root(user.account_id) })
    navigate("/", { replace: true })
  }, [navigate, permissionRevoked, queryClient, user])

  return {
    permissionRevoked,
    message:
      error instanceof ProblemDetailsError
        ? error.problem.detail
        : "评价中心暂时不可用，请重试。",
  }
}
```

Use this hook in both evaluation pages. Page tests must prove a 403 removes a seeded cached URL and navigates home, while 500 retains the ERP login and renders retry.

Create `EvaluationCenterPage.tsx`. It must fetch context with `staleTime: 0`, hide old URLs while refetching, and render a retry state on non-authorization errors:

```tsx
export function EvaluationCenterPage() {
  const { user } = useAuth()
  const context = useQuery({
    queryKey: evaluationKeys.context(user?.account_id ?? "anonymous"),
    queryFn: getEvaluationContext,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
  })

  const accessError = useEvaluationAccessError(context.error)
  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <EvaluationLoading label="正在更新评价中心" />
  }
  if (context.isError) {
    return (
      <EvaluationError
        message={accessError.message}
        onRetry={() => void context.refetch()}
      />
    )
  }

  return (
    <div className="evaluationPage">
      <header className="evaluationPageHeader">
        <p className="pageEyebrow">EVALUATIONS</p>
        <h1>评价中心</h1>
        <p>系统评价与教学反馈</p>
      </header>
      <section className="evaluationBranchGrid" aria-label="评价类型">
        <article className="evaluationBranch active">
          <MonitorUp aria-hidden="true" />
          <div>
            <h2>系统评价</h2>
            <p>查看评分工具页面；截图上传和评分处理将在后续单元启用。</p>
          </div>
          <Link to="/evaluations/system" aria-label="打开系统评价">
            打开系统评价 <ArrowRight aria-hidden="true" />
          </Link>
        </article>
        <DisabledEvaluationBranch title="教学评语" />
        <DisabledEvaluationBranch title="营期评价" />
      </section>
    </div>
  )
}
```

Disabled branches use plain `<span>尚未开放</span>`, not buttons or links. Do not describe keyboard shortcuts or future GPU behavior on this overview page.

- [ ] **Step 6: Add route, navigation and dashboard entry**

Register only `/evaluations` in this task:

```tsx
{
  path: "/evaluations",
  element: (
    <PermissionRoute permission="evaluation_center.access">
      <EvaluationCenterPage />
    </PermissionRoute>
  ),
},
```

Add this navigation item after home and before portal items:

```tsx
{
  to: "/evaluations",
  label: "评价中心",
  shortLabel: "评价",
  icon: MessageSquareText,
  mobilePriority: 4,
  permission: "evaluation_center.access",
  activePrefix: "/evaluations",
},
```

In `PortalDashboardPage`, include `evaluation_center.access` in the page capability decision and render without an extra data fetch:

```tsx
const evaluationAvailable =
  user?.permissions.includes("evaluation_center.access") === true

{evaluationAvailable ? (
  <article className="portalTaskCard evaluation">
    <div className="portalTaskIcon"><MessageSquareText aria-hidden="true" /></div>
    <div className="portalTaskBody">
      <p className="portalTaskLabel">成长反馈</p>
      <h2>评价中心</h2>
      <p>系统评价与教学反馈</p>
    </div>
    <Link to="/evaluations" aria-label="进入评价中心">
      进入评价中心 <ArrowRight aria-hidden="true" />
    </Link>
  </article>
) : null}
```

Update early-return logic so an evaluation-only account does not fall into “当前暂无可用工作项”. Add `.portalTaskCard.evaluation` colors without introducing another blue-only card.

- [ ] **Step 7: Add local scoring-tool proxy and responsive CSS**

Replace `vite.config.ts` with a callback form that reads a server-only environment value:

```ts
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const evaluationTarget =
    env.EVALUATION_TOOL_PROXY_TARGET ?? "http://127.0.0.1:8010"
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": "http://127.0.0.1:8000",
        "/evaluation-tool": {
          target: evaluationTarget,
          changeOrigin: true,
          headers: { "X-Forwarded-Prefix": "/evaluation-tool" },
          rewrite: (path: string) =>
            path.replace(/^\/evaluation-tool/, "") || "/",
        },
      },
    },
  }
})
```

`evaluations.css` must use a three-column desktop grid, two columns below 1024 and one below 768; cards use radius 8px, headings at 18px, stable min-height and 44px actions. It must not add global `overflow-x: hidden`.

- [ ] **Step 8: Run Web tests, build and lint**

```bash
npm --prefix apps/web test -- \
  src/evaluations/api.test.ts \
  src/evaluations/EvaluationCenterPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx \
  src/portal/PortalDashboardPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: selected tests PASS; TypeScript build and ESLint exit 0.

- [ ] **Step 9: Commit the ERP evaluation entry**

```bash
git add apps/web/vite.config.ts apps/web/src/evaluations \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx \
  apps/web/src/portal/PortalDashboardPage.tsx \
  apps/web/src/portal/PortalDashboardPage.test.tsx \
  apps/web/src/portal/portal.css
git commit -m "feat: add evaluation center shell"
```

### Task 5: Build the safe embedded system-evaluation page

**Files:**

- Create: `apps/web/src/evaluations/SystemEvaluationPage.tsx`
- Create: `apps/web/src/evaluations/SystemEvaluationPage.test.tsx`
- Modify: `apps/web/src/evaluations/evaluations.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] **Step 1: Write failing iframe handshake and route tests**

Create `SystemEvaluationPage.test.tsx` with fake timers. It must not treat `load` alone as ready:

```tsx
it("accepts ready only from the configured iframe window and current origin", async () => {
  renderSystemEvaluation()
  const iframe = await screen.findByTitle("系统评价")
  expect(screen.getByText("正在连接系统评价")).toBeVisible()

  window.dispatchEvent(
    new MessageEvent("message", {
      origin: "https://attacker.example",
      source: iframe.contentWindow,
      data: { type: "game-score-tool.ready", api_version: "v1" },
    }),
  )
  expect(screen.getByText("正在连接系统评价")).toBeVisible()

  window.dispatchEvent(
    new MessageEvent("message", {
      origin: window.location.origin,
      source: iframe.contentWindow,
      data: { type: "game-score-tool.ready", api_version: "v1" },
    }),
  )
  expect(screen.queryByText("正在连接系统评价")).not.toBeInTheDocument()
})
```

Add tests proving:

- a same-origin message from another window is ignored;
- wrong `type` or API version is ignored;
- after 10 seconds without ready, an alert shows “系统评价暂时无法连接” and a retry button creates a new iframe key;
- the standalone link uses the API-provided URL, `target="_blank"` and `rel="noopener noreferrer"`;
- no upload input or video input is rendered by the ERP parent page;
- `/evaluations/system` is permission guarded.

- [ ] **Step 2: Run the selected test and verify it fails**

```bash
npm --prefix apps/web test -- \
  src/evaluations/SystemEvaluationPage.test.tsx src/app/router.test.tsx
```

Expected: FAIL because the component and route do not exist.

- [ ] **Step 3: Implement fixed-origin iframe readiness**

Create `SystemEvaluationPage.tsx`:

```tsx
const READY_MESSAGE = {
  type: "game-score-tool.ready",
  api_version: "v1",
} as const

function isReadyMessage(value: unknown): boolean {
  if (!value || typeof value !== "object") return false
  const candidate = value as Record<string, unknown>
  return (
    candidate.type === READY_MESSAGE.type &&
    candidate.api_version === READY_MESSAGE.api_version
  )
}

export function SystemEvaluationPage() {
  const { user } = useAuth()
  const [attempt, setAttempt] = useState(0)
  const [frameReady, setFrameReady] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const context = useQuery({
    queryKey: evaluationKeys.context(user?.account_id ?? "anonymous"),
    queryFn: getEvaluationContext,
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
  })
  const accessError = useEvaluationAccessError(context.error)

  useEffect(() => {
    if (!context.data) return
    setFrameReady(false)
    setTimedOut(false)
    const timer = window.setTimeout(() => setTimedOut(true), 10_000)
    function receive(event: MessageEvent): void {
      if (
        event.origin !== window.location.origin ||
        event.source !== frameRef.current?.contentWindow ||
        !isReadyMessage(event.data)
      ) return
      window.clearTimeout(timer)
      setFrameReady(true)
    }
    window.addEventListener("message", receive)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener("message", receive)
    }
  }, [attempt, context.data])

  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <EvaluationLoading label="正在连接系统评价" />
  }
  if (context.isError) {
    return (
      <EvaluationError
        message={accessError.message}
        onRetry={() => void context.refetch()}
      />
    )
  }

  return (
    <div className="systemEvaluationPage">
      <header className="systemEvaluationHeader">
        <div>
          <p className="pageEyebrow">SYSTEM EVALUATION</p>
          <h1>系统评价</h1>
        </div>
        <a
          href={context.data.standalone_start_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          单独打开 <ExternalLink aria-hidden="true" />
        </a>
      </header>
      <section className="evaluationFrameShell">
        {!frameReady && !timedOut ? (
          <div className="evaluationFrameLoading" aria-live="polite">
            正在连接系统评价
          </div>
        ) : null}
        {timedOut && !frameReady ? (
          <div className="evaluationFrameError" role="alert">
            <strong>系统评价暂时无法连接</strong>
            <button type="button" onClick={() => setAttempt((value) => value + 1)}>
              重试
            </button>
          </div>
        ) : null}
        <iframe
          key={attempt}
          ref={frameRef}
          title="系统评价"
          src={context.data.embedded_start_url}
          className={frameReady ? "ready" : "waiting"}
          referrerPolicy="same-origin"
        />
      </section>
    </div>
  )
}
```

Do not add `sandbox` in this unit: the trusted same-origin proxied tool needs its HttpOnly Cookie, form POST and navigation bridge. The score-tool response CSP and ERP service authorization are the isolation controls.

- [ ] **Step 4: Register route and responsive frame layout**

Add the guarded route:

```tsx
{
  path: "/evaluations/system",
  element: (
    <PermissionRoute permission="evaluation_center.access">
      <SystemEvaluationPage />
    </PermissionRoute>
  ),
},
```

In `evaluations.css`, desktop frame height is `min(76vh, 900px)` with minimum 620px. At 767px and below, use the full available content width, minimum height `calc(100dvh - 190px)`, keep the standalone action 44px high, and leave room above `.shellMobileNavigation`. Do not use fixed widths.

- [ ] **Step 5: Run tests, build and commit**

```bash
npm --prefix apps/web test -- \
  src/evaluations/SystemEvaluationPage.test.tsx src/app/router.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: selected tests PASS; build and lint exit 0.

```bash
git add apps/web/src/evaluations/SystemEvaluationPage.tsx \
  apps/web/src/evaluations/SystemEvaluationPage.test.tsx \
  apps/web/src/evaluations/evaluations.css \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx
git commit -m "feat: embed the system evaluation shell"
```

### Task 6: Add the ERP login-continuation and form-post page

**Files:**

- Create: `apps/web/src/evaluations/EvaluationConnectPage.tsx`
- Create: `apps/web/src/evaluations/EvaluationConnectPage.test.tsx`
- Modify: `apps/web/src/evaluations/evaluations.css`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/auth/LoginPage.tsx`
- Modify: `apps/web/src/auth/LoginPage.test.tsx`

- [ ] **Step 1: Write failing safe-continuation tests**

Add to `LoginPage.test.tsx` and router tests:

```tsx
it("returns to an evaluation connection after successful login", async () => {
  const requestId = "90000000-0000-4000-8000-000000000001"
  const router = renderRouter({
    initialEntries: [
      {
        pathname: "/login",
        state: { returnTo: `/evaluation-connect/${requestId}` },
      },
    ],
  })
  await submitValidLogin()
  await waitFor(() =>
    expect(router.state.location.pathname).toBe(`/evaluation-connect/${requestId}`),
  )
})

it.each([
  "https://attacker.example/",
  "//attacker.example/",
  "/people",
  "/evaluation-connect/not-a-uuid",
])("rejects unsafe login continuation %s", async (returnTo) => {
  const router = renderRouter({
    initialEntries: [{ pathname: "/login", state: { returnTo } }],
  })
  await submitValidLogin()
  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
})
```

The same matrix must pass after forced password change.

- [ ] **Step 2: Write failing form-post tests**

Create `EvaluationConnectPage.test.tsx`:

```tsx
it("creates one ticket and form-posts ticket plus state outside the URL", async () => {
  const submit = vi
    .spyOn(HTMLFormElement.prototype, "submit")
    .mockImplementation(() => undefined)
  renderConnectPage("90000000-0000-4000-8000-000000000001")

  const form = await screen.findByTestId("evaluation-callback-form")
  expect(form).toHaveAttribute(
    "action",
    "http://localhost:8010/auth/erp/callback",
  )
  expect(form).toHaveAttribute("method", "post")
  expect(within(form).getByDisplayValue("ticket-value")).toHaveAttribute(
    "name",
    "ticket",
  )
  expect(within(form).getByDisplayValue("state-value")).toHaveAttribute(
    "name",
    "state",
  )
  expect(window.location.search).not.toContain("ticket-value")
  expect(submit).toHaveBeenCalledTimes(1)
})
```

Add tests for invalid UUID (no API call), 404/410/409 error text, retry issuing only one new mutation, and ticket data being removed from the DOM after an error.

- [ ] **Step 3: Run login/connect tests and verify they fail**

```bash
npm --prefix apps/web test -- \
  src/evaluations/EvaluationConnectPage.test.tsx \
  src/auth/LoginPage.test.tsx src/app/router.test.tsx
```

Expected: FAIL because safe continuation and the connect page do not exist.

- [ ] **Step 4: Implement the only allowed post-login return helper**

In `router.tsx`:

```tsx
type LoginRouteState = { returnTo?: unknown }

const EVALUATION_CONNECT_PATH =
  /^\/evaluation-connect\/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function safeLoginReturn(state: unknown): string {
  if (!state || typeof state !== "object") return "/"
  const returnTo = (state as LoginRouteState).returnTo
  return typeof returnTo === "string" && EVALUATION_CONNECT_PATH.test(returnTo)
    ? returnTo
    : "/"
}
```

`LoginBoundary` and `PasswordBoundary` must read `useLocation().state`. When authenticated, login navigates to `safeLoginReturn(state)`; when password change completes, `ChangePasswordPage` receives the validated path as a prop and navigates there. No ordinary business path is accepted by this helper.

Create `EvaluationConnectBoundary` outside `AuthenticatedLayout`:

```tsx
function EvaluationConnectBoundary() {
  const { status } = useAuth()
  const location = useLocation()
  if (status === "loading") return <LoadingScreen />
  if (status === "anonymous") {
    return (
      <Navigate
        to="/login"
        state={{ returnTo: location.pathname }}
        replace
      />
    )
  }
  if (status === "force_password_change") {
    return (
      <Navigate
        to="/change-password"
        state={{ returnTo: location.pathname }}
        replace
      />
    )
  }
  return <EvaluationConnectPage />
}
```

Register `{ path: "/evaluation-connect/:requestId", element: <EvaluationConnectBoundary /> }` before the authenticated shell route.

- [ ] **Step 5: Implement auto-submit without putting the ticket in navigation state**

Create `EvaluationConnectPage.tsx`:

```tsx
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function EvaluationConnectPage() {
  const { requestId = "" } = useParams()
  const formRef = useRef<HTMLFormElement>(null)
  const startedRef = useRef(false)
  const submittedRef = useRef(false)
  const validRequest = UUID_V4.test(requestId)
  const ticket = useMutation({
    mutationFn: () => createEvaluationTicket(requestId),
  })

  useEffect(() => {
    startedRef.current = false
    submittedRef.current = false
  }, [requestId])

  useEffect(() => {
    if (!validRequest || startedRef.current) return
    startedRef.current = true
    ticket.mutate()
  }, [requestId, ticket.mutate, validRequest])

  useEffect(() => {
    if (!ticket.data || submittedRef.current || !formRef.current) return
    submittedRef.current = true
    formRef.current.submit()
  }, [ticket.data])

  if (!validRequest) return <EvaluationConnectError message="连接地址无效" />
  if (ticket.isError) {
    return (
      <EvaluationConnectError
        message="连接已失效，请返回评价页面重试"
        onRetry={() => {
          submittedRef.current = false
          ticket.reset()
          ticket.mutate()
        }}
      />
    )
  }
  if (!ticket.data) return <EvaluationConnectLoading />

  return (
    <main className="evaluationConnectPage">
      <p aria-live="polite">正在安全进入系统评价…</p>
      <form
        ref={formRef}
        data-testid="evaluation-callback-form"
        action={ticket.data.callback_url}
        method="post"
      >
        <input type="hidden" name="ticket" value={ticket.data.ticket} />
        <input type="hidden" name="state" value={ticket.data.state} />
      </form>
    </main>
  )
}
```

The `startedRef` guard is required; the test renders under React StrictMode and asserts only one ticket request.

- [ ] **Step 6: Run tests, build and commit**

```bash
npm --prefix apps/web test -- \
  src/evaluations/EvaluationConnectPage.test.tsx \
  src/auth/LoginPage.test.tsx src/app/router.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: selected tests PASS; build and lint exit 0.

```bash
git add apps/web/src/evaluations/EvaluationConnectPage.tsx \
  apps/web/src/evaluations/EvaluationConnectPage.test.tsx \
  apps/web/src/evaluations/evaluations.css \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/auth/LoginPage.tsx apps/web/src/auth/LoginPage.test.tsx
git commit -m "feat: add evaluation form post bridge"
```

### Task 7: Add score-tool configuration, signed cookies and ERP client

**Repository:** `/Users/skymoonzyj/Documents/game-score-tool`

**Files:**

- Create: `config.py`
- Create: `session_codec.py`
- Create: `erp_client.py`
- Create: `requirements-dev.txt`
- Create: `tests/conftest.py`
- Create: `tests/test_session_codec.py`
- Create: `tests/test_erp_client.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add the isolated score-tool test environment**

Create `requirements-dev.txt`:

```text
-r requirements.txt
pytest==8.3.5
httpx==0.27.2
```

Append the exact runtime dependency to `requirements.txt`:

```text
httpx==0.27.2
```

Create a local virtual environment without changing ERP dependencies:

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Expected: installation exits 0 and does not create `data/`, `uploads/` or `runtime/`.

Create `tests/conftest.py` before importing `main` in any test:

```python
from __future__ import annotations

import os

os.environ.setdefault("SCORE_TOOL_ENV", "test")
os.environ.setdefault("ERP_API_BASE_URL", "http://127.0.0.1:8000/api/v1")
os.environ.setdefault("ERP_WEB_BASE_URL", "http://127.0.0.1:5175")
os.environ.setdefault(
    "ERP_SERVICE_TOKEN", "evaluation-local-service-token-2026-change-me"
)
os.environ.setdefault(
    "SCORE_TOOL_SESSION_SECRET", "score-tool-session-secret-2026-change-me"
)
os.environ.setdefault("ERP_EMBEDDED_PATH", "/evaluation-tool")
os.environ.setdefault("ERP_ALLOWED_PARENT_ORIGIN", "http://127.0.0.1:5175")
os.environ.setdefault("SCORE_TOOL_COOKIE_SECURE", "false")
```

Later fixtures in the same file construct `ScoreToolSettings.from_env()`, `CookieCodec`, a fake ERP client and `TestClient(create_app(settings, fake_erp))`; they must not monkeypatch global network calls.

- [ ] **Step 2: Write failing configuration and cookie tests**

Create `tests/test_session_codec.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from config import ScoreToolSettings, validate_return_path
from session_codec import CookieCodec, CookieInvalid

NOW = datetime(2026, 7, 30, 8, 0, tzinfo=UTC)


def test_login_state_round_trips_without_browser_storage() -> None:
    codec = CookieCodec(b"s" * 32, clock=lambda: NOW)
    token = codec.encode_login_state(
        mode="standalone",
        nonce="nonce_abcdefghijklmnopqrstuvwxyz012345",
        return_path="/fps",
        expires_at=NOW + timedelta(minutes=5),
    )
    decoded = codec.decode_login_state(token)
    assert decoded.mode == "standalone"
    assert decoded.nonce == "nonce_abcdefghijklmnopqrstuvwxyz012345"
    assert decoded.return_path == "/fps"
    assert decoded.expires_at == NOW + timedelta(minutes=5)

    replacement = "A" if token[-1] != "A" else "B"
    with pytest.raises(CookieInvalid):
        codec.decode_login_state(token[:-1] + replacement)


def test_session_cookie_round_trips_and_rejects_tampering() -> None:
    codec = CookieCodec(b"s" * 32, clock=lambda: NOW)
    token = codec.encode_session(
        mode="embedded",
        session_id="90000000-0000-4000-8000-000000000001",
        expires_at=NOW + timedelta(minutes=30),
    )
    decoded = codec.decode_session(token)
    assert decoded.mode == "embedded"
    assert decoded.session_id == "90000000-0000-4000-8000-000000000001"
    assert decoded.expires_at == NOW + timedelta(minutes=30)

    replacement = "A" if token[-1] != "A" else "B"
    with pytest.raises(CookieInvalid):
        codec.decode_session(token[:-1] + replacement)


@pytest.mark.parametrize(
    "value",
    [
        "https://attacker.example/",
        "//attacker.example/",
        "/\\attacker",
        "/line\nbreak",
        "moba",
    ],
)
def test_return_path_rejects_external_or_ambiguous_values(value: str) -> None:
    with pytest.raises(ValueError):
        validate_return_path(value)
```

Add tests for expired login-state/session tokens, exact-key rejection, wrong version, invalid mode, login-state nonce validation, session UUID validation, cross-purpose token rejection, production requiring Secure cookies, URL credentials/query/fragment rejection, and embedded path `/evaluation-tool` normalization. Assert that a login state is at most 1024 characters and uses only Base64URL characters plus `.`.

- [ ] **Step 3: Write failing ERP client tests**

Create `tests/test_erp_client.py` using `httpx.MockTransport`:

```python
@pytest.mark.anyio
async def test_erp_client_uses_bearer_and_fixed_v1_paths() -> None:
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        if request.url.path.endswith("/login-requests"):
            return httpx.Response(
                201,
                json={
                    "request_id": "90000000-0000-4000-8000-000000000001",
                    "authorize_url": (
                        "http://127.0.0.1:5175/evaluation-connect/"
                        "90000000-0000-4000-8000-000000000001"
                    ),
                    "expires_at": "2026-07-30T08:05:00Z",
                },
            )
        raise AssertionError(request.url)

    client = ErpEvaluationClient(
        base_url="http://127.0.0.1:8000/api/v1",
        service_token="evaluation-local-service-token-2026-change-me",
        transport=httpx.MockTransport(handler),
    )
    result = await client.create_login_request(
        state=(
            "eyJleHBpcmVzX2F0IjoxNzg1Mzg5MTAwLCJtb2RlIjoiZW1iZWRkZWQiLCJub25jZSI6"
            "ImFiYyIsInJldHVybl9wYXRoIjoiLyIsInYiOjF9.signature_abcdefghijklmnopqrstuvwxyz"
        ),
        mode="embedded",
    )
    assert result.request_id == "90000000-0000-4000-8000-000000000001"
    assert observed[0].url.path == (
        "/api/v1/integrations/evaluations/v1/login-requests"
    )
    assert observed[0].headers["Authorization"] == (
        "Bearer evaluation-local-service-token-2026-change-me"
    )
```

Test all four client methods, 5-second timeout, non-JSON responses, 401/403 mapping to `ErpSessionInvalid`, 409/410 mapping to `ErpBridgeRejected`, 5xx/network mapping to `ErpUnavailable`, and response schema validation without logging response bodies.

- [ ] **Step 4: Run score-tool tests and verify they fail**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_session_codec.py tests/test_erp_client.py -q
```

Expected: FAIL because config, codec and client modules do not exist.

- [ ] **Step 5: Implement strict score-tool settings**

Create `config.py`:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


def _origin(value: str, *, name: str, allow_path: bool = False) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(f"{name} cannot include credentials, query, or fragment")
    if not allow_path and parsed.path not in {"", "/"}:
        raise ValueError(f"{name} must contain only an origin")
    if allow_path and (not parsed.path.startswith("/") or ".." in parsed.path):
        raise ValueError(f"{name} contains an invalid API path")
    return value.rstrip("/")


def _local_path(value: str) -> str:
    normalized = value.rstrip("/")
    if (
        not normalized.startswith("/")
        or normalized.startswith("//")
        or ".." in normalized
        or "\\" in normalized
        or "?" in normalized
        or "#" in normalized
    ):
        raise ValueError("ERP_EMBEDDED_PATH must be one absolute local path")
    return normalized


def validate_return_path(value: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or urlsplit(value).scheme
        or urlsplit(value).netloc
    ):
        raise ValueError("return_path must stay inside the score tool")
    return value


@dataclass(frozen=True)
class ScoreToolSettings:
    app_env: Literal["development", "test", "production"]
    erp_api_base_url: str
    erp_web_base_url: str
    erp_service_token: str
    session_secret: bytes
    embedded_path: str
    allowed_parent_origin: str
    cookie_secure: bool

    @classmethod
    def from_env(cls) -> "ScoreToolSettings":
        app_env = os.environ.get("SCORE_TOOL_ENV", "development")
        if app_env not in {"development", "test", "production"}:
            raise ValueError("SCORE_TOOL_ENV is invalid")
        service_token = os.environ["ERP_SERVICE_TOKEN"]
        session_secret = os.environ["SCORE_TOOL_SESSION_SECRET"].encode()
        if len(service_token) < 32 or len(session_secret) < 32:
            raise ValueError("score-tool secrets must be at least 32 bytes")
        cookie_secure = os.environ.get("SCORE_TOOL_COOKIE_SECURE", "true").lower()
        if cookie_secure not in {"true", "false"}:
            raise ValueError("SCORE_TOOL_COOKIE_SECURE must be true or false")
        settings = cls(
            app_env=app_env,
            erp_api_base_url=_origin(
                os.environ["ERP_API_BASE_URL"],
                name="ERP_API_BASE_URL",
                allow_path=True,
            ),
            erp_web_base_url=_origin(
                os.environ["ERP_WEB_BASE_URL"], name="ERP_WEB_BASE_URL"
            ),
            erp_service_token=service_token,
            session_secret=session_secret,
            embedded_path=_local_path(os.environ.get("ERP_EMBEDDED_PATH", "/evaluation-tool")),
            allowed_parent_origin=_origin(
                os.environ["ERP_ALLOWED_PARENT_ORIGIN"],
                name="ERP_ALLOWED_PARENT_ORIGIN",
            ),
            cookie_secure=cookie_secure == "true",
        )
        if not settings.cookie_secure and not all(
            urlsplit(url).hostname in {"127.0.0.1", "localhost", "::1"}
            for url in (
                settings.erp_api_base_url,
                settings.erp_web_base_url,
                settings.allowed_parent_origin,
            )
        ):
            raise ValueError("non-Secure cookies are allowed only on loopback")
        if settings.app_env == "production":
            if not settings.cookie_secure:
                raise ValueError("production requires Secure score-tool cookies")
            if any(
                urlsplit(url).scheme != "https"
                for url in (
                    settings.erp_api_base_url,
                    settings.erp_web_base_url,
                    settings.allowed_parent_origin,
                )
            ):
                raise ValueError("production score-tool URLs require HTTPS")
        return settings
```

- [ ] **Step 6: Implement canonical signed login state and session cookies**

Create `session_codec.py` using only stdlib. Encoding is `base64url(canonical JSON).base64url(HMAC)` with no padding. Define frozen `LoginState` and `SessionCookie` dataclasses. Reject unknown keys before constructing them. `LoginState` contains exactly `v`, `mode`, `nonce`, `return_path` and `expires_at`; it is carried in the ERP `state` field and is never stored separately in the browser.

Core methods:

```python
class CookieCodec:
    def __init__(
        self,
        secret: bytes,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("cookie secret must be at least 32 bytes")
        self.secret = secret
        self.clock = clock or (lambda: datetime.now(UTC))

    def _sign(self, purpose: bytes, payload: bytes) -> bytes:
        return hmac.new(
            self.secret,
            b"game-score-v1\x00" + purpose + b"\x00" + payload,
            hashlib.sha256,
        ).digest()

    def _encode(self, value: dict[str, object], *, purpose: bytes) -> str:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return f"{_b64(payload)}.{_b64(self._sign(purpose, payload))}"

    def _decode(self, token: str, *, purpose: bytes) -> dict[str, object]:
        try:
            payload_token, signature_token = token.split(".", 1)
            payload = _unb64(payload_token)
            signature = _unb64(signature_token)
            if not hmac.compare_digest(signature, self._sign(purpose, payload)):
                raise CookieInvalid("cookie signature is invalid")
            value = json.loads(payload)
        except (ValueError, UnicodeError, json.JSONDecodeError):
            raise CookieInvalid("cookie is invalid") from None
        if not isinstance(value, dict):
            raise CookieInvalid("cookie is invalid")
        return value
```

`encode_login_state()` accepts a freshly generated nonce, mode, validated return path and an expiry no more than five minutes ahead; `decode_login_state()` uses purpose `b"login-state"`, requires the exact keys, validates the nonce and local path, and rejects expired or over-1024-character tokens. `encode_session()`/`decode_session()` use purpose `b"session-cookie"`, the frozen payload in this plan, and validate UUID plus aware expiry. Never use pickle, unsigned JSON or the same signing purpose for both token types.

- [ ] **Step 7: Implement the typed ERP HTTP client**

Create `erp_client.py` with frozen dataclasses matching the ERP schemas. Use one `httpx.AsyncClient` and never include browser cookies:

```python
class ErpEvaluationClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {service_token}"},
            timeout=httpx.Timeout(5.0),
            transport=transport,
        )

    async def _request(self, method: str, path: str, **kwargs: object) -> dict[str, object]:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ErpUnavailable("ERP is unavailable") from exc
        if response.status_code in {401, 403}:
            raise ErpSessionInvalid("ERP session is invalid")
        if response.status_code in {409, 410, 422}:
            raise ErpBridgeRejected("ERP bridge request was rejected")
        if response.status_code >= 500:
            raise ErpUnavailable("ERP is unavailable")
        if not response.is_success:
            raise ErpProtocolError("unexpected ERP response")
        try:
            value = response.json()
        except ValueError:
            raise ErpProtocolError("ERP returned non-JSON data") from None
        if not isinstance(value, dict):
            raise ErpProtocolError("ERP returned an invalid payload")
        return value
```

Implement exact methods `capabilities()`, `create_login_request(state, mode)`, `exchange_ticket(ticket)`, and `session_context(session_id)`. Each validates required keys and types before returning a dataclass. Add `aclose()` for app shutdown.

- [ ] **Step 8: Run score-tool unit tests and commit**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_session_codec.py tests/test_erp_client.py -q
```

Expected: all selected tests PASS.

```bash
git add config.py session_codec.py erp_client.py requirements.txt \
  requirements-dev.txt tests/conftest.py tests/test_session_codec.py \
  tests/test_erp_client.py
git commit -m "feat: add ERP bridge client"
```

### Task 8: Enforce ERP authentication in the score tool

**Repository:** `/Users/skymoonzyj/Documents/game-score-tool`

**Files:**

- Create: `integration.py`
- Create: `tests/test_integration_flow.py`
- Modify: `main.py`

- [ ] **Step 1: Write failing standalone and embedded flow tests**

Create `tests/test_integration_flow.py` using a fake async ERP client. The standalone journey must prove state binding, fixed form callback and cookie flags:

```python
def test_standalone_bridge_uses_signed_state_and_sets_ERP_bounded_session(
    app_client: TestClient,
    fake_erp: FakeErpClient,
    codec: CookieCodec,
) -> None:
    started = app_client.get(
        "/auth/erp/start?mode=standalone&return_path=%2Ffps",
        follow_redirects=False,
    )
    assert started.status_code == 303
    assert started.headers["location"].startswith(
        "http://127.0.0.1:5175/evaluation-connect/"
    )
    assert fake_erp.login_requests[0].mode == "standalone"
    signed_state = fake_erp.login_requests[0].state
    login_state = codec.decode_login_state(signed_state)
    assert login_state.mode == "standalone"
    assert login_state.return_path == "/fps"
    assert "set-cookie" not in started.headers

    callback = app_client.post(
        "/auth/erp/callback",
        data={
            "ticket": "ticket_abcdefghijklmnopqrstuvwxyz0123456789",
            "state": signed_state,
        },
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/fps"
    set_cookie = callback.headers.get_list("set-cookie")
    assert any(
        "game_score_session_standalone=" in value and "HttpOnly" in value
        for value in set_cookie
    )
    assert any("Path=/" in value and "SameSite=lax" in value for value in set_cookie)

    page = app_client.get("/fps")
    assert page.status_code == 200
    assert fake_erp.session_context_calls[-1] == fake_erp.session.session_id
```

The embedded test sends `X-Forwarded-Prefix: /evaluation-tool` and starts with `mode=embedded`; decode the posted state and assert its mode and return path, assert no temporary login Cookie is set, assert the session Cookie path is `/evaluation-tool`, callback redirect is `/evaluation-tool/`, rendered asset/navigation URLs preserve `/evaluation-tool`, and the ERP-selected callback is never supplied by a browser parameter.

Add tests for:

- missing, malformed, wrong-mode or tampered state returning 400/422 and never calling `exchange_ticket`;
- external, `//`, backslash and control-character `return_path` returning 422;
- expired signed state returning 400 without any Cookie dependency;
- consumed/expired ERP ticket returning a generic reconnect page with no ticket/state echo;
- missing page session redirecting to the correct standalone or embedded start URL;
- invalid/revoked delegated session redirecting HTML GET but returning JSON 401 to `/api/*`;
- `force_password_change` and permission loss producing the same session-invalid behavior;
- response headers containing `Cache-Control: no-store, private` and a `frame-ancestors` CSP with only self plus `ERP_ALLOWED_PARENT_ORIGIN`.

- [ ] **Step 2: Run the flow test and verify it fails**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_integration_flow.py -q
```

Expected: FAIL because the integration router/middleware and app factory do not exist.

- [ ] **Step 3: Implement auth routes and Cookie helpers**

Create `integration.py` with only the long-lived mode-specific session Cookie constants and path helpers:

```python
SESSION_COOKIES = {
    "standalone": "game_score_session_standalone",
    "embedded": "game_score_session_embedded",
}
EMBEDDED_HEADER = "X-Forwarded-Prefix"


def request_mode(request: Request, settings: ScoreToolSettings) -> str:
    return (
        "embedded"
        if request.headers.get(EMBEDDED_HEADER) == settings.embedded_path
        else "standalone"
    )


def cookie_path(mode: str, settings: ScoreToolSettings) -> str:
    return settings.embedded_path if mode == "embedded" else "/"


def app_path(path: str, mode: str, settings: ScoreToolSettings) -> str:
    normalized = "/" + path.lstrip("/")
    return settings.embedded_path + normalized if mode == "embedded" else normalized
```

Build `create_integration_router()` with:

```python
@router.get("/health/live")
async def health() -> dict[str, str]:
    return {"status": "ok", "api_version": "v1"}


@router.get("/auth/erp/start")
async def start(
    request: Request,
    mode: Literal["standalone", "embedded"],
    return_path: str = "/",
) -> Response:
    validated_return = validate_return_path(return_path)
    observed_mode = request_mode(request, settings)
    if mode != observed_mode:
        raise HTTPException(status_code=400, detail="integration mode mismatch")
    state = codec.encode_login_state(
        mode=mode,
        nonce=secrets.token_urlsafe(32),
        return_path=validated_return,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    registered = await erp_client.create_login_request(state=state, mode=mode)
    response = RedirectResponse(registered.authorize_url, status_code=303)
    return private_response(response)


@router.post("/auth/erp/callback")
async def callback(
    request: Request,
    ticket: str = Form(min_length=32, max_length=256),
    state: str = Form(
        min_length=32,
        max_length=1024,
        pattern=r"^[A-Za-z0-9_.-]+$",
    ),
) -> Response:
    observed_mode = request_mode(request, settings)
    login_state = codec.decode_login_state(state)
    if login_state.mode != observed_mode:
        raise HTTPException(status_code=400, detail="login state mode is invalid")
    erp_session = await erp_client.exchange_ticket(ticket)
    session_token = codec.encode_session(
        mode=login_state.mode,
        session_id=erp_session.session_id,
        expires_at=erp_session.expires_at,
    )
    path = cookie_path(login_state.mode, settings)
    response = RedirectResponse(
        app_path(login_state.return_path, login_state.mode, settings), status_code=303
    )
    response.set_cookie(
        SESSION_COOKIES[login_state.mode],
        session_token,
        max_age=max(1, int((erp_session.expires_at - datetime.now(UTC)).total_seconds())),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=path,
    )
    return private_response(response)
```

Catch `CookieInvalid` from `decode_login_state()` and return a generic 400 reconnect page before calling ERP. Map ERP
availability to a generic 503 reconnect page. Never render ticket or state in an error template.

- [ ] **Step 4: Add request-time delegated-session middleware**

`ErpSessionMiddleware` excludes `/static/*`, `/health/*` and `/auth/erp/*`. For every other request:

1. 根据固定代理前缀选择 `game_score_session_standalone` 或 `game_score_session_embedded` 并解码；本地验收的独立地址使用 `localhost`，内嵌地址经 ERP 的 `127.0.0.1` 同源代理进入。
2. Call ERP `session_context(decoded.session_id)`.
3. Require returned `session_id` and expiry to match the signed Cookie.
4. Set `scope["state"]["erp_context"]`, `mode` and `app_base`.
5. On failure, delete the session Cookie. For `/api/*`, return JSON 401 `erp_session_invalid`; for HTML GET, redirect to `app_path(f"/auth/erp/start?mode={mode}&return_path=%2F", mode, settings)`.

Use this exact API error body without ERP details:

```python
JSONResponse(
    status_code=401,
    content={
        "code": "erp_session_invalid",
        "detail": "ERP 登录已失效，请重新连接",
    },
)
```

Add `ScoreToolSecurityHeadersMiddleware` that sets:

```text
Cache-Control: no-store, private
Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'self' <ERP_ALLOWED_PARENT_ORIGIN>; base-uri 'self'
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
```

Only use the already validated configured parent origin in CSP.

- [ ] **Step 5: Refactor `main.py` into an app factory without changing algorithms**

Perform these mechanical changes:

1. Replace global route decorators `@app.get/post/delete` with `@legacy_router.get/post/delete`.
2. Remove `db.init_db()`, `os.makedirs(UPLOAD_DIR, exist_ok=True)` and `app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")`.
3. Keep `database.py`, `scorer.py` and `ocr_utils.py` imports temporarily because legacy handler bodies still reference them, but middleware will block those handlers in Task 9.
4. Build the app at the bottom:

```python
def create_app(
    settings: ScoreToolSettings | None = None,
    erp_client: ErpEvaluationClient | None = None,
) -> FastAPI:
    resolved = settings or ScoreToolSettings.from_env()
    client = erp_client or ErpEvaluationClient(
        base_url=resolved.erp_api_base_url,
        service_token=resolved.erp_service_token,
    )
    application = FastAPI(title="Game Score Tool")
    application.state.score_settings = resolved
    application.state.erp_client = client
    application.mount(
        "/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"
    )
    application.include_router(
        create_integration_router(
            settings=resolved,
            erp_client=client,
            codec=CookieCodec(resolved.session_secret),
        )
    )
    application.include_router(legacy_router)
    application.add_middleware(
        ErpSessionMiddleware,
        settings=resolved,
        erp_client=client,
        codec=CookieCodec(resolved.session_secret),
    )
    application.add_middleware(
        ScoreToolSecurityHeadersMiddleware,
        allowed_parent_origin=resolved.allowed_parent_origin,
    )
    return application


app = create_app()
```

Tests pass a fake ERP client and do not need a real network or database.

- [ ] **Step 6: Run bridge/regression tests and commit**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_session_codec.py tests/test_erp_client.py \
  tests/test_integration_flow.py -q
.venv/bin/python -m py_compile main.py integration.py config.py session_codec.py erp_client.py
```

Expected: selected tests PASS; Python compilation exits 0; no `data/`, `uploads/` or `runtime/` directory is created by importing `main` in tests.

```bash
git add main.py integration.py tests/test_integration_flow.py
git commit -m "feat: require ERP score tool sessions"
```

### Task 9: Convert score-tool pages to a base-aware read-only shell

**Repository:** `/Users/skymoonzyj/Documents/game-score-tool`

**Files:**

- Create: `templates/_integration_banner.html`
- Create: `static/embed.js`
- Create: `tests/test_read_only_shell.py`
- Modify: `main.py`
- Modify: `integration.py`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Modify: `templates/index.html`
- Modify: `templates/moba.html`
- Modify: `templates/fps.html`
- Modify: `templates/media.html`
- Modify: `templates/search.html`
- Modify: `templates/snapshot.html`

- [ ] **Step 1: Write failing tests that prove legacy data cannot escape**

Create `tests/test_read_only_shell.py`:

```python
def test_import_and_authenticated_pages_do_not_create_or_read_legacy_data(
    tmp_path: Path,
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    index = authenticated_client.get("/")
    assert index.status_code == 200
    assert "旧历史数据不会迁入" in index.text
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "uploads").exists()
    assert not (tmp_path / "runtime").exists()

    assert authenticated_client.get("/media").status_code == 200
    assert authenticated_client.get("/search?q=secret-player").status_code == 200
    assert "secret-player" not in authenticated_client.get("/search?q=secret-player").text
    assert authenticated_client.get("/match/1/snapshot").status_code == 410
    assert authenticated_client.get("/uploads/legacy.png").status_code == 404
```

Test every legacy API method with an authenticated session:

```python
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/moba/submit"),
        ("POST", "/api/fps/submit"),
        ("POST", "/api/analyze-screenshot"),
        ("POST", "/api/player/add"),
        ("GET", "/api/player/player-1"),
        ("GET", "/api/search?q=player-1"),
        ("GET", "/api/fps/history-summary?player_id=player-1"),
        ("DELETE", "/api/match/1"),
    ],
)
def test_every_legacy_api_is_stably_blocked(
    authenticated_client: TestClient,
    method: str,
    path: str,
) -> None:
    response = authenticated_client.request(method, path)
    assert response.status_code == 503
    assert response.json() == {
        "code": "integration_phase_unavailable",
        "detail": "截图、评分和历史功能尚未在 ERP 中开放",
    }
```

Add HTML assertions for:

- the authenticated ERP display name appears but email/account ID/permission list do not;
- all form controls, image inputs, video inputs and submit buttons are disabled;
- video help text is exactly “GPU 服务尚未开放”；
- direct pages use `/static/style.css`, `/static/app.js`, `/moba`, `/fps` links;
- embedded pages use `/evaluation-tool/static/style.css`, `/evaluation-tool/static/app.js`, `/evaluation-tool/moba`, `/evaluation-tool/fps` links;
- no template contains a root-absolute `href="/`, `src="/`, `action="/` or inline `<script>` block after rendering in embedded mode;
- Jinja autoescape converts a crafted display name such as `<img src=x onerror=alert(1)>` to text;
- `embed.js` posts only `{type: "game-score-tool.ready", api_version: "v1"}` to the configured parent when mode is embedded.

- [ ] **Step 2: Run the read-only test and verify it fails**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_read_only_shell.py -q
```

Expected: FAIL because legacy API, database initialization, public media and absolute paths still exist.

- [ ] **Step 3: Block all legacy API handlers after ERP session validation**

In `ErpSessionMiddleware`, after a valid ERP context is stored and before calling the app:

```python
if path.startswith("/api/"):
    return await JSONResponse(
        status_code=503,
        content={
            "code": "integration_phase_unavailable",
            "detail": "截图、评分和历史功能尚未在 ERP 中开放",
        },
        headers={"Cache-Control": "no-store, private"},
    )(scope, receive, send)
```

The auth check stays first: anonymous API calls return 401, authenticated calls return 503. Do not delete legacy handler functions in this unit; later scoring work will replace them with ERP calls.

- [ ] **Step 4: Make Jinja rendering safe and context-driven**

Change the environment and render helper in `main.py`:

```python
from jinja2 import Environment as Jinja2Env
from jinja2 import FileSystemLoader as FsLoader
from jinja2 import select_autoescape

_jinja_env = Jinja2Env(
    loader=FsLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)


def render(name: str, request: Request, **context: object) -> HTMLResponse:
    erp_context = request.state.erp_context
    mode = request.state.mode
    app_base = request.state.app_base
    template = _jinja_env.get_template(name)
    html = template.render(
        request=request,
        erp_user=erp_context,
        integration_phase="page_shell",
        mode=mode,
        app_base=app_base,
        allowed_parent_origin=request.app.state.score_settings.allowed_parent_origin,
        **context,
    )
    return HTMLResponse(html, headers={"Cache-Control": "no-store, private"})
```

Change page handlers so they never call `database.py`:

```python
@legacy_router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return render("index.html", request, players=[])


@legacy_router.get("/moba", response_class=HTMLResponse)
async def moba_page(request: Request):
    return render(
        "moba.html",
        request,
        games=GAME_OPTIONS["moba"],
        positions_by_game=POSITIONS_BY_GAME,
        player_id="",
        edit_data=None,
        edit_id=0,
        today=date.today().isoformat(),
    )


@legacy_router.get("/fps", response_class=HTMLResponse)
async def fps_page(request: Request):
    return render(
        "fps.html",
        request,
        games=GAME_OPTIONS["fps"],
        player_id="",
        edit_data=None,
        edit_id=0,
        today=date.today().isoformat(),
    )


@legacy_router.get("/media", response_class=HTMLResponse)
async def media_page(request: Request):
    return render("media.html", request, media_items=[])


@legacy_router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    return render("search.html", request, players=[], q="", game_type="", matches=[])


@legacy_router.get("/match/{match_id}/snapshot", response_class=HTMLResponse)
async def match_snapshot(request: Request, match_id: int):
    del match_id
    return HTMLResponse(
        "旧历史数据不会迁入，当前没有可显示的评价快照。",
        status_code=410,
        headers={"Cache-Control": "no-store, private"},
    )
```

Confirm the exact `app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")` call, `db.init_db()` and upload-directory creation are absent; remove any one still present.

- [ ] **Step 5: Make every template base-aware and visibly read-only**

Create `_integration_banner.html`:

```html
<section class="integration-banner" role="status">
  <div>
    <strong>已连接竞学 ERP</strong>
    <span>{{ erp_user.display_name }}</span>
  </div>
  <p>当前开放页面预览；截图、评分和历史功能尚未开放。</p>
</section>
```

Every template must:

1. Use `<body data-app-base="{{ app_base }}" data-integration-phase="{{ integration_phase }}" data-mode="{{ mode }}" data-parent-origin="{{ allowed_parent_origin }}">`.
2. Include `_integration_banner.html` immediately after the header.
3. Change asset links to `{{ app_base }}/static/style.css`, `{{ app_base }}/static/app.js` and `{{ app_base }}/static/embed.js`.
4. Change navigation to `{{ app_base }}/`, `{{ app_base }}/moba`, `{{ app_base }}/fps`, `{{ app_base }}/media`, `{{ app_base }}/search`.
5. Remove inline `<script>` blocks. Move the FPS field-toggle logic into `static/app.js`; remove unused media filtering in the empty media state.
6. Wrap each MOBA/FPS form body in `<fieldset class="integration-read-only" disabled>`.
7. Replace video hint text with `GPU 服务尚未开放` and remove language promising accepted video formats.
8. Render media/search/snapshot as empty explanatory states without links that add/edit/delete records.

Do not use `|safe` for ERP names, player IDs or any request value.

- [ ] **Step 6: Make JavaScript base-aware and incapable of submission in shell mode**

At the top of `static/app.js`:

```javascript
(function () {
    'use strict';

    function appUrl(path) {
        const base = document.body?.dataset.appBase || '';
        const normalized = path.startsWith('/') ? path : `/${path}`;
        return `${base}${normalized}`;
    }

    function pageShellIsReadOnly() {
        return document.body?.dataset.integrationPhase === 'page_shell';
    }

    document.addEventListener('DOMContentLoaded', function () {
        if (pageShellIsReadOnly()) {
            document.querySelectorAll('form').forEach((form) => {
                form.addEventListener('submit', (event) => event.preventDefault());
            });
            document
                .querySelectorAll('form input, form select, form textarea, form button')
                .forEach((control) => { control.disabled = true; });
            return;
        }
        initializeLegacyInteractions();
    });
})();
```

Move existing DOMContentLoaded work into `initializeLegacyInteractions()`. For every existing `fetch` whose first argument begins with `/api/`, pass that same concrete path through `appUrl()`; for example, replace `fetch('/api/analyze-screenshot', options)` with `fetch(appUrl('/api/analyze-screenshot'), options)` and replace the history-summary template string with `appUrl('/api/fps/history-summary?' + query.toString())`.

Create `static/embed.js`:

```javascript
document.addEventListener('DOMContentLoaded', function () {
    const body = document.body;
    if (!body || body.dataset.mode !== 'embedded') return;
    const targetOrigin = body.dataset.parentOrigin;
    if (!targetOrigin) return;
    window.parent.postMessage(
        { type: 'game-score-tool.ready', api_version: 'v1' },
        targetOrigin,
    );
});
```

- [ ] **Step 7: Add read-only and responsive styling**

Add styles for `.integration-banner`, `.integration-read-only`, disabled inputs/buttons and empty media/search states. At 767px and below, the existing two-column game forms must become one column, navigation must wrap without horizontal overflow, and every remaining link must have a 44px hit area. Do not hide overflow globally.

- [ ] **Step 8: Run all score-tool tests and inspect generated paths**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest -q
.venv/bin/python -m py_compile main.py integration.py config.py session_codec.py erp_client.py
! rg -n 'app\.mount\("/uploads"|db\.init_db\(\)|href="/|src="/' \
  main.py static templates
! rg -n "fetch\\((['\"])/api" static/app.js
! rg -nUP '<script(?:\s[^>]*)?>\s*(?!</script>)\S' templates
```

Expected: pytest PASS; compile exits 0; the final scans return no forbidden public uploads, SQLite initialization, root-absolute resources, root-absolute API fetches or inline script blocks. External `<script src>` tags do not match the final PCRE2 scan.

- [ ] **Step 9: Commit the read-only score-tool shell**

```bash
git add main.py integration.py static/app.js static/style.css static/embed.js \
  templates tests/test_read_only_shell.py
git commit -m "feat: add ERP connected score tool shell"
```

### Task 10: Add cross-repository contracts and local three-service E2E

**ERP files:**

- Create: `apps/api/tests/contracts/evaluation_integration_v1.json`
- Create: `apps/web/tests/evaluation-page-integration.spec.ts`
- Create: `scripts/run-evaluation-page-e2e.sh`
- Modify: `apps/api/tests/api/test_evaluation_integration.py`
- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/tests/helpers.ts`

**Score-tool files:**

- Create: `/Users/skymoonzyj/Documents/game-score-tool/tests/contracts/evaluation_integration_v1.json`
- Create: `/Users/skymoonzyj/Documents/game-score-tool/tests/test_contract.py`

- [ ] **Step 1: Write the frozen machine-readable contract in both repositories**

Create identical JSON files:

```json
{
  "api_version": "v1",
  "account_endpoints": {
    "context": "GET /api/v1/evaluations/context",
    "issue_ticket": "POST /api/v1/evaluations/bridge/tickets"
  },
  "service_endpoints": {
    "capabilities": "GET /api/v1/integrations/evaluations/v1/capabilities",
    "login_request": "POST /api/v1/integrations/evaluations/v1/login-requests",
    "exchange_ticket": "POST /api/v1/integrations/evaluations/v1/login-tickets/exchange",
    "session_context": "POST /api/v1/integrations/evaluations/v1/session-context"
  },
  "features": ["auth_bridge", "page_shell"],
  "legacy_writes_enabled": false,
  "ready_message": {
    "type": "game-score-tool.ready",
    "api_version": "v1"
  },
  "modes": ["standalone", "embedded"]
}
```

The ERP API test loads its file and asserts every method/path appears in `create_app(test_settings).openapi()`. The score-tool contract test loads its copy and asserts the `ErpEvaluationClient` path constants, mode set and `embed.js` message exactly match.

- [ ] **Step 2: Run contract tests and verify the scorer test fails first**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest tests/test_contract.py -q
```

Expected before adding exported path constants: FAIL. Replace duplicated string literals in `erp_client.py` with:

```python
CAPABILITIES_PATH = "/integrations/evaluations/v1/capabilities"
LOGIN_REQUEST_PATH = "/integrations/evaluations/v1/login-requests"
TICKET_EXCHANGE_PATH = "/integrations/evaluations/v1/login-tickets/exchange"
SESSION_CONTEXT_PATH = "/integrations/evaluations/v1/session-context"
```

Run again and expect PASS.

- [ ] **Step 3: Extend deterministic E2E seed assertions**

The permission seed already grants entry by role; do not add account overrides. Extend `test_seed_e2e.py`:

```python
def test_evaluation_entry_is_seeded_by_role_only(db: Session) -> None:
    rbac = RbacService(db)
    assert rbac.decision_context(_account_id(db, "19990000001")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000003")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000005")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000016")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert not rbac.decision_context(_account_id(db, "19990000004")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
```

If `seed_e2e.py` contains a hard-coded permission-count assertion, update it for UUID 26; do not add evaluation content fixtures.

- [ ] **Step 4: Add the browser journey at all four existing viewports**

Create `evaluation-page-integration.spec.ts`:

```ts
import { expect, test } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  openNavigationItem,
} from "./helpers"

test("admin opens one ERP-authenticated score shell embedded and standalone", async ({
  page,
}) => {
  await loginAsAdmin(page)
  await openNavigationItem(page, "评价中心")
  await expect(page.getByRole("heading", { name: "评价中心" })).toBeVisible()
  await page.getByRole("link", { name: "打开系统评价" }).click()

  const frame = page.frameLocator('iframe[title="系统评价"]')
  await expect(frame.getByText("已连接竞学 ERP")).toBeVisible()
  await expect(frame.getByText("E2E平台管理员")).toBeVisible()
  await expect(frame.locator('input[type="file"]')).toBeDisabled()
  await expect(frame.getByText("GPU 服务尚未开放")).toBeVisible()
  await expect(frame.getByRole("heading", { name: "登录工作台" })).toHaveCount(0)

  const [standalone] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("link", { name: "单独打开" }).click(),
  ])
  await expect(standalone.getByText("已连接竞学 ERP")).toBeVisible()
  await expect(standalone.getByText("E2E平台管理员")).toBeVisible()
  await expect(standalone).toHaveURL(/localhost:8010/)

  const apiCookies = await page.context().cookies(
    "http://127.0.0.1:5175/api/v1/auth/me",
  )
  const embeddedCookies = await page.context().cookies(
    "http://127.0.0.1:5175/evaluation-tool/",
  )
  const standaloneCookies = await page.context().cookies(
    "http://localhost:8010/",
  )
  expect(apiCookies.map((cookie) => cookie.name)).toContain("erp_session")
  expect(embeddedCookies.map((cookie) => cookie.name)).not.toContain("erp_session")
  expect(embeddedCookies.map((cookie) => cookie.name)).toContain(
    "game_score_session_embedded",
  )
  expect(standaloneCookies.map((cookie) => cookie.name)).toEqual(
    expect.arrayContaining(["game_score_session_standalone"]),
  )
  expect(standaloneCookies.map((cookie) => cookie.name)).not.toContain(
    "erp_session",
  )

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationPage")
  await expectAboveMobileNavigation(page, ".evaluationFrameShell")
})

test("customer service has no evaluation entry by default", async ({ page }) => {
  await loginAsCustomerService(page)
  await expect(page.getByRole("link", { name: "评价中心" })).toHaveCount(0)
  await page.goto("/evaluations")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible()
})
```

On 360px, `评价中心` may be in the “更多” dialog. Add this helper and use it instead of `.first().click()`:

```ts
export async function openNavigationItem(page: Page, name: string): Promise<void> {
  const candidates = page.getByRole("link", { name })
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index)
    if (await candidate.isVisible()) {
      await candidate.click()
      return
    }
  }
  const more = page.getByRole("button", { name: "更多" })
  await expect(more).toBeVisible()
  await more.click()
  await page
    .getByRole("dialog", { name: "更多导航" })
    .getByRole("link", { name })
    .click()
}
```

- [ ] **Step 5: Create a Docker-free local runner**

Create `scripts/run-evaluation-page-e2e.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
score_repo=${SCORE_REPO:-/Users/skymoonzyj/Documents/game-score-tool}
: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"

case "$TEST_DATABASE_URL" in
  *_test|*_test\?*|*/erp_e2e|*/erp_e2e\?*) ;;
  *) echo "只允许名称以 _test 结尾或 erp_e2e 的数据库" >&2; exit 2 ;;
esac

: "${FIELD_ENCRYPTION_MASTER_KEY:?}"
: "${FIELD_LOOKUP_KEY:?}"
: "${SESSION_TOKEN_PEPPER:?}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?}"

export APP_ENV=test
export DATABASE_URL="$TEST_DATABASE_URL"
export SESSION_COOKIE_SECURE=false
export FIELD_ENCRYPTION_MASTER_KEY
export FIELD_LOOKUP_KEY
export SESSION_TOKEN_PEPPER
export EVALUATION_TOOL_SERVICE_TOKEN

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/evaluation-shell-e2e.XXXXXX")
pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  wait "${pids[@]:-}" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

wait_http() {
  local url=$1
  for _ in {1..120}; do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  echo "服务未就绪: $url" >&2
  return 1
}

cd "$repo_root/apps/api"
../../.venv/bin/alembic upgrade head
../../.venv/bin/python -m app.cli.seed_e2e

cd "$repo_root"
APP_ENV=test DATABASE_URL="$TEST_DATABASE_URL" SESSION_COOKIE_SECURE=false \
ERP_WEB_BASE_URL=http://127.0.0.1:5175 \
EVALUATION_TOOL_STANDALONE_BASE_URL=http://localhost:8010 \
EVALUATION_TOOL_EMBEDDED_PATH=/evaluation-tool \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
.venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 \
  >"$tmp_dir/api.log" 2>&1 &
pids+=("$!")

EVALUATION_TOOL_PROXY_TARGET=http://127.0.0.1:8010 \
npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5175 \
  >"$tmp_dir/web.log" 2>&1 &
pids+=("$!")

cd "$score_repo"
ERP_API_BASE_URL=http://127.0.0.1:8000/api/v1 \
ERP_WEB_BASE_URL=http://127.0.0.1:5175 \
ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
SCORE_TOOL_SESSION_SECRET=score-tool-session-secret-2026-change-me \
SCORE_TOOL_ENV=test \
ERP_EMBEDDED_PATH=/evaluation-tool \
ERP_ALLOWED_PARENT_ORIGIN=http://127.0.0.1:5175 \
SCORE_TOOL_COOKIE_SECURE=false \
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010 \
  >"$tmp_dir/score.log" 2>&1 &
pids+=("$!")

wait_http http://127.0.0.1:8000/api/v1/health/live
wait_http http://127.0.0.1:5175/login
wait_http http://127.0.0.1:8010/health/live

cd "$repo_root"
cmp apps/api/tests/contracts/evaluation_integration_v1.json \
  "$score_repo/tests/contracts/evaluation_integration_v1.json"
E2E_BASE_URL=http://127.0.0.1:5175 \
npm --prefix apps/web run test:e2e -- evaluation-page-integration.spec.ts
```

Use `apply_patch` to create the script, then `chmod +x scripts/run-evaluation-page-e2e.sh`. The script may delete only its own temporary log directory and terminate only PIDs it started.

- [ ] **Step 6: Run the contract and local E2E gates**

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest -q

cd "/Users/skymoonzyj/Documents/new project3"
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests/api/test_evaluation_integration.py \
  apps/api/tests/cli/test_seed_e2e.py -q
bash -n scripts/run-evaluation-page-e2e.sh
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool \
  scripts/run-evaluation-page-e2e.sh
```

Expected: both repositories' unit/contract tests PASS; shell syntax exits 0; Playwright reports 8 passes (two journeys across four viewport projects) and no failures.

- [ ] **Step 7: Commit contract and E2E work in each repository**

Score-tool commit:

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
git add erp_client.py tests/contracts/evaluation_integration_v1.json tests/test_contract.py
git commit -m "test: freeze ERP integration contract"
```

ERP commit:

```bash
cd "/Users/skymoonzyj/Documents/new project3"
git add apps/api/tests/contracts/evaluation_integration_v1.json \
  apps/api/tests/api/test_evaluation_integration.py \
  apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/web/tests/evaluation-page-integration.spec.ts apps/web/tests/helpers.ts \
  scripts/run-evaluation-page-e2e.sh
git commit -m "test: cover evaluation page integration"
```

### Task 11: Document the local workflow and run the full non-Docker gate

**ERP files:**

- Modify: `README.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`
- Modify: `docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md`

**Score-tool files:**

- Modify: `/Users/skymoonzyj/Documents/game-score-tool/README.txt`

- [ ] **Step 1: Write failing operations-document tests**

Extend `test_operations_docs.py`:

```python
def test_readme_documents_local_evaluation_integration_only() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    required = {
        "评价中心页面整合",
        "game-score-tool",
        "http://localhost:8010",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "SCORE_TOOL_SESSION_SECRET",
        "scripts/run-evaluation-page-e2e.sh",
        "截图、评分、日报、周报和营期问卷尚未开放",
        "Docker/NAS 仍延期到全部 ERP 项目完成后",
    }
    assert required <= {line.strip() for line in readme.splitlines()} | {
        phrase for phrase in required if phrase in readme
    }
```

Add an assertion that README does not claim video, GPU, Docker, NAS, uploads or scoring are complete.

- [ ] **Step 2: Run the documentation test and verify it fails**

```bash
.venv/bin/pytest apps/api/tests/cli/test_operations_docs.py -q
```

Expected: FAIL because the evaluation local workflow is not documented.

- [ ] **Step 3: Document exact local startup and boundaries**

Add a README section containing these commands, with secrets represented as generated/local values rather than committed real credentials:

```bash
# Terminal 1: ERP API
APP_ENV=development DATABASE_URL="$DATABASE_URL" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
ERP_WEB_BASE_URL=http://127.0.0.1:5173 \
EVALUATION_TOOL_STANDALONE_BASE_URL=http://localhost:8010 \
.venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000

# Terminal 2: ERP Web
EVALUATION_TOOL_PROXY_TARGET=http://127.0.0.1:8010 \
npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5173

# Terminal 3: score tool
cd /Users/skymoonzyj/Documents/game-score-tool
ERP_API_BASE_URL=http://127.0.0.1:8000/api/v1 \
ERP_WEB_BASE_URL=http://127.0.0.1:5173 \
ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
SCORE_TOOL_SESSION_SECRET="$SCORE_TOOL_SESSION_SECRET" \
SCORE_TOOL_ENV=development \
ERP_EMBEDDED_PATH=/evaluation-tool \
ERP_ALLOWED_PARENT_ORIGIN=http://127.0.0.1:5173 \
SCORE_TOOL_COOKIE_SECURE=false \
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010
```

Document exact available behavior: evaluation navigation, center page, standalone/embedded score-tool shell and ERP login bridge. State on its own line: `截图、评分、日报、周报和营期问卷尚未开放` and `Docker/NAS 仍延期到全部 ERP 项目完成后`.

Update score-tool `README.txt` with the same environment variables, fixed ERP API v1 contract, read-only shell behavior, and explicit instruction not to restore `db.init_db()` or `/uploads` while ERP is the master record.

Update the design status line to:

```markdown
- **状态：** 书面规格已确认；第一实施单元计划已编写，待执行
```

Add a link to this plan in design section 20.

- [ ] **Step 4: Run every unit, integration and static gate in both repositories**

ERP:

```bash
cd "/Users/skymoonzyj/Documents/new project3"
TEST_DATABASE_URL="${TEST_DATABASE_URL:?必须使用隔离测试库}" \
  .venv/bin/pytest apps/api/tests -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint
bash -n scripts/db-schema-manifest.sh
bash -n scripts/run-evaluation-page-e2e.sh
git diff --check
```

Score tool:

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
.venv/bin/pytest -q
python -m py_compile main.py integration.py config.py session_codec.py erp_client.py
git diff --check
```

Expected: all tests PASS; Ruff, mypy, TypeScript build, ESLint, Python compilation, shell syntax and whitespace checks exit 0.

- [ ] **Step 5: Run the complete local browser gate without Docker**

```bash
cd "/Users/skymoonzyj/Documents/new project3"
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool \
  scripts/run-evaluation-page-e2e.sh
```

Expected: 8 Playwright tests pass. Record that Docker/Compose/NAS was not invoked and therefore is not part of the result.

- [ ] **Step 6: Prove deployment files were not changed**

ERP:

```bash
git diff --name-only main...HEAD | \
  rg '(^|/)(compose([^/]*)\.ya?ml|Dockerfile|nginx\.conf)$'
```

Expected: no output and `rg` exit 1. Inspect `git status --short` and preserve any unrelated user changes.

Score tool:

```bash
git diff --name-only 3b3929fcbfcde2db1da453856b96a1a8f537bba8...HEAD | \
  rg '(^|/)(Dockerfile|compose([^/]*)\.ya?ml)$'
```

Expected: no output and `rg` exit 1.

- [ ] **Step 7: Request code and specification review**

Use `superpowers:requesting-code-review`. Reviewers must separately check:

1. ERP bridge ticket single-use, encrypted temporary secret and live session/permission recheck.
2. ERP `/api` Cookie scope, score-tool host/path/mode separation, signed state validation, fixed callbacks and no token leakage.
3. No SQLite/public uploads/legacy API access.
4. Four viewport layout and fixed-origin ready handshake.
5. Both repositories match the frozen JSON contract and no Docker/NAS files changed.

Fix all blocking findings, rerun the affected focused test and then rerun the full gates above.

- [ ] **Step 8: Commit final documentation in each repository**

Score tool:

```bash
cd /Users/skymoonzyj/Documents/game-score-tool
git add README.txt
git commit -m "docs: explain ERP score tool integration"
```

ERP:

```bash
cd "/Users/skymoonzyj/Documents/new project3"
git add README.md apps/api/tests/cli/test_operations_docs.py \
  docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md
git commit -m "docs: explain evaluation page integration"
```

## Appendix A: Full scope guardrails

- 本计划只实现设计规格第 20 节的第 1 单元：评价中心导航与页面外壳、独立/内嵌入口、ERP 无感登录和集成 API v1 骨架。
- 本计划不实现截图接收、媒体目录、OCR、评分、结果保存、自动发布、人工审核、游戏 ID、辅助手机号、工作人员服务历史、日报、周报、结营问卷或打印。
- `game-score-tool` 的 `data/`、`uploads/`、`runtime/`、旧 SQLite 内容和历史媒体不复制、不读取、不删除；最终删除继续等待正式切换清单和单独确认。
- 旧 `/api/moba/*`、`/api/fps/*`、`/api/analyze-screenshot`、玩家查询和删除接口在本单元必须返回稳定的 `503 integration_phase_unavailable`，不能继续写 SQLite。
- `/uploads` 不再作为公开静态目录挂载；`main.py` 不调用 `db.init_db()`。旧算法文件保留原样，供后续系统评价处理单元接入。
- 录像入口显示“GPU 服务尚未开放”且不可选文件；截图、表单提交和历史入口显示“下一实施单元开放”并不可操作。
- ERP PostgreSQL 只保存短期登录请求和单次票据元数据；评分工具不建立账号表、正式业务库或第二份人员权限数据。
- `evaluation_center.access` 只允许进入页面和建立登录桥接，不授予任何评价内容、上传、审核、原图或实名权限。客服默认没有该权限。
- 所有浏览器返回、桥接接口、评价上下文和评分工具页面使用 `Cache-Control: no-store, private`；票据、state、Cookie 和服务凭据不得写入日志或审计元数据。
- 当前只做本地开发。不得修改任何 Compose 文件、Dockerfile、NAS 配置、生产镜像或容器测试脚本。
- 固定验收宽度为 1440、1024、768、360；内嵌页不得出现双层导航遮挡、页面级横向滚动或手机端不可达操作。

## Appendix B: Fixed repositories and revisions

实施时使用两个独立 Git 检出目录：

```text
ERP_REPO=/Users/skymoonzyj/Documents/new project3
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool
```

评分工具基线固定为 GitHub `skymoonzyj/game-score-tool` 的提交：

```text
3b3929fcbfcde2db1da453856b96a1a8f537bba8
```

执行前使用 `superpowers:using-git-worktrees` 为 ERP 创建隔离工作树。若评分工具目录不存在，克隆并从固定提交创建 `codex/erp-integration-shell`；若已存在，先确认工作区干净且 `main` 包含固定提交，不能覆盖用户改动：

```bash
test -d "$ERP_REPO/.git"
if [[ ! -d "$SCORE_REPO/.git" ]]; then
  git clone https://github.com/skymoonzyj/game-score-tool.git "$SCORE_REPO"
fi
git -C "$SCORE_REPO" cat-file -e 3b3929fcbfcde2db1da453856b96a1a8f537bba8^{commit}
git -C "$SCORE_REPO" status --short
git -C "$SCORE_REPO" switch -c codex/erp-integration-shell 3b3929fcbfcde2db1da453856b96a1a8f537bba8
```

如果最后一条分支已存在，改为切换该分支并确认其合并基点是固定提交；不得用 `reset --hard` 清理现有工作。

## Appendix C: Frozen HTTP contracts

### ERP account-facing API

```text
GET  /api/v1/evaluations/context
POST /api/v1/evaluations/bridge/tickets
```

### ERP service-facing API

```text
GET  /api/v1/integrations/evaluations/v1/capabilities
POST /api/v1/integrations/evaluations/v1/login-requests
POST /api/v1/integrations/evaluations/v1/login-tickets/exchange
POST /api/v1/integrations/evaluations/v1/session-context
```

服务接口只接受：

```http
Authorization: Bearer <EVALUATION_TOOL_SERVICE_TOKEN>
```

浏览器不得得到该服务凭据。ERP 账号接口继续使用 `erp_session` Cookie 和 `X-CSRF-Token`；该 Cookie 的
`Path=/api`，不会发送到 `/evaluation-tool/*`，登录设置和退出删除必须使用同一路径。

### ERP Pydantic contracts

```python
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EvaluationToolMode = Literal["standalone", "embedded"]
EvaluationApiVersion = Literal["v1"]


class EvaluationCenterContextResponse(BaseModel):
    accessible: Literal[True] = True
    system_evaluation_available: Literal[True] = True
    embedded_start_url: str
    standalone_start_url: str
    upload_available: Literal[False] = False
    video_available: Literal[False] = False
    reports_available: Literal[False] = False
    surveys_available: Literal[False] = False
    integration_api_version: EvaluationApiVersion = "v1"


class EvaluationIntegrationCapabilities(BaseModel):
    api_version: EvaluationApiVersion = "v1"
    features: list[Literal["auth_bridge", "page_shell"]]
    legacy_writes_enabled: Literal[False] = False


class EvaluationLoginRequestCreate(BaseModel):
    client_id: Literal["game-score-tool"]
    mode: EvaluationToolMode
    state: str = Field(
        min_length=32,
        max_length=1024,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )


class EvaluationLoginRequestCreated(BaseModel):
    request_id: UUID
    authorize_url: str
    expires_at: datetime


class EvaluationTicketCreate(BaseModel):
    request_id: UUID


class EvaluationTicketIssued(BaseModel):
    callback_url: str
    ticket: str
    state: str
    expires_at: datetime


class EvaluationTicketExchange(BaseModel):
    ticket: str = Field(min_length=32, max_length=256)


class EvaluationDelegatedSessionRequest(BaseModel):
    session_id: UUID


class EvaluationToolSessionContext(BaseModel):
    api_version: EvaluationApiVersion = "v1"
    account_id: UUID
    person_id: UUID
    display_name: str
    session_id: UUID
    expires_at: datetime
    page_shell_available: Literal[True] = True
    upload_available: Literal[False] = False
```

### Score-tool signed state and session cookie

`game_score_session_standalone` 和 `game_score_session_embedded` 是评分工具自己签名的 HttpOnly Cookie。浏览器
Cookie 不按端口隔离，因此本地独立页固定使用 `localhost`，内嵌页固定使用 ERP 的 `127.0.0.1` 主机；两种
模式仍分开命名并固定路径，防止配置变化或代理请求造成会话串用。两者载荷只能包含：

```json
{
  "v": 1,
  "mode": "standalone|embedded",
  "session_id": "ERP auth_sessions.id",
  "expires_at": 1785388800
}
```

登录 `state` 不是 Cookie，而是评分工具签名的无状态令牌，载荷只能包含：

```json
{
  "v": 1,
  "mode": "standalone|embedded",
  "nonce": "fresh random Base64URL value",
  "return_path": "/validated/local/path",
  "expires_at": 1785389100
}
```

ERP 将完整 `state` 作为不透明值加密保存并原样 form-post 回传；只有评分工具验证 HMAC、严格字段集合、
5 分钟到期、mode 和本地返回路径。`state` 最大 1024 字符，只允许 Base64URL、`.` 和 `-`；登录 state
与会话 Cookie 使用不同 HMAC purpose，防止跨用途替换。

Cookie 使用 `SCORE_TOOL_SESSION_SECRET` 做 HMAC-SHA256，独立模式路径为 `/`，内嵌模式路径为 `/evaluation-tool`。每次受保护页面请求都使用 `session_id` 经服务端调用 ERP `session-context`，因此 ERP 登出、账号停用、强制改密或 `evaluation_center.access` 被拒绝后立即失效。

### Browser flow

```text
score-tool /auth/erp/start
  -> service POST ERP /login-requests
  -> browser ERP /evaluation-connect/{request_id}
  -> ERP login when needed
  -> browser POST ERP /evaluations/bridge/tickets
  -> auto-submit form POST score-tool /auth/erp/callback
  -> service POST ERP /login-tickets/exchange
  -> score-tool sets the mode-specific game_score_session_* cookie
  -> return to validated local score-tool path
```

固定规则：

- 登录请求 5 分钟有效；票据 60 秒有效且只能消费一次。
- ERP 根据 `mode` 从配置选择固定回调地址；评分工具不得提交任意回调 URL。
- `return_path` 只保存在评分工具签名的短期 `state`，必须以单个 `/` 开头，禁止 `//`、反斜线、控制字符、scheme 和外部主机。
- ERP 登录后的继续地址只允许 `/evaluation-connect/<uuid>`；React Router state 不能成为开放跳转。
- 同一登录请求的票据创建在响应丢失时返回同一未消费票据；不同账号尝试复用请求返回 `409 bridge_request_conflict`。
- 票据交换成功后清空数据库内的票据密文；交换响应丢失时评分工具重新开始登录流程，不允许重放已消费票据。

## Appendix D: Full file map

### ERP create

- `apps/api/app/evaluations/__init__.py` — 评价领域包标记。
- `apps/api/app/evaluations/models.py` — 短期登录请求和票据模型。
- `apps/api/app/evaluations/schemas.py` — 冻结的账户端与服务端 DTO。
- `apps/api/app/evaluations/errors.py` — 稳定桥接错误类型。
- `apps/api/app/evaluations/config.py` — URL、固定回调和起始路径构造。
- `apps/api/app/evaluations/bridge_service.py` — state/票据、会话联动、权限复核和审计。
- `apps/api/app/evaluations/dependencies.py` — 服务依赖与 Bearer 服务认证。
- `apps/api/app/evaluations/router.py` — `/evaluations/*` 账号接口。
- `apps/api/app/evaluations/integration_router.py` — `/integrations/evaluations/v1/*` 服务接口。
- `apps/api/app/evaluations/routing.py` — 对评价响应统一追加 `no-store, private`。
- `apps/api/migrations/versions/0007_evaluation_page_integration.py` — 权限、默认授权及两张桥接表。
- `apps/api/tests/services/test_evaluation_bridge.py` — 桥接状态、单次票据、撤权和会话失效测试。
- `apps/api/tests/api/test_evaluation_integration.py` — HTTP、CSRF、Bearer、脱敏和缓存头契约。
- `apps/api/tests/contracts/evaluation_integration_v1.json` — ERP 侧冻结的跨仓库 v1 契约。
- `apps/web/src/evaluations/api.ts` — 评价上下文和票据 TypeScript 客户端。
- `apps/web/src/evaluations/api.test.ts` — 请求路径和表单字段契约。
- `apps/web/src/evaluations/useEvaluationAccessError.ts` — 撤权时清理当前账号评价缓存并安全回退。
- `apps/web/src/evaluations/EvaluationCenterPage.tsx` — 评价中心三分支外壳。
- `apps/web/src/evaluations/EvaluationCenterPage.test.tsx` — 入口、禁用边界和角色测试。
- `apps/web/src/evaluations/SystemEvaluationPage.tsx` — iframe、就绪握手、重试和独立打开。
- `apps/web/src/evaluations/SystemEvaluationPage.test.tsx` — 来源校验、超时和响应式行为测试。
- `apps/web/src/evaluations/EvaluationConnectPage.tsx` — 登录后票据创建和 form-post 回调。
- `apps/web/src/evaluations/EvaluationConnectPage.test.tsx` — 自动提交、错误和安全继续地址测试。
- `apps/web/src/evaluations/evaluations.css` — 1440/1024/768/360 评价页面布局。
- `apps/web/tests/evaluation-page-integration.spec.ts` — 独立/内嵌登录和四视口 E2E。
- `scripts/run-evaluation-page-e2e.sh` — 不使用 Docker 的本地三服务 E2E 启动器。

### ERP modify

- `docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md` — 保持“书面规格已确认”并链接本计划。
- `apps/api/app/core/config.py` — 本地 ERP/评分工具 URL、服务 token 和 TTL 配置。
- `apps/api/app/auth/dependencies.py` — 固定 `erp_session` 的 `/api` Cookie 路径。
- `apps/api/app/auth/router.py` — 登录设置与退出删除使用同一 `/api` 路径。
- `apps/api/app/rbac/seed.py` — `evaluation_center.access` 和最小默认授权。
- `apps/api/app/api/router.py` — 注册评价账号和集成路由。
- `apps/api/app/main.py` — 注册评价私有响应中间件。
- `apps/api/migrations/env.py` — 导入评价模型元数据。
- `apps/api/tests/conftest.py` — 固定测试 URL 和服务 token。
- `apps/api/tests/db/test_migrations.py` — `0007` 表、约束、权限和降级保护。
- `apps/api/tests/db/test_rbac_seed.py` — UUID、角色授权和客服默认拒绝。
- `apps/api/tests/api/conftest.py` — 评价角色旅程夹具。
- `apps/api/tests/api/test_auth_sessions.py` — ERP Cookie 不进入内嵌评分路径的回归测试。
- `scripts/db-schema-manifest.sh` — Alembic head 与桥接表清单。
- `.env.example` — 本地双服务变量示例，不增加容器配置。
- `apps/web/vite.config.ts` — 本地 `/evaluation-tool` 反向代理。
- `apps/web/src/app/router.tsx` — 评价路由、连接边界和安全登录继续。
- `apps/web/src/app/router.test.tsx` — 权限、匿名继续和强制改密路由。
- `apps/web/src/auth/LoginPage.tsx` — 登录/改密后恢复安全内部地址。
- `apps/web/src/auth/LoginPage.test.tsx` — 内部继续与外部地址拒绝。
- `apps/web/src/layout/AppShell.tsx` — 评价中心导航。
- `apps/web/src/layout/AppShell.test.tsx` — 桌面、手机和客服默认隐藏。
- `apps/web/src/portal/PortalDashboardPage.tsx` — 获权账号的评价工作卡。
- `apps/web/src/portal/PortalDashboardPage.test.tsx` — 评价卡与原门户共存。
- `apps/web/src/portal/portal.css` — 评价卡配色。
- `apps/api/app/cli/seed_e2e.py` — 固定评价入口权限与测试用户。
- `apps/api/tests/cli/test_seed_e2e.py` — 种子幂等和角色授权。
- `apps/api/tests/cli/test_operations_docs.py` — 本地运行说明和延期边界。
- `README.md` — 双仓库本地启动、功能边界和验证命令。

### Score tool create

- `/Users/skymoonzyj/Documents/game-score-tool/config.py` — 环境配置和 URL/路径校验。
- `/Users/skymoonzyj/Documents/game-score-tool/session_codec.py` — 无状态登录 `state` 与会话 Cookie 的分域 HMAC 编解码。
- `/Users/skymoonzyj/Documents/game-score-tool/erp_client.py` — ERP v1 服务端客户端和 DTO。
- `/Users/skymoonzyj/Documents/game-score-tool/integration.py` — 登录开始、回调、会话复核和旧 API 封闭。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/_integration_banner.html` — ERP 身份和阶段边界提示。
- `/Users/skymoonzyj/Documents/game-score-tool/static/embed.js` — 固定来源的 iframe ready 消息。
- `/Users/skymoonzyj/Documents/game-score-tool/requirements-dev.txt` — pytest 与测试 HTTP 客户端。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/conftest.py` — 测试配置、MockTransport、签名 state 和会话 Cookie。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/test_session_codec.py` — 篡改、过期、模式和路径测试。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/test_erp_client.py` — Bearer、路径、超时和错误映射测试。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/test_integration_flow.py` — 独立/内嵌、state、单次交换和撤权测试。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/test_read_only_shell.py` — 匿名拒绝、SQLite 封闭、媒体 404 和页面基础路径。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/contracts/evaluation_integration_v1.json` — 评分工具侧相同 v1 契约。
- `/Users/skymoonzyj/Documents/game-score-tool/tests/test_contract.py` — 客户端路径和 ready 消息契约测试。

### Score tool modify

- `/Users/skymoonzyj/Documents/game-score-tool/main.py` — app factory、桥接依赖、只读页面数据和移除公开 uploads。
- `/Users/skymoonzyj/Documents/game-score-tool/static/app.js` — 使用页面 `data-app-base` 构造接口 URL并尊重只读阶段。
- `/Users/skymoonzyj/Documents/game-score-tool/static/style.css` — 接入横幅、禁用控件和手机布局。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/index.html` — ERP 用户、相对资源和空历史。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/moba.html` — 只读壳、禁用截图/录像/提交。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/fps.html` — 只读壳、禁用三图/录像/提交。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/media.html` — 不读取旧媒体的未开放态。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/search.html` — 不读取旧 SQLite 的未开放态。
- `/Users/skymoonzyj/Documents/game-score-tool/templates/snapshot.html` — 不暴露旧快照。
- `/Users/skymoonzyj/Documents/game-score-tool/requirements.txt` — 固定 httpx 运行依赖。
- `/Users/skymoonzyj/Documents/game-score-tool/README.txt` — ERP 接入后的本地启动和禁用边界。

## Appendix E: Test environment

ERP 后端继续使用名称以 `_test` 结尾的隔离 PostgreSQL。所有命令必须显式提供：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://erp@127.0.0.1:55433/teaching_test'
export FIELD_ENCRYPTION_MASTER_KEY='AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
export FIELD_LOOKUP_KEY='Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA='
export SESSION_TOKEN_PEPPER='EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8='
export EVALUATION_TOOL_SERVICE_TOKEN='evaluation-local-service-token-2026-change-me'
export ERP_WEB_BASE_URL='http://127.0.0.1:5175'
export EVALUATION_TOOL_STANDALONE_BASE_URL='http://localhost:8010'
export EVALUATION_TOOL_EMBEDDED_PATH='/evaluation-tool'
```

评分工具本地测试使用：

```bash
export ERP_API_BASE_URL='http://127.0.0.1:8000/api/v1'
export ERP_WEB_BASE_URL='http://127.0.0.1:5175'
export ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN"
export SCORE_TOOL_SESSION_SECRET='score-tool-session-secret-2026-change-me'
export SCORE_TOOL_ENV='test'
export ERP_EMBEDDED_PATH='/evaluation-tool'
export ERP_ALLOWED_PARENT_ORIGIN='http://127.0.0.1:5175'
export SCORE_TOOL_COOKIE_SECURE='false'
```

本单元的完整本地门禁由普通进程运行 ERP API、ERP Web 和评分工具；不得调用 `docker compose`，不得把 Docker/NAS 标记为通过。

---
