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
    # Per-request thinking level for JARVIS turns only (Hermes default is medium). "low" measured ~40% faster
    # to first spoken word on tool questions; "" = Hermes default, "none" = no thinking.
    hermes_reasoning: str = os.environ.get("JARVIS_REASONING", "low")

    voice_url: str = os.environ.get("VOICESTUDIO_URL", "http://127.0.0.1:3900")
    voice_key: str = os.environ.get("VOICESTUDIO_API_KEY", "local")
    # Default: Kokoro preset "bm_lewis" (~1 s per sentence). For the cloned OmniVoice profile instead:
    # JARVIS_VOICE_MODEL=omnivoice JARVIS_VOICE="JARVIS Butler" (about real-time synthesis, so slower to start)
    voice: str = os.environ.get("JARVIS_VOICE", "bm_lewis")        # Kokoro preset, or VoiceStudio profile name/id
    voice_model: str = os.environ.get("JARVIS_VOICE_MODEL", "mlx-audio")
    voice_language: str = os.environ.get("JARVIS_VOICE_LANGUAGE", "en-gb")  # Kokoro accent (British English)
    voice_speed: float = float(os.environ.get("JARVIS_VOICE_SPEED", "1.0"))
    voice_seed: int = int(os.environ.get("JARVIS_VOICE_SEED", "7"))       # OmniVoice: fixed seed = same voice
    voice_steps: int = int(os.environ.get("JARVIS_VOICE_STEPS", "8"))     # OmniVoice: 8 ~= 2x faster than 16

    timezone: str = os.environ.get("JARVIS_TZ", "America/New_York")
    user_name: str = os.environ.get("JARVIS_USER", "Stephen")
    wake_threshold: float = float(os.environ.get("JARVIS_WAKE_THRESHOLD", "0.5"))
    follow_up_s: float = float(os.environ.get("JARVIS_FOLLOW_UP_S", "4.0"))
    state_dir: Path = Path(os.environ.get("JARVIS_STATE_DIR", Path.home() / ".hermes/jarvis/state"))
    # Briefing (AI-Inbox style triage): direct Gemini JSON call, no agent loop -> fast + structured
    brief_model: str = os.environ.get("JARVIS_BRIEF_MODEL", "gemini-3.8-flash")
    brief_thinking: str = os.environ.get("JARVIS_BRIEF_THINKING", "low")  # low: ~4s vs ~30s, same to-dos
    brief_key: str = field(default_factory=lambda: _env_value("GEMINI_API_KEY"))
    brief_refresh_s: int = int(os.environ.get("JARVIS_BRIEF_REFRESH", "900"))


settings = Settings()
