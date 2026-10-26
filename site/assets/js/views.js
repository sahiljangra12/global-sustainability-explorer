// Chart, map and table views for the current selection.

import { Plot, feature } from "../vendor/vis.bundle.js";
import { fmt, esc, isPercent, mixHex } from "./format.js";

// Line colours for places on the black background: bright, distinct, and not the SDG
// palette (SDG colours already mean "goal" in this interface).
export const SERIES_COLORS = ["#60A5FA", "#FB923C", "#C084FC", "#2DD4BF", "#F472B6", "#FACC15",
  "#4ADE80", "#F87171", "#22D3EE", "#A5B4FC", "#FDBA74", "#CBD5E1"];

const FONT = "D-DIN, Arial Narrow, sans-serif";

let world = null;
export async function worldMap() {
  if (!world) {
    world = fetch(new URL("../vendor/countries-110m.json", import.meta.url))
      .then((r) => r.json())
      .then((t) => feature(t, t.objects.countries));
  }
  return world;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function axisLabel(unit) {
  if (!unit) return null;
  return isPercent(unit) ? "%" : unit;
}

export function chart(el, { rows, geos, geoName, unit, y0, y1 }) {
  const width = Math.max(320, el.clientWidth - 32);
  const sel = rows.filter((r) => geos.includes(r.geo) && r.year >= y0 && r.year <= y1);
  el.innerHTML = "";
  if (!sel.length) {
    el.innerHTML = `<p class="msg">No values for these places and years. Try a wider year range or add places with data (places without data are shown with a dashed outline).</p>`;
    return;
  }
  const order = geos.filter((g) => sel.some((r) => r.geo === g));
  const color = { domain: order.map(geoName), range: SERIES_COLORS };
  const data = sel.map((r) => ({ ...r, place: geoName(r.geo) }));
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = order.map((g, i) =>
    `<span><i style="background:${SERIES_COLORS[i % SERIES_COLORS.length]}"></i>${esc(geoName(g))}</span>`).join("");
  el.append(legend);
  const years = new Set(data.map((d) => d.year));
  const ink = cssVar("--ink");
  const muted = cssVar("--muted");
  let plot;
  if (years.size === 1) {
    plot = Plot.plot({
      width, height: Math.max(160, order.length * 34 + 60), marginLeft: 160, marginRight: 72,
      style: { fontFamily: FONT, fontSize: "13px", color: ink, background: "transparent" },
      x: { grid: true, label: axisLabel(unit), tickFormat: (d) => fmt(d) },
      y: { label: null },
      color,
      marks: [
        Plot.barX(data, { y: "place", x: "value", fill: "place", sort: { y: "x", reverse: true },
          tip: { format: { value: (d) => fmt(d, unit), fill: false } } }),
        Plot.ruleX([0], { stroke: muted }),
        Plot.text(data, { y: "place", x: "value", text: (d) => fmt(d.value, unit), dx: 6, textAnchor: "start", fill: ink }),
      ],
    });
  } else {
    plot = Plot.plot({
      width, height: Math.min(520, Math.max(320, width * 0.48)), marginLeft: 64, marginRight: 24,
      style: { fontFamily: FONT, fontSize: "13px", color: ink, background: "transparent" },
      x: { label: null, tickFormat: (d) => String(d), ticks: Math.max(2, Math.min(12, years.size, Math.floor(width / 70))) },
      y: { grid: true, label: axisLabel(unit), tickFormat: (d) => fmt(d), zero: data.every((d) => d.value >= 0) },
      color,
      marks: [
        Plot.lineY(data, { x: "year", y: "value", stroke: "place", strokeWidth: 2.5, curve: "monotone-x" }),
        Plot.dot(data, { x: "year", y: "value", fill: "place", r: data.length > 400 ? 0 : 2.5 }),
        Plot.tip(data, Plot.pointer({ x: "year", y: "value", stroke: "place",
          title: (d) => `${d.place}\n${d.year}: ${fmt(d.value, unit)}${d.flag ? ` (${d.flag})` : ""}` })),
      ],
    });
  }
  el.append(plot);
}

export function bestMapYear(rows, countrySet, y0, y1) {
  const counts = new Map();
  rows.forEach((r) => {
    if (countrySet.has(r.geo) && r.year >= y0 && r.year <= y1) {
      counts.set(r.year, (counts.get(r.year) || 0) + 1);
    }
  });
  if (!counts.size) return null;
  const best = Math.max(...counts.values());
  return [...counts.entries()].filter(([, c]) => c >= best * 0.7).map(([y]) => y).sort((a, b) => b - a)[0];
}

export async function map(el, { rows, year, geoByCode, unit, highlight }) {
  const width = Math.max(320, el.clientWidth - 32);
  const features = await worldMap();
  const byM49 = new Map();
  rows.forEach((r) => {
    if (r.year !== year) return;
    const g = geoByCode.get(r.geo);
    if (g && g.type === "country" && g.m49) byM49.set(String(g.m49).padStart(3, "0"), { v: r.value, name: g.name, code: g.code });
  });
  el.innerHTML = "";
  if (!byM49.size) {
    el.innerHTML = `<p class="msg">No country values for ${year}.</p>`;
    return;
  }
  const goal = document.documentElement.dataset.goalHex || "#0A97D9";
  const high = mixHex(goal, "#F0F0FA", 0.45);
  const low = mixHex(goal, "#0B0B0E", 0.4);
  const ink = cssVar("--ink");
  const rule = cssVar("--rule");
  const empty = "#1A1B22";
  const vals = [...byM49.values()].map((d) => d.v).sort((a, b) => a - b);
  const q = (p) => vals[Math.min(vals.length - 1, Math.floor(p * (vals.length - 1)))];
  const plot = Plot.plot({
    width, height: width * 0.5,
    projection: "equal-earth",
    style: { fontFamily: FONT, fontSize: "12px", color: ink, background: "transparent" },
    color: { type: "linear", domain: [q(0.02), q(0.98)], range: [low, high], clamp: true,
      legend: true, label: `${year}${unit ? ` · ${axisLabel(unit)}` : ""}`, tickFormat: (d) => fmt(d) },
    marks: [
      Plot.sphere({ stroke: rule }),
      Plot.geo(features.features, { fill: empty, stroke: "#000", strokeWidth: 0.5 }),
      Plot.geo(features.features.filter((f) => byM49.has(f.id)), {
        fill: (f) => byM49.get(f.id)?.v ?? null,
        stroke: (f) => (highlight?.has(byM49.get(f.id)?.code) ? ink : "#000"),
        strokeWidth: (f) => (highlight?.has(byM49.get(f.id)?.code) ? 1.5 : 0.5),
      }),
      Plot.tip(features.features, Plot.pointer(Plot.geoCentroid({
        title: (f) => {
          const d = byM49.get(f.id);
          return d ? `${d.name}\n${year}: ${fmt(d.v, unit)}` : `${f.properties.name}\nno data for ${year}`;
        },
      }))),
    ],
  });
  el.append(plot);
}

export function table(el, { rows, geos, geoName, y0, y1, unit }) {
  const sel = rows.filter((r) => geos.includes(r.geo) && r.year >= y0 && r.year <= y1);
  el.innerHTML = "";
  if (!sel.length) {
    el.innerHTML = `<p class="msg">No values for these places and years.</p>`;
    return;
  }
  const years = [...new Set(sel.map((r) => r.year))].sort((a, b) => b - a);
  const byGeo = new Map();
  sel.forEach((r) => {
    if (!byGeo.has(r.geo)) byGeo.set(r.geo, new Map());
    byGeo.get(r.geo).set(r.year, r);
  });
  const order = geos.filter((g) => byGeo.has(g));
  const t = document.createElement("table");
  t.className = "data";
  t.innerHTML = `<thead><tr><th scope="col">Place</th>${years.map((y) => `<th scope="col">${y}</th>`).join("")}</tr></thead>` +
    `<tbody>${order.map((g) => `<tr><th scope="row">${esc(geoName(g))}</th>${years.map((y) => {
      const r = byGeo.get(g).get(y);
      return r ? `<td title="${r.flag ? esc(r.flag) : ""}">${fmt(r.value, unit)}${r.flag ? "*" : ""}</td>` : "<td></td>";
    }).join("")}</tr>`).join("")}</tbody>`;
  el.append(t);
  const note = document.createElement("p");
  note.className = "table-note";
  note.textContent = `${sel.length} values. * marks a value the source flags (for example an estimate). Unit: ${unit || "as defined by the source"}.`;
  el.append(note);
}
