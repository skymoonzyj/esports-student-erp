from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.identity.readiness import GuardianReadinessPolicy
from app.rbac.service import RbacService
from app.revenue_share.models import (
    RevenueShareOrganization,
    RevenueShareOrganizationManagerAssignment,
)
from app.revenue_share.organization_manager_schemas import (
    OrganizationManagerCandidate,
    OrganizationManagerCandidatePage,
    OrganizationManagerResponse,
)


class OrganizationManagerService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _require_actor(self, actor_user_id: UUID) -> None:
        actor_available = self.db.scalar(
            select(UserAccount.id)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == actor_user_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        rbac = RbacService(self.db)
        if (
            actor_available is None
            or not rbac.account_has_role(actor_user_id, "super_admin")
            or not rbac.decide(
                user_id=actor_user_id,
                permission="revenue_share.manage",
                resource_tokens={"global"},
            ).allowed
        ):
            raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")

    def _organization(
        self, organization_id: UUID, *, lock: bool = False
    ) -> RevenueShareOrganization:
        query = select(RevenueShareOrganization).where(
            RevenueShareOrganization.id == organization_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        organization = self.db.scalar(query)
        if organization is None:
            raise ProblemError(status=404, code="revenue_share_not_found", detail="关联对象不存在")
        return organization

    def _lock_participants(self, actor_user_id: UUID, account_id: UUID) -> None:
        person_ids = set(
            self.db.scalars(
                select(UserAccount.person_id).where(
                    UserAccount.id.in_({actor_user_id, account_id})
                ),
            )
        )
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students(person_ids)
        readiness.lock_accounts(person_ids)
        # Authorization is recomputed after locks; preflight and identity-map
        # snapshots are never used as write authority.
        self._require_actor(actor_user_id)

    def _active_assignment(
        self, organization_id: UUID, account_id: UUID
    ) -> RevenueShareOrganizationManagerAssignment | None:
        return self.db.scalar(
            select(RevenueShareOrganizationManagerAssignment)
            .where(
                RevenueShareOrganizationManagerAssignment.organization_id == organization_id,
                RevenueShareOrganizationManagerAssignment.account_id == account_id,
                RevenueShareOrganizationManagerAssignment.revoked_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _response(
        self, assignment: RevenueShareOrganizationManagerAssignment
    ) -> OrganizationManagerResponse:
        person = self.db.execute(
            select(Person.full_name, Person.nickname)
            .join(
                UserAccount,
                UserAccount.person_id == Person.id,
            )
            .where(UserAccount.id == assignment.account_id)
        ).one()
        return OrganizationManagerResponse(
            account_id=assignment.account_id,
            full_name=person.full_name,
            nickname=person.nickname,
            organization_id=assignment.organization_id,
            granted_at=assignment.granted_at,
        )

    def _audit(
        self,
        actor_user_id: UUID,
        action: str,
        request_id: str,
        *,
        organization_id: UUID | None = None,
        assignment: RevenueShareOrganizationManagerAssignment | None = None,
        result_count: int | None = None,
    ) -> None:
        metadata: dict[str, str | int] = {}
        if organization_id is not None:
            metadata["organization_id"] = str(organization_id)
        if assignment is not None:
            metadata["target_account_id"] = str(assignment.account_id)
            metadata["assignment_id"] = str(assignment.id)
        if result_count is not None:
            metadata["result_count"] = result_count
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action=f"referral_organization_manager.{action}",
            resource_type="referral_organization_manager",
            resource_id=organization_id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata=metadata,
        )

    def list_managers(
        self, *, actor_user_id: UUID, organization_id: UUID, request_id: str
    ) -> list[OrganizationManagerResponse]:
        self._require_actor(actor_user_id)
        self._organization(organization_id)
        rows = self.db.execute(
            select(
                RevenueShareOrganizationManagerAssignment.account_id,
                RevenueShareOrganizationManagerAssignment.organization_id,
                RevenueShareOrganizationManagerAssignment.granted_at,
                Person.full_name,
                Person.nickname,
            )
            .join(
                UserAccount, UserAccount.id == RevenueShareOrganizationManagerAssignment.account_id
            )
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                RevenueShareOrganizationManagerAssignment.organization_id == organization_id,
                RevenueShareOrganizationManagerAssignment.revoked_at.is_(None),
            )
            .order_by(Person.full_name, UserAccount.id)
        ).mappings()
        result = [OrganizationManagerResponse.model_validate(row) for row in rows]
        with atomic_write_transaction(self.db):
            self._audit(
                actor_user_id,
                "list",
                request_id,
                organization_id=organization_id,
                result_count=len(result),
            )
        return result

    def list_candidates(
        self, *, actor_user_id: UUID, q: str, page: int, page_size: int, request_id: str
    ) -> OrganizationManagerCandidatePage:
        self._require_actor(actor_user_id)
        if page < 1 or not 1 <= page_size <= 100 or len(q) > 120:
            raise ProblemError(status=422, code="invalid_manager_query", detail="查询参数无效")
        query = (
            select(UserAccount.id.label("account_id"), Person.full_name, Person.nickname)
            .join(
                Person,
                Person.id == UserAccount.person_id,
            )
            .where(UserAccount.status == "active", Person.deleted_at.is_(None))
        )
        if q.strip():
            term = q.strip()
            conditions = [
                Person.full_name.icontains(term, autoescape=True),
                Person.nickname.icontains(term, autoescape=True),
            ]
            try:
                conditions.append(UserAccount.id == UUID(term))
            except ValueError:
                pass
            query = query.where(or_(*conditions))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(
            query.order_by(Person.full_name, UserAccount.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        items = [OrganizationManagerCandidate.model_validate(row) for row in rows]
        with atomic_write_transaction(self.db):
            self._audit(actor_user_id, "candidates", request_id, result_count=len(items))
        return OrganizationManagerCandidatePage(
            items=items, total=total, page=page, page_size=page_size
        )

    @staticmethod
    def _time(occurred_at: datetime | None) -> datetime:
        result = occurred_at or datetime.now(UTC)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ProblemError(status=422, code="invalid_manager_time", detail="操作时间无效")
        return result

    def grant_manager(
        self,
        *,
        actor_user_id: UUID,
        organization_id: UUID,
        account_id: UUID,
        request_id: str,
        occurred_at: datetime | None = None,
    ) -> OrganizationManagerResponse:
        self._require_actor(actor_user_id)
        with atomic_write_transaction(self.db):
            self._lock_participants(actor_user_id, account_id)
            target = self.db.scalar(
                select(UserAccount.id)
                .join(Person, Person.id == UserAccount.person_id)
                .where(
                    UserAccount.id == account_id,
                    UserAccount.status == "active",
                    Person.deleted_at.is_(None),
                )
            )
            if target is None:
                raise ProblemError(
                    status=404, code="revenue_share_not_found", detail="关联对象不存在"
                )
            organization = self._organization(organization_id, lock=True)
            if not organization.is_active:
                raise ProblemError(
                    status=409, code="organization_inactive", detail="停用机构不能新增负责人"
                )
            assignment = self._active_assignment(organization_id, account_id)
            if assignment is None:
                assignment = RevenueShareOrganizationManagerAssignment(
                    organization_id=organization_id,
                    account_id=account_id,
                    granted_at=self._time(occurred_at),
                    granted_by_user_id=actor_user_id,
                )
                self.db.add(assignment)
                self.db.flush()
                self._audit(
                    actor_user_id,
                    "assign",
                    request_id,
                    organization_id=organization_id,
                    assignment=assignment,
                )
            result = self._response(assignment)
        return result

    def revoke_manager(
        self,
        *,
        actor_user_id: UUID,
        organization_id: UUID,
        account_id: UUID,
        request_id: str,
        occurred_at: datetime | None = None,
    ) -> None:
        self._require_actor(actor_user_id)
        with atomic_write_transaction(self.db):
            self._lock_participants(actor_user_id, account_id)
            self._organization(organization_id, lock=True)
            assignment = self._active_assignment(organization_id, account_id)
            if assignment is not None:
                revoked_at = self._time(occurred_at)
                if revoked_at < assignment.granted_at:
                    raise ProblemError(
                        status=422, code="invalid_manager_time", detail="撤销时间不能早于授权时间"
                    )
                assignment.revoked_at = revoked_at
                assignment.revoked_by_user_id = actor_user_id
                self.db.flush()
                self._audit(
                    actor_user_id,
                    "revoke",
                    request_id,
                    organization_id=organization_id,
                    assignment=assignment,
                )
