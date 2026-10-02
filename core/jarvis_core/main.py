"""JARVIS Core: FastAPI + WebSocket bridge between the HUD and Hermes.

WS /ws  (text frames = JSON, binary frames = 16 kHz mono PCM16 mic audio)
GET /health, GET /status
"""
from __future__ import annotations

import asyncio
import re
import datetime as dt
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from jarvis_google import artifacts, code, markets, places, pylon, routes, sports, store
from jarvis_google import tools as gtools
from jarvis_google.accounts import linked_accounts

from . import pylon_ai
from .audio import MicPipeline, WakeWord
from .briefing import briefing
from .config import settings
from .hermes_client import HermesClient
from .session import Session, classify_confirmation
from .voice import Voice, pcm16_to_wav

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jarvis.core")

STATE: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    STATE["hermes"] = HermesClient(settings.hermes_url, settings.hermes_key, settings.hermes_model,
                                   settings.hermes_provider, settings.hermes_reasoning)
    STATE["voice"] = Voice(settings.voice_url, settings.voice_key, settings.voice, settings.voice_model,
                           settings.voice_speed, settings.voice_seed, settings.voice_steps, settings.voice_language)
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


@app.post("/upload")
async def upload(request: Request, files: list[UploadFile] = File(...)):
    """Files dropped / picked in the HUD. Stored as sandboxed, versioned copies; the HUD then sends `attach`."""
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    out, errors = [], []
    for f in files[:20]:
        data = await f.read(artifacts.MAX_UPLOAD + 1)
        try:
            m = await asyncio.to_thread(artifacts.save_upload, f.filename or "upload", data, f.content_type)
            out.append({"id": m["id"], "filename": m["filename"], "kind": m["kind"], "size": m["size"]})
        except Exception as e:
            errors.append(f"{f.filename}: {e}"[:200])
    return {"files": out, "errors": errors}


@app.get("/artifact/{artifact_id}/raw")
async def artifact_raw(artifact_id: str, request: Request, v: int = 0):
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        path, mime = artifacts.raw_path(artifact_id, v or None)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "no-store"})


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
        if via in ("sleep_phrase", "sleep_wake"):
            # Asleep. "sleep_wake" = the wake-word model fired in the phrase (it also fires on "Morning, Jarvis"), so
            # it always wakes; the words pick the power-on routine vs a plain wake. "sleep_phrase" = no wake word:
            # only "Morning, Jarvis" / "Hey Jarvis" count, anything else is ignored.
            if not session.sleeping:
                return
            heard = ""
            if pcm:
                try:
                    heard = await session.voice.stt(pcm16_to_wav(pcm))
                except Exception:
                    heard = ""
            kind = sleep_word(heard)
            log.info("sleep phrase (%s): %r -> %s", via, heard[:60], kind or "ignored")
            if kind == "morning":
                await session.power_on()
                return
            if kind != "wake" and via != "sleep_wake":
                return
            await session.wake_up()
            rest = after_wake_word(heard)
            if rest and not is_noise_transcript(rest, "wake"):  # "Hey Jarvis, what's the weather": answer it
                await send({"type": "transcript", "text": rest, "via": "wake"})
                await session.user_input(rest, source="voice")
            else:
                await on_mic_event({"type": "listening", "via": "wake"})
                await mic.listen_now()
            return
        await session.state("thinking")
        try:
            text = await session.voice.stt(pcm16_to_wav(pcm))
        except Exception as e:
            await send({"type": "voice_error", "error": f"transcription failed: {e}"})
            await session.state("idle")
            return
        await send({"type": "transcript", "text": text, "via": via})
        answering = session.briefing_question is not None and not session.briefing_question.done()
        # A confirm card is waiting: a short "yes" / "delete it" / "cancel" is his answer, never room noise.
        confirming = bool(store.pending_actions() or session.approvals) and classify_confirmation(text) is not None
        if confirming or not is_noise_transcript(text, via):
            await session.user_input(text, source="voice")
        elif answering:
            session.answer_timed_out()
        else:
            await session.state("idle")

    async def on_mic_event(ev: dict) -> None:
        if ev["type"] == "wake" and session.sleeping:  # "Hey Jarvis" wakes it the usual way (no cinematic)
            await session.wake_up()
        answering = session.briefing_question is not None and not session.briefing_question.done()
        if ev["type"] == "listening" and answering:  # the briefing asked him a question: hear the answer, keep going
            await session.state("listening")
        elif ev["type"] == "listening":
            await session.cancel_turn()  # barge-in: stop speaking / thinking immediately
            await session.state("listening")
            # If another app swapped VoiceStudio's engine (e.g. a video render), reload ours while he talks
            asyncio.create_task(session.voice.keep_warm())
        elif ev["type"] == "listening_end" and ev.get("empty"):
            if answering:
                session.answer_timed_out()
            else:
                await session.state("idle")
        await send(ev)

    mic = MicPipeline(on_mic_event, on_utterance, STATE["wake"])
    session.mic = mic
    follow_up_task: asyncio.Task | None = None

    def cancel_follow_up() -> None:
        nonlocal follow_up_task
        if follow_up_task and not follow_up_task.done():
            follow_up_task.cancel()
        follow_up_task = None

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
            if t == "sleep":
                cancel_follow_up()
                if data.get("enabled", True):
                    await session.go_to_sleep()  # no spoken line: nothing to wait for
                else:
                    await session.wake_up()
                continue
            if t == "power_on":  # the HUD's wake button / ⌘⇧S while asleep: same routine as "Morning, Jarvis"
                cancel_follow_up()
                await session.power_on()
                continue
            if t in ("ptt_start", "listen_now") and session.sleeping:
                await session.wake_up()
            if t == "ptt_start":
                cancel_follow_up()
                await on_mic_event({"type": "listening", "via": "ptt"})
                await mic.ptt_start()
            elif t == "listen_now":
                cancel_follow_up()
                await on_mic_event({"type": "listening", "via": "hotkey"})
                await mic.listen_now()
            elif t == "ptt_end":
                await mic.ptt_end()
            elif t == "mic":
                cancel_follow_up()
                await mic.set_listening(bool(data.get("enabled", True)))
            elif t == "speaking_audio":
                mic.suspended = bool(data.get("playing"))  # avoid self-triggering the wake word
            elif t == "playback_done":
                cancel_follow_up()
                was_speaking = session._state in ("speaking", "confirm") and not session._busy()
                await session.handle(data)
                if (was_speaking
                        and settings.follow_up_s > 0
                        and mic.wake_enabled
                        and mic.wake.available):
                    async def start_follow_up():
                        try:
                            await asyncio.sleep(0.3)
                            # A confirm card is waiting: listen longer for his spoken "confirm" / "cancel".
                            awaiting = session._state == "confirm" and bool(store.pending_actions() or session.approvals)
                            if (session._state == "idle" or awaiting) and not session._busy() and mic.mode == "wake":
                                await mic.listen_follow_up(CONFIRM_LISTEN_S if awaiting else settings.follow_up_s)
                        except asyncio.CancelledError:
                            pass
                    follow_up_task = asyncio.create_task(start_follow_up())
            elif t == "ping":
                await send({"type": "pong",
                            "hermes": await STATE["hermes"].health(),
                            "voice": await STATE["voice"].health()})
            elif t == "rpc":
                async def run_rpc(d=data):
                    op, args, req = str(d.get("op", "")), dict(d.get("args") or {}), d.get("req")
                    fn = {"directions_mode": routes.directions_mode, "market_chart": markets.market_chart,
                          "crypto_chart": markets.crypto_chart}.get(op)
                    try:
                        r = {"ok": True, "result": await asyncio.to_thread(fn, **args)} if fn else {"ok": False, "error": "operation not allowed"}
                    except Exception as e:
                        r = {"ok": False, "error": f"{e}"[:300]}
                    await send({"type": "rpc_result", "op": op, "req": req, **r})
                asyncio.create_task(run_rpc())
            elif t == "market_open":
                async def run_market(d=data):
                    try:
                        sym, kind = str(d.get("symbol", ""))[:40], d.get("kind")
                        fn = markets.crypto_open if kind == "crypto" else markets.stock_open
                        await asyncio.to_thread(fn, sym)
                        await session._flush_feed("direct")
                    except Exception as e:
                        await send({"type": "toast", "text": f"Couldn't load {d.get('symbol', 'that')}: {e}"[:200], "error": True})
                asyncio.create_task(run_market())
            elif t == "game_open":
                async def run_game(d=data):
                    try:
                        await asyncio.to_thread(sports.game_open, str(d.get("league", ""))[:20], str(d.get("event_id", ""))[:20])
                        await session._flush_feed("direct")
                    except Exception as e:
                        await send({"type": "toast", "text": f"Couldn't load that game: {e}"[:200], "error": True})
                asyncio.create_task(run_game())
            elif t == "place_open":
                async def run_place(d=data):
                    try:
                        await asyncio.to_thread(places.place_open, str(d.get("place_id", "")))
                        await session._flush_feed("direct")
                    except Exception as e:
                        await send({"type": "toast", "text": f"Couldn't load that place: {e}"[:200], "error": True})
                asyncio.create_task(run_place())
            elif t == "pylon":
                async def run_pylon(d=data):
                    op, args, req = str(d.get("op", "")), dict(d.get("args") or {}), d.get("req")
                    try:
                        if op == "pylon_options":
                            r = {"ok": True, "result": await asyncio.to_thread(pylon.options)}
                        elif op == "pylon_draft":
                            r = {"ok": True, "result": await pylon_ai.draft(str(args.get("issue_id", "")),
                                                                            str(args.get("kind", "")), str(args.get("steer", "")))}
                        elif op in pylon.CLICK_READ | pylon.CLICK_WRITE:
                            r = {"ok": True, "result": await asyncio.to_thread(getattr(pylon, op), **args)}
                        else:
                            r = {"ok": False, "error": "operation not allowed"}
                    except Exception as e:
                        log.exception("pylon op %s failed", op)
                        r = {"ok": False, "error": f"{e}"[:300]}
                    await send({"type": "pylon_result", "op": op, "req": req, **r})
                    if r.get("ok") and op in pylon.CLICK_WRITE:
                        session.notes.append(f"Stephen used a Pylon card: {op} -> {(r.get('result') or {}).get('text', '')}")
                    if op == "pylon_open" and r.get("ok"):
                        await session._flush_feed("direct")
                asyncio.create_task(run_pylon())
            elif t == "attach":
                # files just uploaded: show each one, and hand them to the next turn as context
                items = [i for i in (data.get("files") or []) if isinstance(i, dict)][:20]
                session.attach(items)
                async def run_attach(items=items):
                    for it in items:
                        try:
                            m = await asyncio.to_thread(artifacts._meta, str(it.get("id", "")))
                            await asyncio.to_thread(store.record_result, "file_upload", None, {"artifact_id": m["id"]},
                                                    artifacts.card_data(m))
                        except Exception as e:
                            await send({"type": "toast", "text": f"Couldn't open {it.get('filename')}: {e}"[:200], "error": True})
                    await session._flush_feed("direct")
                asyncio.create_task(run_attach())
            elif t == "focus":
                f = data.get("focus")
                session.set_focus(f if isinstance(f, dict) else None)
            elif t == "workspace":
                # click-only ops on file / code cards (allow-listed); writes are versioned + audit-logged
                async def run_ws(d=data):
                    op, args, req = str(d.get("op", "")), dict(d.get("args") or {}), d.get("req")
                    fn = artifacts.CLICK_OPS.get(op) or code.CLICK_OPS.get(op)
                    try:
                        r = {"ok": True, "result": await asyncio.to_thread(fn, **args)} if fn else {"ok": False, "error": "operation not allowed"}
                    except Exception as e:
                        r = {"ok": False, "error": f"{e}"[:300]}
                    await send({"type": "workspace_result", "op": op, "req": req, **r})
                    if r.get("ok") and op in ("artifact_save_text", "artifact_sheet_set", "artifact_image_op",
                                               "artifact_revert", "code_undo"):
                        session.notes.append(f"Stephen used a file/code card: {op} on {args.get('artifact_id') or args.get('change_id')}"
                                             f" -> {(r.get('result') or {}).get('text', '')} (re-read before editing it again)")
                    if r.get("ok") and op == "code_open_folder":
                        session.set_focus({"type": "project", "root": args.get("path"), "name": str(args.get("path", "")).rstrip("/").split("/")[-1]})
                        await session._flush_feed("direct")
                asyncio.create_task(run_ws())
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
                if t in ("user_text", "cancel", "reset"):
                    cancel_follow_up()
                    await mic.cancel()
                await session.handle(data)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws loop crashed")
    finally:
        cancel_follow_up()
        await mic.cancel()
        briefing.listeners.discard(send)
        await session.close()


_MORNING = re.compile(r"\b(?:good\s+)?morning[\s,.!]*(?:jarvis|jarvas|jervis|travis|service)\b|\b(?:jarvis|jervis)[\s,.!]+(?:good\s+)?morning\b", re.I)
_HEY = re.compile(r"\b(?:hey|hi|okay|ok)[\s,.!]*(?:jarvis|jervis|jarvas)\b", re.I)


def after_wake_word(text: str) -> str:
    """'Hey Jarvis, what's the weather?' -> "what's the weather?" ('' when nothing follows the wake phrase)."""
    m = _HEY.search(text or "")
    rest = (text[m.end():] if m else text or "").lstrip(" ,.!?-")
    return rest if len(rest.split()) >= 2 else ""


CONFIRM_LISTEN_S = 8.0  # how long the mic stays open for a spoken "confirm" / "cancel" after he's asked


def sleep_word(text: str) -> str | None:
    """What a phrase heard while asleep means: 'morning' (power-on routine + briefing), 'wake', or None (ignore)."""
    t = text or ""
    if _MORNING.search(t):
        return "morning"
    if _HEY.search(t):
        return "wake"
    return None


_SOUND_TAG = re.compile(r"^[\s.]*(?:[\[(*♪][^\])*♪]*[\])*♪]?[\s.,]*)+$")
_FILLER = {"", "you", "thank you", "thanks for watching", "thanks", "bye", "okay", "ok", "um", "uh", "hmm", "mm"}


def is_noise_transcript(text: str, via: str = "") -> bool:
    """Whisper-style annotations of non-speech audio ("*sad music*", "[Music]", "(applause)", "♪") and filler that
    room noise produces. In the open follow-up window (no wake word) one or two stray words are noise too."""
    t = (text or "").strip()
    if not t or _SOUND_TAG.match(t):
        return True
    low = t.strip(" .!?,").lower()
    if low in _FILLER:
        return True
    if re.fullmatch(r"(.+?)\s*\(\1\)", low):  # "3.5mm (3.5mm)": a transcriber echo, not speech
        return True
    # The follow-up window has no wake word, so it hears the room (TV, people nearby). Short fragments there are
    # almost always background chatter; real follow-ups are a few words, or one of the short replies below.
    short_ok = {"yes", "no", "yeah", "nope", "confirm", "cancel", "stop", "thanks", "thank you", "go ahead", "do it",
                "send it", "next", "pause", "skip", "more", "tell me more", "why", "how", "continue", "keep going"}
    return via == "follow_up" and len(low.split()) < 3 and low not in short_ok


def main() -> None:
    import uvicorn
    uvicorn.run("jarvis_core.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
