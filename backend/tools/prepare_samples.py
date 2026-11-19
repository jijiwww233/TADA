"""Install the public showcase if a checkout has no local showcase."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    destination = ROOT / "frontend" / "public" / "lab-assets" / "show"
    if not (destination / "show.json").is_file():
        shutil.copytree(ROOT / "samples" / "show", destination, dirs_exist_ok=True)


if __name__ == "__main__":
    main()
