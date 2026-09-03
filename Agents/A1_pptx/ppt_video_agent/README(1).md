# PPT Video Agent

PPT Video Agent converts a PowerPoint presentation into a narrated explainer video.

It keeps the original PowerPoint slide visuals and does **not** generate replacement slide images. Each slide is rendered from the uploaded `.pptx`, a local LLM creates a slide-by-slide explanation, text-to-speech converts the explanation to audio, and FFmpeg combines the original slide image with its narration.

## What the project does

Pipeline:

```text
PowerPoint (.pptx)
      |
      +--> Extract slide text/tables
      |       |
      |       +--> presentation_data.json
      |
      +--> Render original slides as PNG
              |
              v
        Local LLM (Ollama)
              |
              v
       Narration scripts
              |
              v
          Edge TTS
              |
              v
          MP3 audio
              |
              v
           FFmpeg
              |
              v
     Slide-wise MP4 clips
              |
              v
       Final merged video
```

## Main scripts

- `extract_ppt.py`  
  Extracts slide titles, text, and tables from the PowerPoint file.

- `render_slides.py`  
  Uses Microsoft PowerPoint through `pywin32` to export each slide as a PNG image.

- `generate_narration.py`  
  Sends each slide's extracted content to a local Ollama model and creates a natural narration script.

- `generate_audio.py`  
  Converts each narration script to speech using Edge TTS.

- `create_clips.py`  
  Combines each slide image with its corresponding narration audio using FFmpeg.

- `merge_clips.py`  
  Merges all slide clips into one final MP4.

- `cleanup.py`  
  Removes generated files from previous runs while leaving the original input PowerPoint untouched.

- `run_pipeline.py`  
  Runs the complete pipeline in the correct order.

## Required folders

```text
ppt_video_agent/
|
|-- input/
|-- output/
|-- slides/
|-- scripts/
|-- audio/
|-- clips/
|
|-- extract_ppt.py
|-- render_slides.py
|-- generate_narration.py
|-- generate_audio.py
|-- create_clips.py
|-- merge_clips.py
|-- cleanup.py
|-- run_pipeline.py
|-- requirements.txt
|-- README.md
`-- INSTALLATION_GUIDE.md
```

## Input

Place one `.pptx` presentation inside:

```text
input/
```

Example:

```text
input/my_presentation.pptx
```

The scripts are designed to automatically detect a `.pptx` file in the input folder.

## Output

After a successful run, the final video is created at:

```text
output/final_presentation_video.mp4
```

Generated intermediate files are stored in:

```text
slides/   -> rendered slide images
scripts/  -> slide narration text
audio/    -> slide narration MP3 files
clips/    -> slide-wise MP4 files
```

## Run the complete project

Open Command Prompt inside the project folder and run:

```bash
python run_pipeline.py
```

If `python` does not work on Windows, try:

```bash
py run_pipeline.py
```

## Current default AI components

The current implementation uses:

- **Ollama** for running the LLM locally
- **Qwen 2.5 7B** as the narration model
- **Edge TTS** for speech generation
- **FFmpeg** for video creation

If your `generate_narration.py` uses a different model name, install that model instead.

## Default Ollama model

The project was developed with:

```bash
ollama pull qwen2.5:7b
```

The model should match this line in `generate_narration.py`:

```python
MODEL_NAME = "qwen2.5:7b"
```

## Default TTS voice

The current example configuration uses:

```python
VOICE = "en-US-AriaNeural"
```

You can change the voice inside `generate_audio.py`.

## Important limitations

### Windows dependency

The current slide renderer uses Microsoft PowerPoint automation through `pywin32`.

Therefore the current version requires:

- Windows
- Microsoft PowerPoint installed

It is not currently cross-platform.

### Internet connection

The Ollama LLM runs locally after the model has been downloaded.

However, the current Edge TTS implementation normally requires an internet connection to generate speech.

### PowerPoint rendering

The project renders the original slides through Microsoft PowerPoint. This preserves the PowerPoint design instead of recreating the slides using AI.

### Visual understanding

The current narration model primarily receives extracted slide text and tables.

Slides that are mostly diagrams, pictures, or complex charts may require a future multimodal/vision upgrade for deeper explanation.

## Sharing the project

When sending the project to another computer, do not assume that the receiver already has the system dependencies.

Send:

```text
project source code
requirements.txt
README.md
INSTALLATION_GUIDE.md
```

The receiver must separately install:

- Python
- Microsoft PowerPoint
- FFmpeg
- Ollama
- the required Ollama model

You normally do not need to include the Ollama model files inside the project ZIP.

## Recommended next improvements

Possible future upgrades include:

- GUI/web upload interface
- selectable voice
- selectable language
- narration length settings
- subtitles
- slide transitions
- speaker-note extraction
- visual/chart understanding
- offline TTS
- nodeRels Knowledge Brain integration
- Dockerization of the portable components
- API deployment
