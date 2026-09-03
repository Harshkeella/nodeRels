"""Authenticated artifact status, bounded file streaming, and scoped editor access."""
import json
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Literal

from app.core import auth
from app.services import agent_client, artifacts

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifacts"])


def ticket_secret():
    configured = os.getenv("ARTIFACT_SIGNING_SECRET") or os.getenv("AGENT_SHARED_SECRET")
    if configured:
        if len(configured) < 32:
            raise ValueError("Artifact signing secret must be at least 32 characters")
        return configured
    path = artifacts.store().root / ".signing-key"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text(encoding="utf-8")


def ticket(job_id, permission="view"):
    return jwt.encode({"sub": auth.current_user().id, "artifact": job_id, "permission": permission,
                       "aud": "artifact-access", "exp": int(time.time()) + 3600}, ticket_secret(), algorithm="HS256")


def authorize(request: Request, job_id: str, edit=False):
    raw = request.query_params.get("ticket") or request.headers.get("Authorization", "").removeprefix("Bearer ")
    try:
        claims = jwt.decode(raw, ticket_secret(), algorithms=["HS256"], audience="artifact-access",
                            options={"require": ["exp", "sub", "artifact", "permission"]})
        if claims["artifact"] != job_id or edit and claims["permission"] != "edit":
            raise ValueError()
        auth.run_as(auth.User(str(claims["sub"])))
        return artifacts.store().get(auth.current_user().id, job_id)
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Preview access expired. Reopen the artifact from chat.")


@router.get("/{job_id}", dependencies=[Depends(auth.get_current_user)])
async def get_status(job_id: str):
    try:
        return await artifacts.status(job_id)
    except (KeyError, ValueError):
        raise HTTPException(404, "Artifact not found")
    except Exception:
        raise HTTPException(503, "The rendering service is temporarily unavailable. Retry shortly.")


class AccessRequest(BaseModel):
    mode: Literal["preview", "edit"] = "preview"


@router.post("/{job_id}/access", dependencies=[Depends(auth.get_current_user)])
async def access(job_id: str, payload: AccessRequest):
    try:
        job = artifacts.store().get(auth.current_user().id, job_id)
    except (ValueError, KeyError):
        raise HTTPException(404, "Artifact not found")
    studio_url = os.getenv("DECK_STUDIO_URL", "http://localhost:5173")
    parsed = urlparse(studio_url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")):
        raise HTTPException(503, "Configure a valid HTTPS Deck Studio URL")
    if payload.mode == "edit" and job["payload"]["format"] == "pdf":
        raise HTTPException(400, "PDFs cannot be edited in Deck Studio")
    return {"ticket": ticket(job_id, "edit" if payload.mode == "edit" else "view"), "studio_url": studio_url}


@router.get("/{job_id}/file/{name}")
async def download(request: Request, job_id: str, name: str):
    job = authorize(request, job_id)
    if name not in ("document.pdf", "deck.pptx", "video.mp4") and not re.fullmatch(r"(?:equation-\d+|image-(?:cover|\d+))\.png", name):
        raise HTTPException(404, "File not found")
    if job["state"] != "done":
        raise HTTPException(409, "The artifact is not ready")
    if name == "document.pdf" and job["payload"]["format"] == "pdf":
        path = artifacts.store().folder(job_id) / name
        if not path.exists():
            raise HTTPException(404, "File expired")
        return FileResponse(path, media_type="application/pdf", filename=name,
                            content_disposition_type="attachment" if request.query_params.get("download") else "inline", headers={"Cache-Control": "private, no-store"})
    remote_id = (job.get("result") or {}).get("remote_id")
    if not remote_id:
        raise HTTPException(404, "File not found")
    headers = agent_client.headers()
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=60)
    try:
        response = await client.send(client.build_request("GET", agent_client.endpoint() + f"/files/{remote_id}/{name}", headers=headers), stream=True)
    except Exception:
        await client.aclose()
        raise HTTPException(503, "The rendering service is unavailable")
    if response.status_code not in (200, 206):
        await response.aclose()
        await client.aclose()
        raise HTTPException(response.status_code if response.status_code in (404, 416) else 503, "File unavailable")

    async def chunks():
        try:
            async for chunk in response.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    forwarded = {k: v for k, v in response.headers.items() if k in ("content-type", "content-length", "content-range", "accept-ranges")}
    forwarded["Cache-Control"] = "private, no-store"
    disposition = "attachment" if request.query_params.get("download") else "inline"
    forwarded["Content-Disposition"] = f'{disposition}; filename="{name}"'
    return StreamingResponse(chunks(), status_code=response.status_code, headers=forwarded)


@router.api_route("/{job_id}/studio/{path:path}", methods=["GET", "PUT"])
async def studio(request: Request, job_id: str, path: str):
    job = authorize(request, job_id, edit=request.method == "PUT")
    remote = (job.get("result") or {}).get("remote_id")
    if not remote:
        raise HTTPException(409, "Presentation is not ready")
    try:
        if path == "api/meta" and request.method == "GET":
            return await agent_client.call("studio_meta", {})
        if path == "api/decks" and request.method == "GET":
            result = [await agent_client.call("studio_document", {"job_id": remote})]
        elif path == "api/deck/" + remote:
            if request.method == "PUT":
                raw = bytearray()
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 500_000:
                        raise HTTPException(413, "Deck is too large")
                    raw.extend(chunk)
                document = json.loads(raw)["deck"]
                result = await agent_client.call("save_studio_document", {"job_id": remote, "document": document})
            else:
                result = await agent_client.call("studio_document", {"job_id": remote})
        else:
            raise HTTPException(404, "This action is available in standalone Deck Studio")
        base = str(request.base_url).rstrip("/")
        for deck in result if isinstance(result, list) else [result]:
            for slide in deck["slides"]:
                for e in slide["elements"]:
                    if e["type"] == "image":
                        name = Path(e["content"].get("url", "")).name
                        e["content"]["url"] = f"{base}/api/v1/artifacts/{job_id}/file/{name}?ticket={ticket(job_id)}"
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Deck Studio is unavailable. Reopen the editor from chat.")
