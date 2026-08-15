"use client";

import { ThemeToggle } from "@ascras/ui";

/**
 * There is no sign-up form anywhere on this site, by design: accounts are
 * created by hand and credentials handed over personally. Every call to action
 * therefore points at a real channel rather than collecting an address.
 */
const CONTACT = {
  site: "https://ubaidbinwaris.com",
  discord: "https://ubaidbinwaris.com/#discord",
  linkedin: "https://ubaidbinwaris.com/#linkedin",
  fiverr: "https://ubaidbinwaris.com/#fiverr",
  upwork: "https://ubaidbinwaris.com/#upwork",
};

const CAPABILITIES = [
  {
    title: "Knows who said what",
    body: "Speaker diarization separates agent from customer, with word-level timestamps. Click any line and the audio jumps to it.",
  },
  {
    title: "Admits what it is unsure of",
    body: "Every word carries a confidence score. Doubtful passages are decoded a second time by a different search algorithm; where the two disagree, the span is flagged for a human rather than quietly guessed.",
  },
  {
    title: "Recovers speech others drop",
    body: "Short turns lost inside a long recording are re-decoded in isolation. Where two people genuinely talk over each other, the gap is marked instead of silently omitted.",
  },
  {
    title: "Findings that prove themselves",
    body: "Each compliance issue must quote the transcript verbatim, at the right timestamp, from the right speaker. Anything that cannot be evidenced is dropped before you ever see it.",
  },
  {
    title: "Hears tone, not just words",
    body: "Pitch and loudness are measured against each speaker's own baseline, so a raised voice is visible even when the words look polite.",
  },
  {
    title: "Runs on your hardware",
    body: "Transcription, diarization and analysis all execute locally on a single GPU. No third-party API, no per-minute fee, and recordings never leave the building.",
  },
];

const MEASURED = [
  { value: "16×", label: "faster than real time, sustained" },
  { value: "~10s", label: "for a two-minute call, end to end" },
  { value: "100%", label: "of findings backed by a quoted timestamp" },
  { value: "0", label: "recordings sent to any third party" },
];

export default function Landing() {
  return (
    <main>
      <div className="ascras-shell">
        <nav className="ascras-nav">
          <div className="ascras-brand">
            <span className="ascras-mark">A</span>
            <span>ASCRAS</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <a href="#what" className="ascras-muted">
              What it does
            </a>
            <a href="#contact" className="ascras-muted">
              Contact
            </a>
            <a className="ascras-btn" href={process.env.NEXT_PUBLIC_PORTAL_URL ?? "http://localhost:5001"}>
              Client login
            </a>
            <ThemeToggle />
          </div>
        </nav>

        <section className="hero">
          <p className="eyebrow">Automated Sales Call Review and Analysis System</p>
          <h1>
            Call QA that <span className="grad">proves what it claims</span>
          </h1>
          <p className="lede">
            ASCRAS transcribes your sales calls, separates the speakers, scores the
            conversation and flags compliance problems — and shows you exactly which
            words it was unsure of, so a confident-sounding verdict is never built on
            a transcript nobody checked.
          </p>
          <div className="cta-row">
            <a className="ascras-btn" href={CONTACT.site} target="_blank" rel="noreferrer">
              Talk to us about access
            </a>
            <a className="ascras-btn ascras-btn-ghost" href="#what">
              See what it does
            </a>
          </div>
          <p className="ascras-muted" style={{ marginTop: 14, fontSize: 13 }}>
            Accounts are set up personally — there is no sign-up form. Reach out and
            we will create yours and send the credentials over.
          </p>
        </section>

        <section className="stats">
          {MEASURED.map((s) => (
            <div key={s.label} className="ascras-card stat">
              <div className="stat-value">{s.value}</div>
              <div className="ascras-muted">{s.label}</div>
            </div>
          ))}
        </section>

        <section id="what" className="section">
          <h2>What it actually does</h2>
          <div className="cards">
            {CAPABILITIES.map((c) => (
              <div key={c.title} className="ascras-card">
                <h3>{c.title}</h3>
                <p className="ascras-muted">{c.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <h2>How it works</h2>
          <ol className="steps">
            <li>
              <strong>Your bot is configured.</strong> We set up a processing bot for
              your account with the daily volume and hours that suit you.
            </li>
            <li>
              <strong>Recordings go in.</strong> Upload through the portal, and the bot
              picks them up and does the work.
            </li>
            <li>
              <strong>Results come back.</strong> Transcript, speaker split, talk ratio,
              interruptions, compliance findings and a QA score — each finding tied to a
              moment in the audio you can play.
            </li>
          </ol>
        </section>

        <section id="contact" className="section">
          <h2>Get access</h2>
          <p className="ascras-muted" style={{ maxWidth: 620 }}>
            No forms, no automated onboarding. Get in touch through whichever of these
            suits you and we will set your account up by hand.
          </p>
          <div className="contact-grid">
            <a className="ascras-card contact" href={CONTACT.site} target="_blank" rel="noreferrer">
              <strong>ubaidbinwaris.com</strong>
              <span className="ascras-muted">Everything in one place</span>
            </a>
            <a className="ascras-card contact" href={CONTACT.discord} target="_blank" rel="noreferrer">
              <strong>Discord</strong>
              <span className="ascras-muted">Fastest for a quick question</span>
            </a>
            <a className="ascras-card contact" href={CONTACT.linkedin} target="_blank" rel="noreferrer">
              <strong>LinkedIn</strong>
              <span className="ascras-muted">Professional enquiries</span>
            </a>
            <a className="ascras-card contact" href={CONTACT.fiverr} target="_blank" rel="noreferrer">
              <strong>Fiverr</strong>
              <span className="ascras-muted">Hire through the platform</span>
            </a>
            <a className="ascras-card contact" href={CONTACT.upwork} target="_blank" rel="noreferrer">
              <strong>Upwork</strong>
              <span className="ascras-muted">Contract work</span>
            </a>
          </div>
        </section>

        <footer className="footer">
          <span className="ascras-muted">
            ASCRAS — Automated Sales Call Review and Analysis System
          </span>
          <a href={CONTACT.site} target="_blank" rel="noreferrer">
            ubaidbinwaris.com
          </a>
        </footer>
      </div>
    </main>
  );
}
