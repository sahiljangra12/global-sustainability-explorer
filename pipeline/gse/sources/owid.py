"""Our World in Data: CO2/greenhouse-gas and energy datasets.

Data: https://github.com/owid/co2-data and https://github.com/owid/energy-data
Each numeric column becomes one indicator. A dataset whose file content hash is
unchanged since the last run is reused.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re

import pandas as pd

from ..geo import norm, register_aggregate
from ..schema import SOURCES, coerce_obs, empty_obs
from . import SourceResult

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com/owid"
DATASETS = {
    "co2": {
        "data": f"{RAW}/co2-data/master/owid-co2-data.csv",
        "codebook": f"{RAW}/co2-data/master/owid-co2-codebook.csv",
        "page": "https://github.com/owid/co2-data",
        "goal": "13",
        "topic": "Climate change",
    },
    "energy": {
        "data": f"{RAW}/energy-data/master/owid-energy-data.csv",
        "codebook": f"{RAW}/energy-data/master/owid-energy-codebook.csv",
        "page": "https://github.com/owid/energy-data",
        "goal": "7",
        "topic": "Energy",
    },
}
ID_COLUMNS = {"country", "year", "iso_code"}
# Columns that are context rather than sustainability indicators; kept once only.
SHARED_COLUMNS = {"population", "gdp"}
CLIMATE_IN_ENERGY = re.compile(r"(co2|ghg|carbon|greenhouse)", re.I)


def _geo_for(row_iso: str, country: str, geo: dict) -> str:
    if isinstance(row_iso, str) and row_iso and not row_iso.startswith("OWID_"):
        if row_iso in geo:
            return row_iso
    if row_iso == "OWID_KOS":
        return register_aggregate(geo, "XKX", "Kosovo", "owid") if "XKX" not in geo else "XKX"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", country).strip("-")
    return register_aggregate(geo, f"OWID-{slug}", f"{country} (OWID)" if country != "World"
                              else "World (OWID)", "owid")


def parse_dataset(key: str, csv_bytes: bytes, codebook_bytes: bytes, geo: dict,
                  skip_columns: set[str]) -> tuple[pd.DataFrame, list[dict]]:
    cfg = DATASETS[key]
    df = pd.read_csv(io.BytesIO(csv_bytes), low_memory=False).copy()
    cb = pd.read_csv(io.BytesIO(codebook_bytes))
    cb = cb.set_index("column")
    geos = {(i, c): _geo_for(i, c, geo)
            for i, c in df[["iso_code", "country"]].drop_duplicates()
            .itertuples(index=False, name=None)}
    df["geo"] = [geos[(i, c)] for i, c in zip(df["iso_code"], df["country"])]
    value_cols = [c for c in df.columns
                  if c not in ID_COLUMNS | {"geo"} | skip_columns
                  and pd.api.types.is_numeric_dtype(df[c])]
    long = df.melt(id_vars=["geo", "year"], value_vars=value_cols,
                   var_name="col", value_name="value").dropna(subset=["value"])
    long["indicator_id"] = "owid:" + long["col"]
    long["dims"] = ""
    long["flag"] = ""
    obs = coerce_obs(long)

    ind_rows = []
    for c in value_cols:
        meta = cb.loc[c] if c in cb.index else None
        title = (meta["title"] if meta is not None and isinstance(meta.get("title"), str)
                 else c.replace("_", " ").capitalize())
        unit = meta["unit"] if meta is not None and isinstance(meta.get("unit"), str) else ""
        desc = (meta["description"] if meta is not None
                and isinstance(meta.get("description"), str) else "")
        src = meta["source"] if meta is not None and isinstance(meta.get("source"), str) else ""
        src = re.sub(r"\s*\[https?://[^\]]+\]", "", src).strip()
        goal = cfg["goal"]
        topic = cfg["topic"]
        if key == "energy" and CLIMATE_IN_ENERGY.search(c):
            goal, topic = "13", "Climate change"
        if c in SHARED_COLUMNS:
            goal, topic = None, "Context"
        ind_rows.append({
            "indicator_id": f"owid:{c}",
            "source": "owid",
            "code": c,
            "name": title,
            "unit": unit,
            "description": desc,
            "sdg_goals": [goal] if goal else [],
            "sdg_targets": [],
            "sdg_indicators": [],
            "sdg_mapping": "approximate" if goal else "",
            "topics": [topic],
            "source_org": src,
            "source_url": cfg["page"],
            "license": SOURCES["owid"]["license"],
            "release": "",
        })
    return obs, ind_rows


def run(ctx) -> SourceResult:
    s = ctx.session
    prev_state = ctx.prev_state.get("owid", {})
    prev_obs = ctx.prev_obs("owid")
    frames, ind_rows, state, fetched, reused = [], [], {}, 0, 0
    seen_cols: set[str] = set()
    for key, cfg in DATASETS.items():
        data = s.get(cfg["data"], timeout=300)
        data.raise_for_status()
        cb = s.get(cfg["codebook"], timeout=120)
        cb.raise_for_status()
        digest = hashlib.sha256(data.content).hexdigest()[:16]
        obs, rows = parse_dataset(key, data.content, cb.content, ctx.geo, seen_cols)
        seen_cols |= {r["code"] for r in rows}
        release = f"{key}@{digest}"
        for r in rows:
            r["release"] = release
        if prev_state.get(key) == digest and prev_obs is not None and not prev_obs.empty:
            ids = {r["indicator_id"] for r in rows}
            frames.append(prev_obs[prev_obs["indicator_id"].isin(ids)])
            reused += 1
        else:
            frames.append(obs)
            fetched += 1
        ind_rows.extend(rows)
        state[key] = digest
        log.info("OWID %s: %d indicators, %d values (%s)", key, len(rows), len(obs),
                 "unchanged" if prev_state.get(key) == digest else "new data")
    obs = pd.concat(frames, ignore_index=True) if frames else empty_obs()
    release = ", ".join(f"{k}@{v}" for k, v in state.items())
    return SourceResult("owid", obs, pd.DataFrame(ind_rows), release, state=state,
                        fetched=fetched, reused=reused)


__all__ = ["run", "parse_dataset", "norm"]
