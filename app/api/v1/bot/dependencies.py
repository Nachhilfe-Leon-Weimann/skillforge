from typing import Annotated

from fastapi import Depends

from app.core.auth import Scope, require_scopes

BotRead = Annotated[object, Depends(require_scopes(Scope.BOT_READ))]
BotWrite = Annotated[object, Depends(require_scopes(Scope.BOT_WRITE))]
