"""Hand-authored ground truth for the three synthetic freelancer personas.

Every project is stored ONCE as structured facts. All documents (case study, repo README + code, CV bullet,
client review, past proposals) are rendered from these facts in `render.py`, so gold evidence labels are known
by construction. `skills` are named in prose; `implied` skills appear only in code / stack lines / reviews, which
is what defeats naive RAG (assignment Part B). All people, clients and numbers are invented.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Project:
    slug: str
    title: str
    industry: str
    year: int
    problem: str            # noun phrase after "needed"
    solution: tuple[str, ...]   # past-tense fragments, "built a ..."
    skills: tuple[str, ...]     # named in prose
    implied: tuple[str, ...]    # only visible in code / stack lines
    metric: str             # past-tense verb phrase, e.g. "deflected 38% of tickets"
    repo: bool = True
    team: str = "solo"

    @property
    def true_skills(self) -> tuple[str, ...]:
        return self.skills + self.implied


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    headline: str
    hourly_rate: int
    floor_fixed: int
    years: int
    domain_words: tuple[str, ...]      # used to build job titles
    projects: tuple[Project, ...] = field(default_factory=tuple)

    def all_skills(self) -> list[str]:
        seen: dict[str, None] = {}
        for p in self.projects:
            for s in p.true_skills:
                seen.setdefault(s, None)
        return list(seen)


def P(slug, title, industry, year, problem, solution, skills, implied, metric, repo=True, team="solo"):
    return Project(slug, title, industry, year, problem, tuple(solution), tuple(skills), tuple(implied), metric, repo, team)


AI_ML = Persona(
    id="ai_ml", name="Priya Nair (synthetic)", headline="AI/ML Engineer: RAG, NLP and applied ML", hourly_rate=55,
    floor_fixed=300, years=6, domain_words=("AI", "machine learning", "LLM", "NLP", "model"),
    projects=(
        P("support-rag-bot", "Support assistant for a B2B SaaS helpdesk", "SaaS", 2025,
          "to cut repeat support tickets without hallucinated answers",
          ["built a retrieval-augmented assistant over 4,000 help articles", "added answer citations and a refusal path when nothing relevant was found"],
          ["RAG", "LangChain", "FastAPI"], ["pgvector", "BM25", "reranking"], "deflected 38% of tier-1 tickets within two months"),
        P("telco-churn", "Churn prediction for a mobile operator", "Telecom", 2022,
          "to find subscribers likely to leave in the next 30 days",
          ["engineered 60 behavioural features from billing and usage logs", "trained and calibrated a gradient boosted model"],
          ["scikit-learn", "XGBoost", "feature engineering"], ["SHAP", "imbalanced-learn"], "reached 0.86 AUC and lifted retention-campaign ROI by 22%"),
        P("invoice-extraction", "Invoice data extraction for an accounting firm", "Finance", 2023,
          "to stop re-typing supplier invoices by hand",
          ["built a document pipeline that reads scanned invoices", "validated totals against line items before export"],
          ["OCR", "PyTorch", "document AI"], ["LayoutLM", "Hugging Face Transformers", "Tesseract"], "reached 96% field-level accuracy and saved about 30 staff hours a week"),
        P("retail-demand-forecast", "Demand forecasting for a grocery chain", "Retail", 2022,
          "to reduce stock-outs and spoilage across 120 stores",
          ["built per-store, per-category forecasts with holiday effects", "tracked every experiment so results were reproducible"],
          ["time series", "forecasting", "Python"], ["Prophet", "statsmodels", "MLflow"], "cut forecast error (MAPE) from 24% to 15%"),
        P("llm-eval-harness", "LLM evaluation harness for a legal-tech startup", "Legal", 2025,
          "to know whether a prompt or model change made answers better or worse",
          ["built a regression suite of 300 graded questions", "ran it on every pull request"],
          ["LLM evaluation", "prompt engineering", "Python"], ["pytest", "OpenAI API", "RAGAS"], "caught three silent quality regressions before release"),
        P("shop-recsys", "Product recommender for an online store", "E-commerce", 2023,
          "to show relevant products on a cold-start catalogue",
          ["trained a two-tower retrieval model on click logs", "served nearest-neighbour lookups with sub-50 ms latency"],
          ["recommender systems", "PyTorch"], ["FAISS", "Redis"], "increased click-through by 14% in an A/B test"),
        P("review-sentiment", "App-review sentiment and topic mining", "Mobile apps", 2021,
          "to understand why ratings dropped after a release",
          ["fine-tuned a transformer classifier on 25,000 labelled reviews", "grouped complaints into topics for the product team"],
          ["NLP", "sentiment analysis", "Hugging Face"], ["spaCy", "scikit-learn"], "pinpointed the crash cause within a day and recovered a 0.4 star drop"),
        P("defect-vision", "Visual defect detection on a production line", "Manufacturing", 2023,
          "to catch surface defects that human inspectors missed at line speed",
          ["trained an object detector on 9,000 annotated images", "exported the model for an edge device"],
          ["computer vision", "OpenCV"], ["YOLO", "ONNX"], "detected 94% of defects at 25 frames per second"),
        P("call-summaries", "Call transcription and summaries for a sales team", "Sales", 2024,
          "to stop reps from writing call notes manually",
          ["transcribed recorded calls and produced structured CRM summaries", "processed audio in background jobs"],
          ["speech-to-text", "Whisper", "LLM"], ["Celery", "S3"], "saved reps roughly 5 hours a week each"),
        P("fraud-anomaly", "Transaction anomaly detection for a payments company", "Fintech", 2022,
          "to flag suspicious transactions without drowning analysts in alerts",
          ["scored transactions in near real time", "tuned thresholds with the risk team"],
          ["anomaly detection", "scikit-learn"], ["Isolation Forest", "Kafka"], "raised precision of alerts from 31% to 58%"),
        P("contract-qa", "Contract question answering for a law firm", "Legal", 2024,
          "to let lawyers query hundreds of contracts in plain English",
          ["indexed contracts by clause with metadata filters", "added a reranking step and page-level citations"],
          ["RAG", "prompt engineering"], ["Pinecone", "Cohere rerank"], "lawyers found the right clause on the first try in 81% of test queries"),
        P("model-deploy-pipeline", "Model deployment pipeline for a health-tech team", "Healthcare", 2023,
          "to move models from notebooks to monitored production",
          ["containerised training and serving", "added automated tests and drift alerts"],
          ["MLOps", "Docker", "MLflow"], ["GitHub Actions", "Kubernetes"], "cut release time from two weeks to one day"),
        P("text-to-sql-agent", "Natural-language analytics agent", "Logistics", 2025,
          "to let operations managers ask questions of the warehouse database",
          ["built an agent that writes and checks read-only SQL", "added a schema-aware retry loop"],
          ["LLM agents", "SQL"], ["LangGraph", "PostgreSQL"], "answered 87% of benchmark questions correctly"),
        P("dynamic-pricing", "Dynamic pricing model for a rentals marketplace", "Marketplace", 2021,
          "to set nightly prices that fill empty dates without underpricing",
          ["modelled demand by neighbourhood and season", "shipped a nightly batch job"],
          ["regression", "Python", "pricing"], ["LightGBM", "Airflow"], "raised revenue per listing by 9%"),
        P("semantic-job-search", "Semantic search for a recruiting platform", "HR tech", 2024,
          "to match candidates to roles beyond keyword overlap",
          ["embedded profiles and job posts", "blended keyword and vector scores"],
          ["embeddings", "semantic search"], ["sentence-transformers", "Elasticsearch"], "improved recruiter shortlist acceptance by 19%", team="team of 3"),
        P("hr-faq-chatbot", "Internal HR assistant for a 600-person company", "HR", 2023,
          "to answer policy questions quickly and escalate sensitive ones",
          ["built an intent-routed assistant with a human hand-off", "logged every unanswered question for content fixes"],
          ["chatbot", "NLP"], ["Rasa", "FastAPI"], "handled 62% of questions without HR involvement"),
    ),
)

FULLSTACK = Persona(
    id="fullstack_web", name="Daniel Okafor (synthetic)", headline="Full-stack web developer: React, Node and Django", hourly_rate=45,
    floor_fixed=250, years=7, domain_words=("web app", "website", "dashboard", "API", "platform"),
    projects=(
        P("saas-analytics-dashboard", "Analytics dashboard for a subscription SaaS", "SaaS", 2024,
          "to give customers self-serve usage reports",
          ["built a responsive dashboard with role-based views", "paginated and cached heavy report queries"],
          ["React", "Next.js", "TypeScript"], ["Tailwind CSS", "Prisma", "PostgreSQL"], "cut report load time from 9 s to 1.2 s"),
        P("booking-platform", "Online booking platform for a wellness studio", "Wellness", 2022,
          "to replace phone bookings with online scheduling and payment",
          ["built the booking flow with deposits", "sent reminders from a background queue"],
          ["Node.js", "Stripe", "REST API"], ["Express", "Redis", "BullMQ"], "moved 70% of bookings online and halved no-shows"),
        P("rental-marketplace-mvp", "Two-sided marketplace MVP for equipment rental", "Marketplace", 2023,
          "to launch a marketplace in ten weeks on a small budget",
          ["delivered listings, messaging, and escrow-style payouts", "wrote admin tooling for support"],
          ["Django", "PostgreSQL", "marketplace"], ["Celery", "Stripe Connect"], "launched on schedule and reached 400 listings in the first quarter"),
        P("realtime-chat", "Realtime chat for a telehealth portal", "Healthcare", 2023,
          "to let patients and clinicians message securely in real time",
          ["added presence, typing indicators and read receipts", "kept messages encrypted at rest"],
          ["WebSockets", "Node.js", "React"], ["Socket.IO", "Redis"], "supported 2,000 concurrent users with p95 latency under 120 ms"),
        P("headless-shop", "Headless storefront for a furniture brand", "E-commerce", 2024,
          "to make the shop faster and easier for marketing to change",
          ["rebuilt the storefront on a headless commerce backend", "added incremental static regeneration"],
          ["Next.js", "Shopify"], ["GraphQL", "Vercel"], "improved mobile Lighthouse score from 48 to 94 and conversion by 11%"),
        P("ops-admin-panel", "Internal admin panel for a logistics company", "Logistics", 2021,
          "to replace spreadsheets used to dispatch drivers",
          ["built CRUD screens with audit history", "secured the API with role permissions"],
          ["React", "REST API", "authentication"], ["React Query", "JWT"], "dispatch time per order dropped by 40%"),
        P("content-site-cms", "Content site with a custom CMS for a publisher", "Media", 2022,
          "to let editors publish without developer help",
          ["set up a headless CMS with preview and scheduling", "optimised images and caching"],
          ["CMS", "Node.js", "SEO"], ["Strapi", "CDN"], "editors now publish 3x more articles per week"),
        P("clinic-crm", "Lightweight CRM for a dental group", "Healthcare", 2022,
          "to track leads and recalls across eight clinics",
          ["built contact, pipeline and reminder modules", "imported ten years of spreadsheet data"],
          ["Django", "CRM", "Python"], ["PostgreSQL", "Celery"], "recall appointments rose 25%"),
        P("fintech-onboarding", "Customer onboarding flow for a neo-bank", "Fintech", 2024,
          "to shorten signup while meeting identity-check rules",
          ["built a multi-step onboarding with document upload", "integrated an identity verification provider"],
          ["React", "FastAPI", "forms"], ["Docker", "PostgreSQL"], "signup completion rose from 52% to 71%", team="team of 4"),
        P("offline-pwa", "Offline-first field app for inspectors", "Construction", 2023,
          "to collect inspection data in places with no signal",
          ["built a progressive web app with local storage and sync", "resolved edit conflicts predictably"],
          ["PWA", "TypeScript", "offline-first"], ["IndexedDB", "service workers"], "inspectors finished reports on site instead of the next day"),
        P("sso-auth", "Single sign-on and permissions for a B2B portal", "SaaS", 2023,
          "to let enterprise customers sign in with their own identity provider",
          ["added SAML/OIDC login and organisation-level roles", "wrote migration scripts for existing users"],
          ["OAuth", "authentication", "Node.js"], ["Passport.js", "JWT"], "closed two enterprise deals that required SSO"),
        P("cicd-aws-setup", "Deployment pipeline and hosting for a startup", "Startup", 2022,
          "to deploy safely several times a day instead of monthly",
          ["containerised the services and wrote the pipeline", "added staging environments and rollbacks"],
          ["Docker", "CI/CD", "AWS"], ["GitHub Actions", "Terraform"], "deployments went from monthly to daily with no downtime"),
        P("perf-audit", "Performance audit for a news site", "Media", 2024,
          "to fix slow pages that hurt search ranking",
          ["profiled render-blocking assets and slow queries", "added caching and image optimisation"],
          ["web performance", "Next.js", "SEO"], ["Redis", "Lighthouse"], "largest contentful paint fell from 4.8 s to 1.9 s"),
        P("integration-hub", "Integration hub connecting a CRM, billing and support", "SaaS", 2023,
          "to keep customer data consistent across three tools",
          ["built webhook receivers and a retry queue", "made every sync idempotent"],
          ["REST API", "webhooks", "Node.js"], ["BullMQ", "idempotency keys"], "removed about 20 hours a week of manual data entry"),
        P("marketing-landing-pages", "Landing page system for a growth team", "Marketing", 2021,
          "to launch campaign pages without waiting on engineering",
          ["built reusable sections and a theme system", "wired in analytics events"],
          ["HTML", "CSS", "JavaScript"], ["Tailwind CSS", "Google Tag Manager"], "campaign launch time dropped from a week to a day"),
        P("graphql-gateway", "GraphQL gateway for a mobile app backend", "Mobile apps", 2024,
          "to stop the mobile team over-fetching from many services",
          ["designed a schema over six REST services", "added batching and query cost limits"],
          ["GraphQL", "Node.js", "API design"], ["Apollo Server", "DataLoader"], "mobile screen load requests fell from 14 to 2"),
    ),
)

ANALYST = Persona(
    id="data_analyst", name="Sofia Marchetti (synthetic)", headline="Data analyst: SQL, Power BI and experimentation", hourly_rate=40,
    floor_fixed=200, years=5, domain_words=("data analysis", "dashboard", "reporting", "SQL", "analytics"),
    projects=(
        P("sales-powerbi", "Executive sales dashboard for a distributor", "Distribution", 2023,
          "to replace a monthly slide deck with a live dashboard",
          ["modelled sales, margin and targets in a star schema", "published row-level-secured reports"],
          ["Power BI", "DAX", "dashboards"], ["SQL Server", "Power Query"], "cut month-end reporting from three days to two hours"),
        P("cohort-retention", "Cohort and retention analysis for a subscription app", "SaaS", 2024,
          "to understand why trial users did not convert",
          ["built weekly cohort tables and funnel views", "documented every metric definition"],
          ["SQL", "cohort analysis", "retention"], ["dbt", "BigQuery"], "found an onboarding drop-off that lifted conversion by 8%"),
        P("ab-testing-program", "A/B testing programme for an e-commerce team", "E-commerce", 2024,
          "to stop shipping changes on gut feeling",
          ["set up sample-size planning and guardrail metrics", "reviewed 30 experiments with the product team"],
          ["A/B testing", "statistics"], ["scipy", "Python"], "invalidated two false wins and saved an estimated checkout redesign"),
        P("marketing-attribution", "Marketing channel attribution for a D2C brand", "Retail", 2023,
          "to learn which channels really drove first purchases",
          ["combined ad spend with web analytics", "compared last-click with position-based models"],
          ["Google Analytics", "Looker Studio", "attribution"], ["GA4 BigQuery export", "SQL"], "reallocated 15% of spend and lowered cost per acquisition by 18%"),
        P("finance-excel-model", "Monthly finance reporting automation", "Finance", 2021,
          "to speed up consolidation across five entities",
          ["rebuilt the workbook with structured tables and checks", "automated imports"],
          ["Excel", "VBA", "financial reporting"], ["Power Query", "pivot tables"], "reduced close time by two working days"),
        P("etl-ops-pipeline", "ETL pipeline for operations data", "Logistics", 2022,
          "to combine data from four systems into one reporting layer",
          ["wrote scheduled extraction and cleaning jobs", "added data quality tests"],
          ["ETL", "Python", "data cleaning"], ["pandas", "Airflow", "PostgreSQL"], "reports were refreshed nightly with zero manual steps"),
        P("tableau-ops-kpis", "Operations KPI views in Tableau", "Manufacturing", 2022,
          "to give plant managers a daily view of output and downtime",
          ["designed drill-down dashboards", "used level-of-detail calculations for rolling averages"],
          ["Tableau", "data visualization"], ["LOD expressions"], "plant managers adopted the dashboard in daily stand-ups"),
        P("customer-survey-r", "Customer satisfaction survey analysis", "Hospitality", 2021,
          "to turn 8,000 survey responses into decisions",
          ["cleaned and weighted the responses", "tested differences between customer segments"],
          ["R", "statistics", "survey analysis"], ["ggplot2", "dplyr"], "identified three service issues that explained 60% of detractors"),
        P("inventory-forecast-sheets", "Inventory planning model for a wholesaler", "Wholesale", 2022,
          "to set reorder points for 1,500 SKUs",
          ["built seasonal forecasts and safety-stock calculations", "flagged slow movers"],
          ["Excel", "forecasting", "inventory"], ["statsmodels", "Python"], "freed about 12% of working capital tied in stock"),
        P("customer-segmentation", "Customer segmentation for a loyalty programme", "Retail", 2023,
          "to target offers by behaviour instead of demographics",
          ["built RFM features and clustered customers", "described each segment for marketers"],
          ["clustering", "Python", "segmentation"], ["scikit-learn", "k-means"], "targeted offers raised redemption rates by 27%"),
        P("dbt-warehouse", "Analytics warehouse for a fintech", "Fintech", 2024,
          "to give finance and product one trusted set of metrics",
          ["modelled staging and mart layers with tests", "documented lineage"],
          ["dbt", "Snowflake", "SQL"], ["Git", "data modelling"], "removed conflicting revenue numbers between teams", team="team of 3"),
        P("kpi-scorecard-looker", "Company KPI scorecard", "SaaS", 2024,
          "to align leadership on five numbers every Monday",
          ["defined metrics with owners", "built a scorecard with alerts"],
          ["Looker Studio", "KPIs", "SQL"], ["BigQuery"], "leadership meetings shifted from debating numbers to acting on them"),
        P("public-data-ingest", "Public statistics ingestion for a policy think-tank", "Research", 2022,
          "to keep a research dashboard current from official open-data APIs",
          ["pulled data through documented public APIs on a polite schedule", "validated and versioned each release"],
          ["API integration", "Python", "data pipelines"], ["pandas", "requests"], "researchers got updates within an hour of publication"),
        P("hr-attrition", "Employee attrition analysis", "HR", 2023,
          "to understand which teams were losing people and why",
          ["joined HR records with engagement survey data", "built a Power BI report for HR business partners"],
          ["Power BI", "HR analytics", "statistics"], ["Excel", "logistic regression"], "highlighted two managers' teams with triple the average attrition"),
        P("supply-chain-sql", "Supplier delivery performance analysis", "Supply chain", 2022,
          "to measure on-time delivery by supplier",
          ["wrote SQL views for lead time and fill rate", "built a monthly supplier scorecard"],
          ["SQL", "supply chain", "KPIs"], ["PostgreSQL", "Excel"], "renegotiated terms with the five worst suppliers"),
        P("clinic-quality-cleaning", "Patient record cleaning for a quality study", "Healthcare", 2021,
          "to prepare messy records for a quality-improvement study",
          ["deduplicated and standardised fields", "produced a reproducible cleaning log"],
          ["data cleaning", "Python", "healthcare data"], ["pandas", "Jupyter"], "reduced unusable records from 18% to 2%"),
    ),
)

PERSONAS: dict[str, Persona] = {p.id: p for p in (AI_ML, FULLSTACK, ANALYST)}
