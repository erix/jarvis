"""Local AI provider settings for JARVIS."""
import json
import os
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.getenv(
    "JARVIS_AI_SETTINGS_PATH",
    os.path.join(_ROOT, ".jarvis", "ai_settings.json"),
)


def load_ai_settings(path: str = SETTINGS_PATH) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_ai_settings(settings: dict[str, Any], path: str = SETTINGS_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    current = load_ai_settings(path)
    current.update({k: v for k, v in settings.items() if v is not None})
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2)
    os.replace(tmp_path, path)


def resolve_ai_provider() -> str:
    return (
        os.getenv("JARVIS_AI_PROVIDER")
        or os.getenv("AI_PROVIDER")
        or load_ai_settings().get("provider")
        or "openrouter"
    ).lower()


def resolve_ai_model(provider: str) -> str:
    settings = load_ai_settings()
    return (
        os.getenv("JARVIS_MODEL")
        or (os.getenv("CODEX_MODEL") if provider == "codex" else None)
        or settings.get("model")
        or ("gpt-5.5" if provider == "codex" else "anthropic/claude-sonnet-4-6")
    )
