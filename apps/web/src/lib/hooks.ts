"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, toApiError } from "./http";
import type { TraceEvent } from "./types";

export interface AsyncState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
}

/** Load data, expose loading/error explicitly (every screen renders all three states), optionally poll. */
export function useAsync<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[], opts?: { every?: number; skip?: boolean }): AsyncState<T> {
  const [state, setState] = useState<{ data: T | null; error: ApiError | null; loading: boolean }>({ data: null, error: null, loading: !opts?.skip });
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (opts?.skip) {
      setState((s) => ({ ...s, loading: false }));
      return;
    }
    const ac = new AbortController();
    let alive = true;
    const run = async (initial: boolean) => {
      if (initial) setState((s) => ({ ...s, loading: true }));
      try {
        const d = await fnRef.current(ac.signal);
        if (alive) setState({ data: d, error: null, loading: false });
      } catch (e) {
        if (!alive || (e instanceof DOMException && e.name === "AbortError")) return;
        setState((s) => ({ data: s.data, error: toApiError(e), loading: false }));
      }
    };
    void run(true);
    const t = opts?.every ? setInterval(() => void run(false), opts.every) : undefined;
    return () => {
      alive = false;
      ac.abort();
      if (t) clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, opts?.skip, opts?.every]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}

export interface RunStream {
  events: TraceEvent[];
  status: string | null;
  done: boolean;
  live: boolean;
}

/** Live run progress over Server-Sent Events. The server replays from its database, so a refresh or a dropped connection
 *  (EventSource reconnects with Last-Event-ID) loses nothing; events are de-duplicated by seq. */
export function useRunStream(runId: string): RunStream {
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [live, setLive] = useState(false);
  useEffect(() => {
    setEvents([]);
    setStatus(null);
    setDone(false);
    const es = new EventSource(`/api/runs/${runId}/events`);
    es.onopen = () => setLive(true);
    es.onerror = () => setLive(false);
    es.addEventListener("node", (m) => {
      const e = JSON.parse((m as MessageEvent<string>).data) as TraceEvent;
      setEvents((prev) => (prev.some((p) => p.seq === e.seq) ? prev : [...prev, e].sort((a, b) => a.seq - b.seq)));
    });
    es.addEventListener("status", (m) => setStatus((JSON.parse((m as MessageEvent<string>).data) as { status: string }).status));
    es.addEventListener("done", (m) => {
      setStatus((JSON.parse((m as MessageEvent<string>).data) as { status: string }).status);
      setDone(true);
      setLive(false);
      es.close();
    });
    return () => es.close();
  }, [runId]);
  return { events, status, done, live };
}
