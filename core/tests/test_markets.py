"""Markets: symbol resolution, input parsing, card routing (no network)."""
import pytest

from jarvis_core import visuals
from jarvis_google import markets as M


@pytest.mark.parametrize("q,want", [
    ("the S&P", "^GSPC"), ("S&P 500", "^GSPC"), ("the S&P 500 index", "^GSPC"), ("the Dow", "^DJI"),
    ("Nasdaq", "^IXIC"), ("gold", "GC=F"), ("$AMD", "AMD"), ("brk.b", "BRK-B"), ("BRK-B", "BRK-B"),
    ("NVDA", "NVDA"), ("^VIX", "^VIX"), ("BTC-USD", "BTC-USD"), ("10 year treasury", "^TNX"),
])
def test_resolve_offline(q, want):
    assert M.resolve_symbol(q) == want


def test_resolve_names_use_search(monkeypatch):
    calls = []

    class S:
        def __init__(self, q, **kw):
            calls.append(q)
            self.quotes = [{"symbol": "7992.T", "quoteType": "OPTION"}, {"symbol": "COST", "quoteType": "EQUITY"}]

    class Y:
        Search = S

    M._cache.clear()
    monkeypatch.setattr(M, "_yf", lambda: Y)
    assert M.resolve_symbol("Costco stock") == "COST"  # suffix stripped, non-equity skipped
    assert calls == ["costco"]


def test_ticker_heuristic():
    assert M.looks_like_ticker("AAPL") and M.looks_like_ticker("BRK.B") and M.looks_like_ticker("^GSPC")
    assert not M.looks_like_ticker("apple") and not M.looks_like_ticker("Palantir") and not M.looks_like_ticker("the dow")


def test_compare_splits_and_dedupes(monkeypatch):
    seen = {}
    monkeypatch.setattr(M, "resolve_symbol", lambda s: {"apple": "AAPL"}.get(s.lower(), s.upper()))
    monkeypatch.setattr(M, "_compare", lambda syms, rng, rec: seen.update(syms=syms, rng=rng) or {"series": []})
    monkeypatch.setattr(M, "_stock", lambda sym, rng, rec: seen.update(one=sym, rng=rng) or {})
    M.stock_quote("Apple vs MSFT, aapl and GOOGL")
    assert seen["syms"] == ["AAPL", "MSFT", "GOOGL"] and seen["rng"] == "1Y"
    M.stock_quote("NVDA", range="ytd")
    assert seen["one"] == "NVDA" and seen["rng"] == "YTD"
    M.stock_quote("NVDA", range="bogus")
    assert seen["rng"] == "1D"


def test_logos_skip_indices_and_futures():
    assert M.logo_urls("^GSPC", "", "INDEX") == []
    assert M.logo_urls("GC=F", "", "FUTURE") == []
    urls = M.logo_urls("NVDA", "https://www.nvidia.com", "EQUITY")
    assert urls[0].endswith("/NVDA.png") and "domain=nvidia.com" in urls[-1]
    assert M.logo_urls("BTC-USD", "", "CRYPTOCURRENCY")[0].endswith("/BTC.png")


def test_coin_shortcuts():
    assert M.resolve_coin("BTC") == "bitcoin" and M.resolve_coin("eth-usd") == "ethereum" and M.resolve_coin("SOL") == "solana"


def test_cards_from_feed():
    one = visuals.cards_from_feed({"tool": "stock_quote", "result": {"symbol": "NVDA", "name": "NVIDIA Corporation"}})
    assert one[0]["kind"] == "stock" and "NVDA" in one[0]["title"]
    cmp = visuals.cards_from_feed({"tool": "stock_quote", "result": {"series": [{"symbol": "AAPL"}, {"symbol": "MSFT"}]}})
    assert cmp[0]["kind"] == "stock_compare" and cmp[0]["title"] == "AAPL vs MSFT"
    assert visuals.cards_from_feed({"tool": "market_overview", "result": {"indices": []}})[0]["kind"] == "market"
    assert visuals.cards_from_feed({"tool": "crypto_quote", "result": {"indices": [], "focus": "crypto"}})[0]["kind"] == "market"
    assert visuals.cards_from_feed({"tool": "crypto_quote", "result": {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"}})[0]["kind"] == "crypto"
    assert visuals.cards_from_feed({"tool": "stock_quote", "result": {"error": "nope"}}) == []
