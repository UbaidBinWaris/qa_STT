import logging
import queue
import threading

import db
from pipeline import run

logger = logging.getLogger("jobs")

# One GPU worker. A 6-minute call is ~5s of GPU work, so queueing beats
# contending for VRAM between concurrent jobs.
_queue: queue.Queue[str] = queue.Queue()
_worker: threading.Thread | None = None


def _loop():
    while True:
        call_id = _queue.get()
        try:
            run.process(call_id)
        except Exception as e:
            logger.exception(f"Job {call_id} failed")
            db.set_failed(call_id, f"{type(e).__name__}: {e}")
        finally:
            # empty_cache() alone reclaims nothing here: the decoder's alignment
            # tensors are unreachable but still referenced through cycles, so the
            # collection has to run first.
            try:
                import gc

                import torch

                gc.collect()
                torch.cuda.empty_cache()
            except Exception:
                pass
            _queue.task_done()


def start():
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=_loop, daemon=True, name="gpu-worker")
        _worker.start()
        logger.info("GPU worker started.")


def submit(call_id: str):
    _queue.put(call_id)


def depth() -> int:
    return _queue.qsize()
