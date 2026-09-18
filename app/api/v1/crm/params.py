"""Path and query vocabulary of the CRM routes; endpoint signatures use these aliases only."""

import uuid
from typing import Annotated

from fastapi import Path, Query
from pydantic import AfterValidator, Field

from app.api.v1.common import PageParams
from app.core.db.models import PartyType
from app.services.crm.inputs import PartyRole, require_storable_text

PartyId = Annotated[
    uuid.UUID,
    Path(description="ID of the party.", examples=["7d9f4f3e-1c2b-4a5d-9e8f-0a1b2c3d4e5f"]),
]

# Where a created party can be read: the target of the `Location` header of `POST /persons` and `POST /companies`.
PARTY_LOCATION = "/api/v1/crm/parties/{party_id}"

# core.subject.id is a Postgres INTEGER: a larger value could not be bound to the query.
MAX_SUBJECT_ID = 2**31 - 1

SubjectId = Annotated[
    int,
    Path(ge=1, le=MAX_SUBJECT_ID, description="ID of the subject.", examples=[1]),
]


class PartyListParams(PageParams):
    """Filters of `GET /parties`. They never fail: a combination nothing matches is an empty page."""

    type: PartyType | None = Field(None, description="Only parties of this type.")
    role: PartyRole | None = Field(None, description="Only persons holding this role.")
    subject_id: int | None = Field(
        None,
        ge=1,
        le=MAX_SUBJECT_ID,
        description="Only persons having this subject - in the given `role`, or in any role without one.",
    )
    q: Annotated[str, AfterValidator(require_storable_text)] | None = Field(
        None,
        min_length=2,
        description=(
            "Search text, split on whitespace. Every word must appear, ignoring case, in the first name, the last "
            "name, the company name or a contact value. Searching for an e-mail address doubles as the duplicate "
            "check before creating a party."
        ),
    )


type PartyListQuery = Annotated[PartyListParams, Query()]
