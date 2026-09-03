"""Loads .env before any submodule reads os.getenv.

ppt.py and image_engine/config.py freeze their settings into module constants at
import time, so the file has to be in os.environ before those imports run. Python
imports this package first for every entry point -- uvicorn backend.main:app,
python -m backend.ppt, python -m backend.image_engine -- so this is the one place
that covers all of them, and it replaces the `set -a; . ./.env` ritual entirely.

Real environment variables win: KEY=x uvicorn ... still overrides the file.
"""
import os

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _parse(text):
    """.env text -> dict. Tolerates what hand-edited .env files actually contain:
    CRLF, `export ` prefixes, spaces around `=`, quotes, and trailing ` # comments`."""
    out = {}
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        val = val.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if len(val) > 1 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def load(path=_ENV):
    if not os.path.isfile(path):
        return {}
    vals = _parse(open(path, encoding="utf-8-sig").read())
    for k, v in vals.items():
        if v and k not in os.environ:       # a blank line in .env must not mask a real key
            os.environ[k] = v
    return vals


load()
