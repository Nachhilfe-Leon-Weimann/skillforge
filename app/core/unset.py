"""The ``UNSET`` sentinel of the service layer, shared by every domain."""

from enum import Enum


class Unset(Enum):
    """Type of ``UNSET``: "leave this nullable field as it is", where ``None`` already means "clear it"."""

    UNSET = "unset"


UNSET = Unset.UNSET
