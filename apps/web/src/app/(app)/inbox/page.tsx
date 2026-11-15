"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";
import { RiskChips, StatusBadge } from "@/components/Badges";
import { Async, Empty, ErrorBox } from "@/components/States";
import { useApp } from "@/lib/app";
import { budgetText, latestRunByJob, pct, titleCase } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api, qs, toApiError } from "@/lib/http";
import type { JobSummary, Run } from "@/lib/types";

function PasteJob({ persona, onStarted }: { persona: string; onStarted: (r: Run) => void }) {
  const [f, setF] = useState({ title: "", description: "", budget_max: "", budget_min: "", budget_type: "fixed", skills: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function go(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const job = {
        title: f.title, description: f.description, budget_max: Number(f.budget_max), budget_min: Number(f.budget_min || 0), budget_type: f.budget_type,
        required_skills: f.skills.split(",").map((s) => s.trim()).filter(Boolean),
      };
      onStarted(await api.post<Run>("/runs", { kind: "bid", persona, job }));
    } catch (e2) {
      const a = toApiError(e2);
      setErr(a.details?.length ? a.details.map((d) => `${d.loc}: ${d.msg}`).join("; ") : a.message);
      setBusy(false);
    }
  }
  return (
    <form className="card stack" onSubmit={go} aria-label="Paste a job">
      <p className="small muted">For a job that is not on the connected marketplace. You copy the approved proposal by hand; nothing is submitted for pasted jobs.</p>
      <div className="field"><label htmlFor="pt">Title</label><input id="pt" required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
      <div className="field"><label htmlFor="pd">Description (at least 20 characters)</label><textarea id="pd" required minLength={20} style={{ minHeight: 140 }} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
      <div className="grid cols-3">
        <div className="field"><label htmlFor="pb">Budget max</label><input id="pb" inputMode="decimal" required value={f.budget_max} onChange={(e) => setF({ ...f, budget_max: e.target.value })} /></div>
        <div className="field"><label htmlFor="pm">Budget min</label><input id="pm" inputMode="decimal" value={f.budget_min} onChange={(e) => setF({ ...f, budget_min: e.target.value })} /></div>
        <div className="field"><label htmlFor="pty">Type</label><select id="pty" value={f.budget_type} onChange={(e) => setF({ ...f, budget_type: e.target.value })}><option value="fixed">Fixed</option><option value="hourly">Hourly</option></select></div>
      </div>
      <div className="field"><label htmlFor="ps">Required skills (comma separated)</label><input id="ps" value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} /></div>
      {err ? <div className="alert" role="alert">{err}</div> : null}
      <button className="primary" disabled={busy}>Analyse this job</button>
    </form>
  );
}

export default function Inbox() {
  const router = useRouter();
  const { persona } = useApp();
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [paste, setPaste] = useState(false);
  const [startErr, setStartErr] = useState<string | null>(null);
  const jobs = useAsync((s) => api.get<JobSummary[]>(`/jobs/search${qs({ q: term, limit: 30 })}`, s), [term]);
  const runs = useAsync((s) => api.get<Run[]>(`/runs${qs({ kind: "bid", limit: 200 })}`, s), [], { every: 8000 });
  const approvals = useAsync((s) => api.get<Run[]>("/approvals", s), [], { every: 8000 });
  const latest = useMemo(() => latestRunByJob(runs.data ?? []), [runs.data]);
  const ranked = useMemo(() => {
    const list = [...(jobs.data ?? [])];
    const score = (j: JobSummary) => latest.get(j.job_id)?.summary.fit?.score ?? -1;
    return list.map((j, i) => ({ j, i })).sort((a, b) => score(b.j) - score(a.j) || a.i - b.i).map((x) => x.j);
  }, [jobs.data, latest]);

  async function analyse(jobId: string) {
    setStartErr(null);
    try {
      await api.post<Run>("/runs", { kind: "bid", persona, job_id: jobId });
      router.push(`/jobs/${encodeURIComponent(jobId)}`);
    } catch (e) {
      setStartErr(toApiError(e).message);
    }
  }

  return (
    <div className="stack">
      <div className="row between">
        <div><h1>Inbox</h1><p className="muted">Jobs ranked by how well your portfolio fits, from the jobs you have analysed first.</p></div>
        <button onClick={() => setPaste((p) => !p)} aria-expanded={paste}>{paste ? "Close" : "Paste a job"}</button>
      </div>
      {paste ? <PasteJob persona={persona} onStarted={(r) => router.push(`/studio/${r.id}`)} /> : null}

      {approvals.data?.length ? (
        <section className="card flag" aria-label="Awaiting your approval">
          <h2>Awaiting your approval ({approvals.data.length})</h2>
          <ul>
            {approvals.data.map((r) => (
              <li key={r.id}>
                <Link href={`/studio/${r.id}`}>{r.kind === "conversation" ? "Client reply" : r.summary.job?.title ?? r.subject_id}</Link>{" "}
                <span className="muted small">{titleCase(r.kind)} · {r.persona}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <form className="row" role="search" onSubmit={(e) => { e.preventDefault(); setTerm(q.trim()); }}>
        <label className="sr" htmlFor="q">Search jobs</label>
        <input id="q" style={{ maxWidth: 420 }} placeholder="Search jobs, e.g. RAG, dashboard" value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="submit">Search</button>
      </form>
      {startErr ? <div className="alert" role="alert">{startErr}</div> : null}

      <Async state={jobs} what="Loading jobs">
        {() =>
          ranked.length === 0 ? (
            <Empty title="No jobs found">Try another search, or paste a job above.</Empty>
          ) : (
            <div className="grid cols-auto">
              {ranked.map((j) => {
                const r = latest.get(j.job_id);
                return (
                  <article key={j.job_id} className="card stack" aria-label={j.title}>
                    <h3><Link href={`/jobs/${encodeURIComponent(j.job_id)}`}>{j.title}</Link></h3>
                    <div className="small muted">{budgetText(j.budget)} · {j.connects_cost} Connects</div>
                    <p className="small">{j.snippet}</p>
                    <div>{j.required_skills.slice(0, 6).map((s) => <span key={s} className="chip">{s}</span>)}</div>
                    {r ? (
                      <div className="row">
                        <StatusBadge value={r.summary.decision ?? r.status} />
                        <span className="small">Fit {pct(r.summary.fit?.score)}</span>
                        {r.summary.risk?.flags?.length ? <RiskChips flags={r.summary.risk.flags} /> : null}
                      </div>
                    ) : null}
                    <div className="row">
                      {r ? <Link className="btn" href={`/jobs/${encodeURIComponent(j.job_id)}`}>View analysis</Link> : <button onClick={() => void analyse(j.job_id)}>Analyse</button>}
                      {r?.status === "awaiting_approval" ? <Link className="btn" href={`/studio/${r.id}`}>Review draft</Link> : null}
                    </div>
                  </article>
                );
              })}
            </div>
          )
        }
      </Async>
      {jobs.error && jobs.data ? <ErrorBox error={jobs.error} retry={jobs.reload} /> : null}
    </div>
  );
}
