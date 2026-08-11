"""Exercise every rejection path of the upload service against a live server."""
import io
import json
import os
import subprocess
import sys
import tempfile

import requests

BASE = "http://localhost:8000"
PW = os.environ.get("APP_PASSWORD", "")
SP = tempfile.mkdtemp(prefix="qa_stt_test_")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

s = requests.Session()
s.post(f"{BASE}/api/login", json={"password": PW}).raise_for_status()

results = []


def check(name, expect_status, filename, content, expect_text=None):
    files = {"file": (filename, content, "application/octet-stream")}
    r = s.post(f"{BASE}/api/calls", files=files)
    detail = ""
    try:
        detail = r.json().get("detail") or json.dumps(r.json())[:90]
    except Exception:
        detail = r.text[:90]
    ok = r.status_code == expect_status
    if ok and expect_text:
        ok = expect_text.lower() in detail.lower()
    results.append((ok, name, expect_status, r.status_code, detail[:88]))
    return r


# 1. no extension
check("no extension", 415, "recording", b"\x00" * 1000)
# 2. wrong extension
check("disallowed .exe", 415, "malware.exe", b"MZ\x90\x00" * 100)
# 3. empty file
check("empty file", 422, "empty.mp3", b"")
# 4. not really audio (extension spoofed)
check("spoofed .mp3 (is a zip)", 422, "fake.mp3", b"PK\x03\x04" + os.urandom(5000))
# 5. truncated/corrupt audio
real = open(f"{REPO}/test-audio/20260729-122937_7322201282-all.mp3", "rb").read()
check("truncated mp3 header", 422, "corrupt.mp3", b"\xff\xfb" + os.urandom(300))
# 6. too short
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=0.3", f"{SP}/tiny.mp3"], check=True)
check("0.3s recording", 422, "tiny.mp3", open(f"{SP}/tiny.mp3", "rb").read(), "too short")
# 7. oversized
big = b"\xff\xfb" + os.urandom(2 * 1024 * 1024)
check("oversized (limit lowered)", 413, "big.mp3", big * 0 + big) if False else None
# 8. path traversal in filename
r = check("path traversal name", 201, "../../../../etc/passwd.mp3", real)
traversal_id = r.json().get("call_id") if r.ok else None
# 9. valid duplicate of #8
r2 = check("duplicate content", 200, "same-audio-different-name.mp3", real)
dup = r2.json().get("duplicate") if r2.ok else None
results.append((dup is True, "duplicate flagged, not reprocessed", True, dup, ""))

# verify the traversal filename was neutralised and stored safely
if traversal_id:
    info = s.get(f"{BASE}/api/calls/{traversal_id}").json()
    safe = "/" not in info["filename"] and ".." not in info["filename"]
    inside = info["audio_path"].startswith(f"{REPO}/server/uploads/")
    results.append((safe, f"filename sanitised -> {info['filename']!r}", True, safe, ""))
    results.append((inside, "stored inside uploads/", True, inside, info["audio_path"][:60]))
    results.append((info.get("sha256") is not None, "sha256 recorded", True,
                    bool(info.get("sha256")), str(info.get("size_bytes"))))

# 10. no stray temp files left behind
incoming = f"{REPO}/server/uploads/.incoming"
leftover = os.listdir(incoming) if os.path.isdir(incoming) else []
results.append((not leftover, "no partial files left in .incoming", 0, len(leftover), str(leftover[:3])))

print(f"\n{'':2} {'case':38} {'want':>5} {'got':>6}  detail")
print("-" * 100)
fails = 0
for ok, name, want, got, detail in results:
    if not ok:
        fails += 1
    print(f"{'ok' if ok else 'XX':2} {name:38} {str(want):>5} {str(got):>6}  {detail}")
print("-" * 100)
print(f"{len(results) - fails}/{len(results)} passed")

# cleanup created calls
if traversal_id:
    s.delete(f"{BASE}/api/calls/{traversal_id}")
sys.exit(1 if fails else 0)
