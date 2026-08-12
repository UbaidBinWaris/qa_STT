import logging
import os

import db
from pipeline import align, asr, audio, diarize, metrics, qa, recover, reliability, verify

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

    # Diarization comes before the reliability work now: knowing who was speaking
    # is what reveals the turns that produced no text at all.
    db.set_progress(call_id, "diarizing", 40)
    turns = diarize.diarize(wav)
    logger.info(f"[{call_id}] {len(turns)} speaker turns")

    db.set_progress(call_id, "recovering", 50)
    recovery = recover.recover_dropped(wav, words, turns)

    # Mark doubtful words before alignment, so the flags travel with each word
    # into its speaker turn and on into the QA evidence check.
    report = reliability.analyse(words)
    report["recovery"] = recovery

    # Only the doubtful spans are decoded again, so the cost scales with how much
    # of the call was actually in question rather than with its length.
    db.set_progress(call_id, "verifying", 60)
    report["verification"] = verify.verify_spans(wav, report["spans"], words)
    verify.apply_to_words(words, report["spans"])
    report["flagged"] = sum(1 for w in words if w.get("uncertain"))
    report["conflicts"] = sum(1 for w in words if w.get("conflict"))

    db.set_progress(call_id, "aligning", 65)
    segments = align.build(words, turns, duration)

    for seg in segments:
        seg["confidence"], seg["uncertain"] = reliability.segment_confidence(seg["words"])
    # Cross-talk is marked after segmentation so a turn spoken over is shown as
    # possibly incomplete rather than quietly treated as everything that was said.
    recover.mark_crosstalk(segments, recovery["crosstalk_regions"])

    db.set_progress(call_id, "analyzing", 75)
    interruptions = recover.interruption_events(turns)
    stats = metrics.compute(segments, duration, interruptions)

    result = None
    try:
        result = qa.analyze(segments, stats)
    except Exception as e:
        logger.error(f"[{call_id}] QA stage failed: {e}")

    # The LLM sees the whole conversation, so it overrules the heuristic on roles.
    if result and result.get("agent_speaker"):
        segments = align.apply_role_override(segments, result["agent_speaker"])
        stats = metrics.compute(segments, duration, interruptions)

    db.set_progress(call_id, "saving", 95)
    db.save_transcript(call_id, segments)
    db.save_metrics(call_id, stats)
    db.save_reliability(call_id, report)
    if result:
        db.save_qa(call_id, result)
    db.set_completed(call_id)
    logger.info(f"[{call_id}] done — {len(segments)} segments")
