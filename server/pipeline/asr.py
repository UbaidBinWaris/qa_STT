import logging
import os
import threading

logger = logging.getLogger("pipeline.asr")

# Newest v3 Parakeet. NVIDIA publishes no v3 above 0.6b; 1.1b exists but is
# older v2-era and English-only. Override if that ever changes.
MODEL_ID = os.environ.get("ASR_MODEL", "nvidia/parakeet-tdt-0.6b-v3")

_model = None
_lock = threading.Lock()


def load():
    global _model
    with _lock:
        if _model is None:
            import torch
            import nemo.collections.asr as nemo_asr

            logger.info(f"Loading ASR model {MODEL_ID}...")
            m = nemo_asr.models.ASRModel.from_pretrained(MODEL_ID)
            m = m.to("cuda" if torch.cuda.is_available() else "cpu").eval()
            _model = m
            logger.info("ASR model resident.")
    return _model


# Attention is quadratic in sequence length, so a whole-call pass OOMs on long
# audio. Beyond this, transcribe in overlapping windows and splice.
CHUNK_THRESHOLD = 240.0
CHUNK_LENGTH = 180.0
CHUNK_OVERLAP = 15.0


def _release():
    """Return the decoder's scratch memory to the allocator.

    Greedy TDT decoding with timestamps builds a BatchedAlignments holding the
    full joint output — time frames x 8198 vocab, ~0.7 GB for a 3-minute window.
    Those tensors sit in reference cycles, so torch.cuda.empty_cache() on its own
    reclaims nothing: the memory is still referenced, merely unreachable. Without
    the collection first, each call strands roughly 0.8 GB and the process climbs
    from 3 GB to over 12 GB across a handful of jobs, until a long call OOMs.
    """
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _words_of(hyp, offset: float = 0.0) -> list[dict]:
    return [
        {
            "word": w["word"],
            "start": float(w["start"]) + offset,
            "end": float(w["end"]) + offset,
            "confidence": float(w["confidence"]) if "confidence" in w else None,
        }
        for w in hyp.timestamp.get("word", [])
    ]


def _transcribe_chunked(wav_path: str) -> list[dict]:
    import soundfile as sf

    model = load()
    info = sf.info(wav_path)
    sr, duration = info.samplerate, info.frames / info.samplerate

    tmp_dir = os.path.join(os.path.dirname(wav_path), "_chunks")
    os.makedirs(tmp_dir, exist_ok=True)

    words: list[dict] = []
    offset = 0.0
    idx = 0
    try:
        while offset < duration:
            length = min(CHUNK_LENGTH, duration - offset)
            data, _ = sf.read(
                wav_path, start=int(offset * sr), frames=int(length * sr), dtype="float32"
            )
            part = os.path.join(tmp_dir, f"chunk_{idx}.wav")
            sf.write(part, data, sr)
            with _lock:
                hyp = model.transcribe([part], timestamps=True, verbose=False)[0]
            os.remove(part)

            new = _words_of(hyp, offset)
            # Release per window, not just per call: a 20-minute recording is
            # seven windows, and holding all of them at once is what pushed the
            # long calls into OOM part-way through a single job.
            del hyp
            _release()
            # Drop words the previous window already covered.
            if words:
                cutoff = words[-1]["end"]
                new = [w for w in new if w["start"] >= cutoff]
            words.extend(new)

            idx += 1
            offset += CHUNK_LENGTH - CHUNK_OVERLAP
            if length < CHUNK_LENGTH:
                break
    finally:
        if os.path.isdir(tmp_dir):
            for leftover in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, leftover))
            os.rmdir(tmp_dir)

    logger.info(f"Chunked transcription: {idx} windows, {len(words)} words")
    return words


def transcribe(wav_path: str) -> tuple[str, list[dict]]:
    """Transcribe a full call. Returns (text, words with absolute timestamps)."""
    import soundfile as sf

    info = sf.info(wav_path)
    duration = info.frames / info.samplerate

    if duration > CHUNK_THRESHOLD:
        words = _transcribe_chunked(wav_path)
    else:
        model = load()
        with _lock:
            hyp = model.transcribe([wav_path], timestamps=True, verbose=False)[0]
        words = _words_of(hyp)
        del hyp
        _release()

    return " ".join(w["word"] for w in words), words
