# Sales Call QA — Speech Intelligence Pipeline

Local, GPU-accelerated conversation intelligence for sales call recordings. Upload a call and
get back a speaker-attributed transcript with word-level timestamps, conversation metrics, and
an LLM QA scorecard covering compliance, objections, sentiment, and coaching.

Everything runs on one workstation. No cloud, no API keys, no data leaving the office.

---

## Pipeline

```
upload → ffmpeg 16k mono → [GPU worker] → SQLite → dashboard
                               │
                  1. Parakeet TDT v3   full-call ASR + word timestamps
                  2. Sortformer        speaker turns (who spoke when)
                  3. Align             word → speaker by time overlap
                  4. Roles             speaker_N → Agent / Customer
                  5. Metrics           talk ratio, interruptions, dead air, WPM
                  6. Qwen3 8B          QA scorecard (JSON)
```

Both speech models run **once over the whole call** and are joined on the time axis, rather
than transcribing each speaker segment separately. This is faster and preserves the acoustic
context Parakeet needs at turn boundaries.

Silence is deliberately **not** stripped before ASR: removing it would desynchronize every
timestamp from the audio file and break click-to-seek. Dead air is measured instead.

## Models

| Stage | Model | Notes |
|---|---|---|
| ASR | `nvidia/parakeet-tdt-0.6b-v3` | Newest v3 Parakeet. NVIDIA publishes no v3 above 0.6b. |
| Diarization | `nvidia/diar_sortformer_4spk-v1` | Up to 4 speakers, offline single pass. |
| QA | `qwen3:8b` via Ollama | Swap with `QA_MODEL=…`. |

Override any of them with the `ASR_MODEL`, `DIAR_MODEL`, `QA_MODEL` environment variables.

Speech model weights download into `server/models_cache/` (gitignored). Ollama uses its own
system-wide store.

## Measured performance (RTX 5070 Ti, 16 GB)

| | |
|---|---|
| Transcription | 61 s call in 1.6 s — ~38× realtime |
| Diarization | 173 s call in 0.8 s |
| Resident VRAM | ~4 GB speech models + ~5.5 GB Qwen3 8B |

## Setup

Requires `ffmpeg`, an NVIDIA GPU with recent drivers, and [Ollama](https://ollama.com).

```bash
ollama pull qwen3:8b
npm run dev              # creates venv, installs deps, starts server
```

Open http://localhost:8000. First launch downloads ~3 GB of speech models.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/calls` | Upload a recording, returns `call_id` |
| `GET` | `/api/calls` | List calls with status and QA score |
| `GET` | `/api/calls/{id}` | Transcript + metrics + QA |
| `GET` | `/api/calls/{id}/status` | Poll processing progress |
| `GET` | `/api/calls/{id}/audio` | Stream the original audio |
| `POST` | `/api/calls/{id}/reprocess` | Re-run the pipeline |
| `DELETE` | `/api/calls/{id}` | Delete call and audio |
| `GET` | `/api/search?q=` | Full-text search across all transcripts |
| `GET` | `/api/health` | Models, device, VRAM, queue depth |

## Layout

```
server/
  app.py              FastAPI endpoints
  db.py               SQLite schema (calls → segments → words) + FTS5 search
  jobs.py             single GPU worker, queued jobs
  pipeline/
    audio.py          ffmpeg conversion
    asr.py            Parakeet
    diarize.py        Sortformer
    align.py          word→speaker join, segmentation, role assignment
    metrics.py        conversation analytics
    qa.py             Qwen3 QA with quote verification
    run.py            stage orchestration
web/                  vanilla HTML/CSS/JS dashboard
test-audio/           sample recordings
```

## Notes on accuracy

QA findings are **quote-verified**: any objection or compliance issue whose quote does not
appear verbatim in the transcript is discarded before it reaches the database, so the
dashboard cannot show a hallucinated citation. A high-severity compliance issue caps the
call score at 50.

Speaker roles are assigned heuristically (who opens, identification phrasing, question ratio,
talk-time share) and then confirmed by the LLM, which sees the full conversation.
