"""End-to-end gate: drive JARVIS Core over its WebSocket like the HUD does.

  .venv/bin/python tests/e2e_ws.py "question" [--speak] [--out dir]
Prints every protocol event (audio elided), saves speech WAVs + a transcript JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path

import websockets


async def run(question: str, speak: bool, out: Path, timeout: float, url: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    log: list[dict] = []
    t0 = time.monotonic()
    first_audio = None
    async with websockets.connect(url, max_size=64 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "mic", "enabled": False}))
        await ws.send(json.dumps({"type": "speak", "enabled": speak}))
        sent = False
        final_seen = speech_end = False
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                print("!! timeout")
                break
            m = json.loads(raw)
            t = m.get("type")
            el = round(time.monotonic() - t0, 2)
            if t == "hello" and not sent:
                print(f"[{el}] hello hermes={m['hermes']} voice={m['voice']} accounts={[a['email'] for a in m['accounts']]}")
                await ws.send(json.dumps({"type": "user_text", "text": question}))
                sent, t0 = True, time.monotonic()
                continue
            if t == "speech":
                wav = out / f"speech_{m['turn_id']}_{m['seq']:02d}.wav"
                wav.write_bytes(base64.b64decode(m["audio"]))
                first_audio = first_audio or el
                print(f"[{el}] SPEECH #{m['seq']}: {m['text']}  -> {wav.name}")
                m = {**m, "audio": f"<{wav.name}>"}
            elif t == "assistant_delta":
                continue
            elif t == "card":
                c = m["card"]
                print(f"[{el}] CARD {c['kind']} :: {c['title']} (account={c.get('account')})")
            elif t == "tool":
                print(f"[{el}] TOOL {m['status']:5} {m['label']} {m.get('duration') or ''}")
            elif t == "assistant_final":
                print(f"[{el}] FINAL ({m.get('elapsed')}s): {m['text']}")
                final_seen = True
            elif t in ("mic_level",):
                continue
            else:
                print(f"[{el}] {t} {json.dumps({k: v for k, v in m.items() if k != 'type'})[:200]}")
            log.append({"t": el, **m})
            if t == "speech_end" and m.get("turn_id") != "system":
                speech_end = True
            if final_seen and (not speak or speech_end):
                break
    result = {"question": question, "first_audio_s": first_audio, "events": log}
    (out / "transcript.json").write_text(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--speak", action="store_true")
    ap.add_argument("--out", default=str(Path.home() / ".hermes/cache/scratch/jarvis/e2e"))
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    a = ap.parse_args()
    asyncio.run(run(a.question, a.speak, Path(a.out), a.timeout, a.url))
