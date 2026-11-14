"""Render documents from persona facts. Pure functions of (project, rng): deterministic and offline."""
from __future__ import annotations

import random

from .personas import Persona, Project

# ---------------------------------------------------------------- code snippets (reveal implied skills) --------
PY_SNIPPETS = {
    "pgvector": "from pgvector.psycopg import register_vector  # vector column for embeddings",
    "bm25": "from rank_bm25 import BM25Okapi",
    "reranking": "from sentence_transformers import CrossEncoder\nreranker = CrossEncoder('BAAI/bge-reranker-base')",
    "shap": "import shap\nexplainer = shap.TreeExplainer(model)",
    "imbalanced-learn": "from imblearn.over_sampling import SMOTE",
    "layoutlm": "from transformers import LayoutLMv3ForTokenClassification",
    "hugging face transformers": "from transformers import AutoTokenizer, AutoModel",
    "tesseract": "import pytesseract",
    "prophet": "from prophet import Prophet",
    "statsmodels": "import statsmodels.api as sm",
    "mlflow": "import mlflow\nmlflow.log_metric('mape', mape)",
    "pytest": "import pytest",
    "openai api": "from openai import OpenAI\nclient = OpenAI()",
    "ragas": "from ragas import evaluate",
    "faiss": "import faiss\nindex = faiss.IndexFlatIP(dim)",
    "redis": "import redis\ncache = redis.Redis()",
    "spacy": "import spacy\nnlp = spacy.load('en_core_web_sm')",
    "scikit-learn": "from sklearn.ensemble import RandomForestClassifier",
    "yolo": "from ultralytics import YOLO\nmodel = YOLO('defects.pt')",
    "onnx": "import onnxruntime as ort",
    "celery": "from celery import Celery\napp = Celery('jobs', broker='redis://')",
    "s3": "import boto3\ns3 = boto3.client('s3')",
    "isolation forest": "from sklearn.ensemble import IsolationForest",
    "kafka": "from confluent_kafka import Consumer",
    "pinecone": "from pinecone import Pinecone",
    "cohere rerank": "import cohere\nco = cohere.Client()",
    "github actions": "# .github/workflows/train.yml builds and pushes the model image",
    "kubernetes": "# k8s/deployment.yaml: 3 replicas, readiness probe on /healthz",
    "langgraph": "from langgraph.graph import StateGraph",
    "postgresql": "import psycopg",
    "lightgbm": "import lightgbm as lgb",
    "airflow": "from airflow import DAG",
    "sentence-transformers": "from sentence_transformers import SentenceTransformer",
    "elasticsearch": "from elasticsearch import Elasticsearch",
    "rasa": "# domain.yml: intents, slots and fallback policy for Rasa",
    "fastapi": "from fastapi import FastAPI\napp = FastAPI()",
    "pandas": "import pandas as pd",
    "scipy": "from scipy import stats",
    "dbt": "-- models/marts/fct_orders.sql (dbt model)",
    "bigquery": "from google.cloud import bigquery",
    "ggplot2": "library(ggplot2)",
    "dplyr": "library(dplyr)",
    "k-means": "from sklearn.cluster import KMeans",
    "requests": "import requests",
    "jupyter": "# analysis.ipynb: cleaning steps with before/after counts",
    "ga4 bigquery export": "SELECT event_name FROM `proj.analytics_123.events_*`",
    "git": "# models are versioned and reviewed through pull requests",
    "data modelling": "-- star schema: dim_customer, dim_date, fct_sales",
    "sql": "SELECT customer_id, COUNT(*) FROM orders GROUP BY 1;",
    "sql server": "-- T-SQL view feeding the Power BI dataset",
    "power query": "// Power Query M: Table.TransformColumnTypes",
    "pivot tables": "' VBA: refresh all pivot caches before export",
    "lod expressions": "{ FIXED [Plant] : SUM([Downtime]) }",
    "excel": "# openpyxl writes the reviewed workbook",
    "logistic regression": "from sklearn.linear_model import LogisticRegression",
}
TS_SNIPPETS = {
    "tailwind css": "<div className=\"grid gap-4 md:grid-cols-3\">",
    "prisma": "import { PrismaClient } from '@prisma/client'",
    "postgresql": "import { Pool } from 'pg'",
    "express": "import express from 'express'",
    "redis": "import { createClient } from 'redis'",
    "bullmq": "import { Queue } from 'bullmq'",
    "celery": "# worker: celery -A app worker --loglevel=info",
    "stripe connect": "stripe.transfers.create({ destination: seller.stripeAccount })",
    "socket.io": "import { Server } from 'socket.io'",
    "graphql": "import { GraphQLClient } from 'graphql-request'",
    "vercel": "// vercel.json: regions and ISR revalidate settings",
    "react query": "import { useQuery } from '@tanstack/react-query'",
    "jwt": "import jwt from 'jsonwebtoken'",
    "strapi": "// strapi content-types: article, author, category",
    "cdn": "// Cache-Control: public, max-age=31536000, immutable",
    "docker": "FROM node:20-slim",
    "indexeddb": "import { openDB } from 'idb'",
    "service workers": "self.addEventListener('fetch', handleFetch)",
    "passport.js": "import passport from 'passport'",
    "github actions": "# .github/workflows/deploy.yml: build, test, deploy",
    "terraform": "# main.tf: aws_ecs_service with rolling deployment",
    "lighthouse": "// lighthouse-ci budget: LCP < 2.5 s",
    "idempotency keys": "headers['Idempotency-Key'] = event.id",
    "google tag manager": "window.dataLayer.push({ event: 'cta_click' })",
    "apollo server": "import { ApolloServer } from '@apollo/server'",
    "dataloader": "import DataLoader from 'dataloader'",
}


def _snip(skill: str, snippets: dict[str, str]) -> str:
    return snippets.get(skill.lower(), f"# uses {skill}")


def code_files(persona_id: str, p: Project) -> dict[str, str]:
    """Code that reveals implied skills but never states them in prose."""
    ts = persona_id == "fullstack_web"
    snippets = TS_SNIPPETS if ts else PY_SNIPPETS
    ext = "ts" if ts else "py"
    lines = [f"// {p.slug}" if ts else f"# {p.slug}"]
    lines += [_snip(s, snippets) for s in p.implied]
    lines += [_snip(s, snippets) for s in p.skills if s.lower() in snippets]
    return {f"src/main.{ext}": "\n".join(lines) + "\n"}


# -------------------------------------------------------------------------------- documents ---------------------
def _month(rng: random.Random, year: int) -> str:
    return f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def project_date(rng: random.Random, p: Project) -> str:
    return _month(rng, p.year)


def case_study_md(persona: Persona, p: Project, rng: random.Random, date: str) -> str:
    style = rng.choice(("cs", "story", "brief"))
    sol = "\n".join(f"- {s[0].upper() + s[1:]}." for s in p.solution)
    skills = ", ".join(p.skills)
    front = (f"---\npersona: {persona.id}\ntype: case_study\nproject: {p.slug}\nskills: [{skills}]\n"
             f"date: {date}\nclient_industry: {p.industry}\nsynthetic: true\n---\n")
    if style == "cs":
        body = (f"# {p.title}\n\n**Industry:** {p.industry} · **Team:** {p.team} · **Year:** {p.year}\n\n"
                f"## Challenge\nThe client needed {p.problem}.\n\n## Approach\n{sol}\n\n"
                f"## Result\nThe work {p.metric}.\n\n**Skills:** {skills}\n")
    elif style == "story":
        body = (f"# {p.title}\n\nA {p.industry.lower()} client came to me because they needed {p.problem}. "
                f"Working {p.team}, I {p.solution[0]}. Then I {p.solution[-1]}.\n\n"
                f"Outcome: it {p.metric}. Main skills used: {skills}.\n")
    else:
        body = (f"# {p.title} ({p.year})\n\nGoal: {p.problem}.\n\nWhat I did: {'; '.join(p.solution)}.\n\n"
                f"Impact: {p.metric}.\n\nSkills: {skills}\n")
    return front + body


def readme_md(persona: Persona, p: Project, rng: random.Random) -> str:
    """READMEs are intentionally terse/generic half the time: the code reveals the skills."""
    name = p.slug
    install = "## Setup\n```\npip install -r requirements.txt\n```\n" if persona.id != "fullstack_web" else "## Setup\n```\nnpm install && npm run dev\n```\n"
    r = rng.random()
    if r < 0.4:
        return f"# {name}\n\n{install}\n## Notes\nSee `src/` for the implementation. Internal project, details under NDA.\n"
    if r < 0.75:
        stack = ", ".join(p.implied)
        return f"# {name}\n\n{p.solution[0][0].upper() + p.solution[0][1:]}.\n\n## Stack\n{stack}\n\n{install}"
    stack = ", ".join(p.skills + p.implied)
    return f"# {name}\n\n{p.title}.\n\n## Features\n" + "\n".join(f"- {s}" for s in p.solution) + f"\n\n## Stack\n{stack}\n\n{install}"


def cv_bullet(p: Project) -> str:
    return f"{p.title} ({p.year}): {p.solution[0]}; {p.metric}."


def cv_md(persona: Persona, projects: list[Project]) -> str:
    skills = ", ".join(persona.all_skills()[:24])
    parts = [f"# {persona.name}\n\n**{persona.headline}**  \nRate: ${persona.hourly_rate}/h · Experience: {persona.years} years · synthetic persona\n",
             "## Summary\n" + f"{persona.years} years delivering {persona.domain_words[0]} projects for small and mid-size teams. Prefers measurable outcomes and clear scoping.\n",
             "## Selected projects"]
    parts += [f"- {cv_bullet(p)}" for p in sorted(projects, key=lambda x: -x.year)[:10]]
    parts += ["\n## Skills\n" + skills + "\n", "## Education\nBSc Computer Science (synthetic)\n"]
    return "\n".join(parts)


REVIEW_OPEN = ("Great to work with.", "Delivered what was agreed.", "Very professional.", "Solid work and clear updates.", "Exactly what we needed.")
REVIEW_NEG = ("Communication was slow in the first week.", "Went slightly over the first estimate.", "Needed a few rounds of feedback on scope.")


def review(persona: Persona, p: Project, rng: random.Random, date: str) -> dict:
    rating = rng.choices([5.0, 4.8, 4.5, 4.0, 3.5], weights=[50, 20, 15, 10, 5])[0]
    first = persona.name.split()[0]
    text = f"{rng.choice(REVIEW_OPEN)} {first} {p.metric}."
    if rating < 4.8:
        text += " " + rng.choice(REVIEW_NEG)
    if p.implied and rng.random() < 0.5:  # client voice reveals implied skills without naming the catalogue term
        text += f" The {p.implied[0]} side was handled without us asking."
    return {"project": p.slug, "rating": rating, "text": text, "date": date, "client_industry": p.industry}


# ------------------------------------------------------------------------------------ proposals ---------------
FOREIGN_SKILLS = ("Unity", "SAP", "Kubernetes operators", "Swift", "COBOL", "Salesforce Apex", "Blender", "Solidity")
GENERIC_OPENERS = ("Hello, I am a highly motivated professional with many years of experience and I can do this job.",
                   "Dear client, I read your job post and I am the perfect fit. I am hardworking and reliable.",
                   "Hi, I have worked on many similar projects and can start immediately.")
FALSE_CLAIMS = ("I led a team of 25 engineers at a Fortune 500 company.", "I hold a PhD from a top university in this exact field.",
                "I have 12 years of experience with this technology.", "I built the platform used by 10 million users.")
FALSE_CLAIM_RATE = 0.08
WON_THRESHOLD = 1.80      # latent-score cut-offs tuned for ~8% won / ~28% replied / ~64% ignored
REPLY_THRESHOLD = 1.42


def _pick_projects(persona: Persona, rng: random.Random) -> list[Project]:
    k = rng.choices([1, 2, 3], weights=[40, 40, 20])[0]
    return rng.sample(list(persona.projects), k)


def make_job_for(persona: Persona, rng: random.Random, projects: list[Project]) -> dict:
    skills: list[str] = []
    for p in projects:
        pool = list(p.skills) + (list(p.implied) if rng.random() < 0.4 else [])
        skills += rng.sample(pool, k=min(len(pool), rng.randint(1, 3)))
    skills = list(dict.fromkeys(skills))[:5]
    mismatch = rng.random() < 0.2
    if mismatch:
        skills.append(rng.choice(FOREIGN_SKILLS))
    have = set(persona.all_skills())
    relevance = sum(s in have for s in skills) / len(skills)
    lead = projects[0]
    title = f"Need {rng.choice(persona.domain_words)} help {lead.problem}"
    return {"title": title[:110], "skills": skills, "industry": lead.industry, "relevance": round(relevance, 2),
            "problem_hint": lead.problem}


def proposal(persona: Persona, rng: random.Random, idx: int, date: str) -> dict:
    projects = _pick_projects(persona, rng)
    job = make_job_for(persona, rng, projects)
    opener_kind = rng.choices(["generic", "specific_problem", "question"], weights=[35, 40, 25])[0]
    ev_n = rng.choices([0, 1, 2, 3], weights=[20, 35, 30, 15])[0]
    ev_projects = projects[:ev_n]
    lead = projects[0]
    if opener_kind == "generic":
        opener = rng.choice(GENERIC_OPENERS)
    elif opener_kind == "specific_problem":
        opener = f"Your need {lead.problem} is close to what I handled for a {lead.industry} client, where the work {lead.metric}."
    else:
        opener = f"Quick question before I scope this: how will you measure success if the goal is {lead.problem}? On a similar {lead.industry} project the work {lead.metric}."
    claims = [{"text": opener, "supported_by": lead.slug if opener_kind != "generic" else None}]
    body = []
    for p in ev_projects:
        s = f"On the project '{p.title}', I used {p.skills[0]} and the work {p.metric}."
        body.append(s)
        claims.append({"text": s, "supported_by": p.slug})
    fabricated = rng.random() < FALSE_CLAIM_RATE
    if fabricated:
        fc = rng.choice(FALSE_CLAIMS)
        body.append(fc)
        claims.append({"text": fc, "supported_by": None, "fabricated": True})
    close = rng.choice(("I can share a short plan this week. When suits you?", "Happy to start with a small paid milestone. Shall we agree scope?",
                        "I can begin Monday. What is the most urgent deliverable?"))
    filler = " ".join(["I take quality seriously and communicate daily."] * rng.randint(0, 6))
    text = " ".join(x for x in [opener, *body, filler, close] if x)
    words = len(text.split())
    fair_mid = persona.floor_fixed * rng.uniform(1.0, 3.0)
    ratio = rng.uniform(0.7, 1.6)
    price = round(fair_mid * ratio / 5) * 5
    score = (0.9 * job["relevance"] + 0.5 * (opener_kind != "generic") + 0.35 * min(ev_n, 3) / 3
             + 0.2 * (120 <= words <= 220) - 0.3 * (ratio > 1.3) - 0.5 * fabricated + rng.gauss(0, 0.25))
    outcome = "won" if score > WON_THRESHOLD else "replied" if score > REPLY_THRESHOLD else "ignored"
    return {
        "proposal_id": f"{persona.id}-prop-{idx:03d}", "persona": persona.id, "date": date, "job": job, "text": text,
        "claims": claims, "bid_price": price, "outcome": outcome, "synthetic": True,
        "features": {"opener": opener_kind, "n_evidence": ev_n, "relevance": job["relevance"], "words": words,
                     "price_ratio": round(ratio, 2), "contains_fabricated_claim": fabricated},
    }
