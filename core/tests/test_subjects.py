import json

import pytest

from jarvis_google import artifacts as A, generate as GEN, store, subjects as SJ


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(A, "ART_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(SJ, "DIR", tmp_path / "subjects")
    monkeypatch.setattr(store, "audit", lambda *a, **k: None)
    monkeypatch.setattr(store, "record_result", lambda *a, **k: None)
    monkeypatch.setattr(SJ, "_describe", lambda imgs, name, hint: f"silver coupe ({len(imgs)} photos)")
    monkeypatch.setattr(GEN, "_key", lambda: "k")


def _photo(name="car.jpg"):
    return A.save_upload(name, b"\xff\xd8\xff fakejpeg", "image/jpeg")["id"]


def test_save_and_find_by_name_and_alias():
    r = SJ.subject_save("my car", [_photo()], description="2003 CL600", aliases=["the CL600", "the Benz"])
    assert r["photos"] == 1 and "silver coupe" in r["description"]
    for q in ("my car", "car", "The CL600", "the benz"):
        assert SJ.find(q)["name"] == "my car"
    assert SJ.find("my dog") is None


def test_photos_are_copies_and_capped_at_three():
    a = _photo()
    SJ.subject_save("my car", [a])
    A._dir(a)  # still in the workspace; discarding the upload must not break the subject
    for _ in range(4):
        SJ.subject_save("my car", [_photo()])
    s = SJ.find("my car")
    assert len(s["photos"]) == 3 and len(SJ.photos(s)) == 3


def test_rejects_non_images():
    doc = A.save_upload("notes.txt", b"hello", "text/plain")["id"]
    with pytest.raises(ValueError):
        SJ.subject_save("my car", [doc])


def test_unknown_subject_error_lists_saved():
    SJ.subject_save("my car", [_photo()])
    with pytest.raises(ValueError, match="my car"):
        SJ.resolve("my boat")


def test_forget_is_recoverable(tmp_path):
    SJ.subject_save("my car", [_photo()])
    SJ.subject_forget("my car")
    assert SJ.find("my car") is None and any((tmp_path / "subjects_trash").iterdir())


class _R:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, json.dumps(body)

    def json(self):
        return self._b


def test_image_generate_sends_reference_photos(monkeypatch):
    SJ.subject_save("my car", [_photo(), _photo()])
    sent = {}

    def post(url, **kw):
        sent["url"], sent["json"] = url, kw["json"]
        return _R(200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]})
    monkeypatch.setattr(GEN.httpx, "post", post)
    monkeypatch.setattr(A, "_show", lambda *a, **k: None)
    GEN.image_generate("my car on a mountain road", subject="my car")
    parts = sent["json"]["contents"][0]["parts"]
    assert sum("inlineData" in p for p in parts) == 2
    assert "exact subject" in parts[0]["text"] and GEN.IMAGE_MODELS["best"] in sent["url"]


def test_video_generate_uses_veo_reference_images(monkeypatch):
    SJ.subject_save("my car", [_photo()])
    sent = {}

    def post(url, **kw):
        sent["url"], sent["json"] = url, kw["json"]
        return _R(200, {"name": "operations/x"})
    monkeypatch.setattr(GEN.httpx, "post", post)
    monkeypatch.setattr(GEN, "_spawn", lambda job: None)
    monkeypatch.setattr(GEN, "JOBS", store.STATE_DIR / "jobs.json")
    r = GEN.video_generate("my car drifting", aspect_ratio="9:16", duration_s=4, subject="my car")
    inst = sent["json"]["instances"][0]
    assert inst["referenceImages"][0]["referenceType"] == "asset"
    assert sent["json"]["parameters"] == {"aspectRatio": "16:9", "durationSeconds": 8}
    assert r["subject_via"] == "reference_images"


def test_video_falls_back_to_first_frame_when_references_rejected(monkeypatch):
    SJ.subject_save("my car", [_photo()])
    calls = []

    def post(url, **kw):
        calls.append((url, kw["json"]))
        if "predictLongRunning" in url and "referenceImages" in kw["json"]["instances"][0]:
            return _R(400, {"error": {"message": "referenceImages not supported"}})
        if "generateContent" in url:
            return _R(200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]})
        return _R(200, {"name": "operations/y"})
    monkeypatch.setattr(GEN.httpx, "post", post)
    monkeypatch.setattr(GEN, "_spawn", lambda job: None)
    monkeypatch.setattr(GEN, "JOBS", store.STATE_DIR / "jobs.json")
    monkeypatch.setattr(A, "_show", lambda *a, **k: None)
    r = GEN.video_generate("my car drifting", subject="my car")
    assert r["subject_via"] == "first_frame"
    assert "image" in calls[-1][1]["instances"][0]


def test_sheet_goes_to_image_model_only(monkeypatch, tmp_path):
    SJ.subject_save("my car", [_photo()])
    col = tmp_path / "collage.png"
    col.write_bytes(b"\x89PNG fake")
    SJ.set_sheet("my car", str(col))
    sent = {}

    def post(url, **kw):
        sent["json"] = kw["json"]
        return _R(200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "aGk="}}]}}]})
    monkeypatch.setattr(GEN.httpx, "post", post)
    monkeypatch.setattr(A, "_show", lambda *a, **k: None)
    GEN.image_generate("my car at night", subject="my car")
    parts = sent["json"]["contents"][0]["parts"]
    assert sum("inlineData" in p for p in parts) == 2 and "many angles" in parts[0]["text"]
