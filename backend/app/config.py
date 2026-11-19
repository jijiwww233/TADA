"""Paths and settings. DATA_DIR can be overridden (tests use a temp dir)."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_KEY_FROM_ENV = "AI_API_KEY" in os.environ
load_dotenv(PROJECT_ROOT / ".env")


def data_dir() -> Path:
    return Path(os.environ.get("WOMINMIN_DATA_DIR", PROJECT_ROOT / "data"))


def images_dir() -> Path:
    return data_dir() / "images"


def db_path() -> Path:
    return data_dir() / "app.db"


def sync_profile_dir(platform_dir: str) -> Path:
    """Browser profile (holds login cookies; never commit). Non-secret account.json lives here too."""
    return data_dir() / "sync_profiles" / platform_dir


def sync_staging_dir(platform: str) -> Path:
    """Reader downloads land here; store copies them into images/. Cleared after each run."""
    return data_dir() / "sync_staging" / platform


def sync_max_pages() -> int:
    """Bootstrap budget per click (pages of 10). Incremental is not limited by this."""
    return int(os.environ.get("SYNC_MAX_PAGES", "5"))


def auto_sync_allowed() -> bool:
    """Only the launcher-started formal instance may run automatic platform sync.

    The entry script sets WOMINMIN_ALLOW_AUTO_SYNC=1; review/demo instances and bare
    uvicorn runs do not, so they can only sync when the user clicks a manual action.
    """
    return os.environ.get("WOMINMIN_ALLOW_AUTO_SYNC", "").strip().lower() in ("1", "true", "yes")


def ai_settings() -> dict:
    """OpenAI-compatible endpoint from .env. The key never leaves this dict except
    in the Authorization header; do not log it."""
    return {
        "base_url": os.environ.get("AI_BASE_URL", "").rstrip("/"),
        "api_key": os.environ.get("AI_API_KEY", ""),
        "vision_model": os.environ.get("VISION_MODEL", ""),
        "fast_recommendation_needs": os.getenv("WOMINMIN_FAST_RECOMMENDATION_NEEDS", "0") == "1",
        "vision_max_side": int(os.environ.get("VISION_MAX_SIDE", "1280")),
        # thinking only adds billed output tokens for this task (A/B in docs/progress.md)
        "enable_thinking": os.environ.get("VISION_ENABLE_THINKING", "false").lower() in ("1", "true"),
        # 短标题用文字模型（scripts/backfill_titles.py、新的分析结果兜底）
        "title_model": os.environ.get("TITLE_MODEL", "").strip() or "qwen3.7-flash",
        # one combined_d_v1 vector per image (docs/embedding_core.md)
        "embedding_model": os.environ.get("EMBEDDING_MODEL", "").strip() or "qwen3.7-text-embedding-flash",
        "embedding_dimensions": os.environ.get("EMBEDDING_DIMENSIONS", "1024").strip(),
        # the adapter sends a partial batch once its oldest queued image waited this long
        "embedding_flush_seconds": float(os.environ.get("EMBEDDING_FLUSH_SECONDS", "60")),
    }


def analysis_concurrency() -> int:
    raw = os.getenv("ANALYSIS_CONCURRENCY", "").strip()
    try:
        return min(32, max(1, int(raw or "6")))
    except ValueError:
        return 6


def analysis_auto_start() -> bool:
    raw = os.getenv("ANALYSIS_AUTO_START", "").strip().lower()
    return local_library_enabled() if not raw else raw in ("1", "true", "yes")


def local_library_enabled() -> bool:
    return os.environ.get("WOMINMIN_DESKTOP_LIBRARY") == "1"
