"""Path and query vocabulary of the CRM routes; endpoint signatures use these aliases only."""

from typing import Annotated

from fastapi import Path

# core.subject.id is a Postgres INTEGER: a larger value could not be bound to the query.
MAX_SUBJECT_ID = 2**31 - 1

SubjectId = Annotated[
    int,
    Path(ge=1, le=MAX_SUBJECT_ID, description="ID of the subject.", examples=[1]),
]
