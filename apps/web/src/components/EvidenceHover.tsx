"use client";

import { useState, type ReactNode } from "react";
import { useApp } from "@/lib/app";
import { useEvidence } from "@/lib/evidence";
import { titleCase } from "@/lib/format";

/** Popover with the portfolio evidence behind a claim (or a requirement match). Loads lazily on first open; keyboard and touch work (focus/click). */
export function EvidenceHover({ ids, className = "", label, children }: { ids: string[]; className?: string; label?: string; children: ReactNode }) {
  const { persona } = useApp();
  const [open, setOpen] = useState(false);
  const { items, error, loading } = useEvidence(persona, ids, open);
  const has = ids.length > 0;
  return (
    <span
      className={`claim ${className}`}
      tabIndex={0}
      role="button"
      aria-expanded={open}
      aria-label={label}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={() => setOpen((o) => !o)}
      onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
    >
      {children}
      {open ? (
        <span className="pop" role="tooltip">
          {!has ? <em>No portfolio evidence is linked to this.</em> : null}
          {loading ? <em>Loading evidence…</em> : null}
          {error ? <em>Could not load evidence: {error}</em> : null}
          {items?.map((e) => (
            <span key={e.evidence_id} style={{ display: "block", marginBottom: 6 }}>
              <strong>{titleCase(e.type)}</strong>
              {e.project_title ? <> · {e.project_title}</> : null}
              {e.client_industry ? <> · {e.client_industry}</> : null}
              <br />
              <span className="muted">{e.text}{e.truncated ? "…" : ""}</span>
              <br />
              <span className="mono small muted">{e.evidence_id}</span>
            </span>
          ))}
          {items && has && items.length === 0 && !loading ? <em>Evidence not found or not citable.</em> : null}
        </span>
      ) : null}
    </span>
  );
}
