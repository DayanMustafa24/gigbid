"use client";

import type { ReactNode } from "react";
import type { ApiError } from "@/lib/http";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {what}…
    </p>
  );
}

export function ErrorBox({ error, retry }: { error: ApiError; retry?: () => void }) {
  const marketplace = error.code === "marketplace_unavailable";
  return (
    <div className="alert" role="alert">
      <strong>{marketplace ? "Marketplace unavailable." : "Something went wrong."}</strong>{" "}
      {marketplace ? "Browsing and submitting are paused; your drafts and past runs still work." : error.message}
      {error.retryable && retry ? (
        <>
          {" "}
          <button onClick={retry}>Try again</button>
        </>
      ) : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="card muted">
      <strong>{title}</strong>
      {children ? <div>{children}</div> : null}
    </div>
  );
}

/** One place for the three states every data screen must render. */
export function Async<T>({ state, children, what }: { state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void }; children: (d: T) => ReactNode; what?: string }) {
  if (state.data !== null) return <>{children(state.data)}</>;
  if (state.error) return <ErrorBox error={state.error} retry={state.reload} />;
  return <Loading what={what} />;
}
