import os
import glob
import win32com.client


INPUT_FOLDER = "input"
OUTPUT_FOLDER = "slides"


def find_presentation():
    ppt_files = glob.glob(os.path.join(INPUT_FOLDER, "*.pptx"))

    if not ppt_files:
        raise FileNotFoundError(
            "No .pptx file found inside the input folder."
        )

    return os.path.abspath(ppt_files[0])


def render_slides(ppt_path, output_folder):

    output_folder = os.path.abspath(output_folder)

    os.makedirs(output_folder, exist_ok=True)

    print("Opening PowerPoint...")

    powerpoint = win32com.client.Dispatch("PowerPoint.Application")

    powerpoint.Visible = 1

    presentation = powerpoint.Presentations.Open(ppt_path)

    print("Rendering slides...")

    for i, slide in enumerate(presentation.Slides, start=1):

        output_path = os.path.join(
            output_folder,
            f"slide_{i}.png"
        )

        slide.Export(
            output_path,
            "PNG",
            1920,
            1080
        )

        print(f"Rendered slide {i}")

    presentation.Close()

    powerpoint.Quit()

    print("\nRendering completed.")
    print(f"Slides saved inside: {output_folder}")


if __name__ == "__main__":

    ppt_path = find_presentation()

    print(f"Presentation found: {ppt_path}")

    render_slides(
        ppt_path,
        OUTPUT_FOLDER
    )