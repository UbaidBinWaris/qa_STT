"""Direct tests for the limit paths that are impractical to drive over HTTP."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import uploads

SP = tempfile.mkdtemp(prefix="qa_stt_test_")
results = []


def record(ok, name, detail=""):
    results.append((ok, name, detail))


class FakeUpload:
    """Mimics starlette's UploadFile.read(n) streaming interface."""

    def __init__(self, data, filename="x.mp3"):
        self.filename = filename
        self._buf = memoryview(data)
        self._pos = 0

    async def read(self, n):
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return bytes(chunk)


async def main():
    # --- size cap enforced mid-stream, not after buffering ---
    uploads.MAX_BYTES = 1 * 1024 * 1024
    dest = f"{SP}/cap.bin"
    try:
        await uploads.stream_to_disk(FakeUpload(os.urandom(3 * 1024 * 1024)), dest)
        record(False, "size cap rejects oversized", "no error raised")
    except uploads.UploadError as e:
        record(e.status == 413, f"size cap rejects oversized -> {e.status}", e.message)
    finally:
        written = os.path.getsize(dest) if os.path.exists(dest) else 0
        # It must stop early rather than writing the whole body to disk.
        record(written <= uploads.MAX_BYTES + uploads.CHUNK,
               "aborts write before exceeding cap",
               f"{written/1048576:.1f} MB written of 3 MB sent")
        os.path.exists(dest) and os.remove(dest)

    # --- a file exactly at the cap is accepted ---
    uploads.MAX_BYTES = 1 * 1024 * 1024
    try:
        size, digest = await uploads.stream_to_disk(
            FakeUpload(b"a" * uploads.MAX_BYTES), f"{SP}/exact.bin")
        record(size == uploads.MAX_BYTES and len(digest) == 64,
               "file exactly at cap accepted", f"{size} bytes, sha256 len {len(digest)}")
    except uploads.UploadError as e:
        record(False, "file exactly at cap accepted", e.message)
    finally:
        os.path.exists(f"{SP}/exact.bin") and os.remove(f"{SP}/exact.bin")
    uploads.MAX_BYTES = 500 * 1024 * 1024

    # --- hash is stable and content-addressed ---
    a = (await uploads.stream_to_disk(FakeUpload(b"identical"), f"{SP}/h1.bin"))[1]
    b = (await uploads.stream_to_disk(FakeUpload(b"identical"), f"{SP}/h2.bin"))[1]
    c = (await uploads.stream_to_disk(FakeUpload(b"different"), f"{SP}/h3.bin"))[1]
    record(a == b and a != c, "sha256 matches on identical content", a[:16])
    for f in ("h1", "h2", "h3"):
        os.remove(f"{SP}/{f}.bin")

    # --- queue full ---
    try:
        uploads.check_capacity(uploads.MAX_QUEUE)
        record(False, "rejects when queue is full", "no error")
    except uploads.UploadError as e:
        record(e.status == 503, f"rejects when queue is full -> {e.status}", e.message)

    # --- queue has room ---
    try:
        uploads.check_capacity(0)
        record(True, "accepts when queue has room")
    except uploads.UploadError as e:
        record(False, "accepts when queue has room", e.message)

    # --- low disk space ---
    original = uploads.MIN_FREE_BYTES
    uploads.MIN_FREE_BYTES = 10 ** 18  # more than any real disk
    try:
        uploads.check_capacity(0)
        record(False, "rejects when disk is low", "no error")
    except uploads.UploadError as e:
        record(e.status == 507, f"rejects when disk is low -> {e.status}", e.message)
    finally:
        uploads.MIN_FREE_BYTES = original

    # --- filename sanitisation ---
    cases = {
        "../../etc/passwd.mp3": "passwd.mp3",
        "/absolute/path.wav": "path.wav",
        "..\\..\\windows\\evil.mp3": "evil.mp3",
        "": "recording",
        "....mp3": "mp3",
        "a" * 400 + ".mp3": None,  # just needs truncating
    }
    for raw, expected in cases.items():
        got = uploads.safe_filename(raw)
        ok = (len(got) <= 200 and "/" not in got and "\\" not in got
              and (expected is None or got == expected))
        record(ok, f"sanitise {raw[:28]!r}", f"-> {got[:40]!r}")

    # a null byte must never survive into a stored name
    record("\x00" not in uploads.safe_filename("bad\x00name.mp3"),
           "strips null bytes", repr(uploads.safe_filename("bad\x00name.mp3")))

    print(f"\n{'':2} {'case':46} detail")
    print("-" * 96)
    fails = 0
    for ok, name, detail in results:
        fails += 0 if ok else 1
        print(f"{'ok' if ok else 'XX':2} {name:46} {detail[:44]}")
    print("-" * 96)
    print(f"{len(results)-fails}/{len(results)} passed")
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
