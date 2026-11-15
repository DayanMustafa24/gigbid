"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, toApiError } from "@/lib/http";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(mode === "signin" ? "/auth/login" : "/auth/register", { email, password });
      router.replace("/inbox");
    } catch (err) {
      const a = toApiError(err);
      setError(a.code === "registration_closed" ? "Registration is closed on this server. Ask the owner for an account." : a.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card narrow stack" onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title">GigBid</h1>
        <p className="muted">Proposals grounded in your real work. Nothing is sent without your approval.</p>
        <div className="field">
          <label htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} required minLength={mode === "register" ? 10 : 1}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
          {mode === "register" ? <span className="small muted">At least 10 characters.</span> : null}
        </div>
        {error ? <div className="alert" role="alert">{error}</div> : null}
        <button className="primary" disabled={busy} type="submit">{mode === "signin" ? "Sign in" : "Create account"}</button>
        <button type="button" onClick={() => { setMode(mode === "signin" ? "register" : "signin"); setError(null); }}>
          {mode === "signin" ? "First time? Create the owner account" : "I already have an account"}
        </button>
      </form>
    </div>
  );
}
