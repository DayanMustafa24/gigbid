"""Arq worker (Redis). Run: `uv run arq worker.main.WorkerSettings` (docker compose service `worker`).

Three scheduled tasks, all idempotent and all safe to run twice: scout (start bid runs for saved queries; they stop at the human gate),
outcome sync (marketplace proposal status -> outcome memory), stale-run recovery. The worker owns its own Container (own DB engine,
checkpointer, marketplace) built at startup; nothing here can send or spend without a human decision made through the API.
Cadence comes from config.yaml `worker:`.
"""
from __future__ import annotations

import logging
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from agent.observability.context import setup_logging
from agent.settings import get_settings, load_config
from apps.api.container import Container
from apps.api.service import RunService

from .jobs import schedule, scout_all, sync_all

log = logging.getLogger("gigbid.worker")


async def startup(ctx: dict[str, Any]) -> None:
    if get_settings().app_env != "test":
        setup_logging()
    c = Container()
    await c.startup()
    ctx["c"], ctx["svc"] = c, RunService(c)
    log.info("worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    svc: RunService = ctx["svc"]
    await svc.drain()
    await ctx["c"].shutdown()


async def scout(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    res = await scout_all(ctx["c"], ctx["svc"])
    log.info("scout: %s", res)
    return res


async def sync_outcomes(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    res = await sync_all(ctx["c"], ctx["svc"])
    log.info("outcome sync: %s", res)
    return res


async def recover(ctx: dict[str, Any]) -> int:
    n = await ctx["svc"].recover_stale()
    if n:
        log.warning("marked %d stale run(s) failed", n)
    return n


def _redis() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


_W = load_config().get("worker", {})


class WorkerSettings:
    functions = [scout, sync_outcomes, recover]
    cron_jobs = [
        cron(scout, name="scout", **schedule(int(_W.get("scout_every_minutes", 240))), run_at_startup=False, unique=True),
        cron(sync_outcomes, name="sync_outcomes", **schedule(int(_W.get("sync_every_minutes", 60))), unique=True),
        cron(recover, name="recover", **schedule(int(_W.get("recover_every_minutes", 15))), run_at_startup=True, unique=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis()
    max_jobs = 2
    job_timeout = 600
    keep_result = 3600
