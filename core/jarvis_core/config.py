from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def _hermes_key() -> str:
    return os.environ.get("HERMES_API_KEY") or _env_value("API_SERVER_KEY")


@dataclass
class Settings:
    host: str = os.environ.get("JARVIS_HOST", "127.0.0.1")
    port: int = int(os.environ.get("JARVIS_PORT", "8765"))
    token: str = os.environ.get("JARVIS_TOKEN", "")  # UI must present this; empty = dev mode (no auth)

    hermes_url: str = os.environ.get("HERMES_URL", "http://127.0.0.1:8642")
    hermes_key: str = field(default_factory=_hermes_key)
    hermes_model: str = os.environ.get("JARVIS_MODEL", "")        # empty = Hermes default
    hermes_provider: str = os.environ.get("JARVIS_PROVIDER", "")

    voice_url: str = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900")
    voice_key: str = os.environ.get("VOICESTUDIO_API_KEY", "local")
    voice: str = os.environ.get("JARVIS_VOICE", "JARVIS Butler")   # VoiceStudio profile name or id
    voice_model: str = os.environ.get("JARVIS_VOICE_MODEL", "omnivoice")
    voice_speed: float = float(os.environ.get("JARVIS_VOICE_SPEED", "1.0"))
    voice_seed: int = int(os.environ.get("JARVIS_VOICE_SEED", "7"))       # fixed seed = same voice every sentence
    voice_steps: int = int(os.environ.get("JARVIS_VOICE_STEPS", "8"))     # 8 ~= 2x faster than 16, same timbre

    timezone: str = os.environ.get("JARVIS_TZ", "America/New_York")
    user_name: str = os.environ.get("JARVIS_USER", "Stephen")
    wake_threshold: float = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    state_dir: Path = Path(os.environ.get("JARVIS_STATE_DIR", Path.home() / ".hermes/jarvis/state"))
    # Briefing (AI-Inbox style triage): direct Gemini JSON call, no agent loop -> fast + structured
    brief_model: str = os.environ.get("JARVIS_BRIEF_MODEL", "gemini-3.8-flash")
    brief_thinking: str = os.environ.get("JARVIS_BRIEF_THINKING", "low")  # low: ~4s vs ~30s, same to-dos
    brief_key: str = field(default_factory=lambda: _env_value("GEMINI_API_KEY"))
    brief_refresh_s: int = int(os.environ.get("JARVIS_BRIEF_REFRESH", "900"))


settings = Settings()
