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
npm run tunnel:cf     # Cloudflare — no warning page, no account   (recommended)
npm run tunnel        # ngrok — requires a token
```

Both print a public HTTPS URL alongside the password, and both refuse to start unless
`APP_PASSWORD` is set.

> **Why two options.** ngrok's free plan shows every first-time visitor a *"You are about to
> visit…"* interstitial. It cannot be suppressed from the server side: the documented bypass is
> a request header you cannot set on someone else's first page load, and an edge traffic policy
> does not help because the interstitial is served *before* the policy runs. Either click
> through it once per visitor, upgrade ngrok, or use Cloudflare — `trycloudflare.com` links open
> directly at the login screen.

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
| `NGROK_AUTHTOKEN` | *(unset)* | Required by `npm run tunnel`. |
| `PORT` | `8000` | Server port. |
| `ALLOWED_ORIGINS` | localhost | Comma-separated extra CORS origins. |
| `ASR_MODEL` | `nvidia/parakeet-tdt-0.6b-v3` | Transcription model. |
| `DIAR_MODEL` | `nvidia/diar_sortformer_4spk-v1` | Diarization model. |
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
| `POST` | `/api/calls` | Upload a recording (multipart `file`). Returns `call_id`. |
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
