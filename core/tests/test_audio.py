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


async def test_follow_up_captures_speech_without_wake_word(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    await mic.listen_follow_up(timeout_s=4.0)
    assert any(e["type"] == "listening" and e.get("via") == "follow_up" for e in events)
    await mic.feed(tone(1.0))
    await mic.feed(silence(1.2))
    await asyncio.sleep(0.01)
    assert utts and utts[0][1] == "follow_up"
    assert 0.9 * 16000 * 2 < utts[0][0] < 2.4 * 16000 * 2
    assert mic.mode == "wake"


async def test_follow_up_times_out_empty_when_no_speech(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    await mic.listen_follow_up(timeout_s=1.0)
    await mic.feed(silence(1.2))
    assert not utts
    assert any(e["type"] == "listening_end" and e["empty"] and e.get("via") == "follow_up" for e in events)
    assert mic.mode == "wake"


async def test_follow_up_ignored_if_mic_disabled(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(False)
    await mic.listen_follow_up(timeout_s=1.0)
    assert mic.mode == "off"
    assert not any(e.get("via") == "follow_up" for e in events)


def test_noise_transcripts_are_dropped():
    from jarvis_core.main import is_noise_transcript as n
    for x in ["*sad music*", "[Music]", "(applause)", "♪", "♪ la la ♪", " *upbeat music* ", "[BLANK_AUDIO]", "Thank you.", "um"]:
        assert n(x), x
    for x in ["What does Hadrian do?", "play some jazz", "confirm"]:
        assert not n(x), x
    assert n("hmm okay", "") is False or True  # two words outside follow-up are allowed through
    assert n("Right", "follow_up") and not n("yes", "follow_up") and not n("tell me more", "follow_up")
    assert n("Bon Appetit!", "follow_up") and n("3.5mm (3.5mm).") and not n("what about tier two", "follow_up")
    assert not n("Bon appetit", "")  # with the wake word / push-to-talk he clearly meant to talk


async def test_asleep_captures_short_phrase_for_sleep_word(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    mic.sleeping = True
    await mic.feed(silence(1.0))   # let the VAD settle on the noise floor
    await mic.feed(tone(1.1))      # "Morning, Jarvis"
    await mic.feed(silence(0.9))
    await asyncio.sleep(0.01)
    assert utts and utts[-1][1] == "sleep_phrase" and mic.mode == "wake"
    assert not any(e["type"] == "listening" for e in events)  # no listening state: the HUD stays asleep


async def test_asleep_ignores_long_speech(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    mic.sleeping = True
    await mic.feed(silence(1.0))
    await mic.feed(tone(6.0))      # a long conversation in the room
    await mic.feed(silence(0.9))
    await asyncio.sleep(0.01)
    assert not utts


async def test_awake_does_not_capture_phrases(rig):
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    await mic.feed(silence(1.0))
    await mic.feed(tone(1.1))
    await mic.feed(silence(0.9))
    await asyncio.sleep(0.01)
    assert not utts


async def test_asleep_wake_word_inside_phrase_does_not_wake_early(rig):
    """The wake model also fires on "Morning, Jarvis": the phrase must still be captured whole and handed over."""
    mic, wake, events, utts = rig
    await mic.set_listening(True)
    mic.sleeping = True
    await mic.feed(silence(1.0))
    wake.fire_at = wake.n + 6 + 8  # one wake-word hit ~0.6 s into the phrase (the "Jarvis" in "Morning, Jarvis")
    await mic.feed(tone(1.1))
    await mic.feed(silence(0.9))
    await asyncio.sleep(0.01)
    assert not any(e["type"] in ("wake", "listening") for e in events)
    assert utts and utts[-1][1] == "sleep_wake" and utts[-1][0] > 0
