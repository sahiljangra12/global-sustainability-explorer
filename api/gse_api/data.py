"""Load the latest data bundle and answer structured queries with DuckDB.

Every function here returns plain rows from the database. These are the only
"tools" the language model can use, so every number it reports comes from here.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tarfile
import tempfile
import unicodedata
from pathlib import Path

import duckdb
import httpx

log = logging.getLogger(__name__)

DEFAULT_DATA_URL = ("https://github.com/sahiljangra12/global-sustainability-explorer/"
                    "releases/download/data-latest/gse-data.tar.gz")
SOURCE_NAMES = {"unsdg": "UN SDG Global Database", "wdi": "World Bank WDI",
                "owid": "Our World in Data"}
WORLD = {"unsdg": "UN-001", "wdi": "WB-WLD", "owid": "OWID-World"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9.% ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("es") and not w.endswith("ses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


STOP = set("""a an the of in on for to and or vs versus compare what is are was how much many show
me give data about which country countries region world global year years over time trend
latest recent""".split())


def clean(v):
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(f"{v:.6g}")
    return v


class Database:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.con = duckdb.connect()
        obs = (self.dir / "observations.parquet").as_posix()
        ind = (self.dir / "indicators.parquet").as_posix()
        self.con.execute(f"CREATE VIEW obs AS SELECT * FROM read_parquet('{obs}')")
        self.con.execute(f"CREATE VIEW ind AS SELECT * FROM read_parquet('{ind}')")
        self.indicators = {r["indicator_id"]: r for r in self._dicts("SELECT * FROM ind")}
        cov = self._dicts("""SELECT indicator_id, min(year) y0, max(year) y1,
                                    count(DISTINCT geo) ng, bool_or(dims = '') has_total
                             FROM obs GROUP BY 1""")
        for c in cov:
            if c["indicator_id"] in self.indicators:
                self.indicators[c["indicator_id"]].update(c)
        geo = json.loads((self.dir / "geo.json").read_text())
        self.geo = {g["code"]: g for g in geo}
        self.alias = {}
        order = {"aggregate": 0, "group": 1, "region": 2, "country": 3}
        for g in sorted(geo, key=lambda g: order.get(g["type"], 0)):
            for n in [g["name"], g["code"], *(g.get("aliases") or [])]:
                if n:
                    self.alias[norm(n)] = g["code"]
        meta = self.dir / "meta.json"
        self.meta = json.loads(meta.read_text()) if meta.exists() else {}
        news = self.dir / "news.json"
        self.news = json.loads(news.read_text()).get("items", []) if news.exists() else []
        self._tokens = {}
        for iid, r in self.indicators.items():
            words = norm(f"{r['name']} {r['code']} {' '.join(r.get('topics') or [])}").split()
            self._tokens[iid] = {stem(w) for w in words}

    def _dicts(self, sql, params=None):
        cur = self.con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [{c: clean(v) if not hasattr(v, "tolist") else v.tolist() for c, v in zip(cols, row)}
                for row in cur.fetchall()]

    # ---------- tools ----------
    def search_indicators(self, query: str, goal: str | None = None, limit: int = 8) -> list[dict]:
        q = norm(query)
        words = [stem(w) for w in q.split() if w not in STOP]
        want_pc = bool(re.search(r"per capita|per person", q))
        out = []
        for iid, r in self.indicators.items():
            goals = list(r.get("sdg_goals") or [])
            if goal and str(goal) not in goals:
                continue
            toks = self._tokens[iid]
            hits = sum(1 for w in words if w in toks)
            if not hits:
                continue
            name = norm(r["name"])
            score = hits * 3 + (3 if hits == len(words) else 0)
            pc = "per capita" in name or "per person" in name
            score += 3 if (want_pc and pc) else (-2 if pc and not want_pc else 0)
            score -= len(toks) * 0.06
            score += min(r.get("ng") or 0, 200) / 200 + (0.5 if (r.get("y1") or 0) >= 2020 else 0)
            score += {"unsdg": 0.4, "wdi": 0.2}.get(r["source"], 0)
            if not r.get("has_total"):
                score -= 1
            out.append((score, iid))
        out.sort(reverse=True)
        return [self.indicator_info(iid, brief=True) for _, iid in out[:limit]]

    def indicator_info(self, indicator_id: str, brief: bool = False) -> dict:
        r = self.indicators.get(indicator_id)
        if not r:
            return {"error": f"Unknown indicator {indicator_id}"}
        info = {"indicator_id": indicator_id, "name": r["name"], "unit": r.get("unit") or "",
                "source": SOURCE_NAMES.get(r["source"], r["source"]), "code": r["code"],
                "years": f"{r.get('y0')}-{r.get('y1')}", "places": r.get("ng"),
                "sdg_goals": list(r.get("sdg_goals") or []),
                "sdg_mapping": r.get("sdg_mapping") or ""}
        if not brief:
            info.update({"description": (r.get("description") or "")[:800],
                         "produced_by": r.get("source_org") or "", "release": r.get("release") or "",
                         "license": r.get("license") or "", "url": r.get("source_url") or "",
                         "breakdowns": self.breakdowns(indicator_id)})
        return info

    def breakdowns(self, indicator_id: str) -> list[str]:
        rows = self.con.execute("SELECT DISTINCT dims FROM obs WHERE indicator_id = ? LIMIT 60",
                                [indicator_id]).fetchall()
        return sorted(r[0] for r in rows)

    def resolve_geo(self, names: list[str], indicator_id: str | None = None) -> list[dict]:
        out = []
        src = self.indicators.get(indicator_id, {}).get("source") if indicator_id else None
        for n in names:
            key = norm(n)
            if key in ("world", "global", "worldwide"):
                code = WORLD.get(src or "unsdg")
            else:
                code = self.alias.get(key)
            if not code:
                out.append({"query": n, "error": "not found"})
                continue
            g = self.geo.get(code, {})
            out.append({"query": n, "code": code, "name": g.get("name", code), "type": g.get("type"),
                        "members": (g.get("members") or [])[:300] if g.get("type") != "country" else None})
        return out

    def _default_dims(self, indicator_id: str, dims: str | None) -> str:
        if dims is not None:
            return dims
        bd = self.breakdowns(indicator_id)
        return "" if "" in bd else (bd[0] if bd else "")

    def get_series(self, indicator_id: str, geos: list[str], year_from: int | None = None,
                   year_to: int | None = None, dims: str | None = None, limit: int = 400) -> dict:
        d = self._default_dims(indicator_id, dims)
        rows = self._dicts(
            """SELECT geo, year, value, flag FROM obs
               WHERE indicator_id = ? AND dims = ? AND list_contains(?, geo)
                 AND year >= ? AND year <= ? ORDER BY geo, year LIMIT ?""",
            [indicator_id, d, list(geos), year_from or 0, year_to or 9999, limit])
        for r in rows:
            r["place"] = self.geo.get(r["geo"], {}).get("name", r["geo"])
        return {"indicator_id": indicator_id, "breakdown": d or "total",
                "unit": self.indicators.get(indicator_id, {}).get("unit", ""), "rows": rows}

    def trend(self, indicator_id: str, geo: str, year_from: int | None = None,
              year_to: int | None = None, dims: str | None = None) -> dict:
        s = self.get_series(indicator_id, [geo], year_from, year_to, dims)
        rows = s["rows"]
        if len(rows) < 2:
            return {**s, "error": "fewer than two values in that range"}
        a, b = rows[0], rows[-1]
        out = {"indicator_id": indicator_id, "place": a["place"], "unit": s["unit"],
               "breakdown": s["breakdown"], "first": {"year": a["year"], "value": a["value"]},
               "last": {"year": b["year"], "value": b["value"]},
               "absolute_change": clean(b["value"] - a["value"])}
        if a["value"]:
            out["percent_change"] = clean((b["value"] - a["value"]) / abs(a["value"]) * 100)
        yrs = b["year"] - a["year"]
        if a["value"] > 0 and b["value"] > 0 and yrs > 0:
            out["compound_annual_growth_percent"] = clean(((b["value"] / a["value"]) ** (1 / yrs) - 1) * 100)
        return out

    def compare(self, indicator_id: str, geos: list[str], year: int | None = None,
                dims: str | None = None) -> dict:
        d = self._default_dims(indicator_id, dims)
        rows = self._dicts(
            """SELECT geo, max(year) AS year, arg_max(value, year) AS value
               FROM obs WHERE indicator_id = ? AND dims = ? AND list_contains(?, geo) AND year <= ?
               GROUP BY geo ORDER BY value DESC""",
            [indicator_id, d, list(geos), year or 9999])
        for r in rows:
            r["place"] = self.geo.get(r["geo"], {}).get("name", r["geo"])
        return {"indicator_id": indicator_id, "breakdown": d or "total", "unit":
                self.indicators.get(indicator_id, {}).get("unit", ""),
                "note": "latest value at or before the requested year for each place", "rows": rows}

    def rank(self, indicator_id: str, year: int | None = None, n: int = 10, order: str = "desc",
             dims: str | None = None) -> dict:
        d = self._default_dims(indicator_id, dims)
        countries = [c for c, g in self.geo.items() if g.get("type") == "country"]
        if year is None:
            counts = self.con.execute(
                """SELECT year, count(*) c FROM obs WHERE indicator_id = ? AND dims = ?
                   AND list_contains(?, geo) GROUP BY year""", [indicator_id, d, countries]).fetchall()
            if not counts:
                return {"error": "no country values"}
            best = max(c for _, c in counts)
            year = max(y for y, c in counts if c >= best * 0.6)
        direction = "ASC" if order == "asc" else "DESC"
        rows = self._dicts(
            f"""SELECT geo, value, flag FROM obs WHERE indicator_id = ? AND dims = ? AND year = ?
                AND list_contains(?, geo) ORDER BY value {direction} LIMIT ?""",
            [indicator_id, d, year, countries, max(1, min(int(n), 50))])
        total = self.con.execute(
            "SELECT count(*) FROM obs WHERE indicator_id = ? AND dims = ? AND year = ? AND list_contains(?, geo)",
            [indicator_id, d, year, countries]).fetchone()[0]
        for i, r in enumerate(rows, 1):
            r["rank"] = i
            r["place"] = self.geo.get(r["geo"], {}).get("name", r["geo"])
        return {"indicator_id": indicator_id, "year": year, "countries_with_data": total,
                "unit": self.indicators.get(indicator_id, {}).get("unit", ""), "rows": rows}

    def search_news(self, query: str, limit: int = 8) -> list[dict]:
        words = [w for w in norm(query).split() if w not in STOP]
        scored = []
        for it in self.news:
            text = norm(f"{it.get('title', '')} {it.get('teaser', '')} {it.get('event') or ''}")
            hits = sum(1 for w in words if w in text)
            if hits:
                scored.append((hits, it.get("date") or "", it))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [{k: it.get(k) for k in ("title", "source", "date", "url", "event")}
                for _, _, it in scored[:limit]]


def download_bundle(url: str | None = None, dest: Path | None = None) -> Path:
    url = url or os.environ.get("DATA_URL", DEFAULT_DATA_URL)
    dest = dest or Path(os.environ.get("DATA_DIR", tempfile.gettempdir())) / "gse-data"
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / "bundle.tar.gz"
    log.info("Downloading data bundle from %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with archive.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    with tarfile.open(archive) as t:
        t.extractall(dest, filter="data")
    archive.unlink()
    inner = dest / "data"
    return inner if inner.exists() else dest
