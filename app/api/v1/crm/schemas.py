"""Read and write models of the CRM API.

Read models resolve (titles, display names) and are built by explicit ``from_model`` mappers;
write models take IDs and raw values. Update models use Pydantic's experimental ``MISSING``
sentinel: a field is optional but not nullable, and an unset field is absent from ``model_dump()``.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal, Self, assert_never

from pydantic import AfterValidator, Field, StringConstraints, ValidationInfo, field_validator
from pydantic.experimental.missing_sentinel import MISSING

from app.api.v1.common import ApiModel
from app.core.db.models import (
    ContactInfo,
    ContactInfoType,
    Party,
    PartyType,
    Person,
    PreferredMeetingTool,
    Student,
    Subject,
    Tutor,
)
from app.services.crm.inputs import NewContactInfo, normalize_contact_value

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


# --- Contact infos ---


class ContactInfoResponse(ApiModel):
    """One way to reach a party."""

    id: uuid.UUID
    """ID of the contact info."""
    type: ContactInfoType
    """Kind of the contact info; it never changes."""
    value: str
    """The normalized value: an e-mail address in lowercase, a phone number without whitespace."""
    label: str | None
    """Free-text note telling contact infos of the same type apart, e.g. `work`."""

    @classmethod
    def from_model(cls, contact_info: ContactInfo) -> Self:
        return cls(id=contact_info.id, type=contact_info.type, value=contact_info.value, label=contact_info.label)


class ContactInfoCreateRequest(ApiModel):
    """A contact info to attach to a party."""

    type: ContactInfoType = Field(examples=[ContactInfoType.EMAIL])
    """Kind of the contact info; it cannot be changed later."""
    value: str = Field(examples=["max.mustermann@example.com"])
    """An e-mail address (stored in lowercase) or a phone number (stored without whitespace), matching `type`."""
    label: Name | None = Field(None, examples=["private"])
    """Free-text note telling contact infos of the same type apart, e.g. `work`."""

    @field_validator("value")
    @classmethod
    def _normalize_value(cls, value: str, info: ValidationInfo) -> str:
        # `type` is declared first, so it is validated first; if it was rejected there is nothing to check against.
        type = info.data.get("type")
        return normalize_contact_value(type, value) if type is not None else value

    def to_input(self) -> NewContactInfo:
        return NewContactInfo(type=self.type, value=self.value, label=self.label)


def _reject_duplicate_contact_infos(contact_infos: list[ContactInfoCreateRequest]) -> list[ContactInfoCreateRequest]:
    keys = [(contact_info.type, contact_info.value) for contact_info in contact_infos]
    if len(set(keys)) != len(keys):
        raise ValueError("The same type and value must not appear twice")
    return contact_infos


NewContactInfos = Annotated[list[ContactInfoCreateRequest], AfterValidator(_reject_duplicate_contact_infos)]


# --- Parties: the read side ---


class StudentRole(ApiModel):
    """The student role of a person."""

    preferred_meeting_tool: PreferredMeetingTool
    """How the student prefers to meet their tutor."""
    subjects: list[SubjectResponse]
    """The subjects the student takes lessons in, ordered by title."""

    @classmethod
    def from_model(cls, student: Student) -> Self:
        return cls(
            preferred_meeting_tool=student.preferred_meeting_tool,
            subjects=_subjects(link.subject for link in student.student_subjects),
        )


class TutorRole(ApiModel):
    """The tutor role of a person."""

    subjects: list[SubjectResponse]
    """The subjects the tutor teaches, ordered by title."""

    @classmethod
    def from_model(cls, tutor: Tutor) -> Self:
        return cls(subjects=_subjects(link.subject for link in tutor.tutor_subjects))


class PersonDetail(ApiModel):
    """A party of type `person` with everything that belongs to it."""

    type: Literal[PartyType.PERSON]
    """Discriminator: always `person`."""
    id: uuid.UUID
    """ID of the party."""
    display_name: str
    """First and last name."""
    firstname: str
    """First name of the person."""
    lastname: str
    """Last name of the person."""
    student: StudentRole | None
    """The student role, or `null` if the person is not a student."""
    tutor: TutorRole | None
    """The tutor role, or `null` if the person is not a tutor."""
    contact_infos: list[ContactInfoResponse]
    """Contact infos of the party, ordered by type and value."""
    created_at: datetime
    """When the party was created."""
    updated_at: datetime
    """When anything in the party - the person, a role, a contact info, a relation - last changed."""

    @classmethod
    def from_model(cls, party: Party) -> Self:
        person = party.person
        assert person is not None, "a party of type person has a person row"
        return cls(
            type=PartyType.PERSON,
            id=party.id,
            display_name=_person_display_name(person),
            firstname=person.firstname,
            lastname=person.lastname,
            student=StudentRole.from_model(person.student) if person.student is not None else None,
            tutor=TutorRole.from_model(person.tutor) if person.tutor is not None else None,
            contact_infos=_contact_infos(party),
            created_at=party.created_at,
            updated_at=party.updated_at,
        )


class CompanyDetail(ApiModel):
    """A party of type `company` with everything that belongs to it."""

    type: Literal[PartyType.COMPANY]
    """Discriminator: always `company`."""
    id: uuid.UUID
    """ID of the party."""
    display_name: str
    """Name of the company."""
    name: str
    """Name of the company."""
    contact_infos: list[ContactInfoResponse]
    """Contact infos of the party, ordered by type and value."""
    created_at: datetime
    """When the party was created."""
    updated_at: datetime
    """When anything in the party - the company, a contact info, a relation - last changed."""

    @classmethod
    def from_model(cls, party: Party) -> Self:
        company = party.company
        assert company is not None, "a party of type company has a company row"
        return cls(
            type=PartyType.COMPANY,
            id=party.id,
            display_name=company.name,
            name=company.name,
            contact_infos=_contact_infos(party),
            created_at=party.created_at,
            updated_at=party.updated_at,
        )


# A PEP 695 alias becomes the named `PartyDetail` schema (`oneOf` + discriminator). The generated
# client ignores the discriminator and tries the variants in order, which works because the two
# members have different required fields - keep it that way.
type PartyDetail = Annotated[PersonDetail | CompanyDetail, Field(discriminator="type")]


def party_detail(party: Party) -> PersonDetail | CompanyDetail:
    """Map a party loaded through ``PARTY_GRAPH`` to the detail of its type."""
    match party.type:
        case PartyType.PERSON:
            return PersonDetail.from_model(party)
        case PartyType.COMPANY:
            return CompanyDetail.from_model(party)
        case _:
            assert_never(party.type)


def _person_display_name(person: Person) -> str:
    return f"{person.firstname} {person.lastname}"


def _contact_infos(party: Party) -> list[ContactInfoResponse]:
    ordered = sorted(party.contact_infos, key=lambda contact_info: (contact_info.type.value, contact_info.value))
    return [ContactInfoResponse.from_model(contact_info) for contact_info in ordered]


def _subjects(subjects: Iterable[Subject]) -> list[SubjectResponse]:
    ordered = sorted(subjects, key=lambda subject: (subject.title.lower(), subject.id))
    return [SubjectResponse.from_model(subject) for subject in ordered]


# --- Persons and companies: the write side ---


class PersonCreateRequest(ApiModel):
    """Body of `POST /persons`."""

    firstname: Name = Field(examples=["Max"])
    """First name. Surrounding whitespace is stripped."""
    lastname: Name = Field(examples=["Mustermann"])
    """Last name. Surrounding whitespace is stripped."""
    contact_infos: NewContactInfos = Field(
        default_factory=list,
        examples=[[{"type": "email", "value": "max.mustermann@example.com", "label": "private"}]],
    )
    """Contact infos to create with the person. The same `type` and `value` must not appear twice."""

    def contact_info_inputs(self) -> list[NewContactInfo]:
        return [contact_info.to_input() for contact_info in self.contact_infos]


class PersonUpdateRequest(ApiModel):
    """Body of `PATCH /persons/{party_id}`: only the fields that are sent change."""

    firstname: Name | MISSING = MISSING
    """New first name. Surrounding whitespace is stripped."""
    lastname: Name | MISSING = MISSING
    """New last name. Surrounding whitespace is stripped."""


class CompanyCreateRequest(ApiModel):
    """Body of `POST /companies`."""

    name: Name = Field(examples=["Musterfirma GmbH"])
    """Name of the company. Surrounding whitespace is stripped."""
    contact_infos: NewContactInfos = Field(
        default_factory=list,
        examples=[[{"type": "email", "value": "office@musterfirma.example", "label": "office"}]],
    )
    """Contact infos to create with the company. The same `type` and `value` must not appear twice."""

    def contact_info_inputs(self) -> list[NewContactInfo]:
        return [contact_info.to_input() for contact_info in self.contact_infos]


class CompanyUpdateRequest(ApiModel):
    """Body of `PATCH /companies/{party_id}`: only the fields that are sent change."""

    name: Name | MISSING = MISSING
    """New name of the company. Surrounding whitespace is stripped."""
