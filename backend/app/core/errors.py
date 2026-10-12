"""Structured error contract: every non-2xx response has the same JSON shape
{"error": {"code", "message", "details", "request_id"}}."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger("errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFound(AppError):
    status_code, code = 404, "not_found"


class BadRequest(AppError):
    status_code, code = 400, "bad_request"


class ProviderUnavailable(AppError):
    status_code, code = 503, "provider_unavailable"


class ProviderTimeout(AppError):
    status_code, code = 504, "provider_timeout"


class ProviderMisconfigured(AppError):
    status_code, code = 503, "provider_misconfigured"


class IndexNotReady(AppError):
    status_code, code = 503, "index_not_ready"


class DatabaseUnavailable(AppError):
    status_code, code = 503, "database_unavailable"


def _payload(request: Request, code: str, message: str, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        log.warning("app_error", code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(_payload(request, exc.code, exc.message, exc.details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        details = {"fields": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}
        return JSONResponse(_payload(request, "validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(_payload(request, f"http_{exc.status_code}", str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled_exception", path=request.url.path)
        return JSONResponse(_payload(request, "internal_error", "Unexpected server error"), status_code=500)
