"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useApp } from "@/lib/app";

const LINKS = [
  ["/inbox", "Inbox"], ["/pipeline", "Pipeline"], ["/messages", "Messages"], ["/runs", "Runs"], ["/analytics", "Analytics"], ["/settings", "Settings"],
] as const;

/** Degraded-mode banner: says what still works. Reads GET /health/ready (polled by AppProvider). */
export function DegradedBanner() {
  const { health, apiDown } = useApp();
  if (apiDown) {
    return (
      <div className="banner bad" role="status" data-testid="degraded-banner">
        <strong>API unreachable.</strong> Showing what is already on screen; actions are paused until it is back.
      </div>
    );
  }
  const d = health?.degraded ?? [];
  if (!d.length) return null;
  const parts: string[] = [];
  if (d.includes("marketplace")) parts.push("the marketplace is down or not connected (job browsing and submitting are paused; drafts, runs and analytics still work)");
  if (d.includes("models")) parts.push("no language model is configured (deterministic fallbacks are used)");
  if (d.includes("injection_classifier")) parts.push("the injection classifier is off (heuristic checks still run)");
  if (d.includes("database") || d.includes("checkpointer")) parts.push("storage is degraded (approvals may not persist)");
  return (
    <div className="banner warn" role="status" data-testid="degraded-banner">
      <strong>Degraded mode:</strong> {parts.join("; ")}.
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { user, pending, persona, signOut } = useApp();
  return (
    <div className="shell">
      <nav className="nav" aria-label="Main">
        <div className="brand">GigBid</div>
        {LINKS.map(([href, label]) => (
          <Link key={href} href={href} aria-current={path === href || path.startsWith(href + "/") ? "page" : undefined}>
            <span>{label}</span>
            {href === "/inbox" && pending > 0 ? <span className="badge warn" aria-label={`${pending} awaiting approval`}>{pending}</span> : null}
          </Link>
        ))}
        <div className="spacer" />
        <div className="small muted" style={{ padding: "0 .5rem" }}>
          {user.email}
          <br />
          Persona: {persona || "none"}
        </div>
        <button onClick={() => void signOut()}>Sign out</button>
      </nav>
      <div>
        <DegradedBanner />
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
