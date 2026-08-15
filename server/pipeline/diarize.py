import logging
import os
import threading

logger = logging.getLogger("pipeline.diarize")

MODEL_ID = os.environ.get("DIAR_MODEL", "nvidia/diar_sortformer_4spk-v1")

_model = None
_lock = threading.Lock()


def load():
    global _model
    with _lock:
        if _model is None:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel

            logger.info(f"Loading diarization model {MODEL_ID}...")
            m = SortformerEncLabelModel.from_pretrained(MODEL_ID)
            m = m.to("cuda" if torch.cuda.is_available() else "cpu").eval()
            _model = m
            logger.info("Diarization model resident.")
    return _model


CHUNK_THRESHOLD = 420.0
CHUNK_LENGTH = 300.0
CHUNK_OVERLAP = 45.0


def _run(wav_path: str, offset: float = 0.0) -> list[dict]:
    model = load()
    with _lock:
        preds = model.diarize(audio=[wav_path], batch_size=1)
    turns = []
    for line in preds[0]:
        start, end, speaker = line.split()
        turns.append(
            {"speaker": speaker, "start": float(start) + offset, "end": float(end) + offset}
        )
    turns.sort(key=lambda t: t["start"])
    return turns


def _overlap(a, b) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def _stitch(prev: list[dict], new: list[dict], seam_start: float) -> dict[str, str]:
    """Map this chunk's labels onto the previous chunk's by agreement in the overlap."""
    prev_seam = [t for t in prev if t["end"] > seam_start]
    agreement: dict[tuple[str, str], float] = {}
    for n in new:
        if n["start"] >= seam_start + CHUNK_OVERLAP:
            continue
        for p in prev_seam:
            ov = _overlap(n, p)
            if ov > 0:
                key = (n["speaker"], p["speaker"])
                agreement[key] = agreement.get(key, 0.0) + ov

    mapping: dict[str, str] = {}
    taken: set[str] = set()
    for (new_label, prev_label), _ in sorted(agreement.items(), key=lambda kv: -kv[1]):
        if new_label not in mapping and prev_label not in taken:
            mapping[new_label] = prev_label
            taken.add(prev_label)
    return mapping


def _diarize_chunked(wav_path: str, duration: float) -> list[dict]:
    import soundfile as sf

    sr = sf.info(wav_path).samplerate
    # Per-call subdirectory: with multiple GPU workers, two jobs can be in
    # this function at once, and a shared "_diar_chunks" directory let one job's
    # cleanup delete a file the other job still had open.
    call_tag = os.path.splitext(os.path.basename(wav_path))[0]
    tmp_dir = os.path.join(os.path.dirname(wav_path), "_diar_chunks", call_tag)
    os.makedirs(tmp_dir, exist_ok=True)

    turns: list[dict] = []
    offset = 0.0
    idx = 0
    unknown = 0
    try:
        while offset < duration:
            length = min(CHUNK_LENGTH, duration - offset)
            data, _ = sf.read(
                wav_path, start=int(offset * sr), frames=int(length * sr), dtype="float32"
            )
            part = os.path.join(tmp_dir, f"diar_{idx}.wav")
            sf.write(part, data, sr)
            new = _run(part, offset)
            os.remove(part)

            if turns:
                mapping = _stitch(turns, new, offset)
                for t in new:
                    if t["speaker"] not in mapping:
                        unknown += 1
                        mapping[t["speaker"]] = f"speaker_x{unknown}"
                    t["speaker"] = mapping[t["speaker"]]
                cutoff = turns[-1]["end"]
                new = [t for t in new if t["end"] > cutoff]
                for t in new:
                    t["start"] = max(t["start"], cutoff)
            turns.extend(t for t in new if t["end"] > t["start"])

            idx += 1
            offset += CHUNK_LENGTH - CHUNK_OVERLAP
            if length < CHUNK_LENGTH:
                break
    finally:
        if os.path.isdir(tmp_dir):
            for leftover in os.listdir(tmp_dir):
                os.remove(os.path.join(tmp_dir, leftover))
            os.rmdir(tmp_dir)

    turns.sort(key=lambda t: t["start"])
    logger.info(f"Chunked diarization: {idx} windows, {len(turns)} turns")
    return turns


def diarize(wav_path: str) -> list[dict]:
    """Returns speaker turns sorted by start time: [{speaker, start, end}]."""
    import soundfile as sf

    info = sf.info(wav_path)
    duration = info.frames / info.samplerate
    if duration > CHUNK_THRESHOLD:
        return _diarize_chunked(wav_path, duration)
    return _run(wav_path)
