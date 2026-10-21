# Global Sustainability Explorer

**Live site: https://sahiljangra12.github.io/global-sustainability-explorer/**

A free tool for researchers to explore, compare and cite global sustainability data for all 17 Sustainable Development Goals in one place, with a live sustainability news feed.

- **One place for the main open datasets:** the UN SDG Global Database (all 17 goals, with breakdowns by sex, age and location), World Bank WDI (energy, environment, climate, poverty, agriculture) and Our World in Data (CO2, greenhouse gases, energy).
- **Drill down:** Goal → target → indicator → breakdown. Pick places from World → continent → sub-region → country, or groups such as LDCs and SIDS, and set any year range.
- **Chart, map and table views:** download CSV with source columns, copy a shareable link, or copy a citation.
- **Cite everything:** every indicator shows its source, producing agency, series code, release and licence. Flagged values (estimates, modelled data) are marked.
- **Always current:** a weekly job checks every source and re-downloads only series whose release changed. It validates the result and keeps the last good copy if anything fails. A *What's new* page lists every change.
- **Live news:** headlines from UN and specialist newsrooms are refreshed every 3 hours, tagged by SDG, country and conference (CBD COP17, COP31, the UN Water Conference). News about a conference in progress is pinned to the top.

## How it works

```
 UN SDG API ─┐
 World Bank ─┼─▶ pipeline (GitHub Actions, weekly) ─▶ validate + diff ─▶ data-latest release (Parquet)
 OWID ───────┘                                                    │                  │
 RSS feeds ──▶ news (every 3 h) ──────────────────────────────────┤
                                                                  ▼
                                                     static site on GitHub Pages
```

| Folder | What it is |
| --- | --- |
| `pipeline/` | Python package `gse`: source connectors, geography, validation, change detection, export, news. |
| `site/` | The website: plain HTML, CSS and JavaScript. Observable Plot is bundled in `site/assets/vendor`, so there is no CDN dependency. |
| `config/` | `news_sources.yaml` (RSS feeds) and `events.yaml` (UN conference dates). Edit these to add feeds or events. |
| `.github/workflows/` | `update.yml`: the scheduled update and deploy. `tests.yml`: the tests. |

### Data model

All sources are reshaped into one long table, `observations`:

| Column | Meaning |
| --- | --- |
| `indicator_id` | Source plus the source's own code |
| `geo` | ISO3 code, or a region/aggregate code |
| `year` | Year of the value |
| `value` | The value |
| `dims` | Breakdown, if any |
| `flag` | The source's flag, e.g. an estimate |

There is also an `indicators` catalogue (name, unit, description, SDG goal/target/indicator, source, licence, release) and a `geo` table.

Codes are kept per source. UN regions use `UN-<M49>`, and aggregates published by only one source keep a prefix (`WB-SAS`, `OWID-World`), so definitions are never mixed. Each update's data is archived as a dated GitHub release, so past answers can be reproduced.

### Update schedule

| When | What |
| --- | --- |
| Sunday 03:17 UTC | Check all data sources: the UN SDG release labels per series, the WDI last-updated date, and the OWID file hashes. |
| Every 3 hours | Refresh news and redeploy the site. |
| On demand | *Actions → Update data, news and site → Run workflow* (choose sources, or tick *quick* for a test run). |

## Run it locally

```bash
pip install -r pipeline/requirements.txt
cd pipeline
python -m gse.build --prev ../prev --out ../build      # full build (first run downloads everything)
python -m gse.news --out ../build/site-data
cd .. && cp -r site _site && cp -r build/site-data _site/data
python -m http.server -d _site 8000                    # open http://localhost:8000
```

To work faster, limit the build with `GSE_UNSDG_SERIES=EG_FEC_RNEW,EG_ACS_ELEC GSE_WDI_INDICATORS=EG.ELC.ACCS.ZS`, or download the latest bundle from the `data-latest` release into `prev/`.

Run the tests with `cd pipeline && pytest`.

## Limitations

- Coverage is three sources for now. Paywalled data (IEA, some IRENA tables) is not included. Climate Watch and FAOSTAT are the next sources to add.
- World Bank and OWID indicators are linked to SDGs thematically by this project. Only UN SDG series carry official goal, target and indicator codes.
- News items show the publisher's headline and short teaser and link to the original. Feeds that are down are listed as unavailable.

## Licence

Code: MIT (see `LICENSE`). Data remains under each source's licence: UN data under the UN terms of use, and World Bank and Our World in Data under CC BY 4.0. Cite the original source shown on each indicator.

Built by Sahil (MSE Data Science, Johns Hopkins University).
