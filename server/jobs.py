import logging
import os
import queue
import threading
import time

import db
from pipeline import run

logger = logging.getLogger("jobs")

# Multiple worker threads pull from one queue. Real GPU concurrency is still
# bounded, but not by this: asr.py and diarize.py each hold their own
# threading.Lock around their model, and qa.py now does the same around the
# Ollama call — so two workers never run two decodes or two LLM calls at once.
# What DOES overlap is the parts that don't compete for the same lock: one
# job's Ollama HTTP round-trip (I/O wait, not GPU-bound on this process) can
# run alongside another job's ASR or diarization pass. That overlap is where
# the throughput gain actually comes from.
#
# Measured before choosing 2: this card peaks at 12.6 GB VRAM with ONE job
# in its QA stage (speech models resident + Qwen3 8B loaded). Free at that
# peak was 3.6 GB. A second job's diarization pass fits there (~1.2 GB
# resident); its ASR pass is tighter but the locks above mean the two heavy
# stages never actually run simultaneously — only ASR/diarize overlaps with
# QA wait time, not with each other's peak. Verified empirically: see
# tests/eval_concurrency.py.
WORKER_THREADS = int(os.environ.get("WORKER_THREADS", "2"))

_queue: queue.Queue[str] = queue.Queue()
_workers: list[threading.Thread] = []
_active = 0
_active_lock = threading.Lock()
_retries: dict[str, int] = {}

# Measured under a deliberate 27-call burst (every call in the test corpus
# submitted at once against 2 workers): the two longest calls (10 and 20
# minutes) hit CUDA OOM. Root cause is real, not a bug to paper over — a long
# call's chunked ASR decode can land at the same moment as another job's QA
# peak (measured at 12.6 GB alone), and a burst that size is not what 2 workers
# were sized for. But failing the call outright throws away a real recording
# over what is usually a timing accident. One retry, after the GPU has had a
# moment to release what the colliding job was holding, recovers it instead.
MAX_OOM_RETRIES = 2
OOM_RETRY_DELAY = 5.0


def _is_oom(exc: Exception) -> bool:
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _loop():
    global _active
    while True:
        call_id = _queue.get()
        with _active_lock:
            _active += 1
        try:
            run.process(call_id)
            _retries.pop(call_id, None)
        except Exception as e:
            if _is_oom(e) and _retries.get(call_id, 0) < MAX_OOM_RETRIES:
                _retries[call_id] = _retries.get(call_id, 0) + 1
                logger.warning(
                    f"Job {call_id} hit CUDA OOM (attempt {_retries[call_id]}/"
                    f"{MAX_OOM_RETRIES}); retrying after other jobs release memory."
                )
                db.set_progress(call_id, "queued", 0)
                time.sleep(OOM_RETRY_DELAY)
                _queue.put(call_id)
            else:
                logger.exception(f"Job {call_id} failed")
                db.set_failed(call_id, f"{type(e).__name__}: {e}")
                _retries.pop(call_id, None)
        finally:
            # empty_cache() alone reclaims nothing here: the decoder's alignment
            # tensors are unreachable but still referenced through cycles, so the
            # collection has to run first. Safe to call from multiple workers:
            # it only returns memory that is not currently referenced by anyone.
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except Exception:
                pass
            with _active_lock:
                _active -= 1
            _queue.task_done()


def start():
    if _workers:
        return
    for i in range(max(1, WORKER_THREADS)):
        t = threading.Thread(target=_loop, daemon=True, name=f"gpu-worker-{i}")
        t.start()
        _workers.append(t)
    logger.info(f"{len(_workers)} GPU worker(s) started.")


def submit(call_id: str):
    _queue.put(call_id)


def depth() -> int:
    """Jobs waiting for a free worker. A worker removes its job from the queue
    the instant it picks it up, so qsize() already excludes in-progress jobs —
    active() below is a separate, additional count, not a subset to subtract."""
    return _queue.qsize()


def active() -> int:
    """Jobs currently being worked on by a worker thread right now."""
    with _active_lock:
        return _active
