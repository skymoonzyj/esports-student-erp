"""Test-only program injected into the exact owned, portless restore namespace.

No database connection occurs on import. The host supplies existing backup helper
definitions and the one pure legacy business seed function, never DB fixtures.
"""

import base64
import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

OLD = "0065_invited_housing_visibility"
HEAD = "0090_administration_team_mgmt"
DSN = "postgresql://erp_verify@127.0.0.1:5432/erp_verify"
PENDING_ACTIVATION_PASSWORD = "Restore0069!"
TABLES = (
    "personnel_intake_requests",
    "personnel_intake_revisions",
    "personnel_credential_reservations",
    "assisted_approvals",
    "assisted_person_profiles",
    "coach_person_responsibilities",
    "assisted_activation_sessions",
    "profile_claim_sessions",
    "profile_claims",
    "profile_claim_bindings",
    "profile_claim_decisions",
    "intake_activation_role_plans",
    "intake_identity_selections",
    "person_team_staff_positions",
    "person_team_staff_authorizations",
    "team_staff_assignments",
)
# This legacy recovery fixture has no pending role intents. Include the new
# storage in exact-row preservation without claiming nonempty business coverage.
ADDITIVE_EMPTY_TABLES = ("intake_role_intents", "intake_role_decisions", "quick_intake_receipts")


def verify_api_source(expected, root=Path("/app")):
    """Fail before DB work if the immutable image is stale or incompletely bound."""
    if not expected:
        raise ValueError("api_image_source_mismatch")
    for name, digest in expected.items():
        path = root / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("api_image_source_mismatch")


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def row_fingerprints(db, tables, columns=None):
    from psycopg import sql

    result = {}
    for table in sorted(set(tables)):
        projection = (
            sql.SQL("*")
            if columns is None
            else sql.SQL(",").join(map(sql.Identifier, columns[table]))
        )
        rows = db.execute(
            sql.SQL(
                "SELECT to_jsonb(t)::text FROM (SELECT {} FROM {}) t ORDER BY to_jsonb(t)::text"
            ).format(projection, sql.Identifier(table))
        ).fetchall()
        result[table] = fingerprint(rows)
    return result


def trigger_fingerprint(db):
    definitions = db.execute("""
        SELECT t.tgname, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname
    """).fetchall()
    required = {
        "trg_assisted_account_origin_immutable",
        "trg_personnel_intake_revisions_immutable",
        "trg_assisted_approvals_immutable",
        "trg_assisted_approval_guardian_references",
        "trg_profile_claim_sessions_evidence_immutable",
        "trg_profile_claims_evidence_immutable",
        "trg_profile_claim_bindings_evidence_immutable",
        "trg_profile_claim_decisions_immutable",
        "trg_profile_claims_no_delete",
        "trg_profile_claim_bindings_no_delete",
        "trg_assisted_profile_activation_monotonic",
        "trg_accounts_identity_facts",
        "trg_people_account_identity_facts",
        "intake_identity_selections_immutable",
        "person_team_staff_authorizations_immutable",
        "person_team_staff_positions_history_guard",
        "person_staff_authorization_role",
        "intake_role_intents_immutable",
        "intake_role_decisions_immutable",
    }
    assert required <= {row[0] for row in definitions}, "required_triggers_missing"
    return fingerprint(definitions)


def keys(config):
    from app.security.encryption import EnvelopeCipher, LookupDigest

    return (
        EnvelopeCipher(base64.urlsafe_b64decode(config["field_encryption_master_key"])),
        LookupDigest(base64.urlsafe_b64decode(config["field_lookup_key"])),
    )


def person_values(index, cipher, lookup):
    # Entirely synthetic identities, with internally consistent checksum/date/sex.
    birth = date(2010, 1, 2) if index == 2 else date(1988, 1, 2)
    stem = "110101" + birth.strftime("%Y%m%d") + f"{index * 2 + 1:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    identity = stem + "10X98765432"[sum(int(n) * w for n, w in zip(stem, weights)) % 11]
    phone, email = f"13900000{index:03d}", f"restore-{index}@example.com"
    return {
        "id": uuid4(),
        "full_name": f"Synthetic restore {index}",
        "sex": "male",
        "date_of_birth": birth,
        "identity_ciphertext": cipher.encrypt(identity, field_name="identity_number"),
        "identity_digest": lookup.digest(identity),
        "phone_ciphertext": cipher.encrypt(phone, field_name="phone"),
        "phone_digest": lookup.digest(phone),
        "email_ciphertext": cipher.encrypt(email, field_name="email"),
        "email_digest": lookup.digest_domain("person-email", email),
    }


def legacy_person_statement():
    from sqlalchemy import text

    # Current Person INSERT defaults include 0069 columns absent at OLD.
    return text(
        "INSERT INTO people(id,full_name,sex,date_of_birth,identity_ciphertext,identity_digest,"
        "phone_ciphertext,phone_digest,email_ciphertext,email_digest) "
        "VALUES (:id,:full_name,:sex,:date_of_birth,:identity_ciphertext,:identity_digest,"
        ":phone_ciphertext,:phone_digest,:email_ciphertext,:email_digest)"
    )


def pending_fixture_inputs():
    """Synthetic pending inputs; unknown facts are deliberately not fabricated."""
    from app.personnel_intake.schemas import PendingIntakeInput
    from app.security.encryption import EnvelopeCipher, LookupDigest

    cipher, lookup = EnvelopeCipher(b"c" * 32), LookupDigest(b"d" * 32)
    cases = {}
    for index, name in enumerate(
        (
            "pending_passport",
            "pending_passport_unknown_sex",
            "pending_staff",
            "binding_introduced",
            "binding_preserved",
            "revoke_introduced",
            "revoke_preserved",
            "complete_unassigned",
        ),
        20,
    ):
        values = person_values(index, cipher, lookup)
        data = {
            "kind": "staff",
            "full_name": f"Synthetic pending restore {index}",
            "identity_number": cipher.decrypt(
                values["identity_ciphertext"], field_name="identity_number"
            ),
            "sex": "male",
            "date_of_birth": values["date_of_birth"],
        }
        if name.startswith("pending_passport"):
            data.update(
                identity_number=" zz-rst-0069 "
                if name == "pending_passport"
                else "zz-rst-unknown-0069",
                document_type="passport",
                document_issuing_country=None,
                date_of_birth=None,
                sex="male" if name == "pending_passport" else None,
            )
        if name.endswith("preserved") or name == "complete_unassigned":
            data["email"] = f"restore-{name.replace('_', '-')}-0069@example.com"
        if name == "complete_unassigned":
            data["phone"] = f"13900000{index:03d}"
        cases[name] = PendingIntakeInput(**data)
    return cases


def assert_pending_restored_case(observed, expected):
    # Only a controlled label is exposed on failure, never restored PII/ciphertext.
    assert observed == expected, "pending_restore_case_changed"


def identity_staff_fixture_inputs(team_a, team_b):
    """Pure synthetic management intent; actual services produce every DB row."""
    from app.personnel_intake.identity_selection import ManagedIdentityInput, StaffTeamSelection
    from app.security.encryption import EnvelopeCipher, LookupDigest

    cipher, lookup = EnvelopeCipher(b"c" * 32), LookupDigest(b"d" * 32)
    result = {}
    for index, name in enumerate(("unactivated_multi_role", "realized_link", "ended_history",
                                  "reconfirmed_version"), 70):
        values = person_values(index, cipher, lookup)
        result[name] = {
            "facts": {
                "full_name": f"Synthetic identity restore {index}",
                "identity_number": cipher.decrypt(values["identity_ciphertext"], field_name="identity_number"),
                "phone": f"13900000{index:03d}", "email": f"restore-identity-{index}@example.com",
                "sex": "male", "date_of_birth": values["date_of_birth"], "guardians": [],
                "coach_person_ids": [], "team_id": None, "team_allocation_status": "pending",
            },
            "selection": ManagedIdentityInput(primary_identity="coach", concurrent_identities=["team_manager"]),
            "teams": [StaffTeamSelection(staff_role="coach", team_ids=[team_a, team_b]),
                      StaffTeamSelection(staff_role="manager", team_ids=[team_a])],
        }
    return result


def assert_identity_staff_restored_case(observed, expected):
    # Never expose restored person facts, encrypted blobs or authorization data.
    assert observed == expected, "identity_staff_restore_case_changed"


def collect_identity_staff_case(session, actor, person_id, cipher, lookup):
    """Authenticate every retained proof version, not just the current row count."""
    from sqlalchemy import select

    from app.auth.models import UserAccount
    from app.personnel_intake.guardian_records import authenticated_approval_facts
    from app.personnel_intake.identity_models import IntakeIdentitySelection
    from app.personnel_intake.models import AssistedApproval
    from app.personnel_intake.role_grant_models import ActivationRolePlan
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.team_management.models import TeamStaffAssignment
    from app.team_management.person_staff_models import (
        PersonTeamStaffAuthorization,
        PersonTeamStaffPosition,
    )
    from app.team_management.person_staff_query import PersonStaffQuery
    from app.team_management.person_staff_schemas import staff_authorization_digest
    from app.team_management.person_staff_service import authorization_evidence

    def raw(row):
        return {column.name: getattr(row, column.name) for column in row.__table__.columns}

    identity = session.get(IntakeIdentitySelection, person_id)
    assert identity is not None, "restored_identity_source_missing"
    assert identity.person_id == person_id, "restored_identity_person_changed"
    original = session.get(AssistedApproval, identity.origin_approval_id)
    assert original is not None and original.person_id == person_id, "restored_identity_approval_changed"
    authenticated_approval_facts(original, cipher=cipher, lookup=lookup)
    positions = list(session.scalars(select(PersonTeamStaffPosition).where(
        PersonTeamStaffPosition.person_id == person_id).order_by(PersonTeamStaffPosition.id)))
    proofs = list(session.scalars(select(PersonTeamStaffAuthorization).where(
        PersonTeamStaffAuthorization.position_id.in_([position.id for position in positions])
    ).order_by(PersonTeamStaffAuthorization.position_id, PersonTeamStaffAuthorization.version)))
    by_id = {position.id: position for position in positions}
    for proof in proofs:
        position = by_id[proof.position_id]
        assert position.person_id == person_id, "restored_position_person_changed"
        assert position.origin_approval_id == identity.origin_approval_id, "restored_position_origin_changed"
        assert proof.evidence_digest == staff_authorization_digest(lookup, authorization_evidence(position, proof)), "restored_staff_proof_invalid"
        plan = session.get(ActivationRolePlan, proof.role_plan_id)
        assert plan is not None and plan.person_id == person_id, "restored_staff_plan_source_changed"
        ActivationRoleService(session, lookup=lookup).authenticate(plan)
    links = []
    for position in positions:
        if position.account_assignment_id is not None:
            link = session.get(TeamStaffAssignment, position.account_assignment_id)
            account = session.get(UserAccount, link.account_id) if link else None
            assert link is not None and account is not None, "restored_realization_link_missing"
            assert (account.person_id, link.team_id, link.staff_role) == (person_id, position.team_id, position.staff_role), "restored_realization_fk_changed"
            assert (link.ended_at is None) == (position.ended_at is None), "restored_realization_end_changed"
            links.append(fingerprint(raw(link)))
    plans = list(session.scalars(select(ActivationRolePlan).where(
        ActivationRolePlan.person_id == person_id).order_by(ActivationRolePlan.version)))
    roster = []
    query = PersonStaffQuery(session, lookup=lookup)
    for team in sorted({position.team_id for position in positions}):
        for status in ("current", "ended"):
            page = query.list(actor, team, status=status, page=1, page_size=100)
            assert page.total <= 100, "restore_roster_fixture_exceeds_page"
            selected = [row for row in page.items if row.person_id == person_id]
            assert len({row.row_key for row in selected}) == len(selected), "restored_duplicate_staff_roster"
            roster.extend({"team": str(team), "status": status, **row.model_dump(mode="json")} for row in selected)
    return {
        "identity": fingerprint(raw(identity)),
        "positions": [raw(position) for position in positions],
        "proofs": [raw(proof) for proof in proofs],
        "plans": [fingerprint(raw(plan)) for plan in plans],
        "links": links, "roster": roster,
    }


def produce_identity_staff_cases(engine, cipher, lookup, actor_id, expected_plain_hashes):
    """Use actual management creation/claim/activation/maintenance transactions."""
    from pydantic import SecretStr
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.identity.models import Person
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.claims.models import ProfileClaim
    from app.personnel_intake.claims.review_service import ProfileClaimReviewService
    from app.personnel_intake.claims.schemas import ClaimDecisionInput, ClaimStartInput
    from app.personnel_intake.guardian_records import authenticated_approval_facts, canonical_json
    from app.personnel_intake.models import AssistedApproval, PersonnelIntakeRevision
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate, PendingIntakeInput
    from app.rbac.models import Role, UserRole
    from app.teaching.models import GameProject, ProfessionalTeam
    from app.team_management.person_staff_models import PersonTeamStaffPosition
    from app.team_management.person_staff_schemas import (
        PersonStaffEndInput,
        PersonStaffReconfirmInput,
    )
    from app.team_management.person_staff_service import PersonStaffService

    cases = {}
    with Session(engine) as session:
        account = session.get(UserAccount, actor_id)
        actor = AuthContext(account=account, person=session.get(Person, account.person_id), session=None)
        # Synthetic fixture qualification; never mock/bypass the actual root gate.
        session.add(UserRole(user_id=actor_id, role_id=session.scalar(select(Role.id).where(Role.key == "super_admin"))))
        division = GameProject(id=uuid4(), name="Synthetic identity restore division", sort_order=987,
                               is_active=True, is_team_division=True, created_by_user_id=actor_id)
        session.add(division)
        session.flush()
        teams = [ProfessionalTeam(id=uuid4(), project_id=division.id, name=f"Synthetic identity team {n}",
                                  is_active=True, created_by_user_id=actor_id) for n in (1, 2)]
        session.add_all(teams)
        session.commit()
        team_a, team_b = (team.id for team in teams)
        inputs = identity_staff_fixture_inputs(team_a, team_b)
        for index, (name, data) in enumerate(inputs.items(), 1):
            created = PersonnelApprovalService(session, cipher=cipher, lookup=lookup).direct_create_pending(
                actor, PendingIntakeInput(kind="staff", **data["facts"]), uuid4(),
                identity_selection=data["selection"], staff_teams=data["teams"],
            )
            person_id = created.person_id
            if name == "realized_link":
                fixture = pending_service_fixture(session, cipher, lookup)
                email = data["facts"]["email"]
                started = fixture.public.start(ClaimStartInput(
                    full_name=SecretStr(data["facts"]["full_name"]),
                    identity_number=SecretStr(data["facts"]["identity_number"]), email=SecretStr(email),
                    completion={key: data["facts"][key] for key in ("phone", "sex", "date_of_birth", "guardians")},
                ), f"127.87.0.{index}")
                token = started.session_token.get_secret_value()
                fixture.public.verify(token, SecretStr(fixture.sender.messages[-1].code))
                claim = session.scalar(select(ProfileClaim).where(ProfileClaim.target_person_id == person_id))
                assert claim is not None, "identity_restore_claim_not_created"
                ProfileClaimReviewService(session, cipher=cipher, lookup=lookup).approve(actor, claim.id,
                    ClaimDecisionInput(expected_version=claim.version, idempotency_key=uuid4(), identity_confirmed=True))
                fixture.clock.after_claim_cooldown()
                started = fixture.activation.start(email, f"127.87.1.{index}")
                token = started.session_token.get_secret_value()
                code = SecretStr(fixture.sender.messages[-1].code)
                fixture.activation.verify(token, code)
                fixture.activation.finish(token, code, SecretStr(PENDING_ACTIVATION_PASSWORD))
            positions = list(session.scalars(select(PersonTeamStaffPosition).where(
                PersonTeamStaffPosition.person_id == person_id).order_by(PersonTeamStaffPosition.id)))
            assert len(positions) == 3, "identity_restore_initial_positions_missing"
            service = PersonStaffService(session, lookup=lookup)
            if name == "ended_history":
                for position in positions:
                    service.end(actor, position.id, PersonStaffEndInput(expected_version=position.version,
                        reason="Synthetic restore end history", idempotency_key=uuid4()))
            elif name == "reconfirmed_version":
                roles = ActivationRoleService(session, lookup=lookup)
                before = roles.get(actor, person_id)
                roles.update(actor, person_id, ActivationRoleUpdate(role_keys=["coach", "team_manager"],
                    expected_version=before.version, idempotency_key=uuid4()))
                for position in positions:
                    service.reconfirm(actor, position.id, PersonStaffReconfirmInput(expected_version=position.version,
                        reason="Synthetic restore explicit reconfirmation", idempotency_key=uuid4()))
            session.commit()
            item = {"person": str(person_id), "teams": [str(team_a), str(team_b)]}
            snapshot = collect_identity_staff_case(session, actor, person_id, cipher, lookup)
            # Evidence JSON roundtrip matches the exact host/container boundary.
            item["expected"] = json.loads(json.dumps(snapshot, default=str, sort_keys=True))
            cases[name] = item
        for revision in session.scalars(select(PersonnelIntakeRevision)):
            raw = cipher.decrypt(revision.payload_ciphertext, field_name="personnel_intake_payload")
            value = fingerprint(raw)
            assert expected_plain_hashes.get(str(revision.id), value) == value
            expected_plain_hashes[str(revision.id)] = value
        for approval in session.scalars(select(AssistedApproval)):
            own, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
            value = [fingerprint(canonical_json(own)), fingerprint(canonical_json(guardians))]
            assert expected_plain_hashes.get(str(approval.id), value) == value
            expected_plain_hashes[str(approval.id)] = value
    return cases


def wait_restore_fixture_clock(target, *, clock=None, sleeper=None, timer=None):
    """Let real UTC catch up to synthetic cooldown time; never rewrite facts."""
    import time

    clock = clock or (lambda: datetime.now(UTC))
    sleeper, timer = sleeper or time.sleep, timer or time.monotonic

    def valid(value):
        return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None

    observed = clock()
    if not valid(target) or not valid(observed):
        raise ValueError("restore_fixture_clock_invalid")
    remaining = (target - observed).total_seconds()
    if remaining > 90:
        raise ValueError("restore_fixture_clock_invalid")
    deadline = timer() + 90
    while remaining > 0:
        if timer() >= deadline:
            raise ValueError("restore_fixture_clock_stalled")
        sleeper(min(.25, remaining))
        current = clock()
        if not valid(current):
            raise ValueError("restore_fixture_clock_invalid")
        if current < observed:
            raise ValueError("restore_fixture_clock_backward")
        if timer() > deadline:
            raise ValueError("restore_fixture_clock_stalled")
        observed = current
        remaining = (target - observed).total_seconds()


def probe_identity_staff_cases(engine, evidence, cipher, lookup):
    """After exact restore equality, prove access, dedupe and maintenance behavior."""
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.identity.models import Person
    from app.rbac.models import Role, UserRole
    from app.team_management.access import TeamAccessService
    from app.team_management.models import TeamManagerAssignment, TeamStaffAssignment
    from app.team_management.person_staff_models import PersonTeamStaffPosition
    from app.team_management.person_staff_schemas import (
        PersonStaffEndInput,
        PersonStaffReconfirmInput,
    )
    from app.team_management.person_staff_service import PersonStaffService
    from app.team_management.staff_service import active_staff_team_ids

    cases = evidence["identity_staff_restore_cases"]
    assert set(cases) == {"unactivated_multi_role", "realized_link", "ended_history", "reconfirmed_version"}, "identity_restore_cases_missing"
    # The activation fixture advances61s for the real cross-purpose cooldown.
    # Close this read-only session before the bounded actual-clock wait and the
    # separate behavioral rollback transaction; don't backdate restored history.
    person_ids = [UUID(item["person"]) for item in cases.values()]
    with Session(engine) as timing:
        position_time = timing.scalar(select(func.max(PersonTeamStaffPosition.started_at)).where(
            PersonTeamStaffPosition.person_id.in_(person_ids)))
        assignment_time = timing.scalar(select(func.max(TeamStaffAssignment.created_at)).join(
            PersonTeamStaffPosition, PersonTeamStaffPosition.account_assignment_id == TeamStaffAssignment.id
        ).where(PersonTeamStaffPosition.person_id.in_(person_ids)))
    if position_time is None or assignment_time is None:
        raise ValueError("restore_fixture_clock_invalid")
    wait_restore_fixture_clock(max(position_time, assignment_time))
    with Session(engine) as session, session.begin() as transaction:
        account = session.get(UserAccount, UUID(evidence["legacy_account"]))
        actor = AuthContext(account=account, person=session.get(Person, account.person_id), session=None)
        for name, item in cases.items():
            person_id = UUID(item["person"])
            snapshot = collect_identity_staff_case(session, actor, person_id, cipher, lookup)
            normalized = json.loads(json.dumps(snapshot, default=str, sort_keys=True))
            assert_identity_staff_restored_case(normalized, item["expected"])
            positions = list(session.scalars(select(PersonTeamStaffPosition).where(
                PersonTeamStaffPosition.person_id == person_id).order_by(PersonTeamStaffPosition.id)))
            current = [position for position in positions if position.ended_at is None]
            target = session.scalar(select(UserAccount).where(UserAccount.person_id == person_id))
            if name == "realized_link":
                assert target is not None and all(position.account_assignment_id for position in current), "restored_activation_link_missing"
                roles = set(session.scalars(select(Role.key).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == target.id)))
                assert roles == {"registered_user", "coach", "team_manager"}, "restored_activation_roles_changed"
                assert active_staff_team_ids(session, target.id, role_keys=roles) == {UUID(team) for team in item["teams"]}, "restored_staff_read_scope_changed"
                assert session.scalar(select(TeamManagerAssignment.id).where(TeamManagerAssignment.account_id == target.id)) is None, "restored_staff_became_writable_manager"
                who = AuthContext(account=target, person=session.get(Person, person_id), session=None)
                context = TeamAccessService(session).context(who)
                assert not context.can_assign_managers and not context.can_manage_catalog and not context.can_transfer, "restored_staff_writable_access"
                assert len(snapshot["roster"]) == 3, "restored_realization_duplicate_roster"
            else:
                assert target is None and not snapshot["links"], "restored_unactivated_premature_access"
            service = PersonStaffService(session, lookup=lookup)
            if name == "ended_history":
                assert not current and len(snapshot["roster"]) == 3, "restored_ended_history_revived"
                # An unrelated role reapproval must not resurrect ended positions.
                from app.personnel_intake.role_grants import ActivationRoleService
                from app.personnel_intake.schemas import ActivationRoleUpdate

                roles_service = ActivationRoleService(session, lookup=lookup)
                before = roles_service.get(actor, person_id)
                roles_service.update(actor, person_id, ActivationRoleUpdate(role_keys=["coach", "team_manager", "guardian"],
                    expected_version=before.version, idempotency_key=uuid4()))
                session.expire_all()
                assert all(position.ended_at is not None for position in session.scalars(select(PersonTeamStaffPosition).where(
                    PersonTeamStaffPosition.person_id == person_id))), "restored_role_regrant_revived_history"
            else:
                assert len(current) == 3, "restored_current_positions_changed"
                if name == "reconfirmed_version":
                    assert len(snapshot["proofs"]) == 6 and len(snapshot["plans"]) == 2, "restored_reconfirmation_history_lost"
                chosen = current[0]
                before_version = chosen.version
                service.reconfirm(actor, chosen.id, PersonStaffReconfirmInput(expected_version=before_version,
                    reason="Synthetic restored maintenance proof", idempotency_key=uuid4()))
                service.end(actor, chosen.id, PersonStaffEndInput(expected_version=before_version + 1,
                    reason="Synthetic restored maintenance end", idempotency_key=uuid4()))
                session.refresh(chosen)
                assert chosen.ended_at is not None, "restored_position_not_maintainable"
        transaction.rollback()


class PendingRestoreClock:
    def __init__(self):
        self.now = datetime.now(UTC)

    def __call__(self):
        return self.now

    def after_claim_cooldown(self):
        # Real verification limits apply across claim and activation purposes.
        self.now += timedelta(seconds=61)


def pending_service_fixture(session, cipher, lookup):
    from types import SimpleNamespace

    from app.personnel_intake.activation_service import AssistedActivationService
    from app.personnel_intake.claims.public_service import ProfileClaimService
    from app.registration.delivery import CapturingVerificationSender
    from app.registration.verification_service import RegistrationVerificationService

    clock = PendingRestoreClock()
    sender = CapturingVerificationSender(messages=[])
    verification = RegistrationVerificationService(
        session,
        lookup=lookup,
        verification_pepper=b"v" * 32,
        sender=sender,
        app_env="test",
        clock=clock,
    )
    return SimpleNamespace(
        clock=clock,
        sender=sender,
        public=ProfileClaimService(
            session,
            cipher=cipher,
            lookup=lookup,
            verification=verification,
            token_pepper=b"t" * 32,
            clock=clock,
        ),
        activation=AssistedActivationService(
            session,
            cipher=cipher,
            lookup=lookup,
            verification=verification,
            token_pepper=b"a" * 32,
            clock=clock,
        ),
    )


def collect_pending_case(session, item, cipher, lookup):
    """Read authoritative restored values; evidence contains hashes, never plaintext."""
    from sqlalchemy import select

    from app.auth.models import AccountLoginIdentifier, UserAccount
    from app.housing.models import HousingStay
    from app.identity.models import Person, StaffProfile
    from app.personnel_intake.claims.models import (
        ProfileClaim,
        ProfileClaimBinding,
        ProfileClaimDecision,
        ProfileClaimSession,
    )
    from app.personnel_intake.claims.proof import validate_claim_proof
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        PersonnelIntakeRequest,
        PersonnelIntakeRevision,
    )

    person = session.get(Person, UUID(item["person"]))
    assert person is not None, "pending_person_missing"
    staff = session.get(StaffProfile, person.id)
    profile = session.get(AssistedPersonProfile, person.id)
    approval = session.get(AssistedApproval, profile.current_approval_id)
    request = session.get(PersonnelIntakeRequest, approval.request_id)
    stays = list(
        session.scalars(
            select(HousingStay).where(HousingStay.person_id == person.id).order_by(HousingStay.id)
        )
    )
    accounts = list(session.scalars(select(UserAccount).where(UserAccount.person_id == person.id)))
    result = {
        "person": str(person.id),
        "staff_profile_person": str(staff.person_id) if staff else None,
        "staff_type": staff.staff_type if staff else None,
        "document_type": person.document_type,
        "document_issuing_country": person.document_issuing_country,
        "sex": person.sex,
        "date_of_birth": person.date_of_birth.isoformat() if person.date_of_birth else None,
        "intake_mode": person.intake_mode,
        "team_allocation_status": person.team_allocation_status,
        "team_allocation_version": person.team_allocation_version,
        "raw_document_ciphertext_hash": fingerprint(person.identity_ciphertext),
        "raw_document_plaintext_hash": fingerprint(
            cipher.decrypt(person.identity_ciphertext, field_name="identity_number")
        ),
        "identity_digest": person.identity_digest,
        "passport_number_digest": person.passport_number_digest,
        "phone_ciphertext_hash": fingerprint(person.phone_ciphertext),
        "email_ciphertext_hash": fingerprint(person.email_ciphertext),
        "email_digest": person.email_digest,
        "person_row": fingerprint(
            {column.name: getattr(person, column.name) for column in Person.__table__.columns}
        ),
        "request": str(request.id),
        "request_version": request.version,
        "approval": str(approval.id),
        "approval_version": approval.revision_version,
        "supersedes_approval": str(approval.supersedes_approval_id)
        if approval.supersedes_approval_id
        else None,
        "operation": approval.operation_kind,
        "revisions": [
            str(value)
            for value in session.scalars(
                select(PersonnelIntakeRevision.id)
                .where(PersonnelIntakeRevision.request_id == request.id)
                .order_by(PersonnelIntakeRevision.version)
            )
        ],
        "stay_ids": [str(stay.id) for stay in stays],
        "stay_rows": [
            fingerprint(
                {
                    column.name: getattr(stay, column.name)
                    for column in HousingStay.__table__.columns
                }
            )
            for stay in stays
        ],
        "accounts": [str(account.id) for account in accounts],
        "login_identifiers": [
            str(value)
            for value in session.scalars(
                select(AccountLoginIdentifier.id).where(
                    AccountLoginIdentifier.account_id.in_([account.id for account in accounts])
                )
            )
        ],
        "email_binding_origin": None,
        "session_submission_version": None,
    }
    if item.get("claim"):
        claim = session.get(ProfileClaim, UUID(item["claim"]))
        binding = session.get(ProfileClaimBinding, UUID(item["binding"]))
        claim_session = session.get(ProfileClaimSession, claim.session_id)
        submission = validate_claim_proof(session, claim, cipher=cipher, lookup=lookup)
        result.update(
            email_binding_origin=binding.email_binding_origin,
            session_submission_version=claim_session.submission_version,
            claim_submission_version=claim.submission_version,
            claim_plaintext_hash=fingerprint(submission.canonical()),
            claim_target_person=str(claim.target_person_id),
            claim_target_approval=str(claim.target_approval_id),
            binding_person=str(binding.person_id),
            binding_claim=str(binding.claim_id),
            binding_approval=str(binding.approval_id),
            claim_status=claim.status,
            claim_session=str(claim_session.id),
            decisions=[
                fingerprint(
                    {
                        column.name: getattr(value, column.name)
                        for column in ProfileClaimDecision.__table__.columns
                    }
                )
                for value in session.scalars(
                    select(ProfileClaimDecision)
                    .where(ProfileClaimDecision.claim_id == claim.id)
                    .order_by(ProfileClaimDecision.before_version)
                )
            ],
        )
    return result


def produce_pending_cases(engine, cipher, lookup, actor_id, expected_plain_hashes):
    """All new v2 evidence is issued by real create/claim/review/activation services."""
    from pydantic import SecretStr
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.auth.models import AccountLoginIdentifier, UserAccount
    from app.auth.service import AuthContext
    from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
    from app.housing.occupancy_service import HousingOccupancyService
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.housing.schemas import StayCreate
    from app.identity.models import Person
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.claims.models import (
        ProfileClaim,
        ProfileClaimBinding,
        ProfileClaimSession,
    )
    from app.personnel_intake.claims.review_service import ProfileClaimReviewService
    from app.personnel_intake.claims.schemas import ClaimDecisionInput, ClaimStartInput
    from app.personnel_intake.guardian_records import authenticated_approval_facts, canonical_json
    from app.personnel_intake.models import AssistedApproval, PersonnelIntakeRevision
    from app.rbac.models import Role, UserRole
    from app.registration.models import RegistrationVerificationChallenge

    cases = {}
    when = datetime(2026, 10, 1, 12, tzinfo=UTC)
    with Session(engine) as session:
        account = session.get(UserAccount, actor_id)
        who = AuthContext(
            account=account, person=session.get(Person, account.person_id), session=None
        )
        session.add(
            UserRole(
                user_id=actor_id, role_id=session.scalar(select(Role.id).where(Role.key == "admin"))
            )
        )
        building = HousingBuilding(id=uuid4(), code="RST0069", name="Synthetic restore 0069")
        floor = HousingFloor(id=uuid4(), building_id=building.id, number=1, name="Synthetic floor")
        room = HousingRoom(
            id=uuid4(),
            building_id=building.id,
            floor_id=floor.id,
            room_number="RST-0069",
            normalized_key="RST-0069",
            room_type="custom",
            capacity=8,
            verification_status="verified",
            service_status="available",
            version=1,
        )
        session.add_all([building, floor, room])
        session.commit()
        room_id = room.id

        def authorize_room(value):
            assert value.id == room_id and value.verification_status == "verified", (
                "synthetic_room_scope_changed"
            )

        def artifact_counts():
            return tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    UserAccount,
                    AccountLoginIdentifier,
                    UserRole,
                    RegistrationVerificationChallenge,
                    AssistedActivationSession,
                    ProfileClaimSession,
                )
            )

        for index, (name, data) in enumerate(pending_fixture_inputs().items(), 1):
            artifacts_before = artifact_counts()
            result = PersonnelApprovalService(
                session, cipher=cipher, lookup=lookup
            ).direct_create_pending(who, data, uuid4())
            initial_id, person_id = result.approval_id, result.person_id
            assert artifact_counts() == artifacts_before, "pending_creation_created_auth_artifacts"
            item = {
                "person": str(person_id),
                "initial": str(initial_id),
                "claim": None,
                "binding": None,
                "activation": None,
            }
            person = session.get(Person, person_id)
            initial_email_hash = fingerprint(person.email_ciphertext)
            initial_email_digest = person.email_digest
            if name != "pending_passport_unknown_sex" and name != "binding_introduced":
                stay = HousingOccupancyService(
                    session,
                    resident_readiness=HousingResidentReadinessPolicy(
                        session, cipher=cipher, lookup=lookup
                    ),
                ).create_stay(
                    room_id,
                    StayCreate(
                        person_id=person_id,
                        stay_purpose="permanent",
                        duration_mode="long_term",
                        planned_check_in_at=when,
                    ),
                    actor_user_id=actor_id,
                    occurred_at=when,
                    authorize_room=authorize_room,
                )
                assert stay.person_id == person_id and stay.actual_check_in_at == when, (
                    "pending_stay_not_created"
                )
            initial_case = collect_pending_case(session, item, cipher, lookup)
            initial_approval = session.get(AssistedApproval, initial_id)
            own, guardians = authenticated_approval_facts(
                initial_approval, cipher=cipher, lookup=lookup
            )
            expected_plain_hashes[str(initial_id)] = [
                fingerprint(canonical_json(own)),
                fingerprint(canonical_json(guardians)),
            ]
            revision = session.scalar(
                select(PersonnelIntakeRevision).where(
                    PersonnelIntakeRevision.request_id == initial_approval.request_id,
                    PersonnelIntakeRevision.version == 1,
                )
            )
            expected_plain_hashes[str(revision.id)] = fingerprint(
                cipher.decrypt(revision.payload_ciphertext, field_name="personnel_intake_payload")
            )
            if name.startswith(("binding_", "revoke_")) or name == "complete_unassigned":
                fixture = pending_service_fixture(session, cipher, lookup)
                public, sender = fixture.public, fixture.sender
                email = (
                    data.email.get_secret_value()
                    if data.email
                    else f"restore-{name.replace('_', '-')}-0069@example.com"
                )
                phone = data.phone.get_secret_value() if data.phone else f"13900001{index:03d}"
                issued = public.start(
                    ClaimStartInput(
                        full_name=data.full_name,
                        identity_number=data.identity_number,
                        email=email,
                        completion={
                            "phone": phone,
                            "sex": data.sex,
                            "date_of_birth": data.date_of_birth,
                        },
                    ),
                    f"127.69.0.{index}",
                )
                token = issued.session_token.get_secret_value()
                public.verify(token, SecretStr(sender.messages[-1].code))
                claim = session.scalar(
                    select(ProfileClaim).where(ProfileClaim.target_person_id == person_id)
                )
                assert claim is not None, "real_claim_not_matched"
                decision_input = ClaimDecisionInput(
                    expected_version=1, idempotency_key=uuid4(), identity_confirmed=True
                )
                reviewed = ProfileClaimReviewService(session, cipher=cipher, lookup=lookup).approve(
                    who,
                    claim.id,
                    decision_input,
                )
                assert reviewed.status == "approved", "real_completion_not_approved"
                binding = session.scalar(
                    select(ProfileClaimBinding).where(ProfileClaimBinding.claim_id == claim.id)
                )
                origin = "preserved" if data.email else "introduced"
                assert binding.email_binding_origin == origin, "wrong_binding_origin"
                session.refresh(person)
                if origin == "preserved":
                    assert fingerprint(person.email_ciphertext) == initial_email_hash, (
                        "preserved_email_reencrypted"
                    )
                    assert person.email_digest == initial_email_digest, (
                        "preserved_email_digest_changed"
                    )
                item.update(claim=str(claim.id), binding=str(binding.id), origin=origin)
                item["decision_input"] = decision_input.model_dump(mode="json")
                if name == "complete_unassigned":
                    fixture.clock.after_claim_cooldown()
                    activation = fixture.activation
                    started = activation.start(email, f"127.69.1.{index}")
                    activation_token = started.session_token.get_secret_value()
                    code = SecretStr(sender.messages[-1].code)
                    activation.verify(activation_token, code)
                    activation.finish(
                        activation_token, code, SecretStr(PENDING_ACTIVATION_PASSWORD)
                    )
                    activated = session.scalar(
                        select(AssistedActivationSession).where(
                            AssistedActivationSession.person_id == person_id
                        )
                    )
                    assert activated.state == "completed", "real_activation_not_completed"
                    item["activation"] = str(activated.id)
            session.commit()
            item["expected"] = collect_pending_case(session, item, cipher, lookup)
            for field in (
                "person",
                "staff_profile_person",
                "stay_ids",
                "stay_rows",
                "raw_document_ciphertext_hash",
                "raw_document_plaintext_hash",
            ):
                assert item["expected"][field] == initial_case[field], (
                    "completion_replaced_original_profile_or_stay"
                )
            assert item["expected"]["staff_type"] == "other"
            assert item["expected"]["team_allocation_status"] == "pending"
            assert item["expected"]["team_allocation_version"] == 1
            assert item["expected"]["person"] == item["expected"]["staff_profile_person"]
            if name != "complete_unassigned":
                assert (
                    not item["expected"]["accounts"] and not item["expected"]["login_identifiers"]
                )
            cases[name] = item

        for revision in session.scalars(select(PersonnelIntakeRevision)):
            raw = cipher.decrypt(revision.payload_ciphertext, field_name="personnel_intake_payload")
            value = fingerprint(raw)
            assert expected_plain_hashes.get(str(revision.id), value) == value
            expected_plain_hashes[str(revision.id)] = value
        for approval in session.scalars(select(AssistedApproval)):
            own, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
            value = [fingerprint(canonical_json(own)), fingerprint(canonical_json(guardians))]
            assert expected_plain_hashes.get(str(approval.id), value) == value
            expected_plain_hashes[str(approval.id)] = value
    return cases


def probe_pending_values(engine, evidence, cipher, lookup):
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.identity.models import Person
    from app.identity.readiness import age_status_for
    from app.personnel_intake.claims.models import ProfileClaimDecision
    from app.personnel_intake.eligibility import AssistedEligibilityPolicy
    from app.personnel_intake.guardian_records import authenticated_approval_facts, canonical_json
    from app.personnel_intake.models import AssistedApproval, CoachPersonResponsibility
    from app.personnel_intake.readiness import AssistedReadinessPolicy
    from app.rbac.models import Role, UserRole
    from app.team_management.models import TeamMembership

    cases = evidence["pending_restore_cases"]
    assert set(cases) == set(pending_fixture_inputs()), "pending_restore_cases_missing"
    today = date(2026, 10, 21)
    with Session(engine) as session:
        ids = [UUID(item["person"]) for item in cases.values()]
        readiness = AssistedReadinessPolicy(session, cipher=cipher, lookup=lookup).evaluate_many(
            ids, reference_date=today
        )
        strict = AssistedEligibilityPolicy(session, cipher=cipher, lookup=lookup).evaluate_many(
            ids, reference_date=today
        )
        for name, item in cases.items():
            values = collect_pending_case(session, item, cipher, lookup)
            assert_pending_restored_case(values, item["expected"])
            person_id = UUID(item["person"])
            person = session.get(Person, person_id)
            state = readiness[person_id]
            assert state.approval_valid, "restored_pending_approval_invalid"
            assert state.housing_ready == (name != "pending_passport_unknown_sex"), (
                "restored_housing_readiness_changed"
            )
            assert state.activation_profile_ready == bool(item["claim"]), (
                "restored_activation_readiness_changed"
            )
            assert state.completion_allowed == (name != "complete_unassigned"), (
                "restored_completion_readiness_changed"
            )
            assert (
                session.scalar(
                    select(TeamMembership.id).where(TeamMembership.person_id == person_id)
                )
                is None
            )
            assert (
                session.scalar(
                    select(CoachPersonResponsibility.id).where(
                        CoachPersonResponsibility.person_id == person_id
                    )
                )
                is None
            )
            initial = session.get(AssistedApproval, UUID(item["initial"]))
            initial_facts, _ = authenticated_approval_facts(initial, cipher=cipher, lookup=lookup)
            assert initial_facts["material_version"] == 2, "pending_approval_not_v2"
            if name.startswith("pending_passport"):
                assert person.document_issuing_country is person.date_of_birth is None
                assert age_status_for(person.date_of_birth, today) == "unknown"
                assert not strict[person_id].eligible, "incomplete_passport_strictly_eligible"
                raw = " zz-rst-0069 " if name == "pending_passport" else "zz-rst-unknown-0069"
                assert values["raw_document_plaintext_hash"] == fingerprint(raw), (
                    "passport_raw_spelling_changed"
                )
                normalized = raw.strip().upper()
                assert person.identity_digest == lookup.digest_domain(
                    "passport-identity", json.dumps([None, normalized], separators=(",", ":"))
                )
                assert person.passport_number_digest == lookup.digest_domain(
                    "passport-number", normalized
                )
            elif name == "pending_staff":
                assert person.phone_ciphertext is person.email_ciphertext is None
                assert not strict[person_id].eligible, "contactless_staff_strictly_eligible"
            if item["claim"]:
                assert values["email_binding_origin"] == item["origin"]
                assert (
                    values["session_submission_version"] == values["claim_submission_version"] == 2
                )
                assert values["operation"] == "claim_complete"
                assert values["supersedes_approval"] == item["initial"]
                assert values["approval_version"] == values["request_version"] == 2
                assert len(values["revisions"]) == 2
                assert values["claim_target_person"] == values["binding_person"] == item["person"]
                assert values["claim_target_approval"] == item["initial"]
                assert values["binding_claim"] == item["claim"]
                assert values["binding_approval"] == values["approval"]
                decision = session.scalar(
                    select(ProfileClaimDecision).where(
                        ProfileClaimDecision.claim_id == UUID(item["claim"])
                    )
                )
                approval = session.get(AssistedApproval, UUID(values["approval"]))
                assert (decision.action, decision.before_version, decision.after_version) == (
                    "approve",
                    1,
                    2,
                )
                assert decision.idempotency_key == approval.operation_key
                assert str(decision.result_binding_id) == item["binding"]
                assert str(decision.result_approval_id) == values["approval"]
                assert decision.request_fingerprint == lookup.digest_domain(
                    "profile-claim-decision",
                    canonical_json(
                        {
                            "claim_id": item["claim"],
                            "action": "approve",
                            "input": item["decision_input"],
                        }
                    ),
                )
                if item["origin"] == "introduced":
                    assert initial_facts["email"] is None, "initial_email_snapshot_rewritten"
                else:
                    assert initial_facts["email"] is not None
            if name == "complete_unassigned":
                assert strict[person_id].eligible
                assert len(values["accounts"]) == len(values["login_identifiers"]) == 1
                roles = list(
                    session.scalars(
                        select(Role.key)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .where(UserRole.user_id == UUID(values["accounts"][0]))
                    )
                )
                assert roles == ["registered_user"], "unassigned_staff_activation_role_changed"
                assert values["claim_status"] == "completed"


def probe_pending_services(engine, evidence, cipher, lookup):
    """Run real post-restore lifecycle checks in caller-owned rollback transactions."""
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.housing.errors import HousingProfileIncomplete
    from app.housing.models import HousingRoom
    from app.housing.occupancy_service import HousingOccupancyService
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.housing.schemas import StayCreate
    from app.identity.models import Person
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.claims.models import ProfileClaim
    from app.personnel_intake.claims.review_service import ProfileClaimReviewService
    from app.personnel_intake.claims.schemas import ClaimReasonInput
    from app.personnel_intake.errors import PersonnelIntakeError
    from app.personnel_intake.models import PersonnelCredentialReservation

    cases = evidence["pending_restore_cases"]
    with Session(engine) as session, session.begin() as transaction:
        account = session.get(UserAccount, UUID(evidence["legacy_account"]))
        who = AuthContext(
            account=account, person=session.get(Person, account.person_id), session=None
        )
        room = session.scalar(select(HousingRoom).where(HousingRoom.room_number == "RST-0069"))
        assert room is not None and room.capacity >= 2

        def authorize_room(value):
            assert value.id == room.id

        def reservation_rows(excluded_person, excluded_claim):
            return [
                fingerprint(
                    {
                        column.name: getattr(value, column.name)
                        for column in PersonnelCredentialReservation.__table__.columns
                    }
                )
                for value in session.scalars(
                    select(PersonnelCredentialReservation)
                    .where(
                        (
                            PersonnelCredentialReservation.person_id.is_(None)
                            | (PersonnelCredentialReservation.person_id != excluded_person)
                        ),
                        (
                            PersonnelCredentialReservation.profile_claim_id.is_(None)
                            | (PersonnelCredentialReservation.profile_claim_id != excluded_claim)
                        ),
                    )
                    .order_by(PersonnelCredentialReservation.id)
                )
            ]

        for name in ("revoke_introduced", "revoke_preserved"):
            item = cases[name]
            person_id, claim_id = UUID(item["person"]), UUID(item["claim"])
            old = collect_pending_case(session, item, cipher, lookup)
            unrelated = reservation_rows(person_id, claim_id)
            claim = session.get(ProfileClaim, claim_id)
            result = ProfileClaimReviewService(session, cipher=cipher, lookup=lookup).revoke(
                who,
                claim_id,
                ClaimReasonInput(
                    expected_version=claim.version,
                    idempotency_key=uuid4(),
                    reason="Synthetic restore revocation",
                ),
            )
            assert result.status == "revoked"
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            session.expire_all()
            changed = collect_pending_case(session, item, cipher, lookup)
            assert changed["person"] == old["person"]
            assert (
                changed["stay_ids"] == old["stay_ids"] and changed["stay_rows"] == old["stay_rows"]
            )
            assert changed["staff_profile_person"] == old["staff_profile_person"]
            assert not changed["accounts"]
            if item["origin"] == "introduced":
                assert changed["email_digest"] is None and changed[
                    "email_ciphertext_hash"
                ] == fingerprint(None)
            else:
                assert changed["email_digest"] == old["email_digest"]
                assert changed["email_ciphertext_hash"] == old["email_ciphertext_hash"]
            assert reservation_rows(person_id, claim_id) == unrelated, (
                "revocation_changed_other_reservations"
            )

        unknown = UUID(cases["pending_passport_unknown_sex"]["person"])
        with session.begin_nested() as attempt:
            try:
                HousingOccupancyService(
                    session,
                    resident_readiness=HousingResidentReadinessPolicy(
                        session, cipher=cipher, lookup=lookup
                    ),
                ).create_stay(
                    room.id,
                    StayCreate(
                        person_id=unknown,
                        stay_purpose="permanent",
                        duration_mode="long_term",
                        planned_check_in_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
                    ),
                    actor_user_id=account.id,
                    occurred_at=datetime(2026, 10, 1, 12, tzinfo=UTC),
                    authorize_room=authorize_room,
                )
            except HousingProfileIncomplete:
                pass
            else:
                raise AssertionError("unknown_sex_stay_accepted")
            attempt.rollback()

        service = PersonnelApprovalService(session, cipher=cipher, lookup=lookup)
        passport = pending_fixture_inputs()["pending_passport"]
        for country in (None, "US"):
            with session.begin_nested() as attempt:
                try:
                    service.direct_create_pending(
                        who,
                        passport.model_copy(update={"document_issuing_country": country}),
                        uuid4(),
                    )
                except PersonnelIntakeError as error:
                    assert error.status == 409
                else:
                    raise AssertionError("ambiguous_passport_duplicate_accepted")
                attempt.rollback()
        with session.begin_nested() as known:
            from pydantic import SecretStr

            data = passport.model_copy(
                update={
                    "identity_number": SecretStr("known-rst-0069"),
                    "document_issuing_country": "US",
                }
            )
            service.direct_create_pending(who, data, uuid4())
            with session.begin_nested() as duplicate:
                try:
                    service.direct_create_pending(who, data, uuid4())
                except PersonnelIntakeError as error:
                    assert error.status == 409
                else:
                    raise AssertionError("same_country_passport_duplicate_accepted")
                duplicate.rollback()
            known.rollback()
        transaction.rollback()


def produce(config, seed_business_rows, database_manifest):
    import psycopg
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.auth.models import AccountLoginIdentifier
    from app.identity.models import GuardianProfile, GuardianRelationship, Person
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.claims.models import (
        ProfileClaim,
        ProfileClaimBinding,
        ProfileClaimDecision,
        ProfileClaimSession,
    )
    from app.personnel_intake.claims.proof import (
        SUBMISSION_DOMAIN,
        SUBMISSION_FIELD,
        ClaimSubmission,
    )
    from app.personnel_intake.guardian_records import (
        canonical_json,
        capture_current_snapshots,
        person_facts,
    )
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        CoachPersonResponsibility,
        PersonnelCredentialReservation,
        PersonnelIntakeRequest,
        PersonnelIntakeRevision,
    )
    from app.registration.models import RegistrationVerificationChallenge
    from app.team_management.models import TeamMembership

    cipher, lookup = keys(config)
    for key, value in config.items():
        os.environ[key.upper()] = value
    migration = Config("/app/alembic.ini")
    migration.attributes["database_url"] = DSN.replace("postgresql://", "postgresql+psycopg://")
    command.upgrade(migration, OLD)
    engine = create_engine(migration.attributes["database_url"], hide_parameters=True)
    actor, legacy = uuid4(), person_values(1, cipher, lookup)
    with engine.begin() as connection:
        connection.execute(legacy_person_statement(), legacy)
        connection.execute(
            text("""
            INSERT INTO user_accounts(id,person_id,login_phone_digest,password_hash,status,
                force_password_change,failed_login_count)
            VALUES (:id,:person,:phone,'synthetic-unusable-hash','active',false,0)
        """),
            {"id": actor, "person": legacy["id"], "phone": legacy["phone_digest"]},
        )
        connection.execute(
            AccountLoginIdentifier.__table__.insert().values(
                id=uuid4(),
                account_id=actor,
                kind="phone",
                identifier_digest=lookup.digest_domain("account-login-phone", "13900000001"),
                verified_at=datetime.now(UTC),
                verification_source="legacy_phone_login",
                is_primary=True,
            )
        )
        connection.execute(
            text(
                "INSERT INTO user_roles(user_id,role_id) SELECT :id,id FROM roles WHERE key='registered_user'"
            ),
            {"id": actor},
        )
    # Extracted pure function supplied by controller; no conftest/reset import.
    inserted = seed_business_rows(engine, legacy["id"], actor)
    old_tables = sorted(
        {table for table, _ in inserted}
        | {"people", "user_accounts", "account_login_identifiers", "user_roles"}
    )
    team_id = next(identifier for table, identifier in inserted if table == "professional_teams")
    with psycopg.connect(DSN) as db:
        columns = {
            table: [
                r[0]
                for r in db.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
            ]
            for table in old_tables
        }
        before = row_fingerprints(db, old_tables, columns)
    command.upgrade(migration, HEAD)
    with psycopg.connect(DSN) as db:
        assert row_fingerprints(db, old_tables, columns) == before, "legacy_upgrade_changed_values"

    evidence = {"legacy_account": str(actor), "legacy_person": str(legacy["id"])}
    expected_plain_hashes = {}
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        people = [Person(**person_values(n, cipher, lookup)) for n in (2, 3, 4)]
        session.add_all(people)
        session.flush()
        dependent, guardian, independent = people
        session.add(GuardianProfile(person_id=guardian.id))
        edge = GuardianRelationship(
            guardian_person_id=guardian.id,
            student_person_id=dependent.id,
            relationship="父亲",
            is_primary=True,
            valid_from=date(2026, 1, 1),
            requires_verification=True,
        )
        session.add(edge)
        session.flush()
        evidence["independent"] = str(independent.id)
        evidence["dependent"] = str(dependent.id)
        session.add_all(
            [
                TeamMembership(
                    person_id=person.id,
                    team_id=team_id,
                    joined_on=date(2026, 10, 1),
                    created_by_user_id=actor,
                )
                for person in (dependent, independent)
            ]
        )

        def approve(
            person, version=1, previous=None, request=None, relationships=(), operation=None
        ):
            if request is None:
                request = PersonnelIntakeRequest(
                    submitted_by_user_id=actor,
                    version=version,
                    state="approved",
                    kind="player",
                    person_id=person.id,
                    target_team_id=team_id,
                )
                session.add(request)
                session.flush()
            else:
                request.version = version
            facts = person_facts(person, cipher)
            person_raw, guardian_raw = capture_current_snapshots(
                session, person_id=person.id, relationship_ids=list(relationships), cipher=cipher
            )
            payload = {
                key: facts[key]
                for key in (
                    "full_name",
                    "identity_number",
                    "phone",
                    "email",
                    "sex",
                    "date_of_birth",
                )
            }
            guardians = []
            for record in json.loads(guardian_raw):
                guardian_values = {
                    key: record["guardian"][key]
                    for key in (
                        "full_name",
                        "identity_number",
                        "phone",
                        "email",
                        "sex",
                        "date_of_birth",
                    )
                }
                guardian_values.update(
                    relationship=record["relationship"], is_primary=record["is_primary"]
                )
                guardians.append(guardian_values)
            payload.update(
                kind="player", coach_person_ids=[], team_id=str(team_id), guardians=guardians
            )
            raw = canonical_json(payload)
            revision = PersonnelIntakeRevision(
                request_id=request.id,
                version=version,
                payload_ciphertext=cipher.encrypt(raw, field_name="personnel_intake_payload"),
                payload_digest=lookup.digest_domain("personnel-intake-payload", raw),
                identity_digest=person.identity_digest,
                email_digest=person.email_digest,
                created_by_user_id=actor,
            )
            session.add(revision)
            session.flush()
            approval = AssistedApproval(
                request_id=request.id,
                revision_version=version,
                person_id=person.id,
                approved_by_user_id=actor,
                snapshot_ciphertext=cipher.encrypt(
                    person_raw, field_name="assisted_person_snapshot"
                ),
                snapshot_digest=lookup.digest_domain("assisted-person-snapshot", person_raw),
                guardian_snapshot_ciphertext=cipher.encrypt(
                    guardian_raw, field_name="assisted_guardian_snapshot"
                ),
                guardian_snapshot_digest=lookup.digest_domain(
                    "assisted-guardian-snapshot", guardian_raw
                ),
                guardian_relationship_ids=list(relationships),
                guardian_relationship_version=version,
                supersedes_approval_id=previous.id if previous else None,
                operation_kind=operation,
                operation_key=uuid4() if operation else None,
                operation_digest=lookup.digest_domain("restore-operation", str(person.id))
                if operation
                else None,
            )
            session.add(approval)
            session.flush()
            expected_plain_hashes[str(revision.id)] = fingerprint(raw)
            expected_plain_hashes[str(approval.id)] = [
                fingerprint(person_raw),
                fingerprint(guardian_raw),
            ]
            return request, approval

        request, original = approve(dependent, relationships=[edge.id])
        account = uuid4()
        session.execute(
            text("""
            INSERT INTO user_accounts(id,person_id,password_hash,status,force_password_change,
                failed_login_count,account_access_origin,assisted_approval_id)
            VALUES (:id,:person,'synthetic-unusable-hash','active',false,0,'assisted_activation',:approval)
        """),
            {"id": account, "person": dependent.id, "approval": original.id},
        )
        dependent.phone_ciphertext = cipher.encrypt("19990000002", field_name="phone")
        dependent.phone_digest = lookup.digest("19990000002")
        session.flush()
        _, corrected = approve(
            dependent, version=2, previous=original, request=request, relationships=[edge.id]
        )
        _, independent_approval = approve(independent)
        _, legacy_approval = approve(session.get(Person, legacy["id"]))
        session.add_all(
            [
                AssistedPersonProfile(
                    person_id=dependent.id, current_approval_id=corrected.id, version=2
                ),
                AssistedPersonProfile(
                    person_id=independent.id, current_approval_id=independent_approval.id
                ),
                PersonnelCredentialReservation(
                    kind="email",
                    digest=dependent.email_digest,
                    request_id=request.id,
                    person_id=dependent.id,
                ),
                CoachPersonResponsibility(
                    coach_person_id=legacy["id"], person_id=independent.id, created_by_user_id=actor
                ),
            ]
        )
        challenge = RegistrationVerificationChallenge(
            purpose="assisted_activation",
            identifier_kind="email",
            identifier_digest=dependent.email_digest,
            code_digest=lookup.digest_domain("test-code", "unused"),
            request_source_digest=lookup.digest_domain("test-source", "private"),
            expires_at=now + timedelta(minutes=10),
            last_sent_at=now,
        )
        session.add(challenge)
        session.flush()
        session.add(
            AssistedActivationSession(
                token_digest=lookup.digest_domain("test-token", "private"),
                challenge_id=challenge.id,
                person_id=dependent.id,
                approval_id=corrected.id,
                revision_version=2,
                email_digest=dependent.email_digest,
                state="requested",
                expires_at=now + timedelta(minutes=10),
            )
        )
        # Synthetic storage fixtures, not a replacement for service/E2E activation
        # acceptance. Each state has a distinct identity and email; old null-email
        # snapshots remain immutable after bind/revoke, and final activation has
        # its own separately consumed challenge rather than reusing claim proof.
        claim_evidence, null_approvals = {}, []
        verified = now - timedelta(hours=2)
        for index, status in enumerate(("pending", "approved", "revoked", "completed"), 5):
            values = person_values(index, cipher, lookup)
            values.update(
                email_ciphertext=None,
                email_digest=None,
                nickname=f"restore-nickname-{index}",
                profile_notes=f"Synthetic restore notes {index}",
            )
            person = Person(**values)
            session.add(person)
            session.flush()
            session.add(
                TeamMembership(
                    person_id=person.id,
                    team_id=team_id,
                    joined_on=date(2026, 10, 1),
                    created_by_user_id=actor,
                )
            )
            intake, initial = approve(person)
            null_approvals.append(str(initial.id))
            email = f"restore-claim-{index}@example.com"
            submitted = ClaimSubmission(
                person.full_name,
                cipher.decrypt(person.identity_ciphertext, field_name="identity_number"),
                email,
            ).canonical()
            email_digest = lookup.digest_domain("registration-verification-email", email)
            payload_digest = lookup.digest_domain(SUBMISSION_DOMAIN, submitted)
            encrypted = cipher.encrypt(submitted, field_name=SUBMISSION_FIELD)
            claim_challenge = RegistrationVerificationChallenge(
                purpose="profile_claim",
                identifier_kind="email",
                identifier_digest=email_digest,
                code_digest=lookup.digest_domain("restore-unused-code", str(index)),
                request_source_digest=lookup.digest_domain("restore-source", str(index)),
                created_at=verified - timedelta(minutes=1),
                expires_at=verified + timedelta(minutes=10),
                last_sent_at=verified,
                consumed_at=verified,
            )
            session.add(claim_challenge)
            session.flush()
            claim_session = ProfileClaimSession(
                token_digest=lookup.digest_domain("restore-claim-token", str(index)),
                challenge_id=claim_challenge.id,
                # One redacted input proves immutable claim proof survives normal
                # original-session payload retention cleanup.
                payload_ciphertext=None if status == "approved" else encrypted,
                payload_digest=payload_digest,
                identity_digest=person.identity_digest,
                email_digest=email_digest,
                state="verified",
                created_at=verified - timedelta(minutes=1),
                expires_at=verified + timedelta(minutes=10),
                verified_at=verified,
            )
            session.add(claim_session)
            session.flush()
            claim = ProfileClaim(
                session_id=claim_session.id,
                challenge_id=claim_challenge.id,
                payload_ciphertext=encrypted,
                payload_digest=payload_digest,
                identity_digest=person.identity_digest,
                email_digest=email_digest,
                verified_at=verified,
                challenge_consumed_at=verified,
                target_person_id=person.id,
                target_approval_id=initial.id,
                private_match_reason="unique_synthetic_identity",
                status="pending",
                expires_at=verified + timedelta(days=7),
                created_at=verified,
            )
            session.add(claim)
            session.flush()
            claim_session.claim_id = claim.id
            reservation = PersonnelCredentialReservation(
                kind="email",
                digest=email_digest,
                profile_claim_id=claim.id,
                created_at=verified,
                ended_at=None if status == "pending" else verified + timedelta(minutes=1),
            )
            session.add(reservation)
            current, binding, completed_account, final_activation = initial, None, None, None
            if status != "pending":
                person.email_ciphertext = cipher.encrypt(email, field_name="email")
                person.email_digest = lookup.digest_domain("person-email", email)
                session.flush()
                _, current = approve(
                    person, version=2, previous=initial, request=intake, operation="claim_bind"
                )
                binding = ProfileClaimBinding(
                    claim_id=claim.id,
                    person_id=person.id,
                    email_digest=email_digest,
                    approval_id=current.id,
                    created_at=verified + timedelta(minutes=1),
                    revoked_at=verified + timedelta(minutes=2) if status == "revoked" else None,
                )
                session.add(binding)
                session.flush()
                session.add(
                    ProfileClaimDecision(
                        claim_id=claim.id,
                        actor_user_id=actor,
                        action="approve",
                        idempotency_key=current.operation_key,
                        request_fingerprint=current.operation_digest,
                        before_version=1,
                        after_version=2,
                        result_approval_id=current.id,
                        result_binding_id=binding.id,
                        created_at=verified + timedelta(minutes=1),
                    )
                )
                claim.version, claim.reviewed_by_user_id = 2, actor
                claim.reviewed_at = verified + timedelta(minutes=1)
            if status == "revoked":
                person.email_ciphertext = person.email_digest = None
                session.flush()
                _, current = approve(
                    person, version=3, previous=current, request=intake, operation="claim_revoke"
                )
                null_approvals.append(str(current.id))
                session.add(
                    ProfileClaimDecision(
                        claim_id=claim.id,
                        actor_user_id=actor,
                        action="revoke",
                        idempotency_key=current.operation_key,
                        request_fingerprint=current.operation_digest,
                        before_version=2,
                        after_version=3,
                        result_approval_id=current.id,
                        result_binding_id=binding.id,
                        created_at=verified + timedelta(minutes=2),
                    )
                )
                claim.version, claim.reviewed_at = 3, verified + timedelta(minutes=2)
            if status == "completed":
                completed_account = uuid4()
                session.execute(
                    text("""INSERT INTO user_accounts
                    (id,person_id,password_hash,status,force_password_change,failed_login_count,
                    account_access_origin,assisted_approval_id,email_normalized)
                    VALUES (:id,:person,'synthetic-unusable-hash','active',false,0,
                    'assisted_activation',:approval,:email)"""),
                    {
                        "id": completed_account,
                        "person": person.id,
                        "approval": current.id,
                        "email": email,
                    },
                )
                session.execute(
                    text(
                        "INSERT INTO user_roles(user_id,role_id) SELECT :id,id FROM roles WHERE key='team_member'"
                    ),
                    {"id": completed_account},
                )
                final_challenge = RegistrationVerificationChallenge(
                    purpose="assisted_activation",
                    identifier_kind="email",
                    identifier_digest=email_digest,
                    code_digest=lookup.digest_domain("restore-final-code", str(index)),
                    request_source_digest=lookup.digest_domain("restore-final-source", str(index)),
                    created_at=verified + timedelta(minutes=2),
                    expires_at=verified + timedelta(minutes=12),
                    last_sent_at=verified + timedelta(minutes=2),
                    consumed_at=verified + timedelta(minutes=3),
                )
                session.add(final_challenge)
                session.flush()
                final_activation = AssistedActivationSession(
                    token_digest=lookup.digest_domain("restore-final-token", str(index)),
                    challenge_id=final_challenge.id,
                    person_id=person.id,
                    approval_id=current.id,
                    revision_version=2,
                    email_digest=email_digest,
                    state="completed",
                    created_at=verified + timedelta(minutes=2),
                    expires_at=verified + timedelta(minutes=12),
                    verified_at=verified + timedelta(minutes=3),
                    completed_at=verified + timedelta(minutes=3),
                )
                session.add_all(
                    [
                        final_activation,
                        AccountLoginIdentifier(
                            account_id=completed_account,
                            kind="email",
                            identifier_digest=lookup.digest_domain("account-login-email", email),
                            verified_at=verified + timedelta(minutes=3),
                            verification_source="assisted_activation",
                            is_primary=False,
                        ),
                    ]
                )
                claim.completed_account_id = completed_account
                claim.completed_at = verified + timedelta(minutes=3)
                claim.version = 3
            claim.status = status
            session.add(
                AssistedPersonProfile(
                    person_id=person.id,
                    current_approval_id=current.id,
                    version=intake.version,
                    activation_requirement="reviewed_claim",
                )
            )
            session.flush()
            claim_evidence[status] = {
                "claim": str(claim.id),
                "person": str(person.id),
                "initial": str(initial.id),
                "current": str(current.id),
                "binding": str(binding.id) if binding else None,
                "account": str(completed_account) if completed_account else None,
                "activation": str(final_activation.id) if final_activation else None,
                "plaintext": fingerprint(submitted),
            }
        evidence.update(claims=claim_evidence, null_approvals=null_approvals, team=str(team_id))
        evidence.update(
            assisted_account=str(account),
            original=str(original.id),
            corrected=str(corrected.id),
            legacy_approval=str(legacy_approval.id),
            request=str(request.id),
            guardian_edge=str(edge.id),
        )
    evidence["pending_restore_cases"] = produce_pending_cases(
        engine, cipher, lookup, actor, expected_plain_hashes
    )
    evidence["identity_staff_restore_cases"] = produce_identity_staff_cases(
        engine, cipher, lookup, actor, expected_plain_hashes
    )
    engine.dispose()
    with psycopg.connect(DSN) as db:
        metadata = database_manifest(db)
        evidence.update(
            rows=row_fingerprints(
                db,
                old_tables
                + list(TABLES)
                + list(ADDITIVE_EMPTY_TABLES)
                + [
                    "guardian_relationships",
                    "guardian_profiles",
                    "student_profiles",
                    "staff_profiles",
                    "housing_stays",
                    "housing_transfers",
                    "housing_rooms",
                    "housing_floors",
                    "housing_buildings",
                    "registration_verification_challenges",
                ],
            ),
            triggers=trigger_fingerprint(db),
            plaintext=expected_plain_hashes,
            old_upgrade_preserved=True,
        )
    metadata.update(
        media=[],
        key_canary=cipher.encrypt("erp-backup-recovery-v1", field_name="backup"),
        lookup_canary=hashlib.sha256(
            ("erp-backup-lookup-v1:" + config["field_lookup_key"]).encode()
        ).hexdigest(),
    )
    return {"database": metadata, "evidence": evidence}


def probe(config, evidence):
    import psycopg
    from psycopg import sql
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.personnel_intake.guardian_records import authenticated_approval_facts, canonical_json
    from app.personnel_intake.models import AssistedApproval

    cipher, lookup = keys(config)
    for key, value in config.items():
        os.environ[key.upper()] = value
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(HEAD,)]
        assert row_fingerprints(db, evidence["rows"]) == evidence["rows"], "restored_values_changed"
        assert trigger_fingerprint(db) == evidence["triggers"], "restored_trigger_function_changed"
        pending_engine = create_engine(
            DSN.replace("postgresql://", "postgresql+psycopg://"), hide_parameters=True
        )
        try:
            probe_pending_values(pending_engine, evidence, cipher, lookup)
        finally:
            pending_engine.dispose()
        counts = {
            table: db.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()[0]
            for table in TABLES
        }
        assert all(counts.values()), "new_table_empty"
        assert set(ADDITIVE_EMPTY_TABLES) <= set(evidence["rows"]), "role_intent_storage_missing"
        assert (
            db.execute(
                "SELECT count(*) FROM student_profiles WHERE person_id=%s",
                (evidence["independent"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM user_accounts WHERE person_id=%s", (evidence["independent"],)
            ).fetchone()[0]
            == 0
        )
        assert db.execute(
            "SELECT account_access_origin,assisted_approval_id::text FROM user_accounts WHERE id=%s",
            (evidence["legacy_account"],),
        ).fetchone() == ("legacy", None)
        assert db.execute(
            "SELECT account_access_origin,assisted_approval_id::text FROM user_accounts WHERE id=%s",
            (evidence["assisted_account"],),
        ).fetchone() == ("assisted_activation", evidence["original"])

        def rejects(statement, args, state, constraint=None, trigger=None):
            db.execute("SAVEPOINT rejection")
            try:
                db.execute(statement, args)
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
            except psycopg.Error as exc:
                assert exc.sqlstate == state, "unexpected_rejection_state"
                if constraint:
                    assert exc.diag.constraint_name == constraint, "unexpected_rejection_constraint"
                if trigger:
                    assert trigger in (exc.diag.context or ""), "immutability_trigger_not_exercised"
            else:
                raise AssertionError("invalid_mutation_accepted")
            finally:
                db.execute("ROLLBACK TO SAVEPOINT rejection")
                db.execute("RELEASE SAVEPOINT rejection")

        pending_passport = evidence["pending_restore_cases"]["pending_passport"]
        identity_case = evidence["identity_staff_restore_cases"]["reconfirmed_version"]
        rejects("UPDATE intake_identity_selections SET primary_identity='finance' WHERE person_id=%s",
                (identity_case["person"],), "23514", trigger="prevent_intake_identity_evidence_mutation")
        rejects("UPDATE person_team_staff_authorizations SET evidence_digest=repeat('0',64) WHERE position_id=%s",
                (identity_case["expected"]["positions"][0]["id"],), "23514", trigger="prevent_intake_identity_evidence_mutation")
        rejects("DELETE FROM person_team_staff_positions WHERE person_id=%s",
                (identity_case["person"],), "23514", trigger="protect_person_team_staff_position_history")
        rejects(
            "INSERT INTO user_accounts(id,person_id,password_hash,status,force_password_change,failed_login_count) VALUES (%s,%s,'synthetic-unusable-hash','disabled',false,0)",
            (uuid4(), pending_passport["person"]),
            "23514",
            trigger="require_account_identity_facts",
        )
        activated = evidence["pending_restore_cases"]["complete_unassigned"]
        rejects(
            "UPDATE people SET document_type='passport',passport_number_digest=%s,date_of_birth=NULL,document_issuing_country=NULL WHERE id=%s",
            (
                lookup.digest_domain("passport-number", "RESTORE-DEFERRED-INVARIANT-0069"),
                activated["person"],
            ),
            "23514",
            trigger="require_account_identity_facts",
        )
        for name in ("binding_introduced", "binding_preserved"):
            item = evidence["pending_restore_cases"][name]
            rejects(
                "UPDATE profile_claim_bindings SET email_binding_origin=%s WHERE id=%s",
                ("preserved" if item["origin"] == "introduced" else "introduced", item["binding"]),
                "23514",
                trigger="profile_claim_bindings_evidence_immutable",
            )
            rejects(
                "UPDATE profile_claim_sessions SET submission_version=1 WHERE id=%s",
                (item["expected"]["claim_session"],),
                "23514",
                trigger="profile_claim_sessions_evidence_immutable",
            )

        for index, status in enumerate(("pending", "approved", "revoked", "completed"), 5):
            item = evidence["claims"][status]
            assert db.execute(
                "SELECT status,version,target_person_id::text,target_approval_id::text,"
                "completed_account_id::text,completed_at IS NOT NULL FROM profile_claims WHERE id=%s",
                (item["claim"],),
            ).fetchone() == (
                status,
                {"pending": 1, "approved": 2, "revoked": 3, "completed": 3}[status],
                item["person"],
                item["initial"],
                item["account"],
                status == "completed",
            )
            assert db.execute(
                "SELECT activation_requirement,current_approval_id::text FROM assisted_person_profiles "
                "WHERE person_id=%s",
                (item["person"],),
            ).fetchone() == ("reviewed_claim", item["current"])
            assert db.execute(
                "SELECT kind,request_id,person_id,ended_at IS NULL FROM personnel_credential_reservations "
                "WHERE profile_claim_id=%s",
                (item["claim"],),
            ).fetchall() == [("email", None, None, status == "pending")]
            assert db.execute(
                "SELECT count(*) FROM user_accounts WHERE person_id=%s",
                (item["person"],),
            ).fetchone() == (int(status == "completed"),)
            assert db.execute(
                "SELECT p.nickname,p.profile_notes,t.team_id::text FROM people p "
                "JOIN team_memberships t ON t.person_id=p.id WHERE p.id=%s",
                (item["person"],),
            ).fetchall() == [
                (f"restore-nickname-{index}", f"Synthetic restore notes {index}", evidence["team"])
            ]
            if item["binding"]:
                assert db.execute(
                    "SELECT b.claim_id::text,b.person_id::text,b.revoked_at IS NOT NULL,"
                    "a.operation_kind FROM profile_claim_bindings b JOIN assisted_approvals a "
                    "ON a.id=b.approval_id WHERE b.id=%s",
                    (item["binding"],),
                ).fetchone() == (item["claim"], item["person"], status == "revoked", "claim_bind")
            decisions = db.execute(
                "SELECT d.action,d.before_version,d.after_version,a.operation_kind,"
                "d.idempotency_key=a.operation_key,d.request_fingerprint=a.operation_digest,"
                "d.result_binding_id::text FROM profile_claim_decisions d "
                "JOIN assisted_approvals a ON a.id=d.result_approval_id "
                "WHERE d.claim_id=%s ORDER BY d.before_version",
                (item["claim"],),
            ).fetchall()
            expected = (
                []
                if status == "pending"
                else [("approve", 1, 2, "claim_bind", True, True, item["binding"])]
            )
            if status == "revoked":
                expected.append(("revoke", 2, 3, "claim_revoke", True, True, item["binding"]))
            assert decisions == expected, "restored_claim_decision_evidence_changed"
            if status == "completed":
                assert db.execute(
                    "SELECT r.key FROM user_roles u JOIN roles r ON r.id=u.role_id WHERE u.user_id=%s",
                    (item["account"],),
                ).fetchall() == [("team_member",)]
                assert db.execute(
                    "SELECT a.state,a.completed_at=c.consumed_at,a.challenge_id<>p.challenge_id,"
                    "c.purpose,u.account_access_origin,u.assisted_approval_id=a.approval_id,"
                    "c.identifier_digest=p.email_digest "
                    "FROM assisted_activation_sessions a "
                    "JOIN registration_verification_challenges c ON c.id=a.challenge_id "
                    "JOIN profile_claims p ON p.id=%s JOIN user_accounts u ON u.id=p.completed_account_id "
                    "WHERE a.id=%s",
                    (item["claim"], item["activation"]),
                ).fetchone() == (
                    "completed",
                    True,
                    True,
                    "assisted_activation",
                    "assisted_activation",
                    True,
                    True,
                )

        pending = evidence["claims"]["pending"]
        approved = evidence["claims"]["approved"]
        completed = evidence["claims"]["completed"]
        # Insert, rather than UPDATE, isolates composite FK enforcement from the
        # new immutable-evidence UPDATE triggers.
        rejects(
            "INSERT INTO profile_claim_bindings(id,claim_id,person_id,email_digest,approval_id) "
            "SELECT %s,id,%s,email_digest,%s FROM profile_claims WHERE id=%s",
            (uuid4(), evidence["legacy_person"], evidence["legacy_approval"], pending["claim"]),
            "23503",
            constraint="fk_profile_claim_binding_claim",
        )
        rejects(
            "INSERT INTO profile_claim_bindings(id,claim_id,person_id,email_digest,approval_id) "
            "SELECT %s,id,target_person_id,email_digest,%s FROM profile_claims WHERE id=%s",
            (uuid4(), evidence["legacy_approval"], pending["claim"]),
            "23503",
            constraint="fk_profile_claim_binding_approval",
        )
        rejects(
            "UPDATE profile_claims SET completed_account_id=%s WHERE id=%s",
            (evidence["legacy_account"], completed["claim"]),
            "23503",
            constraint="fk_profile_claim_completed_account_person",
        )
        rejects(
            "UPDATE personnel_credential_reservations SET request_id=%s WHERE profile_claim_id=%s",
            (evidence["request"], pending["claim"]),
            "23514",
            constraint="ck_personnel_reservations_owner",
        )
        rejects(
            "INSERT INTO personnel_credential_reservations(id,kind,digest,profile_claim_id) "
            "SELECT %s,kind,digest,profile_claim_id FROM personnel_credential_reservations "
            "WHERE profile_claim_id=%s",
            (uuid4(), pending["claim"]),
            "23505",
            constraint="uq_personnel_reservations_active",
        )
        rejects(
            "UPDATE assisted_person_profiles SET activation_requirement='reserved_email' WHERE person_id=%s",
            (approved["person"],),
            "23514",
            trigger="assisted_profile_activation_monotonic",
        )
        for table, column, value, identifier in (
            ("profile_claims", "payload_digest", "f" * 64, approved["claim"]),
            ("profile_claim_sessions", "token_digest", "f" * 64, None),
            ("profile_claim_bindings", "approval_id", approved["initial"], approved["binding"]),
        ):
            query = sql.SQL("UPDATE {} SET {}=%s").format(
                sql.Identifier(table), sql.Identifier(column)
            )
            args = (value,)
            if identifier:
                query += sql.SQL(" WHERE id=%s")
                args += (identifier,)
            rejects(query, args, "23514", trigger=table + "_evidence_immutable")
        for table in ("profile_claim_decisions", "profile_claims", "profile_claim_bindings"):
            rejects(
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table)),
                (),
                "23514",
                trigger="assisted_snapshot_immutable",
            )
        rejects(
            "UPDATE profile_claim_decisions SET id=id",
            (),
            "23514",
            trigger="assisted_snapshot_immutable",
        )

        # All replacements are CHECK-valid and refer to an approval for the SAME
        # person: a failing CHECK or composite FK cannot masquerade as immutability.
        rejects(
            "UPDATE user_accounts SET account_access_origin='assisted_activation',assisted_approval_id=%s WHERE id=%s",
            (evidence["legacy_approval"], evidence["legacy_account"]),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE user_accounts SET account_access_origin='legacy',assisted_approval_id=NULL WHERE id=%s",
            (evidence["assisted_account"],),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE user_accounts SET assisted_approval_id=%s WHERE id=%s",
            (evidence["corrected"], evidence["assisted_account"]),
            "23514",
            trigger="assisted_account_origin_immutable",
        )
        rejects(
            "UPDATE personnel_intake_requests SET state='invalid' WHERE id=%s",
            (evidence["request"],),
            "23514",
            constraint="ck_personnel_intake_requests_state",
        )
        rejects(
            "UPDATE assisted_person_profiles SET current_approval_id=%s WHERE person_id=%s",
            (evidence["legacy_approval"], evidence["dependent"]),
            "23503",
            constraint="fk_assisted_profile_approval_person",
        )
        rejects(
            "INSERT INTO personnel_credential_reservations(id,kind,digest,request_id,person_id) "
            "SELECT %s,kind,digest,request_id,person_id FROM personnel_credential_reservations LIMIT 1",
            (uuid4(),),
            "23505",
            constraint="uq_personnel_reservations_active",
        )
        for table in ("personnel_intake_revisions", "assisted_approvals"):
            rejects(
                sql.SQL("UPDATE {} SET id=id").format(sql.Identifier(table)),
                (),
                "23514",
                trigger="assisted_snapshot_immutable",
            )
            rejects(
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table)),
                (),
                "23514",
                trigger="assisted_snapshot_immutable",
            )
        # Guardian reference trigger runs before other constraints on this duplicate
        # row and rejects a real relationship belonging to a different person.
        rejects(
            "INSERT INTO assisted_approvals SELECT id,request_id,revision_version, %s,approved_by_user_id,"
            "approved_at,snapshot_ciphertext,snapshot_digest,guardian_relationship_version,"
            "guardian_relationship_ids,guardian_snapshot_ciphertext,guardian_snapshot_digest,"
            "supersedes_approval_id,operation_key,operation_digest,operation_kind "
            "FROM assisted_approvals WHERE id=%s",
            (evidence["independent"], evidence["original"]),
            "23503",
            trigger="assisted_approval_guardian_references",
        )
        for identifier, encrypted, digest in db.execute(
            "SELECT id,payload_ciphertext,payload_digest FROM personnel_intake_revisions"
        ):
            raw = cipher.decrypt(encrypted, field_name="personnel_intake_payload")
            assert lookup.digest_domain("personnel-intake-payload", raw) == digest
            assert fingerprint(raw) == evidence["plaintext"][str(identifier)]
        assert db.execute(
            "SELECT count(*) FROM personnel_intake_revisions r JOIN assisted_approvals a "
            "ON a.request_id=r.request_id AND a.revision_version=r.version WHERE a.id=ANY(%s::uuid[]) AND r.email_digest IS NULL",
            (evidence["null_approvals"],),
        ).fetchone() == (len(evidence["null_approvals"]),), "null_email_revision_history_lost"
        assert row_fingerprints(db, evidence["rows"]) == evidence["rows"], "probe_changed_rows"
    engine = create_engine(
        DSN.replace("postgresql://", "postgresql+psycopg://"), hide_parameters=True
    )
    with Session(engine) as session:
        for approval in session.scalars(select(AssistedApproval)):
            person, guardians = authenticated_approval_facts(approval, cipher=cipher, lookup=lookup)
            assert [
                fingerprint(canonical_json(person)),
                fingerprint(canonical_json(guardians)),
            ] == evidence["plaintext"][str(approval.id)]
            if str(approval.id) in evidence["null_approvals"]:
                assert person["email"] is None, "null_email_snapshot_rewritten"
        from app.personnel_intake.claims.binding import require_activation_binding_in_transaction
        from app.personnel_intake.claims.errors import ProfileClaimError
        from app.personnel_intake.claims.models import ProfileClaim
        from app.personnel_intake.claims.proof import validate_claim_proof
        from app.registration.models import RegistrationVerificationChallenge
        from app.registration.verification_service import VerificationInvalidOrExpired

        for status, item in evidence["claims"].items():
            claim = session.get(ProfileClaim, UUID(item["claim"]))
            submitted = validate_claim_proof(session, claim, cipher=cipher, lookup=lookup)
            assert fingerprint(submitted.canonical()) == item["plaintext"]
            challenge = session.get(RegistrationVerificationChallenge, claim.challenge_id)
            assert challenge.expires_at < datetime.now(UTC), "fixture_must_test_historical_proof"
            # A missing original consumed proof must fail even when ciphertext
            # and all claim/session digest links remain intact.
            with session.begin_nested() as nested:
                challenge.consumed_at = None
                session.flush()
                try:
                    validate_claim_proof(session, claim, cipher=cipher, lookup=lookup)
                except VerificationInvalidOrExpired:
                    pass
                else:
                    raise AssertionError("missing_consumed_claim_proof_accepted")
                nested.rollback()
            if status == "approved":
                binding = require_activation_binding_in_transaction(
                    session,
                    person_id=claim.target_person_id,
                    email_digest=claim.email_digest,
                    cipher=cipher,
                    lookup=lookup,
                )
                assert str(binding.id) == item["binding"]
            else:
                try:
                    require_activation_binding_in_transaction(
                        session,
                        person_id=claim.target_person_id,
                        email_digest=claim.email_digest,
                        cipher=cipher,
                        lookup=lookup,
                    )
                except ProfileClaimError:
                    pass
                else:
                    raise AssertionError("nonapproved_claim_activation_binding_accepted")
    probe_identity_staff_cases(engine, evidence, cipher, lookup)
    probe_pending_services(engine, evidence, cipher, lookup)
    engine.dispose()
    with psycopg.connect(DSN) as db:
        assert row_fingerprints(db, evidence["rows"]) == evidence["rows"], (
            "proof_probe_changed_rows"
        )
    return counts
