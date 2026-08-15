"use client";

import {
  api,
  ApiError,
  type BotWithQuota,
  type CallSummary,
  type SessionUser,
} from "@ascras/api-client";
import { ThemeToggle } from "@ascras/ui";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

const ACTIVE = new Set(["QUEUED", "PROCESSING"]);

export default function PortalHome() {
  const router = useRouter();
  const [user, setUser] = useState<SessionUser | null>(null);
  const [bots, setBots] = useState<BotWithQuota[]>([]);
  const [calls, setCalls] = useState<CallSummary[]>([]);
  const [selectedBot, setSelectedBot] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const [b, c] = await Promise.all([api.myBots(), api.myCalls()]);
    setBots(b);
    setCalls(c);
    setSelectedBot((prev) => prev || b[0]?.id || "");
  }, []);

  useEffect(() => {
    api
      .me()
      .then(({ user }) => {
        setUser(user);
        return refresh();
      })
      .catch(() => router.replace("/login"));
  }, [refresh, router]);

  // Poll only while something is actually in flight. A dashboard left open on a
  // finished list should not keep the server busy indefinitely.
  useEffect(() => {
    if (!calls.some((c) => ACTIVE.has(c.status))) return;
    const timer = setInterval(() => {
      if (!document.hidden) refresh().catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [calls, refresh]);

  async function upload(files: FileList | null) {
    if (!files?.length || !selectedBot) return;
    setUploading(true);
    setError(null);
    for (const file of Array.from(files)) {
      setStatus(`Uploading ${file.name}…`);
      try {
        const res = await api.upload(file, selectedBot);
        setStatus(
          res.duplicate
            ? `${file.name} was already uploaded — showing the existing call.`
            : `${file.name} accepted.`,
        );
      } catch (err) {
        setError(err instanceof ApiError ? err.message : `${file.name} failed to upload`);
        setStatus(null);
      }
    }
    if (fileRef.current) fileRef.current.value = "";
    setUploading(false);
    refresh().catch(() => {});
  }

  const bot = bots.find((b) => b.id === selectedBot);

  if (!user) return <main className="ascras-shell">Loading…</main>;

  return (
    <main className="ascras-shell">
      <nav className="ascras-nav">
        <div className="ascras-brand">
          <span className="ascras-mark">A</span>
          <span>ASCRAS</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="ascras-muted">{user.name}</span>
          <ThemeToggle />
          <button
            className="ascras-btn ascras-btn-ghost"
            onClick={async () => {
              await api.logout();
              router.replace("/login");
            }}
          >
            Sign out
          </button>
        </div>
      </nav>

      <section className="ascras-card" style={{ marginBottom: 18 }}>
        <h2 style={{ margin: "0 0 14px", fontSize: 18 }}>Upload recordings</h2>

        {bots.length === 0 ? (
          <p className="ascras-muted">
            No bot has been assigned to your account yet. Contact us and we will set
            one up.
          </p>
        ) : (
          <>
            <div className="upload-row">
              <div style={{ flex: "1 1 240px" }}>
                <label className="ascras-label" htmlFor="bot">
                  Bot
                </label>
                <select
                  id="bot"
                  className="ascras-input"
                  value={selectedBot}
                  onChange={(e) => setSelectedBot(e.target.value)}
                >
                  {bots.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name} {b.status === "DISABLED" ? "(disabled)" : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div style={{ flex: "2 1 320px" }}>
                <label className="ascras-label" htmlFor="file">
                  Audio files
                </label>
                <input
                  id="file"
                  ref={fileRef}
                  className="ascras-input"
                  type="file"
                  accept=".mp3,.wav,.m4a,.flac,.ogg,.opus,.webm,.aac"
                  multiple
                  disabled={uploading || !bot || bot.status === "DISABLED"}
                  onChange={(e) => upload(e.target.files)}
                />
              </div>
            </div>

            {bot && (
              <p className="ascras-muted" style={{ marginTop: 12, fontSize: 13 }}>
                {bot.quota.allowed ? (
                  <>
                    Remaining today:{" "}
                    <strong>
                      {bot.quota.callsRemaining ?? "unlimited"} calls
                    </strong>
                    {" · "}
                    <strong>
                      {bot.quota.minutesRemaining ?? "unlimited"} minutes
                    </strong>
                  </>
                ) : (
                  <span className="ascras-error">{bot.quota.reason}</span>
                )}
              </p>
            )}
          </>
        )}

        {status && (
          <p className="ascras-muted" style={{ marginTop: 10 }}>
            {status}
          </p>
        )}
        {error && (
          <p className="ascras-error" style={{ marginTop: 10 }}>
            {error}
          </p>
        )}
      </section>

      <section className="ascras-card">
        <h2 style={{ margin: "0 0 14px", fontSize: 18 }}>Your calls</h2>
        {calls.length === 0 ? (
          <p className="ascras-muted">Nothing uploaded yet.</p>
        ) : (
          <table className="ascras-table">
            <thead>
              <tr>
                <th>Recording</th>
                <th>Status</th>
                <th>Length</th>
                <th>QA score</th>
                <th>Transcript</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.id}>
                  <td>{c.filename}</td>
                  <td>
                    {c.status === "PROCESSING" ? (
                      <span className="ascras-muted">
                        {c.stage ?? "processing"} · {c.progress}%
                      </span>
                    ) : c.status === "FAILED" ? (
                      <span className="ascras-chip chip-conflict" title={c.error ?? ""}>
                        failed
                      </span>
                    ) : c.status === "QUEUED" ? (
                      <span className="ascras-muted">queued</span>
                    ) : (
                      <span className="ascras-chip chip-verified">done</span>
                    )}
                  </td>
                  <td className="ascras-muted">{fmtDuration(c.durationSeconds)}</td>
                  <td>{c.score ?? "—"}</td>
                  <td>
                    {/* Transcript reliability, shown apart from the QA score: a
                        confident verdict over a doubtful transcript is exactly
                        what this product exists to make visible. */}
                    {c.reliabilityScore == null ? (
                      "—"
                    ) : (
                      <span
                        className={`ascras-chip ${
                          c.reliabilityScore >= 95
                            ? "chip-verified"
                            : c.reliabilityScore >= 88
                              ? "chip-uncertain"
                              : "chip-conflict"
                        }`}
                      >
                        {c.reliabilityScore.toFixed(1)}%
                      </span>
                    )}
                  </td>
                  <td>
                    {c.status === "COMPLETED" && (
                      <Link href={`/calls/${c.id}`}>Open</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

function fmtDuration(seconds: number | null) {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
