from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from bluesky_feed_consumer.config import Settings, get_settings
from bluesky_feed_consumer.db import get_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def verify_api_key(
    x_api_key: Annotated[str, Header()],
    settings: SettingsDep,
) -> None:
    if x_api_key != settings.service_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


RequireAuth = Depends(verify_api_key)
