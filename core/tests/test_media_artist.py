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


def test_find_groups_results(monkeypatch):
    class Y(FakeYT):
        def search(self, q, filter=None, limit=20):
            return [{"category": "Top result", "resultType": "artist", "artists": [{"name": "Test Artist", "id": "UC1"}], "thumbnails": []},
                    {"category": "Artists", "resultType": "artist", "browseId": "UC9", "artist": "Someone Else", "thumbnails": []},
                    {"category": "Songs", "resultType": "song", "videoId": "v1", "title": "Song One", "artists": [{"name": "Test Artist", "id": "UC1"}]},
                    {"category": "Albums", "resultType": "album", "browseId": "MPREb_1", "title": "Vol 1", "artists": [{"name": "Test Artist"}]},
                    {"category": "Community playlists", "resultType": "playlist", "browseId": "VLPL123", "title": "Mix", "author": "x"}]
    monkeypatch.setattr(M, "_ytm", lambda: Y())
    M._cache.clear()
    r = M.music_find("test")
    assert r["top"]["type"] == "artist" and r["top"]["name"] == "Test Artist" and r["top"]["id"] == "UC1" and r["songs"][0]["video_id"] == "v1"
    assert r["albums"][0]["id"] == "MPREb_1" and r["playlists"][0]["id"] == "PL123"


def test_signed_out_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "_auth_path", lambda: tmp_path / "none.json")
    assert M.music_auth_status() == {"signed_in": False} and M.music_library() == {"signed_in": False}
    import pytest
    with pytest.raises(ValueError):
        M.music_set_auth("foo=bar")


class AuthYT(FakeYT):
    def __init__(self):
        self.rated, self.added, self.created = [], [], []

    def rate_song(self, vid, r):
        self.rated.append((vid, r.value))

    def get_liked_songs(self, limit=1000):
        return {"tracks": [{"videoId": "v1"}]}

    def get_library_playlists(self, limit=100):
        return [{"playlistId": "LM", "title": "Liked Music"}, {"playlistId": "PLgym", "title": "Gym", "count": 3}]

    def add_playlist_items(self, pid, vids, duplicates=False):
        self.added.append((pid, vids))
        return {"status": "STATUS_SUCCEEDED"}

    def create_playlist(self, title, desc, privacy_status="PRIVATE", video_ids=None):
        self.created.append((title, video_ids, privacy_status))
        return "PLnew"

    def search(self, q, filter=None, limit=3):
        return [{"videoId": "v7", "title": "Let It Happen", "artists": [{"name": "Tame Impala", "id": "UCt"}]}]


def test_likes_and_playlists(monkeypatch):
    yt = AuthYT()
    monkeypatch.setattr(M, "_ytm", lambda: yt)
    monkeypatch.setattr(M, "music_auth_status", lambda: {"signed_in": True})
    M._cache.clear()
    assert M.music_like_status(["v1", "v2"])["liked"] == ["v1"]
    M.music_rate("v2", "like")
    assert yt.rated == [("v2", "LIKE")] and set(M.music_like_status(["v1", "v2"])["liked"]) == {"v1", "v2"}
    M.music_rate("v2", "none")
    assert M.music_like_status(["v2"])["liked"] == []
    assert [p["title"] for p in M.music_my_playlists()["playlists"]] == ["Gym"]  # Liked Music isn't editable
    r = M.music_library_action("add", song="let it happen", playlist="gym")
    assert r == {"done": "added", "song": "Let It Happen", "playlist": "Gym"} and yt.added == [("PLgym", ["v7"])]
    r = M.music_library_action("add", song="let it happen", new_playlist="Drive")
    assert r["done"] == "created_and_added" and yt.created == [("Drive", ["v7"], "PRIVATE")]
    r = M.music_library_action("like", song="let it happen")
    assert r["done"] == "like" and yt.rated[-1] == ("v7", "LIKE")


def test_library_actions_need_sign_in(monkeypatch):
    monkeypatch.setattr(M, "music_auth_status", lambda: {"signed_in": False})
    import pytest
    with pytest.raises(RuntimeError, match="Sign in"):
        M.music_rate("v1")
