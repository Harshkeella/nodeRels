import os
import glob
import subprocess


CLIPS_FOLDER = "clips"
OUTPUT_FOLDER = "output"
FINAL_VIDEO = os.path.join(
    OUTPUT_FOLDER,
    "final_presentation_video.mp4"
)


def get_slide_number(file_path):

    filename = os.path.basename(file_path)

    return int(
        filename
        .replace("slide_", "")
        .replace(".mp4", "")
    )


def merge_clips():

    os.makedirs(
        OUTPUT_FOLDER,
        exist_ok=True
    )

    clips = glob.glob(
        os.path.join(
            CLIPS_FOLDER,
            "slide_*.mp4"
        )
    )

    if not clips:
        print("No slide clips found.")
        return

    clips.sort(
        key=get_slide_number
    )

    print(f"Clips found: {len(clips)}")

    # Create FFmpeg concat list
    concat_file = os.path.join(
        CLIPS_FOLDER,
        "clips.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as file:

        for clip in clips:

            absolute_path = os.path.abspath(
                clip
            )

            # FFmpeg concat format
            file.write(
                f"file '{absolute_path}'\n"
            )

    print("Merging clips...")

    command = [
        "ffmpeg",
        "-y",

        "-f",
        "concat",

        "-safe",
        "0",

        "-i",
        concat_file,

        "-c",
        "copy",

        FINAL_VIDEO
    ]

    subprocess.run(
        command,
        check=True
    )

    print("\nVideo created successfully!")
    print(f"Output: {FINAL_VIDEO}")


if __name__ == "__main__":

    merge_clips()