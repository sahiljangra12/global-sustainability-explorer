"""Tests for the database tools and the number check, using a tiny local dataset."""

import json

import pandas as pd
import pytest

from gse_api.agent import Agent, supported, unsupported_numbers
from gse_api.data import Database


@pytest.fixture
def db(tmp_path):
    rows = []
    for geo, base in (("IND", 30.0), ("CHN", 10.0), ("USA", 5.0), ("UN-001", 15.0)):
        for i, y in enumerate(range(2010, 2023)):
            rows.append(("unsdg:EG_FEC_RNEW", geo, y, base + i * 0.5, "", ""))
    rows.append(("unsdg:EG_FEC_RNEW", "IND", 2021, 12.0, "Location=RURAL", ""))
    for geo, v in (("IND", 1.9), ("CHN", 8.0), ("USA", 14.2)):
        rows.append(("owid:co2_per_capita", geo, 2024, v, "", ""))
    obs = pd.DataFrame(rows, columns=["indicator_id", "geo", "year", "value", "dims", "flag"])
    obs.to_parquet(tmp_path / "observations.parquet")
    ind = pd.DataFrame([
        {"indicator_id": "unsdg:EG_FEC_RNEW", "source": "unsdg", "code": "EG_FEC_RNEW",
         "name": "Renewable energy share in the total final energy consumption (%)", "unit": "Percentage",
         "description": "7.2.1", "sdg_goals": ["7"], "sdg_targets": ["7.2"], "sdg_indicators": ["7.2.1"],
         "sdg_mapping": "official", "topics": [], "source_org": "IEA", "source_url": "", "license": "",
         "release": "2026.Q2"},
        {"indicator_id": "owid:co2_per_capita", "source": "owid", "code": "co2_per_capita",
         "name": "CO2 emissions per capita", "unit": "tonnes per person", "description": "",
         "sdg_goals": ["13"], "sdg_targets": [], "sdg_indicators": [], "sdg_mapping": "approximate",
         "topics": ["Climate change"], "source_org": "GCP", "source_url": "", "license": "CC BY 4.0",
         "release": "co2@abc"},
    ])
    ind.to_parquet(tmp_path / "indicators.parquet")
    geo = [{"code": "IND", "name": "India", "type": "country", "aliases": ["Bharat"]},
           {"code": "CHN", "name": "China", "type": "country"},
           {"code": "USA", "name": "United States", "type": "country", "aliases": ["USA", "America"]},
           {"code": "UN-001", "name": "World", "type": "region", "members": ["IND", "CHN", "USA"]}]
    (tmp_path / "geo.json").write_text(json.dumps(geo))
    (tmp_path / "news.json").write_text(json.dumps({"items": [
        {"title": "COP31 opens in Antalya", "source": "UN", "date": "2026-11-09", "url": "u", "event": "COP31"}]}))
    return Database(tmp_path)


def test_search_and_info(db):
    hits = db.search_indicators("renewable energy share")
    assert hits[0]["indicator_id"] == "unsdg:EG_FEC_RNEW"
    assert db.search_indicators("co2 per capita")[0]["indicator_id"] == "owid:co2_per_capita"
    info = db.indicator_info("unsdg:EG_FEC_RNEW")
    assert info["unit"] == "Percentage" and "Location=RURAL" in info["breakdowns"]


def test_geo_and_values(db):
    r = db.resolve_geo(["india", "America", "world", "Atlantis"], "unsdg:EG_FEC_RNEW")
    assert [x.get("code") for x in r] == ["IND", "USA", "UN-001", None]
    s = db.get_series("unsdg:EG_FEC_RNEW", ["IND"], 2020, 2022)
    assert [row["year"] for row in s["rows"]] == [2020, 2021, 2022] and s["breakdown"] == "total"
    t = db.trend("unsdg:EG_FEC_RNEW", "IND", 2010, 2022)
    assert t["first"]["value"] == 30.0 and t["last"]["value"] == 36.0
    assert t["absolute_change"] == 6.0 and round(t["percent_change"], 1) == 20.0
    c = db.compare("owid:co2_per_capita", ["IND", "USA", "CHN"])
    assert [row["geo"] for row in c["rows"]] == ["USA", "CHN", "IND"]
    rk = db.rank("owid:co2_per_capita", n=2, order="asc")
    assert [row["geo"] for row in rk["rows"]] == ["IND", "CHN"] and rk["countries_with_data"] == 3
    assert db.search_news("COP31 Antalya")[0]["event"] == "COP31"


def test_number_check():
    allowed = {36.0, 30.0, 20.0, 2022.0, 2010.0, 2.7e9}
    assert supported(36.0, allowed) and supported(5, allowed) and supported(2.7, allowed)
    assert not supported(41.3, allowed)
    assert unsupported_numbers("India rose from 30 to 36% (2010-2022), +20%.", allowed, "q") == []
    assert unsupported_numbers("It reached 41.3% in 2022.", allowed, "q") == [41.3]


class FakeLLM:
    """Plays back a scripted conversation in place of the HTTP client."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def post(self, url, headers=None, json=None):
        self.requests.append(json)
        msg = self.script.pop(0)

        class R:
            def raise_for_status(self):
                pass

            def json(self_inner):
                return {"choices": [{"message": msg}]}

        return R()


def tc(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_agent_happy_path_and_citations(db):
    llm = FakeLLM([
        {"content": "", "tool_calls": [tc(1, "search_indicators", {"query": "renewable share"})]},
        {"content": "", "tool_calls": [tc(2, "trend", {"indicator_id": "unsdg:EG_FEC_RNEW", "geo": "IND",
                                                       "year_from": 2010, "year_to": 2022})]},
        {"content": "India's renewable share rose from 30% in 2010 to 36% in 2022. "
                    "Source: UN SDG Global Database, series EG_FEC_RNEW."},
    ])
    agent = Agent(db, api_key="x", client=llm)
    out = agent.ask("renewable share India since 2010")
    assert out["verified"] and "36%" in out["answer"]
    assert out["citations"][0]["code"] == "EG_FEC_RNEW"
    assert out["view"]["geos"] == ["IND"] and out["indicator"] == "unsdg:EG_FEC_RNEW"


def test_agent_blocks_made_up_numbers(db):
    llm = FakeLLM([
        {"content": "", "tool_calls": [tc(1, "trend", {"indicator_id": "unsdg:EG_FEC_RNEW", "geo": "IND"})]},
        {"content": "India reached 41.3% in 2022."},
        {"content": "India reached 47.9% in 2022."},
    ])
    out = Agent(db, api_key="x", client=llm).ask("renewable share India")
    assert not out["verified"]
    assert "41.3" not in out["answer"] and "47.9" not in out["answer"]
    # the model was told which numbers were unsupported
    assert "41.3" in llm.requests[2]["messages"][-1]["content"]
