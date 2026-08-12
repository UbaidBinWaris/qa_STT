#!/usr/bin/env python3
"""Measure recognition robustness without a labelled dataset.

Accuracy questions normally need ground truth, and there is none here. This
sidesteps that: take a call, transcribe it clean, and treat that as the
reference. Then damage the audio in a known way — noise, low level, clipping —
and measure how far the transcript moves. Finally repair the damaged audio with
each candidate filter chain and measure again.

The reference is the model's own output, so this cannot report absolute accuracy.
That is fine, because the question is comparative: *given audio that has got
worse, does this filter bring the transcript back?* A chain that recovers word
error rate is helping; one that does not is noise dressed as a feature.

    python tests/eval_robustness.py [calls...]      # defaults to test-audio/

Reports, per degradation:  WER damaged  ->  WER after each repair chain.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "server"))
os.environ.setdefault("HF_HOME", os.path.join(REPO, "server", "models_cache"))

# How the audio gets worse. These mirror real telephony complaints rather than
# laboratory noise: a caller on speakerphone, a quiet handset, a hot line.
DEGRADATIONS = {
    "noise-light": "anoisesrc=color=pink:amplitude=0.02[n];[0:a][n]amix=inputs=2:duration=first",
    "noise-heavy": "anoisesrc=color=pink:amplitude=0.06[n];[0:a][n]amix=inputs=2:duration=first",
    "quiet-12dB": "volume=-12dB",
    "quiet-20dB": "volume=-20dB",
    "clipped": "volume=6dB,alimiter=limit=0.35",
}

# Candidate repairs, including the ones rejected earlier on weaker evidence.
REPAIRS = {
    "none": None,
    "dynaudnorm": "dynaudnorm=f=200:g=5:p=0.9:m=10",
    "loudnorm": "loudnorm=I=-16:TP=-1.5:LRA=11",
    "speechnorm": "speechnorm=e=12.5:r=0.0001:l=1",
    "denoise": "highpass=f=80,afftdn=nf=-25,lowpass=f=3800",
    "denoise+dyn": "highpass=f=80,afftdn=nf=-25,lowpass=f=3800,"
                   "dynaudnorm=f=200:g=5:p=0.9:m=10",
}


def render(src: str, dst: str, *chains: str | None):
    """Apply filter chains in order, always ending at 16 kHz mono PCM."""
    active = [c for c in chains if c]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src]
    if active:
        joined = ",".join(active)
        # amix-style chains declare their own labels and need -filter_complex.
        flag = "-filter_complex" if "[" in joined else "-af"
        cmd += [flag, joined]
    cmd += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    subprocess.run(cmd, check=True, capture_output=True)


def wer(reference: list[str], hypothesis: list[str]) -> float:
    """Word error rate by Levenshtein distance over word sequences."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    prev = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        cur = [i]
        for j, h in enumerate(hypothesis, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(reference)


def normalise(text: str) -> list[str]:
    import re
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def main():
    from pipeline import asr

    sources = sys.argv[1:] or [
        os.path.join(REPO, "test-audio", f)
        for f in sorted(os.listdir(os.path.join(REPO, "test-audio")))
        if f.endswith((".mp3", ".wav"))
    ][:3]

    if not sources:
        print("No audio found. Pass files, or put some in test-audio/.")
        return 1

    asr.load()
    tmp = tempfile.mkdtemp(prefix="robustness_")
    totals: dict[tuple[str, str], list[float]] = {}

    for src in sources:
        name = os.path.basename(src)[:24]
        clean = os.path.join(tmp, "clean.wav")
        render(src, clean)
        ref_text, _ = asr.transcribe(clean)
        reference = normalise(ref_text)
        print(f"\n{name}  ({len(reference)} reference words)")
        print(f"  {'degradation':<14} " + "".join(f"{r:>13}" for r in REPAIRS))

        for dname, damage in DEGRADATIONS.items():
            row = []
            for rname, repair in REPAIRS.items():
                path = os.path.join(tmp, f"{dname}_{rname}.wav")
                render(src, path, damage, repair)
                hyp, _ = asr.transcribe(path)
                score = wer(reference, normalise(hyp))
                totals.setdefault((dname, rname), []).append(score)
                row.append(score)
                os.remove(path)
            best = min(row)
            cells = "".join(
                f"{v * 100:>12.1f}%" + ("*" if v == best else " ") for v in row
            )
            print(f"  {dname:<14} {cells}")
        os.remove(clean)

    print("\n" + "=" * 78)
    print("MEAN WER ACROSS ALL CALLS (lower is better, * = best repair)")
    print("=" * 78)
    print(f"  {'degradation':<14} " + "".join(f"{r:>13}" for r in REPAIRS))
    verdicts = []
    for dname in DEGRADATIONS:
        row = [sum(totals[(dname, r)]) / len(totals[(dname, r)]) for r in REPAIRS]
        best_idx = row.index(min(row))
        cells = "".join(
            f"{v * 100:>12.1f}%" + ("*" if i == best_idx else " ")
            for i, v in enumerate(row)
        )
        print(f"  {dname:<14} {cells}")
        verdicts.append((dname, list(REPAIRS)[best_idx], row[0], min(row)))

    print("\nVERDICT")
    for dname, best, baseline, improved in verdicts:
        if best == "none":
            print(f"  {dname:<14} no repair helps (best is leaving it alone)")
        else:
            gain = (baseline - improved) / max(baseline, 1e-9) * 100
            print(f"  {dname:<14} '{best}' recovers {gain:.0f}% of the error "
                  f"({baseline * 100:.1f}% -> {improved * 100:.1f}% WER)")
    os.rmdir(tmp) if not os.listdir(tmp) else None
    return 0


if __name__ == "__main__":
    sys.exit(main())
