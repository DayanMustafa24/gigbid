"use client";

import { useState, type FormEvent } from "react";
import { Async } from "@/components/States";
import { useApp } from "@/lib/app";
import { useAsync } from "@/lib/hooks";
import { api, toApiError } from "@/lib/http";
import type { MarketplaceStatus, Settings } from "@/lib/types";

export default function Onboarding() {
  const { personas, settings } = useApp();
  const status = useAsync((s) => api.get<MarketplaceStatus>("/marketplace/status", s), []);
  const [persona, setPersona] = useState(settings.default_persona ?? personas[0]?.id ?? "");
  const chosen = personas.find((p) => p.id === persona);
  const [rate, setRate] = useState(String(settings.hourly_rate_usd ?? chosen?.hourly_rate_usd ?? ""));
  const [minBudget, setMinBudget] = useState(String(settings.min_project_budget_usd ?? chosen?.minimum_project_budget_usd ?? ""));
  const [connects, setConnects] = useState(String(settings.weekly_connects_budget ?? ""));
  const [queries, setQueries] = useState(settings.scout_queries.join(", "));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = { default_persona: persona, scout_queries: queries.split(",").map((q) => q.trim()).filter(Boolean) };
    if (rate) body.hourly_rate_usd = Number(rate);
    if (minBudget) body.min_project_budget_usd = Number(minBudget);
    if (connects) body.weekly_connects_budget = Number(connects);
    try {
      await api.put<Settings>("/settings", body);
      window.location.assign("/inbox"); // full load: every screen starts from the saved settings
    } catch (err) {
      setError(toApiError(err).message);
      setBusy(false);
    }
  }

  if (!personas.length) return <div className="alert" role="alert">No freelancer portfolio found. Run `make seed` (synthetic personas) or add your own portfolio folder.</div>;
  return (
    <form className="stack" onSubmit={save} style={{ maxWidth: 720 }} aria-labelledby="onb-title">
      <h1 id="onb-title">Set up GigBid</h1>
      <p className="muted">Pick whose portfolio the agents may cite. Every claim in a proposal is checked against that portfolio.</p>

      <fieldset className="card" style={{ border: "1px solid var(--line)" }}>
        <legend>Portfolio</legend>
        {personas.map((p) => (
          <label key={p.id} style={{ fontWeight: 400, display: "flex", gap: ".5rem", padding: ".35rem 0" }}>
            <input type="radio" name="persona" value={p.id} checked={persona === p.id} onChange={() => setPersona(p.id)} style={{ width: "auto" }} />
            <span>
              <strong>{p.display_name ?? p.id}</strong>
              <br />
              <span className="muted small">{p.headline}</span>
            </span>
          </label>
        ))}
      </fieldset>

      <div className="card">
        <h2>Money rules</h2>
        <div className="grid cols-3">
          <div className="field"><label htmlFor="rate">Hourly rate (USD)</label><input id="rate" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} /></div>
          <div className="field"><label htmlFor="minb">Minimum project budget (USD)</label><input id="minb" inputMode="decimal" value={minBudget} onChange={(e) => setMinBudget(e.target.value)} /></div>
          <div className="field"><label htmlFor="conn">Weekly Connects budget</label><input id="conn" inputMode="numeric" value={connects} onChange={(e) => setConnects(e.target.value)} /></div>
        </div>
        <p className="small muted">Jobs below your minimum are skipped. Pricing and the Connects value check use these numbers.</p>
      </div>

      <div className="card">
        <h2>What to look for</h2>
        <div className="field">
          <label htmlFor="queries">Search phrases (comma separated)</label>
          <input id="queries" value={queries} onChange={(e) => setQueries(e.target.value)} placeholder="RAG, document AI, dashboard" />
        </div>
      </div>

      <div className="card">
        <h2>Marketplace and approval</h2>
        <Async state={status} what="Checking marketplace">
          {(m) => (
            <p>
              Marketplace: <span className={`badge ${m.status === "up" ? "ok" : "warn"}`}>{m.status}</span>
              {m.connects !== null ? <> · {m.connects} Connects available</> : null}
              {m.policy?.update_active ? <> · platform policy update active (+{m.policy.connects_surcharge} Connects{m.policy.requires_ai_disclosure ? ", AI disclosure required" : ""})</> : null}
            </p>
          )}
        </Async>
        <p className="small">Human approval is required for every proposal submitted and every client message sent. This is not a setting.</p>
      </div>

      {error ? <div className="alert" role="alert">{error}</div> : null}
      <button className="primary" type="submit" disabled={busy || !persona}>Save and open the inbox</button>
    </form>
  );
}
