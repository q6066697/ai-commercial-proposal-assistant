"""
FastAPI application:

    uvicorn api.main:create_app --factory --app-dir src --port 8001

A factory: importing this module reads no settings and opens no connections.
There is no authentication yet, so it must listen on localhost only.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, Optional

import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request as HttpRequest, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from api import service
from api.schemas import HealthOut, QuoteOut, RequestCreate, RequestOut
from db.price_lists import NoActivePriceList
from db.session import make_engine, make_session_factory
from settings import Settings, get_settings

logger = logging.getLogger("api")

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HEALTH_TIMEOUT_S = 5
# Raised while connecting (DB down, starting up, shutting down)
CONNECTION_ERRORS = (ConnectionError, TimeoutError, OperationalError,
                     asyncpg.PostgresConnectionError, asyncpg.CannotConnectNowError)


async def get_session(http: HttpRequest) -> AsyncIterator[AsyncSession]:
    async with http.app.state.session_factory() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


def create_app(settings: Optional[Settings] = None, engine: Optional[AsyncEngine] = None) -> FastAPI:
    settings = settings or get_settings()
    # Creating an engine doesn't connect: the API starts (and /health answers) without the database
    engine = engine or make_engine(settings, connect_args={"timeout": HEALTH_TIMEOUT_S})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await engine.dispose()

    app = FastAPI(title="AI Commercial Proposal Assistant: API-сервис", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    _add_error_handlers(app)
    _add_routes(app)
    return app


def _add_routes(app: FastAPI) -> None:
    quotes_dir = app.state.settings.quotes_dir

    @app.get("/health", response_model=HealthOut)
    async def health(response: Response) -> HealthOut:
        try:
            async with asyncio.timeout(HEALTH_TIMEOUT_S), app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as e:  # whatever the reason, the database is not usable
            _log_unavailable(e)
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return HealthOut(status="error", database="unavailable")
        return HealthOut(status="ok", database="ok")

    @app.post("/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
    async def create_request(body: RequestCreate, session: Session):
        return await service.create_request(session, body.text, body.client_name)

    @app.get("/requests/{request_id}", response_model=RequestOut)
    async def read_request(request_id: uuid.UUID, session: Session):
        return await service.get_request(session, request_id)

    @app.post("/requests/{request_id}/quote", response_model=QuoteOut, status_code=status.HTTP_201_CREATED,
              responses={200: {"model": QuoteOut, "description": "This quote already exists"}})
    async def create_quote(request_id: uuid.UUID, session: Session, response: Response) -> QuoteOut:
        quote, version, created = await service.create_quote(session, request_id, quotes_dir)
        if not created:
            response.status_code = status.HTTP_200_OK
        return QuoteOut(
            id=quote.id, request_id=quote.request_id, status=quote.status, total=quote.total,
            currency=quote.currency, price_list_version=version.version, breakdown=quote.breakdown,
            download_url=f"/quotes/{quote.id}/download", created_at=quote.created_at,
        )

    @app.get("/quotes/{quote_id}/download", response_class=FileResponse)
    async def download_quote(quote_id: uuid.UUID, session: Session) -> FileResponse:
        path = await service.get_quote_docx(session, quote_id, quotes_dir)
        return FileResponse(path, media_type=DOCX_MEDIA_TYPE, filename=f"KP_{str(quote_id)[:8].upper()}.docx")


def _error(code: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": message, **extra}, status_code=code)


def _log_unavailable(e: Exception) -> None:
    """The client gets only a 503; the reason is for the server log."""
    logger.warning("database unavailable: %s: %s", type(e).__name__, e)


def _add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(service.NotFound)
    async def not_found(_, e):
        return _error(404, str(e))

    @app.exception_handler(service.CannotQuote)
    async def cannot_quote(_, e):
        return _error(409, str(e))

    @app.exception_handler(service.ExtractionFailed)
    async def extraction_failed(_, e):
        return _error(422, str(e))

    @app.exception_handler(service.ServiceNotDetected)
    async def service_not_detected(_, e):
        return _error(422, str(e), clarify_fields=e.clarify_fields, warnings=e.warnings)

    @app.exception_handler(NoActivePriceList)
    async def no_price_list(_, e):
        return _error(503, str(e))

    @app.exception_handler(RequestValidationError)
    async def invalid_body(_, e):
        # Without the default "input" echo: it breaks on NaN and broken Unicode
        return _error(422, [{"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()])

    async def database_unavailable(_, e):
        _log_unavailable(e)
        return _error(503, "База данных недоступна.")

    for error in CONNECTION_ERRORS:
        app.add_exception_handler(error, database_unavailable)

    @app.exception_handler(DBAPIError)
    async def database_error(_, e):
        if e.connection_invalidated:
            return await database_unavailable(_, e)
        raise e
