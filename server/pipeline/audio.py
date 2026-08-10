import json
import os
import subprocess

TARGET_SR = 16000


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def to_wav(src: str, dst: str) -> float:
    """Decode any input to 16 kHz mono PCM wav. Returns duration in seconds."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src,
         "-ac", "1", "-ar", str(TARGET_SR), "-c:a", "pcm_s16le", dst],
        check=True, capture_output=True,
    )
    return probe_duration(dst)
