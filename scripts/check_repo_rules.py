"""Repo-rule checks (assignment 8.2), run by CI: uv run python -m scripts.check_repo_rules

  1. no secrets: .env is not tracked; no tracked text file contains a provider key / private key pattern
  2. gigsim/ (instructor package) unchanged in a PR (skipped when gigsim/ is absent or no base ref is given)
  3. prompts live only in agent/prompts: no inline "You are ..." system prompt in application code
  4. every number source cited exists: each `evals/results/<file>` referenced in README.md / docs/*.md is a real file
Exit 1 with file:line for every violation.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(r"gsk_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}"
                    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
SKIP_SECRET_FILES = {"uv.lock", "package-lock.json"}
INLINE_PROMPT = re.compile(r"""(?:"{3}|'{3}|"|')\s*You are (?:a|an|the|Claude|GigBid)\b""")
PROMPT_SCAN_ROOTS = ("agent/", "apps/", "rag/", "worker/", "mcp_servers/")
PROMPT_SKIP = ("agent/prompts/", "tests/")
RESULT_REF = re.compile(r"evals/results/([A-Za-z0-9_.\-]+)")


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    return [ln for ln in out.stdout.splitlines() if ln]


def main() -> int:
    tracked = git("ls-files")
    if not tracked:
        print("not a git checkout: nothing to check")
        return 0
    problems: list[str] = []

    if ".env" in tracked:
        problems.append(".env is tracked by git")
    for f in tracked:
        p = ROOT / f
        if f in SKIP_SECRET_FILES or f.startswith("data/") or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if SECRET.search(line):
                problems.append(f"{f}:{i}: looks like a secret")
            if f.endswith(".py") and f.startswith(PROMPT_SCAN_ROOTS) and not f.startswith(PROMPT_SKIP) and INLINE_PROMPT.search(line):
                problems.append(f"{f}:{i}: inline system prompt (move it to agent/prompts/*.md)")

    base = os.environ.get("GITHUB_BASE_REF")
    if base and any(f.startswith("gigsim/") for f in tracked):
        changed = git("diff", "--name-only", f"origin/{base}...HEAD", "--", "gigsim")
        problems += [f"{f}: instructor gigsim/ must not change" for f in changed]

    docs = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for d in docs:
        if not d.exists():
            continue
        for i, line in enumerate(d.read_text(encoding="utf-8").splitlines(), 1):
            for name in RESULT_REF.findall(line):
                if not (ROOT / "evals" / "results" / name.rstrip(".,)")).exists():
                    problems.append(f"{d.relative_to(ROOT).as_posix()}:{i}: cites missing evals/results/{name}")

    for p in problems:
        print("VIOLATION", p)
    print(f"{len(problems)} violation(s)" if problems else "repo rules ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
