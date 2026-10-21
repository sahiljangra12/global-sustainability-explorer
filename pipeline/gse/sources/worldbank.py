"""World Bank World Development Indicators (WDI), sustainability-related topics.

API docs: https://datahelpdesk.worldbank.org/knowledgebase/topics/125589
All WDI indicators tagged with the topics below are fetched for every country
and aggregate, all years. If WDI's "last updated" date has not changed since the
previous run, the previous data is reused.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..geo import register_aggregate
from ..http import get_json
from ..schema import SOURCES, coerce_obs, empty_obs
from . import SourceResult

log = logging.getLogger(__name__)

BASE = "https://api.worldbank.org/v2"
WDI_SOURCE_ID = "2"
# World Bank topic ids -> our topic label (and the SDG goal used when no code rule matches)
TOPICS = {
    "1": ("Agriculture & rural development", "2"),
    "5": ("Energy & mining", "7"),
    "6": ("Environment", None),
    "11": ("Poverty", "1"),
    "19": ("Climate change", "13"),
}

# Approximate SDG mapping by indicator-code prefix (first match wins).
SDG_RULES = [
    (r"^SI\.POV|^SI\.DST", "1"),
    (r"^SN\.ITK|^AG\.YLD|^AG\.PRD|^AG\.CON|^AG\.LND\.(AGRI|ARBL|CREL|CROP|IRIG)", "2"),
    (r"^SH\.STA\.(AIRP|WASH|POIS)|^SH\.DTH", "3"),
    (r"^SH\.H2O|^SH\.STA\.(SMSS|BASS|HYGN|ODFC)|^ER\.H2O", "6"),
    (r"^EG\.", "7"),
    (r"^EN\.POP\.SLUM|^SP\.URB|^EN\.ATM\.PM25|^EN\.URB", "11"),
    (r"^EN\.ATM\.(CO2|GHG|METH|NOXE|HFCG|PFCG|SF6G)|^EN\.GHG|^EN\.CLC|^CC\.", "13"),
    (r"^ER\.MRN|^ER\.FSH|^ER\.PTD\.TOTL\.ZS|^EN\.MAM", "14"),
    (r"^AG\.LND\.FRST|^ER\.LND|^ER\.PTD|^EN\.(BIR|FSH|HPT|MAM)|^ER\.BDV", "15"),
]


def countries(s) -> list[dict]:
    js = get_json(s, f"{BASE}/country", params={"format": "json", "per_page": 400})
    return js[1] if isinstance(js, list) and len(js) > 1 else []


def wdi_last_updated(s) -> str:
    js = get_json(s, f"{BASE}/sources/{WDI_SOURCE_ID}", params={"format": "json"})
    try:
        return js[1][0].get("lastupdated", "")
    except (IndexError, KeyError, TypeError):
        return ""


def topic_indicators(s) -> dict[str, dict]:
    """All WDI indicators in the chosen topics: {code: metadata}."""
    out: dict[str, dict] = {}
    for tid, (label, _goal) in TOPICS.items():
        js = get_json(s, f"{BASE}/topic/{tid}/indicator",
                      params={"format": "json", "per_page": 2000})
        rows = js[1] if isinstance(js, list) and len(js) > 1 and js[1] else []
        for r in rows:
            if (r.get("source") or {}).get("id") != WDI_SOURCE_ID:
                continue
            rec = out.setdefault(r["id"], {**r, "_topics": []})
            rec["_topics"].append(label)
    only = os.environ.get("GSE_WDI_INDICATORS")
    if only:
        wanted = {x.strip() for x in only.split(",")}
        out = {k: v for k, v in out.items() if k in wanted}
    return out


def sdg_for(code: str, topics: list[str]) -> list[str]:
    for pattern, goal in SDG_RULES:
        if re.search(pattern, code):
            return [goal]
    for label, goal in TOPICS.values():
        if label in topics and goal:
            return [goal]
    return []


def unit_from_name(name: str, unit: str) -> str:
    if unit:
        return unit
    m = re.search(r"\(([^()]*)\)\s*$", name or "")
    return m.group(1) if m else ""


def fetch_indicator(s, code: str, geo: dict) -> pd.DataFrame:
    recs, page = [], 1
    while True:
        js = get_json(s, f"{BASE}/country/all/indicator/{code}",
                      params={"format": "json", "per_page": 20000, "page": page,
                              "source": WDI_SOURCE_ID})
        if not isinstance(js, list) or len(js) < 2 or js[1] is None:
            break
        meta, rows = js[0], js[1]
        for r in rows:
            v = r.get("value")
            if v is None:
                continue
            iso = r.get("countryiso3code") or (r.get("country") or {}).get("id") or ""
            if not iso:
                continue
            if iso in geo and geo[iso].get("type") == "country":
                g = iso
            else:
                g = register_aggregate(geo, f"WB-{iso}",
                                       (r.get("country") or {}).get("value", iso), "wdi")
            try:
                year = int(r.get("date"))
            except (TypeError, ValueError):
                continue
            recs.append({"indicator_id": f"wdi:{code}", "geo": g, "year": year,
                         "value": v, "dims": "", "flag": r.get("obs_status") or ""})
        if page >= int(meta.get("pages") or 1):
            break
        page += 1
    if not recs:
        return empty_obs()
    return coerce_obs(pd.DataFrame(recs))


def run(ctx) -> SourceResult:
    s = ctx.session
    prev_state = ctx.prev_state.get("wdi", {})
    prev_obs = ctx.prev_obs("wdi")
    last_updated = wdi_last_updated(s)
    meta = topic_indicators(s)
    log.info("WDI: %d indicators in scope, last updated %s", len(meta), last_updated)

    ind_rows = []
    for code, r in meta.items():
        topics = sorted(set(r["_topics"]))
        name = (r.get("name") or code).strip()
        ind_rows.append({
            "indicator_id": f"wdi:{code}",
            "source": "wdi",
            "code": code,
            "name": name,
            "unit": unit_from_name(name, (r.get("unit") or "").strip()),
            "description": (r.get("sourceNote") or "").strip(),
            "sdg_goals": sdg_for(code, topics),
            "sdg_targets": [],
            "sdg_indicators": [],
            "sdg_mapping": "approximate",
            "topics": topics,
            "source_org": (r.get("sourceOrganization") or "").strip(),
            "source_url": f"https://data.worldbank.org/indicator/{code}",
            "license": SOURCES["wdi"]["license"],
            "release": last_updated,
        })
    indicators = pd.DataFrame(ind_rows)

    same_codes = set(prev_state.get("codes", [])) == set(meta)
    if (last_updated and prev_state.get("last_updated") == last_updated and same_codes
            and prev_obs is not None and not prev_obs.empty):
        log.info("WDI unchanged since last run; reusing previous data")
        # Re-register WDI aggregates so the geo table stays complete.
        for g in prev_obs["geo"].unique():
            if str(g).startswith("WB-") and g not in ctx.geo:
                register_aggregate(ctx.geo, g, g[3:], "wdi")
        return SourceResult("wdi", prev_obs, indicators, last_updated,
                            state=prev_state, fetched=0, reused=len(meta))

    frames, failed = [], []
    prev_by_ind = ({k: v for k, v in prev_obs.groupby("indicator_id", sort=False)}
                   if prev_obs is not None and not prev_obs.empty else {})
    with ThreadPoolExecutor(max_workers=int(os.environ.get("GSE_WDI_WORKERS", "4"))) as ex:
        futs = {ex.submit(fetch_indicator, s, code, ctx.geo): code for code in meta}
        for i, fut in enumerate(as_completed(futs), 1):
            code = futs[fut]
            try:
                frames.append(fut.result())
            except Exception as e:  # noqa: BLE001
                log.warning("WDI: %s failed: %s", code, e)
                failed.append(code)
                if f"wdi:{code}" in prev_by_ind:
                    frames.append(prev_by_ind[f"wdi:{code}"])
            if i % 50 == 0:
                log.info("WDI: %d/%d indicators", i, len(meta))
    obs = pd.concat(frames, ignore_index=True) if frames else empty_obs()
    notes = [f"{len(failed)} WDI indicators failed and kept previous data"] if failed else []
    return SourceResult("wdi", obs, indicators, last_updated,
                        state={"last_updated": last_updated, "codes": sorted(meta)},
                        fetched=len(meta) - len(failed), reused=0, notes=notes)
