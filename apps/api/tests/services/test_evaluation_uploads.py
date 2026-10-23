from __future__ import annotations

import io
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image
from sqlalchemy import Engine, delete, event, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.errors import (
    EvaluationResourceNotFound,
    EvaluationUploadLimitReached,
    MediaValidationFailed,
)
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore, ValidatedImage
from app.evaluations.processing_models import (
    EvaluationAdapterCertification,
    SystemEvaluationAttempt,
)
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    EvaluationQuarantineCleanup,
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
    SystemEvaluationUploadSession,
    SystemEvaluationUploadSessionMedia,
)
from app.evaluations.upload_cleanup import drain_quarantine_cleanup
from app.evaluations.upload_service import EvaluationUploadService
from app.identity.models import Person, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.teaching.models import Cohort, GameProject, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime.now(UTC).replace(microsecond=0)


@dataclass(frozen=True)
class AtomicMediaScenario:
    engine: Engine
    store: PrivateMediaStore
    actor_user_id: UUID
    actor_person_id: UUID


@dataclass(frozen=True)
class TaskCreationScenario:
    engine: Engine
    store: PrivateMediaStore
    actor_user_id: UUID
    actor_person_id: UUID
    admin_user_id: UUID
    admin_person_id: UUID
    project_id: UUID
    profile_id: UUID
    cohort_id: UUID
    class_id: UUID
    admission_case_id: UUID
    enrollment_id: UUID


def _store(tmp_path: Path, *, initialize: bool = True) -> PrivateMediaStore:
    store = PrivateMediaStore(
        root=tmp_path / "private-media",
        quarantine_root=tmp_path / "private-quarantine",
        max_bytes=20 * 1024 * 1024,
        max_pixels=50_000_000,
        preview_max_edge=1600,
        expected_identity=UUID("ab70db93-85ba-48d1-9195-a6edf2f693db"),
    )
    if initialize:
        store.initialize_store_identity()
    return store


def _image_bytes(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (48, 32), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _stage(
    scenario: AtomicMediaScenario,
    *,
    color: tuple[int, int, int],
) -> ValidatedImage:
    return scenario.store.stage(
        io.BytesIO(_image_bytes(color)),
        declared_filename="evaluation.png",
    )


def _database_counts(engine: Engine, *, actor_user_id: UUID) -> tuple[int, int]:
    with Session(engine) as observer:
        return (
            observer.scalar(
                select(func.count(EvaluationMediaAsset.id)).where(
                    EvaluationMediaAsset.uploaded_by_user_id == actor_user_id
                )
            )
            or 0,
            observer.scalar(select(func.count(EvaluationMediaBlob.id))) or 0,
        )


def _stored_files(store: PrivateMediaStore) -> set[Path]:
    return {
        path.relative_to(store.root)
        for path in store.root.rglob("*")
        if path.is_file() and path.name != ".erp-store-identity"
    }


def test_missing_file_cleanup_requires_explicit_store_identity(tmp_path: Path) -> None:
    store = _store(tmp_path, initialize=False)
    try:
        with pytest.raises(RuntimeError, match="identity"):
            store.discard_staged_key(".owned-" + "a" * 32)
    finally:
        store.close()


def test_explicit_identity_bootstraps_existing_pair_without_changing_files(tmp_path: Path) -> None:
    store = _store(tmp_path, initialize=False)
    try:
        image = store.stage(io.BytesIO(_image_bytes((22, 55, 88))), declared_filename="old.png")
        before = image.quarantine_path.read_bytes()
        store.initialize_store_identity()
        store.initialize_store_identity()
        assert image.quarantine_path.read_bytes() == before
        store.discard_staged_key(".owned-" + "b" * 32)
        assert image.quarantine_path.is_file()
    finally:
        store.close()


def test_identity_verify_cli_does_not_create_or_modify_files(
    tmp_path: Path, test_settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config
    from app.evaluations import media_store

    store = _store(tmp_path)
    test_settings.evaluation_media_root = store.root
    test_settings.evaluation_media_quarantine_root = store.quarantine_root
    test_settings.evaluation_media_store_id = store.expected_identity
    store.close()
    monkeypatch.setattr(config, "get_settings", lambda: test_settings)
    monkeypatch.setattr("sys.argv", ["media_store", "--verify-store-identity"])

    def forbidden_write(*args, **kwargs):
        raise AssertionError("verification must not modify storage")

    monkeypatch.setattr(Path, "mkdir", forbidden_write)
    monkeypatch.setattr(media_store.os, "fchmod", forbidden_write)
    monkeypatch.setattr(media_store.os, "write", forbidden_write)
    assert media_store.main() == 0


def test_identity_verify_cli_fails_closed_without_mounted_roots(
    tmp_path: Path, test_settings, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import config
    from app.evaluations import media_store

    test_settings.evaluation_media_root = tmp_path / "missing-media"
    test_settings.evaluation_media_quarantine_root = tmp_path / "missing-quarantine"
    test_settings.evaluation_media_store_id = uuid4()
    monkeypatch.setattr(config, "get_settings", lambda: test_settings)
    monkeypatch.setattr("sys.argv", ["media_store", "--verify-store-identity"])
    with pytest.raises(SystemExit) as failure:
        media_store.main()
    assert failure.value.code == 1
    assert not test_settings.evaluation_media_root.exists()
    assert not test_settings.evaluation_media_quarantine_root.exists()


@pytest.mark.parametrize("consumer", ["api", "scheduler"])
def test_relative_roots_match_cli_identity_outside_repository_cwd(
    tmp_path: Path, test_settings, monkeypatch: pytest.MonkeyPatch, consumer: str,
) -> None:
    from types import SimpleNamespace

    from app.core import config
    from app.evaluations import media_store, upload_cleanup
    from app.evaluations.dependencies import get_private_media_store

    repository = tmp_path / "repo"
    monkeypatch.setattr(config, "_REPOSITORY_ROOT", repository)
    monkeypatch.setattr(config, "_IGNORED_RUNTIME_ROOT", repository / "runtime")
    test_settings.evaluation_media_root = Path("runtime/media")
    test_settings.evaluation_media_quarantine_root = Path("runtime/quarantine")
    test_settings.evaluation_media_store_id = uuid4()
    monkeypatch.setattr(config, "get_settings", lambda: test_settings)
    monkeypatch.setattr("sys.argv", ["media_store", "--initialize-store-identity"])
    monkeypatch.chdir(tmp_path)
    assert media_store.main() == 0

    if consumer == "api":
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=test_settings)))
        dependency = get_private_media_store(request)
        store = next(dependency)
        try:
            store.verify_store_identity()
        finally:
            dependency.close()
    else:
        class VerifiedCleanupReached(Exception):
            pass

        def verify_before_database(self, **kwargs):
            self.store.verify_store_identity()
            raise VerifiedCleanupReached

        monkeypatch.setattr(EvaluationUploadService, "expire_stale_sessions", verify_before_database)
        with Session() as db, pytest.raises(VerifiedCleanupReached):
            upload_cleanup.cleanup_expired_uploads(db, settings=test_settings, now=NOW)
    assert not (tmp_path / "runtime").exists()


def test_existing_markers_do_not_authorize_unconfigured_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.expected_identity = None
    try:
        with pytest.raises(RuntimeError, match="identity"):
            store.discard_staged_key(".owned-" + "e" * 32)
    finally:
        store.close()


@pytest.mark.parametrize("invalid_role", ["media", "quarantine"])
def test_identity_rejects_mixed_pair_without_claiming_unmarked_root(
    tmp_path: Path, invalid_role: str,
) -> None:
    store = _store(tmp_path, initialize=False)
    bad_root = store.root if invalid_role == "media" else store.quarantine_root
    other_root = store.quarantine_root if invalid_role == "media" else store.root
    marker = bad_root / ".erp-store-identity"
    marker.write_text(f"{uuid4()}\n{invalid_role}\n")
    marker.chmod(0o600)
    try:
        with pytest.raises(RuntimeError, match="identity"):
            store.initialize_store_identity()
        with pytest.raises(RuntimeError, match="identity"):
            store.discard_staged_key(".owned-" + "c" * 32)
        assert not (other_root / ".erp-store-identity").exists()
    finally:
        store.close()


@pytest.mark.parametrize("changed_role", ["media", "quarantine"])
def test_cleanup_rechecks_live_root_after_volume_replacement(
    tmp_path: Path, changed_role: str,
) -> None:
    store = _store(tmp_path)
    changed_root = store.root if changed_role == "media" else store.quarantine_root
    changed_root.rename(tmp_path / "original-volume")
    changed_root.mkdir(mode=0o700)
    try:
        with pytest.raises(RuntimeError, match="identity"):
            store.discard_staged_key(".owned-" + "d" * 32)
    finally:
        store.close()


def test_quarantine_key_resumes_the_validated_image_without_a_public_path(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        image = store.stage(
            io.BytesIO(_image_bytes((70, 140, 210))),
            declared_filename="evaluation.png",
        )
        key = store.quarantine_storage_key(image)

        resumed = store.resume_staged(
            key,
            sha256=image.sha256,
            mime_type=image.mime_type,
            extension=image.extension,
            byte_size=image.byte_size,
            width=image.width,
            height=image.height,
        )

        assert "/" not in key
        assert ".." not in key
        assert str(store.quarantine_root) not in key
        assert resumed.quarantine_path.name == key
        assert resumed.sha256 == image.sha256
        store.discard_staged(resumed)
        assert {path.name for path in store.quarantine_root.iterdir()} == {".erp-store-identity"}
    finally:
        store.close()


def test_resume_staged_rejects_changed_quarantine_content(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        image = store.stage(
            io.BytesIO(_image_bytes((90, 160, 220))),
            declared_filename="evaluation.png",
        )
        key = store.quarantine_storage_key(image)
        (store.quarantine_root / key).write_bytes(b"changed")

        with pytest.raises(MediaValidationFailed) as error:
            store.resume_staged(
                key,
                sha256=image.sha256,
                mime_type=image.mime_type,
                extension=image.extension,
                byte_size=image.byte_size,
                width=image.width,
                height=image.height,
            )

        assert error.value.internal_reason == "decode_failed"

        store.discard_staged_key(key)
        assert {path.name for path in store.quarantine_root.iterdir()} == {".erp-store-identity"}
    finally:
        store.close()


@pytest.fixture
def atomic_media_scenario(
    postgres_engine: Engine,
    tmp_path: Path,
) -> Iterator[AtomicMediaScenario]:
    store = _store(tmp_path)
    marker = uuid4().hex
    with Session(postgres_engine) as setup_db, setup_db.begin():
        person = Person(
            full_name="原子媒体测试账号",
            identity_ciphertext=f"atomic-media:{marker}",
            identity_digest=(marker * 2)[:64],
            phone_ciphertext=None,
            phone_digest=None,
            email=None,
            sex="female",
            date_of_birth=date(1990, 1, 2),
        )
        setup_db.add(person)
        setup_db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=(marker[::-1] * 2)[:64],
            email_normalized=f"atomic-media-{marker[:12]}@example.test",
            password_hash="atomic-media-test-password-hash",
            status="active",
            force_password_change=False,
        )
        setup_db.add(account)
        setup_db.flush()
        add_test_primary_login_identifier(setup_db, account)
        scenario = AtomicMediaScenario(
            engine=postgres_engine,
            store=store,
            actor_user_id=account.id,
            actor_person_id=person.id,
        )

    try:
        yield scenario
    finally:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            quarantine_keys = list(
                cleanup_db.scalars(
                    select(
                        SystemEvaluationUploadSessionMedia.quarantine_storage_key
                    ).join(
                        SystemEvaluationUploadSession,
                        SystemEvaluationUploadSession.id
                        == SystemEvaluationUploadSessionMedia.session_id,
                    ).where(
                        SystemEvaluationUploadSession.created_by_user_id
                        == scenario.actor_user_id
                    )
                )
            )
            for quarantine_key in quarantine_keys:
                store.discard_staged_key(quarantine_key)
            session_ids = select(SystemEvaluationUploadSession.id).where(
                SystemEvaluationUploadSession.created_by_user_id
                == scenario.actor_user_id
            )
            cleanup_db.execute(
                delete(SystemEvaluationUploadSessionMedia).where(
                    SystemEvaluationUploadSessionMedia.session_id.in_(session_ids)
                )
            )
            cleanup_db.execute(
                delete(SystemEvaluationUploadSession).where(
                    SystemEvaluationUploadSession.created_by_user_id
                    == scenario.actor_user_id
                )
            )
            blob_ids = list(
                cleanup_db.scalars(
                    select(EvaluationMediaAsset.blob_id).where(
                        EvaluationMediaAsset.uploaded_by_user_id
                        == scenario.actor_user_id
                    )
                )
            )
            cleanup_db.execute(
                delete(EvaluationMediaAsset).where(
                    EvaluationMediaAsset.uploaded_by_user_id
                    == scenario.actor_user_id
                )
            )
            if blob_ids:
                cleanup_db.execute(
                    delete(EvaluationMediaBlob).where(
                        EvaluationMediaBlob.id.in_(blob_ids),
                        EvaluationMediaBlob.id.not_in(
                            select(EvaluationMediaAsset.blob_id)
                        ),
                    )
                )
            cleanup_db.execute(
                delete(UserAccount).where(
                    UserAccount.id == scenario.actor_user_id
                )
            )
            cleanup_db.execute(
                delete(Person).where(Person.id == scenario.actor_person_id)
            )
        store.close()


@pytest.fixture
def task_creation_scenario(
    atomic_media_scenario: AtomicMediaScenario,
) -> Iterator[TaskCreationScenario]:
    base = atomic_media_scenario
    marker = uuid4().hex
    with Session(base.engine) as setup_db, setup_db.begin():
        project = GameProject(
            name=f"原子任务项目-{marker[:10]}",
            sort_order=999,
            is_active=True,
            created_by_user_id=base.actor_user_id,
        )
        setup_db.add(project)
        setup_db.flush()
        profile = ProjectAnalysisProfile(
            project_id=project.id,
            version=1,
            mode="manual_only",
            adapter_id=None,
            adapter_version=None,
            scoring_rule_version=None,
            required_fields=["score", "grade"],
            media_slots=[
                {"key": "profile", "label": "个人数据", "required": True},
                {
                    "key": "match_result",
                    "label": "结算战绩",
                    "required": True,
                },
                {
                    "key": "team_detail",
                    "label": "队伍明细",
                    "required": True,
                },
            ],
            result_schema_version="1",
            confidence_thresholds={},
            auto_publish_warning_levels=[],
            publish_mode="review_required",
            effective_from=NOW - timedelta(days=365),
            configured_by_user_id=base.actor_user_id,
            change_reason="task creation test profile",
        )
        setup_db.add(profile)
        setup_db.flush()
        admin_marker = uuid4().hex
        admin_person = Person(
            full_name="原子任务管理员",
            identity_ciphertext=f"atomic-task-admin:{admin_marker}",
            identity_digest=(admin_marker * 2)[:64],
            phone_ciphertext=None,
            phone_digest=None,
            email=None,
            sex="female",
            date_of_birth=date(1990, 1, 2),
        )
        setup_db.add(admin_person)
        setup_db.flush()
        admin_account = UserAccount(
            person_id=admin_person.id,
            login_phone_digest=(admin_marker[::-1] * 2)[:64],
            email_normalized=f"atomic-task-admin-{admin_marker[:12]}@example.test",
            password_hash="atomic-task-admin-test-password-hash",
            status="active",
            force_password_change=False,
        )
        setup_db.add(admin_account)
        setup_db.flush()
        add_test_primary_login_identifier(setup_db, admin_account)
        setup_db.add(
            UserRole(
                user_id=admin_account.id,
                role_id=ROLES["super_admin"].id,
            )
        )
        setup_db.add(
            StudentProfile(
                person_id=base.actor_person_id,
                student_number=f"ATOMIC-{marker[:12]}",
                status="active",
            )
        )
        setup_db.add(
            UserRole(
                user_id=base.actor_user_id,
                role_id=ROLES["student"].id,
            )
        )
        upload_permission = setup_db.scalar(
            select(Permission).where(Permission.key == "evaluation_media.upload")
        )
        assert upload_permission is not None
        setup_db.add(
            UserPermissionOverride(
                user_id=base.actor_user_id,
                permission_id=upload_permission.id,
                effect="allow",
                scope_kind="self",
                scope_id=None,
                valid_from=NOW - timedelta(days=365),
                valid_until=NOW + timedelta(days=365),
                created_by_user_id=base.actor_user_id,
                grant_reason="system evaluation upload fixture",
            )
        )
        cohort = Cohort(
            name=f"原子任务营期-{marker[:10]}",
            starts_at=NOW + timedelta(days=2),
            ends_at=NOW + timedelta(days=23),
            default_check_in_at=NOW + timedelta(days=2),
            default_check_out_at=NOW + timedelta(days=23),
            status="active",
            created_by_user_id=base.actor_user_id,
        )
        setup_db.add(cohort)
        setup_db.flush()
        teaching_class = TeachingClass(
            cohort_id=cohort.id,
            project_id=project.id,
            name=f"原子任务班级-{marker[:10]}",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
            is_active=True,
            created_by_user_id=base.actor_user_id,
        )
        setup_db.add(teaching_class)
        setup_db.flush()
        admission_case = AdmissionCase(
            student_person_id=base.actor_person_id,
            status="ready",
            agreed_tuition_cents=0,
            created_by_user_id=base.actor_user_id,
        )
        setup_db.add(admission_case)
        setup_db.flush()
        enrollment = Enrollment(
            admission_case_id=admission_case.id,
            student_person_id=base.actor_person_id,
            cohort_id=cohort.id,
            class_id=teaching_class.id,
            agreed_tuition_cents=0,
            status="active",
            confirmed_at=NOW - timedelta(days=30),
            created_by_user_id=base.actor_user_id,
        )
        setup_db.add(enrollment)
        setup_db.flush()
        admission_case.status = "enrolled"
        admission_case.current_enrollment_id = enrollment.id
        setup_db.flush()
        scenario = TaskCreationScenario(
            engine=base.engine,
            store=base.store,
            actor_user_id=base.actor_user_id,
            actor_person_id=base.actor_person_id,
            admin_user_id=admin_account.id,
            admin_person_id=admin_person.id,
            project_id=project.id,
            profile_id=profile.id,
            cohort_id=cohort.id,
            class_id=teaching_class.id,
            admission_case_id=admission_case.id,
            enrollment_id=enrollment.id,
        )

    try:
        yield scenario
    finally:
        with Session(base.engine) as cleanup_db, cleanup_db.begin():
            quarantine_keys = list(
                cleanup_db.scalars(
                    select(
                        SystemEvaluationUploadSessionMedia.quarantine_storage_key
                    ).join(
                        SystemEvaluationUploadSession,
                        SystemEvaluationUploadSession.id
                        == SystemEvaluationUploadSessionMedia.session_id,
                    ).where(
                        SystemEvaluationUploadSession.project_id
                        == scenario.project_id
                    )
                )
            )
            for quarantine_key in quarantine_keys:
                base.store.discard_staged_key(quarantine_key)

            for table_name, trigger_name in (
                (
                    "system_evaluation_task_media",
                    "system_evaluation_task_media_immutable",
                ),
                (
                    "system_evaluation_events",
                    "system_evaluation_events_immutable",
                ),
                (
                    "system_evaluation_result_versions",
                    "system_evaluation_results_immutable",
                ),
                (
                    "evaluation_adapter_certifications",
                    "evaluation_adapter_certifications_immutable",
                ),
                (
                    "system_evaluation_tasks",
                    "system_evaluation_tasks_no_delete",
                ),
            ):
                cleanup_db.execute(
                    text(
                        f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"
                    )
                )
            try:
                task_ids = list(
                    cleanup_db.scalars(
                        select(SystemEvaluationTask.id).where(
                            SystemEvaluationTask.project_id == scenario.project_id
                        )
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationAttempt).where(
                        SystemEvaluationAttempt.task_id.in_(task_ids)
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationTaskMedia).where(
                        SystemEvaluationTaskMedia.task_id.in_(task_ids)
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationEvent).where(
                        SystemEvaluationEvent.task_id.in_(task_ids)
                    )
                )
                cleanup_db.execute(
                    update(SystemEvaluationTask)
                    .where(SystemEvaluationTask.id.in_(task_ids))
                    .values(
                        current_result_version_id=None,
                        published_result_version_id=None,
                        revision_of_version_id=None,
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationManualDraft).where(
                        SystemEvaluationManualDraft.task_id.in_(task_ids)
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationResultVersion).where(
                        SystemEvaluationResultVersion.task_id.in_(task_ids)
                    )
                )
                cleanup_db.execute(
                    delete(SystemEvaluationTask).where(
                        SystemEvaluationTask.project_id == scenario.project_id
                    )
                )
                cleanup_db.execute(
                    delete(EvaluationAdapterCertification).where(
                        EvaluationAdapterCertification.project_id
                        == scenario.project_id
                    )
                )
            finally:
                for table_name, trigger_name in reversed(
                    (
                        (
                            "system_evaluation_task_media",
                            "system_evaluation_task_media_immutable",
                        ),
                        (
                            "system_evaluation_events",
                            "system_evaluation_events_immutable",
                        ),
                        (
                            "system_evaluation_result_versions",
                            "system_evaluation_results_immutable",
                        ),
                        (
                            "evaluation_adapter_certifications",
                            "evaluation_adapter_certifications_immutable",
                        ),
                        (
                            "system_evaluation_tasks",
                            "system_evaluation_tasks_no_delete",
                        ),
                    )
                ):
                    cleanup_db.execute(
                        text(
                            f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"
                        )
                    )

            session_ids = select(SystemEvaluationUploadSession.id).where(
                SystemEvaluationUploadSession.project_id == scenario.project_id
            )
            cleanup_db.execute(
                delete(SystemEvaluationUploadSessionMedia).where(
                    SystemEvaluationUploadSessionMedia.session_id.in_(session_ids)
                )
            )
            cleanup_db.execute(
                delete(SystemEvaluationUploadSession).where(
                    SystemEvaluationUploadSession.project_id == scenario.project_id
                )
            )
            blob_ids = list(
                cleanup_db.scalars(
                    select(EvaluationMediaAsset.blob_id).where(
                        EvaluationMediaAsset.project_id == scenario.project_id
                    )
                )
            )
            cleanup_db.execute(
                delete(EvaluationMediaAsset).where(
                    EvaluationMediaAsset.project_id == scenario.project_id
                )
            )
            if blob_ids:
                cleanup_db.execute(
                    delete(EvaluationMediaBlob).where(
                        EvaluationMediaBlob.id.in_(blob_ids),
                        EvaluationMediaBlob.id.not_in(
                            select(EvaluationMediaAsset.blob_id)
                        ),
                    )
                )
            cleanup_db.execute(
                text(
                    "ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"
                )
            )
            try:
                cleanup_db.execute(
                    delete(AuditEvent).where(
                        or_(
                            AuditEvent.actor_user_id.in_(
                                [scenario.actor_user_id, scenario.admin_user_id]
                            ),
                            AuditEvent.resource_id.in_(task_ids),
                        )
                    )
                )
            finally:
                cleanup_db.execute(
                    text(
                        "ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"
                    )
                )
            cleanup_db.execute(
                delete(ProjectAnalysisProfile).where(
                    ProjectAnalysisProfile.project_id == scenario.project_id
                )
            )
            cleanup_db.execute(
                delete(EvaluationIdempotencyKey).where(
                    EvaluationIdempotencyKey.actor_key.in_(
                        (
                            str(scenario.actor_user_id),
                            str(scenario.admin_user_id),
                        )
                    )
                )
            )
            admission_case = cleanup_db.get(
                AdmissionCase,
                scenario.admission_case_id,
            )
            if admission_case is not None:
                admission_case.status = "closed"
                admission_case.current_enrollment_id = None
                admission_case.closed_reason = "task fixture cleanup"
                cleanup_db.flush()
            cleanup_db.execute(
                delete(Enrollment).where(Enrollment.id == scenario.enrollment_id)
            )
            cleanup_db.execute(
                delete(AdmissionCase).where(
                    AdmissionCase.id == scenario.admission_case_id
                )
            )
            cleanup_db.execute(
                delete(TeachingClass).where(
                    TeachingClass.id == scenario.class_id
                )
            )
            cleanup_db.execute(
                delete(Cohort).where(Cohort.id == scenario.cohort_id)
            )
            cleanup_db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == scenario.actor_user_id,
                )
            )
            cleanup_db.execute(
                delete(UserRole).where(UserRole.user_id == scenario.actor_user_id)
            )
            cleanup_db.execute(
                delete(StudentProfile).where(
                    StudentProfile.person_id == scenario.actor_person_id
                )
            )
            cleanup_db.execute(
                delete(UserRole).where(UserRole.user_id == scenario.admin_user_id)
            )
            cleanup_db.execute(
                delete(UserAccount).where(
                    UserAccount.id == scenario.admin_user_id
                )
            )
            cleanup_db.execute(
                delete(Person).where(Person.id == scenario.admin_person_id)
            )
            cleanup_db.execute(
                delete(GameProject).where(GameProject.id == scenario.project_id)
            )


def test_register_validated_images_joins_the_callers_transaction(
    atomic_media_scenario: AtomicMediaScenario,
) -> None:
    scenario = atomic_media_scenario
    baseline_counts = _database_counts(
        scenario.engine,
        actor_user_id=scenario.actor_user_id,
    )
    images = [
        ("profile", _stage(scenario, color=(180, 20, 40))),
        ("match_result", _stage(scenario, color=(20, 180, 40))),
        ("team_detail", _stage(scenario, color=(20, 40, 180))),
    ]

    with Session(scenario.engine) as db:
        service = EvaluationMediaService(db, store=scenario.store)
        with db.begin():
            registered = service.register_validated_images_in_transaction(
                images,
                uploaded_by_user_id=scenario.actor_user_id,
                project_id=INITIAL_PROJECTS[0].id,
            )
            assert [asset.slot_key for asset in registered] == [
                "profile",
                "match_result",
                "team_detail",
            ]
            assert _database_counts(
                scenario.engine,
                actor_user_id=scenario.actor_user_id,
            ) == baseline_counts

    assert _database_counts(
        scenario.engine,
        actor_user_id=scenario.actor_user_id,
    ) == (baseline_counts[0] + 3, baseline_counts[1] + 3)
    assert len(_stored_files(scenario.store)) == 6


def test_register_validated_images_rolls_back_every_row_and_file(
    atomic_media_scenario: AtomicMediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = atomic_media_scenario
    baseline_counts = _database_counts(
        scenario.engine,
        actor_user_id=scenario.actor_user_id,
    )
    images = [
        ("profile", _stage(scenario, color=(190, 30, 50))),
        ("match_result", _stage(scenario, color=(30, 190, 50))),
        ("team_detail", _stage(scenario, color=(30, 50, 190))),
    ]

    with Session(scenario.engine) as db:
        service = EvaluationMediaService(db, store=scenario.store)
        real_create_asset = service._create_asset
        inserted_assets = 0

        def fail_after_second_asset(**kwargs: object):
            nonlocal inserted_assets
            asset = real_create_asset(**kwargs)
            inserted_assets += 1
            if inserted_assets == 2:
                raise RuntimeError("forced task creation failure")
            return asset

        monkeypatch.setattr(service, "_create_asset", fail_after_second_asset)
        with pytest.raises(RuntimeError, match="forced task creation failure"), db.begin():
            service.register_validated_images_in_transaction(
                images,
                uploaded_by_user_id=scenario.actor_user_id,
                project_id=INITIAL_PROJECTS[0].id,
            )

    assert inserted_assets == 2
    assert _database_counts(
        scenario.engine,
        actor_user_id=scenario.actor_user_id,
    ) == baseline_counts
    assert _stored_files(scenario.store) == set()
    assert not any(image.quarantine_path.exists() for _, image in images)


def test_register_validated_images_requires_a_caller_owned_transaction(
    atomic_media_scenario: AtomicMediaScenario,
) -> None:
    scenario = atomic_media_scenario
    image = _stage(scenario, color=(80, 90, 200))

    with Session(scenario.engine) as db:
        service = EvaluationMediaService(db, store=scenario.store)
        with pytest.raises(RuntimeError, match="caller-owned transaction"):
            service.register_validated_images_in_transaction(
                [("match_result", image)],
                uploaded_by_user_id=scenario.actor_user_id,
                project_id=INITIAL_PROJECTS[0].id,
            )

    assert not image.quarantine_path.exists()
    assert _stored_files(scenario.store) == set()


def test_upload_session_stores_opaque_slot_metadata_and_expires_in_30_minutes(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((40, 120, 200))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

    assert session.expires_at == occurred_at + timedelta(minutes=30)
    assert slot.slot_key == "match_result"
    assert slot.position == 2
    assert "/" not in slot.quarantine_storage_key
    assert ".." not in slot.quarantine_storage_key
    assert str(scenario.store.quarantine_root) not in slot.quarantine_storage_key
    assert (scenario.store.quarantine_root / slot.quarantine_storage_key).is_file()


def test_new_session_expires_and_cleans_bounded_stale_uploads(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        stale = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        slot = service.put_slot(
            stale.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((41, 121, 201))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        current = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at + timedelta(minutes=31),
        )

        expired = db.get(SystemEvaluationUploadSession, stale.id)
        assert expired is not None
        assert expired.status == "expired"
        assert current.status == "open"
        assert db.get(SystemEvaluationUploadSessionMedia, slot.id) is None
    assert not (
        scenario.store.quarantine_root / slot.quarantine_storage_key
    ).exists()


def test_upload_slot_replacement_removes_only_the_committed_old_file(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        old_slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((50, 130, 210))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        new_slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((60, 140, 220))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

    assert new_slot.id != old_slot.id
    assert not (
        scenario.store.quarantine_root / old_slot.quarantine_storage_key
    ).exists()
    assert (
        scenario.store.quarantine_root / new_slot.quarantine_storage_key
    ).is_file()


def test_upload_slot_replacement_rollback_preserves_old_and_discards_new(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        old_slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((70, 150, 230))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        def reject_commit(session_: Session) -> None:
            del session_
            raise RuntimeError("forced upload slot commit failure")

        event.listen(db, "before_commit", reject_commit, once=True)
        with pytest.raises(RuntimeError, match="forced upload slot commit failure"):
            service.put_slot(
                session.id,
                slot_key="match_result",
                stream=io.BytesIO(_image_bytes((80, 160, 240))),
                declared_filename="evaluation.png",
                declared_mime_type="image/png",
                actor_user_id=scenario.actor_user_id,
                occurred_at=occurred_at,
            )

    with Session(scenario.engine) as observer:
        current = observer.scalar(
            select(SystemEvaluationUploadSessionMedia).where(
                SystemEvaluationUploadSessionMedia.session_id == session.id,
                SystemEvaluationUploadSessionMedia.slot_key == "match_result",
            )
        )
        assert current is not None
        assert current.id == old_slot.id
    assert (
        scenario.store.quarantine_root / old_slot.quarantine_storage_key
    ).is_file()
    assert len(
        list(
            scenario.store.quarantine_root.glob(".owned-*")
        )
    ) == 1


def test_committed_replacement_cleanup_failure_is_retried_without_api_failure(
    task_creation_scenario: TaskCreationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        old_slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((71, 151, 231))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        real_discard = scenario.store.discard_staged_key
        failures = 0

        def fail_once(quarantine_storage_key: str) -> None:
            nonlocal failures
            if quarantine_storage_key == old_slot.quarantine_storage_key and failures == 0:
                failures += 1
                raise RuntimeError("forced quarantine cleanup failure")
            real_discard(quarantine_storage_key)

        monkeypatch.setattr(scenario.store, "discard_staged_key", fail_once)
        replacement = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((72, 152, 232))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        assert replacement.id != old_slot.id
        assert failures == 1
        assert (
            scenario.store.quarantine_root / old_slot.quarantine_storage_key
        ).is_file()
        service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at + timedelta(minutes=1),
        )

    assert not (
        scenario.store.quarantine_root / old_slot.quarantine_storage_key
    ).exists()


@pytest.mark.parametrize("failure", ["wrong_actor", "expired"])
def test_upload_slot_private_selector_failures_write_no_rows_or_files(
    task_creation_scenario: TaskCreationScenario,
    failure: str,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        actor_user_id = (
            uuid4() if failure == "wrong_actor" else scenario.actor_user_id
        )
        request_time = (
            occurred_at + timedelta(minutes=31)
            if failure == "expired"
            else occurred_at
        )

        with pytest.raises(EvaluationResourceNotFound) as error:
            service.put_slot(
                session.id,
                slot_key="match_result",
                stream=io.BytesIO(_image_bytes((90, 170, 240))),
                declared_filename="evaluation.png",
                declared_mime_type="image/png",
                actor_user_id=actor_user_id,
                occurred_at=request_time,
            )

        assert error.value.code == "evaluation_resource_not_found"
        assert db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id)).where(
                SystemEvaluationUploadSessionMedia.session_id == session.id
            )
        ) == 0
    assert {path.name for path in scenario.store.quarantine_root.iterdir()} == {".erp-store-identity"}


def test_global_cleanup_rejects_wrong_empty_store_without_retiring_upload(
    task_creation_scenario: TaskCreationScenario,
    tmp_path: Path,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        upload = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        slot = service.put_slot(
            upload.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((30, 80, 120))),
            declared_filename="evaluation.png", declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id, occurred_at=NOW,
        )
    wrong_store = _store(tmp_path / "wrong-empty-mount", initialize=False)
    try:
        with Session(scenario.engine) as db, pytest.raises(RuntimeError, match="identity"):
            EvaluationUploadService(db, store=wrong_store).expire_stale_sessions(
                occurred_at=NOW + timedelta(minutes=31),
            )
        with Session(scenario.engine) as observer:
            persisted = observer.get(SystemEvaluationUploadSession, upload.id)
            assert persisted is not None and persisted.status == "open"
            assert observer.get(SystemEvaluationUploadSessionMedia, slot.id) is not None
            assert observer.get(EvaluationQuarantineCleanup, slot.quarantine_storage_key) is None
        assert (scenario.store.quarantine_root / slot.quarantine_storage_key).is_file()
    finally:
        wrong_store.close()


def test_wrong_store_retains_durable_cleanup_and_correct_store_retries(
    task_creation_scenario: TaskCreationScenario,
    tmp_path: Path,
) -> None:
    scenario = task_creation_scenario
    image = scenario.store.stage(
        io.BytesIO(_image_bytes((41, 71, 101))), declared_filename="queued.png",
    )
    key = scenario.store.quarantine_storage_key(image)
    with Session(scenario.engine) as db, db.begin():
        db.add(EvaluationQuarantineCleanup(storage_key=key, next_attempt_at=NOW))
    wrong_store = _store(tmp_path / "empty-scheduler", initialize=False)
    try:
        with Session(scenario.engine) as db:
            assert drain_quarantine_cleanup(db, wrong_store, occurred_at=NOW) == (0, 1)
            queued = db.get(EvaluationQuarantineCleanup, key)
            assert queued is not None and queued.attempts == 1
            assert queued.next_attempt_at == NOW + timedelta(seconds=30)
        assert image.quarantine_path.is_file()
        with Session(scenario.engine) as db:
            assert drain_quarantine_cleanup(
                db, scenario.store, occurred_at=NOW + timedelta(seconds=31),
            ) == (1, 0)
            assert db.get(EvaluationQuarantineCleanup, key) is None
        assert not image.quarantine_path.exists()
    finally:
        wrong_store.close()


def test_verified_store_missing_file_cleanup_remains_idempotent(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    key = ".owned-" + uuid4().hex
    with Session(scenario.engine) as db, db.begin():
        db.add(EvaluationQuarantineCleanup(storage_key=key, next_attempt_at=NOW))
    with Session(scenario.engine) as db:
        assert drain_quarantine_cleanup(db, scenario.store, occurred_at=NOW) == (1, 0)
        assert drain_quarantine_cleanup(db, scenario.store, occurred_at=NOW) == (0, 0)
        assert db.get(EvaluationQuarantineCleanup, key) is None


def test_global_cleanup_expires_abandoned_upload_of_disabled_account(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        slot = service.put_slot(
            session.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((60, 90, 150))),
            declared_filename="evaluation.png", declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id, occurred_at=NOW,
        )
        db.execute(update(UserAccount).where(UserAccount.id == scenario.actor_user_id)
                   .values(status="disabled"))
        db.commit()
    with Session(scenario.engine) as maintenance_db:
        maintenance = EvaluationUploadService(maintenance_db, store=scenario.store)
        assert maintenance.expire_stale_sessions(occurred_at=NOW + timedelta(minutes=31)) >= 1
        expired = maintenance_db.get(SystemEvaluationUploadSession, session.id)
        assert expired is not None and expired.status == "expired"
        assert maintenance_db.scalar(select(SystemEvaluationUploadSessionMedia).where(
            SystemEvaluationUploadSessionMedia.session_id == session.id,
        )) is None
    assert not (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()


def test_cleanup_failure_is_durable_after_request_session_closes(
    task_creation_scenario: TaskCreationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = task_creation_scenario
    real_discard = scenario.store.discard_staged_key
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        slot = service.put_slot(
            session.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((80, 100, 150))),
            declared_filename="evaluation.png", declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id, occurred_at=NOW,
        )

        def fail_discard(key: str) -> None:
            raise OSError("simulated temporary filesystem failure")

        monkeypatch.setattr(scenario.store, "discard_staged_key", fail_discard)
        service.delete_slot(
            session.id, slot_key="match_result", actor_user_id=scenario.actor_user_id,
            occurred_at=NOW + timedelta(minutes=1),
        )
        assert (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()
    monkeypatch.setattr(scenario.store, "discard_staged_key", real_discard)
    with Session(scenario.engine) as restarted:
        queued = restarted.get(EvaluationQuarantineCleanup, slot.quarantine_storage_key)
        assert queued is not None and queued.attempts == 1
        cleaned, failed = drain_quarantine_cleanup(
            restarted, scenario.store, occurred_at=NOW + timedelta(minutes=2),
        )
        assert cleaned >= 1 and failed == 0
        assert restarted.get(EvaluationQuarantineCleanup, slot.quarantine_storage_key) is None
    assert not (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()


def test_upload_slot_outer_rollback_keeps_original_file_and_does_not_enqueue_cleanup(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        slot = service.put_slot(
            session.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((90, 120, 150))),
            declared_filename="evaluation.png", declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id, occurred_at=NOW,
        )
    with Session(scenario.engine) as db, pytest.raises(RuntimeError, match="outer rollback"), db.begin():
        EvaluationUploadService(db, store=scenario.store).delete_slot(
            session.id, slot_key="match_result", actor_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        assert (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()
        raise RuntimeError("outer rollback")
    with Session(scenario.engine) as db:
        assert db.get(SystemEvaluationUploadSessionMedia, slot.id) is not None
        assert db.get(EvaluationQuarantineCleanup, slot.quarantine_storage_key) is None
    assert (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()


def test_external_connection_rollback_does_not_remove_a_live_upload_file(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        slot = service.put_slot(
            session.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((90, 130, 150))),
            declared_filename="evaluation.png", declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id, occurred_at=NOW,
        )
    with scenario.engine.connect() as connection:
        external = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
            EvaluationUploadService(db, store=scenario.store).delete_slot(
                session.id, slot_key="match_result", actor_user_id=scenario.actor_user_id,
                occurred_at=NOW,
            )
            assert (scenario.store.quarantine_root / slot.quarantine_storage_key).exists()
        external.rollback()
    with Session(scenario.engine) as db:
        assert db.get(SystemEvaluationUploadSessionMedia, slot.id) is not None
        assert db.get(EvaluationQuarantineCleanup, slot.quarantine_storage_key) is None


def test_failed_slot_write_and_failed_file_removal_leave_durable_cleanup(
    task_creation_scenario: TaskCreationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW,
        )
        real_open = service._open_session_for_actor

        def reject_at_write(*args, **kwargs):
            return None if kwargs["lock"] else real_open(*args, **kwargs)

        def unavailable_file_removal(image):
            raise OSError("simulated file removal failure")

        monkeypatch.setattr(service, "_open_session_for_actor", reject_at_write)
        monkeypatch.setattr(scenario.store, "discard_staged", unavailable_file_removal)
        with pytest.raises(EvaluationResourceNotFound):
            service.put_slot(
                session.id, slot_key="match_result", stream=io.BytesIO(_image_bytes((95, 135, 155))),
                declared_filename="evaluation.png", declared_mime_type="image/png",
                actor_user_id=scenario.actor_user_id, occurred_at=NOW,
            )
    pending_files = list(scenario.store.quarantine_root.glob(".owned-*"))
    assert len(pending_files) == 1
    with Session(scenario.engine) as restarted:
        assert restarted.get(EvaluationQuarantineCleanup, pending_files[0].name) is not None
        drain_quarantine_cleanup(restarted, scenario.store, occurred_at=NOW + timedelta(minutes=1))
    assert not pending_files[0].exists()


@pytest.mark.parametrize("per_account,total", [(1, 500), (5, 1)])
def test_open_upload_limits_reject_extra_sessions_and_release_expired_capacity(
    task_creation_scenario: TaskCreationScenario,
    per_account: int, total: int,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(
            db, store=scenario.store,
            max_open_sessions_per_account=per_account, max_open_sessions_total=total,
        )
        service.create_session(project_id=scenario.project_id,
                               created_by_user_id=scenario.actor_user_id, occurred_at=NOW)
        with pytest.raises(EvaluationUploadLimitReached):
            service.create_session(project_id=scenario.project_id,
                                   created_by_user_id=scenario.actor_user_id, occurred_at=NOW)
        later = service.create_session(
            project_id=scenario.project_id, created_by_user_id=scenario.actor_user_id,
            occurred_at=NOW + timedelta(minutes=31),
        )
        assert later.status == "open"


def test_concurrent_upload_creation_cannot_exceed_account_quota(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    barrier = threading.Barrier(2)

    def create() -> str:
        with Session(scenario.engine) as db:
            service = EvaluationUploadService(
                db, store=scenario.store, max_open_sessions_per_account=1,
            )
            barrier.wait(timeout=10)
            try:
                service.create_session(project_id=scenario.project_id,
                                       created_by_user_id=scenario.actor_user_id, occurred_at=NOW)
            except EvaluationUploadLimitReached:
                return "limited"
            return "created"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))
    assert sorted(results) == ["created", "limited"]


def test_delete_upload_slot_removes_the_file_only_after_commit(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((100, 180, 240))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        service.delete_slot(
            session.id,
            slot_key="match_result",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        assert db.get(SystemEvaluationUploadSessionMedia, slot.id) is None
    assert {path.name for path in scenario.store.quarantine_root.iterdir()} == {".erp-store-identity"}


def test_delete_upload_slot_rollback_preserves_the_row_and_file(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((110, 190, 250))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        def reject_commit(session_: Session) -> None:
            del session_
            raise RuntimeError("forced upload slot delete failure")

        event.listen(db, "before_commit", reject_commit, once=True)
        with pytest.raises(RuntimeError, match="forced upload slot delete failure"):
            service.delete_slot(
                session.id,
                slot_key="match_result",
                actor_user_id=scenario.actor_user_id,
                occurred_at=occurred_at,
            )

    with Session(scenario.engine) as observer:
        assert observer.get(SystemEvaluationUploadSessionMedia, slot.id) is not None
    assert (
        scenario.store.quarantine_root / slot.quarantine_storage_key
    ).is_file()


def test_upload_slot_position_comes_from_profile_and_declared_mime_must_match(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        slot = service.put_slot(
            session.id,
            slot_key="team_detail",
            stream=io.BytesIO(_image_bytes((115, 195, 250))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )

        assert slot.position == 3
        with pytest.raises(MediaValidationFailed) as error:
            service.put_slot(
                session.id,
                slot_key="team_detail",
                stream=io.BytesIO(_image_bytes((116, 196, 251))),
                declared_filename="evaluation.png",
                declared_mime_type="image/jpeg",
                actor_user_id=scenario.actor_user_id,
                occurred_at=occurred_at,
            )
        assert error.value.internal_reason == "declared_mime_mismatch"
        stored = db.get(SystemEvaluationUploadSessionMedia, slot.id)
        assert stored is not None
        assert stored.position == 3


def test_upload_slot_rechecks_current_authorization_before_staging_or_writing(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    revoked_at = occurred_at + timedelta(minutes=1)
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        upload_override = db.scalar(
            select(UserPermissionOverride)
            .join(
                Permission,
                Permission.id == UserPermissionOverride.permission_id,
            )
            .where(
                UserPermissionOverride.user_id == scenario.actor_user_id,
                Permission.key == "evaluation_media.upload",
            )
        )
        assert upload_override is not None
        upload_override.valid_until = revoked_at
        db.commit()

        with pytest.raises(EvaluationResourceNotFound):
            service.put_slot(
                session.id,
                slot_key="match_result",
                stream=io.BytesIO(_image_bytes((117, 197, 252))),
                declared_filename="evaluation.png",
                declared_mime_type="image/png",
                actor_user_id=scenario.actor_user_id,
                occurred_at=revoked_at,
            )

        assert db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id)).where(
                SystemEvaluationUploadSessionMedia.session_id == session.id
            )
        ) == 0
    assert {path.name for path in scenario.store.quarantine_root.iterdir()} == {".erp-store-identity"}


def test_session_creation_and_slot_delete_recheck_current_upload_permission(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    revoked_at = occurred_at + timedelta(minutes=1)
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        slot = service.put_slot(
            session.id,
            slot_key="match_result",
            stream=io.BytesIO(_image_bytes((121, 201, 255))),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        upload_override = db.scalar(
            select(UserPermissionOverride)
            .join(
                Permission,
                Permission.id == UserPermissionOverride.permission_id,
            )
            .where(
                UserPermissionOverride.user_id == scenario.actor_user_id,
                Permission.key == "evaluation_media.upload",
            )
        )
        assert upload_override is not None
        upload_override.valid_until = revoked_at
        db.commit()

        with pytest.raises(EvaluationResourceNotFound):
            service.delete_slot(
                session.id,
                slot_key="match_result",
                actor_user_id=scenario.actor_user_id,
                occurred_at=revoked_at,
            )
        with pytest.raises(EvaluationResourceNotFound):
            service.create_session(
                project_id=scenario.project_id,
                created_by_user_id=scenario.actor_user_id,
                occurred_at=revoked_at,
            )

        assert db.get(SystemEvaluationUploadSessionMedia, slot.id) is not None
        assert (
            scenario.store.quarantine_root / slot.quarantine_storage_key
        ).is_file()


def test_administrator_reason_is_required_before_upload_session_write(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        with pytest.raises(ValueError, match="business reason"):
            service.create_session(
                project_id=scenario.project_id,
                created_by_user_id=scenario.admin_user_id,
                occurred_at=occurred_at,
            )
        assert db.scalar(
            select(func.count(SystemEvaluationUploadSession.id)).where(
                SystemEvaluationUploadSession.project_id == scenario.project_id
            )
        ) == 0


def test_upload_session_keeps_its_profile_slot_snapshot_when_a_new_version_starts(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    later = occurred_at + timedelta(minutes=1)
    with Session(scenario.engine) as db:
        service = EvaluationUploadService(db, store=scenario.store)
        upload_session = service.create_session(
            project_id=scenario.project_id,
            created_by_user_id=scenario.actor_user_id,
            occurred_at=occurred_at,
        )
        db.add(
            ProjectAnalysisProfile(
                project_id=scenario.project_id,
                version=2,
                mode="manual_only",
                adapter_id=None,
                adapter_version=None,
                scoring_rule_version=None,
                required_fields=["score", "grade"],
                media_slots=[
                    {"key": "team_detail", "label": "队伍明细", "required": True},
                    {"key": "profile", "label": "个人数据", "required": True},
                    {"key": "match_result", "label": "结算战绩", "required": True},
                ],
                result_schema_version="1",
                confidence_thresholds={},
                auto_publish_warning_levels=[],
                publish_mode="review_required",
                effective_from=occurred_at + timedelta(seconds=30),
                configured_by_user_id=scenario.actor_user_id,
                change_reason="profile snapshot test",
            )
        )
        db.commit()

        slots = []
        for slot_key, color in (
            ("profile", (118, 198, 253)),
            ("match_result", (119, 199, 254)),
            ("team_detail", (120, 200, 255)),
        ):
            slots.append(
                service.put_slot(
                    upload_session.id,
                    slot_key=slot_key,
                    stream=io.BytesIO(_image_bytes(color)),
                    declared_filename="evaluation.png",
                    declared_mime_type="image/png",
                    actor_user_id=scenario.actor_user_id,
                    occurred_at=later,
                )
            )
        assert [(slot.slot_key, slot.position) for slot in slots] == [
            ("profile", 1),
            ("match_result", 2),
            ("team_detail", 3),
        ]

        task = EvaluationTaskService(db, store=scenario.store).create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=None,
                enrollment_id=None,
                cohort_id=None,
                class_id=None,
                upload_window_start=occurred_at - timedelta(days=1),
                upload_window_end=occurred_at + timedelta(days=1),
            ),
            occurred_at=later,
            request_id="profile-snapshot-task",
        )
        stored = db.get(SystemEvaluationTask, task.id)
        assert stored is not None
        assert stored.analysis_profile_id == scenario.profile_id
        assert stored.analysis_profile_version == 1



def _create_three_slot_upload(
    scenario: TaskCreationScenario,
    db: Session,
    *,
    occurred_at: datetime,
    actor_user_id: UUID | None = None,
    delegated_upload_reason: str | None = None,
    student_person_id: UUID | None = None,
    enrollment_id: UUID | None = None,
):
    actor_id = actor_user_id or scenario.actor_user_id
    upload_service = EvaluationUploadService(db, store=scenario.store)
    upload_session = upload_service.create_session(
        project_id=scenario.project_id,
        created_by_user_id=actor_id,
        occurred_at=occurred_at,
        delegated_upload_reason=delegated_upload_reason,
        student_person_id=student_person_id,
        enrollment_id=enrollment_id,
    )
    for slot_key, color in (
        ("profile", (120, 40, 60)),
        ("match_result", (40, 120, 60)),
        ("team_detail", (40, 60, 120)),
    ):
        upload_service.put_slot(
            upload_session.id,
            slot_key=slot_key,
            stream=io.BytesIO(_image_bytes(color)),
            declared_filename="evaluation.png",
            declared_mime_type="image/png",
            actor_user_id=actor_id,
            occurred_at=occurred_at,
        )
    return upload_session


def test_create_task_atomically_promotes_all_slots_and_history(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        baseline_blobs = db.scalar(select(func.count(EvaluationMediaBlob.id))) or 0

        task = EvaluationTaskService(db, store=scenario.store).create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=None,
                enrollment_id=None,
                cohort_id=None,
                class_id=None,
                upload_window_start=occurred_at - timedelta(days=1),
                upload_window_end=occurred_at + timedelta(days=1),
            ),
            occurred_at=occurred_at,
            request_id="atomic-task-success",
        )

        assert task.status == "pending_match"
        assert db.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.id == task.id
            )
        ) == 1
        assert db.scalar(
            select(func.count(SystemEvaluationTaskMedia.id)).where(
                SystemEvaluationTaskMedia.task_id == task.id
            )
        ) == 3
        assert db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id
            )
        ) == 2
        assert db.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.project_id == scenario.project_id
            )
        ) == 3
        assert db.scalar(select(func.count(EvaluationMediaBlob.id))) == baseline_blobs + 3
        persisted_session = db.get(
            SystemEvaluationUploadSession,
            upload_session.id,
        )
        assert persisted_session is not None
        assert persisted_session.status == "committed"
        assert db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id)).where(
                SystemEvaluationUploadSessionMedia.session_id == upload_session.id
            )
        ) == 0

    assert {path.name for path in scenario.store.quarantine_root.iterdir()} == {".erp-store-identity"}
    assert len(_stored_files(scenario.store)) == 6


def test_delegated_reason_is_copied_to_pending_match_media_but_not_audit(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    reason = "管理员代上传待匹配截图"
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
            actor_user_id=scenario.admin_user_id,
            delegated_upload_reason=f"  {reason}  ",
        )
        task = EvaluationTaskService(db, store=scenario.store).create_task(
            upload_session.id,
            actor_user_id=scenario.admin_user_id,
            target=EvaluationTaskTarget(
                student_person_id=None,
                enrollment_id=None,
                cohort_id=None,
                class_id=None,
                upload_window_start=occurred_at - timedelta(days=1),
                upload_window_end=occurred_at + timedelta(days=1),
            ),
            occurred_at=occurred_at,
            request_id="delegated-pending-media",
        )

        assert task.status == "pending_match"
        reasons = list(
            db.scalars(
                select(EvaluationMediaAsset.delegated_upload_reason)
                .join(
                    SystemEvaluationTaskMedia,
                    SystemEvaluationTaskMedia.media_id == EvaluationMediaAsset.id,
                )
                .where(SystemEvaluationTaskMedia.task_id == task.id)
            )
        )
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_type == "system_evaluation_task",
                AuditEvent.resource_id == task.id,
            )
        )
        assert reasons == [reason, reason, reason]
        assert audit is not None
        assert audit.purpose is None
        assert reason not in repr(audit.event_metadata)


def test_create_task_derives_target_and_upload_window_from_current_enrollment(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.task_access import upload_window_for_cohort
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        task = EvaluationTaskService(db, store=scenario.store).create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=scenario.actor_person_id,
                enrollment_id=scenario.enrollment_id,
                cohort_id=scenario.cohort_id,
                class_id=scenario.class_id,
                upload_window_start=occurred_at + timedelta(days=1),
                upload_window_end=occurred_at + timedelta(days=2),
            ),
            occurred_at=occurred_at,
            request_id="server-derived-task-target",
        )

        stored = db.get(SystemEvaluationTask, task.id)
        cohort = db.get(Cohort, scenario.cohort_id)
        assert stored is not None
        assert cohort is not None
        window = upload_window_for_cohort(
            starts_at=cohort.starts_at,
            ends_at=cohort.ends_at,
        )
        assert stored.student_person_id == scenario.actor_person_id
        assert stored.enrollment_id == scenario.enrollment_id
        assert stored.cohort_id == scenario.cohort_id
        assert stored.class_id == scenario.class_id
        assert stored.upload_window_start == window.starts_at
        assert stored.upload_window_end == window.ends_at


def test_create_task_failure_after_second_asset_leaves_no_partial_business_data(
    task_creation_scenario: TaskCreationScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        baseline_blobs = db.scalar(select(func.count(EvaluationMediaBlob.id))) or 0
        media_service = EvaluationMediaService(db, store=scenario.store)
        real_create_asset = media_service._create_asset
        real_discard_key = scenario.store.discard_staged_key
        inserted_assets = 0
        cleanup_failures = 0

        def fail_after_second_asset(**kwargs: object):
            nonlocal inserted_assets
            asset = real_create_asset(**kwargs)
            inserted_assets += 1
            if inserted_assets == 2:
                raise RuntimeError("forced task persistence failure")
            return asset

        def fail_first_cleanup(quarantine_storage_key: str) -> None:
            nonlocal cleanup_failures
            if cleanup_failures == 0:
                cleanup_failures += 1
                raise RuntimeError("forced task cleanup failure")
            real_discard_key(quarantine_storage_key)

        monkeypatch.setattr(media_service, "_create_asset", fail_after_second_asset)
        monkeypatch.setattr(
            scenario.store,
            "discard_staged_key",
            fail_first_cleanup,
        )
        service = EvaluationTaskService(
            db,
            store=scenario.store,
            media_service=media_service,
        )

        with pytest.raises(RuntimeError, match="forced task persistence failure"):
            service.create_task(
                upload_session.id,
                actor_user_id=scenario.actor_user_id,
                target=EvaluationTaskTarget(
                    student_person_id=None,
                    enrollment_id=None,
                    cohort_id=None,
                    class_id=None,
                    upload_window_start=occurred_at - timedelta(days=1),
                    upload_window_end=occurred_at + timedelta(days=1),
                ),
                occurred_at=occurred_at,
                request_id="atomic-task-failure",
            )

    assert inserted_assets == 2
    assert cleanup_failures == 1
    with Session(scenario.engine) as observer:
        assert observer.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.project_id == scenario.project_id
            )
        ) == 0
        assert observer.scalar(
            select(func.count(SystemEvaluationTaskMedia.id)).join(
                SystemEvaluationTask,
                SystemEvaluationTask.id == SystemEvaluationTaskMedia.task_id,
            ).where(SystemEvaluationTask.project_id == scenario.project_id)
        ) == 0
        assert observer.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.project_id == scenario.project_id
            )
        ) == 0
        assert observer.scalar(select(func.count(EvaluationMediaBlob.id))) == baseline_blobs
        persisted_session = observer.get(
            SystemEvaluationUploadSession,
            upload_session.id,
        )
        assert persisted_session is not None
        assert persisted_session.status == "cancelled"
        assert observer.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id)).where(
                SystemEvaluationUploadSessionMedia.session_id == upload_session.id
            )
        ) == 0
        # A failed unlink is durable and can be retried by another worker,
        # independently of the failed task-creation request.
        assert observer.scalar(select(func.count()).select_from(EvaluationQuarantineCleanup)) >= 1
        drain_quarantine_cleanup(observer, scenario.store,
                                 occurred_at=occurred_at + timedelta(minutes=1))
    assert _stored_files(scenario.store) == set()
    assert {path.name for path in scenario.store.quarantine_root.iterdir()} == {".erp-store-identity"}


def test_create_task_idempotency_replays_the_same_resource_without_raw_key_storage(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.idempotency import EvaluationIdempotencyService
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    target = EvaluationTaskTarget(
        student_person_id=None,
        enrollment_id=None,
        cohort_id=None,
        class_id=None,
        upload_window_start=occurred_at - timedelta(days=1),
        upload_window_end=occurred_at + timedelta(days=1),
    )
    raw_key = "task-create-idempotency-key"
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        idempotency = EvaluationIdempotencyService(
            db,
            digest_key=b"evaluation-idempotency-test-key-32-bytes",
        )
        service = EvaluationTaskService(
            db,
            store=scenario.store,
            idempotency_service=idempotency,
        )

        first = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=target,
            occurred_at=occurred_at,
            request_id="idempotency-first",
            idempotency_key=raw_key,
        )
        replay = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=target,
            occurred_at=occurred_at,
            request_id="idempotency-replay",
            idempotency_key=raw_key,
        )

        assert replay == first
        assert db.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.project_id == scenario.project_id
            )
        ) == 1
        row = db.scalar(
            select(EvaluationIdempotencyKey).where(
                EvaluationIdempotencyKey.resource_id == first.id
            )
        )
        assert row is not None
        assert row.key_digest != raw_key
        assert raw_key not in row.key_digest
        assert len(row.key_digest) == 64
        assert len(row.payload_digest) == 64


def test_idempotency_replay_rechecks_authorization_before_returning_old_task(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.idempotency import EvaluationIdempotencyService
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    revoked_at = occurred_at + timedelta(minutes=1)
    target = EvaluationTaskTarget(
        student_person_id=None,
        enrollment_id=None,
        cohort_id=None,
        class_id=None,
        upload_window_start=occurred_at - timedelta(days=1),
        upload_window_end=occurred_at + timedelta(days=1),
    )
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        service = EvaluationTaskService(
            db,
            store=scenario.store,
            idempotency_service=EvaluationIdempotencyService(
                db,
                digest_key=b"evaluation-idempotency-test-key-32-bytes",
            ),
        )
        first = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=target,
            occurred_at=occurred_at,
            request_id="idempotency-before-revocation",
            idempotency_key="revoked-replay-key",
        )
        upload_override = db.scalar(
            select(UserPermissionOverride)
            .join(
                Permission,
                Permission.id == UserPermissionOverride.permission_id,
            )
            .where(
                UserPermissionOverride.user_id == scenario.actor_user_id,
                Permission.key == "evaluation_media.upload",
            )
        )
        assert upload_override is not None
        upload_override.valid_until = revoked_at
        db.commit()

        with pytest.raises(EvaluationResourceNotFound):
            service.create_task(
                upload_session.id,
                actor_user_id=scenario.actor_user_id,
                target=target,
                occurred_at=revoked_at,
                request_id="idempotency-after-revocation",
                idempotency_key="revoked-replay-key",
            )

        assert db.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.project_id == scenario.project_id
            )
        ) == 1
        assert db.get(SystemEvaluationTask, first.id) is not None


def test_idempotency_uses_server_canonical_target_for_explicit_session_replay(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.idempotency import EvaluationIdempotencyService
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
            student_person_id=scenario.actor_person_id,
            enrollment_id=scenario.enrollment_id,
        )
        service = EvaluationTaskService(
            db,
            store=scenario.store,
            idempotency_service=EvaluationIdempotencyService(
                db,
                digest_key=b"evaluation-idempotency-test-key-32-bytes",
            ),
        )
        first = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=None,
                enrollment_id=None,
                cohort_id=None,
                class_id=None,
                upload_window_start=occurred_at - timedelta(days=1),
                upload_window_end=occurred_at + timedelta(days=1),
            ),
            occurred_at=occurred_at,
            request_id="canonical-target-first",
            idempotency_key="canonical-target-key",
        )
        replay = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=scenario.actor_person_id,
                enrollment_id=scenario.enrollment_id,
                cohort_id=scenario.cohort_id,
                class_id=scenario.class_id,
                upload_window_start=occurred_at + timedelta(days=10),
                upload_window_end=occurred_at + timedelta(days=11),
            ),
            occurred_at=occurred_at,
            request_id="canonical-target-replay",
            idempotency_key="canonical-target-key",
        )

        assert replay == first


def test_concurrent_same_key_confirmation_returns_one_task_to_both_callers(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.idempotency import EvaluationIdempotencyService
    from app.evaluations.task_service import (
        EvaluationTaskRecord,
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    with Session(scenario.engine) as setup_db:
        upload_session = _create_three_slot_upload(
            scenario,
            setup_db,
            occurred_at=occurred_at,
        )
    target = EvaluationTaskTarget(
        student_person_id=None,
        enrollment_id=None,
        cohort_id=None,
        class_id=None,
        upload_window_start=occurred_at - timedelta(days=1),
        upload_window_end=occurred_at + timedelta(days=1),
    )
    barrier = threading.Barrier(2)
    results: list[EvaluationTaskRecord] = []
    errors: list[BaseException] = []

    def confirm(index: int) -> None:
        try:
            with Session(scenario.engine) as db:
                service = EvaluationTaskService(
                    db,
                    store=scenario.store,
                    idempotency_service=EvaluationIdempotencyService(
                        db,
                        digest_key=b"evaluation-idempotency-test-key-32-bytes",
                    ),
                )
                barrier.wait(timeout=5)
                results.append(
                    service.create_task(
                        upload_session.id,
                        actor_user_id=scenario.actor_user_id,
                        target=target,
                        occurred_at=occurred_at,
                        request_id=f"concurrent-idempotency-{index}",
                        idempotency_key="concurrent-same-key",
                    )
                )
        except BaseException as exc:  # noqa: BLE001 - thread result transport
            errors.append(exc)

    threads = [threading.Thread(target=confirm, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert results[0] == results[1]
    with Session(scenario.engine) as observer:
        assert observer.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.project_id == scenario.project_id
            )
        ) == 1
        assert observer.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.actor_key == str(scenario.actor_user_id)
            )
        ) == 1


def test_create_task_idempotency_rejects_same_key_with_changed_payload(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    from app.evaluations.errors import EvaluationIdempotencyConflict
    from app.evaluations.idempotency import EvaluationIdempotencyService
    from app.evaluations.task_service import (
        EvaluationTaskService,
        EvaluationTaskTarget,
    )

    scenario = task_creation_scenario
    occurred_at = NOW
    target = EvaluationTaskTarget(
        student_person_id=None,
        enrollment_id=None,
        cohort_id=None,
        class_id=None,
        upload_window_start=occurred_at - timedelta(days=1),
        upload_window_end=occurred_at + timedelta(days=1),
    )
    with Session(scenario.engine) as db:
        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )
        service = EvaluationTaskService(
            db,
            store=scenario.store,
            idempotency_service=EvaluationIdempotencyService(
                db,
                digest_key=b"evaluation-idempotency-test-key-32-bytes",
            ),
        )
        first = service.create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=target,
            occurred_at=occurred_at,
            request_id="idempotency-conflict-first",
            idempotency_key="same-key-different-payload",
        )
        second_upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
        )

        with pytest.raises(EvaluationIdempotencyConflict):
            service.create_task(
                second_upload_session.id,
                actor_user_id=scenario.actor_user_id,
                target=target,
                occurred_at=occurred_at,
                request_id="idempotency-conflict-second",
                idempotency_key="same-key-different-payload",
            )

        assert db.scalar(
            select(func.count(SystemEvaluationTask.id)).where(
                SystemEvaluationTask.project_id == scenario.project_id
            )
        ) == 1
        assert db.get(SystemEvaluationTask, first.id) is not None
        persisted_second = db.get(
            SystemEvaluationUploadSession,
            second_upload_session.id,
        )
        assert persisted_second is not None
        assert persisted_second.status == "open"
        assert db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id)).where(
                SystemEvaluationUploadSessionMedia.session_id
                == second_upload_session.id
            )
        ) == 3
