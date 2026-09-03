# PPT Video Agent — Math Upgrade

This is a drop-in upgrade for the existing PPT Video Agent. It adds a deterministic
SymPy solver before the Ollama narration step, so the language model explains a
verified result instead of inventing the calculation.

## What this version supports

- Typed arithmetic: `Evaluate (12 + 8) / 4`
- One equation at a time: `Solve for x: 2x + 5 = 15`
- Polynomial factoring: `Factor x^2 - 5x + 6`
- Expansion: `Expand (x + 2)(x - 3)`
- Simplification: `Simplify (x^2 - 1)/(x - 1)`
- Derivatives: `Differentiate x^3 + 2x with respect to x`
- Indefinite integrals: `Integrate 2x with respect to x`

Each successful calculation is saved in:

```text
output/math_verification.json
```

## Install the upgrade on Windows

1. Open your existing `ppt_video_agent` folder.
2. Rename the old `generate_narration.py` to `generate_narration_backup.py`.
3. Copy these files into the main project folder:

```text
math_engine.py
generate_narration.py
test_math_engine.py
requirements.txt
```

4. Open Command Prompt in the project folder and install the updated dependencies:

```bash
python -m pip install -r requirements.txt
```

If `python` does not work, use:

```bash
py -m pip install -r requirements.txt
```

## Test the math engine first

Run:

```bash
python -m unittest -v test_math_engine.py
```

The test summary should end with:

```text
OK
```

## Run the complete PPT pipeline

Use the same command as before:

```bash
python run_pipeline.py
```

The existing pipeline can keep calling `generate_narration.py`; no extra pipeline
step is required.

## How to write problems in PowerPoint

For the most reliable result, place each problem on its own line and use a clear
command:

```text
Solve for x: 4x + 8 = 24
Factor x^2 - 9
Differentiate x^2 + 3x with respect to x
Integrate 6x with respect to x
```

Plain Unicode symbols such as `×`, `÷`, `−`, `π`, `²`, `³`, and `√` are normalized
before parsing.

## Important limits

- This phase reads math that appears in the extracted slide text.
- Handwriting, screenshots, graphs, geometry diagrams, and equations stored only as
  images are not understood yet.
- Microsoft Equation objects may not be included by `python-pptx` text extraction.
- Multiple simultaneous equations, definite integrals, limits, word problems, and
  advanced mathematical notation may be placed in the verification report as
  unparsed instead of being guessed.
- Always review generated teaching content before publishing it.

Those visual and advanced cases belong in the next multimodal math upgrade.

