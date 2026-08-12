"""Second-pass verification of the spans the first pass was unsure about.

The failure that matters most in call QA is a confident-but-wrong transcription:
"I don't want the plan" heard as "I do want the plan" inverts the verdict, and no
amount of downstream reasoning recovers it, because the analyst never hears the
audio.

So the flagged spans — and only those — are decoded a second time and the two
readings are compared. The second opinion is worth something only if it is
genuinely independent, which here means two differences at once:

  search   greedy decoding versus beam ('malsd_batch', beam 4)
  context  the whole call or a 180 s window, versus a few seconds around the span

When the two agree the span is confirmed. When they disagree the span is NOT
silently rewritten: nothing here can tell which reading is correct, so it is
marked as conflicting and sent to a human with the audio. Guessing would trade a
visible doubt for an invisible error.
"""
import logging
import os
import re
import time

logger = logging.getLogger("pipeline.verify")

# Context around the span. A span decoded in isolation loses the acoustic context
# the model needs, which manufactures disagreements rather than finding them.
PAD = 4.0
MAX_SPANS = int(os.environ.get("VERIFY_MAX_SPANS", "25"))
# Fraction of the span's content words the second pass must also produce.
AGREE_FULL = 1.0
AGREE_PARTIAL = 0.6

CONFIRMED = "confirmed"
LIKELY = "likely"
CONFLICT = "conflict"
UNCHECKED = "unchecked"

# Function words carry no disputable content: a span consisting only of "your"
# or "at" produced a conflict on every call, which is noise a reviewer learns to
# ignore. Negations are deliberately absent from this list — "not" and "don't"
# are the words whose loss matters most, and they must always be adjudicated.
_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "in", "is", "it", "that", "this",
    "so", "you", "i", "we", "they", "uh", "um", "yeah", "okay", "into", "your",
    "at", "on", "for", "with", "my", "me", "his", "her", "their", "our", "was",
    "were", "be", "been", "am", "are", "have", "has", "had", "do", "does", "did",
    "but", "if", "as", "from", "by", "up", "out", "about", "just", "like",
    "well", "right", "got", "know", "oh", "hmm", "mm", "he", "she", "them",
}


def _tokens(text: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9 ]", " ", text.lower()).split() if t]


def _content(text: str) -> list[str]:
    return [t for t in _tokens(text) if t not in _STOP]


def _agreement(original: str, second: str) -> float:
    """Share of the original's content words the second pass also produced."""
    want = _content(original)
    if not want:
        # Nothing but filler — treat as agreement, there is nothing to dispute.
        return 1.0
    got = set(_tokens(second))
    return sum(1 for t in want if t in got) / len(want)


def _window_text(words: list[dict], start: float, end: float) -> str:
    """What the first pass heard across the same audio the clip covers."""
    return " ".join(w["word"] for w in words if w["end"] > start and w["start"] < end)


def _classify(span_text: str, window_text: str, second: str) -> tuple[str, float]:
    """Compare the two readings of one span.

    The comparison is made over the whole padded window, not the span alone: a
    one-word span judged against nine seconds of beam output disagrees for
    trivial reasons, which buries the real disagreements in noise.

    A localised difference — the surrounding words match but the span's own words
    are absent — is the signal worth acting on. Where the entire window disagrees,
    the second decode is itself unreliable (usually poor audio), so the span is
    marked for review rather than presented as a contradiction.
    """
    span_ok = _agreement(span_text, second) >= AGREE_FULL
    window = _agreement(window_text, second)

    if span_ok:
        return CONFIRMED, round(window, 2)
    if window >= AGREE_PARTIAL:
        return CONFLICT, round(window, 2)
    return LIKELY, round(window, 2)


def verify_spans(wav_path: str, spans: list[dict], words: list[dict] | None = None) -> dict:
    """Re-decode each flagged span and classify agreement.

    Mutates each span with `verdict`, `second_pass` and `agreement`. Returns a
    summary. Verification never raises into the pipeline: a failure here must
    leave the call transcribed rather than failing it outright.
    """
    if not spans:
        return {"checked": 0, "confirmed": 0, "likely": 0, "conflict": 0, "seconds": 0.0}

    import soundfile as sf

    from pipeline import asr

    started = time.time()
    model = asr.load()
    todo = spans[:MAX_SPANS]
    if len(spans) > MAX_SPANS:
        # Never silently cover less than the caller thinks.
        logger.warning(f"{len(spans)} spans flagged; verifying the first {MAX_SPANS}.")

    tmp_dir = os.path.join(os.path.dirname(wav_path), "_verify")
    os.makedirs(tmp_dir, exist_ok=True)
    clips = []

    try:
        data, sr = sf.read(wav_path, dtype="float32")
        total = len(data) / sr
        windows = []
        for i, span in enumerate(todo):
            a = max(0.0, span["start"] - PAD)
            b = min(total, span["end"] + PAD)
            windows.append(_window_text(words or [], a, b))
            path = os.path.join(tmp_dir, f"span_{i}.wav")
            sf.write(path, data[int(a * sr):int(b * sr)], sr)
            clips.append(path)

        with asr._lock:
            asr.use_verification_decoder(model)
            try:
                # One batched call: switching decoders is far more expensive than
                # decoding, so it must happen once per call, not once per span.
                hyps = model.transcribe(clips, timestamps=False, verbose=False)
                texts = [getattr(h, "text", "") or "" for h in hyps]
            finally:
                asr.use_primary_decoder(model)
                asr._release()
    except Exception as e:
        logger.exception("Second pass failed; spans left unchecked.")
        for span in spans:
            span.setdefault("verdict", UNCHECKED)
        return {"checked": 0, "confirmed": 0, "likely": 0, "conflict": 0,
                "seconds": round(time.time() - started, 2), "error": str(e)[:200]}
    finally:
        for path in clips:
            if os.path.exists(path):
                os.remove(path)
        if os.path.isdir(tmp_dir) and not os.listdir(tmp_dir):
            os.rmdir(tmp_dir)

    counts = {CONFIRMED: 0, LIKELY: 0, CONFLICT: 0}
    for span, second, window in zip(todo, texts, windows):
        verdict, ratio = _classify(span["text"], window or span["text"], second)
        span["verdict"] = verdict
        span["agreement"] = round(ratio, 2)
        # Only worth storing when it differs — otherwise it is noise in the UI.
        span["second_pass"] = second.strip()[:300] if verdict != CONFIRMED else None
        counts[verdict] += 1

    for span in spans[len(todo):]:
        span.setdefault("verdict", UNCHECKED)

    elapsed = time.time() - started
    logger.info(
        f"Second pass: {counts[CONFIRMED]} confirmed, {counts[LIKELY]} likely, "
        f"{counts[CONFLICT]} conflicting across {len(todo)} span(s) in {elapsed:.1f}s"
    )
    return {
        "checked": len(todo),
        "confirmed": counts[CONFIRMED],
        "likely": counts[LIKELY],
        "conflict": counts[CONFLICT],
        "seconds": round(elapsed, 2),
    }


def apply_to_words(words: list[dict], spans: list[dict]):
    """Push each span's verdict down onto the words it covers.

    Confirming a span clears its uncertainty: the doubt has been checked and
    resolved, and leaving the mark would train reviewers to ignore all marks.
    """
    for span in spans:
        verdict = span.get("verdict")
        if not verdict or verdict == UNCHECKED:
            continue
        for w in words:
            if w["start"] >= span["start"] - 0.01 and w["end"] <= span["end"] + 0.01:
                w["verdict"] = verdict
                if verdict == CONFIRMED:
                    w["uncertain"] = False
                elif verdict == CONFLICT:
                    w["conflict"] = True
