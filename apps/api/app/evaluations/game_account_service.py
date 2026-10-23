from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import (
    AuxiliaryPhoneConflict,
    EvaluationGameAccountError,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    GameAccountAlreadyUnbound,
    GameAccountLimitInvalid,
    GameAccountLimitReached,
    GameIdAlreadyBound,
)
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.evaluations.schemas import (
    AuxiliaryPhoneCreate,
    AuxiliaryPhoneDeactivate,
    AuxiliaryPhoneItem,
    AuxiliaryPhoneMatch,
    AuxiliaryPhoneReplace,
    GameAccountBind,
    GameAccountLimitUpdate,
    GameAccountUnbind,
    StudentGameAccountItem,
    normalize_game_id,
)
from app.identity.models import Person, StudentProfile
from app.identity.normalization import normalize_phone
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import GameProject

_GAME_ID_FIELD = "student_game_id"
_AUXILIARY_PHONE_FIELD = "student_auxiliary_phone"
_MANAGE_PERMISSION = "student_game_accounts.manage"
_CENTER_PERMISSION = "evaluation_center.access"
_AUXILIARY_PHONE_MATCH_CANDIDATE_LIMIT = 100
_AMBIGUOUS_MATCH_COUNT = 2
_GAME_ID_CONSTRAINTS = frozenset(
    {
        "uq_student_game_accounts_active_project_digest",
        "excl_student_game_accounts_project_digest_overlap",
    }
)

class GameAccountService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        access: EvaluationAccessService | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.access = access or EvaluationAccessService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("operation datetime must include an offset")

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

    @staticmethod
    def _mask_phone(value: str) -> str:
        return f"{value[:3]}****{value[-4:]}"

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)

    @classmethod
    def _translate_game_integrity(cls, exc: IntegrityError) -> EvaluationGameAccountError:
        if cls._constraint_name(exc) in _GAME_ID_CONSTRAINTS:
            return GameIdAlreadyBound("game id is already bound in this project")
        return EvaluationGameAccountError("game account transaction failed")

    @classmethod
    def _translate_phone_integrity(cls, exc: IntegrityError) -> EvaluationGameAccountError:
        if cls._constraint_name(exc) == "uq_student_auxiliary_phones_active_student_digest":
            return AuxiliaryPhoneConflict("auxiliary phone is already active")
        return EvaluationGameAccountError("auxiliary phone transaction failed")

    def _append_audit(
        self,
        *,
        actor_user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID,
        request_id: str,
        metadata: dict[str, object],
    ) -> None:
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata=metadata,
        )

    def _require_object_access(
        self,
        *,
        actor_user_id: UUID,
        student_person_id: UUID,
        project_id: UUID | None = None,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        if not self._target_is_visible(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("game account operation is not allowed")
        decision = self.access.decide(
            account_id=actor_user_id,
            permission=_MANAGE_PERMISSION,
            resource=EvaluationResource(
                student_person_id=student_person_id,
                project_id=project_id,
            ),
            occurred_at=occurred_at,
        )
        if not decision.allowed:
            raise EvaluationOperationDenied("game account operation is not allowed")

    def _require_game_account_access(
        self,
        *,
        actor_user_id: UUID,
        student_person_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> None:
        center_decision = self.access.decide(
            account_id=actor_user_id,
            permission=_CENTER_PERMISSION,
            resource=EvaluationResource(
                student_person_id=student_person_id,
                project_id=project_id,
            ),
            occurred_at=occurred_at,
        )
        if not center_decision.allowed:
            raise EvaluationResourceNotFound("game account resource was not found")
        self._require_object_access(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )

    def _target_is_visible(
        self,
        *,
        actor_user_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        return EvaluationContextService(
            self.db,
            access=self.access,
        ).target_is_visible(
            account_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )

    def _require_administrator(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._require_aware(occurred_at)
        decision = self.access.decide(
            account_id=actor_user_id,
            permission=_MANAGE_PERMISSION,
            resource=EvaluationResource(),
            occurred_at=occurred_at,
        )
        if not decision.allowed:
            raise EvaluationOperationDenied("administrator permission is required")

    def _require_targeted_administrator(
        self,
        *,
        actor_user_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._require_administrator(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        self._require_object_access(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )

    def _lock_live_student_person(self, student_person_id: UUID) -> None:
        live_person_id = self.db.scalar(
            select(Person.id)
            .where(
                Person.id == student_person_id,
                Person.deleted_at.is_(None),
            )
            .with_for_update(read=True)
        )
        if live_person_id is None:
            raise EvaluationResourceNotFound("student was not found")

    def _student_profile(
        self,
        student_person_id: UUID,
        *,
        lock: bool,
    ) -> StudentProfile:
        if lock:
            self._lock_live_student_person(student_person_id)
            statement = (
                select(StudentProfile)
                .where(StudentProfile.person_id == student_person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        else:
            statement = (
                select(StudentProfile)
                .join(Person, Person.id == StudentProfile.person_id)
                .where(
                    StudentProfile.person_id == student_person_id,
                    Person.deleted_at.is_(None),
                )
            )
        profile = self.db.scalar(statement)
        if profile is None:
            raise EvaluationResourceNotFound("student was not found")
        return profile

    def _require_project(self, project_id: UUID) -> GameProject:
        project = self.db.scalar(
            select(GameProject)
            .where(
                GameProject.id == project_id,
                GameProject.is_active.is_(True),
            )
            .with_for_update(read=True)
        )
        if project is None:
            raise EvaluationResourceNotFound("project was not found")
        return project

    def bind_game_account(
        self,
        student_person_id: UUID,
        input: GameAccountBind,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> StudentGameAccount:
        self._require_game_account_access(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            project_id=input.project_id,
            occurred_at=occurred_at,
        )
        normalized = normalize_game_id(input.game_id)
        digest = student_game_id_digest(self.lookup, normalized)
        ciphertext = self.cipher.encrypt(normalized, field_name=_GAME_ID_FIELD)
        try:
            with write_transaction(self.db):
                profile = self._student_profile(student_person_id, lock=True)
                project = self._require_project(input.project_id)
                self._require_game_account_access(
                    actor_user_id=actor_user_id,
                    student_person_id=student_person_id,
                    project_id=project.id,
                    occurred_at=occurred_at,
                )
                active_count = self.db.scalar(
                    select(func.count(StudentGameAccount.id)).where(
                        StudentGameAccount.student_person_id == student_person_id,
                        StudentGameAccount.unbound_at.is_(None),
                    )
                ) or 0
                if active_count >= profile.game_account_limit:
                    raise GameAccountLimitReached("active game account limit reached")
                account = StudentGameAccount(
                    student_person_id=student_person_id,
                    project_id=project.id,
                    identifier_ciphertext=ciphertext,
                    identifier_digest=digest,
                    valid_from=occurred_at,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(account)
                self.db.flush()
                self._append_audit(
                    actor_user_id=actor_user_id,
                    action="student_game_account.bind",
                    resource_type="student_game_account",
                    resource_id=account.id,
                    request_id=request_id,
                    metadata={
                        "game_account_id": str(account.id),
                        "student_person_id": str(student_person_id),
                        "project_id": str(project.id),
                    },
                )
            return account
        except IntegrityError as exc:
            raise self._translate_game_integrity(exc) from None

    def unbind_game_account(
        self,
        student_person_id: UUID,
        account_id: UUID,
        input: GameAccountUnbind,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> StudentGameAccount:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            self._lock_live_student_person(student_person_id)
            account = self.db.scalar(
                select(StudentGameAccount)
                .where(
                    StudentGameAccount.id == account_id,
                    StudentGameAccount.student_person_id == student_person_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if account is None:
                raise EvaluationResourceNotFound("game account was not found")
            self._require_game_account_access(
                actor_user_id=actor_user_id,
                student_person_id=student_person_id,
                project_id=account.project_id,
                occurred_at=occurred_at,
            )
            if account.unbound_at is not None:
                raise GameAccountAlreadyUnbound("game account is already unbound")
            if occurred_at <= account.valid_from:
                raise GameIdAlreadyBound("unbind time must follow bind time")
            account.unbound_at = occurred_at
            account.unbound_by_user_id = actor_user_id
            account.unbind_reason = input.reason
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="student_game_account.unbind",
                resource_type="student_game_account",
                resource_id=account.id,
                request_id=request_id,
                metadata={
                    "game_account_id": str(account.id),
                    "student_person_id": str(student_person_id),
                    "project_id": str(account.project_id),
                },
            )
        return account

    def list_game_accounts(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_unbound: bool,
        page: int = 1,
        page_size: int = 20,
    ) -> list[StudentGameAccountItem]:
        statement = self._game_account_history_statement(
            student_person_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            include_unbound=include_unbound,
            page=page,
            page_size=page_size,
        )
        if statement is None:
            return []
        return self._game_account_history_items(
            statement,
            page=page,
            page_size=page_size,
        )

    def list_game_accounts_page(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_unbound: bool,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[StudentGameAccountItem], int]:
        statement = self._game_account_history_statement(
            student_person_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            include_unbound=include_unbound,
            page=page,
            page_size=page_size,
        )
        if statement is None:
            return [], 0
        total = (
            self.db.scalar(
                select(func.count()).select_from(statement.order_by(None).subquery())
            )
            or 0
        )
        return (
            self._game_account_history_items(
                statement,
                page=page,
                page_size=page_size,
            ),
            total,
        )

    def _game_account_history_statement(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_unbound: bool,
        page: int,
        page_size: int,
    ) -> Select[tuple[StudentGameAccount, GameProject]] | None:
        self._validate_pagination(page, page_size)
        self._require_aware(occurred_at)
        self._student_profile(student_person_id, lock=False)
        if not self._target_is_visible(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("game account operation is not allowed")
        account_filters = [StudentGameAccount.student_person_id == student_person_id]
        if not include_unbound:
            account_filters.append(StudentGameAccount.unbound_at.is_(None))
        project_ids = list(
            self.db.scalars(
                select(StudentGameAccount.project_id)
                .where(*account_filters)
                .distinct()
            )
        )
        if not project_ids:
            return None
        authorized_resources = self.access.filter_resources(
            account_id=actor_user_id,
            permission=_MANAGE_PERMISSION,
            resources=[
                EvaluationResource(
                    student_person_id=student_person_id,
                    project_id=project_id,
                )
                for project_id in project_ids
            ],
            occurred_at=occurred_at,
        )
        authorized_resources = self.access.filter_resources(
            account_id=actor_user_id,
            permission=_CENTER_PERMISSION,
            resources=authorized_resources,
            occurred_at=occurred_at,
        )
        authorized_project_ids = {
            resource.project_id
            for resource in authorized_resources
            if resource.project_id is not None
        }
        if not authorized_project_ids:
            return None
        return (
            select(StudentGameAccount, GameProject)
            .join(GameProject, GameProject.id == StudentGameAccount.project_id)
            .where(
                *account_filters,
                StudentGameAccount.project_id.in_(authorized_project_ids),
            )
        )

    def _game_account_history_items(
        self,
        statement: Select[tuple[StudentGameAccount, GameProject]],
        *,
        page: int,
        page_size: int,
    ) -> list[StudentGameAccountItem]:
        rows = list(
            self.db.execute(
                statement.order_by(
                    StudentGameAccount.valid_from.desc(),
                    StudentGameAccount.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return [
            StudentGameAccountItem(
                id=account.id,
                project_id=project.id,
                project_name=project.name,
                game_id=self.cipher.decrypt(
                    account.identifier_ciphertext,
                    field_name=_GAME_ID_FIELD,
                ),
                status="unbound" if account.unbound_at is not None else "active",
                valid_from=account.valid_from,
                unbound_at=account.unbound_at,
                unbind_reason=account.unbind_reason,
            )
            for account, project in rows
        ]

    def update_game_account_limit(
        self,
        student_person_id: UUID,
        input: GameAccountLimitUpdate,
        *,
        actor_user_id: UUID,
        request_id: str,
        occurred_at: datetime | None = None,
    ) -> StudentProfile:
        reference_time = occurred_at or datetime.now(UTC)
        self._require_targeted_administrator(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=reference_time,
        )
        with write_transaction(self.db):
            profile = self._student_profile(student_person_id, lock=True)
            active_count = self.db.scalar(
                select(func.count(StudentGameAccount.id)).where(
                    StudentGameAccount.student_person_id == student_person_id,
                    StudentGameAccount.unbound_at.is_(None),
                )
            ) or 0
            if input.max_active_accounts < active_count:
                raise GameAccountLimitInvalid("limit cannot be below active account count")
            old_limit = profile.game_account_limit
            profile.game_account_limit = input.max_active_accounts
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="student_game_account.limit_update",
                resource_type="student_profile",
                resource_id=student_person_id,
                request_id=request_id,
                metadata={
                    "old_limit": old_limit,
                    "new_limit": input.max_active_accounts,
                },
            )
        return profile

    def add_auxiliary_phone(
        self,
        student_person_id: UUID,
        input: AuxiliaryPhoneCreate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> StudentAuxiliaryPhone:
        self._require_targeted_administrator(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )
        normalized = normalize_phone(input.phone)
        try:
            with write_transaction(self.db):
                self._student_profile(student_person_id, lock=True)
                phone = StudentAuxiliaryPhone(
                    student_person_id=student_person_id,
                    phone_ciphertext=self.cipher.encrypt(
                        normalized,
                        field_name=_AUXILIARY_PHONE_FIELD,
                    ),
                    phone_digest=self.lookup.digest(normalized),
                    valid_from=occurred_at,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(phone)
                self.db.flush()
                self._append_audit(
                    actor_user_id=actor_user_id,
                    action="student_auxiliary_phone.create",
                    resource_type="student_auxiliary_phone",
                    resource_id=phone.id,
                    request_id=request_id,
                    metadata={
                        "auxiliary_phone_id": str(phone.id),
                        "student_person_id": str(student_person_id),
                    },
                )
            return phone
        except IntegrityError as exc:
            raise self._translate_phone_integrity(exc) from None

    def _locked_auxiliary_phone(
        self,
        student_person_id: UUID,
        phone_id: UUID,
    ) -> StudentAuxiliaryPhone:
        phone = self.db.scalar(
            select(StudentAuxiliaryPhone)
            .where(
                StudentAuxiliaryPhone.id == phone_id,
                StudentAuxiliaryPhone.student_person_id == student_person_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if phone is None:
            raise EvaluationResourceNotFound("auxiliary phone was not found")
        return phone

    def replace_auxiliary_phone(
        self,
        student_person_id: UUID,
        phone_id: UUID,
        input: AuxiliaryPhoneReplace,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> StudentAuxiliaryPhone:
        self._require_targeted_administrator(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )
        normalized = normalize_phone(input.phone)
        try:
            with write_transaction(self.db):
                self._student_profile(student_person_id, lock=True)
                previous = self._locked_auxiliary_phone(student_person_id, phone_id)
                if previous.disabled_at is not None:
                    raise AuxiliaryPhoneConflict("auxiliary phone is already inactive")
                if occurred_at <= previous.valid_from:
                    raise AuxiliaryPhoneConflict("replacement time must follow creation")
                previous.disabled_at = occurred_at
                previous.disabled_by_user_id = actor_user_id
                previous.disable_reason = input.reason
                self.db.flush()
                replacement = StudentAuxiliaryPhone(
                    student_person_id=student_person_id,
                    phone_ciphertext=self.cipher.encrypt(
                        normalized,
                        field_name=_AUXILIARY_PHONE_FIELD,
                    ),
                    phone_digest=self.lookup.digest(normalized),
                    valid_from=occurred_at,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(replacement)
                self.db.flush()
                self._append_audit(
                    actor_user_id=actor_user_id,
                    action="student_auxiliary_phone.replace",
                    resource_type="student_auxiliary_phone",
                    resource_id=replacement.id,
                    request_id=request_id,
                    metadata={
                        "auxiliary_phone_id": str(replacement.id),
                        "student_person_id": str(student_person_id),
                    },
                )
            return replacement
        except IntegrityError as exc:
            raise self._translate_phone_integrity(exc) from None

    def deactivate_auxiliary_phone(
        self,
        student_person_id: UUID,
        phone_id: UUID,
        input: AuxiliaryPhoneDeactivate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> StudentAuxiliaryPhone:
        self._require_targeted_administrator(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )
        with write_transaction(self.db):
            self._student_profile(student_person_id, lock=True)
            phone = self._locked_auxiliary_phone(student_person_id, phone_id)
            if phone.disabled_at is not None:
                raise AuxiliaryPhoneConflict("auxiliary phone is already inactive")
            if occurred_at <= phone.valid_from:
                raise AuxiliaryPhoneConflict("deactivation time must follow creation")
            phone.disabled_at = occurred_at
            phone.disabled_by_user_id = actor_user_id
            phone.disable_reason = input.reason
            self.db.flush()
            self._append_audit(
                actor_user_id=actor_user_id,
                action="student_auxiliary_phone.deactivate",
                resource_type="student_auxiliary_phone",
                resource_id=phone.id,
                request_id=request_id,
                metadata={
                    "auxiliary_phone_id": str(phone.id),
                    "student_person_id": str(student_person_id),
                },
            )
        return phone

    def list_auxiliary_phones(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_inactive: bool,
        page: int = 1,
        page_size: int = 20,
    ) -> list[AuxiliaryPhoneItem]:
        statement = self._auxiliary_phone_history_statement(
            student_person_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
        return self._auxiliary_phone_history_items(
            statement,
            page=page,
            page_size=page_size,
        )

    def list_auxiliary_phones_page(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_inactive: bool,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AuxiliaryPhoneItem], int]:
        statement = self._auxiliary_phone_history_statement(
            student_person_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            include_inactive=include_inactive,
            page=page,
            page_size=page_size,
        )
        total = self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        return (
            self._auxiliary_phone_history_items(
                statement,
                page=page,
                page_size=page_size,
            ),
            total,
        )

    def _auxiliary_phone_history_statement(
        self,
        student_person_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        include_inactive: bool,
        page: int,
        page_size: int,
    ) -> Select[tuple[StudentAuxiliaryPhone]]:
        self._validate_pagination(page, page_size)
        self._require_targeted_administrator(
            actor_user_id=actor_user_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
        )
        self._student_profile(student_person_id, lock=False)
        statement = select(StudentAuxiliaryPhone).where(
            StudentAuxiliaryPhone.student_person_id == student_person_id
        )
        if not include_inactive:
            statement = statement.where(StudentAuxiliaryPhone.disabled_at.is_(None))
        return statement

    def _auxiliary_phone_history_items(
        self,
        statement: Select[tuple[StudentAuxiliaryPhone]],
        *,
        page: int,
        page_size: int,
    ) -> list[AuxiliaryPhoneItem]:
        phones = self.db.scalars(
            statement.order_by(
                StudentAuxiliaryPhone.valid_from.desc(),
                StudentAuxiliaryPhone.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [
            AuxiliaryPhoneItem(
                id=phone.id,
                masked_phone=self._mask_phone(
                    self.cipher.decrypt(
                        phone.phone_ciphertext,
                        field_name=_AUXILIARY_PHONE_FIELD,
                    )
                ),
                status="inactive" if phone.disabled_at is not None else "active",
                created_at=phone.created_at,
                disabled_at=phone.disabled_at,
            )
            for phone in phones
        ]

    def match_auxiliary_phone(
        self,
        raw_phone: str,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> AuxiliaryPhoneMatch:
        self._require_administrator(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        digest = self.lookup.digest(normalize_phone(raw_phone))
        matched_student_ids = list(
            self.db.scalars(
                select(StudentAuxiliaryPhone.student_person_id)
                .join(Person, Person.id == StudentAuxiliaryPhone.student_person_id)
                .where(
                    StudentAuxiliaryPhone.phone_digest == digest,
                    StudentAuxiliaryPhone.disabled_at.is_(None),
                    Person.deleted_at.is_(None),
                )
                .distinct()
                .order_by(StudentAuxiliaryPhone.student_person_id)
                .limit(_AUXILIARY_PHONE_MATCH_CANDIDATE_LIMIT + 1)
            )
        )
        if len(matched_student_ids) > _AUXILIARY_PHONE_MATCH_CANDIDATE_LIMIT:
            # Matching only distinguishes zero, unique, and multiple candidates. A
            # conservative multiple result prevents unbounded policy work and can
            # never turn a high-cardinality phone into an automatic unique match.
            return AuxiliaryPhoneMatch(
                student_person_id=None,
                match_count=_AMBIGUOUS_MATCH_COUNT,
                ambiguous=True,
            )
        authorized_resources = self.access.filter_resources(
            account_id=actor_user_id,
            permission=_MANAGE_PERMISSION,
            resources=[
                EvaluationResource(student_person_id=student_id)
                for student_id in matched_student_ids
            ],
            occurred_at=occurred_at,
        )
        student_ids = [
            resource.student_person_id
            for resource in authorized_resources
            if resource.student_person_id is not None
        ]
        return AuxiliaryPhoneMatch(
            student_person_id=student_ids[0] if len(student_ids) == 1 else None,
            match_count=len(student_ids),
            ambiguous=len(student_ids) > 1,
        )
