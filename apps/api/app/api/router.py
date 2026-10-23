from typing import Literal

from fastapi import APIRouter

from app.admissions.router import router as admissions_router
from app.asset_management.router import router as asset_management_router
from app.audit.router import router as audit_router
from app.auth.admin_router import router as account_admin_router
from app.auth.router import router as auth_router
from app.esports_assessments.router import router as esports_assessments_router
from app.evaluations.admin_router import router as evaluation_admin_router
from app.evaluations.integration_router import router as evaluation_integration_router
from app.evaluations.integration_v2_router import (
    router as evaluation_integration_v2_router,
)
from app.evaluations.integration_v2_web_router import (
    router as evaluation_integration_v2_web_router,
)
from app.evaluations.portal_router import router as evaluation_portal_router
from app.evaluations.processing_router import router as evaluation_processing_router
from app.evaluations.report_router import router as teaching_report_router
from app.evaluations.review_router import router as evaluation_review_router
from app.evaluations.router import router as evaluation_router
from app.evaluations.survey_router import (
    admin_router as evaluation_survey_admin_router,
)
from app.evaluations.survey_router import (
    router as evaluation_survey_router,
)
from app.evaluations.task_router import router as evaluation_task_router
from app.food_preferences.router import router as food_preferences_router
from app.housing.occupancy_router import router as housing_occupancy_router
from app.housing.portal_router import router as housing_portal_router
from app.housing.reporting_router import router as housing_reporting_router
from app.housing.router import router as housing_router
from app.identity.router import router as identity_router
from app.mail.settings_router import router as mail_settings_router
from app.operations.router import router as operations_router
from app.portal.router import router as portal_router
from app.procurement.router import router as procurement_router
from app.rbac.router import router as rbac_router
from app.registration.admin_router import router as registration_admin_router
from app.registration.router import router as registration_router
from app.revenue_share.router import router as revenue_share_router
from app.talent_tests.router import router as talent_tests_router
from app.teaching.router import router as teaching_router
from app.team_management.router import router as team_management_router
from app.travel_reports.router import router as travel_reports_router

api_router = APIRouter()
api_router.include_router(admissions_router)
api_router.include_router(asset_management_router)
api_router.include_router(auth_router)
api_router.include_router(registration_router)
api_router.include_router(registration_admin_router)
api_router.include_router(revenue_share_router)
api_router.include_router(procurement_router)
api_router.include_router(travel_reports_router)
api_router.include_router(identity_router)
api_router.include_router(mail_settings_router)
api_router.include_router(operations_router)
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
api_router.include_router(evaluation_task_router)
api_router.include_router(evaluation_portal_router)
api_router.include_router(evaluation_admin_router)
api_router.include_router(evaluation_review_router)
api_router.include_router(evaluation_survey_router)
api_router.include_router(evaluation_survey_admin_router)
api_router.include_router(teaching_report_router)
api_router.include_router(evaluation_integration_router)
api_router.include_router(evaluation_integration_v2_web_router)
api_router.include_router(evaluation_integration_v2_router)
api_router.include_router(evaluation_processing_router)
api_router.include_router(food_preferences_router)
api_router.include_router(talent_tests_router)
api_router.include_router(team_management_router)
api_router.include_router(esports_assessments_router)


@api_router.get("/health/live", tags=["health"])
def liveness() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
