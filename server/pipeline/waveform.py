import json
import logging
import os
import numpy as np

logger = logging.getLogger("waveform")


def get_waveform(call_id: str, audio_path: str, num_bars: int = 140) -> dict:
    """
    Extract pitch & amplitude waveform envelope for a call recording.
    Returns a dict with duration, num_bars, heights (0.12..1.0), and normalized pitches.
    Caches result to disk at server/outputs/{call_id}_waveform.json.
    """
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_dir = os.path.join(server_dir, "outputs")
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"{call_id}_waveform.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except Exception:
            pass

    data = compute_pitch_waveform(audio_path, num_bars=num_bars)
    data["call_id"] = call_id

    try:
        with open(cache_file, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Failed to save waveform cache: {e}")

    return data


def compute_pitch_waveform(audio_path: str, num_bars: int = 140) -> dict:
    """
    Computes RMS amplitude envelope combined with fundamental pitch/frequency profile
    to produce dynamic height bars [0.12 .. 1.0] for the audio soundbar.
    """
    try:
        import soundfile as sf

        y, sr = sf.read(audio_path)
        if y.ndim > 1:
            y = np.mean(y, axis=1)
        duration = float(len(y) / sr)
    except Exception as e:
        logger.warning(f"soundfile failed for {audio_path}: {e}, falling back to librosa")
        try:
            import librosa

            y, sr = librosa.load(audio_path, sr=None, mono=True)
            duration = float(len(y) / sr)
        except Exception as ex:
            logger.error(f"Failed to load audio {audio_path}: {ex}")
            heights = [0.2] * num_bars
            return {"duration": 0.0, "num_bars": num_bars, "heights": heights, "pitches": [0.5] * num_bars}

    if len(y) == 0 or duration == 0:
        heights = [0.2] * num_bars
        return {"duration": 0.0, "num_bars": num_bars, "heights": heights, "pitches": [0.5] * num_bars}

    # Divide audio samples into num_bars chunks
    samples_per_bar = max(1, len(y) // num_bars)
    bar_amplitudes = []
    bar_pitches = []

    for i in range(num_bars):
        start = i * samples_per_bar
        end = (i + 1) * samples_per_bar if i < num_bars - 1 else len(y)
        chunk = y[start:end]

        if len(chunk) == 0:
            bar_amplitudes.append(0.0)
            bar_pitches.append(0.0)
            continue

        # RMS Amplitude
        rms = float(np.sqrt(np.mean(chunk**2)))
        bar_amplitudes.append(rms)

        # Zero crossings count
        zc = float(np.sum(np.diff(chunk > 0) != 0)) / len(chunk)

        # FFT Spectral Centroid / Pitch Energy Frequency balance
        fft_len = min(len(chunk), 2048)
        fft_data = np.abs(np.fft.rfft(chunk[:fft_len]))
        freqs = np.fft.rfftfreq(fft_len, 1.0 / sr)

        if np.sum(fft_data) > 0:
            centroid = float(np.sum(freqs * fft_data) / np.sum(fft_data))
        else:
            centroid = 0.0

        # Pitch score combining spectral centroid frequency & zero crossing rate
        pitch_score = (centroid / (sr / 2.0)) * 0.7 + (zc * 2.0) * 0.3
        bar_pitches.append(pitch_score)

    amp_arr = np.array(bar_amplitudes)
    pitch_arr = np.array(bar_pitches)

    max_amp = np.max(amp_arr) if np.max(amp_arr) > 0 else 1.0
    norm_amp = amp_arr / max_amp

    max_pitch = np.max(pitch_arr) if np.max(pitch_arr) > 0 else 1.0
    norm_pitch = pitch_arr / max_pitch if max_pitch > 0 else pitch_arr

    # Height depending on audio pitch & amplitude envelope
    raw_heights = 0.5 * norm_amp + 0.5 * (norm_amp * (0.3 + 0.7 * norm_pitch))

    if np.max(raw_heights) > 0:
        scaled_heights = 0.12 + 0.88 * (raw_heights / np.max(raw_heights))
    else:
        scaled_heights = np.full(num_bars, 0.2)

    heights = [round(float(h), 3) for h in scaled_heights]
    pitches = [round(float(p), 3) for p in norm_pitch]

    return {
        "duration": round(duration, 2),
        "num_bars": num_bars,
        "heights": heights,
        "pitches": pitches,
    }
