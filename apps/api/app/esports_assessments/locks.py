"""Lock ordering shared by formal and practice assessment writes."""

from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person

from .access import AssessmentAccess
from .errors import AssessmentForbidden


def _account_mutex_key(account_id: object) -> int:
    digest = hashlib.sha256(f"esports-assessments:account:{account_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def lock_actor_and_occupancy(
    db: Session,
    actor: AuthContext,
    now: datetime,
) -> AuthContext:
    """Acquire person, account, live authorization, then module account mutex.

    Despite the historical interface name this is acquire-only. Replay callers
    decide when fresh-start occupancy reconciliation is allowed to mutate rows.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    person_id, account_id = actor.person.id, actor.account.id
    person = db.scalar(
        select(Person)
        .where(Person.id == person_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    account = db.scalar(
        select(UserAccount)
        .where(UserAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        person is None
        or person.deleted_at is not None
        or account is None
        or account.person_id != person_id
        or account.status != "active"
    ):
        raise AssessmentForbidden("无权参加天赋评测")
    current = AuthContext(account=account, person=person, session=actor.session)
    AssessmentAccess(db, current, now).require_take()
    db.scalar(select(func.pg_advisory_xact_lock(_account_mutex_key(account.id))))
    return current
