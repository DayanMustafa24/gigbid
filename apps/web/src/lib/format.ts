import type { Budget, Run } from "./types";

export function money(n: number | null | undefined, currency = "USD"): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return new Intl.NumberFormat("en-US", { style: "currency", currency, maximumFractionDigits: 0 }).format(n);
}

export function budgetText(b: Budget | null | undefined): string {
  if (!b) return "Budget n/a";
  const unit = b.type === "hourly" ? "/hr" : "";
  return b.min && b.min !== b.max ? `${money(b.min, b.currency)}–${money(b.max, b.currency)}${unit}` : `${money(b.max, b.currency)}${unit}`;
}

export function pct(n: number | null | undefined): string {
  return n === null || n === undefined ? "–" : `${Math.round(n * 100)}%`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

/** The run to show for a job: the most recent one (API lists newest first). */
export function latestRunByJob(runs: Run[]): Map<string, Run> {
  const m = new Map<string, Run>();
  for (const r of runs) if (r.kind === "bid" && !m.has(r.subject_id)) m.set(r.subject_id, r);
  return m;
}

export function slotLabel(s: Record<string, unknown>): string {
  const start = (s.start ?? s.starts_at ?? s.begin) as string | undefined;
  const end = (s.end ?? s.ends_at) as string | undefined;
  if (start) return end ? `${when(start)} → ${when(end)}` : when(start);
  return JSON.stringify(s);
}
