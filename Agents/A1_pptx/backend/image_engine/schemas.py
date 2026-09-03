"""Every model the engine passes around. One file -- they are read together.

The pipeline speaks only these types; nothing downstream of providers.py ever sees a
provider's own JSON shape.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

Role = Literal["hero", "supporting", "background", "object", "icon"]

# Drives query wording and provider routing (config.ROUTES).
ImageType = Literal[
    "literal_object", "person", "place", "product", "technology", "process",
    "abstract_concept", "conceptual_realistic", "background", "data_visualization",
    "historical", "futuristic",
]


class ImageSlot(BaseModel):
    """One hole in a slide template that needs a picture."""
    slot_id: str
    role: Role = "supporting"
    aspect_ratio: str = "16:9"          # "w:h"
    orientation: Optional[Literal["landscape", "portrait", "square"]] = None

    @field_validator("aspect_ratio")
    @classmethod
    def _parseable(cls, v: str) -> str:
        w, _, h = v.partition(":")
        if not (w.strip().isdigit() and h.strip().isdigit() and int(h)):
            raise ValueError("aspect_ratio must look like '16:9'")
        return v

    @property
    def target_ratio(self) -> float:
        w, _, h = self.aspect_ratio.partition(":")
        return int(w) / int(h)


class SlideRequest(BaseModel):
    """What the PPT planner hands us for one slide."""
    presentation_id: str = "default"
    presentation_topic: str = ""
    slide_number: int = 1
    slide_title: str
    slide_content: str = ""
    template_id: str = ""
    image_slots: list[ImageSlot] = Field(default_factory=list)
    debug: bool = False


class Query(BaseModel):
    query: str
    priority: int = 1
    intent: str = "primary_literal_visual"


class VisualPlan(BaseModel):
    """The Visual Understanding Agent's whole output -- intent, concepts and queries."""
    visual_intent: str
    image_type: ImageType = "conceptual_realistic"
    primary_concepts: list[str] = Field(default_factory=list)
    secondary_concepts: list[str] = Field(default_factory=list)
    visual_style: str = "professional realistic photography"
    avoid: list[str] = Field(default_factory=list)
    queries: list[Query] = Field(default_factory=list)
    source: Literal["llm", "fallback"] = "llm"

    def ranking_text(self) -> str:
        """The rich semantic target candidates are scored against -- never the bare query.
        Phase 2 embeds this string with CLIP/SigLIP; Phase 1 token-matches it."""
        return " ".join([self.visual_intent, self.visual_style,
                         *self.primary_concepts, *self.primary_concepts,   # weight x2
                         *self.secondary_concepts])


class ImageCandidate(BaseModel):
    """A provider hit, normalised. Scores are filled in as the pipeline proceeds."""
    id: str
    provider: str
    preview_url: str
    download_url: str
    source_url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    description: Optional[str] = None
    alt_text: Optional[str] = None
    photographer: Optional[str] = None
    license: str = "unknown"
    # Unsplash's API terms require pinging links.download_location on actual use.
    # Skipping it gets the app key revoked, so it rides along on the candidate.
    trigger_url: Optional[str] = None
    query: str = ""
    provider_rank: int = 0                 # position in that provider's result list
    scores: dict[str, float] = Field(default_factory=dict)
    rejected: Optional[str] = None         # why it was dropped, for debug mode

    def text(self) -> str:
        """Everything the provider said about this image, for semantic matching."""
        return " ".join(filter(None, [self.description, self.alt_text]))

    @property
    def ratio(self) -> Optional[float]:
        return self.width / self.height if self.width and self.height else None

    def attribution(self) -> str:
        """Credit line. Providers require it; some licences make it mandatory."""
        who = self.photographer or "unknown"
        if self.provider == "wikimedia":
            return f"Image by {who} on Wikimedia Commons ({self.license}) — {self.source_url}"
        return f"Photo by {who} on {self.provider.title()}"


class SelectedImage(BaseModel):
    slot_id: str
    local_path: str
    source: str
    source_url: Optional[str]
    photographer: Optional[str]
    attribution: str
    license: str
    search_query: str
    width: int
    height: int
    relevance_score: float
    quality_score: float
    aspect_ratio_score: float
    final_score: float


class SlideImages(BaseModel):
    slide_number: int
    selected_images: list[SelectedImage] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    candidates: Optional[list[ImageCandidate]] = None    # debug mode only
