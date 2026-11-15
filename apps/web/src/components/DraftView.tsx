"use client";

import { segment, labelOf } from "@/lib/claims";
import type { Claim } from "@/lib/types";
import { EvidenceHover } from "./EvidenceHover";

const LEGEND: [string, string][] = [["supported", "Supported by your portfolio"], ["partly_supported", "Partly supported"], ["unsupported", "Not supported"], ["unchecked", "Not checked"]];

/** The cover letter with every factual claim underlined by its verification label; hover/focus shows the evidence it rests on. */
export function DraftView({ text, claims }: { text: string; claims: Claim[] }) {
  const { segments, unplaced } = segment(text, claims);
  return (
    <div className="stack">
      <div className="draft" data-testid="draft-text">
        {segments.map((s, i) =>
          s.claim ? (
            <EvidenceHover key={i} ids={s.claim.evidence_ids} className={labelOf(s.claim)} label={`Claim: ${labelOf(s.claim).replace("_", " ")}`}>
              {s.text}
            </EvidenceHover>
          ) : (
            <span key={i}>{s.text}</span>
          ),
        )}
      </div>
      <div className="row small muted" aria-label="Legend">
        {LEGEND.map(([k, v]) => (
          <span key={k}><span className={`claim ${k}`}>abc</span> {v}</span>
        ))}
      </div>
      {unplaced.length ? (
        <div className="alert info small">
          <strong>Claims that no longer appear verbatim in the text (edited?):</strong>
          <ul>{unplaced.map((c, i) => <li key={i}>{c.text} — {labelOf(c).replace("_", " ")}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}
