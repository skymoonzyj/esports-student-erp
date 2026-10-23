"""Assisted guardian facts never substitute for relationship-targeted consent."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.auth.service import AuthContext
from app.esports_assessments.access import AssessmentAccess
from app.evaluations.access import EvaluationAccessService, EvaluationSqlResource
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import CampSurveyResourceNotFound
from app.evaluations.survey_access import CampSurveyAccessService
from app.evaluations.survey_models import CampSurveySubmission
from app.evaluations.survey_schemas import CampSurveySubmissionInput
from app.evaluations.survey_service import CampSurveyService
from app.food_preferences.access import FoodPreferenceAccess
from app.housing.portal_service import HousingPortalService
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.router import _family_scope
from app.portal.errors import FamilyStudentNotFound
from app.rbac.policy import PolicyRule
from app.rbac.service import RbacService
from app.registration.guardian_service import GuardianInvitationUnavailable
from app.registration.models import GuardianRegistrationInvitation, RegistrationConsentAcceptance
from app.talent_tests.access import TalentAccess
from tests.api.conftest import platform_users as platform_users  # noqa: PLC0414
from tests.api.test_camp_surveys import OPEN_AT, CampApiScenario
from tests.api.test_camp_surveys import camp_api_scenario as camp_api_scenario  # noqa: PLC0414
from tests.services.test_evaluation_access import START, EvaluationScenario, _student_resource
from tests.services.test_evaluation_access import (
    evaluation_scenario as evaluation_scenario,  # noqa: PLC0414
)
from tests.services.test_portal_learning import PortalLearningScenario
from tests.services.test_portal_learning import (
    portal_learning_scenario as portal_learning_scenario,  # noqa: PLC0414
)
from tests.services.test_registration_guardians import (
    NOW,
    _adult_student_account,
    _guardian_account,
)
from tests.services.test_registration_guardians import (
    guardian_service as guardian_service,  # noqa: PLC0414
)


@pytest.fixture
def pending_learning(portal_learning_scenario: PortalLearningScenario) -> PortalLearningScenario:
    scenario = portal_learning_scenario
    scenario.minor_relationship.requires_verification = True
    scenario.db.flush()
    return scenario


@pytest.mark.parametrize("path", ("single", "bulk", "family_scope", "person_route"))
def test_pending_relationship_does_not_grant_person_family_authority(
    pending_learning: PortalLearningScenario,
    path: str,
) -> None:
    s = pending_learning
    service = RbacService(s.db)
    pending = s.minor_student.id
    trusted = s.adult_student.id
    if path == "single":
        tokens = {
            pid: service.person_resource_tokens(
                actor_person_id=s.guardian.id,
                target_person_id=pid,
                reference_date=s.today,
            )
            for pid in (pending, trusted)
        }
    elif path == "bulk":
        tokens = service.person_resource_tokens_many(
            actor_person_id=s.guardian.id,
            target_person_ids={pending, trusted},
            reference_date=s.today,
        )
    elif path == "family_scope":
        groups = service._resource_tokens_for_rule(
            account=s.guardian_account,
            rule=PolicyRule("people.read", "allow", "family", None),
        )
        assert any(f"family:{trusted}" in group for group in groups)
        assert all(f"family:{pending}" not in group for group in groups)
        return
    else:
        assert _family_scope(s.db, s.guardian.id, trusted) is True
        assert _family_scope(s.db, s.guardian.id, pending) is False
        return
    assert f"family:{trusted}" in tokens[trusted]
    assert f"family:{pending}" not in tokens[pending]


def test_pending_relationship_cannot_list_or_read_learning_portal(
    pending_learning: PortalLearningScenario,
) -> None:
    s = pending_learning
    result = s.service.list_students(
        user_id=s.guardian_account.id,
        actor_person_id=s.guardian.id,
        reference_date=s.today,
    )
    assert [row.student_person_id for row in result.items] == [s.adult_student.id]
    with pytest.raises(FamilyStudentNotFound):
        s.service.get_teaching(
            user_id=s.guardian_account.id,
            actor_person_id=s.guardian.id,
            student_person_id=s.minor_student.id,
            reference_date=s.today,
            page=1,
            page_size=20,
        )


@pytest.mark.parametrize("include_issues", (False, True))
def test_pending_relationship_cannot_satisfy_legacy_readiness(
    pending_learning: PortalLearningScenario,
    include_issues: bool,
) -> None:
    s = pending_learning
    # Give the guardian a complete legacy contact; authorization is the only missing fact.
    s.guardian.email_ciphertext = "isolated-test-ciphertext"
    s.guardian.email_digest = "isolated-test-digest"
    s.db.flush()
    policy = GuardianReadinessPolicy(s.db)
    pending = policy.evaluate_many(
        [s.minor_student],
        student_ids={s.minor_student.id},
        reference_date=s.today,
        include_issues=include_issues,
    )[s.minor_student.id]
    assert pending.guardian_status == "pending"
    assert pending.active_relationship_ids == ()
    s.minor_relationship.requires_verification = False
    s.db.flush()
    trusted = policy.evaluate_one(s.minor_student.id, s.today)
    assert trusted.guardian_status == "ready"
    assert trusted.active_relationship_ids == (s.minor_relationship.id,)


@pytest.mark.parametrize("path", ("object", "batch", "sql"))
def test_pending_relationship_does_not_grant_evaluation_authority(
    db: Session,
    evaluation_scenario: EvaluationScenario,
    path: str,
) -> None:
    s = evaluation_scenario
    pending = s.people["other_student"].id
    db.add(
        GuardianRelationship(
            guardian_person_id=s.people["guardian"].id,
            student_person_id=pending,
            relationship="parent",
            is_primary=True,
            valid_from=START.date(),
            requires_verification=True,
        )
    )
    db.flush()
    trusted = _student_resource(s)
    resources = [trusted, replace(trusted, student_person_id=pending)]
    service = EvaluationAccessService(db)
    kwargs = {
        "account_id": s.accounts["guardian"].id,
        "permission": "evaluation_media.original.read",
        "occurred_at": START,
    }
    if path == "object":
        assert [service.decide(resource=row, **kwargs).allowed for row in resources] == [
            True,
            False,
        ]
    elif path == "batch":
        assert service.filter_resources(resources=resources, **kwargs) == [trusted]
    else:
        authorization = service.sql_context(**kwargs)
        assert [
            db.scalar(
                select(
                    authorization.allowed(
                        EvaluationSqlResource(
                            student_person_id=literal(row.student_person_id),
                        )
                    )
                )
            )
            for row in resources
        ] == [True, False]


@pytest.fixture
def pending_surveys(camp_api_scenario: CampApiScenario) -> CampApiScenario:
    s = camp_api_scenario
    relationship = s.db.get(GuardianRelationship, s.guardian_one_relationship_id)
    assert relationship is not None
    relationship.requires_verification = True
    s.db.commit()
    return s


def test_pending_relationship_cannot_resolve_survey_respondent(
    pending_surveys: CampApiScenario,
) -> None:
    s = pending_surveys
    access = CampSurveyAccessService(s.db)
    kwargs = {"account_id": s.accounts["guardian"].id, "occurred_at": OPEN_AT}
    assert access.resolve_respondent(cycle_id=s.second_cycle.id, **kwargs)
    with pytest.raises(CampSurveyResourceNotFound):
        access.resolve_respondent(cycle_id=s.cycle.id, **kwargs)


def test_pending_relationship_cannot_advertise_survey_context(
    pending_surveys: CampApiScenario,
) -> None:
    s = pending_surveys
    # Remove the remaining trusted target's usable survey window, leaving only pending B.
    s.second_cycle.opens_at = OPEN_AT - timedelta(days=2)
    s.second_cycle.due_at = OPEN_AT - timedelta(days=1)
    s.db.flush()
    assert (
        EvaluationContextService(s.db).surveys_available(
            account_id=s.accounts["guardian"].id,
            occurred_at=OPEN_AT,
        )
        is False
    )


@pytest.mark.parametrize("route", ("", "/context"))
def test_pending_relationship_not_in_survey_lists(
    db_client: TestClient,
    pending_surveys: CampApiScenario,
    route: str,
) -> None:
    s = pending_surveys
    s.cycle.opens_at = s.second_cycle.opens_at + timedelta(seconds=1)
    s.db.commit()
    s.login(db_client, "guardian")
    # An unauthorized cycle must not consume the page's single slot before filtering.
    listed = db_client.get(f"/api/v1/evaluations/surveys{route}?page_size=1")
    assert listed.status_code == 200, listed.text
    assert {row["cycle_id"] for row in listed.json()} == {str(s.second_cycle.id)}
    detail = db_client.get(f"/api/v1/evaluations/surveys/{s.cycle.id}")
    assert detail.status_code == 404


def test_survey_write_rechecks_pending_verification_after_route_snapshot(
    camp_api_scenario: CampApiScenario,
) -> None:
    s = camp_api_scenario
    account = s.accounts["guardian"]
    respondent = CampSurveyAccessService(s.db).resolve_respondent(
        cycle_id=s.cycle.id,
        account_id=account.id,
        occurred_at=OPEN_AT,
    )
    relationship = s.db.get(GuardianRelationship, respondent.guardian_relationship_id)
    assert relationship is not None
    relationship.requires_verification = True
    s.db.commit()
    with pytest.raises(CampSurveyResourceNotFound):
        CampSurveyService(s.db).submit(
            s.cycle.id,
            CampSurveySubmissionInput.model_validate(
                s.submission_payload(key="assisted-unverified-write")
            ),
            actor_user_id=account.id,
            actor_person_id=respondent.person_id,
            student_person_id=respondent.student_person_id,
            respondent_kind=respondent.respondent_kind,
            guardian_relationship_id=respondent.guardian_relationship_id,
            guardian_relationship_snapshot=respondent.guardian_relationship_snapshot,
            occurred_at=OPEN_AT,
        )
    assert (
        s.db.scalar(
            select(func.count(CampSurveySubmission.id)).where(
                CampSurveySubmission.cycle_id == s.cycle.id,
                CampSurveySubmission.respondent_person_id == account.person_id,
            )
        )
        == 0
    )


@pytest.mark.parametrize("failure", (None, "wrong_identifier", "expired", "consent"))
def test_invitation_acceptance_authorizes_only_exact_pending_relationship(
    db: Session,
    guardian_service,
    failure: str | None,
) -> None:
    service, identity, _cipher, _lookup, _sender = guardian_service
    guardian = _guardian_account(db, identity)
    adult = _adult_student_account(db, identity)
    # A trusted existing child, B targeted pending child, C another pending child.
    dependent_ids = [uuid4(), adult.person_id, uuid4()]
    for pid in (dependent_ids[0], dependent_ids[2]):
        db.add(
            Person(
                id=pid,
                full_name="隔离虚拟学员",
                identity_ciphertext="virtual",
                identity_digest=uuid4().hex,
                sex="male",
                date_of_birth=NOW.date().replace(year=1990),
            )
        )
    db.flush()
    for index in (0, 2):
        db.add(
            StudentProfile(
                person_id=dependent_ids[index],
                student_number=f"ASSIST-{uuid4().hex[:12]}",
                status="active",
            )
        )
    relationships = [
        GuardianRelationship(
            guardian_person_id=guardian.person_id,
            student_person_id=pid,
            relationship="parent",
            is_primary=True,
            valid_from=NOW.date(),
            requires_verification=index != 0,
        )
        for index, pid in enumerate(dependent_ids)
    ]
    db.add_all(relationships)
    db.commit()
    invitation = service.invite_for_account(
        adult.id,
        identifier_kind="phone",
        identifier="13800008299" if failure == "wrong_identifier" else "13800008213",
        relationship="parent",
        is_primary=True,
        request_id="assisted-confirm-invite",
    )
    if failure == "expired":
        row = db.get(GuardianRegistrationInvitation, invitation.id)
        assert row is not None
        row.expires_at = NOW - timedelta(seconds=1)
        db.commit()
    consent_count = db.scalar(select(func.count(RegistrationConsentAcceptance.id)))
    kwargs = {
        "guardian_account_id": guardian.id,
        "consent_version": "invalid" if failure == "consent" else "2026-08",
        "request_id": "assisted-confirm-accept",
    }
    if failure:
        with pytest.raises(GuardianInvitationUnavailable):
            service.accept(invitation.id, **kwargs)
    else:
        result = service.accept(invitation.id, **kwargs)
        assert result.relationship_id == relationships[1].id
    db.expire_all()
    assert [row.requires_verification for row in relationships] == [False, bool(failure), True]
    assert db.scalar(select(func.count(RegistrationConsentAcceptance.id))) == (
        consent_count + (0 if failure else 1)
    )
    rbac = RbacService(db)
    assert [
        f"family:{pid}"
        in rbac.person_resource_tokens(
            actor_person_id=guardian.person_id,
            target_person_id=pid,
            reference_date=NOW.date(),
        )
        for pid in dependent_ids
    ] == [True, not bool(failure), False]


@pytest.mark.parametrize("kind", ("talent", "esports"))
def test_pending_relationship_excluded_from_assessment_sql_visibility(
    pending_learning: PortalLearningScenario,
    kind: str,
) -> None:
    s = pending_learning
    actor = AuthContext(session=AuthSession(), account=s.guardian_account, person=s.guardian)
    access = (TalentAccess if kind == "talent" else AssessmentAccess)(s.db, actor, s.now)
    ids = set(s.db.scalars(access.visible_person_ids()))
    assert s.adult_student.id in ids
    assert s.minor_student.id not in ids


def test_pending_relationship_excluded_from_housing_portal(
    pending_learning: PortalLearningScenario,
) -> None:
    s = pending_learning
    subjects = HousingPortalService(s.db).list_authorized_subjects(
        user_id=s.guardian_account.id,
        actor_person_id=s.guardian.id,
        as_of=s.now,
    )
    assert [row.person_id for row in subjects] == [s.adult_student.id]


def test_pending_relationship_excluded_from_food_authority_and_access_revision(
    pending_learning: PortalLearningScenario,
) -> None:
    s = pending_learning

    def access() -> FoodPreferenceAccess:
        return FoodPreferenceAccess(s.db, account_id=s.guardian_account.id, occurred_at=s.now)

    assert access().can_target("food_preferences.read", s.adult_student.id) is True
    assert access().can_target("food_preferences.read", s.minor_student.id) is False


def test_pending_relationship_does_not_change_food_access_revision(
    pending_learning: PortalLearningScenario,
) -> None:
    s = pending_learning

    def access() -> FoodPreferenceAccess:
        return FoodPreferenceAccess(s.db, account_id=s.guardian_account.id, occurred_at=s.now)

    pending_revision = access().access_revision
    s.db.delete(s.minor_relationship)
    s.db.flush()
    assert access().access_revision == pending_revision


def test_pending_relationship_cannot_read_person_detail_but_admin_keeps_fact(
    db_client: TestClient,
    pending_surveys: CampApiScenario,
) -> None:
    s = pending_surveys
    s.login(db_client, "guardian")
    pending = db_client.get(f"/api/v1/people/{s.cycle.student_person_id}")
    trusted = db_client.get(f"/api/v1/people/{s.second_cycle.student_person_id}")
    assert pending.status_code == 403
    assert trusted.status_code == 200, trusted.text
    s.login(db_client, "admin")
    facts = db_client.get(f"/api/v1/people/{s.cycle.student_person_id}/guardians")
    assert facts.status_code == 200, facts.text
    assert any(
        row["relationship_id"] == str(s.guardian_one_relationship_id) for row in facts.json()
    )
