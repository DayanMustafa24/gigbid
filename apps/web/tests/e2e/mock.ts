import type { Page, Route } from "@playwright/test";

/** Deterministic in-browser API: every /api/* request is answered here, so e2e needs no FastAPI, database or model keys.
 *  The shapes mirror apps/api (see src/lib/types.ts). Unknown routes answer 404 and are recorded, so a missing mock fails loudly. */
export interface Call { method: string; path: string; body?: unknown }
export interface MockOptions { persona?: string | null; degraded?: string[]; apiDown?: boolean; runStatus?: string }
export interface Mock { calls: Call[]; state: { persona: string | null; run: Record<string, unknown> } }

const NOW = "2026-11-15T10:00:00Z";
const EVIDENCE = { evidence_id: "ai_ml:contract-qa:case_study", type: "case_study", project: "contract-qa", project_title: "Contract QA assistant",
  text: "Built a retrieval assistant for a legal team; deflected 38% of tier-one tickets.", truncated: false, date: "2025-06-01", client_industry: "Legal", rating: null };
const JOB = { job_id: "job-1", title: "RAG assistant for support tickets", snippet: "Need a retrieval assistant", description: "We need a retrieval assistant for our support team.",
  budget: { min: 400, max: 900, currency: "USD", type: "fixed" }, required_skills: ["RAG", "Python"], connects_cost: 8, client_id: "c-1", screening_questions: ["Describe a similar project."], duration_days: 30 };
const DRAFT = "Hi Sam, I built a RAG assistant that cut ticket handling time by 38%. Happy to share more.";

const REGENERATED = "Hi Sam, short rewrite grounded in my legal project.";

function pendingRun(status: string): Record<string, unknown> {
  return {
    id: "run-1", kind: "bid", subject_id: "job-1", persona: "ai_ml", status, decision: "bid", error: null, created_at: NOW, updated_at: NOW,
    summary: { decision: "bid", reasons: ["Strong fit and fair budget."], risk: { score: 0.1, flags: [], explanation: "" },
      fit: { score: 0.9, matched_skills: ["RAG"], missing_skills: [], matches: [{ requirement_id: "r1", evidence_ids: [EVIDENCE.evidence_id], strength: 1 }] },
      job_spec: { requirements: [{ id: "r1", text: "Experience with RAG", kind: "skill", must_have: true }], deliverables: [], hidden_needs: [] },
      job: { title: JOB.title } },
    pending: status !== "awaiting_approval" ? null : {
      kind: "proposal_approval", job_id: "job-1",
      draft: { cover_letter: DRAFT, screening_answers: [], evidence_ids: [EVIDENCE.evidence_id], revision: 0,
        claims: [{ text: "I built a RAG assistant that cut ticket handling time by 38%.", evidence_ids: [EVIDENCE.evidence_id], label: "supported" }] },
      price_quote: { amount: 650, currency: "USD", pricing_type: "fixed", band_low: 400, band_high: 900, floor: 300, connects_cost: 8, connects_worth_it: true, rationale: "Inside the market band." },
      critique: { passed: true, issues: [], grounding: 1, personalisation: 0.8, length_ok: true }, compliance: { allowed: true, violations: [] },
      verification: { unsupported_before: 1, factual_claims_before: 3, unsupported_after: 0 }, submission_flags: { ai_disclosure: false }, needs_attention: false,
      actions: ["approve", "edit", "reject"],
    },
  };
}

export async function mockApi(page: Page, opts: MockOptions = {}): Promise<Mock> {
  const m: Mock = { calls: [], state: { persona: opts.persona === undefined ? "ai_ml" : opts.persona, run: pendingRun(opts.runStatus ?? "awaiting_approval") } };
  const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = req.method();
    let body: unknown;
    try { body = req.postDataJSON(); } catch { body = undefined; }
    m.calls.push({ method, path, body });

    if (path === "/health/ready") {
      if (opts.apiDown) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
      const degraded = opts.degraded ?? [];
      return json(route, { status: degraded.length ? "degraded" : "ok", degraded, policy_version: "test", mode: "gigsim", checkpointer_kind: "memory",
        components: { database: true, checkpointer: true, marketplace: degraded.includes("marketplace") ? "down" : "up", models: !degraded.includes("models"), injection_classifier: true } });
    }
    if (path === "/auth/me") return json(route, { id: "u1", email: "owner@example.com", role: "owner" });
    if (path === "/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/settings" && method === "GET") return json(route, { default_persona: m.state.persona, scout_queries: [] });
    if (path === "/settings" && method === "PUT") { m.state.persona = (body as { default_persona: string }).default_persona; return json(route, body); }
    if (path === "/personas") return json(route, [{ id: "ai_ml", display_name: "Priya Nair", headline: "AI/ML Engineer", hourly_rate_usd: 55, minimum_project_budget_usd: 300 }]);
    if (path === "/marketplace/status") return json(route, { status: "up", connects: 100, policy: { version: "v1", update_active: false, connects_surcharge: 0, requires_ai_disclosure: false, notes: [] } });
    if (path === "/approvals") return json(route, m.state.run.status === "awaiting_approval" ? [m.state.run] : []);
    if (path === "/jobs/search") return json(route, [JOB]);
    if (path === "/jobs/job-1") return json(route, JOB);
    if (path === "/runs" && method === "GET") return json(route, [m.state.run]);
    if (path === "/runs" && method === "POST") return json(route, m.state.run, 202);
    if (path === "/runs/run-1" && method === "GET") return json(route, m.state.run);
    if (path === "/runs/run-1/events") return route.fulfill({ status: 200, contentType: "text/event-stream", body: `event: done\ndata: {"status":"awaiting_approval"}\n\n` });
    if (path === "/runs/run-1/regenerate") return json(route, { paragraph: 0, paragraph_text: REGENERATED, cover_letter: REGENERATED, removed_claims: [], violations: [], evidence_ids: [] });
    if (path === "/runs/run-1/decision") {
      const b = body as { action: string };
      const done = pendingRun("completed");
      const summary = done.summary as Record<string, unknown>;
      if (b.action === "reject") summary.approval_status = "rejected";
      else summary.submission = { status: "submitted", proposal_id: "p-1", connects_spent: 8, balance_after: 92 };
      m.state.run = done;
      return json(route, done, 202);
    }
    if (path === "/personas/ai_ml/evidence") return json(route, [EVIDENCE]);
    if (path === "/proposals") return json(route, []);
    return json(route, { error: { code: "not_mocked", message: `No mock for ${method} ${path}` } }, 404);
  });
  return m;
}

export const TEXT = { draft: DRAFT, evidence: "deflected 38% of tier-one tickets", regenerated: REGENERATED };
