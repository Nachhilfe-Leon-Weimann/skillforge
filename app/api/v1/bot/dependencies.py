from typing import Annotated

from app.core.auth import Principal, Scope, require_scopes

BotRead = Annotated[Principal, require_scopes(Scope.BOT_READ)]
BotWrite = Annotated[Principal, require_scopes(Scope.BOT_WRITE)]
