"""A fake HTTP session serving small, realistic API responses for offline tests."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

UN_TREE = [
    {"geoAreaCode": 1, "geoAreaName": "World (total) by continental regions", "type": "Region",
     "children": [
         {"geoAreaCode": 142, "geoAreaName": "Asia", "type": "Region", "children": [
             {"geoAreaCode": 34, "geoAreaName": "Southern Asia", "type": "Region", "children": [
                 {"geoAreaCode": 356, "geoAreaName": "India", "type": "Country", "children": None},
                 {"geoAreaCode": 50, "geoAreaName": "Bangladesh", "type": "Country", "children": None},
             ]},
             {"geoAreaCode": 30, "geoAreaName": "Eastern Asia", "type": "Region", "children": [
                 {"geoAreaCode": 156, "geoAreaName": "China", "type": "Country", "children": None},
             ]},
         ]},
         {"geoAreaCode": 19, "geoAreaName": "Americas", "type": "Region", "children": [
             {"geoAreaCode": 21, "geoAreaName": "Northern America", "type": "Region", "children": [
                 {"geoAreaCode": 840, "geoAreaName": "United States of America", "type": "Country",
                  "children": None},
             ]},
         ]},
     ]},
    {"geoAreaCode": 1, "geoAreaName": "World (total) by SDG regions", "type": "Region",
     "children": [
         {"geoAreaCode": 62, "geoAreaName": "Central and Southern Asia", "type": "Region",
          "children": [{"geoAreaCode": 356, "geoAreaName": "India", "type": "Country",
                        "children": None}]},
     ]},
    {"geoAreaCode": 199, "geoAreaName": "Least Developed Countries (LDC)", "type": "Region",
     "children": [{"geoAreaCode": 50, "geoAreaName": "Bangladesh", "type": "Country",
                   "children": None}]},
]

WB_COUNTRIES = [
    {"id": "IND", "iso2Code": "IN", "name": "India", "region": {"id": "SAS", "value": "South Asia"},
     "incomeLevel": {"id": "LMC", "value": "Lower middle income"}},
    {"id": "CHN", "iso2Code": "CN", "name": "China",
     "region": {"id": "EAS", "value": "East Asia & Pacific"},
     "incomeLevel": {"id": "UMC", "value": "Upper middle income"}},
    {"id": "USA", "iso2Code": "US", "name": "United States",
     "region": {"id": "NAC", "value": "North America"},
     "incomeLevel": {"id": "HIC", "value": "High income"}},
    {"id": "BGD", "iso2Code": "BD", "name": "Bangladesh", "region": {"id": "SAS", "value": "South Asia"},
     "incomeLevel": {"id": "LMC", "value": "Lower middle income"}},
    {"id": "WLD", "iso2Code": "1W", "name": "World", "region": {"id": "NA", "value": "Aggregates"},
     "incomeLevel": {"id": "NA", "value": "Aggregates"}},
]

SERIES_LIST = [
    {"goal": ["7"], "target": ["7.2"], "indicator": ["7.2.1"], "release": "2026.Q2.G.02",
     "code": "EG_FEC_RNEW",
     "description": "Renewable energy share in the total final energy consumption (%)"},
    {"goal": ["5"], "target": ["5.5"], "indicator": ["5.5.1"], "release": "2026.Q2.G.02",
     "code": "SG_GEN_PARL", "description": "Proportion of seats held by women in national parliaments (%)"},
]

GOAL_LIST = [
    {"code": "7", "title": "Ensure access to affordable, reliable, sustainable and modern energy for all",
     "targets": [{"code": "7.2", "description": "Increase the share of renewable energy",
                  "indicators": [{"code": "7.2.1",
                                  "description": "Renewable energy share in the total final energy consumption"}]}]},
    {"code": "5", "title": "Achieve gender equality", "targets": [
        {"code": "5.5", "description": "Women's participation",
         "indicators": [{"code": "5.5.1", "description": "Seats held by women"}]}]},
]


def _un_item(code, geo, name, year, value, dims=None, nature="E", units="PERCENT"):
    return {"goal": ["7"], "series": code, "geoAreaCode": str(geo), "geoAreaName": name,
            "timePeriodStart": float(year), "value": value, "source": "IEA (2025)",
            "attributes": {"Nature": nature, "Units": units},
            "dimensions": dims or {"Reporting Type": "G"}}


UN_DATA = {
    "EG_FEC_RNEW": [
        _un_item("EG_FEC_RNEW", 356, "India", 2020, "36.1"),
        _un_item("EG_FEC_RNEW", 356, "India", 2021, "35.2"),
        _un_item("EG_FEC_RNEW", 356, "India", 2021, "40.0", {"Reporting Type": "N"}),
        _un_item("EG_FEC_RNEW", 156, "China", 2021, "14.9"),
        _un_item("EG_FEC_RNEW", 840, "USA", 2021, "<0.1"),
        _un_item("EG_FEC_RNEW", 1, "World", 2021, "18.7"),
        _un_item("EG_FEC_RNEW", 34, "Southern Asia", 2021, "NaN"),
    ],
    "SG_GEN_PARL": [
        _un_item("SG_GEN_PARL", 356, "India", 2022, "14.9", {"Reporting Type": "G"}, nature="C"),
        _un_item("SG_GEN_PARL", 50, "Bangladesh", 2022, "20.9", {"Reporting Type": "G"}, nature="C"),
    ],
}

WB_TOPIC = [
    {"id": "EG.ELC.ACCS.ZS", "name": "Access to electricity (% of population)", "unit": "",
     "source": {"id": "2", "value": "World Development Indicators"},
     "sourceNote": "Access to electricity is the percentage of population with access to electricity.",
     "sourceOrganization": "World Bank, SE4ALL"},
    {"id": "EN.ATM.CO2E.PC", "name": "CO2 emissions (metric tons per capita)", "unit": "",
     "source": {"id": "2", "value": "World Development Indicators"},
     "sourceNote": "Carbon dioxide emissions per person.", "sourceOrganization": "Climate Watch"},
    {"id": "X.OTHER", "name": "Not WDI", "unit": "", "source": {"id": "11", "value": "Other"}},
]


def _wb_rows(code):
    out = []
    for iso, name in (("IND", "India"), ("CHN", "China"), ("WLD", "World"), ("SAS", "South Asia")):
        for y, base in ((2020, 90.0), (2021, 95.5)):
            out.append({"indicator": {"id": code, "value": code},
                        "country": {"id": iso[:2], "value": name}, "countryiso3code": iso,
                        "date": str(y), "value": base if iso != "CHN" else None,
                        "unit": "", "obs_status": "", "decimal": 1})
    return out


OWID_CO2 = b"""country,year,iso_code,population,gdp,co2,co2_per_capita
India,2021,IND,1400000000,9.0e12,2700.5,1.93
India,2022,IND,1410000000,9.5e12,2830.1,2.0
China,2022,CHN,1410000000,2.3e13,11400.0,8.1
World,2022,OWID_WRL,7950000000,,37500.0,4.7
Asia (GCP),2022,,,,20000.0,
"""
OWID_CO2_CB = b"""column,title,description,unit,source
country,Country,Geographic location.,,OWID
year,Year,Year.,,OWID
iso_code,ISO code,ISO.,,ISO
population,Population,People.,people,UN WPP
gdp,GDP,Output.,international-$,Maddison
co2,Annual CO2 emissions,Annual total emissions of carbon dioxide (CO2).,million tonnes,Global Carbon Project
co2_per_capita,Annual CO2 emissions per capita,Per person.,tonnes per person,Global Carbon Project
"""
OWID_EN = b"""country,year,iso_code,population,renewables_share_energy,greenhouse_gas_emissions
India,2022,IND,1410000000,9.8,3200.0
China,2022,CHN,1410000000,15.2,13000.0
"""
OWID_EN_CB = b"""column,title,description,unit,source
country,Country,Geographic location.,,OWID
year,Year,Year.,,OWID
iso_code,ISO code,ISO.,,ISO
population,Population,People.,people,UN WPP
renewables_share_energy,Renewables share of primary energy,Share.,%,Energy Institute
greenhouse_gas_emissions,Greenhouse gas emissions from electricity,Emissions.,million tonnes CO2e,Ember
"""


class FakeResponse:
    def __init__(self, payload=None, content: bytes | None = None, status=200, url=""):
        self._payload = payload
        self.content = content if content is not None else json.dumps(payload).encode()
        self.status_code = status
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.content)


class FakeSession:
    """Routes URLs to fixtures. `overrides` lets a test change one response."""

    def __init__(self, overrides=None, fail=()):
        self.overrides = overrides or {}
        self.fail = set(fail)
        self.calls: list[str] = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        params = params or {}
        self.calls.append(url + " " + json.dumps(params, sort_keys=True))
        for key in self.fail:
            if key in url or key == params.get("seriesCode"):
                return FakeResponse({"error": "boom"}, status=500, url=url)
        for key, val in self.overrides.items():
            if key in url:
                return val if isinstance(val, FakeResponse) else FakeResponse(val, url=url)
        p = urlparse(url).path
        if p.endswith("/GeoArea/Tree"):
            return FakeResponse(UN_TREE)
        if p.endswith("/Series/List"):
            return FakeResponse(SERIES_LIST)
        if p.endswith("/Goal/List"):
            return FakeResponse(GOAL_LIST)
        if p.endswith("/Series/Data"):
            code = params["seriesCode"]
            items = UN_DATA[code]
            size = int(params.get("pageSize", 10000))
            page = int(params.get("page", 1))
            chunk = items[(page - 1) * size: page * size]
            pages = max(1, -(-len(items) // size))
            return FakeResponse({"size": size, "totalElements": len(items), "totalPages": pages,
                                 "pageNumber": page, "data": chunk,
                                 "attributes": [{"id": "Units", "codes": [
                                     {"code": "PERCENT", "description": "Percentage"}]}]})
        if p.endswith("/v2/country"):
            return FakeResponse([{"page": 1, "pages": 1}, WB_COUNTRIES])
        if "/v2/sources/2" in p:
            return FakeResponse([{"page": 1}, [{"id": "2", "lastupdated": "2026-07-01"}]])
        if "/v2/topic/" in p:
            tid = p.split("/topic/")[1].split("/")[0]
            return FakeResponse([{"page": 1}, WB_TOPIC if tid in ("5", "19") else []])
        if "/indicator/" in p and "/v2/country/all/" in p:
            code = p.rsplit("/", 1)[1]
            return FakeResponse([{"page": 1, "pages": 1, "lastupdated": "2026-07-01"},
                                 _wb_rows(code)])
        q = parse_qs(urlparse(url).query)
        del q
        if url.endswith("owid-co2-data.csv"):
            return FakeResponse(content=OWID_CO2)
        if url.endswith("owid-co2-codebook.csv"):
            return FakeResponse(content=OWID_CO2_CB)
        if url.endswith("owid-energy-data.csv"):
            return FakeResponse(content=OWID_EN)
        if url.endswith("owid-energy-codebook.csv"):
            return FakeResponse(content=OWID_EN_CB)
        return FakeResponse({"error": "not found"}, status=404, url=url)
