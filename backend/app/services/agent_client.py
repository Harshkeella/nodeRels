"""Allowlisted service registry; each call carries server-derived tenant identity."""
import json
import os
import time
from functools import lru_cache
from urllib.parse import urlparse

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from app.core import auth


def signing_secret():
    value = os.getenv("AGENT_SHARED_SECRET", "")
    if len(value) < 32:
        raise ValueError("Set AGENT_SHARED_SECRET on both services before generating presentations.")
    return value


@lru_cache
def registry():
    path = os.getenv("AGENT_REGISTRY")
    if path:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle)
    else:
        entries = {"deck": {"url": os.getenv("DECK_AGENT_URL", "http://127.0.0.1:8101"),
                            "audience": "deck-agent"}}
    for config in entries.values():
        url = urlparse(config["url"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            raise ValueError("Invalid agent endpoint")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "deck-agent"):
            raise ValueError("Remote agent endpoints require HTTPS")
    return entries


def endpoint(agent="deck"):
    return registry()[agent]["url"].rstrip("/")


def headers(agent="deck"):
    token = jwt.encode({"sub": auth.current_user().id, "aud": registry()[agent]["audience"],
                        "exp": int(time.time()) + 120}, signing_secret(), algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


async def call(tool: str, arguments: dict, agent="deck") -> dict:
    async with httpx.AsyncClient(headers=headers(agent), timeout=45) as http:
        async with streamable_http_client(endpoint(agent) + "/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, arguments)
    # Raise after closing SDK task groups so actionable tool errors stay ValueErrors.
    if result.isError:
        raise ValueError(" ".join(c.text for c in result.content if c.type == "text")[:300])
    return result.structuredContent or json.loads(next(c.text for c in result.content if c.type == "text"))
