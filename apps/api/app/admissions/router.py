from fastapi import APIRouter

from app.admissions.case_router import router as case_router
from app.admissions.enrollment_router import router as enrollment_router
from app.admissions.payment_router import router as payment_router

router = APIRouter()
router.include_router(payment_router)
router.include_router(case_router)
router.include_router(enrollment_router)
