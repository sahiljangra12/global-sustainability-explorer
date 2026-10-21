import json

import pandas as pd
import pytest

from gse import build as buildmod
from gse import geo as geomod
from gse.sources import unsdg
from gse.validate import ValidationError, diff, validate_source
from gse.schema import dims_key, parse_dims

from fakes import UN_TREE, WB_COUNTRIES, FakeResponse, FakeSession


@pytest.fixture
def fake(monkeypatch):
    sess = FakeSession()
    monkeypatch.setattr(buildmod, "session", lambda: sess)
    return sess


def run_build(tmp_path, prev=None, sources="unsdg,wdi,owid"):
    out = tmp_path / "build"
    buildmod.main(["--prev", str(prev or tmp_path / "noprev"), "--out", str(out),
                   "--sources", sources])
    return out


def test_parse_value():
    assert unsdg.parse_value("52.94") == (52.94, "")
    assert unsdg.parse_value(">95") == (95.0, ">")
    assert unsdg.parse_value("<0.1") == (0.1, "<")
    assert unsdg.parse_value("NaN") == (None, "")
    assert unsdg.parse_value("N") == (None, "")
    assert unsdg.parse_value(None) == (None, "")


def test_dims_roundtrip():
    k = dims_key({"Sex": "FEMALE", "Age": "15-24", "Empty": ""})
    assert k == "Age=15-24;Sex=FEMALE"
    assert parse_dims(k) == {"Age": "15-24", "Sex": "FEMALE"}


def test_geo_tree():
    g = geomod.build_geo(UN_TREE, WB_COUNTRIES)
    assert g["IND"]["type"] == "country"
    assert g["IND"]["parent"] == "UN-034"
    assert g["IND"]["continent"] == "UN-142"
    assert g["IND"]["income"] == "Lower middle income"
    assert g["IND"]["sdg_region"] == "Central and Southern Asia"
    assert set(g["UN-142"]["members"]) == {"IND", "BGD", "CHN"}
    assert set(g["UN-001"]["members"]) == {"IND", "BGD", "CHN", "USA"}
    assert g["UN-199"]["members"] == ["BGD"] and "LDC" in g["BGD"]["groups"]
    assert g["WB-WLD"]["type"] == "aggregate"
    idx = geomod.alias_index(g)
    assert idx["usa"] == "USA" and idx["america"] == "USA"
    assert idx["southern asia"] == "UN-034" and idx["india"] == "IND"


def test_full_build(tmp_path, fake):
    out = run_build(tmp_path)
    obs = pd.read_parquet(out / "data" / "observations.parquet")
    # UN: global (G) value preferred over national (N) for India 2021
    india = obs[(obs.indicator_id == "unsdg:EG_FEC_RNEW") & (obs.geo == "IND") & (obs.year == 2021)]
    assert india["value"].tolist() == [35.2]
    usa = obs[(obs.indicator_id == "unsdg:EG_FEC_RNEW") & (obs.geo == "USA")]
    assert usa["flag"].tolist() == ["<"]
    assert "UN-001" in set(obs.geo) and "UN-034" not in set(obs.geo)  # NaN dropped
    # WDI: only WDI-source indicators, null values dropped, aggregates prefixed
    wdi = obs[obs.indicator_id.str.startswith("wdi:")]
    assert set(wdi.indicator_id) == {"wdi:EG.ELC.ACCS.ZS", "wdi:EN.ATM.CO2E.PC"}
    assert "CHN" not in set(wdi.geo) and {"WB-WLD", "WB-SAS"} <= set(wdi.geo)
    # OWID: per-column indicators, aggregates prefixed, population not duplicated
    assert {"owid:co2", "owid:co2_per_capita", "owid:renewables_share_energy"} <= set(obs.indicator_id)
    assert "OWID-World" in set(obs.geo) and "OWID-Asia-GCP" in set(obs.geo)
    ind = pd.read_parquet(out / "data" / "indicators.parquet")
    assert (ind.indicator_id == "owid:population").sum() == 1
    g = ind.set_index("indicator_id")
    assert list(g.loc["owid:greenhouse_gas_emissions", "sdg_goals"]) == ["13"]
    assert list(g.loc["wdi:EG.ELC.ACCS.ZS", "sdg_goals"]) == ["7"]
    assert g.loc["unsdg:EG_FEC_RNEW", "unit"] == "Percentage"
    assert g.loc["unsdg:EG_FEC_RNEW", "source_org"] == "IEA (2025)"

    site = out / "site-data"
    cat = json.loads((site / "catalog.json").read_text())
    ids = {i["id"] for i in cat["indicators"]}
    assert "unsdg:EG_FEC_RNEW" in ids and len(cat["goals"]) == 17
    entry = next(i for i in cat["indicators"] if i["id"] == "unsdg:EG_FEC_RNEW")
    ser = json.loads((site / "series" / entry["file"]).read_text())
    assert ser["unit"] == "Percentage" and ser["geos"][0] in ser["geos"]
    assert entry["y1"] == 2021 and entry["upd"]
    log = json.loads((site / "changelog.json").read_text())
    assert log and log[0]["new_indicators"] == len(ids)


def test_reuse_and_change_detection(tmp_path, fake):
    first = run_build(tmp_path / "a")
    prev = tmp_path / "prev"
    (prev / "data").mkdir(parents=True)
    for f in (first / "data").iterdir():
        (prev / "data" / f.name).write_bytes(f.read_bytes())
    fake.calls.clear()
    second = run_build(tmp_path / "b", prev=prev)
    # Nothing changed upstream: no UN series or WDI indicator re-downloaded.
    assert not any("/Series/Data" in c for c in fake.calls)
    assert not any("/v2/country/all/indicator/" in c for c in fake.calls)
    o1 = pd.read_parquet(first / "data" / "observations.parquet")
    o2 = pd.read_parquet(second / "data" / "observations.parquet")
    assert len(o1) == len(o2)
    log = json.loads((second / "data" / "changelog.json").read_text())
    assert len(log) == 1  # no new changelog entry when nothing changed


def test_failed_source_keeps_previous_data(tmp_path, monkeypatch):
    sess = FakeSession()
    monkeypatch.setattr(buildmod, "session", lambda: sess)
    first = run_build(tmp_path / "a")
    prev = tmp_path / "prev"
    (prev / "data").mkdir(parents=True)
    for f in (first / "data").iterdir():
        (prev / "data" / f.name).write_bytes(f.read_bytes())
    broken = FakeSession(fail={"/v2/sources/2"})
    monkeypatch.setattr(buildmod, "session", lambda: broken)
    second = run_build(tmp_path / "b", prev=prev)
    o2 = pd.read_parquet(second / "data" / "observations.parquet")
    assert (o2.indicator_id.str.startswith("wdi:")).sum() > 0
    meta = json.loads((second / "site-data" / "meta.json").read_text())
    assert meta["sources"]["wdi"]["status"] == "failed"


def test_one_bad_un_series_does_not_stop_run(tmp_path, monkeypatch):
    sess = FakeSession(fail={"SG_GEN_PARL"})
    monkeypatch.setattr(buildmod, "session", lambda: sess)
    out = run_build(tmp_path, sources="unsdg")
    obs = pd.read_parquet(out / "data" / "observations.parquet")
    assert "unsdg:EG_FEC_RNEW" in set(obs.indicator_id)
    assert "unsdg:SG_GEN_PARL" not in set(obs.indicator_id)


def test_validation_rejects_big_drop():
    obs = pd.DataFrame({"indicator_id": ["x:a"] * 5, "geo": list("ABCDE"), "year": [2020] * 5,
                        "value": [1.0] * 5, "dims": [""] * 5, "flag": [""] * 5})
    ind = pd.DataFrame({"indicator_id": ["x:a"]})
    with pytest.raises(ValidationError):
        validate_source("x", obs, ind, prev_rows=100)
    cleaned, warns = validate_source("x", pd.concat([obs, obs]), ind, prev_rows=5)
    assert len(cleaned) == 5 and warns


def test_diff_detects_revisions_and_new_years():
    base = dict(indicator_id="x:a", geo="IND", dims="", flag="")
    prev = pd.DataFrame([{**base, "year": 2020, "value": 1.0}, {**base, "year": 2021, "value": 2.0}])
    new = pd.DataFrame([{**base, "year": 2020, "value": 1.5}, {**base, "year": 2021, "value": 2.0},
                        {**base, "year": 2022, "value": 3.0}])
    d = diff(prev, new)
    assert d["revised_values"] == 1 and d["added_rows"] == 1
    assert d["changed"]["x:a"]["new_latest_year"] == 2022
