import asyncio
import logging
import os

# Keep model weights inside the repo. Must precede any HF/NeMo import.
_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache")
os.environ.setdefault("HF_HOME", _CACHE)
os.environ.setdefault("TORCH_HOME", _CACHE)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

from fastapi import Body, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config

config.load_env()

import auth  # noqa: E402  (must follow load_env so APP_PASSWORD is visible)
import db  # noqa: E402
import jobs  # noqa: E402
import uploads  # noqa: E402
import warmup  # noqa: E402
from pipeline import asr, diarize, qa, waveform  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("app")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Sales Call QA — Speech Intelligence", version="2.0.0")

# Credentialed requests must not be accepted from arbitrary origins — with a
# password gate and a session cookie, "*" would permit cross-site reads.
_origins = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(auth.middleware)


@app.on_event("startup")
async def startup():
    os.makedirs(uploads.UPLOAD_DIR, exist_ok=True)
    db.init()
    db.reset_stuck_jobs()
    uploads.cleanup_incoming()
    jobs.start()

    loop = asyncio.get_event_loop()
    app.state.warmup = await loop.run_in_executor(None, warmup.run)

    for call_id in db.pending_call_ids():
        jobs.submit(call_id)

    logger.info("Ready — http://localhost:8000")


@app.get("/api/auth-status")
def auth_status(request: Request):
    return {
        "auth_required": auth.enabled(),
        "authenticated": not auth.enabled()
        or auth.valid_session(request.cookies.get(auth.COOKIE)),
    }


@app.post("/api/login")
def do_login(response: Response, payload: dict = Body(...)):
    return auth.login(response, str(payload.get("password", "")))


@app.post("/api/logout")
def do_logout(response: Response, request: Request):
    token = request.cookies.get(auth.COOKIE)
    if token:
        auth._sessions.pop(token, None)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"authenticated": False}


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


@app.get("/api/limits")
def get_limits():
    """Lets the browser reject oversized files before spending bandwidth."""
    return uploads.limits()


@app.post("/api/calls", status_code=201)
async def upload_call(response: Response, file: UploadFile = File(...)):
    try:
        result = await uploads.ingest(file, jobs.depth())
    except uploads.UploadError as e:
        raise HTTPException(e.status, e.message)
    except OSError as e:
        logger.exception("Upload failed while writing to disk")
        raise HTTPException(507, f"Could not store the upload: {e.strerror or e}")

    if result["duplicate"]:
        # Already ingested — hand back the original rather than processing twice.
        response.status_code = 200
        return result

    jobs.submit(result["call_id"])
    return result


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


@app.get("/api/calls/{call_id}/waveform")
def get_waveform(call_id: str):
    call = db.get_call(call_id)
    if not call or not os.path.exists(call["audio_path"]):
        raise HTTPException(404, "Audio not found")
    return waveform.get_waveform(call_id, call["audio_path"])


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
