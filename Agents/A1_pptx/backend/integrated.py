"""Private multi-user MCP service. Run separately from the legacy local studio API."""
import asyncio
import contextlib
import copy
import json
import os
import re
import signal
import subprocess
import sys
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlparse

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from noderels_artifacts import Document
from noderels_artifacts.jobs import Jobs, work
from . import deck as D, ppt

load_dotenv()
ROOT = Path(os.getenv("AGENT_STORAGE_DIR", "./agent-storage")).resolve()
queue = Jobs(ROOT)
owner_context = ContextVar("agent_owner")
mcp = FastMCP("Deck Studio", stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(
                  allowed_hosts=os.getenv("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,deck-agent:8101").split(",")))


def secret():
    value = os.getenv("AGENT_SHARED_SECRET", "")
    if len(value) < 32:
        raise RuntimeError("Set AGENT_SHARED_SECRET to the same random 32+ character secret on both servers.")
    return value


class Authenticate:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/health":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        try:
            token = headers.get(b"authorization", b"").decode().removeprefix("Bearer ")
            claims = jwt.decode(token, secret(), algorithms=["HS256"], audience="deck-agent",
                                options={"require": ["sub", "exp", "aud"]})
            owner = str(claims["sub"])
        except (jwt.PyJWTError, ValueError):
            return await JSONResponse({"detail": "Not authenticated"}, status_code=401)(scope, receive, send)
        binding = owner_context.set(owner)
        try:
            await self.app(scope, receive, send)
        finally:
            owner_context.reset(binding)


def owned(job_id):
    return queue.get(owner_context.get(), job_id)


def public(job):
    return {k: job[k] for k in ("id", "state", "result", "error")}


@mcp.tool()
def submit_artifact(job_id: str, document: dict | None = None, video: bool = False, source_id: str | None = None, request: str = "") -> dict:
    """Queue rendering of approved content; never writes or rewrites knowledge."""
    if source_id:
        source = owned(source_id)
        if source["state"] != "done":
            raise ValueError("Source presentation is not ready")
    elif document:
        document = Document.model_validate(document).model_dump()
    else:
        raise ValueError("Provide approved content or an existing presentation")
    if len(request) > 8000:
        raise ValueError("Presentation request is too long")
    return public(queue.submit(owner_context.get(), {"document": document, "video": video, "source_id": source_id, "request": request}, job_id=job_id))


@mcp.tool()
def artifact_status(job_id: str) -> dict:
    """Read the caller's render job."""
    return public(owned(job_id))


@mcp.tool()
def studio_document(job_id: str) -> dict:
    """Open a completed deck in the canvas or preview."""
    job = owned(job_id)
    if job["state"] != "done":
        raise ValueError("Presentation is not ready")
    deck = json.loads((queue.folder(job_id) / "deck.json").read_text(encoding="utf-8"))
    for slide in deck["slides"]:
        for element in slide["elements"]:
            if element["type"] == "image":
                content = element["content"]
                content["url"] = Path(content.get("path", "")).name
                content.pop("path", None)
    return deck


@mcp.tool()
def studio_meta() -> dict:
    """Existing Deck Studio editor vocabulary."""
    return {"templates": ppt.TEMPLATES, "key": False, "providers": [], "integrated": True,
            "video": {"available": False, "missing": ["Generate videos from nodeRels chat"]},
            "doc": {"w": D.W, "h": D.H, "types": list(D.TYPES), "shapes": list(D.SHAPES),
                    "charts": list(D.CHARTS), "tokens": list(D.TOKENS), "ops": list(D.OPS)}}


@mcp.tool()
def save_studio_document(job_id: str, document: dict) -> dict:
    """Save a user edit, checking revision and keeping assets inside this job."""
    owned(job_id)
    folder = queue.folder(job_id)
    # One transaction serializes saves across service processes, preventing lost edits.
    with queue.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = json.loads((folder / "deck.json").read_text(encoding="utf-8"))
        if document.get("revision") != existing.get("revision", 0):
            raise ValueError("This deck changed in another tab. Reload before editing.")
        if len(json.dumps(document)) > 500_000:
            raise ValueError("Deck is too large")
        safe = D.clean(copy.deepcopy(document), existing)
        assets = {e["id"]: e["content"].get("path") for s in existing["slides"] for e in s["elements"] if e["type"] == "image"}
        owned_assets = {Path(p).name: p for p in assets.values() if p}
        incoming_elements = {e.get("id"): e for s in document["slides"] for e in s.get("elements", [])}
        for slide in safe["slides"]:
            for e in slide["elements"]:
                if e["type"] == "image":
                    incoming = incoming_elements.get(e["id"], {})
                    url = str(incoming.get("content", {}).get("url") or "")
                    path = assets.get(e["id"]) or owned_assets.get(Path(urlparse(url).path).name)
                    if not path:
                        raise ValueError("Use existing images in this connected editor")
                    e["content"]["path"] = path
                    e["content"]["url"] = Path(path).name
        safe["revision"] = existing.get("revision", 0) + 1
        safe["grounded"] = False  # User edits are no longer the approved source snapshot.
        ppt.render(safe, str(folder / "deck-next.pptx"), strict=True)
        (folder / "deck-next.json").write_text(json.dumps(safe), encoding="utf-8")
        os.replace(folder / "deck-next.pptx", folder / "deck.pptx")
        os.replace(folder / "deck-next.json", folder / "deck.json")
    return studio_document(job_id)


async def execute(job):
    folder = queue.folder(job["id"])
    folder.mkdir(exist_ok=True)
    payload = copy.deepcopy(job["payload"])
    if payload.get("source_id"):
        source = queue.get(job["owner"], payload["source_id"])
        if source["state"] != "done":
            raise ValueError("Source presentation is not ready")
        payload["source_folder"] = str(queue.folder(source["id"]))
    (folder / "request.json").write_text(json.dumps(payload), encoding="utf-8")
    # A process boundary gives expensive native renderers a real cancellation/timeout boundary.
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "backend.render_job", str(folder),
                                                  stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                                                  **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}))
    try:
        _, error = await asyncio.wait_for(process.communicate(), timeout=3500)
        if process.returncode:
            if (folder / "error.json").is_file():
                raise ValueError(json.loads((folder / "error.json").read_text(encoding="utf-8"))["error"])
            raise ValueError("Renderer failed: " + error.decode(errors="replace")[-220:])
    except asyncio.TimeoutError:
        raise ValueError("Rendering exceeded the time limit. Try fewer slides or a shorter video.")
    finally:
        if process.returncode is None:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                await killer.wait()
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
    return json.loads((folder / "result.json").read_text(encoding="utf-8"))


@contextlib.asynccontextmanager
async def lifespan(app):
    secret()
    queue.prune(int(os.getenv("ARTIFACT_RETENTION_DAYS", "30")))
    workers = [asyncio.create_task(work(queue, execute)) for _ in range(max(1, min(8, int(os.getenv("AGENT_WORKERS", "1")))))]
    async with mcp.session_manager.run():
        yield
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


app = FastAPI(title="Deck Studio MCP", lifespan=lifespan)
app.add_middleware(Authenticate)


@app.get("/health")
def health():
    return {"status": "ok", "service": "deck-studio-mcp"}


@app.get("/files/{job_id}/{name}")
def file(job_id: str, name: str):
    try:
        job = owned(job_id)
        if job["state"] != "done" or name not in ("deck.pptx", "video.mp4") and not re.fullmatch(r"(?:equation-\d+|image-(?:cover|\d+))\.png", name):
            raise KeyError()
        path = queue.folder(job_id) / name
        if not path.is_file():
            raise KeyError()
        return FileResponse(path, filename=name, headers={"Cache-Control": "private, no-store"})
    except (KeyError, ValueError):
        raise HTTPException(404, "Artifact not found")


app.mount("/", mcp.streamable_http_app())
