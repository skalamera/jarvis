"""One JARVIS client session: turns, streaming, cards, speech, confirmations."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
import uuid
from typing import Any, Awaitable, Callable

from jarvis_google import store
from jarvis_google import tools as gtools

from . import visuals as V
from .config import settings
from .hermes_client import HermesClient, HermesError
from .persona import build_instructions
from .voice import Voice, b64

log = logging.getLogger("jarvis.session")
Send = Callable[[dict], Awaitable[None]]

_CONFIRM = re.compile(r"^(?:(?:yes|yeah|yep|ok|okay)[, ]*)?(?:jarvis[, ]*)?(confirm(?:ed)?|authori[sz]e(?:d)?|send it|"
                      r"do it|go ahead|proceed|approved?|yes,? send(?: it)?|send)(?:[, ]*(?:jarvis|please|now))*[.!]?$", re.I)
_CANCEL = re.compile(r"^(?:(?:no|nope)[, ]*)?(?:jarvis[, ]*)?(cancel|abort|stop|don'?t(?: send| do it)?|never ?mind|"
                     r"hold off|scratch that|no)(?:[, ]*(?:that|it|jarvis|please))*[.!]?$", re.I)
_RESET = re.compile(r"^(?:jarvis[, ]*)?(new (?:session|conversation|chat)|start over|reset(?: conversation)?)[.!]?$", re.I)

_ACTION_DONE = {
    "gmail_send": "Sent, sir.", "gmail_send_draft": "Draft sent.", "gmail_reply": "Reply sent.",
    "gmail_trash": "Moved to trash.", "gmail_delete_permanently": "Permanently deleted.",
    "calendar_create": "Event created and invitations sent.", "calendar_delete": "Event deleted.",
    "drive_share": "Shared.", "drive_trash": "Moved to trash.", "sheets_write": "Sheet updated.",
}


def classify_confirmation(text: str) -> str | None:
    t = text.strip().strip("\"'").strip()
    if len(t.split()) > 7:
        return None
    if _CONFIRM.match(t):
        return "confirm"
    if _CANCEL.match(t):
        return "cancel"
    return None


class Session:
    def __init__(self, send: Send, hermes: HermesClient, voice: Voice):
        self._send, self.hermes, self.voice = send, hermes, voice
        self.session_id = f"jarvis-{dt.date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
        self.turn_task: asyncio.Task | None = None
        self.run_id: str | None = None
        self.speak = True
        self.notes: list[str] = []
        self.shown_actions: set[str] = set()
        self.approvals: dict[str, dict] = {}  # card id -> {run_id, request_id}
        self._tts_q: asyncio.Queue | None = None
        self._tts_task: asyncio.Task | None = None
        self._state = "idle"
        self._closed = False
        self._feed_pos = store.feed_head()

    # ------------------------------------------------------------------ io
    async def send(self, msg: dict) -> None:
        if self._closed:
            return
        try:
            await self._send(msg)
        except Exception:  # client went away
            self._closed = True

    async def state(self, s: str) -> None:
        if s != self._state:
            self._state = s
            await self.send({"type": "state", "state": s})

    async def hello(self) -> None:
        await self.send({"type": "hello", "session_id": self.session_id, "accounts": gtools.accounts_list(),
                         "hermes": await self.hermes.health(), "voice": await self.voice.health(),
                         "speak": self.speak, "voice_name": self.voice.voice})
        for a in store.pending_actions():
            await self._show_action(a)

    async def close(self) -> None:
        self._closed = True
        await self.cancel_turn()

    # ------------------------------------------------------------------ inbound
    async def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "user_text":
            await self.user_input(str(msg.get("text", "")), source=msg.get("source", "text"))
        elif t == "cancel":
            await self.cancel_turn()
            await self.state("idle")
        elif t == "confirm":
            await self.resolve_action(msg["action_id"], True, msg.get("source", "click"))
        elif t == "reject":
            await self.resolve_action(msg["action_id"], False, msg.get("source", "click"))
        elif t == "speak":
            self.speak = bool(msg.get("enabled", True))
            await self.send({"type": "speak", "enabled": self.speak})
        elif t == "draft_update":
            await self._draft_update(msg)
        elif t == "reset":
            await self.reset()
        elif t == "playback_done":
            if self._state == "speaking" and not self._busy():
                await self.state("idle")
        elif t == "direct":
            await self._direct(msg)

    # ------------------------------------------------------------------ HUD clicks (no model round-trip)
    _DIRECT_READ = {"gmail_read", "gmail_read_thread", "drive_read_text", "gmail_search", "calendar_list"}
    _DIRECT_PROPOSE = {"gmail_send_draft", "gmail_trash", "calendar_delete", "drive_trash"}
    _DIRECT_EXEC = {"gmail_delete_draft", "gmail_modify"}  # human click on reversible / own-draft ops

    async def _direct(self, msg: dict) -> None:
        op, args = msg.get("op", ""), dict(msg.get("args") or {})
        if op not in self._DIRECT_READ | self._DIRECT_PROPOSE | self._DIRECT_EXEC:
            await self.send({"type": "direct_result", "op": op, "ok": False, "error": "operation not allowed"})
            return
        try:
            result = await asyncio.to_thread(getattr(gtools, op), **args)
        except Exception as e:
            await self.send({"type": "direct_result", "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        await self.send({"type": "direct_result", "op": op, "ok": True, "result": result if op in self._DIRECT_EXEC
                         else None, "args": args})
        if op in self._DIRECT_EXEC:
            self.notes.append(f"Stephen used the HUD to run {op} with {args}.")
        await self._flush_feed("direct")

    def _busy(self) -> bool:
        return bool(self.turn_task and not self.turn_task.done())

    async def reset(self) -> None:
        await self.cancel_turn()
        self.session_id = f"jarvis-{dt.date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
        self.notes.clear()
        await self.send({"type": "reset", "session_id": self.session_id})
        await self.state("idle")

    async def user_input(self, text: str, source: str = "text") -> None:
        text = text.strip()
        if not text:
            await self.state("idle")
            return
        await self.send({"type": "user_message", "text": text, "source": source})

        pending = [a for a in store.pending_actions()]
        verdict = classify_confirmation(text) if pending else None
        if verdict:
            await self.cancel_turn()
            await self.resolve_action(pending[-1]["id"], verdict == "confirm", f"voice:{text}" if source == "voice"
                                      else f"typed:{text}")
            return
        if _RESET.match(text):
            await self.reset()
            await self.say_line("Fresh session, sir.")
            return
        await self.cancel_turn()  # barge-in
        self.turn_task = asyncio.create_task(self._turn(text))

    # ------------------------------------------------------------------ turn
    async def cancel_turn(self) -> None:
        if self.run_id:
            await self.hermes.stop(self.run_id)
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
            try:
                await self.turn_task
            except (asyncio.CancelledError, Exception):
                pass
        self.turn_task, self.run_id = None, None
        await self._stop_tts()
        await self.send({"type": "stop_audio"})

    async def _turn(self, text: str) -> None:
        turn_id = "turn_" + uuid.uuid4().hex[:8]
        await self.state("thinking")
        await self.send({"type": "turn_start", "turn_id": turn_id})
        feed_head = store.feed_head()
        instructions = build_instructions(settings.user_name, settings.timezone, self.notes)
        self.notes = []
        sentences = V.SentenceStream()
        full = ""
        tools_running: dict[str, float] = {}
        acked = False
        poll = asyncio.create_task(self._poll_feed(turn_id, feed_head))
        self._start_tts(turn_id)
        started = time.monotonic()
        try:
            self.run_id = await self.hermes.start_run(text, self.session_id, instructions)
            async for ev in self.hermes.events(self.run_id):
                kind = ev.get("event")
                if kind == "message.delta":
                    full += ev.get("delta") or ""
                    visible = V.partial_visible_text(full)
                    await self.send({"type": "assistant_delta", "turn_id": turn_id, "text": visible})
                    for s in sentences.feed(visible):
                        await self._say(turn_id, s)
                elif kind == "tool.started":
                    name = ev.get("tool") or ""
                    label = V.tool_label(name)
                    tools_running[name] = time.monotonic()
                    if label and not acked and not full.strip() and self.speak:
                        acked = True
                        await self._ack(turn_id)
                    if label:
                        await self.send({"type": "tool", "turn_id": turn_id, "status": "start", "tool": name,
                                         "label": label, "preview": (ev.get("preview") or "")[:140]})
                elif kind == "tool.completed":
                    name = ev.get("tool") or ""
                    label = V.tool_label(name)
                    if label:
                        await self.send({"type": "tool", "turn_id": turn_id, "status": "error" if ev.get("error")
                                         else "done", "tool": name, "label": label, "duration": ev.get("duration")})
                elif kind == "approval.request":
                    await self._hermes_approval(ev)
                elif kind == "subagent.start":
                    await self.send({"type": "tool", "turn_id": turn_id, "status": "start", "tool": "subagent",
                                     "label": "AGENT · DEPLOYED", "preview": (ev.get("preview") or "")[:140]})
                elif kind == "subagent.complete":
                    await self.send({"type": "tool", "turn_id": turn_id, "status": "done", "tool": "subagent",
                                     "label": "AGENT · REPORTED"})
                elif kind == "run.completed":
                    full = ev.get("output") or full
                elif kind in ("run.failed", "run.cancelled"):
                    if kind == "run.failed":
                        err = ev.get("error") or "the run failed"
                        full = full or f"I hit a problem, sir: {err}"
                    break
            clean, vis_cards = V.extract_visuals(full)
            await asyncio.sleep(0.35)  # let the feed poller flush the final tool results
            poll.cancel()
            await self._flush_feed(turn_id)
            for c in vis_cards:
                await self.send({"type": "card", "turn_id": turn_id, "card": c})
            await self.send({"type": "assistant_final", "turn_id": turn_id, "text": clean,
                             "elapsed": round(time.monotonic() - started, 2)})
            for s in sentences.feed(clean, final=True):
                await self._say(turn_id, s)
            await self._end_tts()
        except HermesError as e:
            await self.send({"type": "assistant_final", "turn_id": turn_id,
                             "text": f"I can't reach my core systems right now. {e}", "error": True})
            await self.say_line("I can't reach my core systems right now, sir.")
        except asyncio.CancelledError:
            await self.send({"type": "turn_cancelled", "turn_id": turn_id, "text": V.partial_visible_text(full)})
            raise
        finally:
            poll.cancel()
            self.run_id = None
            if not self.speak and self._state != "confirm":
                await self.state("idle")

    # ------------------------------------------------------------------ cards from the tool feed
    async def _poll_feed(self, turn_id: str, head: int) -> None:
        self._feed_pos = max(head, self._feed_pos)
        while True:
            await asyncio.sleep(0.3)
            await self._flush_feed(turn_id)

    async def _flush_feed(self, turn_id: str) -> None:
        for item in store.feed_since(self._feed_pos):
            self._feed_pos = item["seq"]
            for c in V.cards_from_feed(item):
                await self.send({"type": "card", "turn_id": turn_id, "card": c})
        for a in store.pending_actions():
            await self._show_action(a)

    async def _show_action(self, a: dict) -> None:
        if a["id"] in self.shown_actions:
            return
        self.shown_actions.add(a["id"])
        await self.send({"type": "card", "card": V.action_card(a)})
        await self.state("confirm")

    # ------------------------------------------------------------------ confirmations
    async def resolve_action(self, action_id: str, approve: bool, source: str) -> None:
        if action_id in self.approvals:
            await self._resolve_hermes_approval(action_id, approve)
            return
        action = store.get_action(action_id)
        if not action:
            return
        if not approve:
            store.cancel_action(action_id, source)
            await self.send({"type": "action_result", "action_id": action_id, "status": "cancelled"})
            self.notes.append(f"Stephen CANCELLED: {action['summary']}")
            await self.state("idle")
            await self.say_line("Cancelled.")
            return
        await self.state("thinking")
        out = await asyncio.to_thread(store.execute_action, action_id, source)
        status = "executed" if out.get("ok") else "failed"
        await self.send({"type": "action_result", "action_id": action_id, "status": status, "result": out})
        if out.get("ok"):
            self.notes.append(f"Stephen CONFIRMED and it was executed: {action['summary']} -> {out.get('result')}")
            await self.say_line(_ACTION_DONE.get(action["kind"], "Done."))
        else:
            self.notes.append(f"Execution FAILED for: {action['summary']} -> {out.get('error')}")
            await self.say_line("That didn't go through, sir. The error is on screen.")

    async def _hermes_approval(self, ev: dict) -> None:
        cid = "appr_" + uuid.uuid4().hex[:8]
        self.approvals[cid] = {"run_id": ev.get("run_id") or self.run_id, "request_id": ev.get("request_id")}
        desc = ev.get("description") or ev.get("reason") or "Hermes wants to run a flagged action"
        await self.send({"type": "card", "card": {
            "id": cid, "kind": "confirm", "title": desc, "account": None,
            "data": {"kind": "hermes_approval", "status": "pending",
                     "preview": {"type": "command", "command": ev.get("command", ""), "choices": ev.get("choices")}}}})
        await self.state("confirm")
        await self.say_line("I need your authorization for that, sir.")

    async def _resolve_hermes_approval(self, cid: str, approve: bool) -> None:
        info = self.approvals.pop(cid)
        try:
            await self.hermes.approve(info["run_id"], "once" if approve else "deny", info.get("request_id"))
        finally:
            await self.send({"type": "action_result", "action_id": cid, "status": "executed" if approve else "cancelled"})
            await self.state("thinking" if self._busy() else "idle")

    async def _draft_update(self, msg: dict) -> None:
        try:
            r = await asyncio.to_thread(gtools.gmail_update_draft, msg["account"], msg["draft_id"], msg.get("to", ""),
                                        msg.get("subject", ""), msg.get("body", ""), msg.get("cc", ""), msg.get("bcc", ""))
            await self.send({"type": "draft_saved", "draft_id": msg["draft_id"], "ok": True, "draft": r})
            self.notes.append(f"Stephen edited draft {msg['draft_id']} (subject: {msg.get('subject')}).")
        except Exception as e:
            await self.send({"type": "draft_saved", "draft_id": msg.get("draft_id"), "ok": False, "error": str(e)})

    # ------------------------------------------------------------------ speech
    def _start_tts(self, turn_id: str) -> None:
        self._tts_q = asyncio.Queue()
        self._tts_task = asyncio.create_task(self._tts_worker(turn_id, self._tts_q))

    async def _say(self, turn_id: str, sentence: str) -> None:
        if self.speak and self._tts_q is not None:
            await self._tts_q.put(sentence)

    async def _end_tts(self) -> None:
        if self._tts_q is not None:
            await self._tts_q.put(None)
            if self._tts_task:
                try:
                    await self._tts_task
                except asyncio.CancelledError:
                    pass
        self._tts_q, self._tts_task = None, None

    async def _stop_tts(self) -> None:
        if self._tts_task and not self._tts_task.done():
            self._tts_task.cancel()
        self._tts_q, self._tts_task = None, None

    async def _tts_worker(self, turn_id: str, q: asyncio.Queue) -> None:
        seq = 0
        spoke = False
        while True:
            sentence = await q.get()
            if sentence is None:
                break
            try:
                audio = await self.voice.tts(sentence)
            except Exception as e:
                log.warning("tts failed: %s", e)
                await self.send({"type": "voice_error", "error": str(e)[:200]})
                continue
            if self._state != "confirm":
                await self.state("speaking")
            await self.send({"type": "speech", "turn_id": turn_id, "seq": seq, "text": sentence,
                             "audio": b64(audio), "mime": "audio/wav"})
            seq += 1
            spoke = True
        await self.send({"type": "speech_end", "turn_id": turn_id, "spoke": spoke})
        if not spoke and self._state != "confirm":
            await self.state("idle")

    async def _ack(self, turn_id: str) -> None:
        ack = self.voice.next_ack()
        if ack is None:
            return
        line, audio = ack
        await self.state("speaking")
        await self.send({"type": "speech", "turn_id": turn_id, "seq": -1, "text": line, "audio": b64(audio),
                         "mime": "audio/wav", "ack": True})
        await self.state("thinking")

    async def say_line(self, text: str) -> None:
        """Speak a fixed system line outside a model turn."""
        await self.send({"type": "system_line", "text": text})
        if not self.speak:
            return
        try:
            audio = await self.voice.tts(text)
            await self.state("speaking" if self._state != "confirm" else "confirm")
            await self.send({"type": "speech", "turn_id": "system", "seq": 0, "text": text, "audio": b64(audio),
                             "mime": "audio/wav"})
            await self.send({"type": "speech_end", "turn_id": "system", "spoke": True})
        except Exception as e:
            await self.send({"type": "voice_error", "error": str(e)[:200]})
            await self.state("idle")

    def as_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "state": self._state, "busy": self._busy()}
