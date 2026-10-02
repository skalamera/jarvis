"""Music: tracks carry artist ids (for clickable artist links), artist + album pages parse (no network)."""
from jarvis_google import media as M


class FakeYT:
    def get_artist(self, aid):
        return {"name": "Test Artist", "monthlyListeners": "610K", "subscribers": "65K", "description": "None",
                "thumbnails": [{"url": "https://x/a=w120-h120-l90-rj"}],
                "songs": {"results": [{"videoId": "v1", "title": "Song One", "artists": [{"name": "Test Artist", "id": aid}],
                                       "album": {"name": "Vol 1", "id": "MPREb_1"}, "thumbnails": []}]},
                "albums": {"results": [{"browseId": "MPREb_1", "title": "Vol 1", "year": "2026", "thumbnails": []}]},
                "singles": {"results": [{"browseId": "MPREb_2", "title": "Single", "thumbnails": []}]},
                "related": {"results": [{"browseId": "UC2", "title": "Other Artist", "subscribers": "1M", "thumbnails": []}]}}

    def get_album(self, bid):
        return {"title": "Vol 1", "type": "Album", "year": "2026", "artists": [{"name": "Test Artist", "id": "UC1"}],
                "thumbnails": [], "tracks": [{"videoId": "v1", "title": "Song One", "artists": [{"name": "Test Artist", "id": "UC1"}]},
                                             {"videoId": "v2", "title": "Song Two", "artists": [{"name": "Test Artist", "id": "UC1"}]}]}

    def search(self, q, filter=None, limit=3):
        return [{"browseId": "UC1"}]


def test_track_keeps_artist_ids():
    t = M._track({"videoId": "v", "title": "x", "artists": [{"name": "A", "id": "UC1"}, {"name": "B", "id": None}],
                  "album": {"name": "Al", "id": "MPREb_9"}})
    assert t["artists"] == [{"name": "A", "id": "UC1"}, {"name": "B", "id": None}] and t["album_id"] == "MPREb_9"
    assert t["artist"] == "A, B"


def test_artist_and_album_pages(monkeypatch):
    monkeypatch.setattr(M, "_ytm", lambda: FakeYT())
    M._cache.clear()
    a = M.music_artist(name="test artist")
    assert a["id"] == "UC1" and a["description"] == "" and a["monthly_listeners"] == "610K"
    assert a["top_songs"][0]["video_id"] == "v1" and a["albums"][0]["year"] == "2026" and a["related"][0]["id"] == "UC2"
    al = M.music_album("MPREb_1")
    assert [x["title"] for x in al["tracks"]] == ["Song One", "Song Two"] and al["tracks"][0]["album_id"] == "MPREb_1"
