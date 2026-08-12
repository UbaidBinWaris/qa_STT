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


# Speech-level normalisation, measured rather than assumed. Damaging known-good
# audio and repairing it (tests/eval_robustness.py) put word error rates at:
#
#     degradation     none    speechnorm    denoise
#     quiet -20 dB    4.4%      1.8%          8.3%
#     quiet -12 dB    3.3%      2.2%          6.8%
#     noise light     8.8%      6.6%          6.9%
#     clipped         5.2%      4.5%          6.3%
#
# speechnorm recovers 26-58% of the error on quiet or noisy input and shifts an
# already-clean transcript by about 2%. Real recordings here measure -22 to
# -26 dB mean volume, so the quiet cases are the normal ones, not the edge.
#
# Denoising is deliberately absent: it made quiet audio substantially worse
# (8.3% against 4.4%), which is the opposite of what it promises.
NORMALISE = os.environ.get("AUDIO_NORMALISE", "1") != "0"
SPEECHNORM = "speechnorm=e=12.5:r=0.0001:l=1"


def to_wav(src: str, dst: str) -> float:
    """Decode any input to 16 kHz mono PCM wav. Returns duration in seconds."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if NORMALISE:
        cmd += ["-af", SPEECHNORM]
    cmd += ["-ac", "1", "-ar", str(TARGET_SR), "-c:a", "pcm_s16le", dst]
    subprocess.run(cmd, check=True, capture_output=True)
    return probe_duration(dst)
