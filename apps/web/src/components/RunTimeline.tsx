"use client";

import type { TraceEvent } from "@/lib/types";
import { titleCase } from "@/lib/format";

const SKIP = new Set(["node", "ms", "mode"]);

function notes(data: Record<string, unknown>): string {
  return Object.entries(data)
    .filter(([k, v]) => !SKIP.has(k) && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") || "none" : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

/** Execution trace of a run: one row per node with its mode (llm / deterministic), model, latency and notes. */
export function RunTimeline({ events, live, done }: { events: TraceEvent[]; live?: boolean; done?: boolean }) {
  if (!events.length) return <p className="muted" role="status">{done ? "No trace recorded." : "Waiting for the first step…"}</p>;
  return (
    <div>
      <ol className="timeline" aria-label="Run trace">
        {events.map((e) => {
          const err = e.data?.mode === "error";
          return (
            <li key={e.seq}>
              <span className={`dot ${err ? "err" : ""}`} aria-hidden />
              <div>
                <strong>{titleCase(e.node)}</strong> {e.data?.mode ? <span className={`badge ${err ? "bad" : ""}`}>{String(e.data.mode)}</span> : null}
                <div className="small muted">{notes(e.data ?? {})}</div>
              </div>
              <span className="small muted">{typeof e.data?.ms === "number" ? `${Math.round(e.data.ms as number)} ms` : ""}</span>
            </li>
          );
        })}
      </ol>
      {live && !done ? <p className="small muted" role="status">Live…</p> : null}
    </div>
  );
}
