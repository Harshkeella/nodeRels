"""Deck JSON -> narrated MP4 with the currently spoken point highlighted."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path

VIDEO_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["slides"],
    "properties": {"slides": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["slide_number", "narrations"],
        "properties": {"slide_number": {"type": "integer"},
                       "narrations": {"type": "array", "items": {"type": "string"}}}}}},
}


def requirements() -> list[str]:
    """Missing runtime pieces, without importing optional video packages at app boot."""
    missing = []
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        if not _local_voice():
            missing.append("edge-tts or a local speech engine (Windows SAPI / espeak-ng)")
    if not _libreoffice():
        try:
            import win32com.client  # noqa: F401
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application\\CLSID"):
                pass
        except (ImportError, OSError):
            missing.append("LibreOffice (free slide renderer) or PowerPoint + pywin32")
    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        if not shutil.which("ffmpeg"):
            missing.append("imageio-ffmpeg")
    return missing


def _summary(element: dict) -> str:
    content = element.get("content") or {}
    if element.get("type") == "chart":
        series = "; ".join("%s: %s" % (s.get("name", "Values"), s.get("values", []))
                           for s in content.get("series") or [])
        return "Chart. Categories: %s. %s" % (content.get("categories") or [], series)
    if element.get("type") == "table":
        return "Table. " + "; ".join(" | ".join(map(str, row))
                                      for row in (content.get("rows") or []))
    if element.get("type") == "image":
        # Stock search queries and overview labels are not narration. Equations are.
        if Path(content.get("path") or content.get("url") or "").name.startswith("equation-"):
            return str(content.get("alt") or "")
    return ""


def speaking_points(deck: dict) -> list[dict]:
    """Ordered narration units with the exact slide box each unit should light."""
    points = []
    shown = [s for s in deck.get("slides", []) if not s.get("hidden")]
    for slide_number, slide in enumerate(shown, 1):
        slide_points = []
        elements = sorted((e for e in slide.get("elements", []) if not e.get("hidden")),
                          key=lambda e: (e.get("y", 0), e.get("x", 0)))
        for element in elements:
            text = str((element.get("content") or {}).get("text") or "").strip()
            if element.get("type") == "text" and text:
                lines = [line.strip() for line in text.splitlines() if line.strip()
                         and not re.fullmatch(r"\d+\s*/\s*\d+", line.strip())]
                if not (element.get("style", {}).get("bullets") or element.get("style", {}).get("numbered")):
                    lines = [" ".join(lines)] if lines else []
                if text.isdigit() and element.get("w", 0) < .6:
                    continue  # Number chips in Deck Studio's visual compositions.
                for line_index, line in enumerate(lines):
                    box = {k: float(element.get(k, 0)) for k in ("x", "y", "w", "h")}
                    if len(lines) > 1:
                        box["y"] += box["h"] * line_index / len(lines)
                        box["h"] /= len(lines)
                    slide_points.append({"element_id": element.get("id"), "text": line,
                                         "box": box})
            elif element.get("type") in ("chart", "table", "image"):
                text = _summary(element)
                if text:
                    slide_points.append({"element_id": element.get("id"), "text": text,
                                         "box": {k: float(element.get(k, 0))
                                                 for k in ("x", "y", "w", "h")}})
        for point in slide_points:
            points.append({**point, "slide_number": slide_number,
                           "notes": str(slide.get("notes") or "")[:500]})
    return points


def narrate(deck: dict, points: list[dict]) -> list[str]:
    """One model call for the whole video; exact-count validation keeps mapping honest."""
    from backend import ppt

    grouped = []
    for slide_number in sorted({p["slide_number"] for p in points}):
        group = [p for p in points if p["slide_number"] == slide_number]
        grouped.append({"slide_number": slide_number,
                        "points": [p["text"] for p in group], "notes": group[0]["notes"]})
    prompt = """Write a professional, natural voiceover for this presentation.

For each slide, return exactly one narration sentence for each point, in the same order.
Explain the meaning instead of merely reading the words. Use simple English, preserve all
numbers exactly, and never add facts that are not in the point or speaker notes. Keep each
sentence concise. Introduce the content directly, with varied transitions only where needed.
Never use 'let us focus on this point', 'let's focus on this point', or repetitive preambles.
Preserve equations and units. Do not invent explanations, examples, or conclusions.
Return only the required JSON.

SLIDES:
%s""" % json.dumps(grouped, ensure_ascii=False)
    try:
        raw = ppt._ask([{"role": "system", "content": ppt.SYSTEM},
                        {"role": "user", "content": prompt}],
                       schema=VIDEO_SCHEMA, name="video_script", effort="low")
        by_slide = {int(s["slide_number"]): s["narrations"] for s in raw["slides"]}
        result = []
        for group in grouped:
            lines = by_slide.get(group["slide_number"], [])
            if len(lines) != len(group["points"]) or any(not str(x).strip() for x in lines):
                raise ValueError("narration count did not match speaking points")
            for point, line in zip(group["points"], lines):
                numbers = lambda text: set(re.findall(r"\d+(?:\.\d+)?", text))
                if not numbers(point) <= numbers(str(line)) or not numbers(str(line)) <= numbers(point + group["notes"]):
                    raise ValueError("Narration changed the source numbers")
                if re.search(r"let(?: us|'s|’s) focus on this point", str(line), re.I):
                    raise ValueError("Repetitive narration preamble")
            result.extend(str(x).strip() for x in lines)
        return result
    except (Exception, SystemExit):
        logging.getLogger(__name__).warning("Narration model unavailable; using source text verbatim")
        return [p["text"] for p in points]


def _ffmpeg() -> str:
    configured = os.getenv("IMAGEIO_FFMPEG_EXE")
    if configured:
        return configured
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        found = shutil.which("ffmpeg")
        if found:
            return found
    raise RuntimeError("FFmpeg is unavailable. Install imageio-ffmpeg.")


def _libreoffice() -> str | None:
    candidates = [os.getenv("LIBREOFFICE_PATH"), shutil.which("libreoffice"), shutil.which("soffice")]
    if os.name == "nt":
        candidates += [str(Path(os.getenv(key, "C:/Program Files")) / "LibreOffice/program/soffice.exe")
                       for key in ("ProgramFiles", "ProgramFiles(x86)")]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)


def _render_slides(pptx_path: Path, output: Path) -> None:
    pptx_path, output = pptx_path.resolve(), output.resolve()
    if _libreoffice():
        import fitz
        # Separate profiles let simultaneous headless jobs run without a shared office lock.
        profile = (output / "office-profile").as_uri()
        subprocess.run([_libreoffice(), f"-env:UserInstallation={profile}", "--headless",
                        "--convert-to", "pdf", "--outdir", str(output), str(pptx_path)],
                       check=True, capture_output=True, timeout=180)
        with fitz.open(output / (pptx_path.stem + ".pdf")) as pdf:
            for i, page in enumerate(pdf, 1):
                page.get_pixmap(matrix=fitz.Matrix(1920 / page.rect.width, 1080 / page.rect.height)).save(output / f"slide_{i}.png")
        return
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise RuntimeError("Install LibreOffice to render slides, or PowerPoint with pywin32 on Windows.") from exc

    pythoncom.CoInitialize()
    app = None
    presentation = None
    try:
        app = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = app.Presentations.Open(str(pptx_path), ReadOnly=True,
                                               Untitled=False, WithWindow=False)
        for i, slide in enumerate(presentation.Slides, 1):
            slide.Export(str(output / f"slide_{i}.png"), "PNG", 1920, 1080)
    except Exception as exc:
        raise RuntimeError("PowerPoint could not render the slides. Close any PowerPoint dialogs, or install LibreOffice for unattended rendering.") from exc
    finally:
        if presentation is not None:
            presentation.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _local_voice() -> str | None:
    if os.name == "nt":
        try:
            import win32com.client  # noqa: F401
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "SAPI.SpVoice\\CLSID"):
                return "sapi"
        except (ImportError, OSError):
            pass
    return shutil.which("espeak-ng") or shutil.which("espeak")


def _speak_local(lines: list[str], folder: Path) -> list[Path]:
    engine = _local_voice()
    if not engine:
        raise RuntimeError("The online voice service is unavailable. Retry, or install a local voice (Windows SAPI / espeak-ng).")
    paths = [folder / f"audio_{i:03d}.wav" for i in range(1, len(lines) + 1)]
    if engine == "sapi":
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            for line, path in zip(lines, paths):
                stream = win32com.client.Dispatch("SAPI.SpFileStream")
                stream.Format.Type = 22  # 22 kHz, 16-bit, mono PCM.
                stream.Open(str(path.resolve()), 3)
                try:
                    speaker.AudioOutputStream = stream
                    speaker.Speak(line, 0)
                finally:
                    stream.Close()
        finally:
            pythoncom.CoUninitialize()
    else:
        for line, path in zip(lines, paths):
            subprocess.run([engine, "-w", str(path), "--stdin"], input=line, text=True,
                           capture_output=True, check=True, timeout=90)
    return paths


async def _speak_all(lines: list[str], folder: Path, voice: str) -> list[Path]:
    from .math_speech import math_to_speech

    spoken = [math_to_speech(line) for line in lines]
    if os.getenv("VIDEO_TTS_ENGINE", "edge").lower() == "local":
        return await asyncio.to_thread(_speak_local, spoken, folder)
    try:
        import edge_tts
        paths = []
        for i, line in enumerate(spoken, 1):
            path = folder / f"audio_{i:03d}.mp3"
            for attempt in range(3):
                try:
                    await asyncio.wait_for(edge_tts.Communicate(line, voice).save(str(path)), timeout=60)
                    if not path.is_file() or path.stat().st_size == 0:
                        raise RuntimeError("Voice service returned no audio")
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(attempt + 1)
            paths.append(path)
        return paths
    except Exception:
        logging.getLogger(__name__).warning("Online speech failed; recording the video with a local voice")
        return await asyncio.to_thread(_speak_local, spoken, folder)


def _highlight(box: dict, deck: dict) -> str:
    scale_x, scale_y = 1920 / float(deck.get("w", 13.333)), 1080 / float(deck.get("h", 7.5))
    x, y = max(0, min(1919, round(box["x"] * scale_x) - 10)), max(0, min(1079, round(box["y"] * scale_y) - 8))
    w = max(1, min(1920 - x, round(box["w"] * scale_x) + 20))
    h = max(1, min(1080 - y, round(box["h"] * scale_y) + 16))
    return (f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0xFF4A1C@0.14:t=fill,"
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0xFF6A3D@0.95:t=4")


def build_video(deck: dict, target: str | Path, progress=lambda **_: None,
                voice: str = "en-US-AriaNeural") -> dict:
    """Build one MP4. Progress is a tiny callback so FastAPI can expose job state."""
    missing = requirements()
    if missing:
        raise RuntimeError("Install video requirements: " + ", ".join(missing))
    from backend import ppt

    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    points = speaking_points(deck)
    if not points:
        raise RuntimeError("This presentation has no speakable content.")
    lines = narrate(deck, points)
    ffmpeg = _ffmpeg()

    with tempfile.TemporaryDirectory(prefix="deck-video-", dir=str(target.parent)) as tmp_name:
        tmp = Path(tmp_name)
        progress(step="render", percent=8, text="Rendering slides")
        pptx_path = tmp / "deck.pptx"
        # Speaking point indices refer only to visible slides.
        ppt.render({**deck, "slides": [s for s in deck["slides"] if not s.get("hidden")]}, str(pptx_path), strict=True)
        _render_slides(pptx_path, tmp)

        progress(step="voice", percent=20, text="Writing and recording narration")
        audio = asyncio.run(_speak_all(lines, tmp, voice))
        clips = []
        for i, (point, audio_path) in enumerate(zip(points, audio), 1):
            progress(step="highlight", percent=20 + round(i / len(points) * 68),
                     text=f"Explaining slide {point['slide_number']}")
            clip = tmp / f"clip_{i:03d}.mp4"
            vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2," + _highlight(point["box"], deck)
            cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(tmp / f"slide_{point['slide_number']}.png"),
                   "-i", str(audio_path), "-vf", vf, "-r", "30", "-c:v", "libx264",
                   "-preset", "fast", "-threads", "2", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-b:a", "192k", "-shortest", str(clip)]
            run = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if run.returncode:
                raise RuntimeError("FFmpeg could not create a clip: " + run.stderr[-500:])
            clips.append(clip)

        listing = tmp / "clips.txt"
        listing.write_text("".join("file '%s'\n" % p.name for p in clips),
                           encoding="utf-8")
        progress(step="finish", percent=92, text="Finishing the video")
        finished = tmp / "finished.mp4"
        run = subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "1", "-i", str(listing),
                              "-c", "copy", "-movflags", "+faststart", str(finished)],
                             capture_output=True, text=True, timeout=180)
        if run.returncode:
            raise RuntimeError("FFmpeg could not merge the video: " + run.stderr[-500:])
        os.replace(finished, target)

    progress(step="done", percent=100, text="Video ready")
    return {"path": str(target), "segments": len(points), "voice": voice}


def demo() -> None:
    deck = {"w": 13.333, "h": 7.5, "slides": [{"notes": "", "elements": [
        {"id": "t", "type": "text", "x": 1, "y": 1, "w": 8, "h": 2,
         "content": {"text": "First point\nSecond point"}, "style": {"bullets": True}},
        {"id": "n", "type": "text", "x": 11, "y": 7, "w": 1, "h": .3,
         "content": {"text": "1 / 1"}},
    ]}]}
    points = speaking_points(deck)
    assert [p["text"] for p in points] == ["First point", "Second point"]
    assert points[0]["box"]["h"] == 1 and points[1]["box"]["y"] == 2
    assert "drawbox=" in _highlight(points[0]["box"], deck)
    print("ok - narration points map to highlight boxes")


if __name__ == "__main__":
    demo()
