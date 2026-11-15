"use client";

import { useState, type FormEvent } from "react";
import { Async, Empty } from "@/components/States";
import { useApp } from "@/lib/app";
import { titleCase, when } from "@/lib/format";
import { useAsync } from "@/lib/hooks";
import { api, qs, toApiError } from "@/lib/http";
import type { MarketplaceStatus, MemoryItem, MemoryLog, Settings } from "@/lib/types";

function Memory({ persona }: { persona: string }) {
  const items = useAsync((s) => api.get<MemoryItem[]>(`/memory${qs({ persona })}`, s), [persona]);
  const log = useAsync((s) => api.get<MemoryLog[]>("/memory/log?limit=20", s), []);
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  async function remove(id: number) {
    setMsg(null);
    try {
      await api.del(`/memory/items/${id}`);
      items.reload();
      log.reload();
    } catch (e) {
      setMsg(toApiError(e).message);
    }
  }
  async function forget() {
    setMsg(null);
    try {
      await api.post("/memory/forget", { confirm });
      setConfirm("");
      setMsg("Everything remembered about you was erased.");
      items.reload();
      log.reload();
    } catch (e) {
      setMsg(toApiError(e).message);
    }
  }
  return (
    <section className="card stack" aria-label="What GigBid remembers">
      <h2>What GigBid has learned about you</h2>
      <p className="small muted">Style rules appear only after you have repeatedly edited or approved drafts the same way. You can delete any of them; a deleted rule is never learned again.</p>
      <Async state={items} what="Loading memory">
        {(list) =>
          list.length === 0 ? (
            <Empty title="Nothing learned yet">Edit or approve a few drafts and consistent preferences will show up here.</Empty>
          ) : (
            <ul>
              {list.map((m) => (
                <li key={m.id} className="row between">
                  <span><strong>{m.text}</strong> <span className="small muted">({titleCase(m.kind)} · seen in {m.hits} proposals · {Math.round(m.consistency * 100)}% consistent)</span></span>
                  <button onClick={() => void remove(m.id)} aria-label={`Delete rule: ${m.text}`}>Delete</button>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
      {msg ? <div className="alert info" role="status">{msg}</div> : null}
      <details>
        <summary>Erase everything (right to be forgotten)</summary>
        <div className="field">
          <label htmlFor="forget">Type “forget everything” to confirm</label>
          <input id="forget" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
        <button className="danger" disabled={confirm !== "forget everything"} onClick={() => void forget()}>Erase my memory</button>
      </details>
      <details>
        <summary>Audit log</summary>
        <Async state={log}>{(l) => (l.length ? <ul className="small">{l.map((e, i) => <li key={i}>{when(e.ts)} · {titleCase(e.action)} <span className="muted mono">{e.detail}</span></li>)}</ul> : <p className="muted small">Empty.</p>)}</Async>
      </details>
    </section>
  );
}

type PortfolioState = { persona: string; profile: Record<string, unknown>; files: { kind: string; name: string; chars: number }[]; ready: boolean };

function Portfolio() {
  const { refresh } = useApp();
  const pf = useAsync((s) => api.get<PortfolioState>("/portfolio", s), []);
  const [kind, setKind] = useState("case_study");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [prof, setProf] = useState<{ name: string; headline: string; skills: string; years: string } | null>(null);
  const p = prof ?? { name: String(pf.data?.profile.display_name ?? ""), headline: String(pf.data?.profile.headline ?? ""), skills: ((pf.data?.profile.skills as string[] | undefined) ?? []).join(", "), years: String(pf.data?.profile.years_experience ?? "") };

  async function saveProfile(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.put("/portfolio/profile", { display_name: p.name, headline: p.headline || undefined, skills: p.skills.split(",").map((s) => s.trim()).filter(Boolean), years_experience: p.years ? Number(p.years) : undefined });
      setMsg({ ok: true, text: "Profile saved." });
      pf.reload();
      refresh();
    } catch (err) {
      setMsg({ ok: false, text: toApiError(err).message });
    }
  }
  async function upload(file: File | undefined) {
    if (!file) return;
    setMsg(null);
    try {
      const text = await file.text();
      await api.post("/portfolio/files", { kind, name: file.name.replace(/\.[^.]+$/, ""), text });
      setMsg({ ok: true, text: `${file.name} added. It is indexed on the next run.` });
      pf.reload();
      refresh();
    } catch (err) {
      setMsg({ ok: false, text: toApiError(err).message });
    }
  }
  async function remove(k: string, n: string) {
    setMsg(null);
    try {
      await api.del(`/portfolio/files/${k}/${encodeURIComponent(n)}`);
      pf.reload();
      refresh();
    } catch (err) {
      setMsg({ ok: false, text: toApiError(err).message });
    }
  }
  return (
    <section className="card stack" aria-label="Your portfolio">
      <h2>Your own portfolio</h2>
      <p className="small muted">Upload case studies, repo READMEs and your CV as Markdown or text files. Agents may cite only what is here, and every claim is checked against it. It is private to your account.</p>
      <form className="stack" onSubmit={saveProfile} aria-label="Portfolio profile">
        <div className="grid cols-3">
          <div className="field"><label htmlFor="pf-name">Your name</label><input id="pf-name" required value={p.name} onChange={(e) => setProf({ ...p, name: e.target.value })} /></div>
          <div className="field"><label htmlFor="pf-head">Headline</label><input id="pf-head" value={p.headline} onChange={(e) => setProf({ ...p, headline: e.target.value })} /></div>
          <div className="field"><label htmlFor="pf-years">Years of experience</label><input id="pf-years" inputMode="numeric" value={p.years} onChange={(e) => setProf({ ...p, years: e.target.value })} /></div>
        </div>
        <div className="field"><label htmlFor="pf-skills">Skills (comma separated; used to tag your evidence)</label><input id="pf-skills" value={p.skills} onChange={(e) => setProf({ ...p, skills: e.target.value })} /></div>
        <div className="row"><button type="submit">Save profile</button></div>
      </form>
      <div className="row">
        <div className="field"><label htmlFor="pf-kind">File type</label>
          <select id="pf-kind" value={kind} onChange={(e) => setKind(e.target.value)}><option value="case_study">Case study</option><option value="readme">Repo README</option><option value="cv">CV</option></select></div>
        <div className="field"><label htmlFor="pf-file">File (.md or .txt)</label>
          <input id="pf-file" type="file" accept=".md,.txt,text/markdown,text/plain" onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} /></div>
      </div>
      {msg ? <div className={`alert ${msg.ok ? "ok" : ""}`} role={msg.ok ? "status" : "alert"}>{msg.text}</div> : null}
      <Async state={pf} what="Loading your portfolio">
        {(d) => (
          <>
            <p className="small">Status: <span className={`badge ${d.ready ? "ok" : "warn"}`}>{d.ready ? "ready to cite" : "add a profile and at least one case study or README"}</span></p>
            {d.files.length ? (
              <ul>{d.files.map((f) => (
                <li key={`${f.kind}:${f.name}`} className="row between"><span>{titleCase(f.kind)} · {f.name} <span className="small muted">({f.chars.toLocaleString()} characters)</span></span>
                  <button onClick={() => void remove(f.kind, f.name)} aria-label={`Delete ${f.name}`}>Delete</button></li>
              ))}</ul>
            ) : <Empty title="No files yet" />}
            <p className="small muted">Select it above under “Portfolio the agents may cite” as {d.persona}. PDFs are not accepted here yet: export your CV as Markdown or text.</p>
          </>
        )}
      </Async>
    </section>
  );
}

export default function SettingsPage() {
  const { settings, personas, persona, refresh } = useApp();
  const status = useAsync((s) => api.get<MarketplaceStatus>("/marketplace/status", s), []);
  const [p, setP] = useState(persona);
  const [rate, setRate] = useState(String(settings.hourly_rate_usd ?? ""));
  const [minB, setMinB] = useState(String(settings.min_project_budget_usd ?? ""));
  const [conn, setConn] = useState(String(settings.weekly_connects_budget ?? ""));
  const [queries, setQueries] = useState(settings.scout_queries.join(", "));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = { default_persona: p, scout_queries: queries.split(",").map((q) => q.trim()).filter(Boolean) };
    if (rate) body.hourly_rate_usd = Number(rate);
    if (minB) body.min_project_budget_usd = Number(minB);
    if (conn) body.weekly_connects_budget = Number(conn);
    try {
      await api.put<Settings>("/settings", body);
      setMsg({ ok: true, text: "Saved." });
      refresh();
    } catch (err) {
      setMsg({ ok: false, text: toApiError(err).message });
    }
  }
  return (
    <div className="stack">
      <h1>Settings</h1>
      <form className="card stack" onSubmit={save} aria-label="Preferences">
        <h2>Preferences</h2>
        <div className="field"><label htmlFor="persona">Portfolio the agents may cite</label>
          <select id="persona" value={p} onChange={(e) => setP(e.target.value)}>{personas.map((x) => <option key={x.id} value={x.id}>{x.display_name ?? x.id}</option>)}</select></div>
        <div className="grid cols-3">
          <div className="field"><label htmlFor="rate">Hourly rate (USD)</label><input id="rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} /></div>
          <div className="field"><label htmlFor="minb">Minimum project budget (USD)</label><input id="minb" inputMode="decimal" value={minB} onChange={(e) => setMinB(e.target.value)} /></div>
          <div className="field"><label htmlFor="conn">Weekly Connects budget</label><input id="conn" inputMode="numeric" value={conn} onChange={(e) => setConn(e.target.value)} /></div>
        </div>
        <div className="field"><label htmlFor="queries">Search phrases (comma separated)</label><input id="queries" value={queries} onChange={(e) => setQueries(e.target.value)} /></div>
        {msg ? <div className={`alert ${msg.ok ? "ok" : ""}`} role={msg.ok ? "status" : "alert"}>{msg.text}</div> : null}
        <div className="row"><button className="primary" type="submit">Save</button></div>
        <p className="small muted">Autonomy: human approval is required for every proposal and message. It cannot be switched off.</p>
      </form>

      <section className="card stack" aria-label="Marketplace">
        <h2>Marketplace and platform rules</h2>
        <Async state={status} what="Checking marketplace">
          {(m) => (
            <>
              <p>Status: <span className={`badge ${m.status === "up" ? "ok" : "warn"}`}>{m.status}</span>{m.connects !== null ? ` · ${m.connects} Connects` : ""}</p>
              {m.policy ? (
                <ul className="small">
                  <li>Policy version {m.policy.version}{m.policy.update_active ? " (update in force)" : ""}</li>
                  <li>Extra Connects per proposal: {m.policy.connects_surcharge}</li>
                  <li>AI disclosure required: {m.policy.requires_ai_disclosure ? "yes (set automatically on submit)" : "no"}</li>
                  {m.policy.notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              ) : null}
            </>
          )}
        </Async>
        <p className="small muted">Your own portfolio: upload it in the section below.</p>
      </section>

      <Portfolio />

      {persona ? <Memory persona={persona} /> : null}
    </div>
  );
}
