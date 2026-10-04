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
from .briefing import briefing
from .config import settings
from .hermes_client import HermesClient, HermesError
from .persona import build_instructions
from .quick import quick_answer
from . import showcase
from .voice import Voice, b64

def support_available() -> bool:
    from jarvis_google import support
    return support.available()


log = logging.getLogger("jarvis.session")
Send = Callable[[dict], Awaitable[None]]
HEALTH_CHECK_INTERVAL_S = 10
MARKET_MONITOR_S = 180

# "cancel" is deliberately NOT a confirm verb: as an answer to a confirm card it means "don't".
_VERB = r"(?:delete|remove|book|send|trash|share|reserve|do|make|create|run|post|write)"
_CONFIRM = re.compile(
    r"^(?:(?:yes|yeah|yep|yup|sure|ok|okay|absolutely|definitely|correct|affirmative)[, ]*)?(?:jarvis[, ]*)?"
    r"(?:confirm(?:ed)?|authori[sz]e(?:d)?|send it|do it|go ahead|proceed(?: with (?:it|that|the \w+))?|continue|"
    r"approved?|send|"
    rf"(?:go ahead and |please )?{_VERB}(?: (?:it|that|them|this|the \w+(?: \w+)?))?|"
    r"yes|yeah|yep|yup|sure|ok|okay|absolutely|definitely|correct|affirmative|that'?s right|sounds good)"
    r"(?:[, ]*(?:jarvis|please|now|sir|then))*[.!]?$", re.I)
_CANCEL = re.compile(r"^(?:(?:no|nope)[, ]*)?(?:jarvis[, ]*)?(cancel|abort|stop|don'?t(?: send| do it)?|never ?mind|"
                     r"hold off|scratch that|no)(?:[, ]*(?:that|it|jarvis|please))*[.!]?$", re.I)
_RESET = re.compile(r"^(?:jarvis[, ]*)?(new (?:session|conversation|chat)|start over|reset(?: conversation)?)[.!]?$", re.I)

_ACTION_DONE = {
    "gmail_send": "Sent, sir.", "gmail_send_draft": "Draft sent.", "gmail_reply": "Reply sent.",
    "gmail_trash": "Moved to trash.", "gmail_delete_permanently": "Permanently deleted.",
    "calendar_create": "Event created and invitations sent.", "calendar_delete": "Event deleted.",
    "drive_share": "Shared.", "drive_trash": "Moved to trash.", "sheets_write": "Sheet updated.",
    "flight_book": "Booked, sir. The confirmation is on screen.", "hotel_book": "Your room is booked, sir.",
    "car_rental_book": "The car is reserved, sir.", "restaurant_book": "Your table is booked, sir.",
    "reservation_cancel": "Cancelled, sir.",
    "trade_order": "Order sent to Kraken, sir. The fill is on screen.", "trade_cancel": "Order cancelled.",
}


# Spoken answer to a generated image/video hologram (HoloForge). LOSE is checked first ("no, delete it").
_F_TAIL = r"(?:[, ]*(?:jarvis|please|sir|thanks|thank you|now))*[.!]?$"
_F_OBJ = r"(?: (?:it|that|this|them|the (?:image|picture|photo|video|clip|one)))?"
_FORGE_LOSE = re.compile(
    r"^(?:(?:no|nope|nah)[, ]*)?(?:jarvis[, ]*)?"
    rf"(?:lose{_F_OBJ}|delete{_F_OBJ}|dismiss{_F_OBJ}|discard{_F_OBJ}|trash{_F_OBJ}|scrap{_F_OBJ}|toss{_F_OBJ}|"
    rf"bin{_F_OBJ}|remove{_F_OBJ}|reject{_F_OBJ}|throw{_F_OBJ} (?:away|out)|get rid of{_F_OBJ}|ditch{_F_OBJ}|"
    r"cancel|no|nope|nah|no thanks|not that one|i don'?t like it|don'?t keep it)" + _F_TAIL, re.I)
_FORGE_KEEP = re.compile(
    r"^(?:(?:yes|yeah|yep|yup|sure|ok|okay)[, ]*)?(?:jarvis[, ]*)?"
    rf"(?:keep{_F_OBJ}|accept{_F_OBJ}|confirm{_F_OBJ}|save{_F_OBJ}|approve{_F_OBJ}|add{_F_OBJ}|"
    r"send it(?: over)?|yes|yeah|yep|yup|sure|ok|okay|approved|looks good|perfect|great|nice|i like it|love it|"
    r"that'?s (?:good|great|perfect)|sounds good)" + _F_TAIL, re.I)


def classify_forge(text: str) -> str | None:
    """'keep' / 'lose' for a short spoken answer to the center hologram, else None."""
    t = text.strip().strip("\"'").strip()
    if len(t.split()) > 7:
        return None
    if _FORGE_LOSE.match(t):
        return "lose"
    if _FORGE_KEEP.match(t):
        return "keep"
    return None


def classify_confirmation(text: str) -> str | None:
    t = text.strip().strip("\"'").strip()
    if len(t.split()) > 7:
        return None
    if _CANCEL.match(t):  # checked first: "cancel" / "no, cancel it" always means don't do it
        return "cancel"
    if _CONFIRM.match(t):
        return "confirm"
    return None


# Tools whose displays may land after the turn ended (rendered in the background).
BACKGROUND_TOOLS = {"video_generate", "video_analyze", "booking", "trade_alert", "trade_order_placed", "trade_desk", "trade_orders"}

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
        self._health_task: asyncio.Task | None = None
        self._idle_feed_task: asyncio.Task | None = None
        self.attachments: list[dict] = []  # files uploaded since the last turn, handed to the next one
        self.focus: dict | None = None      # the file / project currently on screen (the conversation's subject)
        self._played = asyncio.Event()      # the HUD finished playing everything queued (showcase pacing)
        self._audio_out = False             # speech sent to the HUD that it hasn't reported finished yet
        self.forge_pending = False          # a generated image/video is waiting in the center hologram
        self._tour_pending = False          # support_model(tour=True) ran this turn: narrate the tour after the reply
        self.sleeping = False               # dimmed HUD; "Morning, Jarvis" powers it on (cinematic + daily briefing)
        self.mic: Any = None                # the MicPipeline (main.py), so sleep can switch it into sleep-word mode
        self.briefing_question: asyncio.Future | None = None  # the briefing is waiting for his answer to a question
        self.pending_input: str | None = None  # a request made mid-briefing, run as a normal turn when it ends

    # ------------------------------------------------------------------ io
    async def send(self, msg: dict) -> None:
        if self._closed:
            return
        try:
            await self._send(msg)
        except Exception:  # client went away
            self._closed = True

    # ------------------------------------------------------------------ sleep / power on
    async def go_to_sleep(self, line: str | None = None) -> None:
        await self.cancel_turn()
        if line:
            self._played.clear()
            await self.say_line(line)
            if self.speak:
                try:  # let the line finish before the lights go down
                    await asyncio.wait_for(self._played.wait(), timeout=6)
                except asyncio.TimeoutError:
                    pass
        self.sleeping = True
        if self.mic is not None:
            self.mic.sleeping = True
        await self.send({"type": "sleep", "asleep": True})
        await self.state("sleep")

    async def wake_up(self) -> None:
        if not self.sleeping:
            return
        self.sleeping = False
        if self.mic is not None:
            self.mic.sleeping = False
        await self.send({"type": "sleep", "asleep": False})
        if self._state == "sleep":
            await self.state("idle")

    async def power_on(self) -> None:
        """'Morning, Jarvis': the HUD plays its short power-on sequence while the briefing gathers, then the briefing."""
        await self.cancel_turn()
        self.sleeping = False
        if self.mic is not None:
            self.mic.sleeping = False
        await self.send({"type": "power_on", "ms": int(showcase.POWER_ON_S * 1000)})
        await self.send({"type": "sleep", "asleep": False})
        await self.start_showcase("demo", intro_delay=showcase.POWER_ON_S)

    async def state(self, s: str) -> None:
        if s != self._state:
            self._state = s
            await self.send({"type": "state", "state": s})

    async def hello(self) -> None:
        h = await self.hermes.health()
        v = await self.voice.health()
        await self.send({"type": "hello", "session_id": self.session_id, "accounts": gtools.accounts_list(),
                         "hermes": h, "voice": v,
                         "speak": self.speak, "voice_name": self.voice.voice})
        for a in store.pending_actions():
            await self._show_action(a)
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._health_loop(h, v))
        if self._idle_feed_task is None or self._idle_feed_task.done():
            self._idle_feed_task = asyncio.create_task(self._idle_feed_loop())
        if getattr(self, "_market_task", None) is None or self._market_task.done():
            self._market_task = asyncio.create_task(self._market_monitor_loop())

    async def _idle_feed_loop(self) -> None:
        """Between turns, still show displays that land late (a generated video finishing in the background)."""
        while not self._closed:
            await asyncio.sleep(1.5)
            if self._busy() or self._closed:
                continue
            try:
                await self._flush_feed("background", only=BACKGROUND_TOOLS)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _market_monitor_loop(self) -> None:
        """Watch his Kraken holdings: price alerts, 5%+ daily moves, filled orders. Cards always; spoken only when idle
        and awake (never interrupts a turn, a briefing or sleep)."""
        from jarvis_google import trading
        await asyncio.sleep(20)
        while not self._closed:
            try:
                if trading.configured():
                    fired = await asyncio.to_thread(trading.monitor_tick)
                    if fired:
                        for f in fired:
                            self.notes.append(f"Market monitor: {f['text']}")
                        if not self._busy() and not self.sleeping and self._state in ("idle", None):
                            await self._flush_feed("background", only=BACKGROUND_TOOLS)
                            await self.say_line("Sir, " + fired[0]["text"][0].lower() + fired[0]["text"][1:]
                                                + (f" And {len(fired) - 1} more on screen." if len(fired) > 1 else ""))
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("market monitor: %s", e)
            await asyncio.sleep(MARKET_MONITOR_S)

    async def _health_loop(self, last_h: bool, last_v: bool) -> None:
        while not self._closed:
            await asyncio.sleep(HEALTH_CHECK_INTERVAL_S)
            if self._closed:
                break
            try:
                h = await self.hermes.health()
                v = await self.voice.health()
                if h != last_h or v != last_v:
                    last_h, last_v = h, v
                    await self.send({"type": "health", "hermes": h, "voice": v})
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def close(self) -> None:
        self._closed = True
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        if self._idle_feed_task and not self._idle_feed_task.done():
            self._idle_feed_task.cancel()
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
            self._played.set()
            self._audio_out = False
            if self._state == "speaking" and not self._busy():
                await self.state("idle")
        elif t == "showcase":
            await self.start_showcase(str(msg.get("which") or "demo"))
        elif t == "direct":
            await self._direct(msg)

    # ------------------------------------------------------------------ HUD clicks (no model round-trip)
    _DIRECT_READ = {"gmail_read", "gmail_read_thread", "drive_read_text", "gmail_search", "calendar_list"}
    _DIRECT_PROPOSE = {"gmail_send_draft", "gmail_trash", "calendar_delete", "drive_trash"}
    # human click on reversible / own-draft ops. gmail_trash_now: Stephen clicked Delete then "Confirm delete?"
    # on that specific visible email, so the double click IS the confirmation (Trash is recoverable + undo).
    _DIRECT_EXEC = {"gmail_delete_draft", "gmail_modify", "gmail_trash_now", "gmail_restore"}
    # "Book" on an offer card: only PROPOSES (a confirm card he still has to authorize).
    _DIRECT_BOOK = {"flight_book", "hotel_book", "car_rental_book", "restaurant_book", "reservation_cancel"}
    # Uber Eats display: browsing + editing the local cart (no money moves; ordering stays behind a confirm card).
    _DIRECT_EATS = {"menu", "cart_add", "cart_remove", "search"}
    # Trading desk: read / refresh, and order tickets that only PROPOSE (a confirm card he authorizes).
    _DIRECT_TRADE = {"portfolio", "insights", "order", "cancel", "alert_set", "alert_remove", "open_orders"}

    async def _direct(self, msg: dict) -> None:
        op, args = msg.get("op", ""), dict(msg.get("args") or {})
        if op.startswith("trade_") and op[6:] in self._DIRECT_TRADE:
            from jarvis_google import trading
            fn = getattr(trading, op[6:])
            try:
                result = await asyncio.to_thread(fn, **args)
                await self.send({"type": "direct_result", "op": op, "ok": True,
                                 "result": result if op != "trade_portfolio" else None})
            except Exception as e:
                await self.send({"type": "direct_result", "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            if op == "trade_order":
                self.notes.append(f"Stephen filled in the order ticket on the trading desk: {args}.")
            await self._flush_feed("direct")
            return
        if op.startswith("eats_") and op[5:] in self._DIRECT_EATS:
            from jarvis_google import eats
            try:
                result = await asyncio.to_thread(getattr(eats, op[5:]), **args)
                await self.send({"type": "direct_result", "op": op, "ok": True, "result": result})
            except Exception as e:
                await self.send({"type": "direct_result", "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            if op == "eats_cart_add":
                self.notes.append(f"Stephen tapped Add on the Uber Eats menu: {args.get('item')}.")
            await self._flush_feed("direct")
            return
        if op not in self._DIRECT_READ | self._DIRECT_PROPOSE | self._DIRECT_EXEC | self._DIRECT_BOOK:
            await self.send({"type": "direct_result", "op": op, "ok": False, "error": "operation not allowed"})
            return
        try:
            if op in self._DIRECT_BOOK:
                from jarvis_google import travel
                result = await asyncio.to_thread(getattr(travel, op), **args)
            else:
                result = await asyncio.to_thread(getattr(gtools, op), **args)
        except Exception as e:
            await self.send({"type": "direct_result", "op": op, "ok": False, "error": f"{type(e).__name__}: {e}"})
            return
        await self.send({"type": "direct_result", "op": op, "ok": True, "result": result if op in self._DIRECT_EXEC
                         else None, "args": args})
        if op in self._DIRECT_BOOK and isinstance(result, dict) and result.get("status") in ("booked", "cancelled"):
            # a fee-free Resy booking / cancel he tapped: done on the spot
            self.notes.append(f"Stephen tapped the HUD and it was done (no fees): {op} -> {result.get('name')}")
            await self._flush_feed("direct")
            await self.say_line("Your table is booked, sir." if result["status"] == "booked" else "Cancelled, sir.")
            return
        if op in self._DIRECT_EXEC:
            self.notes.append(f"Stephen used the HUD to run {op} with {args}.")
            if op == "gmail_trash_now":
                await briefing.forget_messages(args.get("message_ids") or [])
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
        q = self.briefing_question
        if q is not None and not q.done():  # the briefing asked him something: this is the answer, not a new turn
            q.set_result(text)
            return

        pending = [a for a in store.pending_actions()]
        # The hologram is waiting (and no confirm card outranks it): "keep it" / "lose it" answers it directly.
        fv = classify_forge(text) if self.forge_pending and not pending else None
        if fv:
            await self.cancel_turn()
            self.forge_pending = False
            await self.send({"type": "forge_action", "action": fv})
            await self.state("idle")
            return
        verdict = classify_confirmation(text) if pending else None
        if verdict:
            await self.cancel_turn()
            await self.resolve_action(pending[-1]["id"], verdict == "confirm", f"voice:{text}" if source == "voice"
                                      else f"typed:{text}")
            return
        if _RESET.match(text):
            await self.reset()
            await self.say_line("A clean slate, sir.")
            return
        if showcase.is_sleep_request(text):
            # as a task: the goodnight line must finish playing first, and playback_done arrives on this same loop
            await self.cancel_turn()
            self.turn_task = asyncio.create_task(
                self.go_to_sleep("Goodnight, sir." if re.search(r"night", text, re.I) else "Very good, sir."))
            return
        if self.sleeping:  # anything typed or said directly (push-to-talk) wakes it the normal way
            if showcase.is_morning(text):
                await self.power_on()
                return
            await self.wake_up()
        if showcase.is_morning(text):
            await self.power_on()
            return
        if showcase.is_trigger(text):
            await self.start_showcase("demo")
            return
        if showcase.is_tour_request(text) and support_available():
            await self.start_showcase("support_tour")
            return
        quick = quick_answer(text, settings.timezone)
        if quick:  # time/date: answer locally, no model round trip
            await self.cancel_turn()
            await self.state("thinking")
            await self.say_line(quick)
            return
        await self.cancel_turn()  # barge-in
        self.turn_task = asyncio.create_task(self._turn(self._with_context(text)))

    async def listen_for_answer(self, timeout_s: float) -> None:
        """The briefing asked a question: open the mic for his reply without the wake word (if voice is on)."""
        mic = self.mic
        if mic is not None and mic.wake_enabled and mic.wake.available and mic.mode == "wake":
            await mic.listen_follow_up(timeout_s)

    def answer_timed_out(self) -> None:
        q = self.briefing_question
        if q is not None and not q.done():
            q.set_result("")

    async def start_showcase(self, which: str, intro_delay: float = 0.0) -> None:
        """The preset demo, or the narrated support-model tour, run as this session's turn (Esc cancels it)."""
        await self.cancel_turn()
        if which == "support_tour":
            if not support_available():
                await self.say_line("The support blueprint hasn't been built yet, sir.")
                return
            coro = showcase.support_tour(self)
        else:
            coro = showcase.run(self, intro_delay=intro_delay)

        async def guarded():
            self.pending_input = None
            try:
                await coro
                if self.pending_input:  # he asked for something mid-briefing: handle it as a normal turn now
                    text, self.pending_input = self.pending_input, None
                    asyncio.create_task(self.user_input(text, source="voice"))
            except asyncio.CancelledError:
                await self.send({"type": "showcase", "active": False})
                raise
            except Exception:
                log.exception("showcase %s failed", which)
                await self.send({"type": "showcase", "active": False})
                await self.say_line("My apologies, sir. The presentation hit a snag.")
        self.turn_task = asyncio.create_task(guarded())

    # ------------------------------------------------------------------ files & projects in the conversation
    def attach(self, items: list[dict]) -> None:
        for it in items:
            if it.get("id") and all(a["id"] != it["id"] for a in self.attachments):
                self.attachments.append(it)
        if items:
            self.focus = {"type": "file", **items[-1]}

    def set_focus(self, focus: dict | None) -> None:
        self.focus = focus

    def _with_context(self, text: str) -> str:
        """Prefix the model input with what he just attached / what's on screen, so "fix this", "what's in
        it", "add a column" resolve without him naming ids. The HUD shows only his words."""
        lines = []
        if self.attachments:
            lines.append("[Stephen attached: " + "; ".join(
                f"{a.get('filename')} (artifact_id={a['id']}, kind={a.get('kind')})" for a in self.attachments) + "]")
            self.attachments = []
        elif self.focus and self.focus.get("type") == "file":
            lines.append(f"[On screen: file {self.focus.get('filename')} (artifact_id={self.focus.get('id')})]")
        if self.focus and self.focus.get("type") == "project":
            lines.append(f"[Active project: {self.focus.get('name')} at {self.focus.get('root')}]")
        return ("\n".join(lines) + "\n" + text) if lines else text

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
            if self._tour_pending:  # support_model(tour=True): narrate the blueprint once the reply is queued
                self._tour_pending = False
                await showcase.support_tour(self, intro=None, show_card=False)
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
            elif self._state == "speaking" and not self._audio_out:
                # The HUD already finished playing (playback_done arrived while the turn was still wrapping
                # up, so it was ignored there): settle to idle instead of sticking on "speaking".
                await self.state("idle")

    # ------------------------------------------------------------------ cards from the tool feed
    async def _poll_feed(self, turn_id: str, head: int) -> None:
        self._feed_pos = max(head, self._feed_pos)
        while True:
            await asyncio.sleep(0.3)
            await self._flush_feed(turn_id)

    async def _flush_feed(self, turn_id: str, only: set[str] | None = None) -> None:
        for item in store.feed_since(self._feed_pos):
            self._feed_pos = item["seq"]
            if only is not None and item["tool"] not in only:
                continue  # between turns: other sessions' tool calls aren't his displays
            res = item.get("result") if isinstance(item.get("result"), dict) else {}
            if item["tool"] == "support_model" and (item.get("args") or {}).get("tour"):
                self._tour_pending = True
            if item["tool"] in ("code_map", "code_annotate", "code_change") and res.get("root"):
                self.focus = {"type": "project", "root": res["root"], "name": res.get("name")}
            elif res.get("artifact"):
                a = res["artifact"]
                self.focus = {"type": "file", "id": a["id"], "filename": a["filename"], "kind": a["kind"]}
            for c in V.cards_from_feed(item):
                await self.send({"type": "card", "turn_id": turn_id, "card": c})
            if item["tool"] == "video_analyze" and res.get("announce"):
                # a long video finished after the turn ended: say the result, and keep it for follow-ups
                self.notes.append(f"Video analysis finished ({(res.get('video') or {}).get('filename')}): {res['announce']}")
                if turn_id == "background" and not self.sleeping:
                    asyncio.create_task(self.say_line(res["announce"]))
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
            await self._flush_feed("direct", only=BACKGROUND_TOOLS)  # e.g. the "Booked" confirmation display
            self.notes.append(f"Stephen CONFIRMED and it was executed: {action['summary']} -> {out.get('result')}")
            if action["kind"] in ("gmail_trash", "gmail_delete_permanently"):
                await briefing.forget_messages(action["params"].get("message_ids") or [])
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
        """Synthesize sentences in order, one request in flight (VoiceStudio serializes anyway), and send each
        clip the moment it's ready so the HUD always has the next sentence queued before the current ends."""
        seq = 0
        spoke = False
        while True:
            sentence = await q.get()
            if sentence is None:
                break
            # merge sentences that are already waiting (up to a sensible size): one request, natural prosody
            while not q.empty() and len(sentence) < 220:
                nxt = q.get_nowait()
                if nxt is None:
                    q.put_nowait(None)
                    break
                sentence = f"{sentence} {nxt}"
            try:
                audio = await self.voice.tts(sentence)
            except Exception as e:
                log.warning("tts failed: %s", e)
                await self.send({"type": "voice_error", "error": str(e)[:200]})
                continue
            if self._state != "confirm":
                await self.state("speaking")
            self._audio_out = True
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
        self._audio_out = True
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
