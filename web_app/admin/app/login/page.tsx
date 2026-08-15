"use client";

import { api, ApiError } from "@ascras/api-client";
import { ThemeToggle } from "@ascras/ui";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      router.push("/");
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the server. Try again.",
      );
      setBusy(false);
    }
  }

  return (
    <main className="login-wrap">
      <div style={{ position: "absolute", top: 20, right: 20 }}>
        <ThemeToggle />
      </div>

      <form className="ascras-card login-card" onSubmit={submit}>
        <div className="ascras-brand" style={{ justifyContent: "center" }}>
          <span className="ascras-mark">A</span>
          <span>ASCRAS Admin</span>
        </div>
        <p className="ascras-muted" style={{ textAlign: "center", margin: "4px 0 18px" }}>
          Sign in to the admin panel
        </p>

        <label className="ascras-label" htmlFor="email">
          Email
        </label>
        <input
          id="email"
          className="ascras-input"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />

        <label className="ascras-label" htmlFor="password" style={{ marginTop: 14 }}>
          Password
        </label>
        <input
          id="password"
          className="ascras-input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        <button className="ascras-btn" style={{ width: "100%", marginTop: 20 }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {error && (
          <p className="ascras-error" style={{ marginTop: 12, textAlign: "center" }}>
            {error}
          </p>
        )}

        {/* Accounts are created by hand, so there is nothing to link to here —
            saying so is better than a dead "sign up" link. */}
        <p className="ascras-muted" style={{ marginTop: 18, textAlign: "center", fontSize: 13 }}>
          Administrator access only.
        </p>
      </form>
    </main>
  );
}
