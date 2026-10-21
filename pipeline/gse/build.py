"""Build the dataset: fetch every source, validate, diff, and export.

    python -m gse.build --prev prev --out build [--sources unsdg,wdi,owid]

prev/ holds the previous build (downloaded from the "data-latest" GitHub release),
so unchanged series are reused and a failed source keeps its last good data.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import geo as geomod
from .export import export_parquet, export_site
from .http import get_json, session
from .schema import INDICATOR_COLUMNS, SOURCES, empty_obs
from .sources import owid, unsdg, worldbank
from .validate import ValidationError, diff, validate_source

log = logging.getLogger("gse.build")

RUNNERS = {"unsdg": unsdg.run, "wdi": worldbank.run, "owid": owid.run}
CHANGELOG_KEEP = 200


@dataclass
class Context:
    session: object
    geo: dict
    prev_state: dict
    prev_all: pd.DataFrame | None
    prev_indicators: pd.DataFrame | None
    _cache: dict = field(default_factory=dict)

    def prev_obs(self, source: str) -> pd.DataFrame | None:
        if self.prev_all is None or self.prev_all.empty:
            return None
        if source not in self._cache:
            mask = self.prev_all["indicator_id"].str.startswith(f"{source}:")
            self._cache[source] = self.prev_all[mask].reset_index(drop=True)
        return self._cache[source]


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def load_prev(prev: Path):
    d = prev / "data"
    obs = pd.read_parquet(d / "observations.parquet") if (d / "observations.parquet").exists() else None
    ind = pd.read_parquet(d / "indicators.parquet") if (d / "indicators.parquet").exists() else None
    if obs is not None:
        for c in ("indicator_id", "geo", "dims", "flag"):
            obs[c] = obs[c].astype("string")
    return (obs, ind, _read_json(d / "state.json", {}), _read_json(d / "geo.json", []),
            _read_json(d / "changelog.json", []), _read_json(d / "updated.json", {}),
            _read_json(d / "goal_tree.json", []))


def build_geography(s, prev_geo: list[dict]) -> tuple[dict, list[str]]:
    notes = []
    try:
        tree = get_json(s, f"{unsdg.BASE}/GeoArea/Tree", timeout=180)
    except Exception as e:  # noqa: BLE001
        tree = None
        notes.append(f"UN geography tree unavailable ({e}); reused previous geography")
    try:
        wb = worldbank.countries(s)
    except Exception as e:  # noqa: BLE001
        wb = []
        notes.append(f"World Bank country list unavailable ({e})")
    if tree is None:
        if not prev_geo:
            raise RuntimeError("No UN geography tree and no previous build to fall back on")
        return {r["code"]: r for r in prev_geo}, notes
    geo = geomod.build_geo(tree, wb)
    # Keep aggregates registered by sources in earlier runs (names survive reuse).
    for r in prev_geo:
        if r.get("type") == "aggregate" and r["code"] not in geo:
            geo[r["code"]] = r
        elif r.get("type") == "country" and r["code"] not in geo:
            geo[r["code"]] = r
    return geo, notes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prev", type=Path, default=Path("prev"))
    ap.add_argument("--out", type=Path, default=Path("build"))
    ap.add_argument("--sources", default="unsdg,wdi,owid")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    t0 = time.time()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    prev_obs, prev_ind, prev_state, prev_geo, changelog, updated, prev_tree = load_prev(args.prev)
    s = session()
    wanted = [x.strip() for x in args.sources.split(",") if x.strip()]
    if not wanted and prev_geo:
        # Re-export only (e.g. a news refresh): reuse everything from the previous build.
        geo, notes = {r["code"]: r for r in prev_geo}, []
    else:
        geo, notes = build_geography(s, prev_geo)
    ctx = Context(session=s, geo=geo, prev_state=prev_state, prev_all=prev_obs,
                  prev_indicators=prev_ind)

    obs_parts, ind_parts, state, summary = [], [], dict(prev_state), {}
    goal_tree = prev_tree
    for src in RUNNERS:
        prev_src = ctx.prev_obs(src)
        prev_src_ind = (prev_ind[prev_ind["source"] == src]
                        if prev_ind is not None and not prev_ind.empty else None)
        if src not in wanted:
            if prev_src is not None and not prev_src.empty:
                obs_parts.append(prev_src)
                ind_parts.append(prev_src_ind)
                # Not checked this run (e.g. a news-only refresh): keep the last check's summary.
                summary[src] = prev_state.get(f"_{src}_summary") or {"status": "ok"}
            continue
        log.info("=== %s ===", src)
        ts = time.time()
        try:
            res = RUNNERS[src](ctx)
            obs, warns = validate_source(src, res.observations, res.indicators,
                                         len(prev_src) if prev_src is not None else None)
            notes.extend(warns + res.notes)
            ind = res.indicators[res.indicators["indicator_id"].isin(obs["indicator_id"].unique())]
            obs_parts.append(obs)
            ind_parts.append(ind)
            state[src] = res.state
            if res.extra.get("goal_tree"):
                goal_tree = res.extra["goal_tree"]
            summary[src] = {"status": "ok", "release": res.release, "fetched": res.fetched,
                            "reused": res.reused, "indicators": int(ind["indicator_id"].nunique()),
                            "rows": int(len(obs)), "seconds": round(time.time() - ts)}
        except (ValidationError, Exception) as e:  # noqa: BLE001
            log.exception("%s failed", src)
            notes.append(f"{SOURCES[src]['name']}: update failed ({e}); kept previous data")
            if prev_src is not None and not prev_src.empty:
                obs_parts.append(prev_src)
                ind_parts.append(prev_src_ind)
            summary[src] = {**prev_state.get(f"_{src}_summary", {}), "status": "failed",
                            "error": str(e)[:300]}
        state[f"_{src}_summary"] = summary[src]

    obs = pd.concat([o for o in obs_parts if o is not None], ignore_index=True) if obs_parts else empty_obs()
    ind_frames = [i for i in ind_parts if i is not None and not i.empty]
    indicators = (pd.concat(ind_frames, ignore_index=True) if ind_frames
                  else pd.DataFrame(columns=INDICATOR_COLUMNS))
    indicators = indicators.drop_duplicates("indicator_id", keep="first")
    if obs.empty:
        raise SystemExit("Build produced no data at all; refusing to publish.")

    log.info("Diffing against previous build")
    d = diff(prev_obs, obs)
    for iid in d["new_indicators"]:
        updated[iid] = today
    for iid in d["changed"]:
        updated[iid] = today
    updated = {k: v for k, v in updated.items() if k in set(indicators["indicator_id"])}

    names = dict(zip(indicators["indicator_id"], indicators["name"]))
    something_changed = bool(d["new_indicators"] or d["changed"] or d["removed_indicators"])
    if something_changed or not changelog:
        top = sorted(d["changed"].items(),
                     key=lambda kv: -(kv[1].get("added", 0) + kv[1].get("revised", 0)))[:60]
        changelog.insert(0, {
            "date": today,
            "sources": {k: {kk: vv for kk, vv in v.items() if kk in ("status", "release")}
                        for k, v in summary.items()},
            "new_indicators": len(d["new_indicators"]),
            "new_indicator_ids": d["new_indicators"][:60],
            "removed_indicators": d["removed_indicators"][:60],
            "changed_indicators": len(d["changed"]),
            "changed": [{"id": iid, "name": names.get(iid, iid), **c} for iid, c in top],
            "added_rows": d["added_rows"],
            "revised_values": d["revised_values"],
            "removed_rows": d["removed_rows"],
            "notes": notes,
        })
    changelog = changelog[:CHANGELOG_KEEP]

    data_dir = args.out / "data"
    export_parquet(data_dir, obs, indicators, geo)
    (data_dir / "state.json").write_text(json.dumps(state, default=str))
    (data_dir / "changelog.json").write_text(json.dumps(changelog, default=str))
    (data_dir / "updated.json").write_text(json.dumps(updated))
    (data_dir / "goal_tree.json").write_text(json.dumps(goal_tree))

    site = args.out / "site-data"
    sources_meta = {k: {**SOURCES[k], **summary.get(k, {})} for k in RUNNERS}
    export_site(site, obs, indicators, geo, goal_tree, sources_meta, updated)
    (site / "changelog.json").write_text(json.dumps(changelog[:30], default=str))
    meta = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(), "sources": sources_meta,
            "rows": int(len(obs)), "indicators": int(indicators["indicator_id"].nunique()),
            "geos": len(geo), "notes": notes, "seconds": round(time.time() - t0)}
    (site / "meta.json").write_text(json.dumps(meta, default=str))
    (data_dir / "meta.json").write_text(json.dumps(meta, default=str))
    log.info("Done: %s rows, %s indicators in %ss", f"{len(obs):,}", meta["indicators"],
             meta["seconds"])
    print(json.dumps({"summary": summary, "changed": something_changed}, default=str))


if __name__ == "__main__":
    main()
