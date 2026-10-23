from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_models import (
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
)
from app.operations.cloud_auth_security import require_no_maintenance
from app.operations.cloud_models import CloudBackupJob, CloudBackupTarget
from app.operations.cloud_schemas import (
    PROVIDERS,
    CloudBackupResponse,
    CloudCopyRouting,
    CloudJobResponse,
    CloudTargetResponse,
    CloudTargetUpdate,
    validate_backup_id,
)
from app.operations.heartbeat import record_service_heartbeat
from app.operations.models import OperationsServiceHeartbeat
from app.security.encryption import EnvelopeCipher

CLOUD_BACKUP_SERVICE_KEY = "cloud_backup"
SAFE_ERROR_CODES = frozenset(
    {
        "cloud_connector_failed",
        "cloud_redirect_rejected",
        "cloud_remote_corrupt",
        "cloud_local_invalid",
        "cloud_no_local_backup",
        "cloud_disk_space",
        "cloud_invalid_path",
        "cloud_backup_failed",
        "cloud_worker_timeout",
        "cloud_credentials_invalid",
        "cloud_config_changed",
        "cloud_authorization_invalid",
    }
)


def _problem(code: str, detail: str, status: int = 409) -> ProblemError:
    return ProblemError(status=status, code=code, detail=detail)


def _public_target(
    provider: str,
    row: CloudBackupTarget | None,
    username: str | None = None,
    selected_directory: str | None = None,
    authorization_mode: str | None = None,
) -> CloudTargetResponse:
    return CloudTargetResponse(
        provider=provider,
        configured=bool(row and row.password_ciphertext),
        enabled=row.enabled if row else False,
        remote_path=row.remote_path if row else None,
        username=username,
        credential_present=bool(row and row.password_ciphertext),
        revision=row.revision if row else 0,
        connection_status=row.connection_status if row else "untested",
        last_tested_at=row.last_tested_at if row else None,
        last_success_at=row.last_success_at if row else None,
        last_backup_id=row.last_backup_id if row else None,
        last_error_code=row.last_error_code if row else None,
        managed=bool(row and row.managed_candidate_id),
        authorization_status=row.authorization_status if row else "manual",
        selected_directory=selected_directory,
        authorization_mode=authorization_mode,
    )


def public_targets(db: Session) -> list[CloudTargetResponse]:
    rows = {row.provider: row for row in db.scalars(select(CloudBackupTarget))}
    return [
        _public_target(
            p,
            rows.get(p),
            selected_directory=_selected_directory(db, rows.get(p)),
            authorization_mode=_authorization_mode(db, rows.get(p)),
        )
        for p in PROVIDERS
    ]


def _selected_directory(db: Session, row: CloudBackupTarget | None) -> str | None:
    if row is None or row.managed_candidate_id is None:
        return None
    return db.scalar(
        select(CloudAuthCandidate.selected_directory).where(
            CloudAuthCandidate.id == row.managed_candidate_id
        )
    )


def _authorization_mode(db: Session, row: CloudBackupTarget | None) -> str | None:
    if row is None or row.managed_candidate_id is None:
        return None
    return db.scalar(
        select(CloudAuthCandidate.mode).where(CloudAuthCandidate.id == row.managed_candidate_id)
    )


class CloudBackupService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher) -> None:
        self.db, self.cipher = db, cipher

    def _field(self, provider: str, name: str) -> str:
        return f"cloud-backup:{provider}:{name}"

    def _lock(self, provider: str) -> CloudBackupTarget | None:
        if provider not in PROVIDERS:
            raise _problem("cloud_provider_invalid", "不支持的网盘", 422)
        # Also serialize the first creation, where SELECT FOR UPDATE has no row.
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": 62000 + PROVIDERS.index(provider)}
        )
        return self.db.scalar(
            select(CloudBackupTarget)
            .where(CloudBackupTarget.provider == provider)
            .with_for_update()
        )

    def _live(self, provider: str) -> CloudBackupJob | None:
        return self.db.scalar(
            select(CloudBackupJob).where(
                CloudBackupJob.provider == provider,
                CloudBackupJob.status.in_(("queued", "running")),
            )
        )

    def public(self) -> CloudBackupResponse:
        rows = {r.provider: r for r in self.db.scalars(select(CloudBackupTarget))}
        targets = [
            _public_target(
                p,
                rows.get(p),
                self.cipher.decrypt(
                    rows[p].username_ciphertext, field_name=self._field(p, "username")
                )
                if p in rows and rows[p].username_ciphertext
                else None,
                _selected_directory(self.db, rows.get(p)),
                _authorization_mode(self.db, rows.get(p)),
            )
            for p in PROVIDERS
        ]
        heartbeat = self.db.get(OperationsServiceHeartbeat, CLOUD_BACKUP_SERVICE_KEY)
        jobs = self.db.scalars(
            select(CloudBackupJob)
            .order_by(CloudBackupJob.created_at.desc(), CloudBackupJob.id)
            .limit(50)
        )
        return CloudBackupResponse(
            targets=targets,
            jobs=[CloudJobResponse.model_validate(j) for j in jobs],
            worker_last_seen_at=heartbeat.last_seen_at if heartbeat else None,
        )

    def save(self, provider: str, input: CloudTargetUpdate) -> CloudTargetResponse:
        with atomic_write_transaction(self.db):
            row = self._lock(provider)
            require_no_maintenance(self.db)
            self._not_switching(provider, row)
            if input.remote_path == "/" and (row is None or row.managed_candidate_id is None):
                raise _problem("cloud_invalid_path", "手动配置不能使用连接器根目录", 422)
            if row is not None and row.managed_candidate_id:
                username = self.cipher.decrypt(
                    row.username_ciphertext, field_name=self._field(provider, "username")
                )
                if (
                    input.remote_path != row.remote_path
                    or input.username != username
                    or input.password
                    and input.password.get_secret_value()
                ):
                    raise _problem(
                        "cloud_config_changed", "受管授权的目录和凭据只能通过重新绑定修改"
                    )
                if input.enabled:
                    raise _problem(
                        "cloud_provider_acceptance_required",
                        "此提供商尚未完成真实备份回读及隔离恢复验收，请先手动复制验证，暂不能开启自动复制",
                    )
            if self._live(provider):
                raise _problem("cloud_job_live", "任务进行中，请完成后再修改配置")
            password = input.password.get_secret_value() if input.password else ""
            if (row is None or not row.password_ciphertext) and not password:
                raise _problem("cloud_credentials_required", "首次保存需要填写连接器密码", 422)
            changed = row is None or row.remote_path != input.remote_path
            if row is not None:
                changed = changed or input.username != (
                    self.cipher.decrypt(
                        row.username_ciphertext, field_name=self._field(provider, "username")
                    )
                    if row.username_ciphertext
                    else ""
                )
                if password:
                    changed = changed or password != (
                        self.cipher.decrypt(
                            row.password_ciphertext, field_name=self._field(provider, "password")
                        )
                        if row.password_ciphertext
                        else ""
                    )
            if input.enabled and (changed or row is None or row.connection_status != "ok"):
                raise _problem("cloud_test_required", "启用前请先完成当前配置的写入读取测试")
            if row is None:
                row = CloudBackupTarget(
                    provider=provider,
                    remote_path=input.remote_path,
                    username_ciphertext=self.cipher.encrypt(
                        input.username, field_name=self._field(provider, "username")
                    ),
                    password_ciphertext=self.cipher.encrypt(
                        password, field_name=self._field(provider, "password")
                    ),
                    enabled=False,
                    revision=1,
                    connection_status="untested",
                )
                self.db.add(row)
            elif changed:
                row.revision += 1
                row.remote_path = input.remote_path
                row.username_ciphertext = self.cipher.encrypt(
                    input.username, field_name=self._field(provider, "username")
                )
                if password:
                    row.password_ciphertext = self.cipher.encrypt(
                        password, field_name=self._field(provider, "password")
                    )
                row.connection_status = "untested"
                row.last_tested_at = row.last_success_at = None
                row.last_backup_id = row.last_error_code = None
            row.enabled = input.enabled
            self.db.flush()
        return _public_target(
            provider,
            row,
            input.username,
            _selected_directory(self.db, row),
            _authorization_mode(self.db, row),
        )

    def _enqueue(
        self,
        row: CloudBackupTarget,
        kind: str,
        backup_id: str | None = None,
        *,
        automatic: bool = False,
    ) -> CloudBackupJob:
        self._not_switching(row.provider, row)
        if row.authorization_status == "reauthorization_required":
            raise _problem("cloud_authorization_invalid", "授权已失效，请重新绑定")
        if self._live(row.provider):
            raise _problem("cloud_job_live", "此网盘已有待处理任务")
        if kind == "copy" and (
            row.connection_status != "ok"
            or (not row.enabled and (automatic or not row.managed_candidate_id))
        ):
            raise _problem("cloud_test_required", "请先测试并启用此网盘")
        job = CloudBackupJob(
            provider=row.provider,
            kind=kind,
            revision=row.revision,
            status="queued",
            backup_id=backup_id,
            automatic_backup_id=backup_id if automatic else None,
            created_at=datetime.now(UTC),
        )
        self.db.add(job)
        self.db.flush()
        return job

    def enqueue(self, provider: str, kind: str) -> CloudJobResponse:
        if kind not in ("test", "copy"):
            raise _problem("cloud_job_invalid", "任务类型无效", 422)
        with atomic_write_transaction(self.db):
            row = self._lock(provider)
            require_no_maintenance(self.db)
            if row is None:
                raise _problem("cloud_not_configured", "请先保存网盘配置")
            job = self._enqueue(row, kind)
        return CloudJobResponse.model_validate(job)

    def _not_switching(self, provider: str, row: CloudBackupTarget | None) -> None:
        if (row and row.switch_operation_id) or self._switching(provider):
            raise _problem("cloud_target_busy", "目标正在切换授权，请等待旧任务结束")

    def _switching(self, provider: str) -> bool:
        return (
            self.db.scalar(
                select(CloudAuthSession.id)
                .where(CloudAuthSession.provider == provider, CloudAuthSession.state == "applying")
                .limit(1)
            )
            is not None
        )

    def sync(self, backup_id: str | None) -> dict[str, Any]:
        if backup_id is not None:
            validate_backup_id(backup_id)
        count = 0
        with atomic_write_transaction(self.db):
            if backup_id:
                for provider in PROVIDERS:
                    row = self._lock(provider)
                    if (
                        row is None
                        or not row.enabled
                        or row.connection_status != "ok"
                        or row.authorization_status == "reauthorization_required"
                        or row.switch_operation_id is not None
                        or self._switching(provider)
                        or self._live(provider)
                    ):
                        continue
                    seen = self.db.scalar(
                        select(CloudBackupJob.id).where(
                            CloudBackupJob.provider == provider,
                            CloudBackupJob.revision == row.revision,
                            CloudBackupJob.automatic_backup_id == backup_id,
                        )
                    )
                    if seen is None:
                        self._enqueue(row, "copy", backup_id, automatic=True)
                        count += 1
        return {"ok": True, "enqueued": count}

    def _heartbeat(self) -> None:
        record_service_heartbeat(
            self.db,
            service_key=CLOUD_BACKUP_SERVICE_KEY,
            succeeded=True,
            occurred_at=datetime.now(UTC),
        )

    def claim(self) -> dict[str, Any]:
        with atomic_write_transaction(self.db):
            self._heartbeat()
            # Lost host workers cannot leave the UI permanently locked.
            expired = self.db.scalars(
                select(CloudBackupJob)
                .where(
                    CloudBackupJob.status == "running",
                    CloudBackupJob.lease_seen_at < datetime.now(UTC) - timedelta(minutes=30),
                )
                .with_for_update(skip_locked=True)
            )
            for old in expired:
                old.status, old.error_code, old.completed_at = (
                    "failed",
                    "cloud_worker_timeout",
                    datetime.now(UTC),
                )
                target = self.db.scalar(
                    select(CloudBackupTarget)
                    .where(CloudBackupTarget.provider == old.provider)
                    .with_for_update()
                )
                if target and target.revision == old.revision:
                    target.last_error_code = old.error_code
            self.db.flush()
            job = self.db.scalar(
                select(CloudBackupJob)
                .where(CloudBackupJob.status == "queued")
                .order_by(CloudBackupJob.created_at, CloudBackupJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return {"job": None}
            row = self.db.scalar(
                select(CloudBackupTarget)
                .where(CloudBackupTarget.provider == job.provider)
                .with_for_update()
            )
            if row is None or row.revision != job.revision:
                job.status, job.error_code, job.completed_at = (
                    "failed",
                    "cloud_config_changed",
                    datetime.now(UTC),
                )
                return {"job": None}
            switch = (
                self.db.get(CloudAuthOperation, row.switch_operation_id)
                if row.switch_operation_id
                else None
            )
            if (
                row.authorization_status == "reauthorization_required"
                or switch
                and job.created_at > switch.created_at
            ):
                job.status, job.error_code, job.completed_at = (
                    "failed",
                    "cloud_config_changed",
                    datetime.now(UTC),
                )
                return {"job": None}
            try:
                connector_id = None
                if row.managed_candidate_id:
                    candidate = self.db.get(CloudAuthCandidate, row.managed_candidate_id)
                    if (
                        candidate is None
                        or candidate.state != "active"
                        or candidate.provider != row.provider
                    ):
                        raise ValueError("managed connector identity unavailable")
                    connector_id = candidate.connector_id
                credentials = {
                    name: self.cipher.decrypt(
                        getattr(row, name + "_ciphertext"),
                        field_name=self._field(row.provider, name),
                    )
                    for name in ("username", "password")
                }
            except ValueError:
                job.status, job.error_code, job.completed_at = (
                    "failed",
                    "cloud_credentials_invalid",
                    datetime.now(UTC),
                )
                row.last_error_code = job.error_code
                return {"job": None}
            job.status, job.started_at = "running", datetime.now(UTC)
            job.lease_seen_at = job.started_at
            return {
                "job": {
                    "id": str(job.id),
                    "provider": job.provider,
                    "kind": job.kind,
                    "revision": job.revision,
                    "remote_path": row.remote_path,
                    **CloudCopyRouting(
                        managed_auth=bool(row.managed_candidate_id), connector_id=connector_id
                    ).model_dump(mode="json"),
                    **credentials,
                    "backup_id": job.backup_id,
                }
            }

    def heartbeat(self, *, job_id: str, revision: int) -> dict[str, Any]:
        with atomic_write_transaction(self.db):
            job = self.db.scalar(
                select(CloudBackupJob)
                .where(CloudBackupJob.id == UUID(str(job_id)))
                .with_for_update()
            )
            if job is None or job.status != "running" or job.revision != revision:
                raise _problem("cloud_job_stale", "任务已失效")
            job.lease_seen_at = datetime.now(UTC)
            self._heartbeat()
        return {"ok": True}

    def finish(
        self,
        *,
        job_id: str,
        revision: int,
        success: bool,
        error_code: str | None,
        backup_id: str | None,
    ) -> dict[str, Any]:
        if backup_id is not None:
            validate_backup_id(backup_id)
        with atomic_write_transaction(self.db):
            job = self.db.scalar(
                select(CloudBackupJob)
                .where(CloudBackupJob.id == UUID(str(job_id)))
                .with_for_update()
            )
            if job is None or job.status != "running" or job.revision != revision:
                raise _problem("cloud_job_stale", "任务已失效")
            if (
                success
                and job.kind == "copy"
                and (backup_id is None or (job.backup_id and backup_id != job.backup_id))
            ):
                raise _problem("cloud_backup_invalid", "备份标识不匹配", 422)
            self._heartbeat()
            now = datetime.now(UTC)
            job.status = "succeeded" if success else "failed"
            job.completed_at = now
            job.error_code = (
                None
                if success
                else (error_code if error_code in SAFE_ERROR_CODES else "cloud_backup_failed")
            )
            if job.kind == "copy" and backup_id:
                job.backup_id = backup_id
            row = self.db.scalar(
                select(CloudBackupTarget)
                .where(CloudBackupTarget.provider == job.provider)
                .with_for_update()
            )
            if row and row.revision == revision:
                row.last_error_code = job.error_code
                if row.managed_candidate_id and job.error_code == "cloud_authorization_invalid":
                    row.authorization_status = "reauthorization_required"
                if job.kind == "test":
                    row.last_tested_at = now
                    row.connection_status = "ok" if success else "failed"
                elif success:
                    row.last_success_at, row.last_backup_id = now, backup_id
            self.db.flush()
        return {"ok": True}
