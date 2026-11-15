"use client";

import Link from "next/link";
import { useState } from "react";
import { Async, Empty } from "@/components/States";
import { money, titleCase, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api, toApiError } from "@/lib/http";
import type { LedgerEntry, MarketProposal, Run } from "@/lib/types";

type Row = LedgerEntry & { live: MarketProposal | null };
const COLUMNS = [
  ["submitted", "Submitted"], ["viewed", "Viewed"], ["replied", "Replied"], ["interview", "Interview"], ["hired", "Hired"], ["lost", "Lost"],
] as const;
const bucket = (s: string) => (s === "ignored" || s === "closed" || s === "rejected" || s === "declined" ? "lost" : s);

async function load(signal: AbortSignal): Promise<{ rows: Row[]; drafts: Run[] }> {
  const [ledger, drafts] = await Promise.all([api.get<LedgerEntry[]>("/proposals", signal), api.get<Run[]>("/approvals?kind=bid", signal)]);
  const live = await Promise.allSettled(ledger.map((l) => api.get<MarketProposal>(`/proposals/${encodeURIComponent(l.proposal_id)}`, signal)));
  return { rows: ledger.map((l, i) => ({ ...l, live: live[i]?.status === "fulfilled" ? (live[i] as PromiseFulfilledResult<MarketProposal>).value : null })), drafts };
}

export default function Pipeline() {
  const state = useAsync(load, [], { every: 15000 });
  const [msg, setMsg] = useState<string | null>(null);
  async function sync() {
    setMsg(null);
    try {
      const r = await api.post<Record<string, unknown>>("/proposals/sync");
      setMsg(`Synced: ${JSON.stringify(r)}`);
      state.reload();
    } catch (e) {
      setMsg(toApiError(e).message);
    }
  }
  return (
    <div className="stack">
      <div className="row between">
        <div><h1>Pipeline</h1><p className="muted">Every proposal GigBid submitted for you, with its live marketplace status.</p></div>
        <button onClick={() => void sync()}>Sync outcomes</button>
      </div>
      {msg ? <div className="alert info" role="status">{msg}</div> : null}
      <Async state={state} what="Loading the pipeline">
        {({ rows, drafts }) =>
          rows.length === 0 && drafts.length === 0 ? (
            <Empty title="Nothing in the pipeline yet">Analyse a job in the <Link href="/inbox">inbox</Link>; approved proposals appear here.</Empty>
          ) : (
            <div className="kanban" aria-label="Pipeline board">
              <section className="col" aria-label="Awaiting approval">
                <h3>Awaiting approval <span className="badge warn">{drafts.length}</span></h3>
                {drafts.map((r) => (
                  <article key={r.id} className="card small"><Link href={`/studio/${r.id}`}>{r.summary.job?.title ?? r.subject_id}</Link><div className="muted">{when(r.updated_at)}</div></article>
                ))}
              </section>
              {COLUMNS.map(([key, label]) => {
                const items = rows.filter((r) => bucket(r.live?.status ?? r.submission.status ?? "submitted") === key);
                return (
                  <section key={key} className="col" aria-label={label}>
                    <h3>{label} <span className="badge">{items.length}</span></h3>
                    {items.map((r) => (
                      <article key={r.proposal_id} className="card small stack">
                        <strong>{r.title ?? r.job_id}</strong>
                        <div className="muted">{r.live ? `${money(r.live.bid_amount)} ${r.live.bid_type}` : "status unavailable"} · {when(r.submitted_at)}</div>
                        {r.strategy ? <span className="chip">{r.strategy}</span> : null}
                        <Link href={`/messages?proposal=${encodeURIComponent(r.proposal_id)}`}>Messages</Link>
                        {r.live ? <span className="muted">{titleCase(r.live.status)}</span> : null}
                      </article>
                    ))}
                  </section>
                );
              })}
            </div>
          )
        }
      </Async>
      <p className="small muted">The marketplace does not emit a “viewed” event yet, so that column stays empty until it does; “Lost” collects closed, ignored and declined proposals.</p>
    </div>
  );
}
