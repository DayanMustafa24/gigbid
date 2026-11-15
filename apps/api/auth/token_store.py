"""Encrypted OAuth token store (Fernet). Used by the live Upwork adapter (Phase 3.5) to keep the owner's grant at rest.

Only ciphertext reaches the database; the key comes from TOKEN_ENCRYPTION_KEY (required when APP_ENV=production; in development a
key is derived from APP_SECRET_KEY so nothing has to be configured). Rows are keyed by (owner_id, provider): tenant scoping by construction.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db import OAuthToken


class TokenStoreError(Exception):
    pass


def make_fernet(key: str | None, secret: str, env: str) -> Fernet:
    if key:
        try:
            return Fernet(key.encode())
        except ValueError as e:
            raise TokenStoreError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from e
    if env == "production":
        raise TokenStoreError("TOKEN_ENCRYPTION_KEY is required when APP_ENV=production")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(f"gigbid-dev|{secret}".encode()).digest()))


class TokenStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], fernet: Fernet) -> None:
        self.sessions, self.fernet = sessions, fernet

    async def put(self, owner_id: str, provider: str, token: dict[str, Any]) -> None:
        blob = self.fernet.encrypt(json.dumps(token).encode()).decode()
        async with self.sessions() as db:
            await db.merge(OAuthToken(owner_id=owner_id, provider=provider, ciphertext=blob))
            await db.commit()

    async def get(self, owner_id: str, provider: str) -> dict[str, Any] | None:
        async with self.sessions() as db:
            row = (await db.execute(select(OAuthToken).where(OAuthToken.owner_id == owner_id,
                                                            OAuthToken.provider == provider))).scalar_one_or_none()
        if row is None:
            return None
        try:
            return json.loads(self.fernet.decrypt(row.ciphertext.encode()))
        except InvalidToken as e:  # wrong key or tampered row: never return garbage, never log the blob
            raise TokenStoreError("stored token cannot be decrypted with the current key") from e

    async def delete(self, owner_id: str, provider: str) -> bool:
        async with self.sessions() as db:
            res = await db.execute(delete(OAuthToken).where(OAuthToken.owner_id == owner_id, OAuthToken.provider == provider))
            await db.commit()
            return (res.rowcount or 0) > 0
