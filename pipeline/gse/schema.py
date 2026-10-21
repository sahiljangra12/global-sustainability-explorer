"""The unified data model shared by every source.

observations: one row per value
    indicator_id  str    "<source>:<native code>", e.g. "unsdg:EG_FEC_RNEW"
    geo           str    ISO3 for countries; "UN-034", "WB-SAS", "OWID-World" for aggregates
    year          int
    value         float
    dims          str    breakdowns as "Key=Value;Key=Value" (sorted), "" for the total
    flag          str    source flag (e.g. "E" estimated, "<" below value), "" if none

indicators: one row per indicator (see INDICATOR_COLUMNS)
"""

from __future__ import annotations

import pandas as pd

OBS_COLUMNS = ["indicator_id", "geo", "year", "value", "dims", "flag"]

INDICATOR_COLUMNS = [
    "indicator_id",  # "unsdg:EG_FEC_RNEW"
    "source",  # "unsdg" | "wdi" | "owid"
    "code",  # native code
    "name",
    "unit",
    "description",
    "sdg_goals",  # list[str], e.g. ["7"]
    "sdg_targets",  # list[str]
    "sdg_indicators",  # list[str]
    "sdg_mapping",  # "official" | "approximate" | ""
    "topics",  # list[str]
    "source_org",  # the agency that produced the data
    "source_url",
    "license",
    "release",  # the source's own version / release label / last-updated date
    "dim_labels",  # JSON {dimension: {code: description}} for breakdown codes, "{}" if none
]

SOURCES = {
    "unsdg": {
        "name": "UN SDG Global Database",
        "url": "https://unstats.un.org/sdgs/dataportal",
        "license": "UN terms of use (free for non-commercial and commercial use with attribution)",
    },
    "wdi": {
        "name": "World Bank World Development Indicators",
        "url": "https://databank.worldbank.org/source/world-development-indicators",
        "license": "CC BY 4.0",
    },
    "owid": {
        "name": "Our World in Data",
        "url": "https://ourworldindata.org",
        "license": "CC BY 4.0",
    },
}


def empty_obs() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "indicator_id": pd.Series(dtype="string"),
            "geo": pd.Series(dtype="string"),
            "year": pd.Series(dtype="int32"),
            "value": pd.Series(dtype="float64"),
            "dims": pd.Series(dtype="string"),
            "flag": pd.Series(dtype="string"),
        }
    )


def coerce_obs(df: pd.DataFrame) -> pd.DataFrame:
    """Enforce column order and dtypes; drop rows without a numeric value."""
    if df.empty:
        return empty_obs()
    out = df[OBS_COLUMNS].copy()
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out[out["value"].notna() & pd.notna(out["year"])]
    out["year"] = out["year"].astype("int32")
    for c in ("indicator_id", "geo", "dims", "flag"):
        out[c] = out[c].fillna("").astype("string")
    return out.reset_index(drop=True)


def dims_key(d: dict | None) -> str:
    """Canonical string form of a breakdown dict: sorted "Key=Value;Key=Value"."""
    if not d:
        return ""
    parts = [f"{k}={v}" for k, v in sorted(d.items()) if v not in (None, "")]
    return ";".join(parts)


def parse_dims(s: str) -> dict:
    if not s:
        return {}
    out = {}
    for part in s.split(";"):
        k, _, v = part.partition("=")
        out[k] = v
    return out
