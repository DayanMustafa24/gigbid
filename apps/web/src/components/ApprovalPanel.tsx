"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/http";
import { money, slotLabel, titleCase } from "@/lib/format";
import type { Action, DecisionIn, MessagePending, ProposalPending, Run } from "@/lib/types";
import { DraftView } from "./DraftView";

function Decide({ run, text, actions, labels, onDone, regen = false }: {
  run: Run; text: string; actions: Action[]; labels: Partial<Record<Action, string>>; onDone: (r: Run) => void; regen?: boolean;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [edited, setEdited] = useState(text);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const paras = text.split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean);
  const [para, setPara] = useState(0);
  const [instr, setInstr] = useState("");
  const [regenNote, setRegenNote] = useState<string | null>(null);

  async function regenerate() {
    setBusy(true);
    setError(null);
    setRegenNote(null);
    try {
      const r = await api.post<{ cover_letter: string; removed_claims: string[]; violations: string[] }>(`/runs/${run.id}/regenerate`, { paragraph: para, instruction: instr.trim() || undefined });
      setEdited(r.cover_letter);
      setRegenNote(`Paragraph ${para + 1} was rewritten and checked against your portfolio${r.removed_claims.length ? ` (${r.removed_claims.length} unsupported claim(s) removed)` : ""}. Review it, then save.`);
    } catch (e) {
      setError(toApiError(e).message);
    } finally {
      setBusy(false);
    }
  }

  async function send(body: DecisionIn) {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.post<Run>(`/runs/${run.id}/decision`, body));
    } catch (e) {
      const a = toApiError(e);
      setError(a.status === 409 ? "This was already decided (maybe in another tab). Refresh to see the result." : a.message);
      setBusy(false);
    }
  }
  const has = (a: Action) => actions.includes(a);
  const negative: Action = has("reject") ? "reject" : "dismiss";
  return (
    <div className="stack">
      {mode === "edit" && regen && paras.length > 0 ? (
        <fieldset className="card stack" style={{ border: "1px solid var(--line)" }}>
          <legend>Regenerate one paragraph</legend>
          <div className="field"><label htmlFor="regen-para">Paragraph</label>
            <select id="regen-para" value={para} onChange={(e) => setPara(Number(e.target.value))}>
              {paras.map((p, i) => <option key={i} value={i}>{i + 1}. {p.slice(0, 50)}{p.length > 50 ? "…" : ""}</option>)}
            </select></div>
          <div className="field"><label htmlFor="regen-instr">What should change? (optional)</label>
            <input id="regen-instr" maxLength={300} value={instr} onChange={(e) => setInstr(e.target.value)} placeholder="e.g. shorter, lead with the legal project" /></div>
          <div className="row"><button disabled={busy} onClick={() => void regenerate()}>Regenerate paragraph</button></div>
          <p className="small muted">Rewrites that paragraph of the original draft (your other edits in the box are replaced). The result is verified against your portfolio and still needs your approval.</p>
          {regenNote ? <div className="alert info" role="status">{regenNote}</div> : null}
        </fieldset>
      ) : null}
      {mode === "edit" ? (
        <div className="field">
          <label htmlFor="edited">Your version (it is re-checked against the policy and your portfolio before anything is sent)</label>
          <textarea id="edited" value={edited} onChange={(e) => setEdited(e.target.value)} />
        </div>
      ) : null}
      {mode === "reject" ? (
        <div className="field">
          <label htmlFor="reason">Why? (optional, helps the agent learn what you do not want)</label>
          <textarea id="reason" style={{ minHeight: 80 }} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </div>
      ) : null}
      {error ? <div className="alert" role="alert">{error}</div> : null}
      <div className="row">
        {mode === "view" ? (
          <>
            {has("approve") ? <button className="primary" disabled={busy} onClick={() => void send({ action: "approve" })}>{labels.approve ?? "Approve"}</button> : null}
            {has("edit") ? <button disabled={busy} onClick={() => setMode("edit")}>Edit</button> : null}
            {has("edit") && regen ? <button disabled={busy} onClick={() => setMode("edit")}>Regenerate a paragraph</button> : null}
            {has(negative) ? <button className="danger" disabled={busy} onClick={() => setMode("reject")}>{labels[negative] ?? titleCase(negative)}</button> : null}
          </>
        ) : null}
        {mode === "edit" ? (
          <>
            <button className="primary" disabled={busy || !edited.trim()} onClick={() => void send({ action: "edit", edited_text: edited })}>{labels.edit ?? "Save edit and continue"}</button>
            <button disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          </>
        ) : null}
        {mode === "reject" ? (
          <>
            <button className="danger" disabled={busy} onClick={() => void send({ action: negative, reason: reason.trim() || undefined })}>Confirm {negative}</button>
            <button disabled={busy} onClick={() => setMode("view")}>Cancel</button>
          </>
        ) : null}
      </div>
    </div>
  );
}

export function ProposalApproval({ run, p, onDone }: { run: Run; p: ProposalPending; onDone: (r: Run) => void }) {
  const q = p.price_quote;
  const blocked = p.compliance && !p.compliance.allowed;
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <section className="card stack" aria-label="Draft proposal">
        <h2>Cover letter</h2>
        <DraftView text={p.draft.cover_letter} claims={p.draft.claims ?? []} />
        {p.draft.screening_answers?.length ? (
          <div>
            <h3>Screening answers</h3>
            <ol>{p.draft.screening_answers.map((a, i) => <li key={i} className="draft">{a}</li>)}</ol>
          </div>
        ) : null}
      </section>
      <aside className="stack">
        <section className="card stack" aria-label="Terms">
          <h2>Terms</h2>
          {q ? (
            <>
              <p><strong data-testid="bid-amount">{money(q.amount, q.currency)}{q.pricing_type === "hourly" ? "/hr" : ""}</strong>{" "}
                <span className="muted small">({q.pricing_type}{q.band_low ? `, market band ${money(q.band_low)}–${money(q.band_high)}` : ""})</span></p>
              <p className="small">{q.rationale}</p>
              <p className="small">Costs <strong>{q.connects_cost} Connects</strong>. {q.connects_worth_it ? "Worth it at your measured or assumed reply rate." : <span className="badge warn">Low expected value for the Connects cost</span>}</p>
            </>
          ) : <p className="muted">No price was produced.</p>}
          {p.submission_flags?.ai_disclosure ? <p className="alert info small">The platform requires an AI-assistance disclosure; it will be set on submission.</p> : null}
        </section>
        <section className="card stack" aria-label="Checks">
          <h2>Checks</h2>
          {p.verification ? <p className="small">Claims removed or rewritten because your portfolio does not support them: <strong>{p.verification.unsupported_before ?? 0}</strong> of {p.verification.factual_claims_before ?? "?"} factual claims.</p> : null}
          {p.critique ? <p className="small">Quality review: <span className={`badge ${p.critique.passed ? "ok" : "warn"}`}>{p.critique.passed ? "passed" : "needs work"}</span>{p.critique.issues.length ? ` — ${p.critique.issues.join("; ")}` : ""}</p> : null}
          {p.compliance ? (
            <p className="small">Policy: <span className={`badge ${blocked ? "bad" : p.compliance.violations.length ? "warn" : "ok"}`}>{blocked ? "blocked" : p.compliance.violations.length ? "warnings" : "clear"}</span>
              {p.compliance.violations.map((v) => <span key={v.rule_id} className="chip">{v.message}</span>)}</p>
          ) : null}
          {p.needs_attention ? <p className="alert small" role="alert">The agent could not fully satisfy its own checks. Read this draft carefully before approving.</p> : null}
        </section>
        <section className="card" aria-label="Decision">
          <h2>Your decision</h2>
          <p className="small muted">Nothing is submitted until you approve. Approval is bound to exactly this text.</p>
          <Decide run={run} text={p.draft.cover_letter} actions={p.actions} labels={{ approve: `Approve and submit${q ? ` (${q.connects_cost} Connects)` : ""}`, edit: "Save edit and submit", reject: "Reject" }} onDone={onDone} regen />
        </section>
      </aside>
    </div>
  );
}

export function MessageApproval({ run, p, onDone }: { run: Run; p: MessagePending; onDone: (r: Run) => void }) {
  return (
    <div className="grid cols-2" style={{ alignItems: "start" }}>
      <section className="card stack" aria-label="Client message">
        <h2>Client wrote</h2>
        <p className="draft">{p.client_message}</p>
        {p.intent ? <p className="small muted">Detected intent: {titleCase(p.intent)}</p> : null}
        {p.flagged ? <p className="alert" role="alert">Flagged: {p.reason || "this message looks unsafe, so no draft was produced. Reply yourself or dismiss."}</p> : null}
        {p.slots?.length ? <div><h3>Your free slots</h3><ul>{p.slots.map((s, i) => <li key={i}>{slotLabel(s)}</li>)}</ul></div> : null}
      </section>
      <section className="card stack" aria-label="Reply">
        <h2>Reply</h2>
        {p.draft_reply ? <p className="draft" data-testid="draft-text">{p.draft_reply}</p> : <p className="muted">No draft. Write your own with Edit.</p>}
        <Decide run={run} text={p.draft_reply} actions={p.actions} labels={{ approve: "Approve and send", edit: "Save edit and send", dismiss: "Dismiss" }} onDone={onDone} />
      </section>
    </div>
  );
}
