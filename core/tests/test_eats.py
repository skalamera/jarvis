"""Uber Eats: menu parsing (every item), cart with option matching, ordering guard (no network)."""
import json
from pathlib import Path

import pytest

from jarvis_core import visuals as V
from jarvis_google import eats as E, store

FX = json.loads((Path(__file__).parent / "fixtures_eats_store.json").read_text())
OPTS = {"customizationsList": [
    {"uuid": "g-bagel", "title": "Choose your bagel", "minPermitted": 1, "maxPermitted": 1, "options": [
        {"uuid": "o-plain", "title": "Plain Bagel", "price": 0}, {"uuid": "o-every", "title": "Everything Bagel", "price": 0}]},
    {"uuid": "g-prep", "title": "Preparation", "minPermitted": 0, "maxPermitted": 1, "options": [
        {"uuid": "o-toast", "title": "Make it Toasted", "price": 0}]},
    {"uuid": "g-add", "title": "Add", "minPermitted": 0, "maxPermitted": 3, "options": [
        {"uuid": "o-bacon", "title": "Bacon", "price": 250}]}], "title": "x", "price": 1695}


@pytest.fixture()
def ue(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STATE_DIR", tmp_path)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "jarvis.db")
    monkeypatch.setattr(E, "CART", tmp_path / "cart.json")
    monkeypatch.setattr(E, "_env", lambda n: "")
    monkeypatch.setattr(E, "_location", lambda: {"address": {"eaterFormattedAddress": "1 Home St"}})
    E._cache.clear()

    def post(endpoint, body, ttl=0):
        if endpoint == "getStoreV1":
            return FX
        if endpoint == "getMenuItemV1":
            return OPTS
        if endpoint == "getSearchFeedV1":
            return {"feedItems": [{"type": "REGULAR_STORE", "store": {"storeUuid": "11111111-2222-3333-4444-555555555555",
                                                                      "title": {"text": "Test Bagels"}}}]}
        raise AssertionError(endpoint)
    monkeypatch.setattr(E, "_post", post)


def test_menu_lists_every_item_and_opens_display(ue):
    with store.capture() as items:
        r = E.menu("Test Bagels")
    assert r["item_count"] == 6 and set(r["sections"]) == {"Bagels and Spreads", "Drinks"}
    assert r["sections"]["Bagels and Spreads"][0] == "Sliced Lox and Cream Cheese $16.95"
    card = V.cards_from_feed(items[0])[0]
    assert card["kind"] == "eats_menu" and card["data"]["item_count"] == 6
    assert card["data"]["sections"][0]["items"][0]["image"]


def test_cart_add_matches_options_and_defaults_required(ue):
    r = E.cart_add("Test Bagels", "lox", options=["everything", "toasted", "bacon"])
    assert r["added"] == "Sliced Lox and Cream Cheese" and r["options"] == ["Everything Bagel", "Make it Toasted", "Bacon"]
    assert r["defaulted"] == [] and r["cart_subtotal"] == 19.45
    r = E.cart_add("Test Bagels", "Bagel with Butter")
    assert r["defaulted"] == ["Choose your bagel: Plain Bagel"]
    assert E.cart()["subtotal"] == round(19.45 + 3.25 + 0, 2)
    E.cart_remove("butter")
    assert E.cart()["subtotal"] == 19.45


def test_cart_unknown_item_suggests(ue):
    with pytest.raises(ValueError, match="isn't on the Test Bagels menu"):
        E.cart_add("Test Bagels", "pizza")


def test_order_never_places_without_session(ue):
    E.cart_add("Test Bagels", "Juice")
    with pytest.raises(RuntimeError, match="Nothing was ordered"):
        E.order()
    with pytest.raises(ValueError, match="empty"):
        E.cart_remove(clear=True) and None or E.order()
