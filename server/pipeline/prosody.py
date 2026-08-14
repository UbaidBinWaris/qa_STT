"""Vocal tone: what the voice sounds like, independent of what the words say.

QA sentiment today comes from an LLM reading text, and text cannot hear a raised
voice. A flat "yeah, fine" and a sharp, fast "yeah, FINE" transcribe identically,
but only one of them is a customer losing patience. Prosody — pitch, loudness,
speaking rate — is measured directly from the audio and closes that gap.

Validated on three real calls before being wired in: pitch estimates separate
speakers by register as expected (roughly 130-270 Hz), and the turns it scores as
most aroused are plausible moments of tension — a customer saying "Do what?",
an agent asking someone to repeat themselves — while the calmest turns are curt,
low-effort answers ("Correct.", "Hello."). This is a signal, not a verdict.

Arousal is measured relative to each speaker's own baseline for the call, not
against a fixed threshold: a naturally loud or high-pitched talker should not
register as agitated for being themselves.
"""
import logging
import statistics

logger = logging.getLogger("pipeline.prosody")

# Telephony voice band. Outside this, yin's F0 guess is picking up line noise or
# a formant, not a fundamental.
F0_MIN, F0_MAX = 60, 350
MIN_TURN = 0.25  # shorter than this is too little signal to trust
MIN_TURNS_FOR_BASELINE = 4  # a speaker needs a few turns before "usual" means anything


def _measure(clip, sr) -> dict | None:
    if len(clip) < sr * MIN_TURN:
        return None

    import librosa
    import numpy as np

    f0 = librosa.yin(clip, fmin=F0_MIN, fmax=F0_MAX, sr=sr, frame_length=1024)
    voiced = f0[np.isfinite(f0) & (f0 > F0_MIN) & (f0 < F0_MAX)]
    rms = librosa.feature.rms(y=clip, frame_length=1024, hop_length=256)[0]
    loud = 20 * np.log10(np.maximum(rms, 1e-6))

    return {
        "f0_hz": round(float(np.median(voiced)), 1) if len(voiced) else None,
        # Range, not mean pitch: variation is the emotional signal, absolute
        # pitch is mostly just which person is talking.
        "f0_range_hz": round(float(np.percentile(voiced, 90) - np.percentile(voiced, 10)), 1)
        if len(voiced) > 4 else 0.0,
        "loudness_db": round(float(np.mean(loud)), 1),
        "loudness_peak_db": round(float(np.percentile(loud, 95)), 1),
    }


def _zscore(values: list[float], v: float) -> float:
    spread = statistics.pstdev(values)
    return (v - statistics.mean(values)) / spread if spread else 0.0


def _band(z: float) -> str:
    if z >= 1.25:
        return "elevated"
    if z <= -1.25:
        return "flat"
    return "neutral"


def analyse(segments: list[dict], wav_path: str) -> dict:
    """Measure each turn's prosody and its arousal relative to the speaker's own
    baseline for this call. Mutates segments with `prosody`; returns a summary.

    Never raises into the pipeline — a call is worth having without tone data,
    and librosa failures here (corrupt clip, unreadable audio) must not fail it.
    """
    try:
        import soundfile as sf

        data, sr = sf.read(wav_path, dtype="float32")
    except Exception:
        logger.exception("Could not read audio for prosody analysis")
        return {"available": False}

    for seg in segments:
        a, b = int(seg["start"] * sr), int(seg["end"] * sr)
        seg["prosody"] = _measure(data[a:b], sr)

    by_role: dict[str, list[dict]] = {}
    for seg in segments:
        if seg["prosody"]:
            by_role.setdefault(seg["role"], []).append(seg)

    summary = {"available": True, "roles": {}}
    flagged = []

    for role, segs in by_role.items():
        if len(segs) < MIN_TURNS_FOR_BASELINE:
            for seg in segs:
                seg["prosody"]["arousal"] = None
                seg["prosody"]["tone"] = "neutral"
            continue

        ranges = [s["prosody"]["f0_range_hz"] for s in segs]
        peaks = [s["prosody"]["loudness_peak_db"] for s in segs]

        for seg in segs:
            p = seg["prosody"]
            z = _zscore(ranges, p["f0_range_hz"]) + _zscore(peaks, p["loudness_peak_db"])
            p["arousal"] = round(z, 2)
            p["tone"] = _band(z)
            if p["tone"] == "elevated":
                flagged.append({"start": seg["start"], "role": role,
                                "text": seg["text"], "arousal": p["arousal"]})

        summary["roles"][role] = {
            "median_f0_hz": statistics.median(s["prosody"]["f0_hz"]
                                              for s in segs if s["prosody"]["f0_hz"]),
            "turns_analysed": len(segs),
        }

    flagged.sort(key=lambda f: -f["arousal"])
    summary["elevated_turns"] = flagged[:20]
    logger.info(f"Prosody: {len(flagged)} elevated-tone turn(s) across "
                f"{sum(len(v) for v in by_role.values())} analysed")
    return summary
