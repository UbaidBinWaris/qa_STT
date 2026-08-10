import logging
import os

import db
from pipeline import align, asr, audio, diarize, metrics, qa

logger = logging.getLogger("pipeline.run")

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(SERVER_DIR, "outputs")


def process(call_id: str):
    call = db.get_call(call_id)
    if not call:
        return
    src = call["audio_path"]
    wav = os.path.join(WORK_DIR, f"{call_id}.wav")

    db.set_progress(call_id, "converting", 5)
    duration = audio.to_wav(src, wav)
    db.set_duration(call_id, duration)
    logger.info(f"[{call_id}] {duration:.1f}s audio")

    db.set_progress(call_id, "transcribing", 20)
    _, words = asr.transcribe(wav)
    logger.info(f"[{call_id}] {len(words)} words")

    db.set_progress(call_id, "diarizing", 50)
    turns = diarize.diarize(wav)
    logger.info(f"[{call_id}] {len(turns)} speaker turns")

    db.set_progress(call_id, "aligning", 65)
    segments = align.build(words, turns, duration)

    db.set_progress(call_id, "analyzing", 75)
    stats = metrics.compute(segments, duration)

    result = None
    try:
        result = qa.analyze(segments, stats)
    except Exception as e:
        logger.error(f"[{call_id}] QA stage failed: {e}")

    # The LLM sees the whole conversation, so it overrules the heuristic on roles.
    if result and result.get("agent_speaker"):
        segments = align.apply_role_override(segments, result["agent_speaker"])
        stats = metrics.compute(segments, duration)

    db.set_progress(call_id, "saving", 95)
    db.save_transcript(call_id, segments)
    db.save_metrics(call_id, stats)
    if result:
        db.save_qa(call_id, result)
    db.set_completed(call_id)
    logger.info(f"[{call_id}] done — {len(segments)} segments")
