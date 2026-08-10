INTERRUPTION_OVERLAP = 0.3
DEAD_AIR = 3.0


def compute(segments: list[dict], duration: float) -> dict:
    by_role: dict[str, float] = {}
    words_by_role: dict[str, int] = {}
    for s in segments:
        by_role[s["role"]] = by_role.get(s["role"], 0.0) + (s["end"] - s["start"])
        words_by_role[s["role"]] = words_by_role.get(s["role"], 0) + len(s["words"])

    talk_total = sum(by_role.values()) or 1.0
    agent_talk = by_role.get("Agent", 0.0)
    customer_talk = by_role.get("Customer", 0.0)

    interruptions: dict[str, int] = {}
    for prev, cur in zip(segments, segments[1:]):
        if cur["speaker"] != prev["speaker"] and prev["end"] - cur["start"] > INTERRUPTION_OVERLAP:
            interruptions[cur["role"]] = interruptions.get(cur["role"], 0) + 1

    silences = []
    for prev, cur in zip(segments, segments[1:]):
        gap = cur["start"] - prev["end"]
        if gap >= DEAD_AIR:
            silences.append({"start": round(prev["end"], 2), "duration": round(gap, 2)})

    def wpm(role):
        t = by_role.get(role, 0.0)
        return round(words_by_role.get(role, 0) / t * 60, 1) if t > 0 else 0.0

    return {
        "duration": round(duration, 2),
        "talk_time": {k: round(v, 2) for k, v in by_role.items()},
        "talk_ratio": {k: round(v / talk_total * 100, 1) for k, v in by_role.items()},
        "agent_dominance": round(agent_talk / talk_total * 100, 1) if talk_total else 0.0,
        "customer_engagement": round(customer_talk / talk_total * 100, 1) if talk_total else 0.0,
        "silence_total": round(max(0.0, duration - talk_total), 2),
        "dead_air_events": silences,
        "longest_silence": round(max((s["duration"] for s in silences), default=0.0), 2),
        "interruptions": interruptions,
        "wpm": {"Agent": wpm("Agent"), "Customer": wpm("Customer")},
        "turns": len(segments),
    }
