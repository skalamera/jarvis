import pytest

from jarvis_google import social as S


def test_home_views():
    assert S.social_url("instagram")["url"] == "https://www.instagram.com/"
    assert S.social_url("facebook", "marketplace")["url"] == "https://www.facebook.com/marketplace/"
    r = S.social_url("linkedin", "messages")
    assert r["url"] == "https://www.linkedin.com/messaging/" and r["title"] == "LinkedIn · Messages"
    assert S.social_url("linkedin", "bogus")["view"] == "home"


def test_search():
    assert S.social_url("instagram", query="#mercedes")["url"] == "https://www.instagram.com/explore/tags/mercedes/"
    assert S.social_url("instagram", query="formula 1")["url"] == "https://www.instagram.com/explore/search/keyword/?q=formula%201"
    assert S.social_url("facebook", query="cl600", kind="marketplace")["url"] == "https://www.facebook.com/marketplace/search/?query=cl600"
    assert S.social_url("facebook", query="john")["url"] == "https://www.facebook.com/search/top/?q=john"
    assert S.social_url("linkedin", query="hadrius", kind="companies")["url"] == "https://www.linkedin.com/search/results/companies/?keywords=hadrius"
    assert S.social_url("linkedin", query="support engineer", kind="jobs")["url"] == "https://www.linkedin.com/jobs/search/?keywords=support%20engineer"
    with pytest.raises(ValueError):
        S.social_url("facebook", "search")


def test_profiles():
    assert S.social_url("instagram", user="@natgeo")["url"] == "https://www.instagram.com/natgeo/"
    assert S.social_url("linkedin", user="satyanadella")["url"] == "https://www.linkedin.com/in/satyanadella/"
    assert S.social_url("linkedin", user="company:hadrius")["url"] == "https://www.linkedin.com/company/hadrius/"
    assert S.social_url("facebook", user="zuck")["url"] == "https://www.facebook.com/zuck"


def test_cards():
    from jarvis_core import visuals as V
    for site in ("instagram", "facebook", "linkedin"):
        c = V.cards_from_feed({"tool": f"{site}_open", "result": S.social_url(site, "messages")})
        assert c[0]["kind"] == site and c[0]["data"]["url"].startswith("https://")
