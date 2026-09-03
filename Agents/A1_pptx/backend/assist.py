"""Ask AI. The model reads the document and proposes operations; it never writes to it.

The rule this file exists to enforce: an LLM response is untrusted input. It arrives as a
list of operations against ids that must already exist, deck.apply_ops validates every
one, and the answer to the browser is the resulting document plus an honest list of what
was refused. There is no path from a model response to the editor's state that skips
deck.clean_element.

    python -m backend.assist        # self-check, no API call
"""
import copy, json

from . import deck as D, ppt

# Strict constrained decoding: no length keywords, so limits live in the prompt and are
# enforced for real by apply_ops. `changes` and `element` are free-form objects because
# the vocabulary is large and clean_element is what actually polices it.
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "operations"],
    "properties": {
        "summary": {"type": "string"},
        "operations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "element_id", "slide_id", "changes"],
            "properties": {
                "action": {"type": "string", "enum": list(D.OPS)},
                "element_id": {"type": ["string", "null"]},
                "slide_id": {"type": ["string", "null"]},
                "changes": {"type": ["object", "null"]},
            }}}},
}

PROMPT = """You are editing a slide deck. Return operations that change it, not prose.

The deck is {w} x {h} inches. Positions are inches from the top-left; font sizes are
points. Colours are one of the theme tokens {tokens} (preferred -- they follow the theme)
or a #rrggbb literal. Fonts are the roles "display" or "body".

WHAT THE USER SELECTED: {scope}

USER ASKS: {ask}

CURRENT STATE (edit these exact ids -- inventing an id makes the operation fail):
{state}

Rules:
- Preserve the user's facts. Rewrite wording only if they asked you to.
- Keep every element inside 0..{w} horizontally and 0..{h} vertically.
- Do not overlap text with an existing image element. Move the text, not the photo.
- Prefer editing what is there to deleting and re-adding it: ids the user has selected,
  moved or restyled should survive.
- changes may be flat ({{"text": "...", "fontSize": 44}}) or nested
  ({{"style": {{"size": 44}}}}). Both work.
- To add: action "add_element", slide_id set, changes = the whole element
  ({{"type": "text", "x": 1, "y": 1, "w": 4, "h": 1, "content": {{"text": "..."}},
  "style": {{"size": 24, "color": "primary"}}}}).
- Element types: text, image, shape (rect/ellipse/triangle/arrow), line, table
  (content.rows, a list of rows of strings), chart (content.chart bar/line/pie/donut/
  area/scatter, content.categories, content.series [{{"name","values"}}]).
- You cannot create an image from nothing: there is no URL you can invent. To ask for a
  picture, leave the existing image element alone or reposition it.
- summary is one short sentence for the user, in plain words.
- Return the fewest operations that do the job. No operation at all is a valid answer if
  the deck already satisfies the request."""


def _slim(e):
    """What the model sees of an element. Full geometry (it has to place things) but
    text truncated and dead style keys dropped -- the state block is the whole prompt
    budget on a big slide, and an untouched default teaches the model nothing."""
    out = {"id": e["id"], "type": e["type"],
           "box": [round(e["x"], 2), round(e["y"], 2), round(e["w"], 2), round(e["h"], 2)]}
    st = {k: v for k, v in e["style"].items()
          if v not in (None, False, 0) and k not in ("fit", "valign")}
    if st:
        out["style"] = st
    if e["type"] == "text":
        out["text"] = e["content"].get("text", "")[:280]
    elif e["type"] == "image":
        out["alt"] = e["content"].get("alt") or "photo"
    elif e["type"] == "table":
        out["rows"] = e["content"]["rows"][:6]
    elif e["type"] == "chart":
        out["chart"] = e["content"]["chart"]
        out["categories"] = e["content"]["categories"]
        out["series"] = e["content"]["series"]
    elif e["type"] in ("shape", "line"):
        out["shape"] = e["content"].get("shape")
    if e.get("rotation"):
        out["rotation"] = e["rotation"]
    if e.get("hidden"):
        out["hidden"] = True
    return out


def context(deck, selection):
    """The structured state for the ask, and a sentence describing what is selected.

    Selection is what makes "make this smaller" mean anything, so the scope is narrowed
    to exactly what the user picked: elements, then slides, then the whole deck. A
    selected element still ships its whole slide -- the model cannot balance a layout it
    cannot see.
    """
    slide_ids = set(selection.get("slides") or [])
    el_ids = set(selection.get("elements") or [])
    slides = deck["slides"]

    if el_ids:
        owners = [s for s in slides if any(e["id"] in el_ids for e in s["elements"])]
        scope = "%d element(s) on slide %s: %s" % (
            len(el_ids), ", ".join(s["name"] for s in owners) or "?", ", ".join(sorted(el_ids)))
    elif slide_ids:
        owners = [s for s in slides if s["id"] in slide_ids]
        scope = "%d whole slide(s): %s" % (len(owners), ", ".join(s["name"] for s in owners))
    else:
        owners = slides
        scope = "nothing -- operate on the whole deck (%d slides)" % len(slides)

    state = {"deck_title": deck["deck_title"], "theme": deck["template"],
             "slides": [{"id": s["id"], "name": s["name"], "position": slides.index(s) + 1,
                         "notes": (s.get("notes") or "")[:200],
                         "elements": [_slim(e) for e in s["elements"]]}
                        for s in owners]}
    if owners is not slides:            # so the model knows where this sits in the deck
        state["deck_outline"] = ["%d. %s" % (i, s["name"]) for i, s in enumerate(slides, 1)]
    return scope, state


def edit(deck, ask, selection=None, template=None):
    """Run one AI edit. Returns (new_deck, report). Never raises on a bad model response
    and never returns a half-applied document: apply_ops works on a copy."""
    scope, state = context(deck, selection or {})
    body = json.dumps(state, separators=(",", ":"))
    prompt = PROMPT.format(w=deck.get("w", D.W), h=deck.get("h", D.H), ask=ask[:2000],
                           scope=scope, state="", tokens=", ".join(D.TOKENS))
    body = ppt.fit(body, len(prompt))
    out = ppt._ask([{"role": "user", "content": PROMPT.format(
        w=deck.get("w", D.W), h=deck.get("h", D.H), ask=ask[:2000], scope=scope,
        state=body, tokens=", ".join(D.TOKENS))}], SCHEMA, "edit")

    ops = [_unpack(o) for o in (out.get("operations") or [])]
    new, applied, rejected = D.apply_ops(deck, ops)
    if template:
        new["template"] = template
    return new, {"summary": out.get("summary", "")[:400],
                 "applied": len(applied), "rejected": rejected,
                 "actions": applied}


def _unpack(op):
    """The schema makes every key required so strict decoding accepts it, which means
    add_element arrives with its element in `changes`. Put it where apply_ops looks."""
    if not isinstance(op, dict):
        return op
    op = dict(op)
    if op.get("action") == "add_element":
        op["element"] = op.get("changes") or {}
    elif op.get("action") == "add_slide":
        op["slide"] = op.get("changes") or {}
    elif op.get("action") == "reorder_slides":
        op["order"] = (op.get("changes") or {}).get("order")
    elif op.get("action") == "set_theme":
        op["template"] = (op.get("changes") or {}).get("template")
    return op


# ---------- self-check ----------

def demo():
    d = D.expand(copy.deepcopy(ppt.DEMO))
    s0, s2 = d["slides"][0], d["slides"][2]
    title = s0["elements"][0]["id"]

    scope, state = context(d, {})
    assert "whole deck" in scope and len(state["slides"]) == 5
    assert "deck_outline" not in state              # nothing selected: no need to orient

    scope, state = context(d, {"slides": [s2["id"]]})
    assert len(state["slides"]) == 1 and s2["name"] in scope
    assert len(state["deck_outline"]) == 5

    scope, state = context(d, {"elements": [title]})
    assert title in scope and len(state["slides"]) == 1, scope
    assert state["slides"][0]["elements"][0]["id"] == title
    assert "text" in state["slides"][0]["elements"][0]

    # a model response, applied without the model ever touching the document
    fake = {"summary": "Bigger title, one supporting shape.", "operations": [
        {"action": "update_element", "element_id": title, "slide_id": None,
         "changes": {"fontSize": 52, "color": "accent"}},
        {"action": "add_element", "element_id": None, "slide_id": s0["id"],
         "changes": {"type": "shape", "x": 1, "y": 6, "w": 3, "h": 0.1,
                     "content": {"shape": "rect"}, "style": {"fill": "accent"}}},
        {"action": "update_element", "element_id": "hallucinated_id", "slide_id": None,
         "changes": {"text": "nope"}},
        {"action": "add_element", "element_id": None, "slide_id": s0["id"],
         "changes": {"type": "image", "x": 0, "y": 0, "w": 4, "h": 4,
                     "content": {"url": "https://tracker.example/pixel.png"}}},
    ]}
    new, applied, rejected = D.apply_ops(d, [_unpack(o) for o in fake["operations"]])
    assert applied == ["update_element", "add_element", "add_element"], applied
    assert len(rejected) == 1 and "hallucinated_id" in rejected[0], rejected
    assert new["slides"][0]["elements"][0]["style"]["size"] == 52
    assert new["slides"][0]["elements"][0]["style"]["color"] == "accent"
    assert new["slides"][0]["elements"][-1]["content"]["url"] is None, \
        "an off-site image URL must not survive into the document"
    assert d["slides"][0]["elements"][0]["style"]["size"] == 40, "the original is untouched"

    assert _unpack({"action": "reorder_slides", "changes": {"order": ["a"]}})["order"] == ["a"]
    assert _unpack({"action": "set_theme", "changes": {"template": "startup"}})["template"] \
        == "startup"
    print("ok - selection scoping, %d ops applied, hallucinated id and off-site URL both "
          "refused, source deck unchanged" % len(applied))


if __name__ == "__main__":
    demo()
