"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Async, Empty } from "@/components/States";
import { useApp } from "@/lib/app";
import { when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api, toApiError } from "@/lib/http";
import type { LedgerEntry, Run, ThreadMessage } from "@/lib/types";

function Thread({ proposalId }: { proposalId: string }) {
  const router = useRouter();
  const { persona } = useApp();
  const msgs = useAsync((s) => api.get<ThreadMessage[]>(`/proposals/${encodeURIComponent(proposalId)}/messages`, s), [proposalId], { every: 10000 });
  const [err, setErr] = useState<string | null>(null);
  async function suggest(text: string) {
    setErr(null);
    try {
      const run = await api.post<Run>("/runs", { kind: "conversation", persona, proposal_id: proposalId, client_message: text });
      router.push(`/studio/${run.id}`);
    } catch (e) {
      setErr(toApiError(e).message);
    }
  }
  return (
    <section className="card stack" aria-label="Conversation">
      <Async state={msgs} what="Loading messages">
        {(list) => {
          const lastClient = [...list].reverse().find((m) => m.sender === "client");
          const awaitingReply = list.length > 0 && list[list.length - 1]?.sender === "client";
          return (
            <>
              {list.length === 0 ? <p className="muted">No messages on this proposal yet.</p> : null}
              <div className="thread">
                {list.map((m) => (
                  <div key={m.message_id} className={`msg ${m.sender === "freelancer" ? "me" : ""}`}>
                    <div className="small muted">{m.sender === "client" ? "Client" : "You"} · {when(m.sent_at)}</div>
                    <div className="draft">{m.text}</div>
                  </div>
                ))}
              </div>
              {err ? <div className="alert" role="alert">{err}</div> : null}
              <div className="row">
                <button className="primary" disabled={!lastClient} onClick={() => lastClient && void suggest(lastClient.text)}>Suggest a reply</button>
                <span className="small muted">{awaitingReply ? "The client is waiting for you." : "You will review and approve before anything is sent."}</span>
              </div>
            </>
          );
        }}
      </Async>
    </section>
  );
}

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const ledger = useAsync((s) => api.get<LedgerEntry[]>("/proposals", s), []);
  const pending = useAsync((s) => api.get<Run[]>("/approvals?kind=conversation", s), [], { every: 8000 });
  const selected = params.get("proposal");
  return (
    <div className="stack">
      <h1>Messages</h1>
      {pending.data?.length ? (
        <section className="card flag" aria-label="Replies awaiting approval">
          <h2>Replies awaiting your approval ({pending.data.length})</h2>
          <ul>{pending.data.map((r) => <li key={r.id}><Link href={`/studio/${r.id}`}>{r.summary.conversation?.intent ? `Reply (${r.summary.conversation.intent})` : "Client reply"}</Link> <span className="muted small">{r.subject_id}</span></li>)}</ul>
        </section>
      ) : null}
      <Async state={ledger} what="Loading proposals">
        {(rows) =>
          rows.length === 0 ? (
            <Empty title="No submitted proposals yet">Client conversations appear here once a proposal is submitted.</Empty>
          ) : (
            <div className="grid split">
              <nav className="card stack" aria-label="Proposals">
                {rows.map((r) => (
                  <button key={r.proposal_id} aria-pressed={selected === r.proposal_id} className={selected === r.proposal_id ? "primary" : ""} style={{ textAlign: "left" }}
                          onClick={() => router.replace(`/messages?proposal=${encodeURIComponent(r.proposal_id)}`)}>
                    {r.title ?? r.job_id}
                  </button>
                ))}
              </nav>
              {selected ? <Thread proposalId={selected} /> : <Empty title="Pick a proposal" />}
            </div>
          )
        }
      </Async>
    </div>
  );
}

export default function Messages() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading…</p>}>
      <Inner />
    </Suspense>
  );
}
