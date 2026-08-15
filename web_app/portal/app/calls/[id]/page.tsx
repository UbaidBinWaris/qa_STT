"use client";

import { api, type CallDetail, type Segment, type Word } from "@ascras/api-client";
import { ThemeToggle } from "@ascras/ui";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

export default function CallPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [call, setCall] = useState<CallDetail | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [tab, setTab] = useState<"transcript" | "qa" | "metrics">("transcript");
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    api
      .call(id)
      .then(setCall)
      .catch(() => router.replace("/"));
    // Presigned and short-lived: the URL stops working shortly after the page is
    // closed, so a link pasted elsewhere does not become a way in.
    api
      .audioUrl(id)
      .then(({ url }) => setAudioUrl(url))
      .catch(() => {});
  }, [id, router]);

  function seek(seconds: number) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = seconds;
    void audioRef.current.play();
  }

  if (!call) return <main className="ascras-shell">Loading…</main>;

  const rel = call.reliability ?? {};
  const qa = call.qa ?? {};
  const metrics = call.metrics ?? {};
  const crosstalk: { start: number; end: number }[] =
    rel.recovery?.crosstalk_regions ?? [];

  return (
    <main className="ascras-shell">
      <nav className="ascras-nav">
        <div className="ascras-brand">
          <span className="ascras-mark">A</span>
          <Link href="/" style={{ color: "var(--text)" }}>
            ASCRAS
          </Link>
        </div>
        <ThemeToggle />
      </nav>

      <header className="ascras-card" style={{ marginBottom: 16 }}>
        <div className="detail-head">
          <div>
            <h1 style={{ margin: 0, fontSize: 20 }}>{call.filename}</h1>
            <p className="ascras-muted" style={{ margin: "6px 0 0", fontSize: 13 }}>
              {fmtDuration(call.durationSeconds)} · {call.transcript?.length ?? 0} turns ·{" "}
              {new Date(call.createdAt).toLocaleString()}
            </p>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            {rel.score != null && (
              <span
                className={`ascras-chip ${
                  rel.conflicts
                    ? "chip-conflict"
                    : rel.score >= 95
                      ? "chip-verified"
                      : "chip-uncertain"
                }`}
                title={`${rel.flagged ?? 0} of ${rel.total ?? 0} words uncertain`}
              >
                Transcript {rel.score}%
                {rel.conflicts ? ` · ${rel.conflicts} disputed` : ""}
              </span>
            )}
            {call.score != null && (
              <div className="score-badge">
                <strong>{call.score}</strong>
                <span className="ascras-muted">QA</span>
              </div>
            )}
          </div>
        </div>

        {audioUrl && (
          <audio ref={audioRef} src={audioUrl} controls style={{ width: "100%", marginTop: 14 }} />
        )}
      </header>

      <div className="tabs">
        {(["transcript", "qa", "metrics"] as const).map((t) => (
          <button
            key={t}
            className={`ascras-btn ${tab === t ? "" : "ascras-btn-ghost"}`}
            onClick={() => setTab(t)}
          >
            {t === "qa" ? "QA analysis" : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <section className="ascras-card" style={{ marginTop: 14 }}>
        {tab === "transcript" && (
          <Transcript
            segments={call.transcript ?? []}
            crosstalk={crosstalk}
            onSeek={seek}
          />
        )}
        {tab === "qa" && <QaPanel qa={qa} onSeek={seek} />}
        {tab === "metrics" && <MetricsPanel metrics={metrics} onSeek={seek} />}
      </section>
    </main>
  );
}

function Transcript({
  segments,
  crosstalk,
  onSeek,
}: {
  segments: Segment[];
  crosstalk: { start: number; end: number }[];
  onSeek: (s: number) => void;
}) {
  if (!segments.length) return <p className="ascras-muted">No transcript.</p>;

  // Cross-talk that produced no words leaves a hole. Rendering the hole is the
  // point: silence here means "we could not hear it", not "nobody spoke".
  const gaps = crosstalk.filter(
    (c) => !segments.some((s) => s.start < c.end && s.end > c.start),
  );
  const rows: React.ReactNode[] = [];
  let gapIndex = 0;

  for (const seg of segments) {
    while (gapIndex < gaps.length && gaps[gapIndex].start < seg.start) {
      const g = gaps[gapIndex++];
      rows.push(
        <div key={`gap-${g.start}`} className="turn turn-crosstalk" onClick={() => onSeek(g.start)}>
          <div className="turn-head">
            <span style={{ color: "var(--conflict)", fontWeight: 600 }}>Cross-talk</span>
            <span className="ascras-muted">{fmtTime(g.start)}</span>
          </div>
          <p style={{ margin: 0, fontStyle: "italic" }}>
            Both speakers talking at once for {(g.end - g.start).toFixed(1)}s — speech
            here could not be transcribed. Listen to the audio.
          </p>
        </div>,
      );
    }

    rows.push(
      <div
        key={`${seg.start}-${seg.role}`}
        className={`turn ${seg.crosstalk ? "turn-crosstalk" : seg.uncertain ? "turn-uncertain" : ""}`}
        onClick={() => onSeek(seg.start)}
      >
        <div className="turn-head">
          <span className={seg.role === "Agent" ? "role-agent" : "role-customer"}>
            {seg.role}
          </span>
          <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {seg.crosstalk && (
              <span className="ascras-chip chip-conflict">may be incomplete</span>
            )}
            {seg.prosody?.tone === "elevated" && (
              <span className="ascras-chip chip-info" title="Pitch and loudness rose above this speaker's own baseline">
                raised tone
              </span>
            )}
            {seg.uncertain && !seg.crosstalk && (
              <span className="ascras-chip chip-uncertain">check audio</span>
            )}
            <span className="ascras-muted">{fmtTime(seg.start)}</span>
          </span>
        </div>
        <p style={{ margin: 0, lineHeight: 1.7 }}>
          {seg.words.map((w, i) => (
            <WordSpan key={i} word={w} />
          ))}
        </p>
      </div>,
    );
  }
  while (gapIndex < gaps.length) {
    const g = gaps[gapIndex++];
    rows.push(
      <div key={`gap-${g.start}`} className="turn turn-crosstalk">
        <div className="turn-head">
          <span style={{ color: "var(--conflict)", fontWeight: 600 }}>Cross-talk</span>
          <span className="ascras-muted">{fmtTime(g.start)}</span>
        </div>
      </div>,
    );
  }
  return <>{rows}</>;
}

function WordSpan({ word }: { word: Word }) {
  const conflict = word.verdict === "conflict";
  const cls = conflict
    ? "w w-conflict"
    : word.recovered
      ? "w w-recovered"
      : word.uncertain
        ? "w w-uncertain"
        : "w";
  const title = word.recovered
    ? "Recovered from audio the first pass missed — verify"
    : word.confidence != null
      ? `${conflict ? "Decoders disagreed here — check the audio · " : ""}confidence ${(word.confidence * 100).toFixed(1)}%${
          word.risk?.length ? ` · ${word.risk.join(", ")}` : ""
        }`
      : "";
  return (
    <span className={cls} title={title}>
      {word.word}{" "}
    </span>
  );
}

function QaPanel({ qa, onSeek }: { qa: Record<string, any>; onSeek: (s: number) => void }) {
  if (!qa || !Object.keys(qa).length)
    return <p className="ascras-muted">No QA analysis for this call.</p>;
  const issues = qa.compliance?.issues ?? [];
  const objections = qa.objections ?? [];

  return (
    <div className="ascras-grid">
      <div>
        <h3 className="panel-h">Summary</h3>
        <p style={{ lineHeight: 1.7, margin: 0 }}>{qa.summary}</p>
      </div>

      <div>
        <h3 className="panel-h">Compliance</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
          <span className={`ascras-chip ${qa.compliance?.identity_verified ? "chip-verified" : "chip-conflict"}`}>
            Identity verified: {qa.compliance?.identity_verified ? "yes" : "no"}
          </span>
          <span className={`ascras-chip ${qa.compliance?.recording_disclosed ? "chip-verified" : "chip-conflict"}`}>
            Recording disclosed: {qa.compliance?.recording_disclosed ? "yes" : "no"}
          </span>
        </div>
        {issues.length === 0 ? (
          <p className="ascras-muted">No compliance issues detected.</p>
        ) : (
          issues.map((i: any, n: number) => (
            <div key={n} className="finding" onClick={() => onSeek(Number(i.timestamp) || 0)}>
              <div className="turn-head">
                <strong>{i.rule}</strong>
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span className="ascras-chip chip-conflict">{i.severity}</span>
                  {/* A finding resting on doubtful words is shown as needing
                      verification rather than presented as settled fact. */}
                  {i.verified?.transcript_uncertain && (
                    <span className="ascras-chip chip-uncertain">verify audio</span>
                  )}
                  <span className="ascras-muted">{fmtTime(Number(i.timestamp) || 0)}</span>
                </span>
              </div>
              <blockquote>{i.quote}</blockquote>
            </div>
          ))
        )}
      </div>

      <div>
        <h3 className="panel-h">Objections</h3>
        {objections.length === 0 ? (
          <p className="ascras-muted">None raised.</p>
        ) : (
          objections.map((o: any, n: number) => (
            <div key={n} className="finding" onClick={() => onSeek(Number(o.timestamp) || 0)}>
              <div className="turn-head">
                <strong>{o.type}</strong>
                <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <span className={`ascras-chip ${o.handled ? "chip-verified" : "chip-uncertain"}`}>
                    {o.handled ? "handled" : "unhandled"}
                  </span>
                  <span className="ascras-muted">{fmtTime(Number(o.timestamp) || 0)}</span>
                </span>
              </div>
              <blockquote>{o.quote}</blockquote>
            </div>
          ))
        )}
      </div>

      {qa.coaching_feedback?.length > 0 && (
        <div>
          <h3 className="panel-h">Coaching</h3>
          <ul style={{ lineHeight: 1.7 }}>
            {qa.coaching_feedback.map((c: string, n: number) => (
              <li key={n}>{c}</li>
            ))}
          </ul>
        </div>
      )}

      {qa.crm_notes && (
        <div>
          <h3 className="panel-h">CRM notes</h3>
          <p style={{ lineHeight: 1.7, margin: 0 }}>{qa.crm_notes}</p>
        </div>
      )}
    </div>
  );
}

function MetricsPanel({
  metrics,
  onSeek,
}: {
  metrics: Record<string, any>;
  onSeek: (s: number) => void;
}) {
  if (!metrics || !Object.keys(metrics).length)
    return <p className="ascras-muted">No metrics.</p>;
  const ratio = metrics.talk_ratio ?? {};
  const events = metrics.interruption_events ?? [];

  return (
    <div className="ascras-grid">
      <div>
        <h3 className="panel-h">Talk ratio</h3>
        <div className="bar">
          <div className="bar-agent" style={{ width: `${ratio.Agent ?? 0}%` }}>
            {Math.round(ratio.Agent ?? 0)}% Agent
          </div>
          <div className="bar-cust" style={{ width: `${ratio.Customer ?? 0}%` }}>
            {Math.round(ratio.Customer ?? 0)}% Customer
          </div>
        </div>
      </div>

      <div className="stat-grid">
        <Stat label="Turns" value={metrics.turns} />
        <Stat label="Agent WPM" value={metrics.wpm?.Agent} />
        <Stat label="Customer WPM" value={metrics.wpm?.Customer} />
        <Stat label="Silence" value={`${metrics.silence_total ?? 0}s`} />
        <Stat label="Longest pause" value={`${metrics.longest_silence ?? 0}s`} />
        <Stat label="Agent interrupts" value={metrics.interruptions?.Agent ?? 0} />
        <Stat label="Customer interrupts" value={metrics.interruptions?.Customer ?? 0} />
        <Stat label="Overlap total" value={`${metrics.overlap_total ?? 0}s`} />
      </div>

      {events.length > 0 && (
        <div>
          <h3 className="panel-h">Interruptions</h3>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {events.map((e: any, n: number) => (
              <span
                key={n}
                className="ascras-chip"
                style={{ cursor: "pointer" }}
                onClick={() => onSeek(Number(e.start) || 0)}
                title={`${e.speaker} cut in on ${e.interrupted}`}
              >
                {fmtTime(Number(e.start) || 0)} · {e.speaker} {e.duration}s
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="stat">
      <span className="stat-value">{value === undefined || value === null ? "—" : String(value)}</span>
      <span className="ascras-muted" style={{ fontSize: 12 }}>
        {label}
      </span>
    </div>
  );
}

function fmtTime(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}
function fmtDuration(seconds: number | null) {
  if (!seconds) return "—";
  return fmtTime(seconds);
}
