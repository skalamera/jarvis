"""VoiceStudio (local) speech: TTS via OpenAI-compatible /v1/audio/speech, STT via /transcribe.

Two kinds of voice:
  * an engine preset, e.g. Kokoro (model "mlx-audio", voice "bm_lewis"): ~1 s per sentence, the default
  * a VoiceStudio profile on OmniVoice (model "omnivoice", voice = profile name, e.g. "JARVIS Butler"):
    cloned timbre, but synthesis runs at about real time
"""
from __future__ import annotations

import base64
import io
import wave

import httpx
import numpy as np

from .spoken import spoken

LEAD_KEEP_S = 0.03   # engines pad every clip with ~0.2 s of silence on each end; keep just a breath
TRAIL_KEEP_S = 0.06


def trim_silence(wav_bytes: bytes, threshold: int = 500) -> bytes:
    """Cut leading/trailing silence from a 16-bit PCM WAV. Returns the input unchanged if it isn't one."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            if w.getsampwidth() != 2:
                return wav_bytes
            sr, ch = w.getframerate(), w.getnchannels()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, ch)
        level = np.abs(a.astype(np.int32)).max(axis=1)
        loud = np.flatnonzero(level > threshold)
        if loud.size == 0:
            return wav_bytes
        start = max(0, int(loud[0]) - int(sr * LEAD_KEEP_S))
        end = min(level.size, int(loud[-1]) + 1 + int(sr * TRAIL_KEEP_S))
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(a[start:end].tobytes())
        return out.getvalue()
    except Exception:
        return wav_bytes


class Voice:
    ACKS = ("Right away, sir.", "One moment, sir.", "Very good, sir.", "As you wish, sir.", "Checking now, sir.",
            "Very well, sir.")
    # Fallback when an OmniVoice profile is missing: voice design, still pinned by seed.
    DESIGN = "male, elderly, low pitch, british accent"

    def __init__(self, base_url: str, api_key: str, voice: str, model: str = "mlx-audio", speed: float = 1.0,
                 seed: int = 7, steps: int = 8, language: str = "en-gb"):
        self.base = base_url.rstrip("/")
        self.voice_name, self.model, self.speed, self.seed, self.steps = voice, model, speed, seed, steps
        self.language = language
        self.voice = voice          # OmniVoice: resolved to a profile id by resolve()
        self._profile_ok = False
        self._http = httpx.AsyncClient(base_url=self.base, headers={"Authorization": f"Bearer {api_key}"},
                                       timeout=httpx.Timeout(120.0, connect=3.0))
        self._cache: dict[str, bytes] = {}
        self._ack_i = 0

    @property
    def is_preset(self) -> bool:
        """An engine preset voice (Kokoro etc.) rather than a VoiceStudio profile."""
        return self.model != "omnivoice"

    async def resolve(self) -> None:
        """OmniVoice: map the profile name (e.g. 'JARVIS Butler') to its id. Presets need no lookup."""
        if self.is_preset:
            self.voice, self._profile_ok = self.voice_name, True
            return
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
        """Resolve the voice (loading the engine), then pre-synthesize acknowledgements for zero-latency acks."""
        await self.resolve()
        for line in self.ACKS:
            try:
                self._cache[line] = await self.tts(line)
            except Exception:
                return

    async def keep_warm(self) -> None:
        """Tiny synthesis so VoiceStudio has this engine loaded before the next real sentence."""
        try:
            await self.tts("Ready.")
        except Exception:
            pass

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

    def request_body(self, text: str) -> dict:
        body = {"model": self.model, "voice": self.voice, "input": spoken(text)[:4000], "response_format": "wav",
                "speed": self.speed}
        if self.is_preset:
            if self.language:
                body["language"] = self.language
        else:
            body.update({"seed": self.seed, "num_step": self.steps})
            if not self._profile_ok:
                body["instruct"] = self.DESIGN
        return body

    async def tts(self, text: str) -> bytes:
        r = await self._http.post("/v1/audio/speech", json=self.request_body(text))
        r.raise_for_status()
        return trim_silence(r.content)

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
