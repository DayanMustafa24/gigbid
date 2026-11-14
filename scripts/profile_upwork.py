"""Profile the Kaggle Upwork jobs CSV and write a summary JSON (run: uv run python -m scripts.profile_upwork)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "processed"


def find_csv() -> Path:
    hits = sorted((ROOT / "data" / "external").rglob("upwork-jobs*.csv"))
    if not hits:
        raise SystemExit("No upwork-jobs*.csv under data/external/")
    return hits[0]


def main() -> None:
    p = find_csv()
    df = pd.read_csv(p, sep=None, engine="python")  # separator auto-detected (tab or comma)
    desc = df["description"].fillna("")
    has_cat = desc.str.contains(r"Category:", regex=True)
    has_skills = desc.str.contains(r"Skills:", regex=True)
    cats = desc.str.extract(r"Category:\s*(.*?)Skills:", flags=re.S)[0].str.strip()
    summary = {
        "file": str(p.relative_to(ROOT)),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "dates": [str(df["published_date"].min()), str(df["published_date"].max())],
        "hourly_share": float(df["is_hourly"].astype(str).str.upper().eq("TRUE").mean()),
        "budget_present": int(df["budget"].notna().sum()),
        "budget_median": float(df["budget"].median()) if df["budget"].notna().any() else None,
        "hourly_low_median": float(df["hourly_low"].median()) if df["hourly_low"].notna().any() else None,
        "country_present": int(df["country"].notna().sum()),
        "with_category": int(has_cat.sum()),
        "with_skills": int(has_skills.sum()),
        "duplicate_links": int(df["link"].duplicated().sum()),
        "duplicate_titles": int(df["title"].duplicated().sum()),
        "desc_chars_median": int(desc.str.len().median()),
        "top_categories": cats.value_counts().head(25).to_dict(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "upwork_profile.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
