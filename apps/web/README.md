# GigBid web (Next.js 16 + React 19 + TypeScript)

Screens: login, onboarding, inbox, job detail (requirement-to-evidence matrix), proposal studio (claims, EvidenceHover, approve / edit / reject), pipeline,
messages, runs list + run viewer (live SSE trace), analytics, settings (+ memory viewer, right to be forgotten). A degraded-mode banner reads `/health/ready`.

Design rules
- Same-origin `/api` rewrite to FastAPI (`next.config.mjs`), so the session cookie stays first-party; SSE needs `compress: false`.
- Human approval is shown as a fixed rule, not a setting. Pending approvals live on the server and survive refresh.
- Types come from the generated contract `src/lib/api/schema.d.ts` (`npm run gen:api`, run automatically before dev/build/typecheck; commit the file).
- Plain CSS, no UI framework. Every interactive element is labelled; status is never colour-only.

Commands (from `apps/web`)
```
npm install
npm run typecheck        # regenerates the API types first (needs ../api/openapi.json: `make openapi` in the repo root)
npm run test:unit        # pure logic, no browser
npx playwright install chromium
npm run test:e2e         # API mocked at the network layer: no backend, DB or model keys needed
npm run build
npm run dev              # needs the API on :8000 (uv run uvicorn apps.api.main:app --port 8000)
```

Known gaps: regenerate-one-paragraph (no backend endpoint), uploading a portfolio from the browser (use the `rag.ingest` folder layout),
per-category analytics (the API aggregates per persona only), a "Viewed" pipeline column (the marketplace has no such status).
