// Data access: catalog, geography and per-indicator series files, with caching.

const BASE = "data/";
const cache = new Map();

async function getJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(BASE + path).then((r) => {
    if (!r.ok) throw new Error(`Could not load ${path} (HTTP ${r.status})`);
    return r.json();
  });
  cache.set(path, p);
  p.catch(() => cache.delete(path));
  return p;
}

export const SDG_COLORS = {
  1: "#E5243B", 2: "#DDA63A", 3: "#4C9F38", 4: "#C5192D", 5: "#FF3A21", 6: "#26BDE2",
  7: "#FCC30B", 8: "#A21942", 9: "#FD6925", 10: "#DD1367", 11: "#FD9D24", 12: "#BF8B2E",
  13: "#3F7E44", 14: "#0A97D9", 15: "#56C02B", 16: "#00689D", 17: "#19486A",
};

export const SOURCE_NAMES = {
  unsdg: "UN SDG Global Database",
  wdi: "World Bank WDI",
  owid: "Our World in Data",
};

export function normalize(s) {
  return (s || "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9.% ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export class Store {
  async init() {
    const [catalog, geo, meta] = await Promise.all([
      getJSON("catalog.json"),
      getJSON("geo.json"),
      getJSON("meta.json").catch(() => null),
    ]);
    this.catalog = catalog;
    this.meta = meta;
    this.indicators = catalog.indicators;
    this.byId = new Map(this.indicators.map((i) => [i.id, i]));
    this.goals = catalog.goals;
    this.geo = geo;
    this.geoByCode = new Map(geo.map((g) => [g.code, g]));
    this.geoAlias = new Map();
    const rank = { aggregate: 0, group: 1, region: 2, country: 3 };
    [...geo].sort((a, b) => rank[a.type] - rank[b.type]).forEach((g) => {
      [g.name, g.code, ...(g.aliases || [])].forEach((n) => {
        if (n) this.geoAlias.set(normalize(n), g.code);
      });
    });
    // Children lists for the region browser (continental hierarchy).
    this.children = new Map();
    geo.forEach((g) => {
      if (g.parent) {
        if (!this.children.has(g.parent)) this.children.set(g.parent, []);
        this.children.get(g.parent).push(g.code);
      }
    });
    for (const list of this.children.values()) {
      list.sort((a, b) => this.geoName(a).localeCompare(this.geoName(b)));
    }
    return this;
  }

  geoName(code) {
    return this.geoByCode.get(code)?.name || code;
  }

  async series(id) {
    const ind = this.byId.get(id);
    if (!ind) throw new Error(`Unknown indicator ${id}`);
    const raw = await getJSON("series/" + ind.file);
    if (!raw._rows) {
      raw._rows = raw.data.map(([gi, year, value, di, fi]) => ({
        geo: raw.geos[gi],
        year,
        value,
        dims: raw.dims[di],
        flag: fi ? raw.flags[fi - 1] : "",
      }));
    }
    return raw;
  }

  indicatorsForGoal(goal) {
    return this.indicators.filter((i) => (i.g || []).includes(String(goal)));
  }
}

export function parseDims(s) {
  const out = {};
  (s || "").split(";").filter(Boolean).forEach((p) => {
    const i = p.indexOf("=");
    out[p.slice(0, i)] = p.slice(i + 1);
  });
  return out;
}

// Readable labels for UN SDG breakdown codes.
const DIM_LABELS = {
  FEMALE: "Female", MALE: "Male", BOTHSEX: "Both sexes", ALLAREA: "All areas", URBAN: "Urban",
  RURAL: "Rural", ALLAGE: "All ages",
};
export function dimLabel(v, key, labels) {
  const fromSource = labels?.[key]?.[v];
  if (fromSource) return fromSource;
  return DIM_LABELS[v] || v.replace(/_/g, " ").replace(/^(\d+)-(\d+)$/, "$1–$2");
}
export function dimsText(d, labels) {
  const o = parseDims(d);
  const parts = Object.entries(o).map(([k, v]) => `${k}: ${dimLabel(v, k, labels)}`);
  return parts.length ? parts.join(", ") : "Total";
}
