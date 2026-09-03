"""Generate Edge TTS audio after converting math notation to spoken words."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import edge_tts

from math_speech import math_to_speech


PROJECT_ROOT = Path(__file__).resolve().parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
AUDIO_DIR = PROJECT_ROOT / "audio"
TTS_TEXT_DIR = PROJECT_ROOT / "output" / "tts_text"
VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AriaNeural")


def natural_slide_order(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)", path.stem)
    return (int(match.group(1)) if match else 10**9, path.name)


async def generate_one(script_path: Path) -> None:
    raw_narration = script_path.read_text(encoding="utf-8")
    spoken_narration = math_to_speech(raw_narration)
    if not spoken_narration:
        raise ValueError(f"No speakable narration found in {script_path.name}.")

    audio_path = AUDIO_DIR / f"{script_path.stem}.mp3"
    audit_path = TTS_TEXT_DIR / script_path.name
    audit_path.write_text(spoken_narration, encoding="utf-8")

    communicator = edge_tts.Communicate(spoken_narration, VOICE)
    await communicator.save(str(audio_path))
    print(f"Created {audio_path.name}")


async def generate_all() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    TTS_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    script_files = sorted(SCRIPTS_DIR.glob("slide_*.txt"), key=natural_slide_order)
    if not script_files:
        raise FileNotFoundError(
            f"No slide narration files were found in {SCRIPTS_DIR}."
        )

    for script_path in script_files:
        await generate_one(script_path)


if __name__ == "__main__":
    asyncio.run(generate_all())

