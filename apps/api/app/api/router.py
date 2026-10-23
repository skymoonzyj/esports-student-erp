from typing import Literal

from fastapi import APIRouter

from app.admissions.router import router as admissions_router
from app.audit.router import router as audit_router
from app.auth.admin_router import router as account_admin_router
from app.auth.registration_router import router as registration_router
from app.auth.router import router as auth_router
from app.evaluations.admin_router import router as evaluation_admin_router
from app.evaluations.integration_router import router as evaluation_integration_router
from app.evaluations.portal_router import router as evaluation_portal_router
from app.evaluations.router import router as evaluation_router
from app.housing.occupancy_router import router as housing_occupancy_router
from app.housing.portal_router import router as housing_portal_router
from app.housing.reporting_router import router as housing_reporting_router
from app.housing.router import router as housing_router
from app.identity.router import router as identity_router
from app.mail.settings_router import router as mail_settings_router
from app.portal.router import router as portal_router
from app.rbac.router import router as rbac_router
from app.teaching.router import router as teaching_router

api_router = APIRouter()
api_router.include_router(admissions_router)
api_router.include_router(auth_router)
api_router.include_router(registration_router)
api_router.include_router(identity_router)
api_router.include_router(housing_router)
api_router.include_router(housing_occupancy_router)
api_router.include_router(housing_portal_router)
api_router.include_router(housing_reporting_router)
api_router.include_router(account_admin_router)
api_router.include_router(rbac_router)
api_router.include_router(audit_router)
api_router.include_router(teaching_router)
api_router.include_router(portal_router)
api_router.include_router(evaluation_router)
api_router.include_router(evaluation_portal_router)
api_router.include_router(evaluation_admin_router)
api_router.include_router(evaluation_integration_router)
api_router.include_router(mail_settings_router)


@api_router.get("/health/live", tags=["health"])
def liveness() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
