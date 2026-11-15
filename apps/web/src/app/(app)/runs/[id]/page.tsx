"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/Badges";
import { RunTimeline } from "@/components/RunTimeline";
import { Async } from "@/components/States";
import { money, titleCase, when } from "@/lib/format";
import { useAsync, useRunStream } from "@/lib/hooks";
import { api } from "@/lib/http";
import type { Run } from "@/lib/types";

export default function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const run = useAsync((s) => api.get<Run>(`/runs/${id}`, s), [id], { every: 4000 });
  const stream = useRunStream(id);
  return (
    <div className="stack">
      <p><Link href="/runs">← Runs</Link></p>
      <Async state={run} what="Loading the run">
        {(r) => {
          const s = r.summary;
          return (
            <>
              <div className="row between"><h1>{s.job?.title ?? r.subject_id}</h1><StatusBadge value={r.status} /></div>
              <p className="muted">{titleCase(r.kind)} run · persona {r.persona} · started {when(r.created_at)} · updated {when(r.updated_at)}</p>
              {r.status === "awaiting_approval" ? <p><Link className="btn primary" href={`/studio/${r.id}`}>Review and decide</Link></p> : null}
              <div className="grid cols-3">
                <div className="card"><h3>Decision</h3><p>{s.decision ? titleCase(s.decision) : "–"}</p><p className="small muted">{s.reasons?.join(" ")}</p></div>
                <div className="card"><h3>Work</h3><p>{s.steps ?? "–"} steps · {s.revisions ?? 0} revision(s)</p><p className="small muted">Strategy: {s.strategy ?? "–"}</p></div>
                <div className="card"><h3>Cost</h3><p>{((s.usage?.tokens_in ?? 0) + (s.usage?.tokens_out ?? 0)).toLocaleString()} tokens</p><p className="small muted">{money(s.usage?.cost_usd ?? 0)} (open models are $0)</p></div>
              </div>
              {s.errors?.length ? <div className="alert" role="alert"><strong>Errors recorded:</strong><ul>{s.errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div> : null}
              {s.dead_letter ? <div className="alert" role="alert">Stopped: {s.dead_letter.reason}</div> : null}
              {r.error ? <div className="alert" role="alert">{r.error}</div> : null}
            </>
          );
        }}
      </Async>
      <section className="card" aria-label="Trace">
        <h2>Trace</h2>
        <RunTimeline events={stream.events} live={stream.live} done={stream.done} />
      </section>
    </div>
  );
}
