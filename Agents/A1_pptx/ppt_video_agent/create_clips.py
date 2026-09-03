import os
import glob
import subprocess


SLIDES_FOLDER = "slides"
AUDIO_FOLDER = "audio"
CLIPS_FOLDER = "clips"


def get_audio_duration(audio_file):
    """
    Gets MP3 duration in seconds using ffprobe.
    """

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        audio_file
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True
    )

    return float(result.stdout.strip())


def create_video_clip(slide_image, audio_file, output_file):

    duration = get_audio_duration(audio_file)

    print(f"Audio duration: {duration:.2f} seconds")

    command = [
        "ffmpeg",
        "-y",

        # Keep image on screen
        "-loop",
        "1",

        "-i",
        slide_image,

        "-i",
        audio_file,

        # Video settings
        "-c:v",
        "libx264",

        "-t",
        str(duration),

        "-r",
        "30",

        "-pix_fmt",
        "yuv420p",

        # Make sure final video is Full HD
        "-vf",
        (
            "scale=1920:1080:"
            "force_original_aspect_ratio=decrease,"
            "pad=1920:1080:"
            "(ow-iw)/2:"
            "(oh-ih)/2"
        ),

        # Audio settings
        "-c:a",
        "aac",

        "-b:a",
        "192k",

        "-shortest",

        output_file
    ]

    subprocess.run(
        command,
        check=True
    )


def get_slide_number(file_path):

    filename = os.path.basename(file_path)

    return int(
        filename
        .replace("slide_", "")
        .replace(".png", "")
    )


def generate_all_clips():

    os.makedirs(
        CLIPS_FOLDER,
        exist_ok=True
    )

    slide_files = glob.glob(
        os.path.join(
            SLIDES_FOLDER,
            "slide_*.png"
        )
    )

    if not slide_files:
        print("No slide images found.")
        return

    slide_files.sort(
        key=get_slide_number
    )

    print(
        f"Slides found: {len(slide_files)}"
    )

    for slide_file in slide_files:

        slide_number = get_slide_number(
            slide_file
        )

        audio_file = os.path.join(
            AUDIO_FOLDER,
            f"slide_{slide_number}.mp3"
        )

        output_file = os.path.join(
            CLIPS_FOLDER,
            f"slide_{slide_number}.mp4"
        )

        if not os.path.exists(audio_file):

            print(
                f"Audio missing for slide "
                f"{slide_number}. Skipping."
            )

            continue

        print(
            f"\nCreating video for "
            f"slide {slide_number}..."
        )

        try:

            create_video_clip(
                slide_file,
                audio_file,
                output_file
            )

            print(
                f"Created: {output_file}"
            )

        except subprocess.CalledProcessError as error:

            print(
                f"FFmpeg error on "
                f"slide {slide_number}: {error}"
            )

            break

    print(
        "\nAll available slide clips created."
    )


if __name__ == "__main__":

    generate_all_clips()