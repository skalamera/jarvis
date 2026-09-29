"""Voice end-to-end: synthesize a spoken request, stream it as 16 kHz PCM mic frames into /ws,
and verify wake word -> capture -> STT -> Hermes turn -> spoken reply.

  .venv/bin/python tests/e2e_voice.py "Hey Jarvis. What's on my work calendar today?" [--ptt]
"""
import asyncio
import io
import json
import sys
import time
import wave

import httpx
import numpy as np
import websockets

VOICE = "http://localhost:3900"


def synth(text: str) -> np.ndarray:
    r = httpx.post(f"{VOICE}/v1/audio/speech", headers={"Authorization": "Bearer local"}, timeout=120,
                   json={"model": "tts-1", "voice": "echo", "input": text, "response_format": "wav"})
    r.raise_for_status()
    w = wave.open(io.BytesIO(r.content))
    sr, ch = w.getframerate(), w.getnchannels()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    idx = np.arange(0, len(a), sr / 16000.0)  # linear resample to 16 kHz
    return np.interp(idx, np.arange(len(a)), a).astype(np.int16)


async def main():
    text = sys.argv[1]
    ptt = "--ptt" in sys.argv
    pcm = synth(text)
    pad = np.zeros(16000 * 1, dtype=np.int16)
    audio = np.concatenate([pad, pcm, np.zeros(16000 * 2, dtype=np.int16)])  # trailing silence ends capture
    print(f"synthesized {len(pcm) / 16000:.1f}s of speech")
    t0 = time.time()
    async with websockets.connect("ws://127.0.0.1:8765/ws", max_size=64 * 2**20) as ws:
        events = []

        async def reader():
            async for raw in ws:
                m = json.loads(raw)
                t = m["type"]
                if t in ("mic_level", "assistant_delta"):
                    continue
                el = round(time.time() - t0, 2)
                if t == "speech":
                    print(f"[{el:6}] speech seq={m['seq']} {m['text']!r}")
                elif t == "card":
                    print(f"[{el:6}] card {m['card']['kind']} {m['card']['title']!r}")
                else:
                    print(f"[{el:6}] {t} {json.dumps({k: v for k, v in m.items() if k not in ('type', 'accounts', 'audio')})[:220]}")
                events.append((el, m))
                if t == "speech_end" and m.get("turn_id") != "system":
                    return

        rd = asyncio.create_task(reader())
        await asyncio.sleep(0.5)
        if ptt:
            await ws.send(json.dumps({"type": "ptt_start"}))
        chunk = 1280
        for i in range(0, len(audio), chunk):  # real-time pacing, like a microphone
            await ws.send(audio[i:i + chunk].tobytes())
            await asyncio.sleep(chunk / 16000)
            if ptt and i >= len(pad) + len(pcm):
                await ws.send(json.dumps({"type": "ptt_end"}))
                ptt = False
        try:
            await asyncio.wait_for(rd, 150)
        except asyncio.TimeoutError:
            print("TIMEOUT")
        types = [m["type"] for _, m in events]
        print("\nwake:", "wake" in types, "| transcript:", next((m["text"] for _, m in events if m["type"] == "transcript"), None))
        print("reply:", next((m["text"] for _, m in events if m["type"] == "assistant_final"), None))


asyncio.run(main())
