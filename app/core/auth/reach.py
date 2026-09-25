from app.core.db.models import PartyRelationType

# Relations that let a party act on behalf of another party (the relation's ``to_party``). One home,
# so that the bot's delegation check and the API's reach cannot drift apart (ADR 0008).
# Tutor authority runs through role/grants, not delegation, so ``TUTOR_OF`` is intentionally absent.
DELEGATION_RELATION_TYPES = (PartyRelationType.PARENT_OF, PartyRelationType.PAYS_FOR)
