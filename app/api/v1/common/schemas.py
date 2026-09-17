from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base class for API schemas: a docstring under a field becomes its OpenAPI description."""

    model_config = ConfigDict(use_attribute_docstrings=True)


class FieldError(ApiModel):
    """One offending field of a request that failed validation."""

    loc: list[str | int]
    """Path to the field, starting with its location (`path`, `query`, `body`, ...)."""
    message: str
    """Human-readable reason the value was rejected."""
    type: str
    """Machine-readable identifier of the violated rule (e.g. `less_than_equal`)."""


class ErrorResponse(ApiModel):
    """Body of every non-2xx response (ADR 0006)."""

    detail: str
    """Human-readable message. Safe to show; never contains internals."""
    code: str
    """Stable, machine-readable identifier in `snake_case`. Branch on this, never on `detail`."""
    errors: list[FieldError] | None = None
    """Present only for request-validation failures: one entry per offending field."""
