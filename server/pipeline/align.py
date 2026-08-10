import re

# A new segment starts when the speaker changes, or the same speaker resumes
# after a pause long enough to read as a separate turn.
SAME_SPEAKER_GAP = 1.5


def _overlap(a_start, a_end, b_start, b_end) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def assign_speakers(words: list[dict], turns: list[dict]) -> list[dict]:
    """Attach a speaker to every word by maximum time overlap with a diarized turn.

    Words that overlap no turn (diarizer missed the region) inherit the speaker of
    the nearest turn by midpoint distance, so no word is ever dropped.
    """
    if not turns:
        return [dict(w, speaker="speaker_0") for w in words]

    out = []
    for w in words:
        best, best_ov = None, 0.0
        for t in turns:
            if t["start"] >= w["end"]:
                break
            ov = _overlap(w["start"], w["end"], t["start"], t["end"])
            if ov > best_ov:
                best, best_ov = t, ov
        if best is None:
            mid = (w["start"] + w["end"]) / 2
            best = min(turns, key=lambda t: min(abs(mid - t["start"]), abs(mid - t["end"])))
        out.append(dict(w, speaker=best["speaker"]))
    return out


def group_segments(words: list[dict]) -> list[dict]:
    """Group consecutive same-speaker words into conversational turns."""
    segments = []
    for w in words:
        if (
            segments
            and segments[-1]["speaker"] == w["speaker"]
            and w["start"] - segments[-1]["end"] <= SAME_SPEAKER_GAP
        ):
            seg = segments[-1]
            seg["words"].append(w)
            seg["end"] = w["end"]
        else:
            segments.append(
                {"speaker": w["speaker"], "start": w["start"], "end": w["end"], "words": [w]}
            )
    for seg in segments:
        seg["text"] = " ".join(w["word"] for w in seg["words"]).strip()
    return [s for s in segments if s["text"]]


AGENT_PHRASES = [
    "am i speaking with", "is this ", "my name is", "this is ", "calling from",
    "calling you from", "thank you for calling", "how are you doing today",
    "i'm calling regarding", "i'm calling about", "do you have a minute",
    "recorded", "recording", "quality assurance", "may i ask",
]


def _score_agent(segments: list[dict], speaker: str, duration: float) -> float:
    own = [s for s in segments if s["speaker"] == speaker]
    if not own:
        return -1e9
    score = 0.0

    # The outbound agent typically drives the opening.
    opening = [s for s in segments if s["start"] < 45.0]
    if opening and opening[0]["speaker"] == speaker:
        score += 1.0
    if len(opening) > 1 and opening[1]["speaker"] == speaker:
        score += 0.5

    text = " ".join(s["text"] for s in own if s["start"] < 90.0).lower()
    score += 2.0 * sum(1 for p in AGENT_PHRASES if p in text)

    # Agents ask; customers answer.
    questions = sum(s["text"].count("?") for s in own)
    total_q = sum(s["text"].count("?") for s in segments) or 1
    score += 2.0 * (questions / total_q)

    # Agents usually hold the floor on a pitch call.
    talk = sum(s["end"] - s["start"] for s in own)
    total_talk = sum(s["end"] - s["start"] for s in segments) or 1
    score += 1.5 * (talk / total_talk)

    return score


def assign_roles(segments: list[dict], duration: float) -> list[dict]:
    """Map anonymous speaker_N labels to Agent / Customer, locked for the call."""
    speakers = sorted({s["speaker"] for s in segments})
    if not speakers:
        return segments

    scores = {sp: _score_agent(segments, sp, duration) for sp in speakers}
    agent = max(scores, key=scores.get)

    roles = {}
    others = [sp for sp in speakers if sp != agent]
    for i, sp in enumerate(others):
        roles[sp] = "Customer" if i == 0 else f"Speaker {i + 1}"
    roles[agent] = "Agent"

    for s in segments:
        s["role"] = roles[s["speaker"]]
    return segments


def apply_role_override(segments: list[dict], agent_speaker: str) -> list[dict]:
    """Re-label after the LLM confirms which raw speaker is the agent."""
    if not any(s["speaker"] == agent_speaker for s in segments):
        return segments
    others = sorted({s["speaker"] for s in segments if s["speaker"] != agent_speaker})
    roles = {agent_speaker: "Agent"}
    for i, sp in enumerate(others):
        roles[sp] = "Customer" if i == 0 else f"Speaker {i + 1}"
    for s in segments:
        s["role"] = roles[s["speaker"]]
    return segments


def build(words: list[dict], turns: list[dict], duration: float) -> list[dict]:
    return assign_roles(group_segments(assign_speakers(words, turns)), duration)
