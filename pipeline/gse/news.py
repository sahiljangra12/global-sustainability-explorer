"""News feed: pull RSS feeds, de-duplicate, tag by SDG / country / conference.

Each item keeps only the headline, link, date, source and the feed's own short
teaser (trimmed). Full articles are never copied: the site links out.

    python -m gse.news --out build/site-data --prev prev/site-data/news.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import pycountry
import yaml

from .geo import EXTRA_ALIASES, DISPLAY_NAMES, norm
from .http import session

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
KEEP_DAYS = 60
MAX_ITEMS = 600
TEASER_CHARS = 240

# Keyword rules for tagging news with SDGs (lower-case substrings / regexes).
SDG_KEYWORDS = {
    "1": [r"\bpoverty\b", r"\bpoor\b", "social protection"],
    "2": [r"\bhunger\b", "food security", "famine", "malnutrition", r"\bagricultur", r"\bcrops?\b"],
    "3": [r"\bhealth\b", "disease", "pandemic", "malaria", "air pollution", "mortality"],
    "4": [r"\beducation\b", r"\bschools?\b", "literacy"],
    "5": ["gender", r"\bwomen\b", r"\bgirls\b"],
    "6": [r"\bwater\b", "sanitation", "drought", r"\brivers?\b", "groundwater"],
    "7": [r"\benergy\b", "renewable", r"\bsolar\b", r"\bwind\b", r"\bcoal\b", r"\boil\b",
          r"\bgas\b", "electricity", "battery", "batteries", "power grid", "nuclear"],
    "8": [r"\bjobs?\b", "employment", "economic growth", "decent work", "labour", "labor"],
    "9": ["infrastructure", "industry", "innovation"],
    "10": ["inequality", "migrants", "migration", "refugee"],
    "11": [r"\bcities\b", r"\bcity\b", r"\burban\b", "housing", "heatwave", "heat wave"],
    "12": ["plastic", "waste", "circular economy", "consumption", "recycl"],
    "13": [r"\bclimate\b", "emission", r"\bco2\b", "carbon", "warming", r"\b1\.5", "net zero",
           "net-zero", r"\bndcs?\b", "paris agreement", "ipcc", "unfccc", "adaptation",
           "loss and damage", "methane", "greenhouse"],
    "14": [r"\bocean", r"\bmarine\b", r"\bseas?\b", "fisheries", "fishing", "coral",
           "high seas", "coastal"],
    "15": ["biodiversity", "forest", "deforestation", "species", "wildlife", "ecosystem",
           "land degradation", "desertification", "nature"],
    "16": [r"\bpeace\b", r"\bconflict\b", r"\bwar\b", "justice", "corruption"],
    "17": ["finance", "funding", r"\baid\b", "debt", "partnership", "development bank"],
}
_SDG_RX = {g: re.compile("|".join(k), re.I) for g, k in SDG_KEYWORDS.items()}


def clean_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"The post .* appeared first on .*$", "", s).strip()
    return s


def teaser(s: str, n: int = TEASER_CHARS) -> str:
    s = clean_text(s)
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def tag_sdgs(text: str) -> list[str]:
    return [g for g, rx in _SDG_RX.items() if rx.search(text)]


def country_matcher(geo_path: Path | None):
    """Return a function text -> [ISO3] using country names and aliases."""
    names: dict[str, str] = {}
    if geo_path and geo_path.exists():
        for rec in json.loads(geo_path.read_text()):
            if rec.get("type") != "country":
                continue
            for n in [rec["name"], *rec.get("aliases", [])]:
                if n and len(n) > 3:
                    names[norm(n)] = rec["code"]
    else:
        for c in pycountry.countries:
            for n in (c.name, getattr(c, "common_name", None)):
                if n and len(n) > 3:
                    names[norm(n)] = c.alpha_3
        for iso, alist in EXTRA_ALIASES.items():
            for n in alist:
                if len(n) > 3:
                    names[norm(n)] = iso
        for iso, n in DISPLAY_NAMES.items():
            names[norm(n)] = iso
    # Longest names first so "South Africa" wins over "Africa"-like fragments.
    keys = sorted(names, key=len, reverse=True)
    rx = re.compile(r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b") if keys else None

    def match(text: str) -> list[str]:
        if not rx:
            return []
        found = {names[m.group(1)] for m in rx.finditer(norm(text))}
        return sorted(found)

    return match


def load_events() -> list[dict]:
    data = yaml.safe_load((ROOT / "config" / "events.yaml").read_text())
    return data.get("events", [])


def event_status(ev: dict, today: dt.date) -> str:
    start = dt.date.fromisoformat(str(ev["start"]))
    end = dt.date.fromisoformat(str(ev["end"]))
    if today < start:
        return "upcoming"
    if today > end:
        return "past"
    return "live"


def tag_event(text: str, events: list[dict]) -> str | None:
    low = text.lower()
    for ev in events:
        for k in ev.get("keywords", []):
            if k.lower() in low:
                return ev["short"]
    return None


def parse_date(entry) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc).isoformat()
    return None


def fetch_feed(s, feed: dict) -> tuple[list[dict], str | None]:
    try:
        r = s.get(feed["url"], timeout=30)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"[:200]
    if parsed.bozo and not parsed.entries:
        return [], f"not a readable feed ({parsed.bozo_exception})"[:200]
    items = []
    for e in parsed.entries:
        link = e.get("link")
        title = clean_text(e.get("title", ""))
        if not link or not title:
            continue
        items.append({
            "title": title,
            "url": link,
            "date": parse_date(e),
            "source": feed["name"],
            "kind": feed.get("kind", "specialist"),
            "teaser": teaser(e.get("summary") or e.get("description") or ""),
        })
    return items, None


def similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, norm(a), norm(b)).ratio() > 0.9


def build_news(out_dir: Path, prev_path: Path | None, geo_path: Path | None,
               today: dt.date | None = None, feeds: list[dict] | None = None,
               fetch=None) -> dict:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    cfg = yaml.safe_load((ROOT / "config" / "news_sources.yaml").read_text())
    feeds = feeds if feeds is not None else cfg["feeds"]
    events = load_events()
    s = session(retries=2, backoff=1)
    fetch = fetch or (lambda f: fetch_feed(s, f))
    match_countries = country_matcher(geo_path)

    previous = []
    if prev_path and prev_path.exists():
        try:
            previous = json.loads(prev_path.read_text()).get("items", [])
        except (ValueError, OSError):
            previous = []

    feed_status, fresh = [], []
    for f in feeds:
        items, err = fetch(f)
        feed_status.append({"name": f["name"], "url": f["url"], "ok": err is None,
                            "items": len(items), "error": err})
        if err:
            log.warning("News feed %s failed: %s", f["name"], err)
        fresh.extend(items)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    by_url = {it["url"]: it for it in previous}
    for it in fresh:
        it.setdefault("date", None)
        if not it["date"]:
            it["date"] = by_url.get(it["url"], {}).get("date") or now_iso
        by_url[it["url"]] = {**by_url.get(it["url"], {}), **it}

    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = [it for it in by_url.values() if (it.get("date") or "") >= cutoff]
    items.sort(key=lambda it: it.get("date") or "", reverse=True)

    deduped: list[dict] = []
    for it in items:
        if any(similar(it["title"], d["title"]) for d in deduped[-80:]):
            continue
        text = f"{it['title']} {it.get('teaser', '')}"
        it["sdgs"] = tag_sdgs(text)
        it["countries"] = match_countries(text)
        it["event"] = tag_event(text, events)
        deduped.append(it)
    deduped = deduped[:MAX_ITEMS]

    ev_out = []
    for ev in events:
        ev_out.append({k: (str(v) if k in ("start", "end") else v) for k, v in ev.items()
                       if k != "keywords"} | {"status": event_status(ev, today)})

    out = {"generated": now_iso, "feeds": feed_status, "events": ev_out, "items": deduped}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "news.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    log.info("News: %d items from %d/%d feeds", len(deduped),
             sum(f["ok"] for f in feed_status), len(feed_status))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "site-data")
    ap.add_argument("--prev", type=Path, default=None)
    ap.add_argument("--geo", type=Path, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build_news(args.out, args.prev, args.geo or args.out / "geo.json")


if __name__ == "__main__":
    main()
