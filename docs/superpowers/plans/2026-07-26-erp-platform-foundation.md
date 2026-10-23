# 电竞教育 ERP 平台基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付阶段 1A 的可运行平台基础：响应式 Web 外壳、FastAPI、PostgreSQL、人员与实名档案、账号登录、监护人关系、拒绝优先权限、敏感字段加密、审计和自动化测试框架。

**Architecture:** 使用单仓库的模块化单体：React 前端通过 `/api/v1` 调用 FastAPI，业务数据存 PostgreSQL。账号与现实人员分离；身份证和完整手机号采用字段级信封加密并保存不可逆检索摘要；服务端数据库会话、CSRF、对象级权限和只追加审计共同形成安全边界。阶段 1A 只实现平台与身份能力，不实现付款、营期/班级、评价和住宿业务。

**Tech Stack:** React 19、TypeScript、Vite、React Router、TanStack Query、Vitest、Playwright；Python 3.12、FastAPI、Pydantic Settings、SQLAlchemy 2、Alembic、PostgreSQL 16、Argon2id、cryptography、pytest；Docker Compose、Nginx。

---

## 计划边界

设计依据：[电竞教育学员 ERP 系统设计](../specs/2026-07-26-esports-student-erp-design.md)。

本计划完成后，管理员能够登录、维护人员/学员/工作人员/访客档案、绑定监护人、创建与停用账号、分配角色和账号级权限、查看审计记录，并在获权时以可审计方式查看敏感字段。桌面与手机浏览器均可操作。

本计划不创建项目、营期、班级、付款、教练评语、截图评测、房间或入住表。这些能力分别属于阶段 1B、2、3。

## 文件结构

```text
.
├── .editorconfig                         # 通用编辑格式
├── .env.example                          # 非敏感配置模板
├── README.md                             # 本地启动、测试和安全说明
├── compose.yaml                          # db/api/web/reverse-proxy
├── compose.prod.yaml                     # NAS 生产覆盖：不发布内部端口
├── compose.e2e.yaml                      # 隔离的端到端测试数据库和配置
├── infra/
│   └── nginx/nginx.conf                  # 同源 Web 与 /api 反向代理
├── apps/
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── alembic.ini
│   │   ├── migrations/
│   │   │   ├── env.py
│   │   │   └── versions/
│   │   ├── app/
│   │   │   ├── main.py                  # FastAPI 工厂和中间件
│   │   │   ├── api/router.py            # /api/v1 聚合路由
│   │   │   ├── core/config.py           # 严格环境配置和 Docker secret 读取
│   │   │   ├── core/database.py         # SQLAlchemy engine/session
│   │   │   ├── core/errors.py           # 统一 Problem Details
│   │   │   ├── core/request_id.py       # 请求追踪 ID
│   │   │   ├── security/encryption.py   # AES-GCM 信封加密与 HMAC 摘要
│   │   │   ├── security/passwords.py    # Argon2id
│   │   │   ├── auth/models.py           # user_accounts/auth_sessions
│   │   │   ├── auth/schemas.py
│   │   │   ├── auth/service.py
│   │   │   ├── auth/dependencies.py
│   │   │   ├── auth/router.py
│   │   │   ├── identity/models.py       # people 和人员类型档案
│   │   │   ├── identity/schemas.py
│   │   │   ├── identity/service.py
│   │   │   ├── identity/router.py
│   │   │   ├── rbac/models.py           # roles/permissions/bindings/overrides
│   │   │   ├── rbac/policy.py            # 拒绝优先判定
│   │   │   ├── rbac/router.py
│   │   │   ├── rbac/seed.py
│   │   │   ├── audit/models.py           # 只追加审计事件
│   │   │   ├── audit/service.py
│   │   │   ├── audit/router.py
│   │   │   └── cli/create_admin.py       # 首个管理员命令
│   │   └── tests/                         # 单元、API、迁移、权限和安全测试
│   └── web/
│       ├── Dockerfile
│       ├── package.json
│       ├── package-lock.json
│       ├── vite.config.ts
│       ├── vitest.config.ts
│       ├── playwright.config.ts
│       ├── src/
│       │   ├── main.tsx
│       │   ├── app/router.tsx
│       │   ├── app/providers.tsx
│       │   ├── api/client.ts
│       │   ├── auth/AuthProvider.tsx
│       │   ├── auth/LoginPage.tsx
│       │   ├── layout/AppShell.tsx
│       │   ├── layout/AppShell.css
│       │   ├── people/PeoplePage.tsx
│       │   ├── people/PersonForm.tsx
│       │   ├── people/PersonDetailPage.tsx
│       │   ├── settings/AccountsPage.tsx
│       │   ├── settings/PermissionsPage.tsx
│       │   ├── settings/AuditPage.tsx
│       │   └── styles/{tokens.css,global.css}
│       └── tests/                         # Vitest 与 Playwright
└── scripts/
    ├── check.sh                           # 后端、前端和 Compose 总验证
    ├── backup-db.sh                       # PostgreSQL 备份
    └── restore-db.sh                      # PostgreSQL 恢复演练
```

除非步骤明确切换目录，所有命令都从仓库根目录执行。首次创建 `.venv` 后，每个新的终端会话先执行：

```bash
export PATH="$PWD/.venv/bin:$PATH"
```

## Task 1：建立单仓库与可复现开发环境

**Files:**
- Modify: `.gitignore`
- Create: `.editorconfig`
- Create: `.env.example`
- Create: `README.md`
- Create: `compose.yaml`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/Dockerfile`
- Create: `apps/web/package.json`
- Create: `apps/web/Dockerfile`
- Create: `infra/nginx/nginx.conf`

- [ ] **Step 1：写入根目录配置和环境模板**

`.env.example` 必须包含真实键名但不包含可用秘密：

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://erp:erp@db:5432/erp
SESSION_COOKIE_SECURE=false
SESSION_TTL_MINUTES=480
FIELD_ENCRYPTION_MASTER_KEY=
FIELD_LOOKUP_KEY=
SESSION_TOKEN_PEPPER=
```

`.editorconfig` 使用 UTF-8、LF、末尾换行；Python 4 空格，TypeScript/CSS/JSON 2 空格。

- [ ] **Step 2：定义后端依赖**

`apps/api/pyproject.toml` 至少固定以下直接依赖和工具配置：

```toml
[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "esports-erp-api"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "fastapi>=0.140,<0.141",
  "uvicorn[standard]>=0.51,<0.52",
  "pydantic-settings>=2.14,<3",
  "sqlalchemy>=2.0.51,<2.1",
  "psycopg[binary]>=3.3.4,<3.4",
  "alembic>=1.18.5,<1.19",
  "argon2-cffi>=25.1,<26",
  "cryptography>=49,<50",
  "email-validator>=2.3,<3",
]

[project.optional-dependencies]
dev = [
  "httpx>=0.28.1,<0.29",
  "pytest>=9.1,<10",
  "pytest-cov>=7.1,<8",
  "ruff>=0.16,<0.17",
  "mypy>=2.3,<2.4",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
python_version = "3.12"
strict = true
plugins = ["pydantic.mypy"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 3：定义前端依赖和脚本**

`apps/web/package.json` 使用下列脚本名，后续任务统一调用：

```json
{
  "name": "esports-erp-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "test:e2e": "playwright test",
    "lint": "eslint ."
  },
  "dependencies": {
    "@hookform/resolvers": "^5.5.5",
    "@tanstack/react-query": "^5.101.4",
    "react": "^19.2.8",
    "react-dom": "^19.2.8",
    "react-hook-form": "^7.83.0",
    "react-router-dom": "^7.18.1",
    "zod": "^4.4.3"
  },
  "devDependencies": {
    "@eslint/js": "^10.0.1",
    "@playwright/test": "^1.62.0",
    "@testing-library/jest-dom": "^7.0.0",
    "@testing-library/react": "^16.3.2",
    "@testing-library/user-event": "^14.6.1",
    "@types/node": "^26.1.1",
    "@types/react": "^19.2.17",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.4",
    "eslint": "^10.8.0",
    "jsdom": "^29.1.1",
    "typescript": "~7.0.2",
    "typescript-eslint": "^8.65.0",
    "vite": "^8.1.5",
    "vitest": "^4.1.10"
  }
}
```

- [ ] **Step 4：创建 Compose 服务骨架**

`compose.yaml` 定义 `db`、`api`、`web`、`proxy`。`proxy` 暴露 `8080:80`；开发数据库仅绑定 `127.0.0.1:5432:5432`，不监听局域网地址。`db` 使用 PostgreSQL 16、命名卷和健康检查；`api` 等待健康数据库；开发阶段 `web` 使用 Vite。Nginx 将 `/api/` 代理至 `api:8000`，其他路径代理至 `web:5173`。`.gitignore` 增加 `.venv/`、`backups/` 和 Playwright 输出目录。

- [ ] **Step 5：验证配置能够解析**

Run:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'apps/api[dev]'
npm --prefix apps/web install
docker compose --env-file .env.example config -q
```

Expected: 四条命令退出码均为 0；生成 `.venv/` 和 `apps/web/package-lock.json`；不启动服务。

- [ ] **Step 6：提交环境骨架**

```bash
git add .editorconfig .env.example README.md compose.yaml apps infra
git commit -m "chore: scaffold ERP platform"
```

## Task 2：建立 FastAPI 工厂、严格配置和统一错误

**Files:**
- Create: `apps/api/app/main.py`
- Create: `apps/api/app/api/router.py`
- Create: `apps/api/app/core/config.py`
- Create: `apps/api/app/core/errors.py`
- Create: `apps/api/app/core/request_id.py`
- Create: `apps/api/tests/conftest.py`
- Test: `apps/api/tests/api/test_health.py`

- [ ] **Step 1：先写健康与错误格式失败测试**

```python
import base64
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://erp:erp@localhost:5432/erp",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
    )


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(test_settings)) as value:
        yield value


def test_liveness(client):
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_uses_problem_details(client):
    response = client.get("/api/v1/missing")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "route_not_found"
    assert body["status"] == 404
    assert response.headers["x-request-id"]
```

- [ ] **Step 2：运行测试并确认失败**

Run: `python -m pytest apps/api/tests/api/test_health.py -q`

Expected: FAIL，原因是 `app.main` 或路由尚不存在。

- [ ] **Step 3：实现严格配置**

`Settings` 必须支持直接环境变量和同名 `_FILE` 路径。生产环境缺少三个 32 字节 Base64 密钥或 `SESSION_COOKIE_SECURE=false` 时抛出 `ValueError`。测试通过依赖覆盖注入测试设置，不读取开发机真实环境。

核心签名：

```python
import base64
import binascii


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"]
    database_url: str
    session_cookie_secure: bool = True
    session_ttl_minutes: int = 480
    field_encryption_master_key: SecretStr
    field_lookup_key: SecretStr
    session_token_pepper: SecretStr

    def validate_runtime_secrets(self) -> None:
        for field_name in (
            "field_encryption_master_key",
            "field_lookup_key",
            "session_token_pepper",
        ):
            raw = getattr(self, field_name).get_secret_value()
            try:
                decoded = base64.urlsafe_b64decode(raw)
            except (ValueError, binascii.Error) as exc:
                raise ValueError(f"{field_name} must be URL-safe Base64") from exc
            if len(decoded) != 32:
                raise ValueError(f"{field_name} must decode to 32 bytes")
        if self.app_env == "production" and not self.session_cookie_secure:
            raise ValueError("production requires secure session cookies")
```

- [ ] **Step 4：实现应用工厂和 Problem Details**

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    resolved.validate_runtime_secrets()
    app = FastAPI(title="Esports Education ERP", docs_url=None if resolved.app_env == "production" else "/docs")
    app.state.settings = resolved
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router, prefix="/api/v1")
    install_exception_handlers(app)
    return app


app = create_app()
```

错误响应固定字段：`type`、`title`、`status`、`detail`、`code`、`request_id`，校验错误另带 `errors`。

- [ ] **Step 5：运行并通过测试**

Run: `python -m pytest apps/api/tests/api/test_health.py -q`

Expected: `2 passed`。

- [ ] **Step 6：运行静态检查并提交**

```bash
ruff check apps/api/app apps/api/tests
mypy apps/api/app
git add apps/api
git commit -m "feat: add API application foundation"
```

Expected: Ruff 和 mypy 均退出 0。

## Task 3：建立 PostgreSQL 模型、迁移和测试事务

**Files:**
- Create: `apps/api/app/core/database.py`
- Create: `apps/api/app/core/models.py`
- Create: `apps/api/app/identity/models.py`
- Create: `apps/api/app/auth/models.py`
- Create: `apps/api/app/rbac/models.py`
- Create: `apps/api/app/audit/models.py`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/migrations/env.py`
- Create: `apps/api/migrations/versions/0001_platform_foundation.py`
- Test: `apps/api/tests/db/test_migrations.py`

- [ ] **Step 1：写数据库迁移失败测试**

```python
EXPECTED_TABLES = {
    "people", "student_profiles", "staff_profiles", "visitor_profiles",
    "guardian_relationships", "user_accounts", "auth_sessions",
    "auth_rate_limits",
    "roles", "permissions", "role_permissions", "user_roles",
    "user_permission_overrides", "audit_events",
}


def test_alembic_head_creates_platform_tables(postgres_engine):
    tables = set(inspect(postgres_engine).get_table_names())
    assert EXPECTED_TABLES <= tables
```

- [ ] **Step 2：启动测试数据库并确认失败**

```bash
docker compose up -d db
DATABASE_URL=postgresql+psycopg://erp:erp@localhost:5432/erp python -m pytest apps/api/tests/db/test_migrations.py -q
```

Expected: FAIL，缺少迁移或预期表。

- [ ] **Step 3：实现 Base、时间字段和会话依赖**

```python
class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
```

- [ ] **Step 4：实现身份和账号表的精确约束**

模型必须具有以下关键列和约束：

```text
people: id UUID PK; full_name; identity_ciphertext; identity_digest UNIQUE;
        phone_ciphertext NULL; phone_digest NULL; email NULL; sex;
        date_of_birth; deleted_at; timestamps
student_profiles: person_id PK/FK; student_number UNIQUE; status
staff_profiles: person_id PK/FK; staff_type
visitor_profiles: person_id PK/FK; linked_student_id NULL; visit_reason NOT NULL
guardian_relationships: guardian_person_id + student_person_id UNIQUE;
                        relationship; is_primary; valid_from; valid_until
user_accounts: id UUID PK; person_id UNIQUE FK; login_phone_digest UNIQUE;
               email_normalized; password_hash; status; force_password_change;
               failed_login_count; locked_until; timestamps
auth_sessions: id UUID PK; user_id FK; token_digest UNIQUE; csrf_digest;
               expires_at; revoked_at; timestamps
auth_rate_limits: key_digest TEXT PK; attempts; window_started_at; blocked_until;
                  updated_at
roles: id UUID PK; key UNIQUE; name
permissions: id UUID PK; key UNIQUE; name; sensitive BOOL
role_permissions: role_id + permission_id + scope_kind UNIQUE
user_roles: user_id + role_id UNIQUE
user_permission_overrides: id UUID PK; user_id; permission_id; effect;
                           scope_kind; scope_id NULL;
                           UNIQUE NULLS NOT DISTINCT composite
audit_events: id UUID PK; actor_user_id NULL; action; resource_type;
              resource_id NULL; purpose NULL; outcome; request_id; metadata JSONB;
              created_at; no updated_at
```

枚举值固定为：`sex=male|female`，`student_status=draft|active|inactive`，`staff_type=coach|life_teacher|customer_service|administration|other`，`account_status=active|disabled`，`effect=allow|deny`，`scope_kind=global|self|family|person|assigned_class`，`audit_outcome=allowed|denied|failed`。数据库使用带 CHECK 的字符串枚举，避免 PostgreSQL 原生枚举阻碍后续迁移。

所有外键显式设置删除策略。人员、账号和关系禁止数据库级级联删除；会话可随账号删除。业务时间全部使用带时区时间戳。

- [ ] **Step 5：生成并检查首个迁移**

Run:

```bash
cd apps/api
alembic revision --autogenerate -m "platform foundation"
alembic upgrade head
```

Expected: 迁移包含上面 14 张表、唯一约束和索引；`alembic upgrade head` 退出 0。将迁移文件重命名为 `0001_platform_foundation.py` 并把 `revision` 固定为 `0001_platform_foundation`。

- [ ] **Step 6：为审计表安装只追加触发器**

迁移中加入：

```sql
CREATE FUNCTION prevent_audit_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
```

降级迁移按“触发器 → 函数 → 表”的顺序删除。

- [ ] **Step 7：运行迁移测试并提交**

Run: `DATABASE_URL=postgresql+psycopg://erp:erp@localhost:5432/erp python -m pytest apps/api/tests/db/test_migrations.py -q`

Expected: PASS，并验证直接 UPDATE/DELETE `audit_events` 均抛出数据库异常。

```bash
git add apps/api
git commit -m "feat: add platform database schema"
```

## Task 4：实现信封加密、检索摘要和身份规范化

**Files:**
- Create: `apps/api/app/security/encryption.py`
- Create: `apps/api/app/identity/normalization.py`
- Test: `apps/api/tests/unit/security/test_encryption.py`
- Test: `apps/api/tests/unit/identity/test_normalization.py`

- [ ] **Step 1：写加密与规范化失败测试**

```python
def test_envelope_cipher_round_trip_and_randomizes_ciphertext(cipher):
    one = cipher.encrypt("11010519491231002X", field_name="identity_number")
    two = cipher.encrypt("11010519491231002X", field_name="identity_number")
    assert one != two
    assert cipher.decrypt(one, field_name="identity_number") == "11010519491231002X"
    assert cipher.decrypt(two, field_name="identity_number") == "11010519491231002X"


def test_lookup_digest_is_stable_and_keyed(lookup):
    assert lookup.digest("13800138000") == lookup.digest("13800138000")
    assert lookup.digest("13800138000") != lookup.digest("13900139000")


def test_cn_id_birth_date_must_match():
    normalized = normalize_cn_id("11010519491231002X")
    assert normalized.birth_date == date(1949, 12, 31)
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/unit/security apps/api/tests/unit/identity -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3：实现 AES-GCM 信封**

`EnvelopeCipher.encrypt(plaintext: str, *, field_name: str) -> str` 每次生成随机 32 字节 DEK。`decrypt(payload: str, *, field_name: str) -> str` 必须使用相同字段名。DEK 使用主密钥 AES-GCM 包装，明文使用 DEK AES-GCM 加密；序列化对象固定为：

```python
class EnvelopePayload(TypedDict):
    v: Literal[1]
    wrapped_key_nonce: str
    wrapped_key: str
    data_nonce: str
    ciphertext: str
```

所有二进制字段用 URL-safe Base64。AAD 固定包含版本和业务字段名，解密时校验；认证失败转换为 `EncryptedFieldError`，不得回显密文或密钥。

- [ ] **Step 4：实现 HMAC-SHA256 检索摘要**

```python
class LookupDigest:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("lookup key must be 32 bytes")
        self._key = key

    def digest(self, normalized: str) -> str:
        return hmac.new(self._key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
```

- [ ] **Step 5：实现手机号和中国居民身份证规范化**

手机号只接受 11 位中国大陆手机号并输出纯数字。身份证接受末位 `X/x`，验证 18 位格式、校验码和出生日期；输入生日与证件生日不一致时返回字段级校验错误。

- [ ] **Step 6：运行安全测试并提交**

Run: `python -m pytest apps/api/tests/unit/security apps/api/tests/unit/identity -q`

Expected: 全部 PASS；相同明文密文不同，摘要相同。

```bash
git add apps/api/app/security apps/api/app/identity/normalization.py apps/api/tests/unit
git commit -m "feat: protect identity fields"
```

## Task 5：实现人员、学员、员工和访客服务

**Files:**
- Create: `apps/api/app/identity/schemas.py`
- Create: `apps/api/app/identity/service.py`
- Test: `apps/api/tests/services/test_people.py`

- [ ] **Step 1：写人员创建、掩码和手机号检索失败测试**

```python
STUDENT_PAYLOAD = {
    "full_name": "测试学员",
    "identity_number": "11010519491231002X",
    "sex": "female",
    "date_of_birth": "1949-12-31",
    "phone": "13800138000",
    "email": "student@example.com",
    "student_number": "S20260001",
}

STAFF_WITH_SAME_ID = {
    "full_name": "重复人员",
    "identity_number": "11010519491231002X",
    "sex": "female",
    "date_of_birth": "1949-12-31",
    "phone": "13900139000",
    "email": "staff@example.com",
    "staff_type": "coach",
}


def test_create_student_and_search_by_phone(identity_service):
    person = identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    found = identity_service.search_people(phone="13800138000", page=1, page_size=20)
    assert [item.id for item in found.items] == [person.id]
    assert found.items[0].masked_phone == "138****8000"
    assert found.items[0].masked_identity == "110105********002X"


def test_duplicate_identity_is_rejected(identity_service):
    identity_service.create_student(StudentCreate(**STUDENT_PAYLOAD))
    with pytest.raises(IdentityAlreadyExists):
        identity_service.create_staff(StaffCreate(**STAFF_WITH_SAME_ID))
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/services/test_people.py -q`

Expected: FAIL，身份服务尚不存在。

- [ ] **Step 3：定义输入和输出契约**

```python
class PersonBaseInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    identity_number: str
    sex: Literal["male", "female"]
    date_of_birth: date
    phone: str | None = None
    email: EmailStr | None = None


class StudentCreate(PersonBaseInput):
    student_number: str = Field(min_length=3, max_length=32)


class GuardianCreate(PersonBaseInput):
    phone: str
    email: EmailStr


class StaffCreate(PersonBaseInput):
    phone: str
    email: EmailStr
    staff_type: Literal["coach", "life_teacher", "customer_service", "administration", "other"]


class VisitorCreate(PersonBaseInput):
    phone: str
    visit_reason: str = Field(min_length=2, max_length=200)
    linked_student_id: UUID | None = None
```

所有普通响应只返回掩码字段，不返回密文、摘要或密码字段。

- [ ] **Step 4：实现事务服务**

`IdentityService.create_student/create_guardian/create_staff/create_visitor` 按顺序执行规范化、摘要冲突检查、信封加密、基础 `people` 插入和适用的类型档案插入。监护人身份由 `guardian_relationships` 确立，不创建空的监护人 profile 表。重复证件摘要抛出 `IdentityAlreadyExists(code="identity_already_exists")`。任何一步失败必须回滚。`search_people(phone=normalized_phone)` 用 HMAC 摘要精确查询，不解密全表。

- [ ] **Step 5：实现分页查询和安全输出模型**

服务方法：

```text
create_student(input) -> PersonSummary
create_guardian(input) -> PersonSummary
create_staff(input) -> PersonSummary
create_visitor(input) -> PersonSummary
search_people(phone, name, person_type, page, page_size) -> Page[PersonSummary]
get_person(person_id) -> PersonDetail
update_person(person_id, input) -> PersonDetail
```

`page_size` 范围为 1–100；姓名搜索最多返回 100 条且转义 `%`/`_`；软删除人员不出现在默认结果。

- [ ] **Step 6：运行测试并提交**

Run: `python -m pytest apps/api/tests/services/test_people.py -q`

Expected: PASS，响应不含 `ciphertext`、`digest`、完整身份证或完整手机号。

```bash
git add apps/api/app/identity apps/api/tests/services/test_people.py
git commit -m "feat: add protected people records"
```

## Task 6：实现监护人关系和未成年就绪校验

**Files:**
- Modify: `apps/api/app/identity/schemas.py`
- Modify: `apps/api/app/identity/service.py`
- Test: `apps/api/tests/unit/identity/test_guardians.py`
- Test: `apps/api/tests/services/test_guardians.py`

- [ ] **Step 1：写年龄和监护人失败测试**

```python
def test_minor_is_computed_at_reference_date():
    assert is_minor(date(2010, 7, 27), date(2026, 7, 26)) is True
    assert is_minor(date(2008, 7, 26), date(2026, 7, 26)) is False


def test_minor_without_guardian_is_not_ready(identity_service, minor_student):
    result = identity_service.readiness(minor_student.id, reference_date=date(2026, 7, 26))
    assert result.ready is False
    assert result.missing == ["guardian"]
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/unit/identity/test_guardians.py apps/api/tests/services/test_guardians.py -q`

Expected: FAIL，缺少年龄与监护人实现。

- [ ] **Step 3：实现关系约束和就绪服务**

```python
def is_minor(birth_date: date, reference_date: date) -> bool:
    try:
        eighteenth = birth_date.replace(year=birth_date.year + 18)
    except ValueError:
        eighteenth = date(birth_date.year + 18, 2, 28)
    return reference_date < eighteenth


@dataclass(frozen=True)
class IdentityReadiness:
    ready: bool
    missing: list[str]
```

`readiness()` 对所有人员校验姓名/身份证/性别/出生日期；对账号校验手机号/邮箱；对未成年人校验在参考日有效的监护人关系。监护人不能等于学员本人，不能创建环形监护关系。

- [ ] **Step 4：实现关系服务方法**

```text
add_guardian(student_id, guardian_id, relationship, is_primary, valid_from, valid_until)
list_guardians(student_id, reference_date)
remove_guardian(student_id, guardian_id)
readiness(person_id, reference_date, account_candidate=None)
```

删除最后一名有效监护人允许保存，但未成年学员立即变为未就绪；账号激活及后续入住接口必须复用该校验。

- [ ] **Step 5：运行测试并提交**

Run: `python -m pytest apps/api/tests/unit/identity/test_guardians.py apps/api/tests/services/test_guardians.py -q`

Expected: PASS，包括闰日生日按 18 周年当年 2 月 28 日计算、18 岁当天、失效监护关系和环形关系测试。

```bash
git add apps/api/app/identity apps/api/tests
git commit -m "feat: add guardian readiness rules"
```

## Task 7：实现账号密码与账号生命周期

**Files:**
- Create: `apps/api/app/security/passwords.py`
- Create: `apps/api/app/auth/schemas.py`
- Create: `apps/api/app/auth/service.py`
- Test: `apps/api/tests/unit/auth/test_passwords.py`
- Test: `apps/api/tests/services/test_accounts.py`

- [ ] **Step 1：写密码和账号失败测试**

```python
def test_password_hash_uses_argon2_and_verifies(passwords):
    encoded = passwords.hash("A-strong-password-2026")
    assert encoded.startswith("$argon2id$")
    assert passwords.verify("A-strong-password-2026", encoded) is True
    assert passwords.verify("wrong", encoded) is False


def test_account_requires_ready_person_and_unique_phone(account_service, unready_minor):
    with pytest.raises(PersonNotReady) as error:
        account_service.create_account(AccountCreate(
            person_id=unready_minor.id,
            login_phone="13800138000",
            email="minor@example.com",
        ))
    assert error.value.code == "person_not_ready"
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/unit/auth/test_passwords.py apps/api/tests/services/test_accounts.py -q`

Expected: FAIL，认证模块尚未实现。

- [ ] **Step 3：实现 Argon2id 与密码策略**

Argon2 配置：`time_cost=3`、`memory_cost=65536`、`parallelism=4`、`hash_len=32`、`salt_len=16`。新密码要求 12–128 字符，至少包含字母和数字；不记录输入值。

- [ ] **Step 4：实现账号生命周期服务**

服务方法：

```text
create_account(input) -> AccountCreatedWithTemporaryPassword
list_accounts(page, page_size, status) -> Page[AccountSummary]
disable_account(account_id) -> None
enable_account(account_id) -> None
reset_password(account_id) -> TemporaryPassword
```

创建账号前调用 `IdentityService.readiness()`；登录手机号摘要唯一；临时密码使用 `secrets.token_urlsafe(18)`，仅在创建/重置响应中返回一次且不写日志；`force_password_change=true`。

账号手机号和邮箱必须与人员档案联系信息一致。人员尚无联系信息时，账号创建事务先写入加密手机号、检索摘要和规范化邮箱再创建账号；人员已有不同值时返回 `account_contact_mismatch`，必须先通过人员编辑流程更正，不能悄悄覆盖。

- [ ] **Step 5：运行测试并提交**

Run: `python -m pytest apps/api/tests/unit/auth/test_passwords.py apps/api/tests/services/test_accounts.py -q`

Expected: PASS，包括重复手机号、停用/启用、临时密码只返回一次和重置使旧密码失效。

```bash
git add apps/api/app/security/passwords.py apps/api/app/auth apps/api/tests
git commit -m "feat: add account lifecycle"
```

## Task 8：实现服务端会话、CSRF 和登录限速

**Files:**
- Modify: `apps/api/app/auth/service.py`
- Create: `apps/api/app/auth/dependencies.py`
- Create: `apps/api/app/auth/router.py`
- Modify: `apps/api/app/api/router.py`
- Test: `apps/api/tests/api/test_auth_sessions.py`

- [ ] **Step 1：写登录、CSRF、锁定和停用失败测试**

```python
def test_login_sets_http_only_cookie_and_requires_csrf(client, active_account):
    login = client.post("/api/v1/auth/login", json={"phone": active_account.phone, "password": active_account.password})
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    rejected = client.post("/api/v1/auth/logout")
    assert rejected.status_code == 403
    accepted = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert accepted.status_code == 204


def test_disabling_account_revokes_all_sessions(admin_client, signed_in_account):
    admin_client.post(f"/api/v1/accounts/{signed_in_account.id}/disable")
    assert signed_in_account.client.get("/api/v1/auth/me").status_code == 401
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/api/test_auth_sessions.py -q`

Expected: FAIL，登录或 CSRF 逻辑不存在。

- [ ] **Step 3：实现不透明会话令牌**

登录生成 32 字节随机令牌；浏览器 Cookie 保存原令牌，数据库只保存 `HMAC(SESSION_TOKEN_PEPPER, token)`。CSRF 令牌独立随机生成并只存摘要。`GET /auth/csrf` 为有效会话轮换 CSRF。退出和停用账号均设置 `revoked_at`。

- [ ] **Step 4：实现路由和依赖**

```text
POST /api/v1/auth/login
GET  /api/v1/auth/me
GET  /api/v1/auth/csrf
POST /api/v1/auth/change-password
POST /api/v1/auth/logout
```

除 `GET/HEAD/OPTIONS` 和登录外，所有会话认证的变更请求必须同时通过 Session Cookie 和 `X-CSRF-Token`。强制改密账号只能访问 `me/csrf/change-password/logout`。

将认证路由注册到 `apps/api/app/api/router.py`，并确认生产日志过滤 Cookie、授权头、密码和 CSRF 字段。

- [ ] **Step 5：实现失败登录规则**

连续 5 次失败后锁定 15 分钟；成功登录清零失败计数。错误响应统一为 `invalid_credentials`，不区分手机号不存在、密码错误或账号停用。登录接口将“手机号摘要 + 规范化 IP”再次用 `SESSION_TOKEN_PEPPER` 做 HMAC，结果作为 `auth_rate_limits.key_digest`；数据库只保存摘要、窗口和计数，不保存原始 IP。该表保证多进程限速一致。每次登录检查前，`AuthRateLimiter.prune_expired(limit=500)` 删除 `updated_at` 早于 7 天的最多 500 条记录，相关时间列建立索引。

- [ ] **Step 6：运行测试并提交**

Run: `python -m pytest apps/api/tests/api/test_auth_sessions.py -q`

Expected: PASS，包括过期、撤销、Cookie 安全属性、CSRF 错误、强制改密和锁定恢复。

```bash
git add apps/api/app/api/router.py apps/api/app/auth apps/api/tests/api/test_auth_sessions.py
git commit -m "feat: add secure login sessions"
```

## Task 9：实现拒绝优先 RBAC 和初始角色

**Files:**
- Create: `apps/api/app/rbac/policy.py`
- Create: `apps/api/app/rbac/seed.py`
- Create: `apps/api/app/rbac/router.py`
- Create: `apps/api/app/cli/create_admin.py`
- Create: `apps/api/migrations/versions/0002_seed_rbac.py`
- Modify: `apps/api/app/api/router.py`
- Test: `apps/api/tests/unit/rbac/test_policy.py`
- Test: `apps/api/tests/api/test_permissions.py`

- [ ] **Step 1：写权限优先级失败测试**

```python
def allow(permission: str, scope: str) -> PolicyRule:
    scope_kind, separator, raw_id = scope.partition(":")
    return PolicyRule(permission, "allow", scope_kind, UUID(raw_id) if separator else None)


def deny(permission: str, scope: str) -> PolicyRule:
    scope_kind, separator, raw_id = scope.partition(":")
    return PolicyRule(permission, "deny", scope_kind, UUID(raw_id) if separator else None)


def test_explicit_deny_beats_role_and_user_allow(policy):
    decision = policy.decide(
        permission="people.identity.reveal",
        resource_tokens={"global", "person:00000000-0000-0000-0000-000000000123"},
        role_rules=[allow("people.identity.reveal", "global")],
        overrides=[
            allow("people.identity.reveal", "person:00000000-0000-0000-0000-000000000123"),
            deny("people.identity.reveal", "person:00000000-0000-0000-0000-000000000123"),
        ],
    )
    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_role_allows_union_but_scope_must_match(policy):
    assert policy.decide(
        "people.read",
        {"family:00000000-0000-0000-0000-000000000001"},
        [allow("people.read", "family")],
        [],
    ).allowed
    assert not policy.decide(
        "people.read",
        {"person:00000000-0000-0000-0000-000000000002"},
        [allow("people.read", "family")],
        [],
    ).allowed
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/unit/rbac/test_policy.py apps/api/tests/api/test_permissions.py -q`

Expected: FAIL，策略模块和路由不存在。

- [ ] **Step 3：实现纯函数策略引擎**

```python
@dataclass(frozen=True)
class PolicyRule:
    permission: str
    effect: Literal["allow", "deny"]
    scope_kind: Literal["global", "self", "family", "person", "assigned_class"]
    scope_id: UUID | None = None


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
```

判定顺序固定为：匹配账号级 deny → 匹配账号级 allow → 匹配角色 allow → 默认拒绝。无匹配 scope 的规则视为不匹配。`assigned_class` 在阶段 1A 始终不产生对象令牌，阶段 1B 接入班级关系解析器。

- [ ] **Step 4：种子化角色和权限**

权限键：

```text
people.list
people.read
people.create
people.update
people.phone.reveal
people.identity.reveal
accounts.manage
permissions.manage
audit.read
```

角色：`admin`、`coach`、`customer_service`、`life_teacher`、`staff`、`guardian`、`student`。管理员获得全局全部权限；客服获得全局 `people.list/read` 但响应始终脱敏；监护人 `people.read` 限 `family`；学生 `people.read` 限 `self`；其余角色阶段 1A 默认不获得人员访问，等待管理员显式授权或后续班级关系。

`0002_seed_rbac.py` 使用稳定 UUID 插入上述角色、权限和 `role_permissions`，并可重复执行而不重复数据。`rbac.seed.seed_roles_and_permissions(db)` 为测试数据库提供同一份常量；迁移测试必须断言数据库键集合与代码常量完全一致。

- [ ] **Step 5：实现首个管理员 CLI**

```bash
python -m app.cli.create_admin \
  --name "系统管理员" \
  --phone 13800138000 \
  --email admin@example.com \
  --identity-number 11010519491231002X \
  --sex female \
  --date-of-birth 1949-12-31
```

密码通过隐藏提示输入两次。CLI 在一个事务中创建 `people`、`staff_profiles(staff_type=administration)`、账号和管理员角色。只有数据库中没有任何管理员账号时允许执行；成功后输出账号 ID，不输出密码。

- [ ] **Step 6：实现权限管理 API**

```text
GET    /api/v1/roles
GET    /api/v1/permissions
POST   /api/v1/accounts/{id}/roles
DELETE /api/v1/accounts/{id}/roles/{role_id}
POST   /api/v1/accounts/{id}/permission-overrides
DELETE /api/v1/accounts/{id}/permission-overrides/{override_id}
```

禁止管理员删除自己的最后一个 `admin` 角色或给自己添加拒绝 `permissions.manage` 的规则；系统必须始终至少保留一个有效管理员。

- [ ] **Step 7：运行权限测试并提交**

Run: `python -m pytest apps/api/tests/unit/rbac/test_policy.py apps/api/tests/api/test_permissions.py -q`

Expected: PASS，包括多角色并集、拒绝优先、family/self/person/global scope、默认拒绝和最后管理员保护。

```bash
git add apps/api/app/api/router.py apps/api/app/rbac apps/api/app/cli/create_admin.py apps/api/migrations/versions/0002_seed_rbac.py apps/api/tests
git commit -m "feat: add deny-first permissions"
```

## Task 10：暴露受保护的管理 API、只追加审计与敏感查看

**Files:**
- Create: `apps/api/app/audit/service.py`
- Create: `apps/api/app/audit/router.py`
- Create: `apps/api/app/identity/router.py`
- Create: `apps/api/app/auth/admin_router.py`
- Modify: `apps/api/app/api/router.py`
- Test: `apps/api/tests/api/test_people.py`
- Test: `apps/api/tests/api/test_guardians.py`
- Test: `apps/api/tests/api/test_accounts.py`
- Test: `apps/api/tests/api/test_sensitive_reveal.py`
- Test: `apps/api/tests/api/test_audit.py`

- [ ] **Step 1：写受保护管理 API、敏感查看和审计失败测试**

```python
def test_customer_service_list_is_masked(customer_service_client, person):
    response = customer_service_client.get("/api/v1/people")
    assert response.status_code == 200
    row = next(item for item in response.json()["items"] if item["id"] == str(person.id))
    assert row["phone"] == "138****8000"
    assert row["identity_number"] == "110105********002X"


def test_guardian_reads_only_bound_students(guardian_client, own_student, other_student):
    assert guardian_client.get(f"/api/v1/people/{own_student.id}").status_code == 200
    assert guardian_client.get(f"/api/v1/people/{other_student.id}").status_code == 403


def test_sensitive_reveal_requires_permission_and_purpose(customer_service_client, person):
    denied = customer_service_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )
    assert denied.status_code == 403


def test_authorized_reveal_is_no_store_and_audited(identity_admin_client, person, db):
    response = identity_admin_client.post(
        f"/api/v1/people/{person.id}/sensitive-reveal",
        json={"field": "identity_number", "purpose": "入住资料核对"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["value"] == person.identity_number
    assert db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.action == "person.identity.reveal")) == 1
```

- [ ] **Step 2：运行并确认失败**

Run: `python -m pytest apps/api/tests/api/test_people.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_sensitive_reveal.py apps/api/tests/api/test_audit.py -q`

Expected: FAIL，受保护的身份/账号路由、敏感查看接口和审计服务不存在。

- [ ] **Step 3：实现审计服务**

```python
class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def append(
        self,
        *,
        actor_user_id: UUID | None,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        purpose: str | None,
        outcome: Literal["allowed", "denied", "failed"],
        request_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            outcome=outcome,
            request_id=request_id,
            metadata=metadata or {},
        )
        self.db.add(event)
        self.db.flush()
        return event
```

审计元数据禁止包含完整手机号、身份证、密码、会话令牌、CSRF 或密文。

- [ ] **Step 4：实现受保护的人员、监护人和账号 API**

```text
POST   /api/v1/people/students
POST   /api/v1/people/guardians
POST   /api/v1/people/staff
POST   /api/v1/people/visitors
GET    /api/v1/people?phone=&name=&type=&page=1&page_size=20
GET    /api/v1/people/{person_id}
PATCH  /api/v1/people/{person_id}
POST   /api/v1/people/{student_id}/guardians
GET    /api/v1/people/{student_id}/guardians
DELETE /api/v1/people/{student_id}/guardians/{guardian_id}
GET    /api/v1/people/{person_id}/readiness?reference_date=2026-07-26
POST   /api/v1/accounts
GET    /api/v1/accounts?page=&status=
POST   /api/v1/accounts/{id}/disable
POST   /api/v1/accounts/{id}/enable
POST   /api/v1/accounts/{id}/reset-password
```

每个路由调用 Task 9 的策略引擎和对象 scope token；管理员 CRUD 使用相应权限，客服只获得掩码列表，监护人只读取 family scope，学生只读取 self。账号停用必须在同一事务中撤销所有会话。将 identity、account-admin、rbac 和 audit 路由统一注册到 `/api/v1`。

- [ ] **Step 5：实现敏感查看接口**

`field` 仅允许 `phone` 或 `identity_number`；`purpose` 长度 4–200。接口先做对象级权限判定，再解密单一字段，响应加 `Cache-Control: no-store, private`。允许、拒绝和解密失败都写审计；拒绝响应不暴露字段是否存在。

- [ ] **Step 6：实现审计查询 API**

`GET /api/v1/audit-events` 支持 `actor_user_id/action/resource_type/date_from/date_to/page/page_size`，只允许 `audit.read`；返回元数据仍进行字段白名单过滤。

- [ ] **Step 7：运行测试并提交**

Run: `python -m pytest apps/api/tests/api/test_people.py apps/api/tests/api/test_guardians.py apps/api/tests/api/test_accounts.py apps/api/tests/api/test_sensitive_reveal.py apps/api/tests/api/test_audit.py -q`

Expected: PASS，并验证数据库 UPDATE/DELETE 审计事件失败。

```bash
git add apps/api/app/api apps/api/app/audit apps/api/app/auth/admin_router.py apps/api/app/identity/router.py apps/api/tests/api
git commit -m "feat: expose audited platform administration"
```

## Task 11：建立 React、登录和响应式应用外壳

**Files:**
- Create: `apps/web/index.html`
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/tsconfig.app.json`
- Create: `apps/web/tsconfig.node.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/src/test/setup.ts`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/app/providers.tsx`
- Create: `apps/web/src/app/router.tsx`
- Create: `apps/web/src/api/client.ts`
- Create: `apps/web/src/auth/AuthProvider.tsx`
- Create: `apps/web/src/auth/LoginPage.tsx`
- Create: `apps/web/src/layout/AppShell.tsx`
- Create: `apps/web/src/layout/AppShell.css`
- Create: `apps/web/src/styles/tokens.css`
- Create: `apps/web/src/styles/global.css`
- Test: `apps/web/src/auth/LoginPage.test.tsx`
- Test: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1：写登录和导航失败测试**

```tsx
it("submits phone and password and handles forced password change", async () => {
  const user = userEvent.setup()
  render(<LoginPage />)
  await user.type(screen.getByLabelText("手机号"), "13800138000")
  await user.type(screen.getByLabelText("密码"), "A-strong-password-2026")
  await user.click(screen.getByRole("button", { name: "登录" }))
  expect(await screen.findByText("请先修改临时密码")).toBeVisible()
})


it("shows desktop sidebar and mobile bottom navigation from the same route model", () => {
  render(<AppShell />)
  expect(screen.getAllByRole("navigation")).toHaveLength(2)
})
```

- [ ] **Step 2：运行并确认失败**

Run: `npm --prefix apps/web test -- LoginPage.test.tsx AppShell.test.tsx`

Expected: FAIL，组件尚不存在。

- [ ] **Step 3：实现统一 API Client**

```ts
export type ProblemDetails = {
  type: string
  title: string
  status: number
  detail: string
  code: string
  request_id: string
  errors?: Array<{ field: string; message: string }>
}

export class ProblemDetailsError extends Error {
  constructor(public readonly problem: ProblemDetails) {
    super(problem.detail)
  }

  static async fromResponse(response: Response): Promise<ProblemDetailsError> {
    return new ProblemDetailsError((await response.json()) as ProblemDetails)
  }
}

let csrfToken: string | null = null

export function setCsrfToken(value: string | null): void {
  csrfToken = value
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(init.method ?? "GET")) {
    headers.set("X-CSRF-Token", csrfToken)
  }
  const request = Object.assign({}, init, { headers, credentials: "include" as const })
  const response = await fetch(`/api/v1${path}`, request)
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  return response.status === 204 ? (undefined as T) : response.json()
}
```

CSRF 只保存在运行内存；页面刷新后通过 `/auth/csrf` 重新获取。不得写入 `localStorage`。

- [ ] **Step 4：实现 AuthProvider 和路由守卫**

状态明确为 `loading/authenticated/anonymous/force_password_change`。匿名访问业务路由跳转 `/login`；强制改密账号跳转 `/change-password`；登出清空 Query 缓存和 CSRF。

- [ ] **Step 5：实现响应式 AppShell**

导航模型只定义一次；桌面宽度 `>=1024px` 显示侧栏，`<768px` 显示底部导航，768–1023px 使用折叠顶部导航。没有权限的导航项不渲染。手机按钮最小触控高度 44px，页面横向不得溢出。

- [ ] **Step 6：运行测试、构建并提交**

```bash
npm --prefix apps/web test
npm --prefix apps/web run build
git add apps/web
git commit -m "feat: add responsive authenticated shell"
```

Expected: Vitest 全部 PASS，TypeScript 和 Vite 构建退出 0。

## Task 12：实现人员、监护人和敏感查看界面

**Files:**
- Create: `apps/web/src/people/api.ts`
- Create: `apps/web/src/people/PeoplePage.tsx`
- Create: `apps/web/src/people/PersonForm.tsx`
- Create: `apps/web/src/people/PersonDetailPage.tsx`
- Create: `apps/web/src/people/GuardianPanel.tsx`
- Test: `apps/web/src/people/PeoplePage.test.tsx`
- Test: `apps/web/src/people/PersonDetailPage.test.tsx`

- [ ] **Step 1：写列表、手机卡片和敏感查看失败测试**

```tsx
it("renders masked data and searches exact phone", async () => {
  const user = userEvent.setup()
  render(<PeoplePage />)
  await user.type(screen.getByLabelText("手机号查询"), "13800138000")
  await user.click(screen.getByRole("button", { name: "查询" }))
  expect(await screen.findByText("138****8000")).toBeVisible()
  expect(screen.queryByText("13800138000")).not.toBeInTheDocument()
})


it("requires a purpose before revealing identity", async () => {
  const user = userEvent.setup()
  render(<PersonDetailPage />)
  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))
  expect(screen.getByLabelText("查看用途")).toBeRequired()
})
```

- [ ] **Step 2：运行并确认失败**

Run: `npm --prefix apps/web test -- PeoplePage.test.tsx PersonDetailPage.test.tsx`

Expected: FAIL，页面尚不存在。

- [ ] **Step 3：实现列表和创建表单**

桌面使用表格，手机使用卡片；两者消费相同查询结果。表单按“学员/监护人/工作人员/访客”切换字段；身份证生日不一致、未成年无监护人、重复证件等后端字段错误显示在对应控件旁，不用全局弹窗替代。

- [ ] **Step 4：实现详情、监护人和敏感查看**

详情默认只显示掩码。敏感查看必须先填写用途，成功值只保存在组件状态，离开页面立即清除；60 秒后自动重新遮盖。监护人面板支持绑定已有人员、设为主要监护人和移除关系，并刷新 readiness。

- [ ] **Step 5：运行测试、构建并提交**

```bash
npm --prefix apps/web test -- PeoplePage.test.tsx PersonDetailPage.test.tsx
npm --prefix apps/web run build
git add apps/web/src/people apps/web/src/app/router.tsx
git commit -m "feat: add people management UI"
```

Expected: PASS；手机视口不存在横向滚动。

## Task 13：实现账号、权限和审计管理界面

**Files:**
- Create: `apps/web/src/settings/AccountsPage.tsx`
- Create: `apps/web/src/settings/PermissionsPage.tsx`
- Create: `apps/web/src/settings/AuditPage.tsx`
- Test: `apps/web/src/settings/AccountsPage.test.tsx`
- Test: `apps/web/src/settings/PermissionsPage.test.tsx`
- Test: `apps/web/src/settings/AuditPage.test.tsx`

- [ ] **Step 1：写账号和权限安全失败测试**

```tsx
it("shows a temporary password only in the creation result dialog", async () => {
  const user = userEvent.setup()
  render(<AccountsPage />)
  await user.click(screen.getByRole("button", { name: "开通账号" }))
  expect(await screen.findByText("请立即安全交付临时密码")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "我已保存并关闭" }))
  expect(screen.queryByText(/临时密码/)).not.toBeInTheDocument()
})


it("explains that explicit deny overrides grants", () => {
  render(<PermissionsPage />)
  expect(screen.getByText("显式拒绝优先于角色和账号允许")).toBeVisible()
})
```

- [ ] **Step 2：运行并确认失败**

Run: `npm --prefix apps/web test -- src/settings`

Expected: FAIL，页面尚不存在。

- [ ] **Step 3：实现账号生命周期页面**

支持开通、停用、启用和重置密码。临时密码仅在一次性对话框显示；关闭后前端清空变量，不提供再次查看。禁止把临时密码复制到日志、URL 或 Query 缓存。

- [ ] **Step 4：实现权限页面**

按账号展示角色和覆盖规则。新增覆盖必须选择“允许/拒绝、能力、范围、范围对象”；保存前展示最终效果摘要。页面阻止用户移除自己的最后管理员能力，并正确显示后端的最后管理员保护错误。

- [ ] **Step 5：实现审计页面**

按操作人、动作、资源、日期筛选；只显示白名单元数据。身份证、手机号和临时密码不应出现在 DOM。桌面表格在手机变为事件卡片。

- [ ] **Step 6：运行测试、构建并提交**

```bash
npm --prefix apps/web test -- src/settings
npm --prefix apps/web run build
git add apps/web/src/settings apps/web/src/app/router.tsx
git commit -m "feat: add security administration UI"
```

Expected: PASS，非管理员路由守卫不渲染设置页面。

## Task 14：完成端到端验收、容器加固和运维交付

**Files:**
- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/tests/helpers.ts`
- Create: `apps/web/tests/platform-foundation.spec.ts`
- Create: `apps/api/app/cli/seed_e2e.py`
- Create: `compose.e2e.yaml`
- Create: `scripts/check.sh`
- Create: `scripts/backup-db.sh`
- Create: `scripts/restore-db.sh`
- Create: `compose.prod.yaml`
- Modify: `compose.yaml`
- Modify: `apps/api/Dockerfile`
- Modify: `apps/web/Dockerfile`
- Modify: `infra/nginx/nginx.conf`
- Modify: `README.md`

- [ ] **Step 1：写端到端平台旅程**

`seed_e2e.py` 必须在 `APP_ENV=test` 之外拒绝运行；它重建测试数据并创建固定夹具：管理员 `19990000001 / E2E-admin-password-2026`、客服 `19990000004 / E2E-customer-password-2026`，以及监护人和学员人员档案。这些凭据只存在测试数据库，不写入 `.env.example` 或生产镜像。`helpers.ts` 使用可访问标签封装下列签名：

```ts
import type { Page } from "@playwright/test"

let personSequence = 10

function makeCnId(birth: string, sequence: number): string {
  const body = `110105${birth}${String(sequence).padStart(3, "0")}`
  const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
  const checks = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]
  const sum = body.split("").reduce((value, digit, index) => value + Number(digit) * weights[index], 0)
  return `${body}${checks[sum % 11]}`
}

async function login(page: Page, phone: string, password: string): Promise<void> {
  await page.goto("/login")
  await page.getByLabel("手机号").fill(phone)
  await page.getByLabel("密码").fill(password)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("heading", { name: "工作台" }).waitFor()
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await login(page, "19990000001", "E2E-admin-password-2026")
}

export async function loginAsCustomerService(page: Page): Promise<void> {
  await login(page, "19990000004", "E2E-customer-password-2026")
}

export async function createPerson(
  page: Page,
  type: "guardian" | "student" | "staff",
): Promise<string> {
  personSequence += 1
  const minor = type === "student"
  const birth = minor ? "20120102" : "19900102"
  const name = `${type}-${personSequence}`
  await page.goto("/people/new")
  await page.getByLabel("人员类型").selectOption(type)
  await page.getByLabel("姓名").fill(name)
  await page.getByLabel("身份证").fill(makeCnId(birth, personSequence))
  await page.getByLabel("性别").selectOption(personSequence % 2 === 0 ? "female" : "male")
  await page.getByLabel("出生日期").fill(minor ? "2012-01-02" : "1990-01-02")
  await page.getByLabel("手机号").fill(`1390000${String(personSequence).padStart(4, "0")}`)
  await page.getByLabel("邮箱").fill(`${type}-${personSequence}@example.com`)
  if (type === "student") await page.getByLabel("学员编号").fill(`E2E-S-${personSequence}`)
  if (type === "staff") await page.getByLabel("工作人员类型").selectOption("life_teacher")
  await page.getByRole("button", { name: "保存人员" }).click()
  await page.getByRole("heading", { name }).waitFor()
  return name
}

export async function createMinorStudent(page: Page): Promise<string> {
  return createPerson(page, "student")
}

export async function bindGuardian(
  page: Page,
  studentName: string,
  guardianName: string,
): Promise<void> {
  await page.goto(`/people?name=${encodeURIComponent(studentName)}`)
  await page.getByRole("link", { name: studentName }).click()
  await page.getByRole("button", { name: "绑定监护人" }).click()
  await page.getByLabel("监护人").fill(guardianName)
  await page.getByRole("option", { name: guardianName }).click()
  await page.getByRole("button", { name: "确认绑定" }).click()
}

export async function createStaffAccount(
  page: Page,
  staffType: "coach" | "life_teacher" | "customer_service" | "administration" | "other",
): Promise<void> {
  const name = await createPerson(page, "staff")
  await page.getByLabel("工作人员类型").selectOption(staffType)
  await page.getByRole("button", { name: "保存修改" }).click()
  await page.getByRole("button", { name: "开通账号" }).click()
  await page.getByRole("button", { name: "确认开通" }).click()
  await page.getByText("请立即安全交付临时密码").waitFor()
  await page.getByRole("heading", { name }).waitFor()
}
```

每个 helper 必须通过页面表单操作，不直接调用数据库；返回值是页面中唯一的人员姓名。`playwright.config.ts` 固定 `workers: 1`，并定义桌面 1440×900 和手机 390×844 两个项目。`platform-foundation.spec.ts` 必须覆盖：

```ts
test("admin creates a guarded minor and a staff account", async ({ page }) => {
  await loginAsAdmin(page)
  const guardian = await createPerson(page, "guardian")
  const student = await createMinorStudent(page)
  await bindGuardian(page, student, guardian)
  await expect(page.getByText("实名资料完整")).toBeVisible()
  await createStaffAccount(page, "life_teacher")
  await expect(page.getByText("请立即安全交付临时密码")).toBeVisible()
})

test("customer service sees roster data but cannot reveal identity", async ({ page }) => {
  await loginAsCustomerService(page)
  await page.goto("/people")
  await expect(page.getByText(/\*{8}/)).toBeVisible()
  await expect(page.getByRole("button", { name: "查看完整身份证" })).toHaveCount(0)
})
```

同一旅程在 1440×900 与 390×844 两个项目运行。

- [ ] **Step 2：运行 E2E 并确认至少一个失败**

Run:

```bash
docker compose -f compose.yaml -f compose.e2e.yaml up -d --build
docker compose -f compose.yaml -f compose.e2e.yaml exec -T api alembic upgrade head
docker compose -f compose.yaml -f compose.e2e.yaml exec -T api python -m app.cli.seed_e2e
npm --prefix apps/web run test:e2e
```

`compose.e2e.yaml` 必须使用独立数据库 `erp_e2e` 和独立命名卷，设置 `APP_ENV=test`，不能挂载或连接开发/生产数据库。Expected: 首次 FAIL，暴露尚未完成的代理、种子账号或响应式问题；记录具体失败并只修复真实原因。

- [ ] **Step 3：加固容器和反向代理**

API/Web 容器使用非 root 用户、只读根文件系统、`no-new-privileges`、健康检查和固定可写临时卷。Nginx 添加 CSP、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`、合理上传上限，并只允许同源调用。`compose.prod.yaml` 覆盖开发端口设置，生产只发布反向代理端口，不发布数据库、API 或 Vite 端口。

- [ ] **Step 4：实现数据库备份和恢复脚本**

`backup-db.sh` 接受显式输出文件，使用 `pg_dump --format=custom --no-owner` 并生成同名 `.sha256`。`restore-db.sh` 只允许恢复到显式指定的空测试数据库，先校验哈希，再用 `pg_restore --clean --if-exists --no-owner`。脚本缺少目标参数时退出非零，绝不默认覆盖生产数据库。

- [ ] **Step 5：创建总验证脚本**

```bash
#!/usr/bin/env bash
set -euo pipefail
python -m pytest apps/api/tests -q
ruff check apps/api/app apps/api/tests
mypy apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run build
docker compose config -q
docker compose -f compose.yaml -f compose.prod.yaml config -q
docker compose -f compose.yaml -f compose.e2e.yaml up -d --build
trap 'docker compose -f compose.yaml -f compose.e2e.yaml down --remove-orphans' EXIT
docker compose -f compose.yaml -f compose.e2e.yaml exec -T api alembic upgrade head
docker compose -f compose.yaml -f compose.e2e.yaml exec -T api python -m app.cli.seed_e2e
npm --prefix apps/web run test:e2e
```

- [ ] **Step 6：执行完整验证**

Run: `bash scripts/check.sh`

Expected: 后端测试、Ruff、mypy、前端测试、构建、Compose 校验和 Playwright 全部退出 0；无跳过的关键旅程。

- [ ] **Step 7：执行数据库恢复演练**

```bash
bash scripts/backup-db.sh ./backups/phase1a-verification.dump
bash scripts/restore-db.sh ./backups/phase1a-verification.dump erp_restore_test
```

Expected: 哈希校验通过；恢复库的 Alembic 版本为 `0002_seed_rbac`；14 张平台表存在；人员、账号、权限和审计样本行数与源库一致。

- [ ] **Step 8：更新 README 并提交阶段 1A**

README 必须包含本地启动、生成密钥、创建首个管理员、运行迁移、运行全部检查、备份/恢复、生产 Docker secret、默认端口和阶段 1A 明确边界。

```bash
git add README.md compose.yaml apps infra scripts
git commit -m "test: verify ERP platform foundation"
```

## 阶段 1A 完成判据

- `bash scripts/check.sh` 全部通过。
- `git status --short` 无未提交文件。
- 管理员、客服、生活老师、监护人和学员的权限旅程通过。
- 完整身份证和手机号默认不出现在列表、日志或审计元数据。
- 显式拒绝覆盖所有允许，最后管理员无法被误删。
- 无独立账号的未成年学员可通过监护人完成实名就绪。
- 桌面和手机端完成相同核心操作，无横向溢出。
- PostgreSQL 备份和恢复演练通过。
- NAS 生产配置校验通过，但阶段 1A 不承诺付款、评价或住宿业务可用。
