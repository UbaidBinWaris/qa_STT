<div align="center">

# Sales Call Intelligence

**Local, GPU-accelerated conversation intelligence for sales call recordings.**

Upload a call — get a speaker-attributed transcript with word-level timestamps,
conversation analytics, and an LLM quality-assurance scorecard covering compliance,
objections, sentiment, and coaching.

Everything runs on one workstation. No cloud, no API keys, no customer data leaving the office.

</div>

---

## Contents

- [What it does](#what-it-does)
- [Pipeline](#pipeline)
- [Design decisions](#design-decisions)
- [Models](#models)
- [Performance](#performance)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the server](#running-the-server)
- [Remote access](#remote-access)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Data model](#data-model)
- [Project layout](#project-layout)
- [Security](#security)
- [Transcript reliability](#transcript-reliability)
- [Known defects found by audit](#known-defects-found-by-audit)
- [Accuracy safeguards](#accuracy-safeguards)
- [Troubleshooting](#troubleshooting)

---

## What it does

For every recording, the system answers the questions a QA reviewer would ask:

| Question | Produced by |
|---|---|
| Who spoke, and when? | Sortformer diarization → Agent / Customer roles |
| What exactly was said? | Parakeet TDT v3, word-level timestamps |
| Who dominated the call? | Talk ratio, interruptions, overlap, dead air, words per minute |
| Did their tone shift? | Pitch/loudness arousal relative to the speaker's own baseline |
| Did the customer show intent? | LLM scorecard with quoted evidence |
| Was the script followed? | Identity verification and disclosure checks |
| Were there TCPA issues? | Compliance findings, each tied to a timestamp |
| How good was the call? | Score 0–100, coaching notes, CRM summary, follow-up email |

The dashboard renders the conversation as a chat-style timeline. Clicking any turn seeks the
audio to that moment, and words highlight as they are spoken.

---

## Pipeline

```
   upload ──► ffmpeg 16 kHz mono ──► GPU worker ──► SQLite ──► dashboard
                                         │
              ┌──────────────────────────┴──────────────────────────┐
              │  1  Parakeet TDT v3   full-call ASR + word timings   │
              │  2  Sortformer        speaker turns                  │
              │  3  Align             word → speaker by overlap      │
              │  4  Roles             speaker_N → Agent / Customer   │
              │  5  Metrics           talk ratio, interrupts, pauses │
              │  6  Qwen3 8B          QA scorecard (validated JSON)  │
              └─────────────────────────────────────────────────────┘
```

Uploads return immediately and are processed by a background worker. The dashboard polls for
per-stage progress (`converting → transcribing → diarizing → aligning → analyzing → saving`),
so a long call never blocks the next upload.

---

## Design decisions

Three choices differ from the obvious approach. Each was measured, not assumed.

**One ASR pass over the whole call, joined to speakers on the time axis.**
The intuitive design transcribes each diarized segment separately. That is slower — dozens of
small GPU calls instead of one — and less accurate, because it strips the acoustic context
Parakeet uses at turn boundaries, clipping words. Instead both models run once over the full
call, and every word is assigned to whichever speaker turn it overlaps most. Segments are then
formed by grouping consecutive same-speaker words.

**Silence is measured, not removed.**
Stripping silence before ASR is a common optimisation. It also desynchronises every downstream
timestamp from the audio file, which breaks click-to-seek, word highlighting, and waveform
alignment. At 38× realtime the saving is not worth the damage, so silence stays — and becomes a
signal (dead air, longest pause) rather than a preprocessing step.

**Stages run sequentially; concurrency comes from the queue.**
A six-minute call is roughly five seconds of GPU work. Running stages in parallel would add
VRAM contention for no meaningful gain. One worker, jobs queued. Three simultaneous uploads
finish in about the time of three sequential ones, with no risk of an out-of-memory failure.

---

## Models

| Stage | Model | Notes |
|---|---|---|
| Transcription | `nvidia/parakeet-tdt-0.6b-v3` | Newest v3 Parakeet. NVIDIA publishes **no v3 above 0.6b**; the 1.1b model is older and English-only. |
| Diarization | `nvidia/diar_sortformer_4spk-v1` | Up to 4 speakers, offline single pass. |
| QA analysis | `qwen3:8b` via Ollama | Structured JSON output with schema enforcement. |

All three are overridable — see [Configuration](#configuration).

Speech weights download into `server/models_cache/` inside the repository (gitignored).
Ollama uses its own system-wide store.

---

## Performance

Measured on an **RTX 5070 Ti (16 GB, Blackwell)** with 8 kHz mono telephony recordings:

| Operation | Result |
|---|---|
| Transcription | 61 s call in **1.6 s** — ≈38× realtime |
| Diarization | 173 s call in **0.8 s** |
| Full pipeline incl. QA | 105 s call in **8 s** |
| Sustained throughput | 108 min of audio in 6.6 min — **≈16× realtime** (recovery, verification, prosody all on) |
| Longest tested call | 20.7 min → 197 turns, 2 809 words |
| Server start (warm) | **25 s** |
| VRAM, idle | **3.3 GB** (speech models resident) |
| VRAM, under sustained load | **4.25 GB, flat** — measured over 18 consecutive jobs |

Sustained throughput is the number that matters for capacity planning: one GPU absorbs roughly
**27 audio-hours per wall-clock hour**, so a day of continuous operation covers several hundred
hours of calls.

Calls longer than four minutes are transcribed in overlapping windows and spliced, so memory
stays bounded regardless of recording length.

---

## Requirements

| | |
|---|---|
| GPU | NVIDIA, 8 GB VRAM minimum; 16 GB comfortable |
| OS | Linux with recent NVIDIA drivers |
| Python | 3.10 – 3.14 |
| Tools | `ffmpeg`, [Ollama](https://ollama.com) |
| Disk | ~4 GB for speech models, ~6 GB for the QA model |

The architecture is GPU-agnostic. On slower hardware jobs simply spend longer in the queue.

---

## Installation

```bash
git clone <repository-url> && cd qa_STT
ollama pull qwen3:8b
npm run dev
```

The first launch creates a virtual environment, installs dependencies, and downloads roughly
3 GB of speech models. Subsequent launches take about 25 seconds.

Open **http://localhost:8000**.

---

## Running the server

| Command | Purpose |
|---|---|
| `npm run dev` | Start the server (stops any previous instance first) |
| `npm run stop` | Stop the server and release its VRAM |
| `npm run dev -- --reinstall` | Force a dependency reinstall |
| `npm test` | Upload, limits and security suites (55 checks) |
| `npm run eval` | Recognition robustness — needs no labelled data |
| `PORT=8080 npm run dev` | Run on a different port |

`npm run dev` is safe to re-run at any time. Every launch:

1. **Stops the previous instance** — by PID file, then by process name, then by whatever holds
   the port — and waits for the port to free. Two servers never contend for the GPU.
2. **Verifies dependencies by importing them**, installing only when something is missing.
   It deliberately avoids running `pip install` on every launch: NeMo's own pins would
   downgrade `numpy`, `ml_dtypes`, and `protobuf` to versions that cannot import.
3. **Warms up for real** — loads both models and runs an actual inference pass through each, so
   CUDA graphs are compiled before your first upload. It also confirms the Ollama QA model is
   pulled and warns clearly if not, rather than silently producing empty scorecards.

The warm-up report is exposed at `/api/health`.

---

## Remote access

```bash
npm run tunnel          # Cloudflare — routes via Karachi, no warning page, no account
npm run tunnel:ngrok    # ngrok — stable URL, but routes via Mumbai
```

Both print a public HTTPS URL alongside the password, and both refuse to start unless
`APP_PASSWORD` is set.

> **Why two options.** ngrok's free plan shows every first-time visitor a *"You are about to
> visit…"* interstitial. It cannot be suppressed from the server side: the documented bypass is
> a request header you cannot set on someone else's first page load, and an edge traffic policy
> does not help because the interstitial is served *before* the policy runs. Either click
> through it once per visitor, upgrade ngrok, or use Cloudflare — `trycloudflare.com` links open
> directly at the login screen.

### Which tunnel to choose

| | `npm run tunnel` (Cloudflare) | `npm run tunnel:ngrok` (ngrok) |
|---|---|---|
| **Traffic routes via** | **Karachi, Pakistan** (`KHI`), anchored in Singapore | **Mumbai, India** — not changeable on the free plan |
| URL stability | New random URL each time **the tunnel** starts | Same every launch (one free static domain) |
| Warning page | None | Once per visitor |
| Account needed | No | Yes, authtoken |

Cloudflare is the default because it keeps traffic regional. Use ngrok only when a bookmarkable
address matters more than the routing path.

**The Cloudflare URL changes when the *tunnel* restarts — not when the server does.** Restarting
the server with `npm run dev` while `npm run tunnel` keeps running leaves the address untouched
(verified: the link stayed identical and reachable across a full server stop/start, returning
502 only while the server was down). Keep the tunnel process alive in its own terminal and the
link stays valid all day; only Ctrl-C on the tunnel mints a new one.

Set `NGROK_DOMAIN` in `.env` to pin the ngrok hostname explicitly:

```
NGROK_DOMAIN="your-name.ngrok-free.dev"
```

### Tunnel routing

Nothing in this project selects a country — each provider picks its own edge, and the two
behave very differently.

**Cloudflare** is anycast: every visitor is served by the Cloudflare PoP nearest to *them*.
From Islamabad that is **`KHI` (Karachi, Pakistan)**; the tunnel itself anchors at
`sin` (Singapore). No Indian infrastructure is involved.

**ngrok** resolves its free `*.ngrok-free.dev` domains through GeoDNS, and for this part of the
world it always answers Mumbai. Measured by resolving the hostname with different client
subnets:

| Visitor location | ngrok edge returned |
|---|---|
| Pakistan | Mumbai, India |
| UAE (Dubai) | Mumbai, India |
| Germany | Mumbai, India |
| United States | Ohio, USA |

ngrok v3 has **no `--region` flag**, and free static domains cannot be pinned to an edge, so
routing away from Mumbai is not possible on the free plan. If ngrok is required *and* Indian
routing is unacceptable, a paid plan with a reserved domain and chosen edge region is the only
option — otherwise use the Cloudflare tunnel.

The origin address visitors ultimately reach is your own office connection; the edge only
relays traffic.

### The ngrok domain

Free accounts get **one auto-assigned development domain**, e.g.
`lazily-stunned-freemason.ngrok-free.dev`. It is stable — the same hostname comes back on every
restart, reboot, and reinstall, because it belongs to the account rather than the session.

Choosing your own name is a paid feature. Attempting it fails with:

```
Only paid plans may create endpoints with custom subdomains.
This account is on the 'Free' plan.
```

You can delete the assigned domain in the ngrok dashboard and create another, but the
replacement is another random name — and any link you already shared stops working.

### Long-running deployments

Neither free tunnel is built to stay up indefinitely. The published limits:

| | ngrok Free | TryCloudflare (quick tunnel) |
|---|---|---|
| HTTP requests | **20,000 / month** | No documented quota |
| Data transfer | **1 GB / month** | No documented quota |
| Concurrent requests | — | 200 in flight |
| Uptime guarantee | — | **None** — "testing and development only" |

**ngrok Free cannot support a continuously-open dashboard.** A single browser tab left open
issues a heartbeat every 30 seconds — roughly 29,000 requests a month before anyone uploads
anything, already past the 20,000 cap. Streaming call audio then consumes the 1 GB transfer
allowance in a few hundred plays.

To keep request volume low, the dashboard pauses all polling while its tab is in the
background and uses a 30-second heartbeat rather than a busy loop.

For genuine round-the-clock access, use a **named Cloudflare Tunnel** instead of a quick
tunnel: it is free, has no request quota, keeps a fixed hostname on a domain you control, and
is the configuration Cloudflare actually supports for production. Run it under `systemd` so it
survives terminal closes and reboots — both tunnel scripts here trap SIGHUP and exit with the
shell by design.

### Access control

Authentication activates whenever `APP_PASSWORD` is set and stays off when it is not, so local
use is frictionless while a tunnelled instance is never publicly readable.

- Login issues an **HttpOnly session cookie**, valid 12 hours.
- Every API route **and the audio stream** require a valid session.
- Failed attempts are throttled and compared in constant time.
- CORS is restricted to localhost by default — with credentials enabled, a wildcard origin
  would permit cross-site reads. Extend it via `ALLOWED_ORIGINS`.

Ctrl-C closes a tunnel, as does `pkill -f tunnel.py`: both scripts trap SIGTERM and take the
tunnel process down with them.

> [!WARNING]
> Call recordings contain customer names, addresses, and phone numbers. Share the URL and
> password privately, and close the tunnel when you are finished.

---

## Configuration

Settings come from `.env` in the repository root (gitignored) or the environment.

| Variable | Default | Purpose |
|---|---|---|
| `APP_PASSWORD` | *(unset)* | Enables authentication. Required for tunnels. |
| `NGROK_AUTHTOKEN` | *(unset)* | Required by `npm run tunnel:ngrok`. |
| `NGROK_DOMAIN` | *(unset)* | Pin a specific ngrok hostname instead of the assigned one. |
| `PORT` | `8000` | Server port. |
| `ALLOWED_ORIGINS` | localhost | Comma-separated extra CORS origins. |
| `ASR_MODEL` | `nvidia/parakeet-tdt-0.6b-v3` | Transcription model. |
| `DIAR_MODEL` | `nvidia/diar_sortformer_4spk-v1` | Diarization model. |
| `MAX_UPLOAD_MB` | `500` | Largest accepted upload. |
| `MAX_DURATION_MIN` | `240` | Longest accepted recording. |
| `MIN_DURATION_SEC` | `1` | Shortest accepted recording. |
| `MAX_QUEUE` | `50` | Uploads rejected with 503 beyond this backlog. |
| `MIN_FREE_MB` | `2048` | Refuse uploads below this much free disk. |
| `QA_MODEL` | `qwen3:8b` | Ollama model for QA. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama endpoint. |

```bash
# .env
APP_PASSWORD="choose-something-strong"
NGROK_AUTHTOKEN="..."
```

---

## API reference

All routes require a session cookie when `APP_PASSWORD` is set.

### Calls

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/calls` | Upload a recording (multipart `file`). `201` on success, `200` if it duplicates an existing call. |
| `GET` | `/api/limits` | Active upload limits and accepted formats. |
| `GET` | `/api/calls` | List calls with status and QA score. |
| `GET` | `/api/calls/{id}` | Full record: transcript, metrics, QA. |
| `GET` | `/api/calls/{id}/status` | Poll processing stage and progress. |
| `GET` | `/api/calls/{id}/audio` | Stream the original audio. |
| `GET` | `/api/calls/{id}/waveform` | Cached amplitude/pitch envelope for rendering. |
| `POST` | `/api/calls/{id}/reprocess` | Re-run the pipeline. |
| `DELETE` | `/api/calls/{id}` | Delete the call, its audio, and derived data. |

### Search, auth, and health

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/search?q=` | Full-text search across all transcripts (FTS5). |
| `GET` | `/api/auth-status` | Whether auth is required and the session is valid. |
| `POST` | `/api/login` | Exchange a password for a session cookie. |
| `POST` | `/api/logout` | Invalidate the session. |
| `GET` | `/api/health` | Models, device, VRAM, queue depth, warm-up report. |

**Accepted audio:** `.mp3` `.wav` `.m4a` `.flac` `.ogg` `.opus` `.webm` `.aac`
Any sample rate or channel count — ffmpeg normalises to 16 kHz mono.

### Upload validation

Ingestion lives in [`server/uploads.py`](server/uploads.py), separate from the HTTP layer, and is
fail-closed: a call is registered in the database only after its audio is safely on disk and
proven decodable. Rejected or interrupted uploads leave nothing behind.

| Check | Response |
|---|---|
| Missing or disallowed extension | `415` |
| Empty file | `422` |
| Not decodable as audio, or corrupt | `422` |
| No audio track in the container | `422` |
| Shorter than `MIN_DURATION_SEC` | `422` |
| Larger than `MAX_UPLOAD_MB` | `413` |
| Longer than `MAX_DURATION_MIN` | `413` |
| Queue at `MAX_QUEUE` | `503` |
| Free disk below `MIN_FREE_MB` | `507` |
| Byte-identical to an existing call | `200` with `duplicate: true` |
| Accepted | `201` |

Details worth knowing:

- **Content is verified, not trusted.** Extensions are trivially spoofed, so every upload is
  probed with `ffprobe`; a `.mp3` that is really a ZIP is rejected before it can reach the GPU.
- **The size cap applies mid-stream.** The body is written in 1 MB chunks and aborted the moment
  it exceeds the limit, so an oversized upload never lands on disk or in memory.
- **Duplicates are detected by SHA-256** of the content, not by filename, and return the
  original call instead of transcribing it again.
- **Filenames are sanitised** for display only — stored files are named by call id, so path
  traversal, null bytes, and Windows paths cannot influence where anything is written.
- **Writes are atomic.** Uploads land in `uploads/.incoming/` and are moved into place only once
  valid; any leftovers are swept at startup.

Configure the limits with `MAX_UPLOAD_MB`, `MAX_DURATION_MIN`, `MIN_DURATION_SEC`, `MAX_QUEUE`,
and `MIN_FREE_MB`. `GET /api/limits` returns the active values, which the browser uses to reject
obvious mistakes before spending bandwidth.

---

## Data model

SQLite in WAL mode, structured the way conversation-intelligence systems generally are:

```
calls ──► segments ──► words
  │
  ├── metrics      talk ratio, interruptions, dead air, WPM
  ├── qa           score, summary, objections, compliance, CRM notes
  └── segments_fts full-text index for cross-call search
```

Storing every word — with `start`, `end`, and `confidence` — is what makes click-to-seek, live
highlighting, search, and clip extraction straightforward rather than special cases.

### QA scorecard fields

`score` · `summary` · `sentiment` (per speaker, plus trajectory) · `buying_intent` with
evidence · `objections[]` with quote, timestamp, and whether they were handled ·
`compliance` (identity verified, recording disclosed, TCPA issues by severity) ·
`action_items[]` · `coaching_feedback[]` · `crm_notes` · `followup_email`

---

## Project layout

```
server/
  app.py              FastAPI application and routes
  auth.py             password gate, sessions, request middleware
  config.py           .env loading
  db.py               SQLite schema, queries, FTS5 search
  jobs.py             single GPU worker and job queue
  warmup.py           model preloading and dependency checks
  start_server.py     launcher: stop previous, verify deps, start
  stop_server.py      graceful shutdown
  tunnel.py           ngrok tunnel
  tunnel_cf.py        Cloudflare tunnel
  pipeline/
    audio.py          ffmpeg conversion
    asr.py            Parakeet, with chunking for long calls
    diarize.py        Sortformer, with speaker stitching for long calls
    align.py          word → speaker join, segmentation, role assignment
    metrics.py        conversation analytics
    qa.py             Qwen3 scorecard with quote verification
    waveform.py       amplitude/pitch envelope, cached per call
    run.py            stage orchestration
web/
  index.html          dashboard
  login.html          sign-in page
  app.js              polling, audio sync, rendering
  styles.css          theme
test-audio/           sample recordings
```

---

## Security

```bash
npm test    # 57 checks across upload validation, limits, and security posture
```

Run against a live server. The security suite brute-forces from a synthetic client address,
so tripping the lockout never locks out the operator running the tests.

### Browser hardening

Every response carries a strict Content-Security-Policy (`script-src 'self'`), plus
`X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a
`Permissions-Policy` denying camera/microphone/geolocation, and HSTS when the request arrives
over TLS. API responses and HTML are `no-store`; static assets are `no-cache` so a security fix
cannot be defeated by a stale cached script.

The CSP is only meaningful because the pages contain **no inline JavaScript**: event handling is
delegated through `data-` attributes, and element sizing goes through the CSSOM rather than
`style` attributes.

> **Why this matters here.** QA scorecards are written by a language model that reads a
> transcript, and the transcript is dictated by whoever is on the call. A caller can therefore
> influence model output by speaking. Earlier the rendering interpolated those values straight
> into `class` and `onclick` attributes, so a crafted value executed JavaScript in the
> reviewer's authenticated session — confirmed by injecting a payload and watching it run. All
> model-derived values are now escaped, numbers coerced, and severities restricted to a known
> set.

### Access control

- Authentication activates whenever `APP_PASSWORD` is set; local use stays open, tunnels never do.
- Sessions are HttpOnly, `SameSite=Strict` (which removes CSRF as a concern), and `Secure`
  whenever the request arrived over TLS. Twelve-hour expiry, capped in number.
- Failed logins are compared in constant time and rate limited per client — 8 attempts in
  5 minutes triggers a 15-minute lockout with `Retry-After`.
- `X-Forwarded-For` is believed only when the connection arrives from loopback (a tunnel or
  reverse proxy on this machine put it there) or when `TRUST_PROXY` names an upstream proxy. A
  request straight off the network cannot forge its way into another rate-limit bucket. This
  matters behind a tunnel: cloudflared and ngrok connect over loopback, so without it every
  remote user collapses into one identity and a single password-guesser would lock out the
  whole team.
- Page loads redirect to the sign-in screen; scripts and API calls receive `401` instead, so the
  browser never gets HTML where it expects JavaScript.
- CORS is limited to explicit origins, methods, and headers — a wildcard with credentials would
  permit cross-site reads.

### Input and error handling

Request bodies outside the upload path are capped at `MAX_BODY_KB`. Search queries are length
limited. Error responses never echo exception text, file paths, or SQLite internals; the detail
goes to the log instead.

### Audit trail

Successful logins, failed logins, lockouts, audio retrieval, and deletions are logged with the
client address and marked `AUDIT`, so access to recordings can be reconstructed.

### Known limitations

- **Single shared password, no per-user accounts.** Fine for a small office; an audit trail that
  must attribute access to individuals needs real user accounts.
- **Sessions live in memory**, so restarting the server signs everyone out.
- **Rate limiting is per-process**, which suits this single-node deployment.
- **Prompt injection is contained, not prevented.** A caller can influence QA wording; the
  schema, quote verification, and output escaping bound the damage.

---

## Transcript reliability

A QA verdict is only as trustworthy as the transcript beneath it. "I don't want the plan" and
"I do want the plan" differ by one short word, and a mis-heard name or figure quietly corrupts a
compliance finding. The pipeline therefore scores its own transcript and shows the doubt rather
than hiding it.

**Per-word confidence.** NeMo emits no confidence by default — every word in the database used to
carry `NULL`. Greedy TDT decoding now runs with entropy-based word confidence
(`aggregation: min`, so a word is only as good as its weakest token). Measured cost: **+0.9 GB
working set, ~8% slower decode**, no change to steady-state VRAM.

**Thresholds are relative, because the raw scores are not spread out.** On real calls confidence
sits in a narrow band — median 0.990, p5 0.965, floor 0.934 — so an absolute cutoff like 0.5
would never fire. A word is flagged when it falls in the bottom decile of *its own call*, or
below a hard floor of 0.95.

**Risk categories.** Confidence alone misses the words that matter most. Each word is also tagged
where a mistake would change a QA outcome: `negation`, `number`, `money`, `date`, `contact`,
`compliance`, `proper-noun`. Risk-tagged words are held to a stricter confidence bar, since a
confident error on "not" is far more damaging than a hesitant one on "the".

On a representative call this flags **6.8% of words and 8.6% as risk-bearing** — including a
customer name the model rendered four different ways (`Ahmud`, `AR Akrum`, `Akram`) and a garbled
account number (`890s, 870. 89870.`).

**Evidence validation.** Every objection and compliance finding must now prove itself: the quote
must exist in the transcript, its timestamp must point at the turn it came from, and the speaker
must match. Wrong timestamps and speakers are corrected from the located segment rather than
discarding an otherwise sound finding; unlocatable quotes are dropped. A finding whose evidence
rests on flagged words is kept but marked `transcript_uncertain`, and the count surfaces as
`evidence_review_required`.

### Recovering dropped speech

Diarization regularly finds a speaker talking where the transcript has nothing at all. Measured
on the test corpus, word density **halves during cross-talk** — 1.6 words/s inside overlap
against 3.0 words/s outside — and several turns produced no text whatever.

Those turns are re-decoded in isolation. A short turn loses out in a long decode because the
model weighs it against minutes of surrounding audio; given only its own seconds, it usually
comes back. Across the corpus this recovered **110 words from 41 turns** that had been silently
dropped. Recovered words carry no confidence score, are always marked uncertain, and are shown
with a dashed underline.

**What cannot be recovered is marked instead.** Where two people genuinely speak at once, a
single mixed mono recording does not contain a separable second voice, and nothing in this
pipeline can extract one. The region is rendered in the transcript as an explicit cross-talk
gap rather than left blank — silence there means "we could not hear it", not "nobody spoke". A
customer objection lost under the agent's voice must not vanish from the record without trace.

> Separating simultaneous speakers would need a source-separation model and, realistically,
> stereo recordings with one speaker per channel. If your telephony can deliver dual-channel
> audio, that single change removes the problem entirely — each channel transcribes cleanly on
> its own.

### Audio normalisation, measured without a dataset

Accuracy questions normally need ground truth, and there is none for these calls. `npm run eval`
sidesteps that: transcribe a call clean and treat that as the reference, damage the audio in a
known way, then measure how far each repair chain brings the transcript back. It cannot report
absolute accuracy, but it answers the question that matters — *given audio that got worse, does
this filter recover it?*

Mean word error rate across three calls:

| degradation | none | speechnorm | loudnorm | dynaudnorm | denoise |
|---|---|---|---|---|---|
| quiet −20 dB | 4.4% | **1.8%** | 3.7% | 3.1% | 8.3% |
| quiet −12 dB | 3.3% | **2.2%** | 4.0% | 3.7% | 6.8% |
| noise light | 8.8% | **6.6%** | 7.4% | 8.5% | 6.9% |
| noise heavy | 12.0% | 11.2% | 10.6% | 10.4% | 10.8% |
| clipped | 5.2% | **4.5%** | 5.1% | 4.8% | 6.3% |

`speechnorm` recovers **26–58% of the error** on quiet or noisy input and moves an already-clean
transcript by about 2%, so it is enabled by default. Real recordings here measure −22 to −26 dB
mean volume, which makes the quiet cases the normal ones rather than the edge.

**Denoising is deliberately not used.** It made quiet audio substantially *worse* — 8.3% against
4.4% — which is the opposite of what it promises.

An earlier version of this document rejected normalisation outright, on the strength of
confidence scores rather than error rates. That was the wrong instrument: confidence fell while
accuracy improved. Disable with `AUDIO_NORMALISE=0`.

### Vocal tone, independent of the words

QA sentiment comes from an LLM reading transcribed text, and text cannot hear a raised voice —
"yeah, fine" and a sharp, fast "yeah, FINE" transcribe identically. Prosody closes that gap:
pitch range, loudness and speaking rate are measured directly from each turn's audio.

Validated on three calls before being wired in. Median pitch separated speakers by register as
expected (roughly 130–270 Hz depending on the person), and the turns scored highest for arousal
were plausible tension — a customer's flat "Do what?", an agent asking someone to repeat
themselves — while the lowest were curt, low-effort answers ("Correct.", "Hello.").

Arousal is measured against each speaker's own baseline for the call, not a fixed threshold, so
a naturally loud or high-pitched talker is not flagged for being themselves. A turn needs at
least 4 turns of history for that baseline to mean anything; shorter calls get pitch and loudness
without an arousal verdict.

This is a signal, not a verdict — shown in the UI as a "raised tone" chip, not folded into the QA
score. Cost: roughly 15% of throughput (18.9× → 16.2× realtime), confirmed with the same
full-corpus soak used elsewhere in this document.

### Second-pass verification

Flagged spans — and only those — are decoded a second time and the two readings compared. A
second opinion is worth something only if it is independent, so the second pass differs in two
ways at once:

| | first pass | second pass |
|---|---|---|
| search | greedy (`greedy_batch`) | beam 4 (`malsd_batch`) |
| context | whole call, or a 180 s window | a few seconds around the span |

Agreement is measured over the whole padded window rather than the span alone — a one-word span
judged against nine seconds of beam output disagrees for trivial reasons. A *localised*
difference, where the surrounding words match but the span's own words are absent, is the signal
worth acting on:

| verdict | meaning |
|---|---|
| `confirmed` | both decoders produced the span's words — the flag is cleared |
| `conflict` | the region matches but the span's words differ — **human check required** |
| `likely` | the whole window disagrees, so the second decode is itself unreliable |

**A conflict is never auto-resolved.** Nothing in the pipeline can tell which reading is correct,
so the span keeps both and asks for a human with the audio. Silently picking one would trade a
visible doubt for an invisible error.

Real catches on a test call: `24` against the beam's *"date is thirtieth"*, and `BGNE.` against
*"By the way…"*. Cost: **1.0 s for 20 spans**, because only the doubtful audio is re-decoded.

Note that TDT beam search cannot preserve alignments, so it produces no timestamps or confidence
— which is fine, since the second pass only needs to answer "what words are in this audio?".

**In the UI**, uncertain words are underlined with their confidence on hover, conflicting words
are highlighted in red with both readings, affected turns get a "check audio" chip, the call
header shows transcript reliability plus a disputed-word count, and findings built on doubtful
text are marked "verify audio".

Tuning constants live at the top of [`server/pipeline/reliability.py`](server/pipeline/reliability.py).

---

## Known defects found by audit

A systematic audit of the pipeline turned up two things worth recording, one fixed and one
deliberately left alone.

**Interruption counting never worked.** Every call ever processed reported `interruptions: {}`.
The check asked whether one segment ended after the next began — but segments are built from
consecutive words and so never overlap, making the test arithmetically dead. Interruptions are
now derived from the diarized turns, which do overlap, and attributed to whoever started second.
On a 10-minute call this surfaces 39 agent interruptions against 21 by the customer, along with
total and longest overlap. The same root cause — overlap information being discarded at
alignment — was behind cross-talk speech going missing.

**Single-word turns are correct, not a defect.** 20% of turns contain one word, and 87% of those
sit between two turns by the other speaker, which looks like diarization thrash. Inspection
showed they are real: `"Hello."`, `"Fine."`, `"Eric."` — customers answering. Smoothing them away
would have destroyed exactly the one-word answers ("No.", "Yes.") that matter most to a QA
verdict, so the behaviour is left as it is.

Verified sound in the same audit: QA scoring is deterministic (identical scores, compliance
issues and objections across repeated runs of the same audio), the full-text index matches the
segment table exactly, talk ratios sum to 100%, and no orphaned audio or output files accumulate
on disk.

---

## Accuracy safeguards

**Quote verification.** Every objection and compliance finding must quote the transcript
verbatim. Findings whose quotes cannot be located are discarded before reaching the database,
so the dashboard cannot display a fabricated citation.

**Score capping.** A high-severity compliance issue caps the call score at 50, regardless of
what the model proposes.

**Self-consistency.** The model is instructed that its summary must agree with its own
findings — a call cannot be described as compliant while a disclosure failure is recorded.

**Role confirmation.** Speaker roles are assigned heuristically (who opens the call,
identification phrasing, question ratio, talk-time share) and then confirmed by the LLM, which
sees the entire conversation before deciding which speaker is the agent.

---

## Troubleshooting

**Models load but every scorecard is empty.**
Ollama is unreachable or the QA model is not pulled. The warm-up log says which. Fix with
`ollama pull qwen3:8b`.

**`CUDA out of memory` during transcription.**
Another process is holding VRAM — commonly a resident LLM. The QA stage requests `keep_alive: 0`
so Ollama releases its memory after each call; check `nvidia-smi` for other consumers.

**`import nemo.collections.asr` fails with a protobuf version error.**
NeMo pins `protobuf~=5.29.5`, but the bundled onnx requires `>=5.31`. The launcher installs the
override separately with `--no-deps`. Run `npm run dev -- --reinstall` to repair.

**Port already in use.**
`npm run dev` clears it automatically. If a foreign process holds the port, the launcher says so
and exits rather than starting a second server.

**Signed in but the dashboard stays empty over a tunnel.**
Hard-refresh (Ctrl-Shift-R). Browsers cache `app.js` and `login.html` aggressively.

---

## License

MIT
