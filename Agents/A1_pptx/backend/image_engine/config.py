"""Every tunable in one place. Nothing below is hardcoded anywhere else in the engine."""
import os

def _f(name, default):
    return float(os.getenv(name, default))

def _i(name, default):
    return int(os.getenv(name, default))

# ---------- providers ----------
KEYS = {                                    # provider -> env var holding its key
    "unsplash": "UNSPLASH_ACCESS_KEY",
    "pexels": "PEXELS_API_KEY",
    "pixabay": "PIXABAY_API_KEY",
}
PUBLIC_PROVIDERS = ("wikimedia",)            # Commons needs no account or API key
PER_QUERY = _i("IMG_PER_QUERY", 8)          # candidates one provider returns for one query
MAX_CANDIDATES = _i("IMG_MAX_CANDIDATES", 100)
HTTP_TIMEOUT = _f("IMG_HTTP_TIMEOUT", 12)
SLIDE_BUDGET = _f("IMG_SLIDE_BUDGET", 45)   # hard ceiling for one slide, seconds

# Which providers to try, per image_type the planner returns. First listed is queried first;
# all are queried concurrently, the order only breaks ties in provider_rank.
# ponytail: a dict, not a QueryRouter class. It is a lookup with a default.
ROUTES = {
    "literal_object": ["pixabay", "pexels", "unsplash"],
    "product":        ["pixabay", "unsplash", "pexels"],
    "person":         ["pexels", "unsplash", "pixabay"],
    "place":          ["unsplash", "pexels", "pixabay"],
    "historical":     ["unsplash", "pixabay", "pexels"],
    "data_visualization": ["pixabay", "unsplash", "pexels"],
    "background":     ["unsplash", "pexels", "pixabay"],
}
DEFAULT_ROUTE = ["unsplash", "pexels", "pixabay"]

# ---------- licensing ----------
# Anything not on this list never reaches the ranker. Decks get shipped to customers;
# a non-commercial or share-alike image is a legal problem, not a ranking problem.
ALLOWED_LICENSES = set(os.getenv(
    "IMG_ALLOWED_LICENSES", "unsplash,pexels,pixabay,cc0,pdm,cc-by,cc-by-sa").split(","))
# Older .env files predate the key-free Commons provider. These four are its commercial,
# attribution-safe licences; keeping them here makes the new fallback work on upgrade.
ALLOWED_LICENSES.update({"cc0", "pdm", "cc-by", "cc-by-sa"})

# ---------- validation ----------
MIN_BYTES = _i("IMG_MIN_BYTES", 8_000)      # below this it is a placeholder or an error page
MAX_BYTES = _i("IMG_MAX_BYTES", 25_000_000)
FORMATS = ("JPEG", "PNG", "WEBP")
# Minimum pixels per slot role. A full-bleed background is 13.33in wide, a grid tile is not.
MIN_PX = {"background": (1600, 900), "hero": (1200, 700), "supporting": (800, 600),
          "object": (600, 600), "icon": (256, 256)}
DEFAULT_MIN_PX = (800, 600)

# ---------- scoring weights ----------
FINAL_WEIGHTS = {
    "semantic": _f("W_SEMANTIC", 0.50),
    "quality": _f("W_QUALITY", 0.22),
    "aspect": _f("W_ASPECT", 0.18),
    "diversity": _f("W_DIVERSITY", 0.10),
}
QUALITY_WEIGHTS = {
    "resolution": _f("WQ_RESOLUTION", 0.40),
    "sharpness": _f("WQ_SHARPNESS", 0.25),
    "exposure": _f("WQ_EXPOSURE", 0.20),
    "colour": _f("WQ_COLOUR", 0.15),
    # aesthetic: 0.0 until a real model exists. Raising it above 0 with no model
    # would be a constant added to every candidate -- it would change nothing.
    "aesthetic": _f("WQ_AESTHETIC", 0.0),
}
ASPECT_GAMMA = _f("IMG_ASPECT_GAMMA", 1.5)  # >1 sharpens the crop-loss penalty
DUP_HAMMING = _i("IMG_DUP_HAMMING", 8)      # dHash distance <= this == same picture (0-64)

# ---------- cache ----------
CACHE = os.getenv("IMG_CACHE") or os.path.join(os.getenv("AGENT_STORAGE_DIR") or os.path.dirname(os.path.abspath(__file__)), "cache")

# ---------- planner ----------
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PLAN_TIMEOUT = _f("IMG_PLAN_TIMEOUT", 40)
