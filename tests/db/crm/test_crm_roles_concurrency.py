"""Two overlapping PUTs of the same role - a client retrying - must both succeed.

This needs two real transactions, so it cannot run on the rolled-back ``session`` fixture: it
commits, and removes what it created.
"""

import asyncio

import pytest
from sqlalchemy import func, select

from app.core.db import Database
from app.core.db.models import PreferredMeetingTool, Student, Tutor
from app.services.crm import parties, persons, roles

pytestmark = pytest.mark.db


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_an_overlapping_put_of_the_same_role_waits_and_then_changes_nothing(db: Database, role: str):
    async with db.session() as setup:
        party_id = (await persons.create_person(setup, firstname="Race", lastname="Condition")).id

    async def put(session):
        if role == "student":
            return await roles.put_student_role(session, party_id, preferred_meeting_tool=PreferredMeetingTool.DISCORD)
        return await roles.put_tutor_role(session, party_id)

    first, second = db.session_factory(), db.session_factory()
    try:
        # The first request has written its role but not committed yet ...
        await put(first)
        # ... when the retry arrives. Without the row lock it would find no role either, insert the
        # same primary key and fail with an IntegrityError as soon as the first request commits.
        retry = asyncio.create_task(put(second))
        await asyncio.sleep(0.5)
        assert not retry.done(), "the retry waits for the first request"

        await first.commit()
        party = await asyncio.wait_for(retry, timeout=10)
        await second.commit()

        assert party.person is not None
        assert (party.person.student if role == "student" else party.person.tutor) is not None
        async with db.session(write=False) as check:
            model = Student if role == "student" else Tutor
            assert await check.scalar(select(func.count()).select_from(model).where(model.person_id == party_id)) == 1
    finally:
        await first.close()
        await second.close()
        async with db.session() as cleanup:
            await parties.delete_party(cleanup, party_id)
