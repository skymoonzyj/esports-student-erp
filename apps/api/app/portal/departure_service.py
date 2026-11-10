"""One independently owned read-only snapshot per reminder request."""

from datetime import datetime

from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.auth.account_readiness import AccountAccessReadinessPolicy
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.identity.models import Person
from app.portal.departure_access import DepartureAccess
from app.portal.departure_dates import DepartureWindow
from app.portal.departure_query import DepartureQuery
from app.portal.departure_schemas import DepartureRemindersResponse
from app.security.encryption import EnvelopeCipher, LookupDigest


class PortalDepartureService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        self.db = db
        self.cipher = cipher
        self.lookup = lookup

    def list_reminders(
        self,
        *,
        actor: AuthContext,
        now: datetime,
        window: DepartureWindow,
        page: int,
        page_size: int,
    ) -> DepartureRemindersResponse:
        bind = self.db.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
        # Authentication may already have AUTOBEGIN on the caller's Session.
        # Never commit it or issue SET TRANSACTION after its first read.
        with (
            engine.connect().execution_options(
                isolation_level="REPEATABLE READ",
                postgresql_readonly=True,
            ) as connection,
            connection.begin(),
            Session(bind=connection, autoflush=False) as snapshot,
        ):
            row = snapshot.execute(
                select(AuthSession, UserAccount, Person)
                .join(UserAccount, UserAccount.id == AuthSession.user_id)
                .join(Person, Person.id == UserAccount.person_id)
                .where(
                    AuthSession.id == actor.session.id,
                    UserAccount.id == actor.account.id,
                    Person.id == actor.person.id,
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > now,
                    UserAccount.status == "active",
                    Person.deleted_at.is_(None),
                )
            ).one_or_none()
            if row is None:
                raise ProblemError(
                    status=401, code="invalid_session", detail="登录已失效，请重新登录"
                )
            session, account, person = row
            readiness = AccountAccessReadinessPolicy(
                snapshot,
                cipher=self.cipher,
                lookup=self.lookup,
            ).evaluate_one(account, reference_date=business_date_at(now))
            # Authentication disables an unready account as a normal auth
            # side effect. This endpoint must only reject: never disable,
            # revoke, lock, or audit from its business read-only snapshot.
            if readiness.missing:
                raise ProblemError(
                    status=401, code="invalid_session", detail="登录已失效，请重新登录"
                )
            if account.force_password_change:
                raise ProblemError(
                    status=403,
                    code="force_password_change_required",
                    detail="请先修改临时密码",
                )
            current_actor = AuthContext(session=session, account=account, person=person)
            return DepartureQuery(
                snapshot, DepartureAccess(snapshot, current_actor, now)
            ).read_page(
                now=now,
                window=window,
                page=page,
                page_size=page_size,
            )
