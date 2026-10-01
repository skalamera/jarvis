"""Measure spoken latency end-to-end over the Core WebSocket (same stream the HUD plays).

Sends a typed question, records when each speech clip arrives and how long it is, and simulates back-to-back
playback to report: time to first audio, and any silent gap between clips (a clip arriving after the previous
one finished playing).

    .venv/bin/python tests/e2e_speech_timing.py "What's the weather like today?" ["Any new Slack messages?" ...]
"""
import asyncio
import base64
import io
import json
import sys
import time
import wave

import websockets

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from jarvis_core.config import settings  # noqa: E402


def clip_seconds(b64: str) -> float:
    with wave.open(io.BytesIO(base64.b64decode(b64))) as w:
        return w.getnframes() / w.getframerate()


async def ask(q: str) -> None:
    url = f"ws://{settings.host}:{settings.port}/ws" + (f"?token={settings.token}" if settings.token else "")
    async with websockets.connect(url, max_size=None) as ws:
        # drain the hello burst
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=1.5)
        except asyncio.TimeoutError:
            pass
        t0 = time.monotonic()
        await ws.send(json.dumps({"type": "user_text", "text": q, "source": "voice"}))
        clips, reply, final_at = [], "", None
        while time.monotonic() - t0 < 120:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            except asyncio.TimeoutError:
                break
            t = m.get("type")
            if t == "speech" and m.get("audio"):
                clips.append((time.monotonic() - t0, clip_seconds(m["audio"]), bool(m.get("ack")), m.get("text", "")))
            elif t == "assistant_final":
                final_at, reply = time.monotonic() - t0, m.get("text", "")
            elif t == "speech_end" and m.get("turn_id") != "system":
                break
    print(f"\n{q!r}")
    if not clips:
        print("   no speech", reply[:120])
        return
    play_end = 0.0
    gaps = []
    for i, (arr, dur, ack, text) in enumerate(clips):
        start = max(arr, play_end)
        gap = start - play_end if i else 0.0
        if i:
            gaps.append(gap)
        print(f"   clip {i}{' (ack)' if ack else ''}: arrives {arr:5.2f}s  plays {start:5.2f}s  len {dur:4.1f}s"
              f"  gap {gap:4.2f}s | {text[:70]}")
        play_end = start + dur
    first_real = next((c for c in clips if not c[2]), clips[0])
    print(f"   first audio {clips[0][0]:.2f}s | first answer audio {first_real[0]:.2f}s | text done {final_at or 0:.2f}s"
          f" | total silent gaps {sum(gaps):.2f}s | finished speaking {play_end:.1f}s")


async def main():
    for q in sys.argv[1:]:
        await ask(q)


if __name__ == "__main__":
    asyncio.run(main())
