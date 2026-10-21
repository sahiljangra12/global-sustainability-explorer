"""Geography: one table of countries, regions and groups that every source maps onto.

Countries use ISO 3166 alpha-3 codes. Regions follow the UN M49 geoscheme
(World > continent > sub-region > country) and use "UN-<m49>" codes. Aggregates
that only one source publishes (World Bank income groups, OWID's "Asia (GCP)")
keep a source prefix ("WB-", "OWID-") so different definitions are never mixed.
"""

from __future__ import annotations

import logging
import re
import unicodedata

import pycountry

log = logging.getLogger(__name__)

# Common names people type that pycountry does not carry.
EXTRA_ALIASES = {
    "USA": ["US", "U.S.", "U.S.A.", "United States", "America", "United States of America"],
    "GBR": ["UK", "U.K.", "Britain", "Great Britain", "England"],
    "RUS": ["Russia"],
    "KOR": ["South Korea", "Korea", "Republic of Korea"],
    "PRK": ["North Korea", "DPRK"],
    "IRN": ["Iran"],
    "VNM": ["Vietnam", "Viet Nam"],
    "SYR": ["Syria"],
    "LAO": ["Laos", "Lao PDR"],
    "BOL": ["Bolivia"],
    "VEN": ["Venezuela"],
    "TZA": ["Tanzania"],
    "CZE": ["Czech Republic", "Czechia"],
    "TUR": ["Turkey", "Türkiye", "Turkiye"],
    "CIV": ["Ivory Coast", "Cote d'Ivoire", "Côte d'Ivoire"],
    "COD": ["DRC", "DR Congo", "Democratic Republic of the Congo", "Congo-Kinshasa"],
    "COG": ["Congo", "Republic of the Congo", "Congo-Brazzaville"],
    "MDA": ["Moldova"],
    "FSM": ["Micronesia"],
    "PSE": ["Palestine", "State of Palestine"],
    "TWN": ["Taiwan"],
    "CPV": ["Cape Verde", "Cabo Verde"],
    "SWZ": ["Swaziland", "Eswatini"],
    "MKD": ["Macedonia", "North Macedonia"],
    "MMR": ["Burma", "Myanmar"],
    "NLD": ["Holland", "Netherlands"],
    "ARE": ["UAE", "Emirates"],
    "SAU": ["KSA", "Saudi"],
    "BRN": ["Brunei"],
    "KGZ": ["Kyrgyzstan"],
    "SVK": ["Slovakia"],
    "GMB": ["Gambia", "The Gambia"],
    "BHS": ["Bahamas", "The Bahamas"],
    "VAT": ["Vatican", "Holy See"],
    "XKX": ["Kosovo"],
}

# Short display names where pycountry's are long or formal.
DISPLAY_NAMES = {
    "BOL": "Bolivia",
    "VEN": "Venezuela",
    "IRN": "Iran",
    "KOR": "South Korea",
    "PRK": "North Korea",
    "RUS": "Russia",
    "SYR": "Syria",
    "TZA": "Tanzania",
    "VNM": "Vietnam",
    "LAO": "Laos",
    "MDA": "Moldova",
    "FSM": "Micronesia",
    "COD": "DR Congo",
    "COG": "Congo",
    "PSE": "Palestine",
    "TWN": "Taiwan",
    "GBR": "United Kingdom",
    "USA": "United States",
    "CZE": "Czechia",
    "TUR": "Türkiye",
    "BRN": "Brunei",
    "MKD": "North Macedonia",
    "CIV": "Côte d'Ivoire",
    "XKX": "Kosovo",
    "VAT": "Holy See",
}

CONTINENTAL_ROOT = "World (total) by continental regions"
SDG_ROOT = "World (total) by SDG regions"
GROUP_ROOTS = {
    "Least Developed Countries (LDC)": "LDC",
    "Land Locked Developing Countries (LLDC)": "LLDC",
    "Small Island Developing States (SIDS)": "SIDS",
}


def norm(s: str) -> str:
    """Lower-case, strip accents and punctuation: used for alias matching."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def iso3_from_m49(code: int) -> str | None:
    c = pycountry.countries.get(numeric=f"{int(code):03d}")
    return c.alpha_3 if c else None


def un_code(m49: int) -> str:
    """Geo code for a UN area: ISO3 when it is a country, else "UN-<m49>"."""
    iso = iso3_from_m49(m49)
    return iso if iso else f"UN-{int(m49):03d}"


def _country_record(iso3: str) -> dict:
    c = pycountry.countries.get(alpha_3=iso3)
    if c is None:
        name = DISPLAY_NAMES.get(iso3, iso3)
        return {"code": iso3, "name": name, "type": "country", "m49": None, "iso2": None,
                "aliases": sorted(set(EXTRA_ALIASES.get(iso3, [])))}
    names = {c.name}
    for attr in ("common_name", "official_name"):
        v = getattr(c, attr, None)
        if v:
            names.add(v)
    names.update(EXTRA_ALIASES.get(iso3, []))
    display = DISPLAY_NAMES.get(iso3) or getattr(c, "common_name", None) or c.name
    names.discard(display)
    return {
        "code": iso3,
        "name": display,
        "type": "country",
        "m49": int(c.numeric),
        "iso2": c.alpha_2,
        "aliases": sorted(names),
    }


def _walk(node: dict, parents: list[dict]):
    yield node, parents
    for child in node.get("children") or []:
        yield from _walk(child, parents + [node])


def build_geo(un_tree: list[dict], wb_countries: list[dict] | None = None) -> dict[str, dict]:
    """Build the geo table.

    un_tree: JSON from https://unstats.un.org/SDGAPI/v1/sdg/GeoArea/Tree
    wb_countries: rows from https://api.worldbank.org/v2/country?format=json&per_page=400
    Returns {code: record}.
    """
    geo: dict[str, dict] = {}
    roots = {r["geoAreaName"]: r for r in un_tree}

    # 1. Continental hierarchy: World > continent > sub-region (> intermediate) > country.
    cont = roots.get(CONTINENTAL_ROOT)
    if cont is None:
        raise ValueError("UN GeoArea tree has no continental-regions root")
    geo["UN-001"] = {"code": "UN-001", "name": "World", "type": "region", "m49": 1,
                     "parent": None, "level": 0, "aliases": ["World", "Global", "Earth"],
                     "members": []}
    for node, parents in _walk(cont, []):
        if node is cont:
            continue
        m49 = int(node["geoAreaCode"])
        kind = node.get("type")
        parent_code = "UN-001"
        for p in reversed(parents):
            if p is not cont:
                parent_code = un_code(p["geoAreaCode"])
                break
        if kind == "Country":
            code = un_code(m49)
            rec = _country_record(code) if not code.startswith("UN-") else {
                "code": code, "name": node["geoAreaName"], "type": "country", "m49": m49,
                "iso2": None, "aliases": []}
            rec["parent"] = parent_code
            chain = [p for p in parents if p is not cont]
            rec["continent"] = un_code(chain[0]["geoAreaCode"]) if chain else None
            rec["subregion"] = parent_code
            if node["geoAreaName"] not in (rec["name"], *rec["aliases"]):
                rec["aliases"] = sorted(set(rec["aliases"]) | {node["geoAreaName"]})
            geo[code] = {**geo.get(code, {}), **rec}
        elif kind == "Region":
            code = f"UN-{m49:03d}"
            geo[code] = {"code": code, "name": node["geoAreaName"], "type": "region",
                         "m49": m49, "parent": parent_code, "level": len(parents),
                         "aliases": [], "members": []}

    # 2. SDG regions (a second regional grouping used in UN SDG reports).
    sdg = roots.get(SDG_ROOT)
    if sdg is not None:
        for node, parents in _walk(sdg, []):
            if node.get("type") != "Country":
                continue
            code = un_code(node["geoAreaCode"])
            if code in geo:
                chain = [p for p in parents if p is not sdg]
                if chain:
                    geo[code]["sdg_region"] = chain[0]["geoAreaName"]
        for node, parents in _walk(sdg, []):
            if node is sdg or node.get("type") != "Region":
                continue
            code = f"UN-{int(node['geoAreaCode']):03d}"
            geo.setdefault(code, {"code": code, "name": node["geoAreaName"], "type": "region",
                                  "m49": int(node["geoAreaCode"]), "parent": "UN-001",
                                  "level": len(parents), "aliases": [], "members": []})

    # 3. Cross-cutting UN groups (LDC, LLDC, SIDS).
    for root_name, short in GROUP_ROOTS.items():
        root = roots.get(root_name)
        if root is None:
            continue
        code = f"UN-{int(root['geoAreaCode']):03d}"
        members = sorted({un_code(n["geoAreaCode"]) for n, _ in _walk(root, [])
                          if n.get("type") == "Country"})
        geo[code] = {"code": code, "name": root_name, "type": "group",
                     "m49": int(root["geoAreaCode"]), "parent": None, "level": 0,
                     "aliases": [short], "members": members}
        for m in members:
            if m in geo:
                geo[m].setdefault("groups", []).append(short)

    # 4. Region membership (all descendant countries).
    countries = [r for r in geo.values() if r["type"] == "country"]
    for c in countries:
        p = c.get("parent")
        seen = set()
        while p and p in geo and p not in seen:
            seen.add(p)
            geo[p].setdefault("members", []).append(c["code"])
            p = geo[p].get("parent")
    for r in geo.values():
        if "members" in r:
            r["members"] = sorted(set(r["members"]))

    # 5. World Bank metadata: income group and WB region; WB aggregates.
    for row in wb_countries or []:
        iso = row.get("id")
        region = (row.get("region") or {}).get("value", "")
        if region == "Aggregates":
            code = f"WB-{iso}"
            geo[code] = {"code": code, "name": row.get("name", iso), "type": "aggregate",
                         "source": "wdi", "parent": None, "aliases": [], "members": []}
            continue
        if not iso:
            continue
        if iso not in geo:
            rec = _country_record(iso)
            rec["parent"] = None
            geo[iso] = rec
        geo[iso]["income"] = ((row.get("incomeLevel") or {}).get("value") or "").strip() or None
        geo[iso]["wb_region"] = region.strip() or None
        wb_name = row.get("name")
        if wb_name and wb_name != geo[iso]["name"]:
            geo[iso]["aliases"] = sorted(set(geo[iso].get("aliases", [])) | {wb_name})

    return geo


def register_aggregate(geo: dict[str, dict], code: str, name: str, source: str) -> str:
    if code not in geo:
        geo[code] = {"code": code, "name": name, "type": "aggregate", "source": source,
                     "parent": None, "aliases": [], "members": []}
    return code


def alias_index(geo: dict[str, dict]) -> dict[str, str]:
    """Map normalised names and aliases to geo codes (countries win over aggregates)."""
    idx: dict[str, str] = {}
    order = {"aggregate": 0, "group": 1, "region": 2, "country": 3}
    for rec in sorted(geo.values(), key=lambda r: order.get(r["type"], 0)):
        for n in [rec["name"], rec["code"], *rec.get("aliases", [])]:
            if n:
                idx[norm(n)] = rec["code"]
    return idx
