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
- [Accuracy safeguards](#accuracy-safeguards)
- [Troubleshooting](#troubleshooting)

---

## What it does

For every recording, the system answers the questions a QA reviewer would ask:

| Question | Produced by |
|---|---|
| Who spoke, and when? | Sortformer diarization → Agent / Customer roles |
| What exactly was said? | Parakeet TDT v3, word-level timestamps |
| Who dominated the call? | Talk ratio, interruptions, dead air, words per minute |
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
| Longest tested call | 20 min → 197 turns, 2 809 words |
| Server start (warm) | **25 s** |
| Idle VRAM | **4.1 GB** (speech models resident) |

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
