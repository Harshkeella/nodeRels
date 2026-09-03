"""HTTP surface. Two endpoints, both thin -- all behaviour lives in engine.py.

    uvicorn image_engine.api:app --reload

FastAPI is only imported here, so ppt.py can call engine.select_for_slide_sync() in
process without pulling a web framework into the deck renderer.
"""
from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import config, engine, providers
from .schemas import ImageCandidate, SlideImages, SlideRequest

app = FastAPI(title="PPT Image Engine", version="1.0")


class SearchRequest(BaseModel):
    slide: SlideRequest
    top_k: int = Field(default=20, ge=1, le=config.MAX_CANDIDATES)


class SearchResponse(BaseModel):
    slide_number: int
    images: list[ImageCandidate]
    metadata: dict


@app.get("/health")
def health() -> dict:
    """Which providers actually have keys, and the weights currently in force."""
    return {"providers": providers.available(),
            "weights": config.FINAL_WEIGHTS,
            "cache": config.CACHE}


@app.post("/images/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """Plan, search and rank. Downloads nothing -- for tuning and debugging queries."""
    log: list[str] = []
    brief, order, queries, cands, found = await engine.collect(req.slide, log)
    return SearchResponse(
        slide_number=req.slide.slide_number,
        images=cands[:req.top_k],
        metadata={"queries_generated": queries, "plan_source": brief.source,
                  "image_type": brief.image_type, "visual_intent": brief.visual_intent,
                  "providers_used": order, "candidates_found": found,
                  "candidates_ranked": len(cands), "log": log})


@app.post("/images/select", response_model=SlideImages)
async def select(slide: SlideRequest) -> SlideImages:
    """The whole pipeline: search, validate, rank, dedupe, download, return one per slot."""
    return await engine.select_for_slide(slide)
