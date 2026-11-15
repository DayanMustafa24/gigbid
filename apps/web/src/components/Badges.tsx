import { titleCase } from "@/lib/format";

const STATUS: Record<string, string> = {
  running: "", awaiting_approval: "warn", completed: "ok", skipped: "", closed: "", needs_attention: "bad", failed: "bad",
  bid: "ok", skip: "", watch: "warn",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <span className={`badge ${STATUS[value] ?? ""}`}>{titleCase(value)}</span>;
}

export function RiskChips({ flags }: { flags: string[] | undefined }) {
  if (!flags?.length) return <span className="badge ok">No risk flags</span>;
  return (
    <>
      {flags.map((f) => (
        <span key={f} className="badge bad" style={{ marginRight: 4 }}>
          {titleCase(f)}
        </span>
      ))}
    </>
  );
}

export function ScoreBar({ value, label }: { value: number | null | undefined; label: string }) {
  const v = value === null || value === undefined ? null : Math.max(0, Math.min(1, value));
  return (
    <div>
      <div className="row between small">
        <span>{label}</span>
        <span className="muted">{v === null ? "–" : `${Math.round(v * 100)}%`}</span>
      </div>
      <div className="bar" role="img" aria-label={`${label} ${v === null ? "unknown" : Math.round(v * 100) + "%"}`}>
        <i style={{ width: `${(v ?? 0) * 100}%` }} />
      </div>
    </div>
  );
}
