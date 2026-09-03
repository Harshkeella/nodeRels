"""The pipeline. Plan -> search -> filter -> rank -> fetch -> validate -> dedupe -> select.

One public coroutine, select_for_slide(). Everything else here is cache and download
plumbing, kept in this file because "check the cache" and "download it" are one operation
-- splitting them into two modules is how you get a race between the check and the write.
"""
import asyncio
import hashlib
import io
import json
import os
import re
import time
import urllib.request

from PIL import Image

from . import config, plan as planner, providers, rank
from .schemas import ImageCandidate, SelectedImage, SlideImages, SlideRequest

IMAGES = os.path.join(config.CACHE, "images")
DECKS = os.path.join(config.CACHE, "presentations")


# ---------- cache ----------

def _paths(cand: ImageCandidate):
    """Where this image lives on disk. Keyed by provider+id, not by URL: the same photo
    served at a different size is the same photo and must not download twice."""
    key = hashlib.sha1(("%s:%s" % (cand.provider, cand.id)).encode()).hexdigest()[:16]
    base = os.path.join(IMAGES, cand.provider, key)
    return base + ".img", base + ".json"


def _deck_path(presentation_id: str) -> str:
    safe = hashlib.sha1(presentation_id.encode()).hexdigest()[:16]
    return os.path.join(DECKS, safe + ".json")


def deck_state(presentation_id: str) -> dict:
    """Perceptual hashes already used in this deck, so slide 4 cannot reuse slide 2's photo.

    ponytail: a JSON file per presentation, read-modify-write. Single-writer assumption --
    one deck is built by one process. Move to SQLite the day slides are farmed out in
    parallel across workers.
    """
    try:
        with open(_deck_path(presentation_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"hashes": [], "ids": []}


def _save_deck_state(presentation_id: str, state: dict) -> None:
    os.makedirs(DECKS, exist_ok=True)
    with open(_deck_path(presentation_id), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def _trigger(cand: ImageCandidate) -> None:
    """Unsplash requires a download ping on real use. Best effort -- never blocks a deck."""
    if not cand.trigger_url:
        return
    key = os.getenv(config.KEYS.get(cand.provider, ""), "")
    try:
        urllib.request.urlopen(urllib.request.Request(
            cand.trigger_url, headers={"Authorization": "Client-ID " + key,
                                       "User-Agent": "image_engine"}), timeout=5).close()
    except Exception:
        pass


def fetch(cand: ImageCandidate) -> tuple[str, bytes]:
    """Local path + bytes for a candidate. Cache hit skips the network entirely."""
    path, meta = _paths(cand)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            return path, fh.read()

    req = urllib.request.Request(cand.download_url, headers={"User-Agent": "image_engine"})
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
        blob = r.read(config.MAX_BYTES + 1)
    if len(blob) > config.MAX_BYTES:
        raise ValueError("larger than IMG_MAX_BYTES")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"                      # never leave a half file where a hit looks
    with open(tmp, "wb") as fh:
        fh.write(blob)
    os.replace(tmp, path)
    with open(meta, "w", encoding="utf-8") as fh:
        json.dump({**cand.model_dump(exclude={"scores", "rejected"}),
                   "local_path": path, "bytes": len(blob),
                   "downloaded_at": time.time()}, fh, indent=1)
    _trigger(cand)
    return path, blob


def validate(blob: bytes, role: str) -> tuple[Image.Image | None, str | None]:
    """Decode and check. Returns (image, None) or (None, reason)."""
    if len(blob) < config.MIN_BYTES:
        return None, "too small (%d bytes) -- placeholder or error page" % len(blob)
    try:
        img = Image.open(io.BytesIO(blob))
        img.load()                            # forces a real decode: catches truncation
    except Exception as e:
        return None, "undecodable: %s" % type(e).__name__
    if img.format not in config.FORMATS:
        return None, "format %s not usable in a deck" % img.format
    min_w, min_h = config.MIN_PX.get(role, config.DEFAULT_MIN_PX)
    if img.width < min_w or img.height < min_h:
        return None, "%dx%d below %dx%d for a %s slot" % (img.width, img.height,
                                                          min_w, min_h, role)
    return img, None


# ---------- candidate assembly ----------

def _dedupe(cands: list[ImageCandidate]) -> list[ImageCandidate]:
    """Same photo found by three different queries is one candidate, not three."""
    out, seen = [], set()
    for c in cands:
        key = (c.provider, c.id)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _licensed(cands, log) -> list[ImageCandidate]:
    ok = [c for c in cands if c.license in config.ALLOWED_LICENSES]
    if len(ok) != len(cands):
        log.append("[Licence] dropped %d candidate(s) outside %s"
                   % (len(cands) - len(ok), sorted(config.ALLOWED_LICENSES)))
    return ok


# ---------- the pipeline ----------

async def collect(slide: SlideRequest, log: list):
    """Plan, search and rank -- everything before a byte is downloaded.

    Shared by /images/search (which stops here) and /images/select (which carries on).
    """
    brief = await asyncio.to_thread(planner.make_plan, slide)
    log.append("[Visual Planner] %s | %s | concepts: %s"
               % (brief.source, brief.image_type, ", ".join(brief.primary_concepts)))
    log.append("[Query Generator] %d queries: %s"
               % (len(brief.queries), " | ".join(q.query for q in brief.queries)))

    live = providers.available()
    if not live:
        log.append("[Providers] no API keys set -- see .env.example")
        return brief, [], [], [], 0
    order = planner.route(brief, live)
    log.append("[Router] %s -> %s" % (brief.image_type, ", ".join(order)))

    slots = slide.image_slots
    queries = [q.query for q in sorted(brief.queries, key=lambda q: q.priority)]
    cands = await providers.search_all(
        queries, order, orientation=slots[0].orientation if slots else None, log=log)
    found = len(cands)
    cands = _licensed(_dedupe(cands), log)
    log.append("[Collector] %d hits -> %d unique candidates" % (found, len(cands)))

    for c in cands:
        c.scores["semantic"] = rank.semantic_score(c, brief)
        c.scores["aspect"] = rank.aspect_score(c.ratio, slots[0].target_ratio) if slots else 0.5
        c.scores["quality"] = (min(1.0, (c.width * c.height) / (1600 * 900)) ** 0.5
                               if c.width and c.height else 0.5)
        c.scores["final"] = rank.final_score(c.scores)
    # Search terms are not proof of relevance, and generated stock art is not a photo.
    cands = [c for c in cands if c.scores["semantic"] >= .25 and not re.search(
        r"\b(?:dall[ -]?e|midjourney|stable diffusion|bing image creator|ai[- ]generated)\b",
        c.text() + " " + (c.photographer or ""), re.I)]
    # Cap the pool by score, not arrival order, so the cut never drops the best.
    cands.sort(key=lambda c: c.scores["final"], reverse=True)
    cands = cands[:config.MAX_CANDIDATES]
    return brief, order, queries, cands, found


async def select_for_slide(slide: SlideRequest) -> SlideImages:
    """Find, rank and download one image per slot on this slide.

    Never raises for an empty result: a slide with no usable image returns zero selections
    and the reason, and the deck still renders.
    """
    deadline = time.monotonic() + config.SLIDE_BUDGET
    log: list[str] = []
    slots = slide.image_slots or []
    if not slots:
        return SlideImages(slide_number=slide.slide_number,
                           metadata={"log": ["[Engine] slide has no image slots"]})

    brief, order, queries, cands, found = await collect(slide, log)

    state = deck_state(slide.presentation_id)
    used_hashes = list(state["hashes"])
    chosen: list[ImageCandidate] = []
    selected: list[SelectedImage] = []
    spent: set[tuple[str, str]] = set()

    for slot in slots:
        target = slot.target_ratio
        for c in cands:
            c.scores["aspect"] = rank.aspect_score(c.ratio, target)
            c.scores["diversity"] = rank.diversity_score(c, chosen)
            # Pre-download quality is the declared resolution only; the real score comes
            # from the pixels below and overwrites this.
            c.scores["quality"] = (
                min(1.0, (c.width * c.height) / (1600 * 900)) ** 0.5
                if c.width and c.height else 0.5)
            c.scores["final"] = rank.final_score(c.scores)

        picked = None
        for c in sorted(cands, key=lambda c: c.scores["final"], reverse=True):
            if (c.provider, c.id) in spent:
                continue
            if time.monotonic() > deadline:
                log.append("[Engine] slide budget spent, stopping at slot %s" % slot.slot_id)
                break
            spent.add((c.provider, c.id))
            if c.id in state["ids"]:
                c.rejected = "already used in this deck"
                continue
            try:
                path, blob = await asyncio.to_thread(fetch, c)
            except Exception as e:
                c.rejected = "download failed: %s" % e
                log.append("[Download] %s/%s failed: %s" % (c.provider, c.id, e))
                continue
            img, why = validate(blob, slot.role)
            if why:
                c.rejected = why
                continue

            h = rank.dhash(img)
            if rank.is_duplicate(h, used_hashes):
                c.rejected = "perceptual duplicate of an image already in the deck"
                continue

            q = rank.quality_score(img)
            c.scores.update({k: round(v, 4) for k, v in q.items()})
            c.width, c.height = img.size
            c.scores["aspect"] = rank.aspect_score(c.ratio, target)
            c.scores["final"] = rank.final_score(c.scores)

            picked, used_hashes = c, used_hashes + [h]
            state["ids"].append(c.id)
            chosen.append(c)
            selected.append(SelectedImage(
                slot_id=slot.slot_id, local_path=path, source=c.provider,
                source_url=c.source_url, photographer=c.photographer,
                attribution=c.attribution(), license=c.license, search_query=c.query,
                width=img.width, height=img.height,
                relevance_score=round(c.scores["semantic"], 4),
                quality_score=round(c.scores["quality"], 4),
                aspect_ratio_score=round(c.scores["aspect"], 4),
                final_score=round(c.scores["final"], 4)))
            log.append("[Selected] %s %s/%s score %.3f (sem %.2f qual %.2f aspect %.2f div %.2f)"
                       % (slot.slot_id, c.provider, c.id, c.scores["final"],
                          c.scores["semantic"], c.scores["quality"],
                          c.scores["aspect"], c.scores["diversity"]))
            break

        if picked is None:
            log.append("[Selected] %s -- nothing usable found" % slot.slot_id)

    state["hashes"] = used_hashes
    _save_deck_state(slide.presentation_id, state)

    rejected = sum(1 for c in cands if c.rejected)
    log.append("[Validation] %d candidate(s) rejected on inspection" % rejected)
    return SlideImages(
        slide_number=slide.slide_number,
        selected_images=selected,
        metadata={
            "queries_generated": queries,
            "plan_source": brief.source,
            "image_type": brief.image_type,
            "visual_intent": brief.visual_intent,
            "providers_used": order,
            "candidates_found": found,
            "candidates_ranked": len(cands),
            "candidates_rejected": rejected,
            "log": log,
        },
        candidates=cands if slide.debug else None,
    )


def select_for_slide_sync(slide: SlideRequest) -> SlideImages:
    """Blocking wrapper, for ppt.py and the stdlib server which are not async."""
    return asyncio.run(select_for_slide(slide))


# ---------- integration with ppt.py ----------

def from_deck(deck: dict, index: int, presentation_id: str = "",
              slots: list | None = None) -> SlideRequest:
    """Turn one slide of a ppt.py deck plan into a SlideRequest.

        images = engine.select_for_slide_sync(engine.from_deck(deck, i, deck["id"], slots))

    The caller supplies the slots because the caller owns the layout: only the renderer
    knows how big the hole is and what shape it wants. The engine deciding that for it
    would be a second, stale copy of ppt.IMAGE_RATIO.
    """
    s = deck["slides"][index]
    body = " ".join(filter(None, [
        s.get("subtitle"), " ".join(s.get("bullets") or []),
        s.get("stat"), s.get("label"), s.get("left"), s.get("right"), s.get("notes")]))
    return SlideRequest(
        presentation_id=presentation_id or deck.get("id") or deck.get("deck_title", "deck"),
        presentation_topic=deck.get("deck_title", ""),
        slide_number=index + 1,
        slide_title=s["title"],
        slide_content=body.strip(),
        template_id=deck.get("template", ""),
        image_slots=slots or [],
    )
