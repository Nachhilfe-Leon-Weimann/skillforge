import uuid

import pytest

from app.core.db.models import (
    DiscordAccount,
    DiscordUser,
    MemberRole,
    Party,
    PartyRelation,
    PartyRelationType,
    PartyType,
    PermissionGrant,
    PermissionGrantEffect,
    PermissionSubjectType,
)
from app.services.bot import PrincipalNotFoundError, check_authorization

ACTOR_ID = 111111111111111111
ACTION = "student_stash"


@pytest.mark.db
async def test_authorizes_delegated_action_via_parent_of(session):
    _, child = await _seed(session, relation=PartyRelationType.PARENT_OF)
    assert await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=child)


@pytest.mark.db
async def test_authorizes_action_on_own_party(session):
    actor, _ = await _seed(session)
    assert await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=actor)


@pytest.mark.db
async def test_authorizes_via_pays_for(session):
    _, child = await _seed(session, relation=PartyRelationType.PAYS_FOR)
    assert await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=child)


@pytest.mark.db
async def test_tutor_of_does_not_delegate(session):
    _, child = await _seed(session, relation=PartyRelationType.TUTOR_OF)
    assert not await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=child)


@pytest.mark.db
async def test_denies_without_grant(session):
    _, child = await _seed(session, relation=PartyRelationType.PARENT_OF, grant=False)
    assert not await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=child)


@pytest.mark.db
async def test_denies_unrelated_target(session):
    await _seed(session, relation=PartyRelationType.PARENT_OF)
    other = Party(type=PartyType.PERSON)
    session.add(other)
    await session.flush()
    assert not await check_authorization(
        session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=other.id
    )


@pytest.mark.db
async def test_inactive_actor_denied(session):
    actor, _ = await _seed(session, active=False)
    assert not await check_authorization(session, actor_discord_id=ACTOR_ID, action_key=ACTION, target_party_id=actor)


@pytest.mark.db
async def test_unknown_actor_raises(session):
    with pytest.raises(PrincipalNotFoundError):
        await check_authorization(session, actor_discord_id=999, action_key=ACTION, target_party_id=uuid.uuid4())


# --- helpers ----------------------------------------------------------------


async def _seed(
    session,
    *,
    active: bool = True,
    grant: bool = True,
    relation: PartyRelationType | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    # Build the graph transient and flush once: assigning relationships between already-persistent
    # objects would lazy-load a backref, which is illegal under an async session.
    actor = Party(type=PartyType.PERSON)
    child = Party(type=PartyType.PERSON)
    objects: list[object] = [
        actor,
        child,
        DiscordAccount(discord_id=ACTOR_ID, party=actor, is_primary=True, active=True),
        DiscordUser(discord_id=ACTOR_ID, role=MemberRole.STUDENT, nick_name="Actor", active=active),
    ]
    if relation is not None:
        objects.append(PartyRelation(from_party=actor, to_party=child, type=relation))
    if grant:
        objects.append(
            PermissionGrant(
                subject_type=PermissionSubjectType.USER,
                subject_key=str(ACTOR_ID),
                action_key=ACTION,
                effect=PermissionGrantEffect.ALLOW,
            )
        )
    session.add_all(objects)
    await session.flush()
    return actor.id, child.id
