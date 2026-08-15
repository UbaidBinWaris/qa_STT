import json
import re
import logging
import os
import threading

import requests

logger = logging.getLogger("pipeline.qa")

# With keep_alive=0, Ollama loads Qwen3 8B fresh for every call and drops it
# straight after. Two jobs calling concurrently would each trigger a load —
# roughly 7 GB apiece on top of whatever the speech models already hold, which
# measured out to a 12.6 GB peak for ONE job. A second concurrent load does not
# fit in 16 GB. This lock is what makes a multi-worker queue safe: only one job
# may have the LLM loaded at a time, no matter how many workers exist.
_llm_lock = threading.Lock()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
QA_MODEL = os.environ.get("QA_MODEL", "qwen3:8b")
TIMEOUT = 600

SCHEMA = {
    "type": "object",
    "required": ["agent_speaker", "score", "summary", "sentiment", "buying_intent",
                 "objections", "compliance", "action_items", "crm_notes",
                 "coaching_feedback", "followup_email"],
    "properties": {
        "agent_speaker": {"type": "string", "description": "which raw speaker label is the agent"},
        "score": {"type": "integer", "minimum": 0, "maximum": 100},
        "summary": {"type": "string"},
        "sentiment": {
            "type": "object",
            "required": ["customer", "agent", "trajectory"],
            "properties": {
                "customer": {"type": "string", "enum": ["positive", "neutral", "negative", "mixed"]},
                "agent": {"type": "string", "enum": ["positive", "neutral", "negative", "mixed"]},
                "trajectory": {"type": "string", "enum": ["improving", "stable", "declining"]},
            },
        },
        "buying_intent": {
            "type": "object",
            "required": ["level", "evidence"],
            "properties": {
                "level": {"type": "string", "enum": ["none", "low", "medium", "high"]},
                "evidence": {"type": "array", "items": {"type": "string"}},
            },
        },
        "objections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type", "quote", "timestamp", "handled"],
                "properties": {
                    "type": {"type": "string"},
                    "quote": {"type": "string"},
                    "timestamp": {"type": "number"},
                    "handled": {"type": "boolean"},
                },
            },
        },
        "compliance": {
            "type": "object",
            "required": ["identity_verified", "recording_disclosed", "issues"],
            "properties": {
                "identity_verified": {"type": "boolean"},
                "recording_disclosed": {"type": "boolean"},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["rule", "severity", "quote", "timestamp"],
                        "properties": {
                            "rule": {"type": "string"},
                            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                            "quote": {"type": "string"},
                            "timestamp": {"type": "number"},
                        },
                    },
                },
            },
        },
        "action_items": {"type": "array", "items": {"type": "string"}},
        "crm_notes": {"type": "string"},
        "coaching_feedback": {"type": "array", "items": {"type": "string"}},
        "followup_email": {"type": "string"},
    },
}

PROMPT = """You are a QA analyst for a sales call center. Analyze the transcript below.

Every speaker turn is prefixed with its raw diarization label and start time in seconds.

TRANSCRIPT:
{transcript}

CALL METRICS:
{metrics}

Produce a QA assessment. Rules you must follow:

- `agent_speaker`: the raw label (e.g. "speaker_0") belonging to the sales agent, not the customer. Decide from behaviour: the agent initiates, identifies themselves, asks qualifying questions, pitches.
- Every `quote` MUST be text copied verbatim from the transcript. Never invent a quote. If you cannot find real supporting text, leave the array empty.
- Every `timestamp` must be the start time of the turn the quote came from.
- `compliance.issues` covers TCPA and consent problems: calling without evident consent, failure to identify the company, ignoring a do-not-call or stop-calling request, failure to disclose recording, misrepresentation, pressure tactics. Only report an issue you can quote.
- `identity_verified` is true only if the agent confirmed they reached the intended person.
- `recording_disclosed` is true only if a recording/monitoring notice was actually spoken.
- `score` (0-100) weighs script adherence, professionalism, discovery quality, objection handling, and compliance. A high-severity compliance issue caps the score at 50.
- `summary` must agree with your own findings. Do not call a call compliant in the summary if you set `recording_disclosed` to false or reported any compliance issue.
- `crm_notes` is 2-4 sentences a rep would paste into a CRM.
- `followup_email` is a short ready-to-send email to the customer.

Respond with JSON only."""


def format_transcript(segments: list[dict]) -> str:
    return "\n".join(
        f'[{s["start"]:.1f}s] {s["role"]} ({s["speaker"]}): {s["text"]}' for s in segments
    )


def _call_ollama(prompt: str) -> dict:
    with _llm_lock:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": QA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": SCHEMA,
                "think": False,
                # Release the LLM's VRAM as soon as QA finishes. The speech models
                # stay resident, and on a 16 GB card an idle 7 GB LLM starves ASR
                # of the activation memory a long call needs.
                "keep_alive": 0,
                "options": {"temperature": 0.2, "num_ctx": 16384},
            },
            timeout=TIMEOUT,
        )
    resp.raise_for_status()
    return json.loads(resp.json()["response"])


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())


def _locate(quote: str, segments: list[dict]) -> dict | None:
    """Find the segment a quote actually came from.

    Matching is done on normalised text so punctuation differences do not
    invalidate a genuine quote, but the words themselves must be present — a
    paraphrase is not evidence.
    """
    needle = " ".join(_normalise(quote).split())
    if len(needle) < 5:
        return None
    for seg in segments:
        if needle in " ".join(_normalise(seg["text"]).split()):
            return seg
    return None


def _check(finding: dict, segments: list[dict], kind: str) -> tuple[bool, dict]:
    """Verify one finding against the transcript.

    A finding survives only if its quote exists, its timestamp points at the turn
    the quote came from, and the speaker it names is the one who said it. Findings
    resting on words the ASR itself doubted are kept but marked for review rather
    than silently trusted.
    """
    seg = _locate(finding.get("quote", ""), segments)
    if seg is None:
        return False, {"reason": "quote not found in transcript"}

    verified = {"segment_start": round(seg["start"], 2), "speaker": seg["role"]}

    # Repair rather than reject: the model routinely quotes correctly but guesses
    # the time, and a correct finding should not be lost over a wrong number.
    stated = finding.get("timestamp")
    if not isinstance(stated, (int, float)) or abs(float(stated) - seg["start"]) > 5.0:
        verified["timestamp_corrected_from"] = stated
        finding["timestamp"] = round(seg["start"], 2)

    claimed_speaker = finding.get("speaker")
    if claimed_speaker and claimed_speaker.lower() not in seg["role"].lower():
        verified["speaker_mismatch"] = claimed_speaker
        finding["speaker"] = seg["role"]

    if seg.get("uncertain"):
        verified["transcript_uncertain"] = True
        verified["segment_confidence"] = seg.get("confidence")
        logger.info(f"{kind} finding rests on uncertain transcript at {seg['start']:.1f}s")

    finding["verified"] = verified
    return True, verified


def _verify_evidence(result: dict, segments: list[dict]) -> dict:
    """Make every finding prove itself against timestamped transcript evidence."""
    dropped = 0
    needs_review = 0

    for kind in ("objections",):
        kept = []
        for item in result.get(kind, []):
            ok, info = _check(item, segments, kind)
            if ok:
                kept.append(item)
                needs_review += bool(info.get("transcript_uncertain"))
            else:
                dropped += 1
        result[kind] = kept

    comp = result.setdefault("compliance", {})
    kept_issues = []
    for issue in comp.get("issues", []):
        ok, info = _check(issue, segments, "compliance")
        if ok:
            kept_issues.append(issue)
            needs_review += bool(info.get("transcript_uncertain"))
        else:
            dropped += 1
    comp["issues"] = kept_issues

    bi = result.get("buying_intent", {})
    bi["evidence"] = [e for e in bi.get("evidence", []) if _locate(e, segments)]

    if dropped:
        logger.warning(f"Dropped {dropped} finding(s) that could not be evidenced.")
    result["evidence_review_required"] = needs_review
    return result


def analyze(segments: list[dict], metrics: dict) -> dict:
    prompt = PROMPT.format(
        transcript=format_transcript(segments), metrics=json.dumps(metrics, indent=2)
    )
    try:
        result = _call_ollama(prompt)
    except (json.JSONDecodeError, requests.HTTPError) as e:
        logger.warning(f"QA call failed ({e}); retrying once.")
        result = _call_ollama(prompt + "\n\nYour previous reply was invalid. Return valid JSON only.")

    if any(i.get("severity") == "high" for i in result.get("compliance", {}).get("issues", [])):
        result["score"] = min(result.get("score", 0), 50)

    return _verify_evidence(result, segments)
