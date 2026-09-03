"""One isolated renderer process per durable job."""
import json
import shutil
import sys
from pathlib import Path

from noderels_artifacts import Document
from . import grounded, ppt


def run(folder: Path):
    payload = json.loads((folder / "request.json").read_text(encoding="utf-8"))
    if payload.get("source_folder"):
        source = Path(payload["source_folder"])
        deck = json.loads((source / "deck.json").read_text(encoding="utf-8"))
        deck["id"] = folder.name
        for slide in deck["slides"]:
            for element in slide["elements"]:
                if element["type"] == "image":
                    old = Path(element["content"]["path"])
                    shutil.copyfile(old, folder / old.name)
                    element["content"]["path"] = str(folder / old.name)
    else:
        deck = grounded.build(Document.model_validate(payload["document"]), folder, folder.name, payload.get("request", ""))
    ppt.render(deck, str(folder / "deck.pptx"), strict=True)
    (folder / "deck.json").write_text(json.dumps(deck, ensure_ascii=False), encoding="utf-8")
    if payload["video"]:
        from ppt_video_agent.agent import build_video
        build_video(deck, folder / "video.mp4")
    result = {"title": deck["deck_title"], "slides": len(deck["slides"]),
              "files": ["deck.pptx"] + (["video.mp4"] if payload["video"] else [])}
    (folder / "result.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    try:
        run(folder)
    except Exception as exc:
        (folder / "error.json").write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
        raise
