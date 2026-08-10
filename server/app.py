import asyncio
import logging
import os
import shutil

# Keep model weights inside the repo. Must precede any HF/NeMo import.
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache")
os.environ.setdefault("HF_HOME", _CACHE)
os.environ.setdefault("TORCH_HOME", _CACHE)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
import jobs
import warmup
from pipeline import asr, diarize, qa

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(SERVER_DIR, "uploads")
ALLOWED_EXT = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".aac"}

app = FastAPI(title="Sales Call QA — Speech Intelligence", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db.init()
    db.reset_stuck_jobs()
    jobs.start()

    loop = asyncio.get_event_loop()
    app.state.warmup = await loop.run_in_executor(None, warmup.run)

    for call_id in db.pending_call_ids():
        jobs.submit(call_id)

    logger.info("Ready — http://localhost:8000")


@app.get("/api/health")
def health():
    import torch

    return {
        "status": "healthy",
        "asr_model": asr.MODEL_ID,
        "diarization_model": diarize.MODEL_ID,
        "qa_model": qa.QA_MODEL,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "vram_used_gb": round(torch.cuda.memory_allocated() / 1e9, 2)
        if torch.cuda.is_available()
        else 0,
        "queue_depth": jobs.depth(),
        "warmup": getattr(app.state, "warmup", None),
    }


@app.post("/api/calls")
async def upload_call(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported audio format '{ext}'")

    call_id = db.create_call(file.filename, "")
    dest = os.path.join(UPLOAD_DIR, f"{call_id}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    db.connect().execute("UPDATE calls SET audio_path=? WHERE id=?", (dest, call_id))
    db.connect().commit()

    jobs.submit(call_id)
    logger.info(f"Queued {file.filename} as {call_id}")
    return {"call_id": call_id, "status": "queued"}


@app.get("/api/calls")
def list_calls():
    return db.list_calls()


@app.get("/api/calls/{call_id}")
def get_call(call_id: str):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    return {
        **call,
        "transcript": db.get_transcript(call_id),
        "metrics": db.get_metrics(call_id),
        "qa": db.get_qa(call_id),
    }


@app.get("/api/calls/{call_id}/status")
def get_status(call_id: str):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    return {
        "status": call["status"],
        "stage": call["stage"],
        "progress": call["progress"],
        "error": call["error"],
    }


@app.get("/api/calls/{call_id}/audio")
def get_audio(call_id: str):
    call = db.get_call(call_id)
    if not call or not os.path.exists(call["audio_path"]):
        raise HTTPException(404, "Audio not found")
    return FileResponse(call["audio_path"], filename=call["filename"])


@app.delete("/api/calls/{call_id}")
def delete_call(call_id: str):
    call = db.get_call(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    for path in (call["audio_path"], os.path.join(SERVER_DIR, "outputs", f"{call_id}.wav")):
        if path and os.path.exists(path):
            os.remove(path)
    db.delete_call(call_id)
    return {"deleted": call_id}


@app.post("/api/calls/{call_id}/reprocess")
def reprocess(call_id: str):
    if not db.get_call(call_id):
        raise HTTPException(404, "Call not found")
    db.set_progress(call_id, "queued", 0)
    jobs.submit(call_id)
    return {"call_id": call_id, "status": "queued"}


@app.get("/api/search")
def search(q: str):
    if not q.strip():
        return []
    try:
        return db.search(q)
    except Exception as e:
        raise HTTPException(400, f"Invalid search query: {e}")


web_dir = os.path.join(os.path.dirname(SERVER_DIR), "web")
if os.path.exists(web_dir):
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000)
