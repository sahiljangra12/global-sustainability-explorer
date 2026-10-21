import datetime as dt
import json

from gse import news


def fake_fetch(feed):
    if feed["name"] == "Down":
        return [], "HTTP 500"
    return [
        {"title": "COP31 negotiators in Antalya agree on adaptation finance",
         "url": "https://example.org/a", "date": "2026-11-12T10:00:00+00:00", "source": feed["name"],
         "kind": "official",
         "teaser": "Countries including India and Brazil backed a new goal for climate finance."},
        {"title": "COP31 negotiators in Antalya agree on adaptation finance!",
         "url": "https://example.org/a-dup", "date": "2026-11-12T09:00:00+00:00",
         "source": feed["name"], "kind": "official", "teaser": ""},
        {"title": "Old story", "url": "https://example.org/old", "date": "2025-01-01T00:00:00+00:00",
         "source": feed["name"], "kind": "official", "teaser": ""},
    ], None


def test_build_news(tmp_path):
    feeds = [{"name": "Good", "url": "u1"}, {"name": "Down", "url": "u2"}]
    out = news.build_news(tmp_path, None, None, today=dt.date(2026, 11, 13), feeds=feeds,
                          fetch=fake_fetch)
    items = out["items"]
    assert len(items) == 1  # duplicate merged, old story dropped
    it = items[0]
    assert it["event"] == "COP31"
    assert "13" in it["sdgs"] and "17" in it["sdgs"]
    assert {"IND", "BRA"} <= set(it["countries"])
    assert [f["ok"] for f in out["feeds"]] == [True, False]
    cop31 = next(e for e in out["events"] if e["short"] == "COP31")
    assert cop31["status"] == "live"
    saved = json.loads((tmp_path / "news.json").read_text())
    assert saved["items"][0]["url"] == "https://example.org/a"


def test_teaser_trims_and_strips_html():
    t = news.teaser("<p>Hello &amp; welcome " + "word " * 100 + "</p>")
    assert t.startswith("Hello & welcome") and t.endswith("…") and len(t) <= news.TEASER_CHARS + 1


def test_news_keeps_previous_items(tmp_path):
    prev = tmp_path / "prev.json"
    prev.write_text(json.dumps({"items": [{"title": "Earlier UN water report", "url": "https://x/1",
                                           "date": "2026-11-10T00:00:00+00:00", "source": "UN",
                                           "kind": "official", "teaser": ""}]}))
    out = news.build_news(tmp_path, prev, None, today=dt.date(2026, 11, 13),
                          feeds=[{"name": "Good", "url": "u"}], fetch=fake_fetch)
    urls = [i["url"] for i in out["items"]]
    assert "https://x/1" in urls and "6" in out["items"][urls.index("https://x/1")]["sdgs"]
