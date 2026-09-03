"""Visual Understanding Agent.

One LLM call turns a slide into a visual brief AND the search queries. The spec asked for
three agents (planner, query generator, router); routing falls out of image_type as a dict
lookup, and splitting planning from query writing costs three round trips to produce one
JSON object the model is perfectly able to emit in one.

If the call fails -- no key, rate limit, timeout, bad JSON -- fallback_plan() produces a
usable brief from the slide text alone. The engine must never go dark because an LLM did.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config
from .schemas import Query, SlideRequest, VisualPlan

# ponytail: this is ppt.py's _ask minus the deck schema and the TPM budgeting. Reusing it
# would make the image engine import the renderer -- wrong direction for a subsystem meant
# to be reusable. 25 lines of urllib is cheaper than that inversion.

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["visual_intent", "image_type", "primary_concepts", "secondary_concepts",
                 "visual_style", "avoid", "queries"],
    "properties": {
        "visual_intent": {"type": "string"},
        "image_type": {"type": "string", "enum": [
            "literal_object", "person", "place", "product", "technology", "process",
            "abstract_concept", "conceptual_realistic", "background",
            "data_visualization", "historical", "futuristic"]},
        "primary_concepts": {"type": "array", "items": {"type": "string"}},
        "secondary_concepts": {"type": "array", "items": {"type": "string"}},
        "visual_style": {"type": "string"},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "queries": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["query", "priority", "intent"],
            "properties": {
                "query": {"type": "string"},
                "priority": {"type": "integer"},
                "intent": {"type": "string"},
            }}},
    },
}

PROMPT = """Decide what photograph belongs on this slide.

Presentation topic: {topic}
Slide title: {title}
Slide content: {content}
Image slot: {slot}

Return:

visual_intent -- one sentence describing the picture a designer should go find.
image_type -- what kind of subject it is. Drives which stock library is searched.
primary_concepts -- 3-5 things that must be visible in the frame. Concrete nouns.
secondary_concepts -- 2-4 things that would help but are optional.
visual_style -- e.g. "professional realistic photography", "clean minimal product shot".
avoid -- what would make an image wrong here (cartoon, text in image, stock cliche...).
queries -- 3 to 8 stock-photo search queries, priority 1 (best) upward.

Queries are searched against Unsplash/Pexels/Pixabay, so:
- 2 to 6 words. Visual nouns. No sentences, no questions, no abstractions.
- Each query must be a DIFFERENT picture, not a rewording.
  Good, for "The Rise of Remote Work":
    person working home office / remote team video call / laptop kitchen table morning
  Bad: remote work / remote working / work remotely
- Describe what is in the frame, never the argument the slide is making.
  Not "how AI is changing healthcare". Yes "doctor examining brain scan monitor"."""


def _ask(prompt: str) -> dict:
    """One strict-JSON Groq call. Raises on anything that is not a usable plan."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    body = json.dumps({
        "model": config.MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "max_completion_tokens": 900,       # a brief is ~250 tokens; the rest is reasoning
        "reasoning_effort": "low",
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "visual_plan", "strict": True, "schema": SCHEMA}},
    }).encode()
    # Groq bills the output reservation against tokens-per-minute BEFORE generating, so an
    # illustrated deck -- one plan call per image slide -- runs the free tier dry after a
    # few slides. Wait the window out once rather than silently dropping to keyword
    # queries for every slide after the third.
    # ponytail: one batched call planning every slide at once would remove the wait
    # entirely. Worth doing when decks routinely carry more than ~5 images.
    for attempt in (0, 1):
        req = urllib.request.Request(config.GROQ_URL, body, {
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "User-Agent": "image_engine"})   # Groq's edge 403s the default urllib agent
        try:
            with urllib.request.urlopen(req, timeout=config.PLAN_TIMEOUT) as r:
                return json.loads(json.load(r)["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as e:
            wait = float(e.headers.get("retry-after") or 0) or 20.0
            if e.code == 429 and attempt == 0 and wait <= 65:
                print("[Visual Planner] TPM window spent, waiting %.0fs" % wait)
                time.sleep(wait)
                continue
            raise RuntimeError("groq %d: %s"
                               % (e.code, e.read().decode("utf-8", "replace")[:300]))


STOP = set("""a an the and or of for to in on at by with from as is are was were be been being
this that these those it its their our your his her they we you i how why what when where which
who will would can could should may might must not no than then so such very more most other
into over under about across after before between during through using use used help helps
new key main major role impact rise future world today modern industry business system systems
approach approaches way ways slide presentation overview introduction conclusion""".split())


def fallback_plan(slide: SlideRequest) -> VisualPlan:
    """No LLM. Pull concrete words out of the slide and build queries from them.

    Deliberately dumb and deterministic: this runs when the model is unavailable, so it
    must never itself fail. Worse queries beat no images.
    """
    text = "%s %s" % (slide.slide_title, slide.slide_content)
    words, seen = [], set()
    for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", text):
        lw = w.lower()
        if lw in STOP or lw in seen:
            continue
        seen.add(lw)
        words.append(lw)
    topic = " ".join(w for w in re.findall(r"[A-Za-z]{3,}", slide.presentation_topic)
                     if w.lower() not in STOP)[:40]
    concepts = words[:5] or [slide.slide_title.lower()[:40]]

    queries, taken = [], set()
    for raw in (" ".join(concepts[:3]),
                " ".join(concepts[:2] + [topic]) if topic else " ".join(concepts[1:4]),
                " ".join(concepts[2:5]) or " ".join(concepts),
                "%s professional photography" % (topic or concepts[0])):
        q = " ".join(raw.split())[:60]
        if q and q not in taken:
            taken.add(q)
            queries.append(Query(query=q, priority=len(queries) + 1, intent="fallback"))

    return VisualPlan(
        visual_intent="A professional photograph illustrating: %s" % slide.slide_title,
        image_type="conceptual_realistic",
        primary_concepts=concepts,
        secondary_concepts=words[5:8],
        avoid=["cartoon", "text inside image", "watermark"],
        queries=queries,
        source="fallback",
    )


def make_plan(slide: SlideRequest) -> VisualPlan:
    """The brief for this slide. Falls back to slide text if the model is unreachable."""
    slot = slide.image_slots[0] if slide.image_slots else None
    try:
        raw = _ask(PROMPT.format(
            topic=slide.presentation_topic or "(not given)",
            title=slide.slide_title,
            content=slide.slide_content or "(none)",
            slot="%s, %s" % (slot.role, slot.aspect_ratio) if slot else "one supporting image"))
        plan = VisualPlan(**raw, source="llm")
        plan.queries = [q for q in plan.queries if q.query.strip()][:8]
        if not plan.queries:
            raise RuntimeError("model returned no queries")
        return plan
    except Exception as e:                    # any failure -> keep going without the LLM
        print("[Visual Planner] LLM unavailable (%s), using fallback" % e)
        return fallback_plan(slide)


def route(plan: VisualPlan, available: list[str]) -> list[str]:
    """Which providers to search for this kind of picture. A lookup, not an agent."""
    order = config.ROUTES.get(plan.image_type, config.DEFAULT_ROUTE)
    ranked = [p for p in order if p in available]
    return ranked + [p for p in available if p not in ranked]
