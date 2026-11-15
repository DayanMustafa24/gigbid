"use client";

import Link from "next/link";
import { useState } from "react";
import { StatusBadge } from "@/components/Badges";
import { Async, Empty } from "@/components/States";
import { titleCase, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api, qs } from "@/lib/http";
import type { Run } from "@/lib/types";

const STATUSES = ["", "running", "awaiting_approval", "completed", "skipped", "closed", "needs_attention", "failed"];

export default function Runs() {
  const [status, setStatus] = useState("");
  const [kind, setKind] = useState("");
  const state = useAsync((s) => api.get<Run[]>(`/runs${qs({ status, kind, limit: 100 })}`, s), [status, kind], { every: 10000 });
  return (
    <div className="stack">
      <h1>Agent runs</h1>
      <p className="muted">Every run is recorded step by step, so you can see why the agents decided what they did.</p>
      <div className="row">
        <div><label htmlFor="st">Status</label><select id="st" value={status} onChange={(e) => setStatus(e.target.value)}>{STATUSES.map((s) => <option key={s} value={s}>{s ? titleCase(s) : "All"}</option>)}</select></div>
        <div><label htmlFor="kd">Kind</label><select id="kd" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All</option><option value="bid">Bid</option><option value="conversation">Conversation</option></select></div>
      </div>
      <Async state={state} what="Loading runs">
        {(runs) =>
          runs.length === 0 ? (
            <Empty title="No runs match">Start one from the inbox.</Empty>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table aria-label="Runs">
                <thead><tr><th>Subject</th><th>Kind</th><th>Status</th><th>Decision</th><th>Updated</th><th /></tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id}>
                      <td>{r.summary.job?.title ?? r.subject_id}</td>
                      <td>{titleCase(r.kind)}</td>
                      <td><StatusBadge value={r.status} /></td>
                      <td>{r.decision ? titleCase(r.decision) : "–"}</td>
                      <td>{when(r.updated_at)}</td>
                      <td>{r.status === "awaiting_approval" ? <Link href={`/studio/${r.id}`}>Review</Link> : null} <Link href={`/runs/${r.id}`}>Details</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
      </Async>
    </div>
  );
}
