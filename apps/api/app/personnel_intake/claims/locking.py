"""Discover before locking; merge/sort these quota and credential keys ONCE.

Lifecycle order: merged advisory keys -> membership/request -> guardian graph ->
sorted people/accounts -> optional activation Roles -> directory -> claim/session -> challenge. Discovery is
read-only; any changed set/fingerprint rejects the transaction for a fresh retry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.membership_locks import lock_person_memberships, person_membership_lock_keys
from app.identity.models import Person
from app.identity.readiness import GuardianReadinessPolicy
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.guardian_records import guardian_graph_component
from app.personnel_intake.locks import person_credential_fingerprint
from app.personnel_intake.models import AssistedPersonProfile, PersonnelIntakeRequest
from app.rbac.models import Role
from app.registration.verification_service import (
    registration_advisory_lock_key,
    registration_credential_lock_keys,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest
from app.team_management.person_staff_service import (
    PreparedPersonStaffLocks,
    lock_person_staff_positions,
)
from app.team_management.write_support import lock_directory, lock_participants


def claim_quota_lock_keys(*, email_digests: set[str], identity_digests: set[str]) -> list[int]:
    """Task4 merges ALL competitor email/identity keys with credentials before rows."""
    return sorted(
        {
            registration_advisory_lock_key("profile-claim-quota-global", "pending"),
            *(
                registration_advisory_lock_key("profile-claim-quota-email", d)
                for d in email_digests
            ),
            *(
                registration_advisory_lock_key("profile-claim-quota-identity", d)
                for d in identity_digests
            ),
        }
    )


def _retry() -> ProfileClaimError:
    return ProfileClaimError(
        code="profile_claim_retry", status=409, detail="资料正在更新，请稍后重试"
    )


@dataclass(repr=False)
class ClaimLockContext:
    identity_digest: str
    stable_person_id: UUID | None
    candidate_ids: set[UUID]
    graph_ids: set[UUID]
    graph_edges: object
    people: dict[UUID, tuple[object, ...]]
    account_ids: set[UUID]
    requests: dict[UUID, tuple[object, ...]]
    profiles: dict[UUID, tuple[object, ...]]
    team_ids: set[UUID]
    membership_keys: list[int]
    credential_keys: list[int]
    role_plans: dict[UUID, tuple[object, ...] | None]
    staff_locks: dict[UUID, PreparedPersonStaffLocks]


def discover_claim_lock_context(
    db: Session,
    *,
    identity_digest: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    stable_person_id: UUID | None = None,
) -> ClaimLockContext:
    candidates = set(db.scalars(select(Person.id).where(Person.identity_digest == identity_digest)))
    if stable_person_id is not None:
        candidates.add(stable_person_id)
    graph_ids, edges = guardian_graph_component(db, candidates)
    profiles = list(
        db.scalars(
            select(AssistedPersonProfile)
            .where(AssistedPersonProfile.person_id.in_(candidates))
            .execution_options(populate_existing=True)
        )
    )
    requests = list(
        db.scalars(
            select(PersonnelIntakeRequest)
            .where(PersonnelIntakeRequest.person_id.in_(candidates))
            .execution_options(populate_existing=True)
        )
    )
    ids = graph_ids | {p for row in requests for p in row.target_coach_person_ids}
    from app.personnel_intake.role_grants import latest_plan, plan_fingerprint

    plans = {identifier: latest_plan(db, identifier) for identifier in candidates}
    staff = {identifier: PreparedPersonStaffLocks.discover(db, identifier) for identifier in candidates}
    ids.update(pid for item in staff.values() for pid in item.person_ids)
    authorizers = {plan.authorizer_user_id for plan in plans.values() if plan is not None}
    ids.update(db.scalars(select(UserAccount.person_id).where(UserAccount.id.in_(authorizers))))
    people = list(
        db.scalars(
            select(Person).where(Person.id.in_(ids)).execution_options(populate_existing=True)
        )
    )
    accounts = set(db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids))))
    identities = {identity_digest, *(p.identity_digest for p in people)}
    emails: set[str] = set()
    for p in people:
        if p.email_ciphertext:
            try:
                emails.add(
                    lookup.digest_domain(
                        "registration-verification-email",
                        cipher.decrypt(p.email_ciphertext, field_name="email"),
                    )
                )
            except EncryptedFieldError:
                pass  # Corrupt target is privately unapprovable; identity fence still applies.
    return ClaimLockContext(
        identity_digest,
        stable_person_id,
        candidates,
        graph_ids,
        edges,
        {p.id: person_credential_fingerprint(p) + (p.full_name,) for p in people},
        accounts,
        {
            r.id: (
                r.version,
                r.state,
                r.person_id,
                tuple(r.target_coach_person_ids),
                r.target_team_id,
            )
            for r in requests
        },
        {
            p.person_id: (p.version, p.current_approval_id, p.activation_requirement)
            for p in profiles
        },
        {r.target_team_id for r in requests if r.target_team_id}
        | {tid for item in staff.values() for tid in item.team_ids},
        person_membership_lock_keys(
            identity_digests=identities,
            phone_digests={p.phone_digest for p in people if p.phone_digest},
            email_digests={p.email_digest for p in people if p.email_digest},
        ),
        registration_credential_lock_keys(emails, identity_digests=identities),
        {identifier: plan_fingerprint(plan) for identifier, plan in plans.items()},
        staff,
    )


def lock_claim_business_context(
    db: Session,
    context: ClaimLockContext,
    *,
    before_directory: Callable[[], None] | None = None,
    role_ids: set[UUID] | None = None,
) -> None:
    """Caller owns advisory keys; activation's complete Role set precedes directory locks."""
    lock_person_memberships(db, context.membership_keys)
    if context.requests:
        list(
            db.scalars(
                select(PersonnelIntakeRequest)
                .where(PersonnelIntakeRequest.id.in_(context.requests))
                .order_by(PersonnelIntakeRequest.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
    guardians = GuardianReadinessPolicy(db)
    for identifier in sorted(context.graph_ids):
        guardians.lock_guardian_relationships(identifier)
    lock_participants(db, sorted(context.people), sorted(context.account_ids))
    from app.personnel_intake.role_grants import latest_plan, plan_fingerprint

    if any(
        plan_fingerprint(latest_plan(db, identifier)) != fingerprint
        for identifier, fingerprint in context.role_plans.items()
    ):
        raise _retry()
    if before_directory is not None:
        before_directory()
    if any(PreparedPersonStaffLocks.discover(db, pid) != prepared
           for pid, prepared in context.staff_locks.items()):
        raise _retry()
    complete_roles = (role_ids or set()) | {rid for item in context.staff_locks.values() for rid in item.role_ids}
    list(db.scalars(select(Role).where(Role.id.in_(complete_roles)).order_by(Role.id)
                    .with_for_update().execution_options(populate_existing=True)))
    lock_directory(db, sorted(context.team_ids))
    for pid, prepared in sorted(context.staff_locks.items()):
        lock_person_staff_positions(db, pid)
        if PreparedPersonStaffLocks.discover(db, pid) != prepared:
            raise _retry()
    if (
        set(db.scalars(select(Person.id).where(Person.identity_digest == context.identity_digest)))
        | ({context.stable_person_id} if context.stable_person_id else set())
        != context.candidate_ids
    ):
        raise _retry()
    if guardian_graph_component(db, context.candidate_ids) != (
        context.graph_ids,
        context.graph_edges,
    ):
        raise _retry()
    if any(
        (p := db.get(Person, identifier)) is None
        or person_credential_fingerprint(p) + (p.full_name,) != old
        for identifier, old in context.people.items()
    ):
        raise _retry()
    if (
        set(db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(context.people))))
        != context.account_ids
    ):
        raise _retry()
    profiles = {
        p.person_id: (p.version, p.current_approval_id, p.activation_requirement)
        for p in db.scalars(
            select(AssistedPersonProfile)
            .where(AssistedPersonProfile.person_id.in_(context.candidate_ids))
            .execution_options(populate_existing=True)
        )
    }
    requests = {
        r.id: (r.version, r.state, r.person_id, tuple(r.target_coach_person_ids), r.target_team_id)
        for r in db.scalars(
            select(PersonnelIntakeRequest)
            .where(PersonnelIntakeRequest.person_id.in_(context.candidate_ids))
            .execution_options(populate_existing=True)
        )
    }
    if profiles != context.profiles or requests != context.requests:
        raise _retry()
