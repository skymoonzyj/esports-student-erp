"""Permission-filtered SQL facts, grouped into a bounded person page."""

from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import Date, String, and_, case, cast, distinct, func, literal, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import CTE

from app.admissions.models import Enrollment
from app.housing.errors import HousingServiceError
from app.housing.history_validity import effective_stay_clause
from app.housing.models import HousingBuilding, HousingRoom, HousingStay
from app.identity.models import Person
from app.portal.departure_access import (
    DepartureAccess,
    DepartureEnrollmentRow,
    DepartureHousingRow,
)
from app.portal.departure_dates import (
    DepartureWindow,
    departure_window_dates,
    next_business_midnight,
)
from app.portal.departure_schemas import (
    DeparturePerson,
    DepartureRemindersResponse,
    EnrollmentEndEvent,
    PlannedCheckoutEvent,
)
from app.teaching.models import Cohort, GameProject, TeachingClass


class DepartureQuery:
    def __init__(self, db: Session, access: DepartureAccess):
        self.db = db
        self.access = access

    def _facts(self, today: date) -> CTE:
        last_day = today + timedelta(days=2)
        housing_date = cast(func.timezone("Asia/Shanghai", HousingStay.planned_check_out_at), Date)
        effective_end = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
            else_=Cohort.ends_at,
        )
        local_end = func.timezone("Asia/Shanghai", effective_end)
        # Match the web helper's hour/minute/second check, ignoring microseconds.
        midnight = and_(
            func.extract("hour", local_end) == 0,
            func.extract("minute", local_end) == 0,
            func.floor(func.extract("second", local_end)) == 0,
        )
        enrollment_date = case(
            (midnight, cast(local_end, Date) - 1),
            else_=cast(local_end, Date),
        )
        empty_text = cast(literal(None), String)
        housing = (
            select(
                HousingStay.person_id.label("person_id"),
                HousingStay.id.label("source_id"),
                literal("planned_checkout").label("kind"),
                housing_date.label("event_date"),
                HousingRoom.id.label("resource_id"),
                HousingBuilding.name.label("building_name"),
                HousingRoom.room_number.label("room_number"),
                empty_text.label("project_name"),
                empty_text.label("cohort_name"),
                empty_text.label("enrollment_status"),
            )
            .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .join(Person, Person.id == HousingStay.person_id)
            .where(
                HousingStay.status.in_(["checked_in", "checkout_pending"]),
                HousingStay.duration_mode == "fixed_term",
                HousingStay.actual_check_out_at.is_(None),
                effective_stay_clause(),
                Person.deleted_at.is_(None),
                housing_date.between(today, last_day),
                self.access.housing_criteria(),
            )
        )
        teaching = (
            select(
                Enrollment.student_person_id.label("person_id"),
                Enrollment.id.label("source_id"),
                literal("enrollment_end").label("kind"),
                enrollment_date.label("event_date"),
                TeachingClass.id.label("resource_id"),
                empty_text.label("building_name"),
                empty_text.label("room_number"),
                GameProject.name.label("project_name"),
                Cohort.name.label("cohort_name"),
                Enrollment.status.label("enrollment_status"),
            )
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .join(Person, Person.id == Enrollment.student_person_id)
            .where(
                Enrollment.status.in_(["active", "completed"]),
                Enrollment.cancelled_at.is_(None),
                Person.deleted_at.is_(None),
                enrollment_date.between(today, last_day),
                self.access.enrollment_criteria(),
            )
        )
        return housing.union_all(teaching).cte("departure_facts")

    def read_page(
        self,
        *,
        now: datetime,
        window: DepartureWindow,
        page: int,
        page_size: int,
    ) -> DepartureRemindersResponse:
        today, last_day = departure_window_dates(now, window)
        available = self.access.has_scope()
        response = DepartureRemindersResponse(
            available=available,
            observed_at=now,
            business_date=today,
            timezone="Asia/Shanghai",
            next_refresh_at=next_business_midnight(now),
            window=window,
            today_count=0,
            three_day_count=0,
            page=page,
            page_size=page_size,
            total=0,
            items=[],
        )
        if not available:
            return response
        facts = self._facts(today)
        stats = self.db.execute(
            select(
                func.count(distinct(facts.c.person_id)).label("three_day_count"),
                func.count(distinct(facts.c.person_id))
                .filter(facts.c.event_date == today)
                .label("today_count"),
            )
        ).one()
        response.today_count = stats.today_count
        response.three_day_count = stats.three_day_count
        response.total = stats.today_count if window == "today" else stats.three_day_count

        # Fail closed only for authorized stays of housing reminder candidates.
        # Hidden rooms must not create an observable success/error side channel.
        # Both counters are public even for the today window: guard every
        # three-day housing candidate, not just the selected page/window.
        # Teaching-only reminders never select a room, so unrelated housing
        # inconsistencies must not block that independent source.
        visible_people = select(facts.c.person_id).where(facts.c.kind == "planned_checkout")
        duplicate = self.db.scalar(
            select(HousingStay.person_id)
            .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
            .where(
                HousingStay.person_id.in_(visible_people),
                HousingStay.status.in_(["checked_in", "checkout_pending"]),
                HousingStay.actual_check_out_at.is_(None),
                effective_stay_clause(),
                self.access.housing_criteria(),
            )
            .group_by(HousingStay.person_id)
            .having(func.count() > 1)
            .limit(1)
        )
        if duplicate is not None:
            raise HousingServiceError("personal housing subject has multiple current stays")

        people = self.db.execute(
            select(
                Person.id,
                Person.full_name,
                Person.nickname,
                func.min(facts.c.event_date).label("first_date"),
            )
            .join(facts, facts.c.person_id == Person.id)
            .where(facts.c.event_date <= last_day)
            .group_by(Person.id, Person.full_name, Person.nickname)
            .order_by("first_date", Person.full_name, Person.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        if not people:
            return response
        rows = self.db.execute(
            select(facts)
            .where(
                facts.c.person_id.in_([person.id for person in people]),
                facts.c.event_date <= last_day,
            )
            .order_by(facts.c.event_date, facts.c.kind, facts.c.source_id)
        ).all()
        housing_rows = [
            DepartureHousingRow(row.source_id, row.person_id, row.resource_id)
            for row in rows
            if row.kind == "planned_checkout"
        ]
        enrollment_rows = [
            DepartureEnrollmentRow(row.source_id, row.person_id, row.resource_id)
            for row in rows
            if row.kind == "enrollment_end"
        ]
        paths = self.access.detail_paths(housing_rows, enrollment_rows)
        by_person: dict[UUID, DeparturePerson] = {
            person.id: DeparturePerson(
                person_id=person.id,
                full_name=person.full_name,
                nickname=person.nickname,
                events=[],
            )
            for person in people
        }
        for row in rows:
            detail_path = paths.get((row.kind, row.source_id))
            fact: PlannedCheckoutEvent | EnrollmentEndEvent
            if row.kind == "planned_checkout":
                fact = PlannedCheckoutEvent(
                    kind="planned_checkout",
                    source_id=row.source_id,
                    date=row.event_date,
                    detail_path=detail_path,
                    building_name=row.building_name,
                    room_number=row.room_number,
                )
            else:
                fact = EnrollmentEndEvent(
                    kind="enrollment_end",
                    source_id=row.source_id,
                    date=row.event_date,
                    detail_path=detail_path,
                    project_name=row.project_name,
                    cohort_name=row.cohort_name,
                    enrollment_status=row.enrollment_status,
                )
            by_person[row.person_id].events.append(fact)
        response.items = list(by_person.values())
        return response
