"""Source connectors. Each module exposes `run(ctx) -> SourceResult`."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class SourceResult:
    source: str
    observations: pd.DataFrame
    indicators: pd.DataFrame
    release: str  # the source's own version label or last-updated date
    state: dict = field(default_factory=dict)  # saved for change detection next run
    fetched: int = 0  # how many items were downloaded fresh this run
    reused: int = 0  # how many items were reused unchanged from the previous run
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # e.g. the SDG goal tree from unsdg
