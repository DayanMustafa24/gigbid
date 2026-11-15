"use client";

import type { ReactNode } from "react";
import { AppProvider } from "@/lib/app";
import { Shell } from "@/components/Shell";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AppProvider fallback={(msg) => <div className="center"><p className="muted" role="status">{msg}</p></div>}>
      <Shell>{children}</Shell>
    </AppProvider>
  );
}
