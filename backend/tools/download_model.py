"""Download the CPU Chinese-CLIP files and verify the installed snapshot."""
import hashlib
import os
import shutil
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit
from urllib.request import urlopen

from app import config

MODEL = "OFA-Sys/chinese-clip-vit-base-patch16"
FILES = {
    "config.json": 3008,
    "preprocessor_config.json": 342,
    "vocab.txt": 109540,
    "pytorch_model.bin": 753177983,
}
WEIGHT = "pytorch_model.bin"
WEIGHT_SHA256 = "7b7b583c210c867410bc6bdb8a55fe14eec62999e0a9ea31ff222dc501f9cfbe"


def valid_file(path: Path, name: str) -> bool:
    if not path.is_file() or path.stat().st_size != FILES[name]:
        return False
    if name == WEIGHT:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == WEIGHT_SHA256
    return True


def install_file(endpoint: str, directory: Path, name: str) -> None:
    target = directory / name
    if valid_file(target, name):
        print(f"已校验 {name}（{FILES[name]:,} 字节）", flush=True)
        return
    url = f"{endpoint}/{MODEL}/resolve/main/{name}"
    print(f"正在下载 {name}（{FILES[name]:,} 字节）", flush=True)
    with NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with urlopen(url, timeout=90) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
        if not valid_file(temporary, name):
            raise ValueError(f"{name} 的大小或校验指纹与预期不符")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    endpoint = (os.environ.get("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("HF_ENDPOINT 应填写无账号凭据的 HTTPS 镜像地址")
    directory = Path(os.environ.get("CHINESE_CLIP_MODEL_DIR") or config.data_dir() / "models" / "chinese-clip-base")
    directory.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        install_file(endpoint, directory, name)
    print(f"模型校验通过，目录为 {directory.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"模型安装失败：{exc}") from None
