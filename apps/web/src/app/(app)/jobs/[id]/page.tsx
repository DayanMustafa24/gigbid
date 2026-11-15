"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { RiskChips, ScoreBar, StatusBadge } from "@/components/Badges";
import { EvidenceHover } from "@/components/EvidenceHover";
import { RunTimeline } from "@/components/RunTimeline";
import { Async, ErrorBox } from "@/components/States";
import { useApp } from "@/lib/app";
import { budgetText, latestRunByJob, money, titleCase } from "@/lib/format";
import { useAsync, useRunStream } from "@/lib/hooks";
import { api, qs, toApiError } from "@/lib/http";
import type { Job, Run } from "@/lib/types";

function Live({ runId }: { runId: string }) {
  const s = useRunStream(runId);
  return <RunTimeline events={s.events} live={s.live} done={s.done} />;
}

function Matrix({ run }: { run: Run }) {
  const spec = run.summary.job_spec;
  const matches = new Map((run.summary.fit?.matches ?? []).map((m) => [m.requirement_id, m]));
  const reqs = spec?.requirements ?? [];
  if (!reqs.length) return <p className="muted">No requirements were extracted.</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table aria-label="Requirements and evidence">
        <thead><tr><th>Requirement</th><th>Kind</th><th>Match</th><th>Evidence from your portfolio</th></tr></thead>
        <tbody>
          {reqs.map((r) => {
            const m = matches.get(r.id);
            const ids = m?.evidence_ids ?? [];
            return (
              <tr key={r.id}>
                <td>{r.text}{r.must_have ? <span className="badge" style={{ marginLeft: 6 }}>must</span> : null}</td>
                <td>{titleCase(r.kind)}</td>
                <td>{ids.length ? <span className="badge ok">{Math.round((m?.strength ?? 0) * 100)}%</span> : <span className="badge bad">no evidence</span>}</td>
                <td>{ids.length ? <EvidenceHover ids={ids} label={`Evidence for ${r.text}`}>{ids.length} item{ids.length > 1 ? "s" : ""}</EvidenceHover> : <span className="muted">The agent will not claim this.</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function JobPage() {
  const { id } = useParams<{ id: string }>();
  const jobId = decodeURIComponent(id);
  const { persona } = useApp();
  const job = useAsync((s) => api.get<Job>(`/jobs/${encodeURIComponent(jobId)}`, s), [jobId]);
  const runs = useAsync((s) => api.get<Run[]>(`/runs${qs({ kind: "bid", limit: 200 })}`, s), [jobId], { every: 3000 });
  const run = useMemo(() => latestRunByJob(runs.data ?? []).get(jobId) ?? null, [runs.data, jobId]);
  const [err, setErr] = useState<string | null>(null);
  const fallback = run?.summary.job;
  const title = job.data?.title ?? fallback?.title ?? jobId;

  async function analyse() {
    setErr(null);
    try {
      await api.post<Run>("/runs", { kind: "bid", persona, job_id: jobId });
      runs.reload();
    } catch (e) {
      setErr(toApiError(e).message);
    }
  }

  const s = run?.summary;
  return (
    <div className="stack">
      <p><Link href="/inbox">← Inbox</Link></p>
      <h1>{title}</h1>
      {job.error && !fallback ? <ErrorBox error={job.error} retry={job.reload} /> : null}
      {job.data ? <p className="muted">{budgetText(job.data.budget)} · {job.data.connects_cost} Connects · {job.data.duration_days ? `${job.data.duration_days} days` : "duration n/a"}</p> : null}

      <div className="row">
        <button className="primary" onClick={() => void analyse()} disabled={run?.status === "running"}>{run ? "Analyse again" : "Analyse this job"}</button>
        {run?.status === "awaiting_approval" ? <Link className="btn" href={`/studio/${run.id}`}>Review the draft</Link> : null}
        {run ? <Link className="btn" href={`/runs/${run.id}`}>Run details</Link> : null}
      </div>
      {err ? <div className="alert" role="alert">{err}</div> : null}

      {run ? (
        <section className="card stack" aria-label="Analysis">
          <div className="row between"><h2>Analysis</h2><StatusBadge value={s?.decision ?? run.status} /></div>
          {run.status === "running" ? <Live runId={run.id} /> : null}
          {s?.reasons?.length ? <ul>{s.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul> : null}
          <div className="grid cols-2">
            <ScoreBar label="Fit with your portfolio" value={s?.fit?.score} />
            <ScoreBar label="Risk" value={s?.risk?.score} />
          </div>
          <div><RiskChips flags={s?.risk?.flags} /> {s?.risk?.explanation ? <span className="small muted">{s.risk.explanation}</span> : null}</div>
          {s?.injection && s.injection.level && s.injection.level !== "none" ? <p className="alert info small">Embedded instructions were detected in the post and ignored (level: {s.injection.level}).</p> : null}
          <h3>Requirements and the evidence behind them</h3>
          <Matrix run={run} />
          {s?.fit?.missing_skills?.length ? <p className="small">Not in your portfolio: {s.fit.missing_skills.map((m) => <span key={m} className="chip miss">{m}</span>)}</p> : null}
          {s?.price ? <p>Suggested price: <strong>{money(s.price.amount, s.price.currency)}{s.price.pricing_type === "hourly" ? "/hr" : ""}</strong> · {s.price.connects_cost} Connects</p> : null}
          {s?.submission?.status ? <p className="alert ok">Submitted ({s.submission.status}). Connects spent: {s.submission.connects_spent ?? "?"}.</p> : null}
        </section>
      ) : <p className="muted">Not analysed yet.</p>}

      <Async state={job} what="Loading the job post">
        {(j) => (
          <section className="card stack" aria-label="Job post">
            <h2>Job post</h2>
            <p className="draft">{j.description}</p>
            {j.screening_questions.length ? <><h3>Screening questions</h3><ol>{j.screening_questions.map((q, i) => <li key={i}>{q}</li>)}</ol></> : null}
            <div>{j.required_skills.map((x) => <span key={x} className="chip">{x}</span>)}</div>
          </section>
        )}
      </Async>
    </div>
  );
}
