# MCP Tool Inventory

Three own servers (official MCP SDK, FastMCP, Streamable HTTP) plus two third-party servers. Agents never call a server
directly: every call goes through `agent/mcp_client.py`, which enforces the per-node allowlist in `agent/config.yaml`
(`mcp.allowlists`; missing = denied), timeouts, bounded retries and a circuit breaker.

Conventions: every tool has a typed Pydantic input/output schema (MCP `outputSchema`), MCP annotations, and returns errors as
`Code: message (retryable=true|false [retry_after=N] [rule=X])`. Every write takes an `idempotency_key`; a retry with the same
key never repeats the action. Servers 2 and 3 require `Authorization: Bearer $MCP_BEARER_TOKEN`, rate-limit per caller and
write a hash-chained, PII-masked audit log carrying the trace id (`x-gigbid-trace-id`).

## 1. Portfolio server (`mcp_servers/portfolio`, :9101) — read-only

| Tool | R/W | Annotations | Purpose | Trust rationale |
|---|---|---|---|---|
| `search_evidence(requirement, k, filters, freelancer_id)` | R | readOnly, idempotent, closed-world | Evidence ranked for ONE requirement; filters: type, project, industry, since, skills | Own data; empty result = "no evidence", agent must not claim it |
| `get_case_study(case_id, freelancer_id)` | R | same | Full case study + reviews + related evidence ids | Own data |
| `list_skills(freelancer_id)` | R | same | Declared skills split into evidenced / not evidenced | Own data; flags claims that cannot be proven |
| `get_profile(freelancer_id)` | R | same | Headline, rate, minimum budget | Own data |

Resources: `portfolio://case/{case_id}`, `portfolio://proposal/{proposal_id}`, `portfolio://profile/{freelancer_id}`.
Prompts: `ground_claim`, `summarize_fit`. Eval answer keys (`gold_skills`, fabrication flags) are never exposed (tested).
`search_evidence` is currently a lexical BM25 baseline; Phase 4 swaps in the hybrid retriever behind the same contract.

## 2. Marketplace server (`mcp_servers/marketplace`, :9102) — read + write, behind the ONE marketplace port

| Tool | R/W | Annotations | Gate |
|---|---|---|---|
| `search_jobs`, `get_job`, `get_client_history`, `get_connects_balance`, `get_platform_policy`, `get_proposal`, `list_messages` | R | readOnly, idempotent, open-world | none; job/message text is UNTRUSTED data |
| `create_proposal_draft(draft, idempotency_key)` | W | non-destructive, idempotent | none (draft only, costs nothing) |
| `submit_proposal(proposal_id, idempotency_key, approval_token, dry_run)` | W | **destructive** (spends Connects) | human approval token bound to the proposal; `dry_run=true` previews cost/policy with no token and no spend |
| `send_message(proposal_id, text, idempotency_key, approval_token)` | W | **destructive** | human approval token bound to the exact text |

Approval tokens: `agent/approvals.py` (HMAC-SHA256 over action, subject, expiry). Issued only by the API when a human approves
(Phase 6); the graph cannot mint them. Adapter behind the port: GigSim (`adapters/gigsim.py`); live Upwork (`adapters/upwork_live.py`, Phase 3.5).

## 3. Workspace server (`mcp_servers/workspace`, :9103) — local MOCK, documented

Calendar and mailbox are a **local mock** (`workspace:` in config, state in `data/workspace/state.json`): working hours,
recurring busy blocks, tentative holds, email drafts. A Google Calendar/Gmail adapter can replace the store later (same methods).
By design there is **no send-email and no confirm-meeting tool**: a human does both.

| Tool | R/W | Annotations | Purpose |
|---|---|---|---|
| `get_free_slots(date_from, date_to, duration_min, limit)` | R | readOnly | Free slots in working hours, clear of events/holds, with notice and buffers |
| `list_holds`, `list_email_drafts(proposal_id)` | R | readOnly | Current state |
| `hold_slot(start, idempotency_key, duration_min, title, proposal_id)` | W | non-destructive, idempotent | TENTATIVE hold; `SlotUnavailable` if no longer free |
| `release_hold(hold_id)` | W | destructive (removes a tentative hold) | Free the slot again |
| `draft_email(to, subject, body, idempotency_key, proposal_id)` | W | non-destructive, idempotent | Draft only; body is not written to the audit log |

## 4. Third-party servers

| Server | Used for | Status |
|---|---|---|
| Official Upwork MCP (OAuth 2.1) | live marketplace mode via `upwork_live.py` | Phase 3.5, needs owner OAuth consent |
| GitHub MCP (read-only fine-grained token) | repo ingestion for the Portfolio index | Phase 3.5/4 |

## 5. Per-node allowlist summary (source of truth: `agent/config.yaml`)

| Node | Allowed tools |
|---|---|
| scout | marketplace: search_jobs, get_job |
| analyst, critic | none (analyst reads untrusted text: tool-less by design) |
| risk_assessor | marketplace: get_client_history, get_platform_policy |
| fit_matcher | portfolio: search_evidence, get_case_study, list_skills, get_profile |
| pricing | portfolio: get_profile; marketplace: get_platform_policy, get_connects_balance |
| writer, verifier | portfolio: search_evidence, get_case_study |
| compliance | marketplace: get_platform_policy, get_connects_balance |
| client_liaison | marketplace: get_proposal, list_messages; workspace: slots/holds/drafts |
| executor (deterministic, after human approval) | marketplace: create_proposal_draft, submit_proposal, send_message, get_proposal |

A unit test asserts that no LLM node can reach `submit_proposal`, `send_message` or `create_proposal_draft`.
