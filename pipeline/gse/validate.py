"""Validation and change detection between the previous and the new build."""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

KEY = ["indicator_id", "geo", "year", "dims"]
MAX_ROW_DROP = float(os.environ.get("GSE_MAX_ROW_DROP", "0.10"))


class ValidationError(Exception):
    pass


def validate_source(source: str, obs: pd.DataFrame, indicators: pd.DataFrame,
                    prev_rows: int | None) -> tuple[pd.DataFrame, list[str]]:
    """Return cleaned observations and warnings; raise ValidationError to reject the source."""
    warnings = []
    if obs.empty:
        raise ValidationError(f"{source}: no observations")
    missing = set(KEY + ["value", "flag"]) - set(obs.columns)
    if missing:
        raise ValidationError(f"{source}: missing columns {sorted(missing)}")
    if not np.isfinite(obs["value"]).all():
        bad = (~np.isfinite(obs["value"])).sum()
        warnings.append(f"{source}: dropped {bad} non-finite values")
        obs = obs[np.isfinite(obs["value"])]
    dupes = obs.duplicated(KEY).sum()
    if dupes:
        warnings.append(f"{source}: dropped {dupes} duplicate rows")
        obs = obs.drop_duplicates(KEY, keep="first")
    unknown = set(obs["indicator_id"].unique()) - set(indicators["indicator_id"])
    if unknown:
        warnings.append(f"{source}: {len(unknown)} indicators without metadata were dropped")
        obs = obs[~obs["indicator_id"].isin(unknown)]
    years = obs["year"]
    if years.min() < 1700 or years.max() > 2100:
        warnings.append(f"{source}: years outside 1700–2100 dropped")
        obs = obs[(years >= 1700) & (years <= 2100)]
    if prev_rows and len(obs) < prev_rows * (1 - MAX_ROW_DROP):
        raise ValidationError(
            f"{source}: row count fell from {prev_rows:,} to {len(obs):,} "
            f"(more than {MAX_ROW_DROP:.0%}); keeping the previous data")
    return obs.reset_index(drop=True), warnings


def diff(prev: pd.DataFrame | None, new: pd.DataFrame) -> dict:
    """Summarise what changed per indicator between two builds."""
    if prev is None or prev.empty:
        per = new.groupby("indicator_id").agg(rows=("value", "size"), y1=("year", "max"))
        return {"new_indicators": sorted(per.index), "removed_indicators": [],
                "changed": {}, "added_rows": int(len(new)), "revised_values": 0,
                "removed_rows": 0}
    p_ids, n_ids = set(prev["indicator_id"].unique()), set(new["indicator_id"].unique())
    common = p_ids & n_ids
    p = prev[prev["indicator_id"].isin(common)]
    n = new[new["indicator_id"].isin(common)]
    m = n.merge(p, on=KEY, how="outer", suffixes=("", "_old"), indicator=True)
    added = m[m["_merge"] == "left_only"]
    removed = m[m["_merge"] == "right_only"]
    both = m[m["_merge"] == "both"]
    revised = both[~np.isclose(both["value"], both["value_old"], rtol=1e-9, atol=0)]
    changed: dict[str, dict] = {}
    for name, frame in (("added", added), ("revised", revised), ("removed", removed)):
        for iid, cnt in frame.groupby("indicator_id").size().items():
            changed.setdefault(iid, {})[name] = int(cnt)
    new_max = n.groupby("indicator_id")["year"].max()
    old_max = p.groupby("indicator_id")["year"].max()
    for iid in common:
        if iid in changed and new_max.get(iid, 0) > old_max.get(iid, 0):
            changed[iid]["new_latest_year"] = int(new_max[iid])
    return {
        "new_indicators": sorted(n_ids - p_ids),
        "removed_indicators": sorted(p_ids - n_ids),
        "changed": changed,
        "added_rows": int(len(added)),
        "revised_values": int(len(revised)),
        "removed_rows": int(len(removed)),
    }
