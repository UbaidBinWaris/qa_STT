"""Transcript reliability: decide which words a reviewer should not take on trust.

A QA verdict is only as good as the transcript under it. "I don't want the plan"
and "I do want the plan" differ by one short word, and a mis-recognised name or
figure quietly corrupts a compliance finding. This module marks the parts of a
transcript that deserve a second look, so the product can show uncertainty
instead of pretending to certainty.

Two independent signals are combined:

  acoustic  - the model's own per-word confidence
  semantic  - whether the word is one whose corruption would change a QA outcome

A high-risk word does not need low confidence to be worth flagging: negations and
figures are exactly where a confident mistake does the most damage.
"""
import logging
import re
import statistics

logger = logging.getLogger("pipeline.reliability")

# Entropy-derived TDT confidence sits in a narrow band near 1.0 — measured on real
# calls: median 0.990, p5 0.965, floor 0.934. An absolute cutoff like 0.5 would
# never fire, so the threshold is relative to the call, with an absolute ceiling
# so a uniformly clean recording is not forced to yield 10% of its words.
LOW_PERCENTILE = 10
ABS_CEILING = 0.97
# Below this, a word is doubtful regardless of how the rest of the call scored.
ABS_FLOOR = 0.95

# Reversing or dropping one of these inverts the meaning of a turn, which is the
# single most damaging ASR error for QA.
NEGATIONS = {
    "no", "not", "never", "none", "nothing", "nobody", "nowhere", "neither", "nor",
    "dont", "doesnt", "didnt", "cant", "cannot", "wont", "wouldnt", "shouldnt",
    "couldnt", "isnt", "arent", "wasnt", "werent", "havent", "hasnt", "hadnt",
    "aint", "without", "decline", "declined", "refuse", "refused", "stop",
    "unsubscribe", "remove",
}

# Phrases that decide a compliance outcome. If one of these is misheard the call
# can be scored as compliant when it was not.
COMPLIANCE_TERMS = {
    "record", "recorded", "recording", "consent", "consented", "permission",
    "authorize", "authorized", "authorization", "tcpa", "dnc", "verify",
    "verification", "confirm", "confirmed", "disclose", "disclosure", "agree",
    "agreed", "terms", "conditions", "cancel", "cancellation", "refund",
    "guarantee", "warranty", "contract", "obligation", "legal", "attorney",
    "complaint", "supervisor", "manager",
}

MONEY_RE = re.compile(r"[$£€]|\bdollars?\b|\bcents?\b", re.I)
NUMBER_RE = re.compile(r"\d")
NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "million", "percent",
}
EMAIL_RE = re.compile(r"@|\bgmail\b|\byahoo\b|\bhotmail\b|\boutlook\b|dot com", re.I)
DATE_WORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "today", "tomorrow",
    "yesterday", "week", "weeks", "month", "months", "year", "years", "day", "days",
}


def _bare(word: str) -> str:
    return re.sub(r"[^a-z0-9@]", "", word.lower())


SENTENCE_END = (".", "?", "!")


def risk_tags(word: str, prev: str | None = None) -> list[str]:
    """Categories that make this word consequential if transcribed wrongly.

    `prev` is the preceding word, needed only to tell a genuine proper noun from
    an ordinary word that happens to start a sentence.
    """
    bare = _bare(word)
    tags = []
    if bare in NEGATIONS:
        tags.append("negation")
    if NUMBER_RE.search(word) or bare in NUMBER_WORDS:
        tags.append("number")
    if MONEY_RE.search(word):
        tags.append("money")
    if EMAIL_RE.search(word):
        tags.append("contact")
    if bare in DATE_WORDS:
        tags.append("date")
    if bare in COMPLIANCE_TERMS:
        tags.append("compliance")
    # A capitalised word *mid-sentence* is usually a name or product: the class
    # of word Parakeet is least sure about and a reviewer most needs to check.
    # Sentence-initial capitals carry no such signal, and counting them tagged a
    # fifth of every call — enough noise to make the marks worth ignoring.
    sentence_start = prev is None or prev.endswith(SENTENCE_END)
    if (word[:1].isupper() and not sentence_start
            and bare and bare not in NUMBER_WORDS and len(bare) > 2):
        tags.append("proper-noun")
    return tags


def _threshold(scores: list[float]) -> float:
    if not scores:
        return ABS_FLOOR
    ordered = sorted(scores)
    idx = max(0, int(len(ordered) * LOW_PERCENTILE / 100) - 1)
    relative = ordered[idx]
    return min(relative, ABS_CEILING)


def analyse(words: list[dict]) -> dict:
    """Annotate words in place with `uncertain` and `risk`, and summarise the call.

    Returns a summary carrying the confidence distribution, the flagged spans and
    a 0-100 reliability score suitable for display.
    """
    scored = [w["confidence"] for w in words if w.get("confidence") is not None]

    if not scored:
        # Confidence unavailable (an older call, or a model that does not emit it).
        for i, w in enumerate(words):
            w["uncertain"] = False
            w["risk"] = risk_tags(w["word"], words[i - 1]["word"] if i else None)
        return {
            "available": False,
            "score": None,
            "flagged": 0,
            "total": len(words),
            "spans": [],
        }

    cutoff = _threshold(scored)

    median = statistics.median(scored)
    for i, w in enumerate(words):
        conf = w.get("confidence")
        tags = risk_tags(w["word"], words[i - 1]["word"] if i else None)
        low = conf is not None and (conf <= cutoff or conf < ABS_FLOOR)
        # A risky word is held to a stricter standard: it only has to be below the
        # median to warrant a check, because the cost of being wrong is higher.
        risky_and_soft = bool(tags) and conf is not None and conf < median
        w["risk"] = tags
        # A recovered word came from audio the main pass produced nothing for, so
        # it is always worth a listen even though it carries no confidence score.
        w["uncertain"] = bool(
            low or (risky_and_soft and conf <= ABS_CEILING) or w.get("recovered")
        )

    flagged = [w for w in words if w["uncertain"]]
    spans = _spans(words)

    mean_conf = statistics.mean(scored)
    # Reliability is the mean confidence penalised by how much of the call was
    # flagged, so a call with a few bad patches does not look pristine.
    ratio = len(flagged) / len(words) if words else 0
    score = max(0.0, min(100.0, (mean_conf * 100) - (ratio * 100 * 0.5)))

    summary = {
        "available": True,
        "score": round(score, 1),
        "mean_confidence": round(mean_conf, 4),
        "min_confidence": round(min(scored), 4),
        "threshold": round(cutoff, 4),
        "flagged": len(flagged),
        "total": len(words),
        "spans": spans[:50],
    }
    logger.info(
        f"Reliability {summary['score']}/100 — {len(flagged)}/{len(words)} words flagged, "
        f"{len(spans)} span(s)"
    )
    return summary


def _spans(words: list[dict], gap: float = 1.5) -> list[dict]:
    """Group adjacent uncertain words into reviewable spans."""
    spans: list[dict] = []
    current: list[dict] = []

    def flush():
        if not current:
            return
        confs = [w["confidence"] for w in current if w.get("confidence") is not None]
        tags = sorted({t for w in current for t in w.get("risk", [])})
        spans.append({
            "start": current[0]["start"],
            "end": current[-1]["end"],
            "text": " ".join(w["word"] for w in current),
            "min_confidence": round(min(confs), 4) if confs else None,
            "risk": tags,
        })

    for w in words:
        if w.get("uncertain"):
            if current and w["start"] - current[-1]["end"] > gap:
                flush()
                current = []
            current.append(w)
        elif current:
            flush()
            current = []
    flush()
    return spans


def segment_confidence(segment_words: list[dict]) -> tuple[float | None, bool]:
    """Confidence for one speaker turn, and whether it warrants a listen.

    Flagging a turn because it contains any single borderline word marked half
    the transcript, which trains reviewers to ignore the marking. A turn is only
    called out when the doubt is substantial: a word below the hard floor, a
    doubtful word that also carries risk, or several doubtful words together.
    """
    confs = [w["confidence"] for w in segment_words if w.get("confidence") is not None]
    doubtful = [w for w in segment_words if w.get("uncertain")]
    # A conflict between the two decoders always surfaces: it is the one case
    # where we know the transcript may be wrong rather than merely unsure.
    flagged = bool(
        any(w.get("conflict") for w in segment_words)
        or any(c < ABS_FLOOR for c in confs)
        or any(w.get("risk") for w in doubtful)
        or len(doubtful) >= 3
    )
    return (round(min(confs), 4) if confs else None), flagged
