from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.core.transactions import write_transaction
from app.housing.models import HousingStay
from app.teaching.models import Cohort


class HousingCheckoutReconciler:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, now: datetime) -> int:
        if now.utcoffset() is None:
            raise ValueError("housing reconciliation now must be timezone-aware")

        cancelled_enrollment = exists(
            select(Enrollment.id).where(
                Enrollment.student_person_id == HousingStay.person_id,
                Enrollment.cohort_id == HousingStay.cohort_id,
                Enrollment.status == "cancelled",
            )
        )
        active_enrollment = exists(
            select(Enrollment.id).where(
                Enrollment.student_person_id == HousingStay.person_id,
                Enrollment.cohort_id == HousingStay.cohort_id,
                Enrollment.status == "active",
            )
        )
        cohort_has_ended = exists(
            select(Cohort.id).where(
                Cohort.id == HousingStay.cohort_id,
                Cohort.ends_at <= now,
            )
        )
        due_condition = or_(
            and_(
                HousingStay.duration_mode == "fixed_term",
                HousingStay.planned_check_out_at.is_not(None),
                HousingStay.planned_check_out_at <= now,
            ),
            and_(
                HousingStay.stay_purpose == "camp",
                HousingStay.cohort_id.is_not(None),
                or_(
                    cohort_has_ended,
                    and_(cancelled_enrollment, ~active_enrollment),
                ),
            ),
        )
        with write_transaction(self.db):
            camp_cohort_ids = (
                select(HousingStay.cohort_id)
                .where(
                    HousingStay.status == "checked_in",
                    HousingStay.stay_purpose == "camp",
                    HousingStay.cohort_id.is_not(None),
                )
                .distinct()
            )
            list(
                self.db.scalars(
                    select(Cohort)
                    .where(Cohort.id.in_(camp_cohort_ids))
                    .order_by(Cohort.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            candidates = list(
                self.db.scalars(
                    select(HousingStay)
                    .where(
                        HousingStay.status == "checked_in",
                        due_condition,
                    )
                    .order_by(
                        HousingStay.planned_check_out_at,
                        HousingStay.cohort_id,
                        HousingStay.person_id,
                        HousingStay.id,
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            )
            for stay in candidates:
                stay.status = "checkout_pending"
                stay.version += 1
            self.db.flush()
        return len(candidates)
