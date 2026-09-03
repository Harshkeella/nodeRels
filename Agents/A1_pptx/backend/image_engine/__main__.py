"""Run the pipeline on one slide from the command line.

    python -m backend.image_engine "AI-Powered Medical Diagnosis" \
        --topic "AI in Healthcare" \
        --content "AI helps doctors analyze medical images." --debug
"""
import argparse
import json

from .engine import select_for_slide_sync
from .schemas import ImageSlot, SlideRequest

a = argparse.ArgumentParser(description="find images for one slide")
a.add_argument("title")
a.add_argument("--topic", default="")
a.add_argument("--content", default="")
a.add_argument("--deck", default="cli", help="presentation id -- shared dedupe scope")
a.add_argument("--ratio", default="16:9")
a.add_argument("--role", default="hero")
a.add_argument("--slots", type=int, default=1)
a.add_argument("--debug", action="store_true", help="include every scored candidate")
n = a.parse_args()

out = select_for_slide_sync(SlideRequest(
    presentation_id=n.deck, presentation_topic=n.topic, slide_title=n.title,
    slide_content=n.content, debug=n.debug,
    image_slots=[ImageSlot(slot_id="image_%d" % (i + 1),
                           role=n.role if i == 0 else "supporting",
                           aspect_ratio=n.ratio) for i in range(n.slots)]))

for line in out.metadata.get("log", []):
    print(line)
print()
print(json.dumps(out.model_dump(exclude_none=True), indent=2))
