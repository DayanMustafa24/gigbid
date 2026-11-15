"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { MessageApproval, ProposalApproval } from "@/components/ApprovalPanel";
import { StatusBadge } from "@/components/Badges";
import { RunTimeline } from "@/components/RunTimeline";
import { Async } from "@/components/States";
import { useAsync, useRunStream } from "@/lib/hooks";
import { api, toApiError } from "@/lib/http";
import type { Run } from "@/lib/types";

function Outcome({ run, onRetry }: { run: Run; onRetry: (r: Run) => void }) {
  const s = run.summary;
  const [err, setErr] = useState<string | null>(null);
  async function retry() {
    setErr(null);
    try {
      onRetry(await api.post<Run>(`/runs/${run.id}/retry`));
    } catch (e) {
      setErr(toApiError(e).message);
    }
  }
  return (
    <section className="card stack" aria-label="Outcome">
      <h2>Outcome</h2>
      {s.submission?.status ? (
        <p className="alert ok" data-testid="outcome">
          {s.job?.manual ? "Approved. Copy the proposal into the marketplace by hand." : `Submitted (${s.submission.status}). Connects spent: ${s.submission.connects_spent ?? "?"}. Balance: ${s.submission.balance_after ?? "?"}.`}
        </p>
      ) : null}
      {s.sent_message ? <p className="alert ok" data-testid="outcome">Message sent.</p> : null}
      {s.approval_status === "rejected" ? <p data-testid="outcome">You rejected this proposal. Nothing was sent.</p> : null}
      {run.status === "skipped" || run.status === "closed" ? <p data-testid="outcome">{s.reasons?.join(" ") || "Skipped by the agent: not worth bidding."}</p> : null}
      {s.dead_letter ? <p className="alert" role="alert">Stopped: {s.dead_letter.reason}</p> : null}
      {run.error ? <p className="alert" role="alert">{run.error}</p> : null}
      {s.submission?.error ? <p className="alert" role="alert">Submission failed: {s.submission.error}{s.submission.reason ? ` (${s.submission.reason})` : ""}</p> : null}
      {err ? <p className="alert" role="alert">{err}</p> : null}
      {run.status === "failed" || run.status === "needs_attention" ? <button onClick={() => void retry()}>Retry this run</button> : null}
    </section>
  );
}

function Live({ runId }: { runId: string }) {
  const st = useRunStream(runId);
  return <RunTimeline events={st.events} live={st.live} done={st.done} />;
}

export default function Studio() {
  const { id } = useParams<{ id: string }>();
  const [override, setOverride] = useState<Run | null>(null);
  const state = useAsync((s) => api.get<Run>(`/runs/${id}`, s), [id]);
  const run = override ?? state.data;
  // poll while the graph is working (the SSE timeline is for detail; this picks up the pending approval)
  const polling = useAsync((s) => api.get<Run>(`/runs/${id}`, s), [id], { every: 1500, skip: !run || run.status !== "running" });
  const current = polling.data && (!run || polling.data.updated_at >= run.updated_at) ? polling.data : run;

  return (
    <div className="stack">
      <p><Link href="/inbox">← Inbox</Link></p>
      <Async state={state} what="Loading the run">
        {() => {
          const r = current!;
          const p = r.pending;
          const title = r.kind === "conversation" ? "Client reply" : r.summary.job?.title ?? r.subject_id;
          return (
            <>
              <div className="row between"><h1>{title}</h1><StatusBadge value={r.status} /></div>
              {r.status === "running" ? <section className="card" aria-label="Progress"><h2>Working…</h2><Live runId={r.id} /></section> : null}
              {r.status === "awaiting_approval" && p?.kind === "proposal_approval" ? <ProposalApproval run={r} p={p} onDone={setOverride} /> : null}
              {r.status === "awaiting_approval" && p?.kind === "message_approval" ? <MessageApproval run={r} p={p} onDone={setOverride} /> : null}
              {r.status === "awaiting_approval" && p?.kind === "unknown" ? <p className="alert" role="alert">This approval request has an unknown format. Open the run details.</p> : null}
              {r.status !== "running" && r.status !== "awaiting_approval" ? <Outcome run={r} onRetry={setOverride} /> : null}
              {r.status === "running" && override?.status === "running" ? <p className="muted" role="status">Decision recorded. Finishing…</p> : null}
              <p><Link href={`/runs/${r.id}`}>Open run details and trace</Link></p>
            </>
          );
        }}
      </Async>
    </div>
  );
}
