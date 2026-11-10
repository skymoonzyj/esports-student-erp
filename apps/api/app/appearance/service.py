from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.appearance.models import GlobalAppearance
from app.appearance.schemas import AppearanceUpdate, AppearanceView, Theme
from app.audit.service import AuditService
from app.auth.dependencies import require_current_standard_account
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.rbac.service import RbacService


class AppearanceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _view(self, row: GlobalAppearance) -> AppearanceView:
        return AppearanceView(theme=cast(Theme, row.theme), revision=row.revision)

    def _row(self, *, lock: bool = False) -> GlobalAppearance:
        query = select(GlobalAppearance).where(GlobalAppearance.id == 1)
        if lock:
            query = query.with_for_update()
        row = self.db.scalar(query.execution_options(populate_existing=True))
        if row is None:
            # Missing migration/restore data is an operational fault, not a new
            # implicit revision that could overwrite the authoritative setting.
            raise ProblemError(status=503, code="appearance_unavailable", detail="系统外观暂不可用")
        return row

    def read(self) -> AppearanceView:
        return self._view(self._row())

    def require_manager(self, context: AuthContext) -> None:
        require_current_standard_account(self.db, context)
        rbac = RbacService(self.db)
        if (
            not rbac.account_has_role(context.account.id, "super_admin")
            or not rbac.decide(
                user_id=context.account.id,
                permission="operations.appearance.manage",
                resource_tokens={"global"},
            ).allowed
        ):
            raise ProblemError(
                status=403, code="permission_denied", detail="只有超级管理员可以管理系统外观"
            )

    def _lock_actor(self, context: AuthContext) -> None:
        # Match lifecycle ordering: person -> account -> login session -> resource.
        # Populate from DB, never authorize an old ORM account/session snapshot.
        account_id, person_id, session_id = (
            context.account.id,
            context.person.id,
            context.session.id,
        )
        for model, identity in (
            (Person, person_id),
            (UserAccount, account_id),
            (AuthSession, session_id),
        ):
            self.db.scalar(
                select(model)
                .where(model.id == identity)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        self.require_manager(context)

    def update(
        self, context: AuthContext, command: AppearanceUpdate, *, request_id: str
    ) -> AppearanceView:
        with atomic_write_transaction(self.db):
            self._lock_actor(context)
            row = self._row(lock=True)
            # A different publisher may have held the singleton while a role or
            # permission was revoked; recheck current authorization after waiting.
            self.require_manager(context)
            if command.expected_revision != row.revision:
                raise ProblemError(
                    status=409,
                    code="appearance_revision_conflict",
                    detail="系统外观已更新，请刷新后重试",
                )
            if command.theme != row.theme:
                old_theme, old_revision = row.theme, row.revision
                row.theme = command.theme
                row.revision += 1
                self.db.flush()
                AuditService(self.db).append(
                    actor_user_id=context.account.id,
                    action="operations.appearance.update",
                    resource_type="global_appearance",
                    resource_id=None,
                    purpose=None,
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "old_theme": old_theme,
                        "new_theme": row.theme,
                        "old_version": old_revision,
                        "new_version": row.revision,
                    },
                )
            result = self._view(row)
        return result
