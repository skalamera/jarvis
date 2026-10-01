"""Media (YouTube Music / YouTube): queue shaping, video parsing, controls, cards, model brief. Offline."""
from jarvis_core import visuals
from jarvis_google import media as M


class FakeYTM:
    def search(self, q, filter=None, limit=5):
        if filter == "songs":
            return [{"videoId": "AAAAAAAAAAA", "title": "Song One", "artists": [{"name": "Band"}], "album": {"name": "LP"},
                     "duration": "3:05", "thumbnails": [{"url": "https://lh3/x=w60-h60-l90-rj"}, {"url": "https://lh3/x=w120-h120-l90-rj"}]}]
        if filter == "albums":
            return [{"browseId": "MPREb_1", "title": "LP"}]
        return []

    def get_watch_playlist(self, videoId, limit=25):
        return {"tracks": [
            {"videoId": videoId, "title": "Song One", "artists": [{"name": "Band"}], "length": "3:05", "thumbnail": []},
            {"videoId": "BBBBBBBBBBB", "title": "Song Two", "artists": [{"name": "Other"}], "length": "4:00", "thumbnail": [{"url": "t2"}]},
            {"videoId": "BBBBBBBBBBB", "title": "dupe", "artists": []},
            {"title": "no id"},
        ]}

    def get_album(self, bid):
        return {"title": "LP", "year": "1999", "artists": [{"name": "Band"}], "thumbnails": [{"url": "art"}],
                "tracks": [{"videoId": "CCCCCCCCCCC", "title": "Track 1", "artists": [{"name": "Band"}], "duration_seconds": 200, "thumbnails": None},
                           {"videoId": None, "title": "unavailable"}]}


def setup_function(_):
    M._yt = FakeYTM()
    M._cache.clear()


def test_song_gets_radio_queue_deduped_with_song_first():
    r = M.music_search("song one")
    assert r["mode"] == "song" and r["title"] == "Song One" and r["subtitle"] == "Band · LP"
    ids = [t["video_id"] for t in r["queue"]]
    assert ids == ["AAAAAAAAAAA", "BBBBBBBBBBB"]
    assert r["queue"][0]["duration"] == 185 and r["queue"][0]["album"] == "LP"
    assert r["art"].endswith("=w544-h544-l90-rj")  # upscaled art


def test_album_queue_skips_unplayable_tracks():
    r = M.music_search("lp", "albums")
    assert r["mode"] == "album" and r["subtitle"] == "Band · 1999"
    assert [t["title"] for t in r["queue"]] == ["Track 1"] and r["queue"][0]["thumb"] == "art"


def test_music_play_errors_and_brief(monkeypatch):
    monkeypatch.setattr(M.store, "record_result", lambda *a, **k: None)
    assert "error" in M.music_play("  ")
    r = M.music_play("song one")
    b = M.brief("music_play", r)
    assert b["now_playing"] == "Song One by Band" and b["up_next"] == ["Song Two - Other"] and b["queue_length"] == 2
    assert "queue" not in b  # the model gets a short brief, not the whole queue


def test_youtube_video_from_url_and_search(monkeypatch):
    monkeypatch.setattr(M.store, "record_result", lambda *a, **k: None)
    monkeypatch.setattr(M, "youtube_search", lambda q, n=10: [
        {"video_id": "xyzxyzxyz12", "title": "A", "channel": "C", "duration": 60, "views": 5, "live": False, "thumb": "", "url": ""}])
    r = M.youtube_video("https://youtu.be/fJ9rUzIMcZQ?t=3")
    assert r["results"][0]["video_id"] == "fJ9rUzIMcZQ"
    r = M.youtube_video("cats")
    assert r["results"][0]["title"] == "A"
    assert M.brief("youtube_video", r)["playing"]["title"] == "A"
    assert "error" in M.youtube_video("")


def test_video_row_prefers_hq_thumb_and_parses_duration():
    row = M._video_row({"id": "abcdefghijk", "title": "T", "channel": "Ch", "duration": 125.0, "view_count": 10,
                        "thumbnails": [{"url": "https://i.ytimg.com/vi/abcdefghijk/hqdefault.jpg?sqp=1"}, {"url": "https://i.ytimg.com/vi/abcdefghijk/hq720.jpg?sqp=2"}]})
    assert row["thumb"] == "https://i.ytimg.com/vi/abcdefghijk/hq720.jpg" and row["duration"] == 125
    assert row["url"] == "https://www.youtube.com/watch?v=abcdefghijk"


def test_media_control_normalizes_and_validates(monkeypatch):
    monkeypatch.setattr(M.store, "record_result", lambda *a, **k: None)
    assert M.media_control("skip")["action"] == "next"
    assert M.media_control("louder")["action"] == "volume_up"
    assert M.media_control("set_volume", 150)["level"] == 100
    assert "error" in M.media_control("set_volume")
    assert "error" in M.media_control("rewind time")


def test_cards():
    feed = lambda tool, r: visuals.cards_from_feed({"tool": tool, "result": r})
    assert feed("music_play", {"title": "LP", "queue": [{}]})[0]["kind"] == "music"
    v = feed("youtube_video", {"query": "q", "results": [{"title": "Vid"}]})[0]
    assert v["kind"] == "video" and v["title"] == "Vid"
    assert feed("media_control", {"action": "pause"})[0]["kind"] == "media_control"
    assert feed("music_play", {"error": "nope"}) == []
