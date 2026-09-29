import asyncio

import numpy as np
import pytest

from jarvis_core.audio import FRAME, MicPipeline


class FakeWake:
    available = True
    error = None
    threshold = 0.5

    def __init__(self):
        self.fire_at = None
        self.n = 0

    def score(self, frame):
        self.n += 1
        return 0.9 if self.fire_at is not None and self.n == self.fire_at else 0.0

    def reset(self):
        pass


def tone(seconds, amp=8000):
    t = np.arange(int(16000 * seconds)) / 16000
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()


def silence(seconds):
    return np.zeros(int(16000 * seconds), dtype=np.int16).tobytes()


@pytest.fixture()
def rig():
    events, utts = [], []

    async def on_event(e):
        events.append(e)

    async def on_utt(pcm, via):
        utts.append((len(pcm), via))

    wake = FakeWake()
    return MicPipeline(on_event, on_utt, wake), wake, events, utts


async def test_wake_then_capture_until_silence(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    wake.fire_at = 5
    await mic.feed(silence(0.6))          # wake fires on frame 5
    assert any(e["type"] == "wake" for e in events)
    await mic.feed(tone(1.0))             # the command
    await mic.feed(silence(1.2))          # end-pointing silence
    await asyncio.sleep(0.01)
    assert utts and utts[0][1] == "wake"
    assert 0.9 * 16000 * 2 < utts[0][0] < 2.4 * 16000 * 2
    assert mic.mode == "wake"


async def test_wake_without_speech_times_out_empty(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    wake.fire_at = 1
    await mic.feed(silence(6.5))
    assert not utts
    assert any(e["type"] == "listening_end" and e["empty"] for e in events)


async def test_push_to_talk(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(False)
    await mic.ptt_start()
    await mic.feed(tone(0.8))
    await mic.ptt_end()
    await asyncio.sleep(0.01)
    assert utts and utts[0][1] == "ptt"


async def test_suspended_blocks_wake(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    mic.suspended = True
    wake.fire_at = 2
    await mic.feed(silence(FRAME * 4 / 16000))
    assert not any(e["type"] == "wake" for e in events)
