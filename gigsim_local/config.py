"""Simulator configuration (env-driven; tests pass SimConfig directly)."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class SimConfig:
    seed: int = 20261114
    dataset: str = "stream"                 # stream | dev | both | sample
    data_dir: Path = ROOT / "data" / "synthetic"
    dev_dir: Path = ROOT / "evals" / "datasets"
    persona: str | None = None              # restrict the feed to one persona's jobs
    initial_released: int = 300             # jobs visible at start; the rest arrive on /admin/tick
    release_per_tick: int = 25
    reply_delay_ticks: int = 1
    initial_connects: int = 200
    token: str = ""                         # empty = auth disabled
    rate_limit_per_min: int = 60
    fault_rate: float = 0.0                 # 5xx before the action runs
    fault_after_commit_rate: float = 0.0    # 5xx AFTER a write committed (response lost): tests idempotency
    client_injection_rate: float = 0.05     # share of client replies carrying a planted injection
    policy_update_enabled: bool = False
    policy_update_file: Path = Path(__file__).with_name("policy_update.yaml")
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    @classmethod
    def from_env(cls) -> SimConfig:
        e = os.environ
        return cls(
            seed=int(e.get("GIGSIM_SEED", cls.seed)),
            dataset=e.get("GIGSIM_DATASET", "stream"),
            persona=e.get("GIGSIM_PERSONA") or None,
            initial_released=int(e.get("GIGSIM_INITIAL_RELEASED", 300)),
            token=e.get("GIGSIM_API_TOKEN", ""),
            rate_limit_per_min=int(e.get("GIGSIM_RATE_LIMIT_PER_MIN", 60)),
            fault_rate=float(e.get("GIGSIM_FAULT_RATE", 0.02)),
            fault_after_commit_rate=float(e.get("GIGSIM_FAULT_AFTER_COMMIT_RATE", 0.01)),
            policy_update_enabled=_flag("GIGSIM_POLICY_UPDATE"),
        )
