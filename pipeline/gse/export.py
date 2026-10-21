"""Export the built data for the website (compact JSON) and the chat API (Parquet)."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

GOAL_NAMES = {
    "1": "No poverty", "2": "Zero hunger", "3": "Good health and well-being",
    "4": "Quality education", "5": "Gender equality", "6": "Clean water and sanitation",
    "7": "Affordable and clean energy", "8": "Decent work and economic growth",
    "9": "Industry, innovation and infrastructure", "10": "Reduced inequalities",
    "11": "Sustainable cities and communities", "12": "Responsible consumption and production",
    "13": "Climate action", "14": "Life below water", "15": "Life on land",
    "16": "Peace, justice and strong institutions", "17": "Partnerships for the goals",
}


def safe_name(indicator_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", indicator_id)


def round_sig(v: float, sig: int = 6) -> float | int:
    if v == 0 or not math.isfinite(v):
        return 0
    r = round(v, sig - int(math.floor(math.log10(abs(v)))) - 1)
    return int(r) if float(r).is_integer() and abs(r) < 2**53 else r


def _listify(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    try:
        return list(v)
    except TypeError:
        return [v]


def write_series(out: Path, ind: dict, frame: pd.DataFrame, updated: str) -> dict:
    frame = frame.sort_values(["geo", "dims", "year"])
    geos = list(dict.fromkeys(frame["geo"]))
    dims = list(dict.fromkeys(frame["dims"]))
    if "" in dims:  # put the total first
        dims.remove("")
        dims.insert(0, "")
    flags = list(dict.fromkeys(f for f in frame["flag"] if f))
    gi = {g: i for i, g in enumerate(geos)}
    di = {d: i for i, d in enumerate(dims)}
    fi = {f: i + 1 for i, f in enumerate(flags)}
    rows = []
    for g, d, y, v, f in zip(frame["geo"], frame["dims"], frame["year"], frame["value"],
                             frame["flag"]):
        row = [gi[g], int(y), round_sig(float(v)), di[d]]
        if f:
            row.append(fi[f])
        rows.append(row)
    payload = {
        "id": ind["indicator_id"],
        "name": ind["name"],
        "unit": ind.get("unit") or "",
        "description": ind.get("description") or "",
        "source": ind["source"],
        "source_org": ind.get("source_org") or "",
        "source_url": ind.get("source_url") or "",
        "license": ind.get("license") or "",
        "release": ind.get("release") or "",
        "updated": updated,
        "geos": geos,
        "dims": dims,
        "flags": flags,
        "data": rows,
    }
    path = out / "series" / f"{safe_name(ind['indicator_id'])}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    # Breakdown keys and values for the catalog's drill-down.
    dim_values: dict[str, set] = {}
    for d in dims:
        for part in filter(None, d.split(";")):
            k, _, v = part.partition("=")
            dim_values.setdefault(k, set()).add(v)
    return {
        "file": path.name,
        "y0": int(frame["year"].min()),
        "y1": int(frame["year"].max()),
        "ng": int(frame.loc[frame["dims"] == "", "geo"].nunique() or len(geos)),
        "nr": int(len(frame)),
        "dims": {k: sorted(v) for k, v in dim_values.items()},
        "has_total": "" in dims,
    }


def export_site(out: Path, obs: pd.DataFrame, indicators: pd.DataFrame, geo: dict,
                goal_tree: list[dict], sources_meta: dict, updated_map: dict[str, str]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "series").mkdir(exist_ok=True)
    for old in (out / "series").glob("*.json"):
        old.unlink()
    ind_by_id = {r["indicator_id"]: r for r in indicators.to_dict("records")}
    catalog = []
    for iid, frame in obs.groupby("indicator_id", sort=False):
        ind = ind_by_id.get(iid)
        if ind is None:
            continue
        info = write_series(out, ind, frame, updated_map.get(iid, ""))
        catalog.append({
            "id": iid,
            "s": ind["source"],
            "c": ind["code"],
            "n": ind["name"],
            "u": ind.get("unit") or "",
            "g": _listify(ind.get("sdg_goals")),
            "t": _listify(ind.get("sdg_targets")),
            "i": _listify(ind.get("sdg_indicators")),
            "m": ind.get("sdg_mapping") or "",
            "tp": _listify(ind.get("topics")),
            "rel": ind.get("release") or "",
            "upd": updated_map.get(iid, ""),
            **info,
        })
    catalog.sort(key=lambda r: (r["s"], r["n"]))

    goals = []
    tree_by_code = {g["code"]: g for g in goal_tree or []}
    for code, name in GOAL_NAMES.items():
        g = tree_by_code.get(code, {})
        goals.append({"code": code, "name": name, "title": g.get("title") or name,
                      "targets": g.get("targets") or []})

    geo_list = []
    for rec in geo.values():
        keep = {k: v for k, v in rec.items() if v not in (None, [], "")}
        if rec["type"] == "country":
            keep.pop("members", None)
        geo_list.append(keep)
    geo_list.sort(key=lambda r: ({"region": 0, "group": 1, "country": 2,
                                  "aggregate": 3}[r["type"]], r["name"]))

    _dump(out / "catalog.json", {"sources": sources_meta, "goals": goals, "indicators": catalog})
    _dump(out / "geo.json", geo_list)
    log.info("Site export: %d indicators, %d geos", len(catalog), len(geo_list))


def export_parquet(out: Path, obs: pd.DataFrame, indicators: pd.DataFrame, geo: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    obs.to_parquet(out / "observations.parquet", index=False, compression="zstd")
    ind = indicators.copy()
    for c in ("sdg_goals", "sdg_targets", "sdg_indicators", "topics"):
        ind[c] = ind[c].apply(_listify)
    ind.to_parquet(out / "indicators.parquet", index=False, compression="zstd")
    _dump(out / "geo.json", list(geo.values()))


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))
