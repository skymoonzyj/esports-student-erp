from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, undefer

from app.auth.models import UserAccount
from app.identity.models import Person
from app.travel_reports.converter import (
    ConvertedTravelReport,
    TravelReportWorkbookError,
    convert_travel_report,
    formula_results_are_cached,
)
from app.travel_reports.models import TravelReportConversion


class TravelReportServiceError(RuntimeError):
    code = "travel_report_service_error"


class TravelReportDuplicate(TravelReportServiceError):
    code = "travel_report_duplicate"


class TravelReportNotFound(TravelReportServiceError):
    code = "travel_report_not_found"


@dataclass(frozen=True)
class TravelReportListItem:
    report: TravelReportConversion
    imported_by_name: str


@dataclass(frozen=True)
class TravelReportBillingPeriod:
    billing_period_start: date
    billing_period_end: date


@dataclass(frozen=True)
class TravelReportListResult:
    items: list[TravelReportListItem]
    page: int
    page_size: int
    total: int
    billing_periods: list[TravelReportBillingPeriod]
    cost_centers: list[str]


class TravelReportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        converted: ConvertedTravelReport,
        *,
        source_content: bytes,
        actor_user_id: UUID,
    ) -> TravelReportConversion:
        existing = self.db.scalar(
            select(TravelReportConversion.id).where(
                TravelReportConversion.source_sha256 == converted.source_sha256
            )
        )
        if existing is not None:
            raise TravelReportDuplicate("source workbook already imported")
        report = TravelReportConversion(
            original_filename=converted.original_filename,
            converted_filename=converted.converted_filename,
            source_sha256=converted.source_sha256,
            converted_sha256=converted.converted_sha256,
            source_byte_size=converted.source_byte_size,
            converted_byte_size=converted.converted_byte_size,
            source_content=source_content,
            converted_content=converted.converted_content,
            company_name=converted.company_name,
            cost_center=converted.cost_center,
            billing_period_start=converted.billing_period_start,
            billing_period_end=converted.billing_period_end,
            domestic_air_count=converted.domestic_air_count,
            international_air_count=converted.international_air_count,
            train_count=converted.train_count,
            source_total_amount=converted.source_total_amount,
            converted_total_amount=converted.converted_total_amount,
            warnings=list(converted.warnings),
            imported_by_user_id=actor_user_id,
        )
        self.db.add(report)
        try:
            self.db.flush()
        except IntegrityError as exc:
            raise TravelReportDuplicate("source workbook already imported") from exc
        return report

    def list(
        self,
        *,
        page: int,
        page_size: int,
        billing_period_start: date | None = None,
        billing_period_end: date | None = None,
        cost_center: str | None = None,
    ) -> TravelReportListResult:
        count_statement = select(func.count(TravelReportConversion.id))
        list_statement = (
            select(TravelReportConversion, Person.full_name)
            .join(UserAccount, UserAccount.id == TravelReportConversion.imported_by_user_id)
            .join(Person, Person.id == UserAccount.person_id)
        )
        if billing_period_start is not None:
            condition = TravelReportConversion.billing_period_start == billing_period_start
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)
        if billing_period_end is not None:
            condition = TravelReportConversion.billing_period_end == billing_period_end
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)
        if cost_center is not None:
            condition = TravelReportConversion.cost_center == cost_center
            count_statement = count_statement.where(condition)
            list_statement = list_statement.where(condition)

        total = int(self.db.scalar(count_statement) or 0)
        rows = self.db.execute(
            list_statement.order_by(
                TravelReportConversion.created_at.desc(),
                TravelReportConversion.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        period_rows = self.db.execute(
            select(
                TravelReportConversion.billing_period_start,
                TravelReportConversion.billing_period_end,
            )
            .distinct()
            .order_by(
                TravelReportConversion.billing_period_start.desc(),
                TravelReportConversion.billing_period_end.desc(),
            )
        ).all()
        cost_centers = list(
            self.db.scalars(
                select(TravelReportConversion.cost_center)
                .distinct()
                .order_by(TravelReportConversion.cost_center.asc())
            )
        )
        return TravelReportListResult(
            items=[
                TravelReportListItem(report=report, imported_by_name=full_name)
                for report, full_name in rows
            ],
            page=page,
            page_size=page_size,
            total=total,
            billing_periods=[
                TravelReportBillingPeriod(
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                )
                for period_start, period_end in period_rows
            ],
            cost_centers=cost_centers,
        )

    def get(self, report_id: UUID, *, content_kind: str | None = None) -> TravelReportConversion:
        statement = select(TravelReportConversion).where(TravelReportConversion.id == report_id)
        if content_kind == "source":
            statement = statement.options(undefer(TravelReportConversion.source_content))
        elif content_kind == "converted":
            statement = statement.options(
                undefer(TravelReportConversion.source_content),
                undefer(TravelReportConversion.converted_content),
            ).with_for_update()
        report = self.db.scalar(statement)
        if report is None:
            raise TravelReportNotFound("travel report not found")
        if content_kind == "converted" and not formula_results_are_cached(
            report.converted_content
        ):
            try:
                converted = convert_travel_report(
                    report.source_content,
                    report.original_filename,
                )
            except TravelReportWorkbookError as exc:
                raise TravelReportServiceError(
                    "stored source workbook could not be refreshed"
                ) from exc
            if converted.source_sha256 != report.source_sha256:
                raise TravelReportServiceError("stored source workbook digest changed")
            report.converted_filename = converted.converted_filename
            report.converted_sha256 = converted.converted_sha256
            report.converted_byte_size = converted.converted_byte_size
            report.converted_content = converted.converted_content
            report.company_name = converted.company_name
            report.cost_center = converted.cost_center
            report.billing_period_start = converted.billing_period_start
            report.billing_period_end = converted.billing_period_end
            report.domestic_air_count = converted.domestic_air_count
            report.international_air_count = converted.international_air_count
            report.train_count = converted.train_count
            report.source_total_amount = converted.source_total_amount
            report.converted_total_amount = converted.converted_total_amount
            report.warnings = list(converted.warnings)
            self.db.flush()
        return report
