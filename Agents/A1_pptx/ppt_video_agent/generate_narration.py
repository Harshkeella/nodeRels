"""Generate slide narration with deterministic SymPy math verification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

from math_engine import analyze_math_text


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
DEFAULT_OLLAMA_URL = os.getenv(
    "OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate"
)


def flatten_content(value: Any, parent_key: str = "") -> list[str]:
    """Collect useful text from different presentation_data.json layouts."""

    ignored_keys = {"slide_number", "slide_index", "number", "index", "id"}
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (int, float)):
        return [] if parent_key.lower() in ignored_keys else [str(value)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(flatten_content(item, parent_key))
        return result
    if isinstance(value, dict):
        result = []
        for key, item in value.items():
            if str(key).lower() in ignored_keys:
                continue
            result.extend(flatten_content(item, str(key)))
        return result
    return []


def load_slides(input_path: Path) -> list[dict[str, Any]]:
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    if not input_path.exists():
        fallback_paths = (
            PROJECT_ROOT / "output" / "presentation_data.json",
            PROJECT_ROOT / "presentation_data.json",
        )
        input_path = next((path for path in fallback_paths if path.exists()), input_path)
    if not input_path.exists():
        raise FileNotFoundError(
            "Slide data was not found. Expected: "
            f"{PROJECT_ROOT / 'output' / 'presentation_data.json'}. "
            "Run extract_ppt.py first."
        )

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        slides = data
    elif isinstance(data, dict) and isinstance(data.get("slides"), list):
        slides = data["slides"]
    elif isinstance(data, dict):
        slides = [data]
    else:
        raise ValueError("presentation_data.json must contain a slide list or object.")

    normalized: list[dict[str, Any]] = []
    for index, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            slide = {"content": slide}
        normalized.append(
            {
                "slide_number": slide.get("slide_number", slide.get("number", index)),
                "raw": slide,
                "content": "\n".join(flatten_content(slide)),
            }
        )
    return normalized


def build_prompt(slide_number: int, content: str, math_report: dict[str, Any]) -> str:
    verified_math = math_report["solved"]
    skipped_math = math_report["skipped"]

    return f"""You are narrating slide {slide_number} for an educational video.

EXTRACTED SLIDE CONTENT:
{content or '[No extractable text was found.]'}

SYMPY MATH RESULTS (authoritative):
{json.dumps(verified_math, indent=2, ensure_ascii=False)}

UNPARSED MATH CANDIDATES:
{json.dumps(skipped_math, indent=2, ensure_ascii=False)}

Write a clear, natural voiceover script in simple English.

Rules:
1. Explain the slide in a logical teaching order; do not merely read bullet points.
2. Treat every verified SymPy result as authoritative. Never change its final answer.
3. For a verified problem, explain the main steps and finish with the exact verified result.
4. Keep each algebraic step equivalent to the previous step. Do not invent missing values.
5. Do not solve an item listed under UNPARSED MATH CANDIDATES. Say that its notation needs review if it is central to the slide.
6. Write every formula as speakable words: say "x squared", "divided by", "equals", and "theta". Never output LaTeX, dollar signs, braces, or backslash commands such as \\theta.
7. For indefinite integration, mention the constant of integration, C.
8. Do not mention prompts, JSON, SymPy, or internal verification tools in the narration.
9. Return only the narration script, with no heading or markdown.
10. Use professional, varied transitions. Never repeat 'let us focus on this point'
    or 'let's focus on this point'; begin directly with the content being explained.
"""


def request_narration(prompt: str, model: str, ollama_url: str) -> str:
    try:
        response = requests.post(
            ollama_url,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            },
            timeout=300,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not reach Ollama. Start Ollama and confirm the model is installed."
        ) from exc

    narration = response.json().get("response", "").strip()
    if not narration:
        raise RuntimeError("Ollama returned an empty narration.")
    return narration


def generate_all(
    input_path: Path,
    scripts_dir: Path,
    report_path: Path,
    model: str,
    ollama_url: str,
) -> None:
    if not scripts_dir.is_absolute():
        scripts_dir = PROJECT_ROOT / scripts_dir
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    slides = load_slides(input_path)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    complete_report: list[dict[str, Any]] = []
    for sequence, slide in enumerate(slides, start=1):
        content = slide["content"]
        math_report = analyze_math_text(content)
        prompt = build_prompt(sequence, content, math_report)
        narration = request_narration(prompt, model, ollama_url)

        script_path = scripts_dir / f"slide_{sequence}.txt"
        script_path.write_text(narration, encoding="utf-8")
        complete_report.append(
            {
                "slide_number": slide["slide_number"],
                "script_file": str(script_path),
                "math": math_report,
            }
        )
        print(
            f"Slide {sequence}: narration saved; "
            f"{len(math_report['solved'])} math problem(s) verified."
        )

    report_path.write_text(
        json.dumps(complete_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Math verification report saved to: {report_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate verified educational narration for extracted PPT slides."
    )
    parser.add_argument("--input", default="output/presentation_data.json")
    parser.add_argument("--scripts-dir", default="scripts")
    parser.add_argument("--report", default="output/math_verification.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_all(
        input_path=Path(args.input),
        scripts_dir=Path(args.scripts_dir),
        report_path=Path(args.report),
        model=args.model,
        ollama_url=args.ollama_url,
    )
