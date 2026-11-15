/** Types. Endpoints with response models come from the GENERATED contract (src/lib/api/schema.d.ts, `npm run gen:api`);
 *  endpoints that return plain dicts (marketplace passthrough, analytics, memory) are typed by hand below, mirroring
 *  agent/marketplace_port.py and apps/api/service.py:summarise. */
import type { components } from "./api/schema";

type S = components["schemas"];
export type Run = Omit<S["RunOut"], "summary" | "pending"> & { summary: RunSummary; pending: Pending | null };
export type RunCreate = S["RunCreate"];
export type DecisionIn = S["DecisionIn"];
export type TraceEvent = S["EventOut"];
export type Persona = S["PersonaOut"];
export type User = S["UserOut"];

export type Action = "approve" | "edit" | "reject" | "dismiss";

export interface Budget { min: number; max: number; currency: string; type: "fixed" | "hourly" }
export interface JobSummary { job_id: string; title: string; snippet: string; budget: Budget; required_skills: string[]; connects_cost: number; client_id: string }
export interface Job extends JobSummary { description: string; screening_questions: string[]; duration_days: number | null }

export interface PriceQuote {
  amount: number; currency: string; pricing_type: "fixed" | "hourly"; band_low?: number | null; band_high?: number | null; floor?: number | null;
  connects_cost: number; connects_worth_it: boolean; rationale: string;
}
export interface Claim { text: string; evidence_ids: string[]; label?: "supported" | "partly_supported" | "unsupported" | null }
export interface Critique { passed: boolean; issues: string[]; grounding: number; personalisation: number; length_ok: boolean }
export interface Compliance { allowed: boolean; violations: { rule_id: string; message: string; severity: string }[]; policy_version?: string }
export interface Verification { unsupported_before?: number; factual_claims_before?: number; unsupported_after?: number; [k: string]: unknown }

export interface ProposalPending {
  kind: "proposal_approval";
  job_id: string;
  draft: { cover_letter: string; screening_answers: string[]; evidence_ids: string[]; claims: Claim[]; strategy?: string | null; revision?: number };
  price_quote: PriceQuote | null;
  critique: Critique | null;
  compliance: Compliance | null;
  verification: Verification | null;
  submission_flags: { ai_disclosure?: boolean } | null;
  needs_attention: boolean;
  actions: Action[];
}
export interface MessagePending {
  kind: "message_approval";
  proposal_id: string;
  client_message: string;
  intent?: string | null;
  draft_reply: string;
  slots: Record<string, unknown>[];
  flagged: boolean;
  reason: string;
  needs_attention: boolean;
  actions: Action[];
}
export type Pending = ProposalPending | MessagePending | { kind: "unknown"; actions?: Action[] };

export interface RunSummary {
  decision?: string | null; reasons?: string[]; confidence?: number | null;
  risk?: { score: number | null; flags: string[]; explanation?: string } | null;
  fit?: { score: number | null; matched_skills: string[]; missing_skills: string[]; matches?: { requirement_id: string; evidence_ids: string[]; strength: number }[]; reasons?: string[] } | null;
  job_spec?: { requirements?: { id: string; text: string; kind: string; must_have: boolean }[] | null; deliverables?: string[] | null; hidden_needs?: string[] | null } | null;
  price?: PriceQuote | null;
  injection?: { level?: string; score?: number } | null;
  verification?: Verification | null;
  strategy?: string | null; revisions?: number; approval_status?: string | null;
  submission?: { status?: string; proposal_id?: string; connects_spent?: number; balance_after?: number; error?: string; reason?: string; issues?: { code: string; message: string }[] } | null;
  needs_attention?: boolean; dead_letter?: { reason: string } | null;
  usage?: { tokens_in?: number; tokens_out?: number; cost_usd?: number } | null;
  steps?: number; errors?: string[];
  job?: { title?: string; description?: string; budget?: Budget | null; required_skills?: string[]; client_id?: string; manual?: boolean };
  conversation?: { intent?: string; reason?: string };
  [k: string]: unknown;
}

export interface Health {
  status: "ok" | "degraded";
  degraded: string[];
  components: { database: boolean; checkpointer: boolean; marketplace: "up" | "down" | "unconfigured"; models: boolean; injection_classifier: boolean };
  policy_version: string; mode: string; checkpointer_kind: string;
}
export interface MarketplaceStatus { status: "up" | "down" | "unconfigured"; policy: { version: string; update_active: boolean; connects_surcharge: number; requires_ai_disclosure: boolean; notes: string[] } | null; connects: number | null }

export interface Settings {
  default_persona: string | null; scout_queries: string[];
  hourly_rate_usd?: number; min_project_budget_usd?: number; weekly_connects_budget?: number;
}
export interface LedgerEntry { proposal_id: string; run_id: string; job_id: string; persona: string; title?: string | null; submission: NonNullable<RunSummary["submission"]>; strategy?: string | null; submitted_at: string }
export interface MarketProposal { proposal_id: string; job_id: string; status: string; connects_cost: number; connects_spent: number; bid_amount: number; bid_type: string; ai_disclosure: boolean }
export interface ThreadMessage { message_id: string; proposal_id: string; sender: "freelancer" | "client"; text: string; sent_at: string }
export interface Evidence { evidence_id: string; type: string; project: string | null; project_title: string | null; text: string; truncated: boolean; date: string | null; client_industry: string | null; rating: number | null }
export interface MemoryItem { id: number; persona: string; kind: string; key: string; text: string; hits: number; testable: number; consistency: number; status: string; created_at: string; updated_at: string }
export interface MemoryLog { ts: string; action: string; detail: string }
export interface Analytics {
  runs: { total: number; by_status: Record<string, number>; by_kind: Record<string, number> };
  funnel: { analysed: number; bid: number; drafted: number; approved: number; submitted: number };
  compute_latency_s: { n: number; p50: number | null; p95: number | null };
  usage: { tokens: number; cost_usd: number };
  outcomes: Record<string, Record<string, unknown>>;
  strategies: Record<string, unknown>;
}
