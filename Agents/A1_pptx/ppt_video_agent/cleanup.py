import os
import glob

FOLDERS_TO_CLEAN = [
    "slides",
    "scripts",
    "audio",
    "clips"
]

FILE_PATTERNS = [
    "slide_*.*",
    "clips.txt"
]


def cleanup_folder(folder):

    if not os.path.exists(folder):
        os.makedirs(folder)
        return

    for pattern in FILE_PATTERNS:

        files = glob.glob(
            os.path.join(folder, pattern)
        )

        for file_path in files:

            try:
                os.remove(file_path)
                print(f"Deleted: {file_path}")

            except Exception as error:
                print(
                    f"Could not delete {file_path}: {error}"
                )


def cleanup():

    print("\nCleaning previous generated files...\n")

    for folder in FOLDERS_TO_CLEAN:
        cleanup_folder(folder)

    print("\nCleanup completed.")


if __name__ == "__main__":
    cleanup()