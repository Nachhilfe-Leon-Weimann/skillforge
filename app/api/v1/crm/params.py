"""Path and query vocabulary of the CRM routes; endpoint signatures use these aliases only."""

import uuid
from typing import Annotated

from fastapi import Path

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
