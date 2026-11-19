"""Disk-cached, content-addressed previews for the original image files."""
from io import BytesIO
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock

from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from . import config

ORIGINAL_NAME = re.compile(r"[a-f0-9]{64}\.(?:jpg|jpeg|png|webp|gif|bmp|tiff?)")
CACHE_CONTROL = "private, max-age=31536000, immutable"
LONG_EDGE = 600
MAX_BYTES = 100 * 1024
_LOCKS = [Lock() for _ in range(32)]


class ImageFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if ORIGINAL_NAME.fullmatch(path) and response.status_code in (200, 206, 304):
            response.headers["Cache-Control"] = CACHE_CONTROL
        return response


def _encode(path: Path) -> bytes:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        image.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
        image = image.convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
    while True:
        for quality in (80, 70, 60, 50):
            output = BytesIO()
            image.save(output, "WEBP", quality=quality, method=4)
            if output.tell() <= MAX_BYTES:
                return output.getvalue()
        width, height = image.size
        image = image.resize((max(1, width * 4 // 5), max(1, height * 4 // 5)), Image.Resampling.LANCZOS)


def ensure(filename: str) -> Path:
    """Return a cached preview, recreating it only if no completed file exists."""
    if not ORIGINAL_NAME.fullmatch(filename):
        raise FileNotFoundError(filename)
    originals = config.images_dir().resolve()
    source = originals / filename
    if not source.is_file() or source.resolve().parent != originals:
        raise FileNotFoundError(filename)
    folder = config.data_dir() / "thumbnails" / "v1"
    target = folder / (source.stem + ".webp")
    with _LOCKS[int(source.stem[:2], 16) % len(_LOCKS)]:
        if target.is_file():
            return target
        data = _encode(source)
        folder.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                temp.write(data)
                temp.close()
                os.replace(temp_path, target)
            finally:
                temp.close()
                temp_path.unlink(missing_ok=True)
        return target


def warm(filename: str) -> None:
    """Don't fail an already-committed import if its preview cannot be cached."""
    try:
        ensure(filename)
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
