"""Microphone pipeline: 16 kHz mono PCM16 frames in -> wake word / utterance capture -> text out.

Modes
  off      mic muted, frames ignored
  wake     openWakeWord 'hey_jarvis' listens; on fire -> capture
  capture  record until trailing silence (VAD) or max length -> STT -> on_utterance
  ptt      push-to-talk: record everything until ptt_end -> STT
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable, Coroutine

import numpy as np

log = logging.getLogger("jarvis.audio")
RATE = 16000
FRAME = 1280  # 80 ms, what openWakeWord expects


class WakeWord:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.model = None
        self.error: str | None = None
        try:
            from openwakeword.model import Model
            from openwakeword.utils import download_models
            try:
                self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
            except Exception:
                download_models(model_names=["hey_jarvis"])
                self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        except Exception as e:  # voice still works via push-to-talk
            self.error = f"{type(e).__name__}: {e}"
            log.warning("wake word unavailable: %s", self.error)

    @property
    def available(self) -> bool:
        return self.model is not None

    def score(self, frame: np.ndarray) -> float:
        if self.model is None:
            return 0.0
        pred = self.model.predict(frame)
        if isinstance(pred, tuple):  # some versions return (scores, details)
            pred = pred[0]
        return float(pred.get("hey_jarvis", 0.0))

    def reset(self) -> None:
        if self.model is not None:
            self.model.reset()


class EnergyVAD:
    """Adaptive-noise-floor energy VAD. Good enough for end-pointing a single speaker at a desk."""

    def __init__(self):
        self.floor = 300.0

    def rms(self, frame: np.ndarray) -> float:
        return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)) + 1e-6)

    def is_speech(self, frame: np.ndarray) -> bool:
        r = self.rms(frame)
        speech = r > max(self.floor * 3.0, 450.0)
        if not speech:  # track background noise slowly
            self.floor = 0.95 * self.floor + 0.05 * r
        return speech


class MicPipeline:
    def __init__(self, on_event: Callable[[dict], Awaitable[None]],
                 on_utterance: Callable[[bytes, str], Coroutine[Any, Any, None]], wake: WakeWord):
        self.on_event, self.on_utterance, self.wake = on_event, on_utterance, wake
        self.vad = EnergyVAD()
        self.mode = "off"
        self.buf = np.zeros(0, dtype=np.int16)
        self.pre = deque(maxlen=6)  # ~0.5s of audio before speech starts
        self.rec: list[np.ndarray] = []
        self.rec_started = 0.0
        self._rec_frames = 0
        self.heard_speech = False
        self.silence_frames = 0
        self.wake_enabled = True
        self.suspended = False  # while JARVIS is speaking without headphones
        self._level_t = 0.0
        self._cooldown_until = 0.0

    # ------------------------------------------------------------------ control
    async def set_listening(self, enabled: bool) -> None:
        self.wake_enabled = enabled
        if self.mode in ("wake", "off"):
            self.mode = "wake" if enabled and self.wake.available else "off"
        await self.on_event({"type": "mic", "mode": self.mode, "wake_available": self.wake.available,
                             "wake_error": self.wake.error})

    async def ptt_start(self) -> None:
        self._begin("ptt")
        await self.on_event({"type": "listening", "via": "ptt"})

    async def listen_now(self) -> None:
        """Hotkey / mic-button tap: capture one utterance, end-pointed by silence (like after a wake word)."""
        self._begin("capture")
        self.rec = []

    async def ptt_end(self) -> None:
        if self.mode == "ptt":
            await self._finish("ptt")

    async def cancel(self) -> None:
        self.rec = []
        self.mode = "wake" if self.wake_enabled and self.wake.available else "off"

    def _begin(self, mode: str) -> None:
        self.mode = mode
        self.rec = list(self.pre)
        self.rec_started = time.monotonic()
        self._rec_frames = 0
        self.heard_speech = mode == "ptt"
        self.silence_frames = 0

    # ------------------------------------------------------------------ audio
    async def feed(self, pcm: bytes) -> None:
        if self.mode == "off" and not self.wake_enabled:
            return
        chunk = np.frombuffer(pcm, dtype=np.int16)
        self.buf = np.concatenate([self.buf, chunk])
        while len(self.buf) >= FRAME:
            frame, self.buf = self.buf[:FRAME], self.buf[FRAME:]
            await self._frame(frame)

    async def _frame(self, frame: np.ndarray) -> None:
        now = time.monotonic()
        speech = self.vad.is_speech(frame)
        if now - self._level_t > 0.08:
            self._level_t = now
            await self.on_event({"type": "mic_level", "level": min(1.0, self.vad.rms(frame) / 6000.0)})

        if self.mode == "wake":
            self.pre.append(frame)
            if self.suspended or now < self._cooldown_until:
                self.wake.score(frame)  # keep model state warm
                return
            s = self.wake.score(frame)
            if s >= self.wake.threshold:
                self.wake.reset()
                self._cooldown_until = now + 1.5
                self._begin("capture")
                self.rec = []  # drop the wake phrase itself
                await self.on_event({"type": "wake", "score": round(s, 3)})
                await self.on_event({"type": "listening", "via": "wake"})
            return

        if self.mode in ("capture", "ptt"):
            self.rec.append(frame)
            dur = self._rec_frames * FRAME / RATE  # audio time, not wall time (robust to backlog bursts)
            self._rec_frames += 1
            if speech:
                self.heard_speech = True
                self.silence_frames = 0
            else:
                self.silence_frames += 1
            if self.mode == "capture":
                # 0.8s of trailing silence after speech ends the utterance; give up after 6s of nothing
                if (self.heard_speech and self.silence_frames >= 10) or (not self.heard_speech and dur > 6) or dur > 30:
                    await self._finish("wake")
            elif dur > 60:
                await self._finish("ptt")

    async def _finish(self, via: str) -> None:
        frames, heard = self.rec, self.heard_speech
        self.rec = []
        self.mode = "wake" if self.wake_enabled and self.wake.available else "off"
        self.wake.reset()
        self._cooldown_until = time.monotonic() + 1.0
        if not frames or not heard:
            await self.on_event({"type": "listening_end", "via": via, "empty": True})
            return
        pcm = np.concatenate(frames).tobytes()
        if len(pcm) < RATE * 2 * 0.3:
            await self.on_event({"type": "listening_end", "via": via, "empty": True})
            return
        await self.on_event({"type": "listening_end", "via": via, "empty": False,
                             "seconds": round(len(pcm) / (RATE * 2), 2)})
        asyncio.create_task(self.on_utterance(pcm, via))
