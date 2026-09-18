"""Shared list vocabulary: offset-based paging with one parameter model and one envelope.

A list endpoint never returns a bare array. It takes ``PageQuery`` - or, with filters, the alias
of a ``PageParams`` subclass declared next to the endpoint - and returns ``Page[Item]``.
FastAPI supports at most one query-parameter model per endpoint: mixing a model with standalone
``Query()`` parameters registers fine but fails every request with a 422.
"""

from collections.abc import Sequence
from typing import Annotated, Self

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

from .schemas import ApiModel

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100
# The largest value Postgres takes as an INTEGER. Beyond int64 the driver cannot even bind the value,
# which would surface as a 500 instead of the validation 422.
MAX_PAGE_OFFSET = 2**31 - 1


class PageParams(BaseModel):
    """Query parameters of every list endpoint; subclass it to add filters."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT, description="Maximum number of items to return.")
    offset: int = Field(
        0, ge=0, le=MAX_PAGE_OFFSET, description="Number of items to skip before the first returned item."
    )


type PageQuery = Annotated[PageParams, Query()]


class Page[T](ApiModel):
    """One page of a list plus what a client needs to request the next one."""

    items: list[T]
    """The items of this page, in the order of the list."""
    total: int
    """Number of items matching the request across all pages."""
    limit: int
    """The page size that was applied."""
    offset: int
    """Number of items skipped before this page."""

    @classmethod
    def of(cls, items: Sequence[T], *, total: int, params: PageParams) -> Self:
        """Build the page for ``items`` from the ``params`` the request was made with."""
        return cls(items=list(items), total=total, limit=params.limit, offset=params.offset)
