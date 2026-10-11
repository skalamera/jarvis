"""Windows stand-in for VoiceStudio: the three endpoints JARVIS Core uses, on http://127.0.0.1:3900.

  GET  /health                -> 200
  GET  /v1/audio/voices       -> Kokoro preset list
  POST /v1/audio/speech       -> WAV (OpenAI-compatible body: model, voice, input, speed, language)
  POST /transcribe            -> {"text": ...}   (multipart field "audio", a WAV)

TTS: Kokoro (kokoro-onnx, same "bm_lewis" voice as the Mac default). STT: faster-whisper on CPU (int8).
Env: VOICE_STT_MODEL (default small.en), VOICE_PORT (3900), VOICE_API_KEY (default "local").
"""
from __future__ import annotations

import io
import logging
import os
import threading
import wave
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
KOKORO_MODEL = MODELS / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS / "voices-v1.0.bin"
STT_MODEL = os.environ.get("VOICE_STT_MODEL", "small.en")
API_KEY = os.environ.get("VOICE_API_KEY", "local")
DEFAULT_VOICE = "bm_lewis"

log = logging.getLogger("voice")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="JARVIS voice (Windows)")
_tts = None
_stt = None
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()


def auth(authorization: str | None = Header(default=None)) -> None:
    if API_KEY and authorization not in (f"Bearer {API_KEY}",):
        raise HTTPException(401, "bad key")


def tts_engine():
    global _tts
    if _tts is None:
        from kokoro_onnx import Kokoro
        _tts = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        log.info("Kokoro loaded (%d voices)", len(_tts.get_voices()))
    return _tts


def stt_engine():
    global _stt
    if _stt is None:
        from faster_whisper import WhisperModel
        _stt = WhisperModel(STT_MODEL, device="cpu", compute_type="int8", cpu_threads=max(4, (os.cpu_count() or 8) // 2))
        log.info("faster-whisper %s loaded", STT_MODEL)
    return _stt


def to_wav(samples: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> np.ndarray:
    """16-bit PCM WAV -> float32 mono at 16 kHz (what Whisper wants)."""
    with wave.open(io.BytesIO(data)) as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw != 2:
        raise HTTPException(400, "expected 16-bit PCM WAV")
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != 16000 and a.size:
        n = int(round(a.size * 16000 / sr))
        a = np.interp(np.linspace(0, a.size - 1, n), np.arange(a.size), a).astype(np.float32)
    return a


class SpeechReq(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str | None = "wav"
    speed: float | None = 1.0
    language: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "tts": "kokoro", "stt": f"faster-whisper:{STT_MODEL}"}


@app.get("/v1/audio/voices", dependencies=[Depends(auth)])
def voices():
    return {"voices": [{"voice_id": v, "name": v, "type": "preset"} for v in sorted(tts_engine().get_voices())]}


@app.post("/v1/audio/speech", dependencies=[Depends(auth)])
def speech(req: SpeechReq):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(400, "empty input")
    eng = tts_engine()
    voice = req.voice if req.voice in eng.get_voices() else DEFAULT_VOICE
    lang = (req.language or ("en-gb" if voice.startswith("b") else "en-us")).lower()
    speed = min(2.0, max(0.5, float(req.speed or 1.0)))
    with _tts_lock:
        samples, sr = eng.create(text, voice=voice, speed=speed, lang=lang)
    return Response(to_wav(samples, sr), media_type="audio/wav")


@app.post("/transcribe", dependencies=[Depends(auth)])
async def transcribe(audio: UploadFile = File(...), mode: str = Form("fast")):
    a = read_wav(await audio.read())
    if a.size < 1600:  # < 0.1 s
        return {"text": ""}
    beam = 1 if mode == "fast" else 5
    with _stt_lock:
        segs, _ = stt_engine().transcribe(a, language="en", beam_size=beam, vad_filter=True,
                                          condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segs).strip()
    return JSONResponse({"text": text})


@app.on_event("startup")
def warm() -> None:
    def _go():
        try:
            tts_engine().create("Ready.", voice=DEFAULT_VOICE, speed=1.0, lang="en-gb")
            stt_engine()
        except Exception:
            log.exception("warm-up failed")
    threading.Thread(target=_go, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("VOICE_PORT", "3900")), log_level="info")
