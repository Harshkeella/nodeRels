"""Scoring. Five pure functions the pipeline calls in order, all weights from config.

The spec wanted five modules for these. They are read together, tuned together and tested
together, so they live together. Each returns a float in 0.0-1.0 and touches no I/O except
the two that take already-decoded pixels.
"""
import math
import re
from collections import Counter

from PIL import Image, ImageFilter, ImageStat

from . import config
from .schemas import ImageCandidate, VisualPlan

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


# ---------- 1. semantic relevance ----------

def semantic_score(cand: ImageCandidate, plan: VisualPlan) -> float:
    """How well the provider's own description of this image matches the visual brief.

    Scored against the whole brief (intent + concepts + style), never the bare query --
    that is the point of having a brief.

    ponytail: Phase 1 is weighted token overlap over provider metadata. It is real signal
    (Unsplash alt_description and Pixabay tags are human/curated) and costs nothing.
    Phase 2 replaces the body of this function with a CLIP/SigLIP cosine between
    plan.ranking_text() and the image itself; the signature and the 0-1 range do not move.
    """
    target = Counter(_tokens(plan.ranking_text()))
    if not target:
        return 0.5
    have = set(_tokens(cand.text()))
    concepts = set(_tokens(" ".join(plan.primary_concepts)))
    if not have or concepts and not have.intersection(concepts):
        return 0.0
    overlap = sum(w for t, w in target.items() if t in have) / sum(target.values())

    # Raw overlap saturates low (the brief is long, a caption is short), so spread it out.
    score = 1.0 - math.exp(-4.0 * overlap)

    if any(t in have for t in _tokens(" ".join(plan.avoid))):
        score *= 0.7

    # The provider's own ordering is a relevance ranking someone already paid for.
    score += 0.10 / (1.0 + cand.provider_rank / 5.0)
    return max(0.0, min(1.0, score))


# ---------- 2. aspect ratio / crop loss ----------

def aspect_score(ratio: float | None, target: float, gamma: float | None = None) -> float:
    """Fraction of the image that survives a centre crop to the slot, sharpened.

    A 16:10 photo in a 16:9 slot keeps 90% of its width -> ~0.86. A 4:3 photo keeps 75%
    -> ~0.65. Nothing is rejected for its shape; it is a penalty, not a filter.
    """
    if not ratio or ratio <= 0:
        return 0.5                       # unknown shape: neutral, do not reward or punish
    kept = min(ratio, target) / max(ratio, target)
    return kept ** (config.ASPECT_GAMMA if gamma is None else gamma)


# ---------- 3. quality ----------

def quality_score(img: Image.Image) -> dict[str, float]:
    """Technical quality from the decoded pixels. Returns the breakdown plus 'quality'.

    Signals are cheap and independent: resolution, edge energy (sharpness), exposure,
    contrast. 'aesthetic' is a declared slot at weight 0 -- an aesthetic model plugs in
    here without touching the caller.
    """
    w, h = img.size
    g = img.convert("L")
    if max(g.size) > 512:                # scoring a 6000px photo full-size is pure waste
        g = g.copy()
        g.thumbnail((512, 512))

    stat = ImageStat.Stat(g)
    mean, sd = stat.mean[0], stat.stddev[0]
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).stddev[0]

    parts = {
        "resolution": min(1.0, (w * h) / (1600 * 900)) ** 0.5,
        "sharpness": min(1.0, edges / 22.0),
        "exposure": 1.0 - abs(mean - 128.0) / 128.0,
        "colour": min(1.0, sd / 60.0),
        "aesthetic": 0.0,                # no model yet; weight 0 in config
    }
    total = sum(config.QUALITY_WEIGHTS.values()) or 1.0
    parts["quality"] = sum(v * config.QUALITY_WEIGHTS.get(k, 0.0)
                           for k, v in parts.items()) / total
    return parts


# ---------- 4. duplicates ----------

def dhash(img: Image.Image) -> int:
    """64-bit difference hash. Same picture at a different size/crop/quality -> same hash."""
    g = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] < px[row * 9 + col + 1])
    return bits


def is_duplicate(h: int, seen, threshold: int | None = None) -> bool:
    """True if h is within Hamming distance of any hash already used in this deck."""
    limit = config.DUP_HAMMING if threshold is None else threshold
    return any(bin(h ^ other).count("1") <= limit for other in seen)


# ---------- 5. diversity ----------

def diversity_score(cand: ImageCandidate, chosen: list[ImageCandidate]) -> float:
    """How different this candidate is from what is already picked for the deck.

    Runs before download, so it works on metadata: same query, same photographer and
    heavy caption overlap all mean "probably the same picture again".

    ponytail: metadata-level diversity. Perceptual dHash catches the identical-image case
    after download; embedding-space MMR is the Phase 2 upgrade for near-duplicates that
    share no words.
    """
    if not chosen:
        return 1.0
    mine = set(_tokens(cand.text()))
    worst = 1.0
    for other in chosen:
        penalty = 1.0
        if cand.query and cand.query == other.query:
            penalty *= 0.65
        if cand.photographer and cand.photographer == other.photographer:
            penalty *= 0.55
        theirs = set(_tokens(other.text()))
        if mine and theirs:
            jaccard = len(mine & theirs) / len(mine | theirs)
            penalty *= 1.0 - 0.8 * jaccard
        worst = min(worst, penalty)
    return max(0.0, worst)


# ---------- final ----------

def final_score(scores: dict[str, float]) -> float:
    """Weighted blend of whatever is known so far. Missing signals score 0.5, not 0 --
    an unmeasured signal must not look like a bad one."""
    w = config.FINAL_WEIGHTS
    total = sum(w.values()) or 1.0
    return sum(scores.get(k, 0.5) * v for k, v in w.items()) / total
