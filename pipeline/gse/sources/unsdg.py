"""UN SDG Global Database (UN Statistics Division SDG API).

API docs: https://unstats.un.org/SDGAPI/swagger/
Every SDG data series is fetched in full (all areas, years and breakdowns).
Series whose release label has not changed since the last run are reused from
the previous build instead of being downloaded again.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..geo import un_code
from ..http import get_json
from ..schema import SOURCES, coerce_obs, dims_key, empty_obs, parse_dims
from . import SourceResult

log = logging.getLogger(__name__)

BASE = "https://unstats.un.org/SDGAPI/v1/sdg"
PAGE_SIZE = 10000
# Flags worth keeping: E estimated, M modelled, G global monitoring data, C country data,
# CA country adjusted, N non-relevant. "NA"/"_X" means "not available" and is dropped.
DROP_FLAGS = {"NA", "_X", ""}
DROP_DIMS = {"Reporting Type"}  # handled separately: global (G) figures preferred


def parse_value(raw) -> tuple[float | None, str]:
    """Parse UN values like "52.94", ">95", "<0.1", "NaN", "N"."""
    if raw is None:
        return None, ""
    if isinstance(raw, (int, float)):
        return float(raw), ""
    s = str(raw).strip().replace(",", "")
    flag = ""
    if s[:1] in "<>":
        flag, s = s[0], s[1:].strip()
    try:
        v = float(s)
    except ValueError:
        return None, ""
    if v != v:  # NaN
        return None, ""
    return v, flag


TOTAL_CODES = {"ALLAREA", "BOTHSEX", "ALLAGE", "TOTAL", "_T", "ALL"}


def normalize_dims(df: pd.DataFrame) -> pd.DataFrame:
    """Make the total of each UN series a plain "" breakdown.

    - Values that mean "total" (all areas, both sexes, all ages) are dropped from the key.
    - A dimension with a single value across the whole series (e.g. Sex=FEMALE for
      "seats held by women") is not a real breakdown and is dropped too.
    Idempotent, so it is safe on data reused from earlier builds.
    """
    if df.empty or not df["dims"].astype(bool).any():
        return df
    out = []
    for _iid, g in df.groupby("indicator_id", sort=False):
        parsed = [parse_dims(d) for d in g["dims"]]
        parsed = [{k: v for k, v in d.items() if v not in TOTAL_CODES} for d in parsed]
        values: dict[str, set] = {}
        for d in parsed:
            for k, v in d.items():
                values.setdefault(k, set()).add(v)
        constant = {k for k, vs in values.items()
                    if len(vs) == 1 and all(k in d for d in parsed)}
        g = g.copy()
        g["dims"] = [dims_key({k: v for k, v in d.items() if k not in constant}) for d in parsed]
        out.append(g.drop_duplicates(["geo", "year", "dims"], keep="first"))
    return pd.concat(out, ignore_index=True)


def rows_to_frame(series_code: str, items: list[dict]) -> tuple[pd.DataFrame, dict]:
    """Convert API data items to observations; return (frame, info about units)."""
    recs = []
    units = {}
    providers = {}
    for it in items:
        src = (it.get("source") or "").strip()
        if src:
            providers[src] = providers.get(src, 0) + 1
        v, vflag = parse_value(it.get("value"))
        if v is None:
            continue
        year = it.get("timePeriodStart")
        if year is None:
            continue
        attrs = it.get("attributes") or {}
        dims = dict(it.get("dimensions") or {})
        rtype = dims.pop("Reporting Type", None)
        for d in DROP_DIMS:
            dims.pop(d, None)
        unit = attrs.get("Units") or ""
        units[unit] = units.get(unit, 0) + 1
        nature = attrs.get("Nature") or ""
        flag = vflag or ("" if nature in DROP_FLAGS else nature)
        recs.append({
            "indicator_id": f"unsdg:{series_code}",
            "geo": un_code(int(it["geoAreaCode"])),
            "year": int(float(year)),
            "value": v,
            "_dims": dims,
            "_unit": unit,
            "_rtype": rtype or "",
            "flag": flag,
        })
    if not recs:
        return empty_obs(), {}
    df = pd.DataFrame(recs)
    multi_unit = df["_unit"].nunique() > 1
    if multi_unit:
        df["_dims"] = [{**d, "Units": u} for d, u in zip(df["_dims"], df["_unit"])]
    df["dims"] = [dims_key(d) for d in df["_dims"]]
    # When a value is reported both nationally (N) and as the UN global series (G),
    # keep the global one so countries stay comparable.
    df["_rank"] = df["_rtype"].map({"G": 0, "R": 1, "N": 2}).fillna(3)
    df = df.sort_values("_rank").drop_duplicates(["geo", "year", "dims"], keep="first")
    main_unit = max(units, key=units.get) if units else ""
    top_providers = sorted(providers, key=providers.get, reverse=True)[:3]
    return normalize_dims(coerce_obs(df)), {"unit": main_unit, "multi_unit": multi_unit,
                            "providers": top_providers}


def fetch_series(s, code: str) -> tuple[pd.DataFrame, dict, dict]:
    """Download every page of one series. Returns (obs, unit info, unit code descriptions)."""
    items: list[dict] = []
    unit_desc: dict[str, str] = {}
    dim_desc: dict[str, dict[str, str]] = {}
    page = 1
    while True:
        js = get_json(s, f"{BASE}/Series/Data",
                      params={"seriesCode": code, "pageSize": PAGE_SIZE, "page": page},
                      timeout=300)
        items.extend(js.get("data") or [])
        for att in js.get("attributes") or []:
            if att.get("id") == "Units":
                for c in att.get("codes") or []:
                    unit_desc[c.get("code")] = c.get("description")
        for dim in js.get("dimensions") or []:
            if dim.get("id") in DROP_DIMS:
                continue
            labels = dim_desc.setdefault(dim.get("id"), {})
            for c in dim.get("codes") or []:
                if c.get("code") and c.get("description"):
                    labels[c["code"]] = c["description"]
        total_pages = js.get("totalPages") or 1
        if page >= total_pages:
            break
        page += 1
    df, info = rows_to_frame(code, items)
    info["dim_labels"] = dim_desc
    return df, info, unit_desc


def goal_tree(s) -> list[dict]:
    """Goals with their targets and indicators (titles), for the drill-down menu."""
    goals = get_json(s, f"{BASE}/Goal/List", params={"includechildren": "true"})
    out = []
    for g in goals:
        out.append({
            "code": str(g.get("code")),
            "title": g.get("title"),
            "targets": [
                {
                    "code": t.get("code"),
                    "title": t.get("description") or t.get("title"),
                    "indicators": [
                        {"code": i.get("code"), "title": i.get("description")}
                        for i in (t.get("indicators") or [])
                    ],
                }
                for t in (g.get("targets") or [])
            ],
        })
    return out


def run(ctx) -> SourceResult:
    s = ctx.session
    prev_state = ctx.prev_state.get("unsdg", {})
    prev_releases: dict = prev_state.get("series_release", {})
    prev_units: dict = prev_state.get("series_units", {})
    prev_providers: dict = prev_state.get("series_providers", {})
    prev_dimlabels: dict = prev_state.get("series_dim_labels", {})
    prev_obs = ctx.prev_obs("unsdg")
    deadline = time.time() + float(os.environ.get("GSE_UNSDG_MAX_MINUTES", "240")) * 60

    series_list = get_json(s, f"{BASE}/Series/List", params={"allreleases": "false"})
    only = os.environ.get("GSE_UNSDG_SERIES")
    if only:
        wanted = {x.strip() for x in only.split(",")}
        series_list = [x for x in series_list if x.get("code") in wanted]
    log.info("UN SDG: %d series listed", len(series_list))

    tree = goal_tree(s)
    ind_titles = {i["code"]: i["title"] for g in tree for t in g["targets"] for i in t["indicators"]}

    prev_by_ind = {}
    if prev_obs is not None and not prev_obs.empty:
        prev_by_ind = {k: v for k, v in prev_obs.groupby("indicator_id", sort=False)}

    frames, ind_rows = [], []
    new_releases, new_units, new_providers, new_dimlabels = {}, {}, {}, {}
    to_fetch, fetched, reused = [], 0, 0
    for sr in series_list:
        code = sr["code"]
        iid = f"unsdg:{code}"
        if (prev_releases.get(code) == sr.get("release") and iid in prev_by_ind
                and code in prev_dimlabels):
            frames.append(prev_by_ind[iid])
            new_releases[code] = sr.get("release")
            new_units[code] = prev_units.get(code, "")
            new_providers[code] = prev_providers.get(code, [])
            new_dimlabels[code] = prev_dimlabels.get(code, {})
            reused += 1
        else:
            to_fetch.append(code)

    log.info("UN SDG: %d series changed or new, %d unchanged", len(to_fetch), reused)
    failed, deferred = [], []
    with ThreadPoolExecutor(max_workers=int(os.environ.get("GSE_UNSDG_WORKERS", "4"))) as ex:
        futs = {}
        for code in to_fetch:
            futs[ex.submit(_fetch_guarded, s, code, deadline)] = code
        for fut in as_completed(futs):
            code = futs[fut]
            res = fut.result()
            if res is None or res is DEFERRED:
                (deferred if res is DEFERRED else failed).append(code)
                iid = f"unsdg:{code}"
                if iid in prev_by_ind:  # keep the last good copy
                    frames.append(prev_by_ind[iid])
                    new_releases[code] = prev_releases.get(code)
                    new_units[code] = prev_units.get(code, "")
                    new_providers[code] = prev_providers.get(code, [])
                    new_dimlabels[code] = prev_dimlabels.get(code, {})
                continue
            df, info, unit_desc = res
            frames.append(df)
            sr = next(x for x in series_list if x["code"] == code)
            new_releases[code] = sr.get("release")
            new_units[code] = unit_desc.get(info.get("unit"), info.get("unit", ""))
            new_providers[code] = info.get("providers", [])
            new_dimlabels[code] = info.get("dim_labels", {})
            fetched += 1
            if fetched % 25 == 0:
                log.info("UN SDG: fetched %d/%d", fetched, len(to_fetch))

    for sr in series_list:
        code = sr["code"]
        goals = sorted(set(map(str, sr.get("goal") or [])), key=_num_key)
        targets = sorted(set(sr.get("target") or []), key=_num_key)
        inds = sorted(set(sr.get("indicator") or []), key=_num_key)
        desc = "; ".join(f"{i} {ind_titles.get(i, '')}".strip() for i in inds)
        ind_rows.append({
            "indicator_id": f"unsdg:{code}",
            "source": "unsdg",
            "code": code,
            "name": sr.get("description") or code,
            "unit": new_units.get(code, ""),
            "description": desc,
            "sdg_goals": goals,
            "sdg_targets": targets,
            "sdg_indicators": inds,
            "sdg_mapping": "official",
            "topics": [],
            "source_org": "; ".join(new_providers.get(code) or []),
            "source_url": f"https://unstats.un.org/sdgs/dataportal/database?seriesCode={code}",
            "license": SOURCES["unsdg"]["license"],
            "release": sr.get("release") or "",
            "dim_labels": json.dumps(new_dimlabels.get(code) or {}, ensure_ascii=False),
        })

    obs = normalize_dims(pd.concat(frames, ignore_index=True)) if frames else empty_obs()
    release = max((r for r in new_releases.values() if r), default="")
    notes = []
    if failed:
        notes.append(f"{len(failed)} UN SDG series could not be downloaded this run (previous data kept "
                     "where there was any): " + ", ".join(sorted(failed)[:20]))
    if deferred:
        notes.append(f"UN SDG backfill in progress: {len(deferred)} series still to download; "
                     "they are fetched on the next scheduled runs.")
        log.info("UN SDG: %d series deferred to the next run", len(deferred))
    # Remember which provider reported each series (shown on indicator cards).
    return SourceResult(
        source="unsdg",
        observations=obs,
        indicators=pd.DataFrame(ind_rows),
        release=release,
        state={"series_release": new_releases, "series_units": new_units,
               "series_providers": new_providers, "series_dim_labels": new_dimlabels},
        fetched=fetched,
        reused=reused,
        notes=notes,
        extra={"goal_tree": tree},
    )


DEFERRED = "deferred"


def _fetch_guarded(s, code, deadline):
    if time.time() > deadline:
        return DEFERRED  # time budget for this run used up; fetched on the next run
    try:
        return fetch_series(s, code)
    except Exception as e:  # noqa: BLE001 - one bad series must not stop the run
        log.warning("UN SDG: series %s failed: %s", code, e)
        return None


def _num_key(s: str):
    parts = []
    for p in str(s).replace("-", ".").split("."):
        parts.append((0, int(p), "") if p.isdigit() else (1, 0, p))
    return parts
