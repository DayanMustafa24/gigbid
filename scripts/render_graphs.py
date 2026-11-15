"""Render the LangGraph graphs FROM CODE (assignment Phase 5 gate: "graph PNG/mermaid rendered from code").

    uv run python -m scripts.render_graphs            # writes docs/images/{bid_graph,conversation_graph}.mmd
    uv run python -m scripts.render_graphs --png      # also .png if Mermaid CLI (`mmdc`) is on PATH

The diagram is derived from the compiled graph, so it cannot drift from the implementation. Commit the generated files.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from agent.graphs.bid_graph import build_bid_graph
from agent.graphs.conversation_graph import build_conversation_graph
from agent.nodes.common import Deps
from agent.settings import ROOT


def mermaid_for(graph) -> str:
    return graph.get_graph().draw_mermaid()


def graphs() -> dict[str, object]:
    deps = Deps()
    return {"bid_graph": build_bid_graph(deps), "conversation_graph": build_conversation_graph(deps)}


def render(out_dir: Path | None = None, png: bool = False) -> list[Path]:
    out = out_dir or ROOT / "docs" / "images"
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, g in graphs().items():
        mmd = out / f"{name}.mmd"
        mmd.write_text(mermaid_for(g), encoding="utf-8")
        written.append(mmd)
        if png and shutil.which("mmdc"):
            target = out / f"{name}.png"
            subprocess.run(["mmdc", "-i", str(mmd), "-o", str(target), "-b", "white"], check=True)
            written.append(target)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", action="store_true")
    a = ap.parse_args()
    for p in render(png=a.png):
        print(p)


if __name__ == "__main__":
    main()
