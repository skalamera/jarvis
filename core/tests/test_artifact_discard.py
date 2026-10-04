import pytest

from jarvis_google import artifacts as A, store


@pytest.fixture(autouse=True)
def tmp_state(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(A, "ART_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(store, "audit", lambda *a, **k: None)


def test_discard_generated_moves_to_trash(tmp_path):
    m = A.save_upload("cat.png", b"\x89PNG fake", "image/png", source="generated", note="gen")
    r = A.click_discard(m["id"])
    assert "Discarded" in r["text"]
    assert not (tmp_path / "artifacts" / m["id"]).exists()
    assert any((tmp_path / "artifacts_trash").iterdir())


def test_discard_refuses_uploads():
    m = A.save_upload("mine.png", b"\x89PNG fake", "image/png")
    with pytest.raises(ValueError):
        A.click_discard(m["id"])


def test_discard_refuses_edited_generated():
    m = A.save_upload("cat.png", b"\x89PNG fake", "image/png", source="generated")
    A._new_version(m, b"\x89PNG v2", "model", "edit")
    with pytest.raises(ValueError):
        A.click_discard(m["id"])


def test_discard_is_not_an_mcp_tool():
    src = open(A.__file__.replace("artifacts.py", "server.py")).read()
    assert "click_discard" not in src and "artifact_discard" not in src
