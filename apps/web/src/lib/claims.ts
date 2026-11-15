import type { Claim } from "./types";

export interface Segment {
  text: string;
  claim?: Claim;
}

export interface Segmented {
  segments: Segment[];
  /** Claims whose text could not be located in the letter (e.g. after a human edit). Shown separately, never silently dropped. */
  unplaced: Claim[];
}

/** Split a cover letter into plain text and claim spans so each claim can show its evidence. Pure: unit-tested. */
export function segment(text: string, claims: Claim[]): Segmented {
  const ranges: { start: number; end: number; claim: Claim }[] = [];
  const unplaced: Claim[] = [];
  const lower = text.toLowerCase();
  for (const claim of claims) {
    const needle = claim.text.trim();
    if (!needle) continue;
    let start = text.indexOf(needle);
    if (start < 0) start = lower.indexOf(needle.toLowerCase());
    if (start < 0) {
      unplaced.push(claim);
      continue;
    }
    const end = start + needle.length;
    if (ranges.some((r) => start < r.end && end > r.start)) continue; // overlapping claim: the first one wins
    ranges.push({ start, end, claim });
  }
  ranges.sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let pos = 0;
  for (const r of ranges) {
    if (r.start > pos) segments.push({ text: text.slice(pos, r.start) });
    segments.push({ text: text.slice(r.start, r.end), claim: r.claim });
    pos = r.end;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos) });
  return { segments, unplaced };
}

export function labelOf(c: Claim): "supported" | "partly_supported" | "unsupported" | "unchecked" {
  return c.label ?? (c.evidence_ids.length ? "supported" : "unchecked");
}
