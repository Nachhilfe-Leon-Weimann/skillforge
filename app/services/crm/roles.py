"""The student and tutor roles of a person: idempotent singletons keyed by the person's party ID."""

import uuid
from collections.abc import Callable, Set

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Party, Person, PreferredMeetingTool, Student, StudentSubject, Tutor, TutorSubject

from .errors import PartyNotFoundError, PersonNotFoundError, RoleNotFoundError
from .parties import load_party, saved
from .subjects import require_subjects


async def put_student_role(
    session: AsyncSession,
    party_id: uuid.UUID,
    *,
    preferred_meeting_tool: PreferredMeetingTool,
    subject_ids: Set[int] = frozenset(),
) -> Party:
    """Give the person the student role or replace its data; ``subject_ids`` replaces the whole set."""
    person = await _load_person(session, party_id)
    await require_subjects(session, subject_ids)

    changed = apply_student_role(person, preferred_meeting_tool=preferred_meeting_tool, subject_ids=subject_ids)
    return await _finish(session, party_id, changed=changed)


async def put_tutor_role(session: AsyncSession, party_id: uuid.UUID, *, subject_ids: Set[int] = frozenset()) -> Party:
    """Give the person the tutor role or replace its data; ``subject_ids`` replaces the whole set."""
    person = await _load_person(session, party_id)
    await require_subjects(session, subject_ids)

    changed = apply_tutor_role(person, subject_ids=subject_ids)
    return await _finish(session, party_id, changed=changed)


async def remove_student_role(session: AsyncSession, party_id: uuid.UUID) -> Party:
    """Take the student role away. CRM rules only: relations and Discord state are not looked at (ADR 0007)."""
    person = await _load_person(session, party_id)
    if person.student is None:
        raise RoleNotFoundError(f"Person {party_id} is not a student")

    person.student = None
    return await _finish(session, party_id, changed=True)


async def remove_tutor_role(session: AsyncSession, party_id: uuid.UUID) -> Party:
    """Take the tutor role away. CRM rules only: relations and Discord state are not looked at (ADR 0007)."""
    person = await _load_person(session, party_id)
    if person.tutor is None:
        raise RoleNotFoundError(f"Person {party_id} is not a tutor")

    person.tutor = None
    return await _finish(session, party_id, changed=True)


def apply_student_role(person: Person, *, preferred_meeting_tool: PreferredMeetingTool, subject_ids: Set[int]) -> bool:
    """Set the role on a person whose roles are loaded (or who is new); return whether anything changed.

    The caller has checked the subjects and ends the write with ``saved`` and ``load_party``.
    """
    student = person.student
    if student is None:
        person.student = Student(
            preferred_meeting_tool=preferred_meeting_tool,
            student_subjects=[StudentSubject(subject_id=subject_id) for subject_id in sorted(subject_ids)],
        )
        return True

    changed = _sync_subjects(
        student.student_subjects, subject_ids, lambda subject_id: StudentSubject(subject_id=subject_id)
    )
    if student.preferred_meeting_tool != preferred_meeting_tool:
        student.preferred_meeting_tool = preferred_meeting_tool
        changed = True
    return changed


def apply_tutor_role(person: Person, *, subject_ids: Set[int]) -> bool:
    """Set the role on a person whose roles are loaded (or who is new); return whether anything changed."""
    tutor = person.tutor
    if tutor is None:
        person.tutor = Tutor(tutor_subjects=[TutorSubject(subject_id=subject_id) for subject_id in sorted(subject_ids)])
        return True

    return _sync_subjects(tutor.tutor_subjects, subject_ids, lambda subject_id: TutorSubject(subject_id=subject_id))


def _sync_subjects[Link: (StudentSubject, TutorSubject)](
    links: list[Link], subject_ids: Set[int], new_link: Callable[[int], Link]
) -> bool:
    """Add the missing rows and delete the surplus ones.

    Reassigning the collection would delete and re-insert the unchanged rows under the same
    composite key.
    """
    current = {link.subject_id: link for link in links}
    surplus = current.keys() - subject_ids
    missing = subject_ids - current.keys()
    for subject_id in surplus:
        links.remove(current[subject_id])
    for subject_id in sorted(missing):
        links.append(new_link(subject_id))
    return bool(surplus or missing)


async def _load_person(session: AsyncSession, party_id: uuid.UUID) -> Person:
    """Load the person through the one loading path; a company's ID does not exist from here."""
    try:
        party = await load_party(session, party_id)
    except PartyNotFoundError:
        raise PersonNotFoundError(f"No person with party id {party_id}") from None
    if party.person is None:
        raise PersonNotFoundError(f"No person with party id {party_id}")

    return party.person


async def _finish(session: AsyncSession, party_id: uuid.UUID, *, changed: bool) -> Party:
    # A PUT that changes nothing is not a write: updated_at stays, and the answer repeats exactly.
    if changed:
        await saved(session, party_id)
    return await load_party(session, party_id)
