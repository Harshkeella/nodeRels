import subprocess
import sys


STEPS = [
    ("Cleaning previous files", "cleanup.py"),
    ("Extracting PowerPoint content", "extract_ppt.py"),
    ("Rendering slides", "render_slides.py"),
    ("Generating narration", "generate_narration.py"),
    ("Generating audio", "generate_audio (1).py"),
    ("Creating slide videos", "create_clips.py"),
    ("Merging final video", "merge_clips.py"),
]


def run_step(name, script):

    print("\n" + "=" * 60)
    print(name)
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, script]
    )

    if result.returncode != 0:
        print(f"\nERROR: {name} failed.")
        print(f"Pipeline stopped at: {script}")
        sys.exit(1)

    print(f"\n{name} completed successfully.")


def main():

    print("\n")
    print("======================================")
    print(" PPT TO EXPLANATION VIDEO PIPELINE")
    print("======================================")

    for name, script in STEPS:
        run_step(name, script)

    print("\n")
    print("======================================")
    print(" ALL STEPS COMPLETED")
    print("======================================")

    print(
        "\nFinal video:"
        "\noutput/final_presentation_video.mp4"
    )


if __name__ == "__main__":
    main()
