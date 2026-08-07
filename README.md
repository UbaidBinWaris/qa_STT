# NVIDIA Parakeet TDT Speech-To-Text (STT) Application

A high-performance, real-time **NVIDIA Parakeet Speech-To-Text (STT)** application built with FastAPI, WebSockets, and a modern single-page live text streaming web interface.

---

## Features

- **NVIDIA Parakeet-TDT-1.1B Engine**: High-accuracy acoustic model with token-and-duration transducer (TDT) architecture for low latency transcription.
- **Live On-Screen Text Generation**: Streaming real-time word-by-word display without requiring text file downloads.
- **Microphone Audio Visualizer**: Live audio recording with interactive Web Audio API waveform canvas.
- **Audio File Upload**: Drag-and-drop support for WAV, MP3, FLAC, M4A, and OGG files.
- **Automatic Warm-Up & Dependency Setup**: Auto-configures virtual environments and executes dummy inference on startup to eliminate cold-start delay.

---

## Quick Start

Run the entire application (backend server + live web interface) with a single command:

```bash
npm run dev
```

Then open `http://localhost:8000` in your web browser.

---

## Project Structure

```
qa_STT/
├── package.json               # Root npm starter script ("npm run dev")
├── .gitignore                 # Excludes virtualenvs, model weights, and node_modules
├── server/
│   ├── start_server.py        # Automated venv creation & server launcher
│   ├── app.py                 # FastAPI backend (WebSockets + REST endpoints)
│   ├── stt_engine.py          # NVIDIA Parakeet STT model manager & warm-up sequence
│   └── requirements.txt       # Python dependencies
└── web/
    ├── index.html             # Standalone modern single-page web app
    ├── styles.css             # Glassmorphism dark UI theme
    └── app.js                 # Web Audio recording & live stream text renderer
```
