"""Opt-in, fixed synthetic invitation fixtures; manager grants belong to the real API test."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_VISITOR_ACCOUNT_ID,
    E2E_VISITOR_PERSON_ID,
    E2ESeedRefused,
    _require_e2e_environment,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.registration.models import RegistrationApplication  # noqa: F401 — binding FK dependency
from app.revenue_share.models import ReferralCode, RevenueShareOrganization, StudentReferralBinding

VIEWPORTS = (1440, 1024, 768, 360)
ORGANIZATION_IDS = tuple(UUID(f"e2900000-0000-4000-8000-{i:012d}") for i in range(1, 5))
STUDENT_IDS = tuple(UUID(f"e2000000-0000-4000-8000-{i * 10:012d}") for i in range(1, 5))
CODE_IDS = tuple(UUID(f"e2910000-0000-4000-8000-{i:012d}") for i in range(1, 5))
BINDING_IDS = tuple(UUID(f"e2920000-0000-4000-8000-{i:012d}") for i in range(1, 5))
OVERRIDE_ID = UUID("e2930000-0000-4000-8000-000000000001")


def seed_invited_housing_e2e(db: Session, *, settings: Settings) -> int:
    _require_e2e_environment(settings)
    with write_transaction(db):
        visitor = db.get(UserAccount, E2E_VISITOR_ACCOUNT_ID)
        if visitor is None or visitor.person_id != E2E_VISITOR_PERSON_ID or visitor.status != "active":
            raise E2ESeedRefused("Invitation E2E requires canonical visitor")
        if db.scalar(select(UserRole.user_id).where(UserRole.user_id == visitor.id)) is not None:
            raise E2ESeedRefused("Invitation E2E visitor must not have roles")
        existing_overrides = list(db.scalars(select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == visitor.id
        )))
        if any(rule.id != OVERRIDE_ID for rule in existing_overrides):
            raise E2ESeedRefused("Invitation E2E visitor must not have other overrides")
        if any(
            rule.permission_id != PERMISSIONS["referrals.read_invited_housing"].id
            or rule.effect != "allow" or rule.scope_kind != "self" or rule.scope_id is not None
            for rule in existing_overrides
        ):
            raise E2ESeedRefused("Invitation E2E override collision")
        expected_people = dict(zip(STUDENT_IDS, VIEWPORTS, strict=True))
        people = list(db.scalars(select(Person).where(Person.id.in_(STUDENT_IDS))))
        if len(people) != 4 or any(
            person.deleted_at is not None or person.full_name != f"E2E招生学员{expected_people[person.id]}"
            for person in people
        ):
            raise E2ESeedRefused("Invitation E2E requires canonical students")
        for viewport, organization_id, student_id, code_id, binding_id in zip(
            VIEWPORTS, ORGANIZATION_IDS, STUDENT_IDS, CODE_IDS, BINDING_IDS, strict=True
        ):
            name = f"E2E邀请组织{viewport}"
            organization = db.get(RevenueShareOrganization, organization_id)
            if organization is None:
                organization = RevenueShareOrganization(
                    id=organization_id, name=name, is_active=True,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                )
                db.add(organization)
            elif organization.name != name or organization.created_by_user_id != E2E_ADMIN_ACCOUNT_ID:
                raise E2ESeedRefused("Invitation E2E organization collision")
            # SessionLocal disables autoflush; persist the FK parent first.
            db.flush()
            code = db.get(ReferralCode, code_id)
            if code is None:
                code = ReferralCode(
                    id=code_id, code=f"E2EINV{viewport}", label=name, source_kind="organization",
                    referrer_organization_id=organization_id, is_active=True,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                )
                db.add(code)
            elif code.referrer_organization_id != organization_id or code.code != f"E2EINV{viewport}":
                raise E2ESeedRefused("Invitation E2E referral collision")
            db.flush()
            binding = db.get(StudentReferralBinding, binding_id)
            if binding is None:
                if db.scalar(select(StudentReferralBinding.id).where(
                    StudentReferralBinding.student_person_id == student_id
                )) is not None:
                    raise E2ESeedRefused("Invitation E2E student already has a different binding")
                db.add(StudentReferralBinding(
                    id=binding_id, student_person_id=student_id, referral_code_id=code_id,
                    source_kind="organization", referrer_organization_id=organization_id,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                ))
            elif binding.student_person_id != student_id or binding.referrer_organization_id != organization_id:
                raise E2ESeedRefused("Invitation E2E binding collision")
        if not existing_overrides:
            db.add(UserPermissionOverride(
                id=OVERRIDE_ID, user_id=visitor.id,
                permission_id=PERMISSIONS["referrals.read_invited_housing"].id,
                effect="allow", scope_kind="self", created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                grant_reason="E2E邀请只读权限夹具",
            ))
        db.flush()
    return 4


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        count = seed_invited_housing_e2e(db, settings=settings)
    print(f"Invited housing synthetic fixtures ready: organizations={count}; managers not pregranted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
