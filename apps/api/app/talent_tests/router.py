from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.assessment_rankings.schemas import ItemRankingView
from app.auth.dependencies import _secret_bytes, require_standard_account
from app.auth.service import AuthContext
from app.core.database import SessionLocal, get_db
from app.talent_tests.access import TalentAccess
from app.talent_tests.errors import (
    TalentConflict,
    TalentForbidden,
    TalentNotFound,
    TalentTestError,
)
from app.talent_tests.item_ranking import TalentItemRankingService
from app.talent_tests.queries import TalentQueries
from app.talent_tests.ranking import report_ranking
from app.talent_tests.ranking_schemas import RankingView
from app.talent_tests.rate_limit import TalentRateLimited, TalentWriteLimiter
from app.talent_tests.schemas import (
    AbandonRequest,
    ContextView,
    Page,
    PersonDisplay,
    RawResultInput,
    ReportSummary,
    ReportView,
    ResultSaveView,
    SessionCreateRequest,
    SessionSummary,
    SessionView,
)
from app.talent_tests.service import TalentTestService

_PRIVATE_ERRORS = {
    400: ("invalid_request", "请求内容无效，请检查后重试"),
    404: ("talent_not_found", "未找到可访问的测评记录"),
    413: ("payload_too_large", "请求内容过大，请重新完成本项"),
    422: ("talent_invalid_data", "测试数据不符合当前规则，请重新完成本项"),
    500: ("internal_error", "服务暂时不可用，请稍后重试"),
}


def _private_error(status: int) -> JSONResponse:
    code, detail = _PRIVATE_ERRORS[status]
    return JSONResponse(
        {"status": status, "code": code, "detail": detail},
        status_code=status,
        headers={"Cache-Control": "no-store, private"},
    )


class TalentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def talent_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except TalentNotFound:
                return _private_error(404)
            except TalentForbidden:
                return JSONResponse(
                    {"code": "talent_forbidden", "detail": "无权进行此测评操作"}, status_code=403
                )
            except TalentRateLimited as exc:
                return JSONResponse(
                    {"code": "talent_rate_limited", "detail": "操作过于频繁，请稍后重试"},
                    status_code=429,
                    headers={"Retry-After": str(exc.retry_after)},
                )
            except TalentConflict as exc:
                body = {"code": exc.code, "detail": "本项目已保存，请刷新查看"}
                if exc.code == "talent_active_session":
                    body["detail"] = "已有进行中的测评，请继续或放弃本轮"
                    if exc.active_session_id is not None:
                        body["active_session_id"] = str(exc.active_session_id)
                elif exc.code == "talent_catalog_updated":
                    body["detail"] = "测试目录已更新，请刷新页面后开始新一轮"
                return JSONResponse(body, status_code=409)
            except (TalentTestError, ValidationError):
                return _private_error(422)

        return talent_handler


router = APIRouter(prefix="/talent-tests", tags=["talent-tests"], route_class=TalentRoute)
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[AuthContext, Depends(require_standard_account)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def get_talent_write_limiter(request: Request) -> TalentWriteLimiter:
    return TalentWriteLimiter(
        SessionLocal, _secret_bytes(request.app.state.settings, "session_token_pepper")
    )


def require_talent_writer(
    actor: Actor,
    db: Db,
    limiter: Annotated[TalentWriteLimiter, Depends(get_talent_write_limiter)],
) -> AuthContext:
    now = datetime.now(UTC)
    TalentAccess(db, actor, now).require_take()
    limiter.record(actor.account.id, now)
    return actor


Writer = Annotated[AuthContext, Depends(require_talent_writer)]


@router.get("/context")
def context(actor: Actor, db: Db) -> ContextView:
    return TalentQueries(db, actor, datetime.now(UTC)).context()


@router.post("/sessions", response_model=SessionView)
def start_session(
    value: SessionCreateRequest, actor: Writer, db: Db, response: Response
) -> SessionView:
    result, created = TalentTestService(db).start(
        actor=actor,
        request_id=value.request_id,
        catalog_version=value.catalog_version,
        now=datetime.now(UTC),
    )
    response.status_code = 201 if created else 200
    return result


@router.get("/sessions")
def sessions(
    actor: Actor, db: Db, page: PageNumber = 1, page_size: PageSize = 20
) -> Page[SessionSummary]:
    return TalentQueries(db, actor, datetime.now(UTC)).sessions(page, page_size)


@router.get("/sessions/{session_id}")
def session(session_id: UUID, actor: Actor, db: Db) -> SessionView:
    return TalentQueries(db, actor, datetime.now(UTC)).session(session_id)


@router.post("/sessions/{session_id}/results", response_model=ResultSaveView)
def save_result(
    session_id: UUID,
    value: Annotated[RawResultInput, Body()],
    actor: Writer,
    db: Db,
    response: Response,
) -> ResultSaveView:
    result, created = TalentTestService(db).submit(
        actor=actor, session_id=session_id, value=value, now=datetime.now(UTC)
    )
    response.status_code = 201 if created else 200
    return result


@router.post("/sessions/{session_id}/abandon")
def abandon(session_id: UUID, value: AbandonRequest, actor: Writer, db: Db) -> SessionView:
    return TalentTestService(db).abandon(
        actor=actor, session_id=session_id, request_id=value.request_id, now=datetime.now(UTC)
    )


@router.get("/subjects")
def subjects(
    actor: Actor,
    db: Db,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    name: Annotated[str | None, Query(max_length=80)] = None,
) -> Page[PersonDisplay]:
    return TalentQueries(db, actor, datetime.now(UTC)).subjects(name, page, page_size)


@router.get("/reports")
def reports(
    actor: Actor,
    db: Db,
    person_id: UUID | None = None,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> Page[ReportSummary]:
    return TalentQueries(db, actor, datetime.now(UTC)).reports(person_id, page, page_size)


@router.get("/reports/{report_id}")
def report(report_id: UUID, actor: Actor, db: Db) -> ReportView:
    return TalentQueries(db, actor, datetime.now(UTC)).report(report_id)


@router.get("/reports/{report_id}/ranking", response_model=RankingView)
def ranking(report_id: UUID, actor: Actor, db: Db) -> RankingView:
    return report_ranking(db, actor, report_id, datetime.now(UTC))


@router.get("/results/{result_id}/ranking", response_model=ItemRankingView)
def item_ranking(result_id: UUID, actor: Actor, db: Db) -> ItemRankingView:
    return TalentItemRankingService(db, TalentAccess(db, actor, datetime.now(UTC))).for_result(
        result_id
    )


class PrivateTalentResponseMiddleware:
    """Bound input before parsing; protect all responses, including unknown paths."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        prefix = "/api/v1/talent-tests"
        if scope["type"] != "http" or not (
            scope["path"] == prefix or scope["path"].startswith(prefix + "/")
        ):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            # At most limit + 1 retained bytes, even when one ASGI chunk is huge.
            body.extend(chunk[: max(0, 65537 - len(body))])
            if len(body) > 65536:
                await _private_error(413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False
        started = False
        replaced = False

        async def receive_body() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def send_private(message: Message) -> None:
            nonlocal started, replaced
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                if status in _PRIVATE_ERRORS:
                    replaced = True
                    await _private_error(status)(scope, receive_body, send)
                    return
                MutableHeaders(scope=message)["Cache-Control"] = "no-store, private"
            if not replaced:
                await send(message)

        try:
            await self.app(scope, receive_body, send_private)
        except Exception:
            if started:
                raise
            # Do not emit exception text or payload-bearing validation details.
            await _private_error(500)(scope, receive_body, send)
