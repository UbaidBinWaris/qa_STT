#!/usr/bin/env python3
"""Prove multi-worker processing is safe, not just fast.

Fires several distinct calls at once, watches nvidia-smi throughout for the
whole run, and fails loudly if the peak crosses a safety margin below the
card's total VRAM — instead of waiting to find out via a production OOM.
"""
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BASE = os.environ.get("BASE_URL", "http://localhost:8000")
SAFETY_MARGIN_MB = 512  # refuse to call it safe if we came within this of the card's total


def curl(session, *args):
    import requests

    method, path = args[0], args[1]
    kwargs = {k: v for k, v in zip(args[2::2], args[3::2])}
    return getattr(session, method)(f"{BASE}{path}", **kwargs)


def gpu_total_mb():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout
    return int(out.strip().splitlines()[0])


def gpu_used_mb():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    ).stdout
    return int(out.strip().splitlines()[0])


def main():
    import requests

    password = os.environ.get("APP_PASSWORD")
    if not password:
        print("APP_PASSWORD not set.")
        return 1

    s = requests.Session()
    s.post(f"{BASE}/api/login", json={"password": password}).raise_for_status()

    audio_dir = os.path.join(REPO, "test-audio")
    files = sorted(
        (os.path.join(audio_dir, f) for f in os.listdir(audio_dir) if f.endswith(".mp3")),
        key=os.path.getsize,
    )
    if len(files) < 2:
        print("Need at least 2 distinct audio files in test-audio/.")
        return 1

    n = min(int(sys.argv[1]) if len(sys.argv) > 1 else 3, len(files))
    picks = files[:n]

    monitor_stop = threading.Event()
    samples = []

    def monitor():
        while not monitor_stop.is_set():
            samples.append(gpu_used_mb())
            time.sleep(0.5)

    mon = threading.Thread(target=monitor, daemon=True)
    baseline = gpu_used_mb()
    print(f"GPU total: {gpu_total_mb()} MiB | baseline used: {baseline} MiB")
    print(f"Submitting {n} distinct calls at once...")

    call_ids = []

    def submit(path):
        with open(path, "rb") as f:
            r = s.post(f"{BASE}/api/calls",
                      files={"file": (os.path.basename(path) + f".{time.time_ns()}.mp3", f)})
        r.raise_for_status()
        call_ids.append(r.json()["call_id"])

    mon.start()
    threads = [threading.Thread(target=submit, args=(p,)) for p in picks]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"All {n} accepted in {time.time() - t0:.1f}s: {call_ids}")

    max_active_seen = 0
    while True:
        health = s.get(f"{BASE}/api/health").json()
        max_active_seen = max(max_active_seen, health["active_jobs"])
        statuses = [s.get(f"{BASE}/api/calls/{c}/status").json()["status"] for c in call_ids]
        done = [st in ("completed", "failed") for st in statuses]
        print(f"  t+{time.time() - t0:5.1f}s  active={health['active_jobs']} "
              f"queued={health['queue_depth']}  statuses={statuses}")
        if all(done):
            break
        time.sleep(2)

    monitor_stop.set()
    mon.join(timeout=2)

    elapsed = time.time() - t0
    peak = max(samples) if samples else gpu_used_mb()
    total = gpu_total_mb()
    headroom = total - peak

    failed = [c for c, st in zip(call_ids, statuses) if st == "failed"]
    if failed:
        print("\nfailed call detail (left in place for inspection):")
        for c in failed:
            print(f"  {c}: {s.get(f'{BASE}/api/calls/{c}').json().get('error')}")
    for c in call_ids:
        if c not in failed:
            s.delete(f"{BASE}/api/calls/{c}")

    print(f"\nmax concurrently active workers observed: {max_active_seen}")
    print(f"peak GPU memory during run: {peak} MiB / {total} MiB (headroom {headroom} MiB)")
    print(f"wall clock: {elapsed:.1f}s")
    print(f"failures: {len(failed)} {failed}")

    ok = not failed and headroom >= SAFETY_MARGIN_MB
    print("\nRESULT:", "SAFE" if ok else "UNSAFE — reduce WORKER_THREADS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
