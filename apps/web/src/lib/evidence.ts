"use client";

import { useEffect, useState } from "react";
import { api, qs } from "./http";
import type { Evidence } from "./types";

const cache = new Map<string, Evidence | null>(); // `${persona}|${id}` -> item (null = looked up, not citable/unknown)

export async function loadEvidence(persona: string, ids: string[]): Promise<Evidence[]> {
  const need = ids.filter((i) => !cache.has(`${persona}|${i}`));
  if (need.length) {
    const rows = await api.get<Evidence[]>(`/personas/${encodeURIComponent(persona)}/evidence${qs({ ids: need.join(",") })}`);
    for (const id of need) cache.set(`${persona}|${id}`, null);
    for (const r of rows) cache.set(`${persona}|${r.evidence_id}`, r);
  }
  return ids.map((i) => cache.get(`${persona}|${i}`)).filter((x): x is Evidence => !!x);
}

export function useEvidence(persona: string, ids: string[], enabled: boolean) {
  const [items, setItems] = useState<Evidence[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = ids.join(",");
  useEffect(() => {
    if (!enabled || !ids.length) return;
    let alive = true;
    loadEvidence(persona, ids)
      .then((r) => alive && setItems(r))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona, key, enabled]);
  return { items, error, loading: enabled && ids.length > 0 && items === null && error === null };
}
