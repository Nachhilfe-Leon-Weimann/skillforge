from pydantic import BaseModel


class FieldError(BaseModel):
    """One offending field of a request that failed validation."""

    loc: list[str | int]
    message: str
    type: str


class ErrorResponse(BaseModel):
    """Body of every non-2xx response (ADR 0006)."""

    detail: str
    code: str
    errors: list[FieldError] | None = None
