"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, type ReactNode } from "react";
import { useAsync } from "./hooks";
import { api } from "./http";
import type { Health, Persona, Run, Settings, User } from "./types";

interface AppCtx {
  user: User;
  settings: Settings;
  personas: Persona[];
  persona: string;
  health: Health | null;
  apiDown: boolean;
  pending: number;
  refresh: () => void;
  signOut: () => Promise<void>;
}

const Ctx = createContext<AppCtx | null>(null);

export function useApp(): AppCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

/** Loads the session, settings, personas, readiness and the pending-approval count once for the whole signed-in area.
 *  Redirects: no session -> /login; no persona chosen yet -> /onboarding. */
export function AppProvider({ children, fallback }: { children: ReactNode; fallback: (msg: string) => ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const me = useAsync((s) => api.get<User>("/auth/me", s), []);
  const signedIn = !!me.data;
  const settings = useAsync((s) => api.get<Settings>("/settings", s), [signedIn], { skip: !signedIn });
  const personas = useAsync((s) => api.get<Persona[]>("/personas", s), [signedIn], { skip: !signedIn });
  const health = useAsync((s) => api.get<Health>("/health/ready", s), [], { every: 15000 });
  const approvals = useAsync((s) => api.get<Run[]>("/approvals", s), [signedIn], { every: 10000, skip: !signedIn });

  useEffect(() => {
    if (me.error?.status === 401) router.replace("/login");
  }, [me.error, router]);
  const needsOnboarding = !!settings.data && !settings.data.default_persona;
  useEffect(() => {
    if (needsOnboarding && path !== "/onboarding") router.replace("/onboarding");
  }, [needsOnboarding, path, router]);

  const refresh = settings.reload;
  const signOut = useCallback(async () => {
    await api.post("/auth/logout");
    router.replace("/login");
  }, [router]);

  if (me.error && me.error.status !== 401) return <>{fallback(`Cannot reach the API (${me.error.message}). Is it running?`)}</>;
  if (!me.data || !settings.data || !personas.data) return <>{fallback("Loading…")}</>;
  const value: AppCtx = {
    user: me.data,
    settings: settings.data,
    personas: personas.data,
    persona: settings.data.default_persona ?? "",
    health: health.data,
    apiDown: !!health.error && !health.data,
    pending: approvals.data?.length ?? 0,
    refresh,
    signOut,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
