"""Every validation problem is a 422 with a list of field errors."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class FieldError(Exception):
    """A domain validation problem on one field, e.g. a rego already taken."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class NotFound(Exception):
    def __init__(self, message: str = "not found"):
        self.message = message
        super().__init__(message)


class Conflict(Exception):
    """Well formed, but not possible as things stand: a run that has left, a
    truck over its limit. `code` is for programs, `message` for people."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class Unauthorised(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class Forbidden(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def violated_constraint(exc: Exception) -> str | None:
    """The name of the database constraint an IntegrityError broke, if known."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def field_errors(errors: list[dict]) -> list[dict]:
    out = []
    for e in errors:
        loc = [str(x) for x in e.get("loc", []) if x not in ("body",)]
        out.append({"field": ".".join(loc) or "body", "message": e.get("msg", "invalid")})
    return out


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"errors": field_errors(exc.errors())})

    @app.exception_handler(FieldError)
    async def _field(request: Request, exc: FieldError):
        return JSONResponse(
            status_code=422, content={"errors": [{"field": exc.field, "message": exc.message}]}
        )

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": exc.message})

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": exc.message, "code": exc.code})

    @app.exception_handler(Unauthorised)
    async def _unauthorised(request: Request, exc: Unauthorised):
        return JSONResponse(status_code=401, content={"detail": exc.message},
                            headers={"WWW-Authenticate": "Bearer"})

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return JSONResponse(status_code=403, content={"detail": exc.message})
