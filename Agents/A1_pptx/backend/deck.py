"""The canonical presentation document. Both renderers read this and nothing else.

Before: ppt.py drew five hardcoded slide kinds in python-pptx and Slide.jsx drew the same
five by hand in the browser, kept in step by shipping the geometry over /api/meta. Two
implementations of one layout is a drift bug waiting for a deadline.

Now there is one list of elements per slide. expand() turns the model's authored plan
into that list once, on the way in; ppt.render() and Slide.jsx are both dumb loops over
it. Editing, AI ops and export all touch the same structure, so the editor cannot
disagree with the .pptx -- there is only one description of the slide.

    python -m backend.deck        # self-check, no API call

Units: inches for geometry (the slide is 13.333 x 7.5), points for type. Exactly what
python-pptx wants and what the browser's cqw maths already converts, so neither renderer
does arithmetic the other might get wrong.
"""
import copy, os, re, time

W, H = 13.333, 7.5                    # 16:9. 4:3 is 10 x 7.5; custom is any pair.
TOKENS = ("primary", "accent", "text", "muted", "bg")   # colours a theme owns
TYPES = ("text", "image", "shape", "line", "table", "chart")
SHAPES = ("rect", "ellipse", "triangle", "arrow", "line")
CHARTS = ("bar", "line", "pie", "donut", "area", "scatter")
HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")

# Text over these lengths used to be cut mid-word because the box was fixed. Elements
# carry their own box now, so these only shape what the model writes.
CAP = {"title": 60, "subtitle": 120, "bullet": 90, "stat": 12, "label": 40,
       "left": 220, "right": 220, "notes": 300}

IMAGE_X, IMAGE_W, IMAGE_H = 7.333, 6.0, 7.5
IMAGE_RATIO = "4:5"
IMAGE_KINDS = ("title", "closing", "bullets")
TEXT_W, TEXT_W_IMG = 11.5, 6.0


def clip(s, n):
    """The authored caps are the overflow guarantee: a generated deck must not arrive
    already spilling out of the box expand() sizes for it. Once it is elements the user
    owns the box, so nothing downstream clips again."""
    return s if s is None or len(s) <= n else s[: n - 1].rstrip() + "…"


def new_id(prefix="el"):
    new_id.n += 1
    return "%s_%d%d" % (prefix, int(time.time() * 1000) % 10**9, new_id.n)


new_id.n = 0


def color(value, template, fallback="text"):
    """A colour is either a theme token or a literal hex. Tokens are the whole theme
    system: retheming a deck swaps the template and every token element follows."""
    v = value or fallback
    if v in TOKENS:
        return "#" + str(template.get(v, "000000")).lstrip("#")
    if HEX.match(str(v)):
        return "#" + str(v).lstrip("#")
    return "#" + str(template.get(fallback, "000000")).lstrip("#")


def el(type, x, y, w, h, content=None, style=None, **kw):
    e = {"id": new_id(type), "type": type, "x": round(x, 4), "y": round(y, 4),
         "w": round(w, 4), "h": round(h, 4), "rotation": 0, "locked": False,
         "hidden": False, "content": content or {}, "style": style or {}}
    e.update(kw)
    return e


def text_el(x, y, w, h, text, size, color, *, font="body", bold=False, align="left",
            valign="top", **style):
    """font is a role -- display or body -- not a family. The template resolves it, so a
    theme change restyles the type without touching a single element."""
    return el("text", x, y, w, h, {"text": text},
              dict(size=size, color=color, font=font, bold=bold, align=align,
                   valign=valign, **style))


# ---------- authored slide -> elements ----------

def expand_slide(s, num, total):
    """One authored slide (the shape the model writes) into positioned elements.

    Geometry is lifted verbatim from the old render()/Slide.jsx pair so decks made
    before the editor existed open looking exactly as they always did.
    """
    kind = s.get("kind", "bullets")
    img = s.get("image") or {}
    has_image = kind in IMAGE_KINDS and bool(
        img.get("url") or (img.get("path") and os.path.isfile(img["path"])))
    w = TEXT_W_IMG if has_image else TEXT_W
    out = []

    if has_image:
        out.append(el("image", IMAGE_X, 0, IMAGE_W, IMAGE_H,
                      {"url": img.get("url"), "path": img.get("path"),
                       "credit": img.get("credit"), "source_url": img.get("source_url"),
                       "alt": img.get("query") or ""},
                      {"fit": "cover"}))

    if kind in ("title", "closing"):
        # The old renderer stacked title and subtitle inside one middle-anchored box.
        # Two elements, so each is independently selectable -- the point of an editor.
        if s.get("subtitle"):
            out.append(text_el(1.3, 2.3, w - 0.8, 1.4, clip(s.get("title", ""), CAP["title"]), 40, "primary",
                               font="display", bold=True, align="center", valign="bottom"))
            out.append(text_el(1.3, 3.92, w - 0.8, 1.28, clip(s["subtitle"], CAP["subtitle"]), 18, "accent",
                               align="center", valign="top"))
        else:
            out.append(text_el(1.3, 2.3, w - 0.8, 2.9, clip(s.get("title", ""), CAP["title"]), 40, "primary",
                               font="display", bold=True, align="center", valign="middle"))
    elif kind == "stat":
        out.append(text_el(0.9, 0.7, w, 1.0, clip(s.get("title", ""), CAP["title"]), 24, "primary",
                           font="display", bold=True))
        out.append(text_el(0.9, 2.1, w, 2.4, clip(s.get("stat") or "", CAP["stat"]), 90, "accent",
                           font="display", bold=True, align="center", valign="bottom"))
        out.append(text_el(0.9, 4.6, w, 0.9, clip(s.get("label") or "", CAP["label"]), 20, "text",
                           align="center", valign="top"))
    elif kind in ("chart", "table"):
        # The data kinds. Both element types already exist in the document, in both
        # renderers and in the exporter -- until now only the planner could not ask for
        # one, so a slide about numbers arrived as a paragraph about numbers.
        out.append(text_el(0.9, 0.7, w, 1.2, clip(s.get("title", ""), CAP["title"]), 30,
                           "primary", font="display", bold=True))
        # clean_element is the one normaliser: ragged series, missing categories and a
        # nonsense chart name all come back legal here rather than in the renderer.
        out.append(clean_element(
            el("chart", 0.9, 2.1, w, 4.3,
               {"chart": s.get("chart") or "bar", "categories": s.get("categories") or [],
                "series": s.get("series") or [], "legend": True, "labels": True},
               {"size": 12, "color": "text"})
            if kind == "chart" else
            el("table", 0.9, 2.1, w, min(4.3, 0.52 * len(s.get("rows") or [["", ""]])),
               {"rows": s.get("rows") or [], "header": True}, {"size": 14, "color": "text"})))
    else:
        out.append(text_el(0.9, 0.7, w, 1.3, clip(s.get("title", ""), CAP["title"]), 30, "primary",
                           font="display", bold=True))
        if kind == "two_col":
            out.append(text_el(0.9, 2.3, 5.4, 4.2, clip(s.get("left") or "", CAP["left"]), 16, "text"))
            out.append(text_el(7.0, 2.3, 5.4, 4.2, clip(s.get("right") or "", CAP["right"]), 16, "text"))
        else:
            bullets = [clip(b, CAP["bullet"]) for b in (s.get("bullets") or [])[:5] if b]
            out.append(text_el(0.9, 2.2, w, 4.3, "\n".join(bullets), 18, "text",
                               bullets=True, spaceAfter=16))

    if kind not in ("title", "closing"):
        out.append(text_el(0.9 + w - 1.0, 6.7, 1.0, 0.4, "%d / %d" % (num, total), 10,
                           "muted", align="right"))

    # The authored fields (title, bullets, image, ...) ride along. The image engine
    # searches on them, and re-expanding a slide once its photo lands has to be able to
    # rebuild the same words. clean_slide drops them at the first save from the editor,
    # which is exactly when they stop being the truth about the slide.
    return dict(s, **{
        "id": s.get("id") or new_id("slide"),
        "name": (s.get("name") or s.get("title") or "Slide")[:120],
        "kind": kind, "background": s.get("background") or {"color": "bg"},
        "elements": out, "notes": s.get("notes", ""), "hidden": bool(s.get("hidden"))})


def expand(deck):
    """Give a deck an elements list per slide if it has not got one. Idempotent, so
    loading, saving and reloading an already-expanded deck changes nothing."""
    deck.setdefault("w", W)
    deck.setdefault("h", H)
    deck["version"] = 2
    slides = deck.get("slides", [])
    total = len(slides)
    deck["slides"] = [s if "elements" in s else expand_slide(s, i, total)
                      for i, s in enumerate(slides, 1)]
    return deck


# ---------- visual coverage ----------
#
# The application decides whether a slide communicates visually, by reading the elements
# it ended up with. Asking the model "did you add a picture?" is asking the thing that
# just failed to do it whether it did it.
#
# A slide that fails gets one repair pass: deterministic, offline, no second model call,
# in the order a designer reaches for -- quantities become a chart, two sides become a
# table, a list becomes a numbered composition. Nothing here invents a picture to satisfy
# a rule; where none of those honestly applies the slide is left alone and says so.

VISUAL_TYPES = ("image", "chart", "table")
# An opener is a title, and a stat slide is one number at 90pt -- that number *is* the
# statistical visualisation. Neither is a slide failing to communicate visually.
NO_VISUAL_NEEDED = ("title", "closing", "stat")
MIN_VISUAL = 0.06                 # of the slide's area, below which it is decoration
FIGURE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*([%\w]{0,3})")


def has_visual(slide):
    """Does this slide already carry something that communicates visually?

    An image counts only once it has a URL to draw, and anything that counts has to be
    big enough to be seen: a thumbnail in a corner is not a visual, and letting one pass
    would make the whole check theatre.
    """
    shapes = []
    for e in slide.get("elements", []):
        if e.get("hidden"):
            continue
        t = e.get("type")
        big = num(e.get("w"), 0, 99) * num(e.get("h"), 0, 99) >= MIN_VISUAL * W * H
        if t == "image" and (e.get("content") or {}).get("url") and big:
            return True
        if t in ("chart", "table") and big:
            return True
        if t in ("shape", "line"):
            shapes.append((round(num(e.get("w"), 0, 99), 2), round(num(e.get("h"), 0, 99), 2)))
    # A repeated shape is a composition: chips down a list, nodes on a timeline, steps in
    # a flow. A single bar under a heading is a rule, and a rule is decoration. Three of
    # anything is a diagram either way.
    return len(shapes) >= 3 or (len(shapes) >= 2 and len(set(shapes)) < len(shapes))


def _body(slide):
    """The slide's body copy: the biggest text box below the heading.

    By box, not by character count -- a "3 / 12" page number is five characters in four
    tenths of a square inch, and measuring the words picks it over the bullets. Read off
    the elements rather than the authored fields, so this works on a slide somebody built
    by hand as well as on a generated one.
    """
    texts = sorted((e for e in slide.get("elements", [])
                    if e.get("type") == "text" and not e.get("hidden")
                    and str((e.get("content") or {}).get("text", "")).strip()),
                   key=lambda e: num(e.get("y"), -H, H * 2))
    return max(texts[1:], default=None,
               key=lambda e: num(e.get("w"), 0, 99) * num(e.get("h"), 0, 99))


def _figures(lines):
    """(label, value) per line, but only when the lines are really one comparable series.

    Mixed units on one axis is a chart that lies: "412 ms" against "1.2 s" plotted as 412
    and 1.2 is worse than the bullet list it replaced. One unit throughout, or no chart.
    """
    out, units = [], set()
    for line in lines:
        m = FIGURE.search(line)
        if not m:
            return []
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            return []
        units.add((m.group(2) or "").lower())
        label = re.sub(r"\s+", " ", line[:m.start()] + line[m.end():]).strip(" :–—-")
        out.append((label[:24] or "Item %d" % (len(out) + 1), value))
    return out if len(out) >= 3 and len(units) == 1 else []


def add_visual(slide):
    """Repair one slide that has no visual. Mutates its element list in place and returns
    what was added, or None when nothing honest applied."""
    body = _body(slide)
    if not body:
        return None
    lines = [ln.strip() for ln in str(body["content"]["text"]).split("\n") if ln.strip()]
    if not lines:
        return None
    els = slide["elements"]
    # Anything sitting level with the body is a second column, and a second column is the
    # other half of a comparison. Worked out first because it decides which repair is even
    # available: there is no free half of the slide to put a chart in.
    sides = [e for e in els if e.get("type") == "text" and e is not body
             and abs(num(e.get("y"), -H, H * 2) - num(body.get("y"), -H, H * 2)) < 0.3
             and str((e.get("content") or {}).get("text", "")).strip()]

    # 1. Quantities. The numbers were already on the slide as prose; this plots them.
    figures = [] if sides else _figures(lines)
    if figures:
        # The chart takes the half the words are not in, and the words give up the rest.
        at = 7.0 if body["x"] + body["w"] / 2 < W / 2 else 0.9
        body["w"] = min(body["w"], 5.4)
        els.append(clean_element(el("chart", at, body["y"], 5.4,
                                    min(4.2, max(2.0, H - body["y"] - 0.9)), {
            "chart": "bar", "categories": [f[0] for f in figures],
            "series": [{"name": str(slide.get("name", "Value"))[:40],
                        "values": [f[1] for f in figures]}], "legend": False},
            {"size": 11, "color": "text"})))
        return "chart"

    # 2. Two sides of an argument are a comparison, and a comparison is a table.
    if sides:
        other = sides[0]
        # Left column first, whichever of the two the body copy turned out to be.
        pair = (other, body) if other["x"] <= body["x"] else (body, other)
        cols = [[ln.strip() for ln in str(c["content"]["text"]).split("\n") if ln.strip()]
                for c in pair]
        rows = [[c[i] if i < len(c) else "" for c in cols]
                for i in range(max(len(cols[0]), len(cols[1])))][:8]
        top = min(body["y"], other["y"])
        els.remove(body)
        els.remove(other)
        els.append(clean_element(el("table", 0.9, top, TEXT_W,
                                    min(4.2, max(1.6, 0.7 * len(rows))),
                                    {"rows": rows, "header": False},
                                    {"size": 15, "color": "text"})))
        return "table"

    # One sentence is a sentence. Numbering it would be theatre, and theatre is what the
    # whole check exists to keep off the slide.
    if len(lines) < 2:
        return None

    # 3. A plain list. Numbered chips give it a focal rhythm and a hierarchy the flat
    #    paragraph did not have. Three or more shapes, so has_visual() counts it for a
    #    real reason rather than because a decorative bar was dropped in.
    lines = lines[:5]
    gap = min(1.05, max(0.62, body["h"] / len(lines)))
    size = num(body["style"].get("size"), 4, 400, 18)
    els.remove(body)
    for i, line in enumerate(lines):
        y = round(body["y"] + i * gap, 4)
        els.append(el("shape", body["x"], y + 0.04, 0.46, 0.46, {"shape": "ellipse"},
                      {"fill": "accent"}))
        els.append(text_el(body["x"], y + 0.04, 0.46, 0.46, str(i + 1), min(15, size * 0.8),
                           "bg", font="display", bold=True, align="center", valign="middle"))
        els.append(text_el(body["x"] + 0.74, y, body["w"] - 0.74, gap, line, size,
                           body["style"].get("color") or "text", valign="middle"))
    return "composition"


def ensure_visuals(deck):
    """Every content slide communicates visually, or the reason is on the record.

    Runs once, after images have landed, so a slide the photo engine filled is left alone
    and only the ones it could not fill are repaired. The repairs are deterministic, so a
    second pass reaches the same answer -- there is no loop here to bound.
    """
    report = []
    for i, s in enumerate(deck.get("slides", []), 1):
        if s.get("kind") in NO_VISUAL_NEEDED:
            continue
        if has_visual(s):
            report.append({"slide": i, "id": s.get("id"), "visual": "present"})
            continue
        added = add_visual(s)
        report.append({"slide": i, "id": s.get("id"), "visual": added or "none",
                       "repaired": bool(added)})
    return report


# ---------- validation: the trust boundary ----------

def num(v, lo, hi, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return default if f != f else max(lo, min(hi, f))      # f != f catches NaN


def _safe_url(u):
    u = str(u or "")
    return u if u.startswith(("http://", "https://")) else None


def clean_element(e, existing=None):
    """Coerce anything -- an AI response, a hand-edited JSON, a stale client -- into a
    legal element. Never raises: a bad field falls back, it does not lose the slide.

    Every element enters the document through here, so this is where the trust boundary
    sits. Nothing downstream re-checks, and nothing downstream has to.
    """
    base = copy.deepcopy(existing) if existing else {}
    if not isinstance(e, dict):
        return base or None
    t = e.get("type", base.get("type", "text"))
    if t not in TYPES:
        t = base.get("type", "text")
    out = {
        "id": str(e.get("id") or base.get("id") or new_id(t))[:64],
        "type": t,
        "x": num(e.get("x", base.get("x", 0)), -W, W * 2),
        "y": num(e.get("y", base.get("y", 0)), -H, H * 2),
        "w": num(e.get("w", base.get("w", 2)), 0.05, W * 2, 2),
        "h": num(e.get("h", base.get("h", 0.5)), 0.05, H * 2, 0.5),
        "rotation": num(e.get("rotation", base.get("rotation", 0)), -360, 360),
        "locked": bool(e.get("locked", base.get("locked", False))),
        "hidden": bool(e.get("hidden", base.get("hidden", False))),
        "content": e.get("content", base.get("content")) or {},
        "style": e.get("style", base.get("style")) or {},
    }
    if not isinstance(out["content"], dict):
        out["content"] = {}
    if not isinstance(out["style"], dict):
        out["style"] = {}
    st, ct = out["style"], out["content"]

    # Style is a small closed vocabulary. Anything else is dropped rather than passed to
    # two renderers that would each have to guess what it meant.
    st["size"] = num(st.get("size", 18), 4, 400, 18)
    st["opacity"] = num(st.get("opacity", 1), 0, 1, 1)
    st["radius"] = num(st.get("radius", 0), 0, 4)
    st["strokeWidth"] = num(st.get("strokeWidth", 0), 0, 1)
    st["lineHeight"] = num(st.get("lineHeight", 1.2), 0.6, 4, 1.2)
    st["letterSpacing"] = num(st.get("letterSpacing", 0), -5, 20)
    st["spaceAfter"] = num(st.get("spaceAfter", 0), 0, 96)
    st["align"] = st.get("align") if st.get("align") in (
        "left", "center", "right", "justify") else "left"
    st["valign"] = st.get("valign") if st.get("valign") in (
        "top", "middle", "bottom") else "top"
    st["font"] = st.get("font") if st.get("font") in ("display", "body") else "body"
    # A literal family overrides the role. Roles are the default so a theme change
    # restyles the deck; the override exists for the font picker in the toolbar.
    fam = str(st.get("family") or "").strip()[:60]
    st["family"] = fam if re.fullmatch(r"[\w \-'.,]+", fam or "") and fam else None
    for k in ("bold", "italic", "underline", "bullets", "numbered", "shadow"):
        st[k] = bool(st.get(k))
    for k in ("color", "fill", "stroke"):
        v = st.get(k)
        st[k] = str(v) if v is not None and (v in TOKENS or HEX.match(str(v))) else None
    st["fit"] = st.get("fit") if st.get("fit") in ("cover", "contain") else "cover"

    if t == "text":
        ct["text"] = str(ct.get("text", ""))[:4000]
    elif t == "image":
        # A remote src would let a saved deck fetch from anywhere the moment it opens.
        # Only our own cache, our own uploads, or an inline data URI may render.
        u = str(ct.get("url") or "")
        ct["url"] = u if u.startswith(("/media/", "/uploads/", "data:image/")) else None
        ct["path"] = str(ct.get("path") or "") or None
        ct["credit"] = str(ct.get("credit") or "")[:200] or None
        ct["source_url"] = _safe_url(ct.get("source_url"))
        ct["alt"] = str(ct.get("alt") or "")[:200]
    elif t == "shape":
        ct["shape"] = ct.get("shape") if ct.get("shape") in SHAPES else "rect"
    elif t == "line":
        ct["shape"] = "line"
    elif t == "table":
        rows = ct.get("rows")
        rows = rows if isinstance(rows, list) and rows else [["", ""], ["", ""]]
        rows = [[str(c)[:2000] for c in (r if isinstance(r, list) else [r])][:12]
                for r in rows][:24]
        width = max(len(r) for r in rows) or 1
        ct["rows"] = [r + [""] * (width - len(r)) for r in rows]
        ct["header"] = bool(ct.get("header", True))
    elif t == "chart":
        ct["chart"] = ct.get("chart") if ct.get("chart") in CHARTS else "bar"
        cats = ct.get("categories")
        ct["categories"] = ([str(c)[:40] for c in cats][:24]
                            if isinstance(cats, list) and cats else ["A", "B", "C"])
        raw = ct.get("series") if isinstance(ct.get("series"), list) else []
        ct["series"] = [{"name": str(s.get("name", "Series"))[:40],
                         "values": [num(v, -1e12, 1e12) for v in (s.get("values") or [])][:24]}
                        for s in raw if isinstance(s, dict)][:8]
        if not ct["series"]:
            ct["series"] = [{"name": "Series 1", "values": [1.0] * len(ct["categories"])}]
        for s in ct["series"]:                   # ragged rows break python-pptx outright
            s["values"] = (s["values"] + [0.0] * len(ct["categories"]))[:len(ct["categories"])]
        ct["legend"] = bool(ct.get("legend", True))
        ct["labels"] = bool(ct.get("labels", True))
    return out


def clean_slide(s, existing=None):
    base = existing or {}
    els = s.get("elements", base.get("elements")) or []
    return {
        "id": str(s.get("id") or base.get("id") or new_id("slide"))[:64],
        "name": str(s.get("name", base.get("name", "Slide")))[:120],
        "kind": s.get("kind", base.get("kind", "bullets")),
        "background": s.get("background", base.get("background")) or {"color": "bg"},
        "elements": [c for c in (clean_element(e) for e in els[:200]) if c],
        "notes": str(s.get("notes", base.get("notes", "")))[:4000],
        "hidden": bool(s.get("hidden", base.get("hidden", False))),
    }


def clean(deck, existing=None):
    """A whole deck, arriving from a browser. Server-owned fields (id, created, prompt)
    come from `existing` and are never taken from the request body."""
    base = copy.deepcopy(existing) if existing else {}
    slides = [clean_slide(s) for s in (deck.get("slides") or [])[:200]]
    if not slides:
        raise ValueError("a deck needs at least one slide")
    base.update({
        "deck_title": str(deck.get("deck_title", base.get("deck_title", "Untitled")))[:200] or "Untitled",
        "template": str(deck.get("template", base.get("template", "corporate")))[:40],
        "w": num(deck.get("w", base.get("w", W)), 4, 60, W),
        "h": num(deck.get("h", base.get("h", H)), 3, 60, H),
        "slides": slides,
        "version": 2,
        "updated": time.time(),
    })
    return base


# ---------- AI operations ----------

OPS = ("update_element", "add_element", "delete_element", "update_slide",
       "add_slide", "delete_slide", "reorder_slides", "set_theme")

ALIAS = {"fontSize": ("style", "size"), "size": ("style", "size"),
         "color": ("style", "color"), "fill": ("style", "fill"),
         "stroke": ("style", "stroke"), "bold": ("style", "bold"),
         "italic": ("style", "italic"), "align": ("style", "align"),
         "valign": ("style", "valign"), "font": ("style", "font"),
         "opacity": ("style", "opacity"), "lineHeight": ("style", "lineHeight"),
         "radius": ("style", "radius"), "bullets": ("style", "bullets"),
         "text": ("content", "text"), "url": ("content", "url"),
         "rows": ("content", "rows"), "series": ("content", "series"),
         "categories": ("content", "categories"), "chart": ("content", "chart"),
         "shape": ("content", "shape")}


def _merge(element, changes):
    """Flat changes are a kindness to the model: it may send {"text": ...} or
    {"fontSize": 44} rather than the nested shape, and both land in the right slot."""
    out = copy.deepcopy(element)
    for k, v in (changes or {}).items():
        if k in ("x", "y", "w", "h", "rotation", "locked", "hidden", "type"):
            out[k] = v
        elif k in ("style", "content"):
            out[k] = dict(out.get(k) or {}, **(v if isinstance(v, dict) else {}))
        elif k in ALIAS:
            slot, key = ALIAS[k]
            out[slot] = dict(out.get(slot) or {}, **{key: v})
    return out


def apply_ops(deck, ops):
    """Apply structured operations. Returns (new_deck, applied, rejected).

    The model never touches the document. It proposes operations against ids that must
    already exist, every element goes through clean_element, and anything unrecognised
    is rejected with a reason rather than silently dropped or silently obeyed. The input
    deck is not mutated, so a rejected batch leaves the user's work exactly as it was.
    """
    deck = copy.deepcopy(deck)
    index = {s["id"]: s for s in deck["slides"]}
    applied, rejected = [], []

    def find(eid):
        for s in deck["slides"]:
            for i, e in enumerate(s["elements"]):
                if e["id"] == eid:
                    return s, i
        return None, None

    for op in (ops or [])[:200]:
        if not isinstance(op, dict):
            rejected.append("operation is not an object")
            continue
        action = op.get("action")
        try:
            if action == "update_element":
                s, i = find(op.get("element_id"))
                if s is None:
                    raise ValueError("no element %r" % op.get("element_id"))
                s["elements"][i] = clean_element(
                    _merge(s["elements"][i], op.get("changes") or {}), s["elements"][i])
            elif action == "add_element":
                s = index.get(op.get("slide_id")) or deck["slides"][0]
                e = clean_element(op.get("element") or op)
                if e is None:
                    raise ValueError("unusable element")
                s["elements"].append(e)
            elif action == "delete_element":
                s, i = find(op.get("element_id"))
                if s is None:
                    raise ValueError("no element %r" % op.get("element_id"))
                s["elements"].pop(i)
            elif action == "update_slide":
                s = index.get(op.get("slide_id"))
                if s is None:
                    raise ValueError("no slide %r" % op.get("slide_id"))
                changes = op.get("changes") or {}
                at = deck["slides"].index(s)
                deck["slides"][at] = index[s["id"]] = clean_slide(
                    dict(s, **{k: v for k, v in changes.items()
                               if k in ("name", "notes", "background", "hidden")}), s)
            elif action == "add_slide":
                s = clean_slide(op.get("slide") or {"elements": []})
                at = op.get("index")
                at = len(deck["slides"]) if at is None else int(num(at, 0, 200))
                deck["slides"].insert(at, s)
                index[s["id"]] = s
            elif action == "delete_slide":
                s = index.get(op.get("slide_id"))
                if s is None or len(deck["slides"]) < 2:
                    raise ValueError("cannot delete that slide")
                deck["slides"].remove(s)
                index.pop(s["id"])
            elif action == "reorder_slides":
                order = [i for i in (op.get("order") or []) if isinstance(i, str)]
                if sorted(order) != sorted(index):
                    raise ValueError("reorder must list every slide id exactly once")
                deck["slides"] = [index[i] for i in order]
            elif action == "set_theme":
                deck["template"] = str(op.get("template", deck["template"]))[:40]
            else:
                raise ValueError("unknown action %r" % action)
            applied.append(action)
        except Exception as e:
            rejected.append("%s: %s" % (action, e))
    return deck, applied, rejected


# ---------- self-check ----------

def demo():
    from . import ppt
    d = expand(copy.deepcopy(ppt.DEMO))
    assert d["version"] == 2 and len(d["slides"]) == 5
    assert [s["kind"] for s in d["slides"]] == \
        ["title", "bullets", "stat", "two_col", "closing"]
    before = copy.deepcopy(d["slides"][0]["elements"])
    assert expand(d)["slides"][0]["elements"] == before, "expand must be idempotent"
    for s in d["slides"]:
        assert s["elements"] and all(e["type"] in TYPES for e in s["elements"])
        for e in s["elements"]:
            assert -0.01 <= e["x"] < W and -0.01 <= e["y"] < H, e
    # every slide but the two ends carries its page number
    assert sum("/" in e["content"].get("text", "") for e in d["slides"][2]["elements"]) == 1
    assert not any("/" in e["content"].get("text", "") for e in d["slides"][0]["elements"])

    t = {"primary": "16324F", "accent": "2E86AB", "bg": "F7F9FB", "text": "1C1C1C",
         "muted": "8A94A0"}
    assert color("accent", t) == "#2E86AB"
    assert color("#ff0000", t) == "#ff0000"
    assert color("javascript:alert(1)", t) == "#1C1C1C"   # falls back, never passes through

    # validation: hostile input comes back legal, never crashes, never passes through
    bad = clean_element({"type": "script", "x": 1e9, "w": -5, "style": {"color": "url(x)"},
                         "content": {"text": "hi"}})
    assert bad["type"] == "text" and bad["x"] <= W * 2 and bad["w"] >= 0.05
    assert bad["style"]["color"] is None
    assert clean_element({"type": "text", "style": {"size": float("nan")}})["style"]["size"] == 18
    img = clean_element({"type": "image", "content": {"url": "https://evil.example/x.png",
                                                      "source_url": "javascript:x"}})
    assert img["content"]["url"] is None and img["content"]["source_url"] is None
    assert clean_element({"type": "image", "content": {"url": "/media/a/b.img"}})["content"]["url"]
    ch = clean_element({"type": "chart", "content": {"chart": "donut", "categories": ["a", "b"],
                                                     "series": [{"name": "S", "values": [1]}]}})
    assert ch["content"]["series"][0]["values"] == [1.0, 0.0], ch["content"]["series"]
    tb = clean_element({"type": "table", "content": {"rows": [["a", "b", "c"], ["d"]]}})
    assert tb["content"]["rows"] == [["a", "b", "c"], ["d", "", ""]], tb["content"]["rows"]

    # operations
    first = d["slides"][0]["elements"][0]["id"]
    out, ok, bad_ops = apply_ops(d, [
        {"action": "update_element", "element_id": first, "changes": {"fontSize": 44}},
        {"action": "add_element", "slide_id": d["slides"][0]["id"],
         "element": {"type": "shape", "x": 1, "y": 1, "w": 2, "h": 1,
                     "content": {"shape": "ellipse"}}},
        {"action": "update_element", "element_id": "nope", "changes": {"fontSize": 9}},
        {"action": "drop_database"},
    ])
    assert ok == ["update_element", "add_element"], ok
    assert len(bad_ops) == 2, bad_ops
    assert out["slides"][0]["elements"][0]["style"]["size"] == 44
    assert out["slides"][0]["elements"][-1]["content"]["shape"] == "ellipse"
    assert d["slides"][0]["elements"][0]["style"]["size"] != 44, "apply_ops must not mutate"

    order = [s["id"] for s in reversed(d["slides"])]
    out2, _, bad2 = apply_ops(d, [{"action": "reorder_slides", "order": order}])
    assert [s["id"] for s in out2["slides"]] == order and not bad2
    assert apply_ops(d, [{"action": "reorder_slides", "order": order[:2]}])[2], \
        "a partial reorder must be rejected"
    assert apply_ops(d, [{"action": "update_slide", "slide_id": d["slides"][1]["id"],
                          "changes": {"notes": "new"}}])[0]["slides"][1]["notes"] == "new"

    # the two data kinds reach real chart and table elements
    data = expand({"deck_title": "Data", "template": "corporate", "slides": [
        {"kind": "chart", "title": "Revenue", "chart": "bar",
         "categories": ["Q1", "Q2"], "series": [{"name": "Rev", "values": [3, 5]}],
         "notes": ""},
        {"kind": "table", "title": "Us and them",
         "rows": [["", "Us", "Them"], ["Latency", "412ms", "980ms"]], "notes": ""},
    ]})
    kinds = [e["type"] for sl in data["slides"] for e in sl["elements"]]
    assert "chart" in kinds and "table" in kinds, kinds
    ch = next(e for e in data["slides"][0]["elements"] if e["type"] == "chart")
    assert ch["content"]["series"][0]["values"] == [3.0, 5.0]
    assert has_visual(data["slides"][0]) and has_visual(data["slides"][1])

    # visual coverage: the application reads the elements, and repairs what it finds
    text_only = expand_slide({"kind": "bullets", "title": "Where the time goes",
                              "bullets": ["Ingest stalls", "Retries pile up",
                                          "Cache misses"]}, 2, 4)
    assert not has_visual(text_only), "a title and a paragraph is not a visual"
    assert add_visual(text_only) == "composition"
    assert has_visual(text_only), "the repair must actually satisfy the check"
    assert sum(e["type"] == "shape" for e in text_only["elements"]) == 3
    assert "Ingest stalls" in "\n".join(
        e["content"].get("text", "") for e in text_only["elements"]), "repair lost the words"

    numbers = expand_slide({"kind": "bullets", "title": "Latency by region",
                            "bullets": ["EU 412ms", "US 388ms", "APAC 910ms"]}, 2, 4)
    assert add_visual(numbers) == "chart", "three comparable numbers are a chart"
    chart = next(e for e in numbers["elements"] if e["type"] == "chart")
    assert chart["content"]["series"][0]["values"] == [412.0, 388.0, 910.0]
    assert chart["content"]["categories"] == ["EU", "US", "APAC"]
    words = _body(numbers)
    assert words["x"] + words["w"] <= chart["x"] + 1e-9, "the chart landed on the words"
    assert chart["x"] + chart["w"] <= W and chart["y"] + chart["h"] <= H, chart

    mixed = expand_slide({"kind": "bullets", "title": "Mixed",
                          "bullets": ["Latency 412ms", "Uptime 99.9%", "Cost 4 usd"]}, 2, 4)
    assert _figures(["Latency 412ms", "Uptime 99.9%", "Cost 4 usd"]) == [], \
        "three different units on one axis is a chart that lies"
    assert add_visual(mixed) == "composition"

    sides = expand_slide({"kind": "two_col", "title": "Before and after",
                          "left": "Serial fetch\nOne row", "right": "Batched\n500 rows"}, 2, 4)
    assert add_visual(sides) == "table" and has_visual(sides)

    # a slide that genuinely does not need one is not padded with a fake
    opener = expand_slide({"kind": "title", "title": "Pipeline Health"}, 1, 4)
    rep = ensure_visuals({"slides": [opener, expand_slide(
        {"kind": "bullets", "title": "T", "bullets": ["a", "b"]}, 2, 4)]})
    assert len(rep) == 1 and rep[0]["repaired"], rep
    # ...and one that has a photo already is left exactly as it was
    shot = expand_slide({"kind": "bullets", "title": "T", "bullets": ["a", "b"],
                         "image": {"url": "/media/x/y.img"}}, 2, 4)
    before = list(shot["elements"])
    assert ensure_visuals({"slides": [shot]})[0]["visual"] == "present"
    assert shot["elements"] == before, "a slide that already had one must not be touched"

    assert clean({"slides": [{"elements": []}]})["slides"][0]["id"]
    assert clean({"slides": [{"elements": []}]}, {"id": "keep"})["id"] == "keep"
    try:
        clean({"slides": []})
        raise AssertionError("an empty deck must be rejected")
    except ValueError:
        pass
    print("ok - expand idempotent, %d element types, hostile input coerced, "
          "ops applied and rejected cleanly" % len(TYPES))
    print("ok - visual coverage: chart and table kinds expand, text-only slides repaired "
          "to chart / table / composition, slides that need none are left alone")


if __name__ == "__main__":
    demo()
