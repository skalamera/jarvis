"""Speech text normalization and clip trimming (no VoiceStudio needed)."""
import io
import wave

import numpy as np
import pytest

from jarvis_core.spoken import int_words, ordinal_words, spoken
from jarvis_core.voice import Voice, trim_silence


@pytest.mark.parametrize("n,w", [(0, "zero"), (13, "thirteen"), (42, "forty-two"), (100, "one hundred"),
                                  (3506, "three thousand five hundred six"), (1_200_000, "one million two hundred thousand")])
def test_int_words(n, w):
    assert int_words(n) == w


@pytest.mark.parametrize("n,w", [(1, "first"), (2, "second"), (3, "third"), (12, "twelfth"), (20, "twentieth"),
                                  (21, "twenty-first"), (29, "twenty-ninth"), (30, "thirtieth")])
def test_ordinals(n, w):
    assert ordinal_words(n) == w


@pytest.mark.parametrize("text,expect", [
    # the exact misreads measured on the TTS engines
    ("a direct deposit of $3,506.99 on 9/29", "a direct deposit of three thousand five hundred six dollars and ninety-nine cents on September twenty-ninth"),
    ("ticket #24680 is On You", "ticket two four six eight zero is On You"),
    ("at 2:15 PM on Wed, Oct 1st", "at two fifteen P M on Wednesday, October first"),
    ("up 1.2% at 6,512", "up one point two percent at six thousand five hundred twelve"),
    ("EPS was $0.50 vs. $0.45", "EPS was fifty cents versus forty-five cents"),
    ("revenue of $2.5B", "revenue of two point five billion dollars"),
    ("It is 62°F", "It is sixty-two degrees"),
    ("since 2026-09-01", "since September first, twenty twenty-six"),
    ("meet at 3pm", "meet at three P M"),
    ("the Q3 report", "the Q three report"),
    ("at 10:05 am", "at ten oh five A M"),
    ("down -2.1%", "down minus two point one percent"),
])
def test_spoken(text, expect):
    assert spoken(text) == expect


def test_spoken_leaves_words_and_codes_alone():
    for t in ["Right away, sir.", "COVID-19 and B2B stay.", "Version 1.2.3 of v2 shipped."]:
        assert spoken(t) == t


def _wav(samples: np.ndarray, sr: int = 24000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.astype(np.int16).tobytes())
    return b.getvalue()


def test_trim_silence_keeps_speech_drops_padding():
    sr = 24000
    tone = (np.sin(np.arange(sr) / 5) * 8000)
    pad = np.zeros(int(sr * 0.25))
    out = trim_silence(_wav(np.concatenate([pad, tone, pad]), sr))
    with wave.open(io.BytesIO(out)) as w:
        dur = w.getnframes() / w.getframerate()
    assert 1.0 <= dur <= 1.0 + 0.03 + 0.06 + 0.01   # the tone plus the kept breath
    assert trim_silence(b"not a wav") == b"not a wav"
    assert trim_silence(_wav(np.zeros(sr), sr)) == _wav(np.zeros(sr), sr)   # all silence: unchanged


def test_request_body_preset_vs_profile():
    kokoro = Voice("http://x", "k", "bm_lewis", "mlx-audio", language="en-gb")
    b = kokoro.request_body("Costs $5.")
    assert b["model"] == "mlx-audio" and b["voice"] == "bm_lewis" and b["language"] == "en-gb"
    assert b["input"] == "Costs five dollars." and "num_step" not in b and "instruct" not in b
    butler = Voice("http://x", "k", "JARVIS Butler", "omnivoice")
    b = butler.request_body("Hi.")
    assert b["model"] == "omnivoice" and b["num_step"] == 8 and b["instruct"]   # profile not resolved yet



def test_resy_pronounced_rez_ee():
    from jarvis_core.spoken import spoken
    assert spoken("Booked on Resy, sir.") == "Booked on Rezzy, sir."
    assert "Rezzy" in spoken("your RESY login") and spoken("Resynchronize") == "Resynchronize"
