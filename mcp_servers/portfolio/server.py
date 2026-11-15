"""Portfolio MCP server (read-only). Official MCP SDK (FastMCP).

Run (Streamable HTTP, default port 9101):   python -m mcp_servers.portfolio.server
Run over stdio (Inspector / Claude Desktop): MCP_TRANSPORT=stdio python -m mcp_servers.portfolio.server
Env: PORTFOLIO_DATA_DIR (default data/synthetic/personas), PORTFOLIO_DEFAULT_FREELANCER, MCP_HOST, MCP_PORT.

Tools    : search_evidence, get_case_study, list_skills, get_profile   (all readOnly, idempotent, closed-world)
Resources: portfolio://case/{case_id}, portfolio://proposal/{proposal_id}, portfolio://profile/{freelancer_id}
Prompts  : ground_claim, summarize_fit
Eval answer keys (gold_skills, fabrication flags) are never exposed; see store.py.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .store import (
    CaseStudy,
    EvidenceFilters,
    PortfolioStore,
    Profile,
    SearchResult,
    SkillList,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def create_server(store: PortfolioStore | None = None, *, host: str = "127.0.0.1", port: int = 9101) -> FastMCP:
    store = store or PortfolioStore()
    mcp = FastMCP(
        "gigbid-portfolio",
        instructions=(
            "Read-only evidence about ONE freelancer. Use search_evidence to find proof for a job requirement, then "
            "cite evidence_id for every claim. If nothing relevant comes back, say so: never invent experience. "
            "list_skills shows which declared skills have no evidence behind them."
        ),
        host=host,
        port=port,
    )

    @mcp.tool(annotations=READ_ONLY)
    def search_evidence(
        requirement: str,
        k: int = 5,
        filters: EvidenceFilters | None = None,
        freelancer_id: str | None = None,
    ) -> SearchResult:
        """Find portfolio evidence (case studies, repo READMEs, code, ...) that supports one job requirement.

        Pass ONE requirement in plain language (e.g. 'built RAG over support articles with citations').
        Returns up to k hits ranked by relevance, each with evidence_id, type, date and the best-matching snippet.
        Use `filters` to narrow by type, project, client industry, date or mentioned skills.
        An empty `results` list means no evidence: do not claim the experience.
        """
        return store.search_evidence(requirement, k, filters, freelancer_id)

    @mcp.tool(annotations=READ_ONLY)
    def get_case_study(case_id: str, freelancer_id: str | None = None) -> CaseStudy:
        """Fetch one full case study with its metadata, client reviews and all related evidence ids.

        `case_id` is a project slug ('support-rag-bot') or any evidence_id of that project
        ('ai_ml:support-rag-bot:readme'). Use it after search_evidence to read the whole story before citing numbers.
        """
        return store.get_case_study(case_id, freelancer_id)

    @mcp.tool(annotations=READ_ONLY)
    def list_skills(freelancer_id: str | None = None) -> SkillList:
        """List declared skills, which of them are backed by evidence (with projects), and which are not.

        Skills in `declared_without_evidence` may be mentioned as interests but must not be claimed as proven.
        """
        return store.list_skills(freelancer_id)

    @mcp.tool(annotations=READ_ONLY)
    def get_profile(freelancer_id: str | None = None) -> Profile:
        """Headline, hourly rate and minimum project budget (inputs for pricing and the bid/skip decision)."""
        return store.get_profile(freelancer_id)

    @mcp.resource("portfolio://case/{case_id}", mime_type="text/markdown")
    def case_resource(case_id: str) -> str:
        """A case study as markdown (project slug or evidence id)."""
        return store.get_case_study(case_id).markdown

    @mcp.resource("portfolio://proposal/{proposal_id}", mime_type="application/json")
    def proposal_resource(proposal_id: str) -> str:
        """A past proposal with its outcome (won / replied / ignored). Eval labels are not included."""
        return store.get_past_proposal(proposal_id).model_dump_json(indent=2)

    @mcp.resource("portfolio://profile/{freelancer_id}", mime_type="application/json")
    def profile_resource(freelancer_id: str) -> str:
        """Public profile facts for one freelancer."""
        return store.get_profile(freelancer_id).model_dump_json(indent=2)

    @mcp.prompt()
    def ground_claim(claim: str) -> str:
        """Check one proposed sentence against the portfolio before it goes into a proposal."""
        return (
            f"Claim to verify: {claim!r}\n\n"
            "1. Call search_evidence with the claim's core requirement.\n"
            "2. Label the claim supported / partially supported / unsupported using ONLY returned snippets.\n"
            "3. If supported, cite the evidence_id. If partial, rewrite it to what the evidence shows. "
            "If unsupported, delete it. Never keep a number that does not appear in the evidence."
        )

    @mcp.prompt()
    def summarize_fit(job_requirements: str) -> str:
        """Summarise how well the portfolio covers a job's requirements."""
        return (
            f"Job requirements:\n{job_requirements}\n\n"
            "For each requirement: call search_evidence, then report the best evidence_id and one sentence on how "
            "strong it is. Finish with list_skills output for any requirement with no evidence."
        )

    return mcp


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    mcp = create_server(
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORTFOLIO_PORT", os.environ.get("MCP_PORT", "9101"))),
    )
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
