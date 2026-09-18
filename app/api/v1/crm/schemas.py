"""Read and write models of the CRM API.

Read models resolve (titles, display names) and are built by explicit ``from_model`` mappers;
write models take IDs and raw values. Update models use Pydantic's experimental ``MISSING``
sentinel: a field is optional but not nullable, and an unset field is absent from ``model_dump()``.
"""

from typing import Annotated, Self

from pydantic import Field, StringConstraints
from pydantic.experimental.missing_sentinel import MISSING

from app.api.v1.common import ApiModel
from app.core.db.models import Subject

# A plain assignment inlines the constraints at the field; a PEP 695 alias would become its own schema.
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SubjectResponse(ApiModel):
    """A subject students learn and tutors teach."""

    id: int
    """ID of the subject."""
    title: str
    """Title of the subject, unique regardless of case."""

    @classmethod
    def from_model(cls, subject: Subject) -> Self:
        return cls(id=subject.id, title=subject.title)


class SubjectCreateRequest(ApiModel):
    """Body of `POST /subjects`."""

    title: Name = Field(examples=["Mathematics"])
    """Title of the subject. Surrounding whitespace is stripped; must be unique regardless of case."""


class SubjectUpdateRequest(ApiModel):
    """Body of `PATCH /subjects/{subject_id}`: only the fields that are sent change."""

    title: Name | MISSING = MISSING
    """New title of the subject. Surrounding whitespace is stripped; must be unique regardless of case."""
