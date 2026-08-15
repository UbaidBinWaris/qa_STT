"""Run a NestJS-dispatched job through the existing pipeline.

This reuses pipeline/run.py rather than duplicating it, so every accuracy feature
built into the local system — confidence scoring, second-pass verification,
dropped-speech recovery, cross-talk marking, prosody — applies identically to
recordings that arrive from the portal.

The difference is only where the audio comes from and where the results go:
object storage in, HTTP callbacks out, no database writes.
"""
import logging
import os
import queue
import threading
import time

import worker_api

logger = logging.getLogger("worker_jobs")

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(SERVER_DIR, "outputs", "_remote")

_queue: "queue.Queue[dict]" = queue.Queue()
_started = False
_lock = threading.Lock()

# Progress is reported per stage so the portal can show something truthful while
# a long call is being processed, rather than a spinner with no information.
STAGE_PROGRESS = {
    "downloading": 5,
    "converting": 10,
    "transcribing": 25,
    "diarizing": 45,
    "recovering": 55,
    "verifying": 65,
    "aligning": 72,
    "analyzing": 85,
    "saving": 95,
}


def submit(job: dict):
    _queue.put(job)
    return _queue.qsize()


def start():
    """One remote worker thread. The GPU-heavy stages inside the pipeline hold
    their own locks, so this coexists safely with the local dashboard's workers."""
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True, name="remote-worker").start()
        _started = True
        logger.info("remote job worker started")


def _loop():
    while True:
        job = _queue.get()
        try:
            _process(job)
        except Exception as e:
            logger.exception(f"remote job {job.get('callId')} failed")
            worker_api.report(
                job.get("callbackUrl", ""),
                {
                    "callId": job.get("callId"),
                    "status": "FAILED",
                    "stage": "failed",
                    "error": f"{type(e).__name__}: {e}"[:500],
                },
            )
        finally:
            _queue.task_done()


def _process(job: dict):
    call_id = job["callId"]
    key = job["objectKey"]
    callback = job.get("callbackUrl", "")

    os.makedirs(WORK_DIR, exist_ok=True)
    ext = os.path.splitext(key)[1] or ".audio"
    src = os.path.join(WORK_DIR, f"{call_id}{ext}")
    wav = os.path.join(WORK_DIR, f"{call_id}.wav")

    def progress(stage: str):
        worker_api.report(callback, {
            "callId": call_id,
            "status": "PROCESSING",
            "stage": stage,
            "progress": STAGE_PROGRESS.get(stage, 0),
        })

    try:
        progress("downloading")
        worker_api.fetch_object(key, src)

        from pipeline import (
            align, asr, audio, diarize, metrics, prosody, qa, recover,
            reliability, verify,
        )

        progress("converting")
        duration = audio.to_wav(src, wav)

        progress("transcribing")
        _, words = asr.transcribe(wav)

        progress("diarizing")
        turns = diarize.diarize(wav)

        progress("recovering")
        recovery = recover.recover_dropped(wav, words, turns)

        report = reliability.analyse(words)
        report["recovery"] = recovery

        progress("verifying")
        report["verification"] = verify.verify_spans(wav, report["spans"], words)
        verify.apply_to_words(words, report["spans"])
        report["flagged"] = sum(1 for w in words if w.get("uncertain"))
        report["conflicts"] = sum(1 for w in words if w.get("conflict"))

        progress("aligning")
        segments = align.build(words, turns, duration)
        for seg in segments:
            seg["confidence"], seg["uncertain"] = reliability.segment_confidence(
                seg["words"]
            )
        recover.mark_crosstalk(segments, recovery["crosstalk_regions"])
        tone = prosody.analyse(segments, wav)

        progress("analyzing")
        interruptions = recover.interruption_events(turns)
        stats = metrics.compute(segments, duration, interruptions)

        result = None
        try:
            result = qa.analyze(segments, stats)
        except Exception as e:
            # A failed QA pass must not lose the transcript, which is the
            # expensive part and useful on its own.
            logger.error(f"[{call_id}] QA stage failed: {e}")

        if result and result.get("agent_speaker"):
            segments = align.apply_role_override(segments, result["agent_speaker"])
            stats = metrics.compute(segments, duration, interruptions)

        progress("saving")
        # The converted audio is what the portal plays: it is what the transcript
        # timestamps actually refer to.
        try:
            worker_api.put_object(f"derived/{call_id}/audio.wav", wav, "audio/wav")
        except Exception as e:
            logger.warning(f"[{call_id}] could not upload derived audio: {e}")

        worker_api.report(callback, {
            "callId": call_id,
            "status": "COMPLETED",
            "stage": "done",
            "progress": 100,
            "durationSeconds": duration,
            "error": None,
            "result": {
                "score": (result or {}).get("score"),
                "reliabilityScore": report.get("score"),
                "transcript": segments,
                "metrics": stats,
                "qa": result,
                "reliability": {k: v for k, v in report.items() if k != "spans"},
                "prosody": tone,
            },
        })
        logger.info(f"[{call_id}] remote job complete — {len(segments)} segments")
    finally:
        for path in (src, wav):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def depth() -> int:
    return _queue.qsize()
