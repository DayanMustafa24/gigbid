"""Portfolio upload (user journey step 1): the signed-in user's own profile, case studies, repo READMEs and CV, as text.
The portfolio becomes a private persona `user-<id>` the agents may cite; every claim in a proposal is still verified against it."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends

from agent.runtime import user_persona_id

from .. import portfolio as store
from ..container import Container
from ..db import User
from ..deps import current_user, get_container
from ..errors import ApiError
from ..evidence import _corpus
from ..schemas import PortfolioFileIn, PortfolioOut, PortfolioProfileIn

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _out(user: User) -> PortfolioOut:
    base = store.base_dir(user.id)
    return PortfolioOut(persona=user_persona_id(user.id), profile=store.read_profile(base) if base.exists() else {},
                        files=store.list_files(base) if base.exists() else [], ready=store.is_ready(base) if base.exists() else False)


def _changed(user: User, c: Container) -> None:
    """New text must be indexed on the next run: drop the cached corpus and the per-persona Deps (retriever index)."""
    _corpus.cache_clear()
    c._deps_cache.pop(user_persona_id(user.id), None)


@router.get("", response_model=PortfolioOut)
async def get_portfolio(user: User = Depends(current_user)) -> PortfolioOut:
    return await asyncio.to_thread(_out, user)


@router.put("/profile", response_model=PortfolioOut)
async def put_profile(body: PortfolioProfileIn, user: User = Depends(current_user), c: Container = Depends(get_container)) -> PortfolioOut:
    await asyncio.to_thread(store.write_profile, store.base_dir(user.id), body.model_dump())
    _changed(user, c)
    return await asyncio.to_thread(_out, user)


@router.post("/files", response_model=PortfolioOut, status_code=201)
async def add_file(body: PortfolioFileIn, user: User = Depends(current_user), c: Container = Depends(get_container)) -> PortfolioOut:
    base = store.base_dir(user.id)
    if not (base / "profile.json").exists():
        raise ApiError(409, "profile_required", "Save your profile (name and skills) before adding files")
    try:
        await asyncio.to_thread(store.save_file, base, body.kind, body.name, body.text)
    except ValueError as e:
        raise ApiError(422, "portfolio_limit", str(e)) from e
    _changed(user, c)
    return await asyncio.to_thread(_out, user)


@router.delete("/files/{kind}/{name}", response_model=PortfolioOut)
async def delete_file(kind: str, name: str, user: User = Depends(current_user), c: Container = Depends(get_container)) -> Any:
    if kind not in ("case_study", "readme", "cv"):
        raise ApiError(404, "file_not_found", "File not found")
    if not await asyncio.to_thread(store.delete_file, store.base_dir(user.id), kind, name):
        raise ApiError(404, "file_not_found", "File not found")
    _changed(user, c)
    return await asyncio.to_thread(_out, user)
