# ADR 0001: One marketplace port; our own simulator stand-in

Status: accepted (2026-11-14)

Context: R1 requires one internal interface with GigSim and live Upwork adapters. The starter kit
did not ship `gigsim/`, personas or eval sets.

Decision: Graph code only sees `agent/marketplace_port.py`. Adapters live in
`mcp_servers/marketplace/adapters/`. We build `gigsim_local/` as a stand-in and keep `gigsim/`
free for the instructor's copy (CI rule: nothing under `gigsim/` changes).

Consequences: switching simulator/live is config only. If the instructor's API differs, only the
GigSim adapter changes. Stand-in results are labelled as such in every report.
