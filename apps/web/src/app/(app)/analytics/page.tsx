"use client";

import { Async, Empty } from "@/components/States";
import { money, titleCase } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api } from "@/lib/http";
import type { Analytics } from "@/lib/types";

const STEPS: [keyof Analytics["funnel"], string][] = [["analysed", "Analysed"], ["bid", "Worth bidding"], ["drafted", "Drafted"], ["approved", "Approved by you"], ["submitted", "Submitted"]];

function Kv({ data }: { data: Record<string, unknown> }) {
  const rows = Object.entries(data);
  if (!rows.length) return <p className="muted small">No data yet.</p>;
  return (
    <table>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><th scope="row">{titleCase(k)}</th><td className="mono small">{typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export default function AnalyticsPage() {
  const state = useAsync((s) => api.get<Analytics>("/analytics/summary", s), [], { every: 20000 });
  return (
    <div className="stack">
      <h1>Analytics</h1>
      <p className="muted">Computed from your own run history. Nothing here is estimated; empty means no data yet.</p>
      <Async state={state} what="Loading analytics">
        {(a) => {
          const max = Math.max(1, a.funnel.analysed);
          const cats = (a as Analytics & { by_category?: Record<string, { analysed: number; bid: number; submitted: number }> }).by_category ?? {};
          return (
            <>
              <section className="card stack" aria-label="Funnel">
                <h2>Funnel</h2>
                {a.runs.total === 0 ? <Empty title="No runs yet" /> : STEPS.map(([k, label]) => (
                  <div key={k}>
                    <div className="row between small"><span>{label}</span><strong>{a.funnel[k]}</strong></div>
                    <div className="bar" role="img" aria-label={`${label}: ${a.funnel[k]}`}><i style={{ width: `${(a.funnel[k] / max) * 100}%` }} /></div>
                  </div>
                ))}
              </section>
              <div className="grid cols-3">
                <section className="card" aria-label="Latency"><h3>Compute time per run</h3>
                  <p>{a.compute_latency_s.p50 === null ? "–" : `p50 ${a.compute_latency_s.p50}s · p95 ${a.compute_latency_s.p95}s`}</p>
                  <p className="small muted">{a.compute_latency_s.n} finished runs. Excludes the time a run waits for you.</p></section>
                <section className="card" aria-label="Usage"><h3>Model usage</h3><p>{a.usage.tokens.toLocaleString()} tokens</p><p className="small muted">{money(a.usage.cost_usd)} total</p></section>
                <section className="card" aria-label="Runs"><h3>Runs</h3><p>{a.runs.total} total</p><p className="small muted">{Object.entries(a.runs.by_status).map(([k, v]) => `${titleCase(k)} ${v}`).join(" · ") || "–"}</p></section>
              </div>
              {Object.entries(a.outcomes).map(([p, o]) => (
                <section key={p} className="card stack" aria-label={`Outcomes ${p}`}><h2>Outcomes: {p}</h2><Kv data={o} />
                  <h3>Opener strategies (experiment)</h3><Kv data={(a.strategies[p] as Record<string, unknown>) ?? {}} /></section>
              ))}
              {Object.keys(cats).length ? (
                <section className="card stack" aria-label="By category">
                  <h2>By job category</h2>
                  <div style={{ overflowX: "auto" }}>
                    <table aria-label="Funnel by category">
                      <thead><tr><th>Category</th><th>Analysed</th><th>Worth bidding</th><th>Submitted</th></tr></thead>
                      <tbody>{Object.entries(cats).sort((x, y) => y[1].analysed - x[1].analysed).map(([k, v]) => (
                        <tr key={k}><th scope="row">{k === "uncategorised" ? "Uncategorised (pasted jobs)" : k}</th><td>{v.analysed}</td><td>{v.bid}</td><td>{v.submitted}</td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                </section>
              ) : null}
              <p className="small muted">Reply and win rates per category appear once submitted proposals have marketplace outcomes; until then they are not shown rather than guessed.</p>
            </>
          );
        }}
      </Async>
    </div>
  );
}
