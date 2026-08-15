"""Recover speech the full-call pass dropped, and mark what cannot be recovered.

Diarization regularly finds a speaker talking where the transcript has nothing at
all. Measured across three real calls: six such turns, and re-decoding those few
seconds in isolation recovered four of them — "Can you work with", "All right.",
"So right?", "How I". Short turns lose out in a long decode because the model is
weighing them against minutes of surrounding audio; given only their own seconds,
they come back.

The remaining cases are genuine cross-talk, where two people speak at once. A
single mixed mono recording does not contain a separable second voice, so nothing
here can recover it. That region is marked instead: the QA layer must know the
transcript is *incomplete* there rather than treating silence as "nobody spoke",
because a customer's objection lost under the agent's voice would otherwise
disappear without trace.

Audio enhancement was measured for this and rejected. Normalising or denoising the
whole call produced more words but lower confidence, and on the clips it recovered
nothing extra while dropping a word from one turn that decoded correctly raw.
"""
import logging
import os

logger = logging.getLogger("pipeline.recover")

# Shorter than this is usually a breath or a diarization edge, not lost speech.
MIN_TURN = 0.6
# A little context helps the decoder settle; too much reintroduces the neighbour.
PAD = 0.3
MAX_ATTEMPTS = int(os.environ.get("RECOVER_MAX_TURNS", "40"))
# Simultaneous speech shorter than this is turn-taking, not talking over.
MIN_OVERLAP = 0.25


def find_overlaps(turns: list[dict]) -> list[dict]:
    """Regions where two different speakers are active at the same time."""
    regions = []
    for i, a in enumerate(turns):
        for b in turns[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if b["speaker"] == a["speaker"]:
                continue
            lo, hi = max(a["start"], b["start"]), min(a["end"], b["end"])
            if hi - lo >= MIN_OVERLAP:
                regions.append({"start": lo, "end": hi,
                                "speakers": sorted({a["speaker"], b["speaker"]})})

    regions.sort(key=lambda r: r["start"])
    merged: list[dict] = []
    for r in regions:
        if merged and r["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], r["end"])
            merged[-1]["speakers"] = sorted(set(merged[-1]["speakers"]) | set(r["speakers"]))
        else:
            merged.append(dict(r))
    return merged


def interruption_events(turns: list[dict]) -> list[dict]:
    """Who started speaking while someone else still held the floor.

    This has to be read from the diarized turns, which genuinely overlap. The
    aligned segments cannot answer it: they are built from consecutive words, so
    one always starts at or after the previous one ends, and any overlap test on
    them is arithmetically dead.
    """
    events = []
    for i, a in enumerate(turns):
        for b in turns[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if b["speaker"] == a["speaker"]:
                continue
            overlap = min(a["end"], b["end"]) - b["start"]
            if overlap < MIN_OVERLAP:
                continue
            # The later starter is the one who cut in.
            interrupter, interrupted = (b, a) if b["start"] >= a["start"] else (a, b)
            events.append({
                "speaker": interrupter["speaker"],
                "interrupted": interrupted["speaker"],
                "start": round(interrupter["start"], 2),
                "duration": round(overlap, 2),
            })
    events.sort(key=lambda e: e["start"])
    return events


def _empty_turns(words: list[dict], turns: list[dict]) -> list[dict]:
    out = []
    for t in turns:
        if t["end"] - t["start"] < MIN_TURN:
            continue
        if any(w["start"] < t["end"] and w["end"] > t["start"] for w in words):
            continue
        out.append(t)
    return out


def recover_dropped(wav_path: str, words: list[dict], turns: list[dict]) -> dict:
    """Re-decode turns that produced no text. Returns a summary; `words` is
    extended in place with anything recovered, kept in timestamp order."""
    overlaps = find_overlaps(turns)
    empty = _empty_turns(words, turns)
    summary = {
        "empty_turns": len(empty),
        "recovered": 0,
        "recovered_words": 0,
        "crosstalk_regions": overlaps,
        "crosstalk_seconds": round(sum(o["end"] - o["start"] for o in overlaps), 2),
        "unrecoverable": 0,
    }
    if not empty:
        return summary

    import soundfile as sf

    from pipeline import asr

    todo = empty[:MAX_ATTEMPTS]
    if len(empty) > MAX_ATTEMPTS:
        logger.warning(f"{len(empty)} empty turns; attempting the first {MAX_ATTEMPTS}.")

    # Per-call subdirectory: with multiple GPU workers, two jobs can be in
    # this function at once, and a shared "_recover" directory let one job's
    # cleanup delete a file the other job still had open.
    call_tag = os.path.splitext(os.path.basename(wav_path))[0]
    tmp_dir = os.path.join(os.path.dirname(wav_path), "_recover", call_tag)
    os.makedirs(tmp_dir, exist_ok=True)
    made = []
    try:
        model = asr.load()
        data, sr = sf.read(wav_path, dtype="float32")
        total = len(data) / sr

        for i, turn in enumerate(todo):
            a = max(0.0, turn["start"] - PAD)
            b = min(total, turn["end"] + PAD)
            clip = os.path.join(tmp_dir, f"turn_{i}.wav")
            sf.write(clip, data[int(a * sr):int(b * sr)], sr)
            made.append(clip)

            try:
                hyp = asr.decode(model, clip)
            except Exception:
                logger.exception(f"Recovery decode failed at {turn['start']:.1f}s")
                continue

            found = [
                {
                    "word": w["word"],
                    # Timestamps are relative to the clip, so shift them back.
                    "start": float(w["start"]) + a,
                    "end": float(w["end"]) + a,
                    "confidence": None,
                    "recovered": True,
                }
                for w in hyp.timestamp.get("word", [])
            ]
            del hyp
            asr._release()

            if not found:
                if any(o["start"] < turn["end"] and o["end"] > turn["start"] for o in overlaps):
                    summary["unrecoverable"] += 1
                continue

            words.extend(found)
            summary["recovered"] += 1
            summary["recovered_words"] += len(found)
            logger.info(
                f"Recovered {len(found)} word(s) at {turn['start']:.1f}s: "
                f"{' '.join(w['word'] for w in found)[:60]!r}"
            )
    finally:
        for clip in made:
            if os.path.exists(clip):
                os.remove(clip)
        if os.path.isdir(tmp_dir) and not os.listdir(tmp_dir):
            os.rmdir(tmp_dir)

    if summary["recovered_words"]:
        words.sort(key=lambda w: w["start"])

    logger.info(
        f"Recovery: {summary['recovered']}/{len(todo)} empty turn(s) recovered "
        f"({summary['recovered_words']} words); {summary['crosstalk_seconds']}s cross-talk, "
        f"{summary['unrecoverable']} region(s) unrecoverable"
    )
    return summary


def mark_crosstalk(segments: list[dict], overlaps: list[dict]):
    """Flag turns that sit under cross-talk as possibly incomplete."""
    for seg in segments:
        if any(o["start"] < seg["end"] and o["end"] > seg["start"] for o in overlaps):
            seg["crosstalk"] = True
            seg["uncertain"] = True
