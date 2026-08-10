import logging
import os
import tempfile
import time

logger = logging.getLogger("warmup")


def _speechlike_wav(path: str, seconds: float = 6.0, sr: int = 16000):
    """Synthesize a voiced-sounding signal to exercise the full decode path.

    Real speech would warm the decoder marginally better, but baking a clip of a
    customer call into the repo is not worth it — this builds the same CUDA graphs.
    """
    import numpy as np
    import soundfile as sf

    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    f0 = 120 + 40 * np.sin(2 * np.pi * 0.7 * t)          # wandering pitch
    signal = sum(np.sin(2 * np.pi * f0 * h * t) / h for h in (1, 2, 3, 4))
    envelope = (np.sin(2 * np.pi * 3.1 * t) > -0.3).astype(np.float32)  # syllable rate
    audio = (signal * envelope * 0.2).astype(np.float32)
    sf.write(path, audio, sr)


def check_llm() -> bool:
    """Confirm the QA model is actually pulled. A missing model otherwise fails
    silently per-call and leaves every scorecard empty."""
    import requests

    from pipeline import qa

    try:
        resp = requests.get(f"{qa.OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
        names = {m["name"] for m in resp.json().get("models", [])}
    except Exception as e:
        logger.warning(f"Ollama unreachable at {qa.OLLAMA_URL} ({e}) — QA analysis will be skipped.")
        logger.warning("  Start it with: ollama serve")
        return False

    if qa.QA_MODEL not in names and f"{qa.QA_MODEL}:latest" not in names:
        logger.warning(f"QA model '{qa.QA_MODEL}' is not pulled — QA analysis will be skipped.")
        logger.warning(f"  Fix with: ollama pull {qa.QA_MODEL}")
        return False

    logger.info(f"QA model '{qa.QA_MODEL}' available.")
    return True


def run() -> dict:
    """Load both speech models and run a real inference pass through each, so the
    first uploaded call does not pay graph-compilation cost or surface a broken
    dependency."""
    from pipeline import asr, diarize

    report = {}
    started = time.time()

    t = time.time()
    asr.load()
    report["asr_load"] = round(time.time() - t, 1)

    t = time.time()
    diarize.load()
    report["diarization_load"] = round(time.time() - t, 1)

    tmp = os.path.join(tempfile.gettempdir(), "qa_stt_warmup.wav")
    try:
        _speechlike_wav(tmp)

        t = time.time()
        asr.transcribe(tmp)
        report["asr_pass"] = round(time.time() - t, 2)

        t = time.time()
        diarize.diarize(tmp)
        report["diarization_pass"] = round(time.time() - t, 2)
    except Exception as e:
        logger.warning(f"Warm-up inference pass failed ({e}); models are loaded regardless.")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    report["llm_ready"] = check_llm()
    report["total"] = round(time.time() - started, 1)

    logger.info(
        f"Warm-up complete in {report['total']}s "
        f"(ASR load {report['asr_load']}s, diarization load {report['diarization_load']}s)"
    )
    return report
