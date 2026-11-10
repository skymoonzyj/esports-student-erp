from datetime import date, timedelta

import pytest
from sqlalchemy import event, text, update

from app.auth.models import AuthSession, UserAccount
from app.core.errors import ProblemError
from app.housing.models import HousingStay
from app.identity.models import Person, StudentProfile
from app.rbac.models import UserRole
from tests.departure_helpers import NOW, committed_scene, departure_service


@pytest.mark.parametrize("change", ["checkout", "revoke"])
def test_independent_connection_change_never_mixes_snapshot(postgres_engine, change):
    with committed_scene(postgres_engine) as (db, context, subject, source):
        changed = False
        isolation = []

        def change_after_first_snapshot_read(
            connection, _cursor, statement, _parameters, _ctx, _many
        ):
            nonlocal changed
            if changed or not statement.lstrip().upper().startswith("SELECT"):
                return
            changed = True
            isolation.append(connection.exec_driver_sql("SHOW transaction_isolation").scalar_one())
            assert connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
            with postgres_engine.begin() as writer:
                if change == "checkout":
                    writer.execute(
                        update(HousingStay)
                        .where(HousingStay.id == source.id)
                        .values(
                            status="checked_out",
                            actual_check_out_at=NOW,
                            checked_out_by_user_id=context.account.id,
                        )
                    )
                else:
                    writer.execute(
                        UserRole.__table__.delete().where(UserRole.user_id == context.account.id)
                    )

        event.listen(postgres_engine, "after_cursor_execute", change_after_first_snapshot_read)
        try:
            result = departure_service(db).list_reminders(
                actor=context,
                now=NOW,
                window="three_days",
                page=1,
                page_size=20,
            )
        finally:
            event.remove(postgres_engine, "after_cursor_execute", change_after_first_snapshot_read)
        assert changed
        assert isolation == ["repeatable read"]
        assert result.total == 1
        assert result.items[0].person_id == subject.id
        next_result = departure_service(db).list_reminders(
            actor=context,
            now=NOW,
            window="three_days",
            page=1,
            page_size=20,
        )
        assert next_result.total == 0


@pytest.mark.parametrize(
    "change, status",
    [
        ("disabled", 401),
        ("deleted", 401),
        ("revoked", 401),
        ("expired", 401),
        ("password", 403),
    ],
)
def test_snapshot_rechecks_current_account_and_session(postgres_engine, change, status):
    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        with postgres_engine.begin() as writer:
            if change == "disabled":
                writer.execute(
                    update(UserAccount)
                    .where(UserAccount.id == context.account.id)
                    .values(status="disabled")
                )
            elif change == "deleted":
                writer.execute(
                    update(Person).where(Person.id == context.person.id).values(deleted_at=NOW)
                )
            elif change == "password":
                writer.execute(
                    update(UserAccount)
                    .where(UserAccount.id == context.account.id)
                    .values(force_password_change=True)
                )
            else:
                writer.execute(
                    update(AuthSession)
                    .where(AuthSession.id == context.session.id)
                    .values(
                        **(
                            {"revoked_at": NOW}
                            if change == "revoked"
                            else {"expires_at": NOW - timedelta(seconds=1)}
                        ),
                    )
                )
        with pytest.raises(ProblemError) as caught:
            departure_service(db).list_reminders(
                actor=context,
                now=NOW,
                window="three_days",
                page=1,
                page_size=20,
            )
        assert caught.value.status == status


def test_caller_autobegin_transaction_is_not_committed(postgres_engine):
    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        db.execute(text("SELECT 1"))
        transaction = db.get_transaction()
        result = departure_service(db).list_reminders(
            actor=context,
            now=NOW,
            window="today",
            page=1,
            page_size=20,
        )
        assert result.total == 1
        assert db.get_transaction() is transaction
        assert transaction.is_active


def test_changed_readiness_fails_closed_without_disabling_or_revoking(postgres_engine):
    with committed_scene(postgres_engine) as (db, context, _subject, _source):
        # Simulate policy-relevant state changing after normal authentication.
        with postgres_engine.begin() as writer:
            writer.execute(
                update(Person)
                .where(Person.id == context.person.id)
                .values(date_of_birth=date(2015, 1, 1))
            )
            writer.execute(
                StudentProfile.__table__.insert().values(
                    person_id=context.person.id,
                    student_number="readiness-" + context.person.id.hex[:16],
                    status="active",
                )
            )
        with pytest.raises(ProblemError) as caught:
            departure_service(db).list_reminders(
                actor=context,
                now=NOW,
                window="three_days",
                page=1,
                page_size=20,
            )
        assert caught.value.status == 401
        db.expire_all()
        assert db.get(UserAccount, context.account.id).status == "active"
        assert db.get(AuthSession, context.session.id).revoked_at is None
