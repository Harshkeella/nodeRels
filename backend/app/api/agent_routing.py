"""Side-effect-free adapters for Plano's HTTP agent routing protocol.

Routing has no credentials, knowledge access, or generation permissions. The normal
authenticated chat handler checks the chosen capability before submitting any work.
"""
import json
import time
from typing import Literal
from fastapi import APIRouter

router = APIRouter(prefix="/internal/agents", tags=["agent-routing"])


@router.post("/{capability}/v1/chat/completions")
async def route(capability: Literal["pdf", "pptx", "video", "chat"]):
    return {"id": "route", "object": "chat.completion", "created": int(time.time()),
            "model": "noderels-capability", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps({"format": capability})}}]}
