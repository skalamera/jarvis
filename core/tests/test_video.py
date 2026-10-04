"""Video understanding: every phone / desktop format converts to a playable H.264 proxy; analysis + Q&A plumbing."""
import shutil
import subprocess

import pytest

from jarvis_google import artifacts as AR
from jarvis_google import video as V

FF = shutil.which("ffmpeg") or "ffmpeg"
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(AR, "ART_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(V.store, "record_result", lambda *a, **k: 0)
    return tmp_path


def _make(path, *codec):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x640:rate=10:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", *codec, "-shortest", str(path)], check=True)


@pytest.mark.parametrize("name,codec", [
    ("IMG_0001.MOV", ["-c:v", "libx265", "-tag:v", "hvc1", "-x265-params", "log-level=error", "-c:a", "aac"]),  # iPhone
    ("Screen_Recording.mp4", ["-c:v", "libx264", "-c:a", "aac"]),                                            # Android
    ("VID_old.3gp", ["-vf", "scale=176:144", "-c:v", "h263", "-c:a", "aac", "-ar", "16000", "-ac", "1"]),     # old Android
    ("clip.webm", ["-c:v", "libvpx-vp9", "-c:a", "libopus"]),
    ("clip.mkv", ["-c:v", "libx264", "-c:a", "aac"]),
    ("clip.avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame"]),
    ("clip.wmv", ["-c:v", "wmv2", "-c:a", "wmav2"]),
])
def test_every_format_converts(sandbox, name, codec):
    src = sandbox / name
    _make(src, *codec)
    assert AR.is_video_name(name)
    m = AR.save_upload_file(name, src)
    assert m["kind"] == "video" and m["mime"].startswith("video/")
    info = V.probe(AR.current_path(m))
    proxy = AR.ART_DIR / m["id"] / "proxy.mp4"
    V._encode(AR.current_path(m), proxy, info)
    out = V.probe(proxy)
    assert out["vcodec"] == "h264" and out["acodec"] == "aac" and abs(out["duration"] - 2) < 0.6


def test_rotation_and_portrait(sandbox):
    src = sandbox / "rot.mp4"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x320:rate=10:duration=1",
                    "-c:v", "libx264", str(sandbox / "land.mp4")], check=True)
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-display_rotation", "-90", "-i", str(sandbox / "land.mp4"),
                    "-c", "copy", str(src)], check=True)
    info = V.probe(src)
    assert info["orientation"] == "portrait" and (info["width"], info["height"]) == (320, 640)
    V._encode(src, sandbox / "p.mp4", info)
    out = V.probe(sandbox / "p.mp4")
    assert (out["width"], out["height"]) == (320, 640) and out["rotation"] == 0   # baked upright


def test_not_a_video(sandbox):
    bad = sandbox / "x.mp4"
    bad.write_bytes(b"not really a video")
    with pytest.raises(ValueError):
        V.probe(bad)


def test_analysis_normalised_and_cached(sandbox, monkeypatch):
    src = sandbox / "s.mp4"
    _make(src, "-c:v", "libx264", "-c:a", "aac")
    m = AR.save_upload_file("s.mp4", src)
    monkeypatch.setattr(V.G, "_upload_file", lambda *a: "files/fake")
    calls = []

    def fake_gen(st, prompt):
        calls.append(prompt)
        if "Question:" in prompt:
            return {"answer": "The card expired.", "moments": [{"t": "0:01", "note": "error"}]}
        return {"title": "Checkout", "type": "screen_recording", "summary": "Tried to pay.",
                "timeline": [{"t": "0:00", "event": "cart"}, {"t": "1:05", "event": "error"}, {"t": "??", "event": "x"}],
                "issues": [{"t": "0:01", "issue": "Payment declined"}], "spoken": "It failed at payment."}
    monkeypatch.setattr(V, "_generate", fake_gen)
    r = V.video_analyze(m["id"])
    assert r["title"] == "Checkout" and r["timeline"][1]["s"] == 65 and r["timeline"][2]["s"] is None
    assert "spoken" not in r and len(calls) == 1
    V.video_analyze(m["id"])
    assert len(calls) == 1                                   # cached
    q = V.video_analyze(m["id"], "why did it fail?")
    assert q["answer"] == "The card expired." and q["moments"][0]["s"] == 1 and len(calls) == 2
    assert V._state(m["id"])["qa"][-1]["q"] == "why did it fail?"


def test_rejects_non_video(sandbox):
    m = AR.save_upload("notes.txt", b"hello")
    with pytest.raises(ValueError):
        V.video_analyze(m["id"])
