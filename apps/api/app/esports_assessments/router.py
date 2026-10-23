"""HTTP routes for independent esports talent assessments."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from .config_schemas import SuiteId
from .dependencies import Actor, Db, FormalCreator, PracticeCreator, Writer
from .errors import AssessmentExpired
from .formal_service import FormalAssessmentService
from .http_support import EsportsAssessmentRoute
from .practice_service import PracticeAssessmentService
from .preview_router import router as preview_router
from .queries import AssessmentQueries
from .query_schemas import (
    AssessmentContextView,
    AssessmentReportSummary,
    AttemptDetailView,
    CursorPage,
    HistoryItem,
    RecoveryItem,
    ReportFilters,
    SuiteCatalogView,
)
from .schemas import (
    AssessmentReportView,
    AttemptCreate,
    AttemptView,
    Mode,
    OperationRequest,
    PracticeCreate,
    ResultSaveView,
    ResultSubmit,
    SessionCreate,
    SessionView,
)

router = APIRouter(
    prefix="/esports-assessments",
    tags=["esports-assessments"],
    route_class=EsportsAssessmentRoute,
)
router.include_router(preview_router)
Cursor = Annotated[str | None, Query(max_length=2048)]
Limit = Annotated[int, Query(ge=1, le=100)]


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/context", response_model=AssessmentContextView)
def context(actor: Actor, db: Db) -> AssessmentContextView:
    return AssessmentQueries(db, actor, _now()).context()


@router.get("/suites", response_model=SuiteCatalogView)
def suites(actor: Actor, db: Db) -> SuiteCatalogView:
    return AssessmentQueries(db, actor, _now()).suites()


@router.get("/recovery", response_model=CursorPage[RecoveryItem])
def recovery(
    actor: Actor,
    db: Db,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> CursorPage[RecoveryItem]:
    return AssessmentQueries(db, actor, _now()).recovery(cursor, limit)


@router.post("/sessions", response_model=SessionView, status_code=201)
def create_session(value: SessionCreate, actor: FormalCreator, db: Db) -> SessionView:
    return FormalAssessmentService(db).create(actor, value, _now())


@router.get("/sessions/{session_id}", response_model=SessionView)
def session(session_id: UUID, actor: Actor, db: Db) -> SessionView:
    return AssessmentQueries(db, actor, _now()).session(session_id)


@router.post("/sessions/{session_id}/abandon", response_model=SessionView)
def abandon_session(
    session_id: UUID,
    value: OperationRequest,
    actor: Writer,
    db: Db,
) -> SessionView:
    return FormalAssessmentService(db).abandon(actor, session_id, value.operation_key, _now())


@router.post(
    "/sessions/{session_id}/attempts",
    response_model=AttemptView,
    status_code=201,
)
def create_attempt(
    session_id: UUID,
    value: AttemptCreate,
    actor: Writer,
    db: Db,
) -> AttemptView:
    return FormalAssessmentService(db).start_attempt(actor, session_id, value, _now())


@router.get("/attempts/{attempt_id}", response_model=AttemptDetailView)
def formal_attempt(attempt_id: UUID, actor: Actor, db: Db) -> AttemptDetailView:
    return AssessmentQueries(db, actor, _now()).detail("formal", attempt_id)


@router.post("/attempts/{attempt_id}/interrupt", response_model=AttemptView)
def interrupt_attempt(
    attempt_id: UUID,
    value: OperationRequest,
    actor: Writer,
    db: Db,
) -> AttemptView:
    result = FormalAssessmentService(db).interrupt(actor, attempt_id, value.operation_key, _now())
    if result.state == "expired":
        raise AssessmentExpired()
    return result


@router.post("/attempts/{attempt_id}/result", response_model=ResultSaveView, status_code=201)
def submit_result(
    attempt_id: UUID,
    value: ResultSubmit,
    actor: Writer,
    db: Db,
) -> ResultSaveView:
    return FormalAssessmentService(db).submit(actor, attempt_id, value, _now())


@router.post("/practice-runs", response_model=AttemptView, status_code=201)
def create_practice(value: PracticeCreate, actor: PracticeCreator, db: Db) -> AttemptView:
    return PracticeAssessmentService(db).start(actor, value, _now())


@router.get("/practice-runs/{run_id}", response_model=AttemptDetailView)
def practice_run(run_id: UUID, actor: Actor, db: Db) -> AttemptDetailView:
    return AssessmentQueries(db, actor, _now()).detail("practice", run_id)


@router.post("/practice-runs/{run_id}/interrupt", response_model=AttemptView)
def interrupt_practice(
    run_id: UUID,
    value: OperationRequest,
    actor: Writer,
    db: Db,
) -> AttemptView:
    result = PracticeAssessmentService(db).interrupt(actor, run_id, value.operation_key, _now())
    if result.state == "expired":
        raise AssessmentExpired()
    return result


@router.post(
    "/practice-runs/{run_id}/result",
    response_model=ResultSaveView,
    status_code=201,
)
def submit_practice(
    run_id: UUID,
    value: ResultSubmit,
    actor: Writer,
    db: Db,
) -> ResultSaveView:
    return PracticeAssessmentService(db).submit(actor, run_id, value, _now())


@router.get("/history", response_model=CursorPage[HistoryItem])
def history(
    mode: Mode,
    actor: Actor,
    db: Db,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> CursorPage[HistoryItem]:
    return AssessmentQueries(db, actor, _now()).history(mode, cursor, limit)


@router.get("/reports", response_model=CursorPage[AssessmentReportSummary])
def reports(
    actor: Actor,
    db: Db,
    suite_id: SuiteId | None = None,
    person_id: UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> CursorPage[AssessmentReportSummary]:
    filters = ReportFilters.model_validate(
        {
            "suite_id": suite_id,
            "person_id": person_id,
            "date_from": date_from,
            "date_to": date_to,
        }
    )
    return AssessmentQueries(db, actor, _now()).reports(filters, cursor, limit)


@router.get("/reports/{report_id}", response_model=AssessmentReportView)
def report(report_id: UUID, actor: Actor, db: Db) -> AssessmentReportView:
    return AssessmentQueries(db, actor, _now()).report(report_id)
