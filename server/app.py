import os
import sys
import io
import logging
import asyncio
import numpy as np
import soundfile as sf
from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from stt_engine import stt_engine

logger = logging.getLogger("ParakeetApp")

app = FastAPI(title="NVIDIA Parakeet STT Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    logger.info("Initializing FastAPI Backend Server for NVIDIA Parakeet STT...")
    # Trigger STT engine loading and warm-up
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, stt_engine.warmup)

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "model": stt_engine.model_name,
        "device": stt_engine.device,
        "warmed_up": stt_engine.is_warmed_up
    }

@app.post("/api/transcribe")
async def transcribe_file(file: UploadFile = File(...)):
    try:
        content = await file.read()
        audio_data, sample_rate = sf.read(io.BytesIO(content))
        
        # Convert to mono if stereo
        if len(audio_data.shape) > 1:
            audio_data = np.mean(audio_data, axis=1)

        # Convert to float32
        audio_data = audio_data.astype(np.float32)

        transcription = stt_engine.transcribe_numpy_audio(audio_data, sample_rate=sample_rate)
        return JSONResponse({
            "success": True,
            "filename": file.filename,
            "duration_seconds": round(len(audio_data) / float(sample_rate), 2),
            "transcription": transcription
        })
    except Exception as e:
        logger.error(f"Transcription error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/transcribe")
async def websocket_transcribe(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket client connected for real-time STT streaming.")
    try:
        while True:
            # Receive audio chunk as bytes
            data = await websocket.receive_bytes()
            if not data:
                continue
            
            # Parse raw PCM 16-bit 16kHz audio float array
            audio_chunk = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
            
            if len(audio_chunk) > 0:
                text = stt_engine.transcribe_numpy_audio(audio_chunk, sample_rate=16000)
                if text:
                    await websocket.send_json({
                        "type": "transcription_chunk",
                        "text": text
                    })
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

# Mount static web UI directory
web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.exists(web_dir):
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
