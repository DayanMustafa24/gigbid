"""Health (liveness + readiness with the degraded-mode flags the UI banner reads), personas, per-user settings."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select, text

from agent.policy.engine import load_policy
from agent.runtime import list_personas, persona_dir, persona_visible

from ..container import Container
from ..db import User, UserSetting
from ..deps import current_user, get_container, get_service
from ..errors import ApiError
from ..schemas import PersonaOut, SettingsIn
from ..service import RunService

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness only (container orchestrators): no I/O."""
    return {"status": "ok", "service": "gigbid-api"}


@router.get("/health/ready")
async def ready(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Readiness + what is degraded. Public, no secrets: names and booleans only."""
    db_ok = True
    try:
        async with c.sessions() as db:
            await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    market = await c.marketplace_status()
    parts = {"database": db_ok, "checkpointer": c.checkpointer is not None, "marketplace": market,
             "models": c.llm is not None, "injection_classifier": c.classifier is not None}
    degraded = [k for k, v in parts.items() if v in (False, "down", "unconfigured")]
    return {"status": "ok" if not degraded else "degraded", "degraded": degraded, "components": parts, "policy_version": load_policy().version,
            "mode": c.settings.marketplace_mode or c.cfg["marketplace"]["mode"], "checkpointer_kind": c.cp_kind or c.cfg["checkpointer"]["kind"]}


@router.get("/personas", response_model=list[PersonaOut])
async def personas(user: User = Depends(current_user), c: Container = Depends(get_container)) -> list[PersonaOut]:
    out = []
    for p in list_personas(c.cfg):
        if not persona_visible(p, user.id):
            continue
        f = persona_dir(p, c.cfg) / "profile.json"
        prof = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
        out.append(PersonaOut(id=p, display_name=prof.get("display_name"), headline=prof.get("headline"), hourly_rate_usd=prof.get("hourly_rate_usd"),
                              minimum_project_budget_usd=prof.get("minimum_project_budget_usd")))
    return out


@router.get("/settings")
async def get_settings(user: User = Depends(current_user), c: Container = Depends(get_container)) -> dict[str, Any]:
    async with c.sessions() as db:
        row = (await db.execute(select(UserSetting).where(UserSetting.owner_id == user.id))).scalar_one_or_none()
    return {"default_persona": None, "scout_queries": [], **(row.data if row else {})}


@router.put("/settings")
async def put_settings(body: SettingsIn, user: User = Depends(current_user), svc: RunService = Depends(get_service),
                       c: Container = Depends(get_container)) -> dict[str, Any]:
    if body.default_persona and not svc.persona_ok(body.default_persona, user.id):
        raise ApiError(422, "unknown_persona", f"Unknown persona {body.default_persona!r}")
    data = {"default_persona": body.default_persona, "scout_queries": [q.strip() for q in body.scout_queries if q.strip()]}
    extras = {"hourly_rate_usd": body.hourly_rate_usd, "min_project_budget_usd": body.min_project_budget_usd,
              "weekly_connects_budget": body.weekly_connects_budget}
    data.update({k: v for k, v in extras.items() if v is not None})
    async with c.sessions() as db:
        row = (await db.execute(select(UserSetting).where(UserSetting.owner_id == user.id))).scalar_one_or_none()
        if row is None:
            db.add(UserSetting(owner_id=user.id, data=data))
        else:
            row.data = data
        await db.commit()
    return data
