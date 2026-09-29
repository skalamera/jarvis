"""VoiceStudio (local) speech: TTS via OpenAI-compatible /v1/audio/speech, STT via /transcribe."""
from __future__ import annotations

import base64
import io
import wave

import httpx


class Voice:
    ACKS = ("Right away, sir.", "One moment, sir.", "Very good, sir.", "Checking now, sir.", "Pulling that up.")
    # Fallback when the cloned profile is missing: OmniVoice voice design, still pinned by seed.
    DESIGN = "male, elderly, low pitch, british accent"

    def __init__(self, base_url: str, api_key: str, voice: str, model: str = "omnivoice", speed: float = 1.0,
                 seed: int = 7, steps: int = 8):
        self.base = base_url.rstrip("/")
        self.voice_name, self.model, self.speed, self.seed, self.steps = voice, model, speed, seed, steps
        self.voice = voice          # resolved to a profile id by resolve()
        self._profile_ok = False
        self._http = httpx.AsyncClient(base_url=self.base, headers={"Authorization": f"Bearer {api_key}"},
                                       timeout=httpx.Timeout(120.0, connect=3.0))
        self._cache: dict[str, bytes] = {}
        self._ack_i = 0

    async def resolve(self) -> None:
        """Map the configured profile name (e.g. 'JARVIS Butler') to its VoiceStudio id."""
        try:
            voices = (await self._http.get("/v1/audio/voices", timeout=10)).json().get("voices", [])
        except Exception:
            return
        for v in voices:
            if v.get("type") == "profile" and self.voice_name in (v.get("voice_id"), v.get("name")):
                self.voice, self._profile_ok = v["voice_id"], True
                return
        self.voice, self._profile_ok = "default", False

    async def warm(self) -> None:
        """Resolve the voice, then pre-synthesize acknowledgement lines so they play with zero latency."""
        await self.resolve()
        for line in self.ACKS:
            try:
                self._cache[line] = await self.tts(line)
            except Exception:
                return

    def next_ack(self) -> tuple[str, bytes] | None:
        ready = [a for a in self.ACKS if a in self._cache]
        if not ready:
            return None
        self._ack_i = (self._ack_i + 1) % len(ready)
        line = ready[self._ack_i]
        return line, self._cache[line]

    async def aclose(self) -> None:
        await self._http.aclose()

    async def health(self) -> bool:
        try:
            return (await self._http.get("/health", timeout=3)).status_code == 200
        except httpx.HTTPError:
            return False

    async def tts(self, text: str) -> bytes:
        body = {"model": self.model, "voice": self.voice, "input": text[:4000], "response_format": "wav",
                "speed": self.speed, "seed": self.seed, "num_step": self.steps}
        if not self._profile_ok:
            body["instruct"] = self.DESIGN
        r = await self._http.post("/v1/audio/speech", json=body)
        r.raise_for_status()
        return r.content

    async def stt(self, wav_bytes: bytes) -> str:
        r = await self._http.post("/transcribe", files={"audio": ("utterance.wav", wav_bytes, "audio/wav")},
                                  data={"mode": "fast"})
        r.raise_for_status()
        return (r.json().get("text") or "").strip()


def pcm16_to_wav(pcm: bytes, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()
