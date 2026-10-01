"""Gemini image / Veo video generation: files, progress cards, background video jobs (HTTP is faked)."""
import base64
import io
import json

import pytest
from PIL import Image

from jarvis_core import visuals as V
from jarvis_google import artifacts as AR, generate as G, store


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    monkeypatch.setattr(AR, "ART_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(G, "JOBS", tmp_path / "gen_jobs.json")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    return tmp_path


def _png(color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(buf, "PNG")
    return buf.getvalue()


class R:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code, self._b, self.content, self.text = status, body or {}, content, json.dumps(body or {})

    def json(self):
        return self._b


def test_image_generate_creates_shown_file(ws, monkeypatch):
    sent = {}

    def post(url, **kw):
        sent.update(url=url, body=kw["json"])
        return R(body={"candidates": [{"content": {"parts": [{"text": "Here you go."}, {"inlineData": {
            "mimeType": "image/png", "data": base64.b64encode(_png()).decode()}}]}}]})
    monkeypatch.setattr(G.httpx, "post", post)
    with store.capture() as items:
        r = G.image_generate("A red CL600 on a rainy Manhattan street at night", aspect_ratio="16:9")
    assert "gemini-3.1-flash-image" in sent["url"] and sent["body"]["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9"}
    assert r["kind"] == "image" and r["filename"].startswith("a-red-cl600") and r["filename"].endswith(".png")
    m = AR._meta(r["id"])
    assert m["versions"][0]["source"] == "generated" and AR.current_path(m).read_bytes() == _png()
    kinds = [c["kind"] for it in items for c in V.cards_from_feed(it)]
    assert kinds == ["generating", "artifact"]  # progress first, then the finished file under the same key
    keys = {it["result"]["key"] for it in items}
    assert keys == {f"artifact:{r['id']}"}


def test_image_rework_adds_version(ws, monkeypatch):
    m = AR.save_upload("car.png", _png())
    monkeypatch.setattr(G.httpx, "post", lambda url, **kw: R(body={"candidates": [{"content": {"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(_png((0, 0, 200))).decode()}}]}}]}))
    with store.capture():
        r = G.image_generate("make it night", source_artifact_id=m["id"])
    m2 = AR._meta(m["id"])
    assert r["version"] == 2 and m2["versions"][-1]["note"].startswith("AI: make it night")
    with Image.open(AR.current_path(m2)) as im:  # converted back to the file's own format
        assert im.format == "PNG"


def test_image_blocked_shows_failure(ws, monkeypatch):
    monkeypatch.setattr(G.httpx, "post", lambda url, **kw: R(body={"candidates": [{"finishReason": "IMAGE_SAFETY",
                                                                                   "content": {"parts": []}}]}))
    with store.capture() as items, pytest.raises(RuntimeError, match="IMAGE_SAFETY"):
        G.image_generate("something")
    assert items[-1]["result"]["generating"]["error"]
    assert V.cards_from_feed(items[-1])[0]["title"] == "Image · failed"


def test_video_job_lands_as_playable_file(ws, monkeypatch):
    monkeypatch.setattr(G, "POLL_S", 0)
    monkeypatch.setattr(G.httpx, "post", lambda url, **kw: R(body={"name": "models/veo/operations/op1"}))
    polls = iter([R(body={"done": False}), R(body={"done": True, "response": {"generateVideoResponse": {
        "generatedSamples": [{"video": {"uri": "https://example.test/v.mp4"}}]}}})])
    monkeypatch.setattr(G.httpx, "get", lambda url, **kw: next(polls) if "operations" in url else
                        R(content=b"\x00\x00\x00\x18ftypmp42fakevideo"))
    monkeypatch.setattr(G, "_spawn", lambda job: None)  # run the watcher inline instead of a thread
    with store.capture() as items:
        r = G.video_generate("Slow push-in on a glowing arc reactor", duration_s=5)
        job = json.loads(G.JOBS.read_text())[r["artifact_id"]]
        G._watch(job)
    assert r["started"] and r["duration_s"] in (4, 6)
    m = AR._meta(r["artifact_id"])
    assert m["kind"] == "video" and AR.view(m)["type"] == "video"
    assert json.loads(G.JOBS.read_text())[r["artifact_id"]]["status"] == "done"
    assert [c["kind"] for it in items for c in V.cards_from_feed(it)] == ["generating", "artifact"]


def test_video_filtered_reports_reason(ws, monkeypatch):
    monkeypatch.setattr(G, "POLL_S", 0)
    monkeypatch.setattr(G.httpx, "post", lambda url, **kw: R(body={"name": "models/veo/operations/op2"}))
    monkeypatch.setattr(G.httpx, "get", lambda url, **kw: R(body={"done": True, "response": {"generateVideoResponse": {
        "raiFilteredReasons": ["blocked by safety filter"]}}}))
    monkeypatch.setattr(G, "_spawn", lambda job: None)
    with store.capture() as items:
        r = G.video_generate("x")
        G._watch(json.loads(G.JOBS.read_text())[r["artifact_id"]])
    assert json.loads(G.JOBS.read_text())[r["artifact_id"]]["status"] == "failed"
    assert "safety" in items[-1]["result"]["generating"]["error"]
