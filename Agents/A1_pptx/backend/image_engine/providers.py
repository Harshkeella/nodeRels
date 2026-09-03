"""Stock image providers, normalised to ImageCandidate.

The contract is a function, not a base class:

    provider(query: str, per_page: int, orientation: str | None, key: str)
        -> list[ImageCandidate]

Adding a provider is: write that function, add one line to PROVIDERS, add its key to
config.KEYS. Nothing downstream sees a provider's own JSON shape, and a provider that
raises is logged and skipped -- search_all() never propagates a provider failure.

ponytail: sync urllib inside asyncio.to_thread rather than httpx. Real concurrency, and
this repo ships with one dependency. Ceiling: a thread per in-flight request, fine at
~15-40 searches per slide. Above ~100 concurrent, swap in httpx behind search_all().
"""
import asyncio
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from . import config
from .schemas import ImageCandidate

# Provider-side caps. Asking for more than these is an error on some, silently ignored
# on others, so clamp before we ask.
MAX_PER_PAGE = {"unsplash": 30, "pexels": 80, "pixabay": 200}

# orientation -> each provider's spelling of it
_ORIENT = {
    "unsplash": {"landscape": "landscape", "portrait": "portrait", "square": "squarish"},
    "pexels": {"landscape": "landscape", "portrait": "portrait", "square": "square"},
    "pixabay": {"landscape": "horizontal", "portrait": "vertical", "square": "all"},
}


def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "image_engine", **(headers or {})})
    with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as r:
        return json.load(r)


def _q(**params) -> str:
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})


def unsplash(query: str, per_page: int, orientation: str | None, key: str):
    url = "https://api.unsplash.com/search/photos?" + _q(
        query=query, per_page=per_page, content_filter="high",
        orientation=_ORIENT["unsplash"].get(orientation or ""))
    data = _get(url, {"Authorization": "Client-ID " + key,
                      "Accept-Version": "v1"})
    return [ImageCandidate(
        id=str(p["id"]), provider="unsplash",
        preview_url=p["urls"]["small"],
        # 'regular' is 1080px on the long side. A portrait crop out of that fills a
        # full-height panel with ~600px and looks soft on a projector. 'full' is a few MB.
        download_url=p["urls"].get("full") or p["urls"]["regular"],
        source_url=p.get("links", {}).get("html"),
        trigger_url=p.get("links", {}).get("download_location"),
        width=p.get("width"), height=p.get("height"),
        description=p.get("description"), alt_text=p.get("alt_description"),
        photographer=(p.get("user") or {}).get("name"),
        license="unsplash", query=query, provider_rank=i,
    ) for i, p in enumerate(data.get("results", []))]


def pexels(query: str, per_page: int, orientation: str | None, key: str):
    url = "https://api.pexels.com/v1/search?" + _q(
        query=query, per_page=per_page,
        orientation=_ORIENT["pexels"].get(orientation or ""))
    data = _get(url, {"Authorization": key})
    return [ImageCandidate(
        id=str(p["id"]), provider="pexels",
        preview_url=p["src"]["medium"],
        download_url=p["src"].get("large2x") or p["src"]["large"],
        source_url=p.get("url"),
        width=p.get("width"), height=p.get("height"),
        description=p.get("alt"), alt_text=p.get("alt"),
        photographer=p.get("photographer"),
        license="pexels", query=query, provider_rank=i,
    ) for i, p in enumerate(data.get("photos", []))]


def pixabay(query: str, per_page: int, orientation: str | None, key: str):
    url = "https://pixabay.com/api/?" + _q(
        key=key, q=query, per_page=max(3, per_page), image_type="photo",
        safesearch="true", order="popular",
        orientation=_ORIENT["pixabay"].get(orientation or "", "all"))
    data = _get(url)
    return [ImageCandidate(
        id=str(p["id"]), provider="pixabay",
        preview_url=p.get("webformatURL"),
        download_url=p.get("largeImageURL") or p.get("webformatURL"),
        source_url=p.get("pageURL"),
        width=p.get("imageWidth"), height=p.get("imageHeight"),
        description=p.get("tags"), alt_text=p.get("tags"),
        photographer=p.get("user"),
        license="pixabay", query=query, provider_rank=i,
    ) for i, p in enumerate(data.get("hits", []))]


def _commons_license(value: str) -> str:
    name = (value or "").lower()
    if "cc0" in name:
        return "cc0"
    if "public domain" in name or name == "pdm":
        return "pdm"
    if "cc by-sa" in name:
        return "cc-by-sa"
    if "cc by" in name:
        return "cc-by"
    return "unknown"


def wikimedia(query: str, per_page: int, orientation: str | None, key: str = ""):
    """Key-free, licensed internet image search via Wikimedia Commons."""
    url = "https://commons.wikimedia.org/w/api.php?" + _q(
        action="query", format="json", formatversion=2, generator="search",
        gsrnamespace=6, gsrsearch=query, gsrlimit=min(per_page, 30),
        prop="imageinfo", iiprop="url|size|extmetadata", iiurlwidth=1920,
        origin="*")
    data = _get(url)
    out = []
    for page in (data.get("query") or {}).get("pages", []):
        # Commons also returns PDF/DjVu title-page thumbnails. Those are documents,
        # not slide illustrations, even when their titles match every search term.
        if not re.search(r"\.(?:jpe?g|png|webp|svg)$", page.get("title", ""), re.I):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        val = lambda name: str((meta.get(name) or {}).get("value") or "")
        artist = html.unescape(re.sub(r"<[^>]+>", "", val("Artist"))).strip()
        desc = html.unescape(re.sub(r"<[^>]+>", "", val("ImageDescription"))).strip()
        download = info.get("thumburl") or info.get("url")
        if not download:
            continue
        out.append(ImageCandidate(
            id=str(page.get("pageid") or page.get("title")), provider="wikimedia",
            preview_url=download, download_url=download,
            source_url=info.get("descriptionurl"),
            width=info.get("thumbwidth") or info.get("width"),
            height=info.get("thumbheight") or info.get("height"),
            description=desc or page.get("title", "").removeprefix("File:"),
            alt_text=page.get("title", "").removeprefix("File:"),
            photographer=artist or "Wikimedia contributor",
            license=_commons_license(val("LicenseShortName") or val("UsageTerms")),
            query=query, provider_rank=len(out)))
    return out


PROVIDERS = {"unsplash": unsplash, "pexels": pexels, "pixabay": pixabay,
             "wikimedia": wikimedia}


def available() -> list[str]:
    """Providers whose key is actually in the environment."""
    keyed = [name for name, env in config.KEYS.items() if os.getenv(env) and name in PROVIDERS]
    return [p for p in config.PUBLIC_PROVIDERS if p in PROVIDERS] + keyed


async def _one(name: str, query: str, per_page: int, orientation: str | None, log: list):
    """One provider, one query. Never raises: a dead provider must not kill the slide."""
    try:
        n = min(per_page, MAX_PER_PAGE.get(name, per_page))
        env = config.KEYS.get(name)
        hits = await asyncio.to_thread(
            PROVIDERS[name], query, n, orientation, os.getenv(env, "") if env else "")
        log.append("[%s] %r -> %d" % (name, query, len(hits)))
        return hits
    except urllib.error.HTTPError as e:
        # 429 is a spent rate-limit window, 401 a bad key. Neither is worth a retry
        # inside one slide budget -- the other providers are already running.
        log.append("[%s] %r FAILED http %d" % (name, query, e.code))
    except Exception as e:
        log.append("[%s] %r FAILED %s: %s" % (name, query, type(e).__name__, e))
    return []


async def search_all(queries, providers, per_page=None, orientation=None, log=None):
    """Every query against every provider, concurrently. Returns a flat candidate list.

    len(queries) * len(providers) searches in flight at once -- 3 to 24 in practice.
    """
    log = log if log is not None else []
    per_page = per_page or config.PER_QUERY
    jobs = [_one(p, q, per_page, orientation, log) for q in queries for p in providers]
    if not jobs:
        return []
    results = await asyncio.gather(*jobs)
    return [c for batch in results for c in batch]
