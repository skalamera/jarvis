"""Wake / sleep phrases, the power-on cue, and the narrated support-model tour.

(The old preset showcase briefing is gone: every briefing is now the time-of-day one in morning.py.)
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import time
import wave
from typing import TYPE_CHECKING, Any, Callable

from jarvis_google import store
from . import visuals as V
from .voice import b64

if TYPE_CHECKING:  # pragma: no cover
    from .session import Session

log = logging.getLogger("jarvis.showcase")


_TOUR = re.compile(
    r"\b(explain|walk me through|walk through|run me through|how does|how do(?:es)?|tell me about|give me (?:an |the )?"
    r"overview|overview of|break down|show me|describe|what is)\b.*\b((?:3|three)[- ]?tier(?:ed)?|support (?:model|system|"
    r"operation|stack|setup|pipeline|architecture|workflow)|tiered support)\b", re.I)
_SPECIFIC = re.compile(r"\b(jamie|hadrian|tier ?(?:1|2|3|one|two|three)\b(?![- ]?tier)|draft[- ]reply|feature request|"
                       r"loop guard|takeover|sybill|budget|payload|on hold|webhook|linear)\b", re.I)


POWER_ON_S = 8.0  # length of the HUD's cinematic power-on sequence; the briefing gathers its data meanwhile
# The power-on phrase "Wake up, Daddy's home" (and the ways Whisper writes it: "daddy is", "dad's", "daddies").
WAKE_PHRASE = "Wake up, Daddy's home"
_MORNING_RE = re.compile(r"\bwake[\s,-]*up\b[\s,.!]*(?:jarvis[\s,.!]*)?(?:daddy|daddie|dad)(?:'?s|\s+is|ies)?[\s,.!]*home\b", re.I)
_SLEEP_RE = re.compile(r"^\W*(?:(?:ok|okay|alright|right|thanks)[\s,]+)?(?:jarvis[\s,]+)?(?:go to sleep|goodnight|good night|"
                       r"sleep mode|power down|that'?s all for (?:now|tonight))(?:[\s,.!]+jarvis)?\W*$", re.I)


def is_morning(text: str) -> bool:
    return bool(_MORNING_RE.search(text or ""))


def is_sleep_request(text: str) -> bool:
    return bool(_SLEEP_RE.search(text or ""))


def is_tour_request(text: str) -> bool:
    """Broad 'explain the 3-tier support model' asks get the instant narrated tour; specific questions go to the model."""
    t = text or ""
    return bool(_TOUR.search(t)) and not _SPECIFIC.search(t.replace("3 tier", "").replace("three tier", ""))


# ---------------------------------------------------------------- small helpers


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return max(1.5, len(data) / 48000)


def _grab(fn: Callable, *a, **kw) -> tuple[Any, list[dict]]:
    """Run a tool with the feed captured: (result, the feed items it would have published)."""
    with store.capture() as items:
        r = fn(*a, **kw)
    return r, list(items)


def chunk_text(text: str, limit: int = 260) -> list[str]:
    """Sentence groups of <= limit chars: short TTS requests (fast) that still sound natural."""
    sents = V.split_sentences(text)
    out, cur = [], ""
    for s in sents:
        if cur and len(cur) + 1 + len(s) > limit:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- display helpers
def _cards(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        out.extend(V.cards_from_feed(it))
    return out


# ---------------------------------------------------------------- runner
async def _synth(session: "Session", text: str) -> bytes | None:
    if not session.speak:
        return None
    try:
        return await session.voice.tts(text)
    except Exception as e:
        log.warning("showcase tts failed: %s", e)
        return None


class Narrator:
    """Plays lines in order: one clip at a time, each awaited until the HUD reports playback done."""

    def __init__(self, session: "Session", turn_id: str):
        self.s, self.turn_id, self.seq = session, turn_id, 0

    async def play(self, text: str, audio: bytes | None) -> None:
        s = self.s
        await s.send({"type": "assistant_final", "turn_id": f"{self.turn_id}_{self.seq}", "text": text})
        if audio is None:
            await s.send({"type": "system_caption", "text": text})
            await asyncio.sleep(min(9.0, 1.2 + len(text) / 17))
            self.seq += 1
            return
        secs = wav_seconds(audio)
        s._played.clear()
        await s.state("speaking")
        await s.send({"type": "speech", "turn_id": self.turn_id, "seq": self.seq, "text": text, "audio": b64(audio),
                       "mime": "audio/wav"})
        self.seq += 1
        t0 = time.monotonic()
        while True:
            left = secs + 6 - (time.monotonic() - t0)
            if left <= 0:
                break
            try:
                await asyncio.wait_for(s._played.wait(), timeout=left)
            except asyncio.TimeoutError:
                break
            if time.monotonic() - t0 >= secs * 0.85:
                break
            s._played.clear()  # a stale "done" from an earlier clip


async def _ui(session: "Session", op: dict) -> None:
    await session.send({"type": "ui", **op})


async def support_tour(session: "Session", intro: str | None = "Allow me to walk you through it, sir.",
                       show_card: bool = True) -> None:
    """Narrated walkthrough of the support blueprint: each paragraph switches the display to the matching tab."""
    from jarvis_google import support as SU
    turn_id = "tour_" + time.strftime("%H%M%S")
    narr = Narrator(session, turn_id)
    paras = await asyncio.to_thread(SU.narration)
    if show_card:
        bp, items = await asyncio.to_thread(_grab, SU.show, "overview", True)
        cards = _cards(items)
        for c in cards:
            await session.send({"type": "card", "turn_id": turn_id, "card": c})
        if cards:
            await _ui(session, {"op": "expand", "card_id": cards[0]["id"]})
    else:  # the model's support_model call already put the card up
        await _ui(session, {"op": "expand_key", "key": "support:blueprint"})
    chunks: list[tuple[dict | None, str]] = []
    if intro:
        chunks.append((None, intro))
    for p in paras:
        views = p.get("views") or [{"tab": p["tab"], "tier": p.get("tier")}]
        parts = chunk_text(p["text"], max(110, min(220, len(p["text"]) // len(views) + 20)))
        for j, ch in enumerate(parts):
            # spread the paragraph's views across its spoken chunks: the display moves with the narration
            vi = min(len(views) - 1, (j * len(views)) // max(1, len(parts)))
            prev = min(len(views) - 1, ((j - 1) * len(views)) // max(1, len(parts))) if j else -1
            op = {"op": "support_tab", **views[vi], "para": True if j == 0 else False} if vi != prev else None
            chunks.append((op, ch))
    chunks.append(({"op": "support_tab", "tab": "overview"}, "That's the whole picture, sir. The blueprint stays on "
                   "screen, and you can ask me about any part of it."))
    await session.send({"type": "showcase", "active": True, "step": 0, "total": len(paras), "label": "SUPPORT MODEL",
                        "mode": "tour"})
    futs: list[asyncio.Future] = []
    loop = asyncio.get_running_loop()
    futs = [loop.create_future() for _ in chunks]

    async def synth_all():
        for f, (_, text) in zip(futs, chunks):
            f.set_result(await _synth(session, text))

    synth = asyncio.create_task(synth_all())
    step = 0
    try:
        for f, (op, text) in zip(futs, chunks):
            if op:
                if op.pop("para", False):
                    step += 1
                await _ui(session, op)
                label = {"architecture": "BLUEPRINT", "flows": "WORKFLOW"}.get(op.get("tab", ""), str(op.get("tab", "")).upper())
                if op.get("tier"):
                    label = f"TIER {op['tier']}"
                await session.send({"type": "showcase", "active": True, "step": min(step, len(paras)),
                                    "total": len(paras), "label": label, "mode": "tour"})
            await narr.play(text, await f)
    finally:
        synth.cancel()
        await session.send({"type": "showcase", "active": False})
        await session.state("idle")
