"""JARVIS Core: FastAPI + WebSocket bridge between the HUD and Hermes.

WS /ws  (text frames = JSON, binary frames = 16 kHz mono PCM16 mic audio)
GET /health, GET /status
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from jarvis_google import store
from jarvis_google import tools as gtools
from jarvis_google.accounts import linked_accounts

from .audio import MicPipeline, WakeWord
from .briefing import briefing
from .config import settings
from .hermes_client import HermesClient
from .session import Session
from .voice import Voice, pcm16_to_wav

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis.core")

STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["hermes"] = HermesClient(settings.hermes_url, settings.hermes_key, settings.hermes_model,
                                   settings.hermes_provider)
    STATE["voice"] = Voice(settings.voice_url, settings.voice_key, settings.voice, settings.voice_model,
                           settings.voice_speed, settings.voice_seed, settings.voice_steps)
    await STATE["voice"].resolve()  # before accepting clients, so the very first sentence uses the right voice
    STATE["wake"] = await asyncio.to_thread(WakeWord, settings.wake_threshold)
    asyncio.create_task(STATE["voice"].warm())
    briefing.start()
    log.info("JARVIS core up. hermes=%s voice=%s wake=%s", settings.hermes_url, settings.voice_url,
             STATE["wake"].available)
    yield
    await STATE["hermes"].aclose()
    await STATE["voice"].aclose()


app = FastAPI(title="JARVIS Core", lifespan=lifespan)


def _authorized(request: Request) -> bool:
    if not settings.token:
        return True
    got = request.headers.get("x-jarvis-token") or request.query_params.get("token", "")
    return secrets.compare_digest(got, settings.token)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status(request: Request):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse({
        "hermes": await STATE["hermes"].health(), "voice": await STATE["voice"].health(),
        "wake_word": STATE["wake"].available, "wake_error": STATE["wake"].error,
        "pending_actions": len(store.pending_actions()), "voice_name": settings.voice,
        "voice_id": STATE["voice"].voice, "voice_profile_ok": STATE["voice"]._profile_ok,
    })


_TELEMETRY: dict = {"at": 0.0, "data": None}


@app.get("/telemetry")
async def telemetry(request: Request):
    """Inbox unread + upcoming events per account for the HUD header. Cached 60s; no LLM involved."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if _TELEMETRY["data"] and time.time() - _TELEMETRY["at"] < 60 and not request.query_params.get("fresh"):
        return _TELEMETRY["data"]
    now = dt.datetime.now(ZoneInfo(settings.timezone))

    def gather():
        out = []
        for a in linked_accounts():
            try:
                out.append(gtools.telemetry(a["account"], now.isoformat(), (now + dt.timedelta(hours=36)).isoformat()))
            except Exception as e:
                out.append({"account": a["account"], "email": a["email"], "error": str(e)[:200]})
        return out

    data = {"now": now.isoformat(), "accounts": await asyncio.to_thread(gather)}
    _TELEMETRY.update(at=time.time(), data=data)
    return data


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if settings.token and not secrets.compare_digest(ws.query_params.get("token", ""), settings.token):
        await ws.close(code=4401)
        return
    await ws.accept()
    lock = asyncio.Lock()

    async def send(msg: dict) -> None:
        async with lock:
            await ws.send_text(json.dumps(msg, default=str))

    session = Session(send, STATE["hermes"], STATE["voice"])

    async def on_utterance(pcm: bytes, via: str) -> None:
        await session.state("thinking")
        try:
            text = await session.voice.stt(pcm16_to_wav(pcm))
        except Exception as e:
            await send({"type": "voice_error", "error": f"transcription failed: {e}"})
            await session.state("idle")
            return
        await send({"type": "transcript", "text": text, "via": via})
        if text and text.strip(" .").lower() not in ("", "you", "thank you", "thanks for watching"):
            await session.user_input(text, source="voice")
        else:
            await session.state("idle")

    async def on_mic_event(ev: dict) -> None:
        if ev["type"] == "listening":
            await session.cancel_turn()  # barge-in: stop speaking / thinking immediately
            await session.state("listening")
        elif ev["type"] == "listening_end" and ev.get("empty"):
            await session.state("idle")
        await send(ev)

    mic = MicPipeline(on_mic_event, on_utterance, STATE["wake"])
    await session.hello()
    briefing.listeners.add(send)
    await send(briefing.snapshot())
    await briefing.ensure()
    await mic.set_listening(True)
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await mic.feed(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            t = data.get("type")
            if t == "ptt_start":
                await on_mic_event({"type": "listening", "via": "ptt"})
                await mic.ptt_start()
            elif t == "listen_now":
                await on_mic_event({"type": "listening", "via": "hotkey"})
                await mic.listen_now()
            elif t == "ptt_end":
                await mic.ptt_end()
            elif t == "mic":
                await mic.set_listening(bool(data.get("enabled", True)))
            elif t == "speaking_audio":
                mic.suspended = bool(data.get("playing"))  # avoid self-triggering the wake word
            elif t == "ping":
                await send({"type": "pong"})
            elif t == "briefing_refresh":
                asyncio.create_task(briefing.refresh("manual"))
            elif t == "briefing_action":
                async def run_action(d=data):
                    try:
                        r = await briefing.act(str(d.get("item_id", "")), str(d.get("action", "")),
                                               str(d.get("body", "")), bool(d.get("reply_all")))
                    except Exception as e:
                        log.exception("briefing action failed")
                        r = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
                    await send({"type": "briefing_result", "action": d.get("action"), "item_id": d.get("item_id"), **r})
                    if r.get("ok") and d.get("action") in ("reply_send", "trash", "archive"):
                        session.notes.append(f"Stephen used the briefing panel: {d.get('action')} on item {d.get('item_id')}.")
                asyncio.create_task(run_action())
            else:
                await session.handle(data)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws loop crashed")
    finally:
        briefing.listeners.discard(send)
        await session.close()


def main() -> None:
    import uvicorn
    uvicorn.run("jarvis_core.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
