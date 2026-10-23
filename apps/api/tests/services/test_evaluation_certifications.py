from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.evaluations.adapter_certification_service import (
    EvaluationAdapterCertificationService,
)
from app.evaluations.errors import (
    EvaluationAdapterCertificationConflict,
    EvaluationAdapterNotSupported,
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.processing_models import EvaluationAdapterCertification
from app.evaluations.review_schemas import EvaluationAdapterCertificationInput
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import GameProject
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = b"certification-idempotency-key-32-bytes"


def _service(db: Session) -> EvaluationAdapterCertificationService:
    return EvaluationAdapterCertificationService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=IDEMPOTENCY_KEY,
        ),
    )


def _input(
    scenario: TaskCreationScenario,
    **overrides: str,
) -> EvaluationAdapterCertificationInput:
    values = {
        "project_id": scenario.project_id,
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "manifest_digest": "a" * 64,
    }
    values.update(overrides)
    return EvaluationAdapterCertificationInput.model_validate(values)


def test_admin_certifies_an_exact_tuple_idempotently_and_lists_it(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = _service(db)
        input = _input(scenario)
        created = service.create(
            input,
            actor_user_id=scenario.admin_user_id,
            idempotency_key="certify-lol-1",
            occurred_at=NOW,
            request_id="evaluation-certify-lol",
        )
        replay = service.create(
            input,
            actor_user_id=scenario.admin_user_id,
            idempotency_key="certify-lol-1",
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-certify-lol-replay",
        )
        assert replay.id == created.id
        listed = service.list_certifications(
            actor_user_id=scenario.admin_user_id,
            occurred_at=NOW + timedelta(seconds=1),
        )
        assert [item.id for item in listed] == [created.id]

    with Session(scenario.engine) as observer:
        assert observer.scalar(
            select(func.count(EvaluationAdapterCertification.id)).where(
                EvaluationAdapterCertification.project_id == scenario.project_id
            )
        ) == 1
        audits = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == created.id,
                    AuditEvent.action == "evaluation.adapter.certified",
                )
            )
        )
        assert len(audits) == 1
        assert "manifest" not in repr(audits[0].event_metadata).lower()
        assert "a" * 64 not in repr(audits[0].event_metadata)


def test_certification_project_directory_uses_certification_capability_and_scope(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db, db.begin():
        active_project_ids = set(
            db.scalars(select(GameProject.id).where(GameProject.is_active.is_(True)))
        )
        assert scenario.project_id in active_project_ids
        override = UserPermissionOverride(
            id=uuid4(),
            user_id=scenario.admin_user_id,
            permission_id=PERMISSIONS["evaluation_adapters.certify"].id,
            effect="deny",
            scope_kind="project",
            scope_id=scenario.project_id,
            valid_from=NOW - timedelta(minutes=1),
            valid_until=NOW + timedelta(minutes=10),
            created_by_user_id=scenario.admin_user_id,
            grant_reason="暂停该项目适配器认证",
        )
        db.add(override)

        page = _service(db).list_projects(
            actor_user_id=scenario.admin_user_id,
            query="",
            page=1,
            page_size=100,
            occurred_at=NOW,
        )

        assert {project.id for project in page.items} == active_project_ids - {
            scenario.project_id
        }
        assert page.total == len(active_project_ids) - 1
        with pytest.raises(EvaluationOperationDenied):
            _service(db).list_projects(
                actor_user_id=scenario.actor_user_id,
                query="",
                page=1,
                page_size=100,
                occurred_at=NOW,
            )
        db.delete(override)


def test_certification_listing_filters_project_scoped_explicit_denies(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db, db.begin():
        certification = EvaluationAdapterCertification(
            id=uuid4(),
            project_id=scenario.project_id,
            adapter_id="lol",
            adapter_version="1.0.0",
            scoring_rule_version="1",
            result_schema_version="1",
            manifest_digest="b" * 64,
            certified_by_user_id=scenario.admin_user_id,
            certified_at=NOW - timedelta(minutes=2),
        )
        override = UserPermissionOverride(
            id=uuid4(),
            user_id=scenario.admin_user_id,
            permission_id=PERMISSIONS["evaluation_adapters.certify"].id,
            effect="deny",
            scope_kind="project",
            scope_id=scenario.project_id,
            valid_from=NOW - timedelta(minutes=1),
            valid_until=NOW + timedelta(minutes=10),
            created_by_user_id=scenario.admin_user_id,
            grant_reason="暂停查看该项目适配器认证",
        )
        db.add_all([certification, override])
        db.flush()

        listed = _service(db).list_certifications(
            actor_user_id=scenario.admin_user_id,
            occurred_at=NOW,
        )

        assert certification.id not in {item.id for item in listed}
        db.delete(override)


def test_certification_requires_global_capability_and_exact_registry_tuple(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = _service(db)
        before = db.scalar(select(func.count(EvaluationAdapterCertification.id)))
        with pytest.raises(EvaluationOperationDenied):
            service.create(
                _input(scenario),
                actor_user_id=scenario.actor_user_id,
                idempotency_key="certify-denied",
                occurred_at=NOW,
                request_id="evaluation-certify-denied",
            )
        with pytest.raises(EvaluationAdapterNotSupported):
            service.create(
                _input(scenario, scoring_rule_version="2"),
                actor_user_id=scenario.admin_user_id,
                idempotency_key="certify-wrong-rule",
                occurred_at=NOW,
                request_id="evaluation-certify-wrong-rule",
            )
        assert db.scalar(select(1)) == 1
        assert before == db.scalar(select(func.count(EvaluationAdapterCertification.id)))


def test_certification_rejects_changed_payloads_and_manifest_replacement(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = _service(db)
        input = _input(scenario)
        service.create(
            input,
            actor_user_id=scenario.admin_user_id,
            idempotency_key="certify-conflict",
            occurred_at=NOW,
            request_id="evaluation-certify-conflict-base",
        )
        changed = _input(scenario, manifest_digest="b" * 64)
        with pytest.raises(EvaluationIdempotencyConflict):
            service.create(
                changed,
                actor_user_id=scenario.admin_user_id,
                idempotency_key="certify-conflict",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-certify-idempotency-conflict",
            )
        with pytest.raises(EvaluationAdapterCertificationConflict):
            service.create(
                changed,
                actor_user_id=scenario.admin_user_id,
                idempotency_key="certify-manifest-replacement",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-certify-manifest-conflict",
            )
        assert db.scalar(select(1)) == 1
        assert db.scalar(
            select(func.count(EvaluationAdapterCertification.id)).where(
                EvaluationAdapterCertification.project_id == scenario.project_id
            )
        ) == 1
