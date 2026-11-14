"""The platform policy update as DATA (policy_update.yaml)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class PolicyUpdate:
    id: str
    effective: str
    provenance: str
    connects_surcharge: int = 0
    require_ai_disclosure: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


def load_policy_update(path: Path) -> PolicyUpdate:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return PolicyUpdate(
        id=raw["id"], effective=str(raw["effective"]), provenance=raw.get("provenance", ""),
        connects_surcharge=int(raw.get("connects_surcharge", 0)),
        require_ai_disclosure=bool(raw.get("require_ai_disclosure", False)),
        notes=tuple(raw.get("notes", [])),
    )
