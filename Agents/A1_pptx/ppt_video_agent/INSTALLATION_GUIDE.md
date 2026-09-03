# Installation Guide — PPT Video Agent

This guide installs everything required to run the project on a Windows computer.

## 1. System requirements

Recommended:

- Windows 10 or Windows 11
- Microsoft PowerPoint installed
- Python 3.12
- Internet connection for initial downloads and Edge TTS
- Enough disk space for the Ollama model
- FFmpeg installed and available in PATH

A GPU is helpful for running a local LLM faster, but Ollama can also run supported models using available CPU/GPU resources.

---

## 2. Copy the project

Place the project somewhere simple, for example:

```text
C:\AIProjects\ppt_video_agent
```

Avoid unnecessarily deep folder paths.

Open Command Prompt in that folder.

One easy method:

1. Open the project folder in File Explorer.
2. Click the address bar.
3. Type `cmd`.
4. Press Enter.

You should see:

```text
C:\AIProjects\ppt_video_agent>
```

---

## 3. Check Python

Run:

```bash
python --version
```

Expected example:

```text
Python 3.12.x
```

If `python` is not recognized, try:

```bash
py --version
```

If Python is not installed, install Python 3.12 and make sure Python is added to PATH.

---

## 4. Install Python dependencies

From the main project folder run:

```bash
pip install -r requirements.txt
```

If `pip` is attached to a different Python installation, use:

```bash
python -m pip install -r requirements.txt
```

or:

```bash
py -m pip install -r requirements.txt
```

The current `requirements.txt` installs:

```text
python-pptx
pywin32
requests
edge-tts
```

---

## 5. Verify Microsoft PowerPoint

The current `render_slides.py` uses Microsoft PowerPoint through Windows COM automation.

Microsoft PowerPoint must therefore be installed.

Open PowerPoint manually once and make sure it works.

---

## 6. Install FFmpeg

### Recommended Windows method

Open Command Prompt and run:

```bash
winget install --id Gyan.FFmpeg
```

After installation:

1. Close Command Prompt.
2. Open a new Command Prompt.
3. Run:

```bash
ffmpeg -version
```

Also check:

```bash
ffprobe -version
```

If the version information appears, FFmpeg is ready.

You can also locate it with:

```bash
where ffmpeg
```

---

## 7. Install Ollama

Install Ollama for Windows.

After installation, open a new Command Prompt and run:

```bash
ollama --version
```

If Ollama responds with its version, continue.

---

## 8. Download the narration LLM

The current project configuration uses:

```text
qwen2.5:7b
```

Download it with:

```bash
ollama pull qwen2.5:7b
```

Test it:

```bash
ollama run qwen2.5:7b
```

Enter a short message and verify that the model responds.

Make sure the model name matches `generate_narration.py`:

```python
MODEL_NAME = "qwen2.5:7b"
```

---

## 9. Verify Ollama API

The project expects Ollama at:

```text
http://localhost:11434
```

`generate_narration.py` uses:

```text
http://localhost:11434/api/generate
```

Normally Ollama manages this automatically when the application/service is running.

---

## 10. Add a PowerPoint presentation

Place one PowerPoint file inside:

```text
input/
```

Example:

```text
input/demo.pptx
```

Do not place generated output files inside the input folder.

---

## 11. Run the complete pipeline

From:

```text
C:\AIProjects\ppt_video_agent>
```

run:

```bash
python run_pipeline.py
```

The pipeline should execute:

```text
cleanup
   ->
PowerPoint extraction
   ->
slide rendering
   ->
LLM narration generation
   ->
text-to-speech
   ->
slide clip generation
   ->
final video merge
```

---

## 12. Find the final video

After the process finishes, open:

```text
output/
```

The final file should be:

```text
output/final_presentation_video.mp4
```

---

# Testing individual stages

If the complete pipeline fails, run the scripts individually to find the problem.

## Test PPT extraction

```bash
python extract_ppt.py
```

Expected output:

```text
output/presentation_data.json
```

## Test slide rendering

```bash
python render_slides.py
```

Expected output:

```text
slides/slide_1.png
slides/slide_2.png
...
```

## Test narration

Make sure Ollama is running, then:

```bash
python generate_narration.py
```

Expected output:

```text
scripts/slide_1.txt
scripts/slide_2.txt
...
```

## Test speech generation

```bash
python generate_audio.py
```

Expected output:

```text
audio/slide_1.mp3
audio/slide_2.mp3
...
```

## Test slide videos

```bash
python create_clips.py
```

Expected output:

```text
clips/slide_1.mp4
clips/slide_2.mp4
...
```

## Test final merge

```bash
python merge_clips.py
```

Expected output:

```text
output/final_presentation_video.mp4
```

---

# Common errors

## `PackageNotFoundError: Package not found at 'input/presentation.pptx'`

Cause:

The PowerPoint file was not found at the expected location.

Fix:

Place a `.pptx` file inside:

```text
input/
```

If your script still contains a hard-coded filename, either rename the PowerPoint or update the path.

---

## `'ffmpeg' is not recognized`

Cause:

FFmpeg is not installed or is not in Windows PATH.

Fix:

Install FFmpeg and then open a new Command Prompt.

Check:

```bash
where ffmpeg
```

---

## Python `FileNotFoundError` when calling FFmpeg/FFprobe

Cause:

Python cannot locate the executable.

Fix:

Verify:

```bash
ffmpeg -version
ffprobe -version
where ffmpeg
where ffprobe
```

Restart Command Prompt after installing FFmpeg.

---

## Cannot connect to Ollama

Typical message:

```text
Could not connect to Ollama
```

Fix:

Make sure Ollama is running and test:

```bash
ollama run qwen2.5:7b
```

Also make sure the configured model is installed:

```bash
ollama list
```

---

## Model not found

Run:

```bash
ollama pull qwen2.5:7b
```

or change `MODEL_NAME` in `generate_narration.py` to an installed model.

---

## PowerPoint slide rendering fails

Check:

- Windows is being used.
- Microsoft PowerPoint is installed.
- PowerPoint can open the presentation manually.
- `pywin32` is installed.

Verify:

```bash
pip show pywin32
```

---

## Edge TTS fails

Check the internet connection.

The current Edge TTS stage is not fully offline.

---

# Moving the project to another computer

On the new computer:

```text
1. Install Python
2. Install Microsoft PowerPoint
3. Install FFmpeg
4. Install Ollama
5. Download qwen2.5:7b
6. Copy the project
7. Install requirements.txt
8. Place a PPT inside input/
9. Run python run_pipeline.py
```

The generated folders can be empty before transfer:

```text
slides/
scripts/
audio/
clips/
output/
```

The `input` folder should contain only the PowerPoint presentation you want to process.

---

# Important security note

Do not place API keys, passwords, credentials, or private secrets directly inside source files before sending the project to someone else.

If you later add external APIs, store secrets in environment variables or a `.env` file that is excluded from version control.
