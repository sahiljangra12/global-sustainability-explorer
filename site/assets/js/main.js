import { Store, SDG_COLORS, SOURCE_NAMES, normalize, dimsText, parseDims, dimLabel } from "./data.js";
import { askLocal, askApi, citation } from "./ask.js";
import { chart, map, table, bestMapYear, SERIES_COLORS } from "./views.js";
import { fmt, esc, csvEscape, download, dateText, luminance } from "./format.js";

const CONFIG = window.GSE_CONFIG || {};
const SHORT_GOALS = {
  1: "No poverty", 2: "Zero hunger", 3: "Good health", 4: "Education", 5: "Gender equality",
  6: "Clean water", 7: "Clean energy", 8: "Decent work", 9: "Industry", 10: "Inequalities",
  11: "Cities", 12: "Consumption", 13: "Climate action", 14: "Life below water",
  15: "Life on land", 16: "Peace & justice", 17: "Partnerships",
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const state = {
  tab: "explore",
  goal: null, // goal filter for the indicator browser
  ind: null,
  geos: [],
  y0: null,
  y1: null,
  view: "chart",
  dims: "",
  mapYear: null,
};
let store;
let series = null;
let playTimer = null;

// ---------- boot ----------
init().catch((e) => {
  console.error(e);
  $("#ind-title").textContent = "The data could not be loaded.";
  $("#ind-meta").textContent = `${e.message}. If you are running the site locally, build the data first (see README).`;
});

async function init() {
  store = await new Store().init();
  $("#ind-search").placeholder = `Search ${store.indicators.length.toLocaleString("en")} indicators`;
  if (store.meta?.generated) $("#build-time").textContent = `Data last built ${dateText(store.meta.generated)}.`;
  renderGoalStrip();
  bindControls();
  readHash();
  window.addEventListener("hashchange", () => {
    if (!suppressHash) readHash();
  });
  loadNews(true);
}

// ---------- URL state ----------
let suppressHash = false;
function writeHash() {
  if (state.tab !== "explore") return;
  const p = new URLSearchParams();
  if (state.ind) p.set("i", state.ind);
  if (state.geos.length) p.set("g", state.geos.join(","));
  if (state.y0 && state.y1) p.set("y", `${state.y0}-${state.y1}`);
  if (state.view !== "chart") p.set("v", state.view);
  if (state.dims) p.set("d", state.dims);
  if (state.goal) p.set("goal", state.goal);
  suppressHash = true;
  history.replaceState(null, "", `#explore?${p.toString()}`);
  setTimeout(() => (suppressHash = false), 0);
}

function readHash() {
  const h = location.hash.replace(/^#/, "");
  const [tab, query] = h.split("?");
  showTab(["news", "updates", "about"].includes(tab) ? tab : "explore");
  if (state.tab !== "explore") return;
  if (!query && state.ind && series) {
    render(); // coming back from another tab: keep the current selection
    return;
  }
  const p = new URLSearchParams(query || "");
  const ind = p.get("i");
  const goal = p.get("goal");
  state.goal = goal && SDG_COLORS[goal] ? goal : state.goal;
  if (p.get("g")) state.geos = p.get("g").split(",").filter((g) => store.geoByCode.has(g) || g);
  if (p.get("y")) {
    const [a, b] = p.get("y").split("-").map(Number);
    if (a && b) [state.y0, state.y1] = [a, b];
  }
  state.view = ["chart", "map", "table"].includes(p.get("v")) ? p.get("v") : state.view;
  if (p.has("d")) state.dims = p.get("d");
  const target = ind && store.byId.has(ind) ? ind : state.ind || defaultIndicator();
  selectIndicator(target, { keepGeos: !!p.get("g"), keepYears: !!p.get("y"), keepDims: p.has("d") });
  renderGoalStrip();
  renderBrowser();
}

function defaultIndicator() {
  const prefs = ["unsdg:EG_FEC_RNEW", "owid:co2_per_capita", "wdi:EG.ELC.ACCS.ZS"];
  return prefs.find((p) => store.byId.has(p)) || store.indicators[0]?.id;
}

// ---------- tabs ----------
function showTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((s) => (s.hidden = s.id !== `tab-${tab}`));
  $$(".tabs a").forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (tab === "news") loadNews();
  if (tab === "updates") loadUpdates();
  if (tab === "about") renderAbout();
}

// ---------- goal strip ----------
function renderGoalStrip() {
  const el = $("#goal-strip");
  const counts = new Map();
  store.indicators.forEach((i) => (i.g || []).forEach((g) => counts.set(g, (counts.get(g) || 0) + 1)));
  el.innerHTML =
    `<button class="goal all" role="tab" aria-selected="${!state.goal}" data-goal="">` +
    `<span class="num">All</span><span class="gname">goals</span></button>` +
    store.goals.map((g) => `<button class="goal" role="tab" style="--c:${SDG_COLORS[g.code]};color:${luminance(SDG_COLORS[g.code]) > 0.38 ? "#132A2E" : "#fff"}" aria-selected="${state.goal === g.code}"
        data-goal="${g.code}" title="Goal ${g.code}: ${esc(g.name)} (${counts.get(g.code) || 0} indicators)">
        <span class="num">${g.code}</span><span class="gname">${esc(SHORT_GOALS[g.code] || g.name)}</span></button>`).join("");
  $$(".goal", el).forEach((b) => b.addEventListener("click", () => {
    state.goal = b.dataset.goal || null;
    $("#ind-search").value = "";
    renderGoalStrip();
    renderBrowser();
    if (state.goal) {
      setGoalTint(state.goal);
      const cur = store.byId.get(state.ind);
      if (!cur || !(cur.g || []).includes(state.goal)) {
        const first = pickGoalDefault(state.goal);
        if (first) selectIndicator(first.id, { keepGeos: true, keepYears: false });
      }
    }
    writeHash();
  }));
}

function pickGoalDefault(goal) {
  const list = store.indicatorsForGoal(goal).filter((i) => i.has_total);
  list.sort((a, b) => (b.s === "unsdg") - (a.s === "unsdg") || b.ng - a.ng || b.y1 - a.y1);
  return list[0];
}

function setGoalTint(goal) {
  const hex = SDG_COLORS[goal] || "#19486A";
  document.documentElement.style.setProperty("--goal", hex);
  document.documentElement.dataset.goalHex = hex;
}

// ---------- indicator browser ----------
function renderBrowser() {
  const el = $("#ind-browser");
  const q = normalize($("#ind-search").value);
  if (q) {
    const words = q.split(" ");
    const hits = store.indicators
      .map((i) => {
        const hay = normalize(`${i.n} ${i.c} ${(i.tp || []).join(" ")} ${(i.i || []).join(" ")}`);
        const all = words.every((w) => hay.includes(w));
        return all ? { i, s: (normalize(i.n).startsWith(words[0]) ? 2 : 0) + (i.s === "unsdg" ? 0.5 : 0) + (i.ng || 0) / 300 } : null;
      })
      .filter(Boolean)
      .sort((a, b) => b.s - a.s)
      .slice(0, 80)
      .map((x) => x.i);
    el.innerHTML = hits.length ? `<ul class="ind-list">${hits.map(indButton).join("")}</ul>` : `<p class="empty">No indicator matches “${esc($("#ind-search").value)}”.</p>`;
  } else if (state.goal) {
    const goal = store.goals.find((g) => g.code === state.goal);
    const inds = store.indicatorsForGoal(state.goal);
    const official = inds.filter((i) => i.m === "official");
    const related = inds.filter((i) => i.m !== "official");
    let html = "";
    (goal.targets || []).forEach((t) => {
      const list = official.filter((i) => (i.t || []).includes(t.code));
      if (!list.length) return;
      const open = list.some((i) => i.id === state.ind);
      html += `<details ${open ? "open" : ""}><summary>Target ${esc(t.code)} <small>(${list.length})</small><br><span class="target-title">${esc(trim(t.title, 110))}</span></summary><ul class="ind-list">${list.map(indButton).join("")}</ul></details>`;
    });
    const untargeted = official.filter((i) => !(goal.targets || []).some((t) => (i.t || []).includes(t.code)));
    if (untargeted.length) html += `<details><summary>Other official series <small>(${untargeted.length})</small></summary><ul class="ind-list">${untargeted.map(indButton).join("")}</ul></details>`;
    ["wdi", "owid"].forEach((s) => {
      const list = related.filter((i) => i.s === s);
      if (list.length) {
        const open = list.some((i) => i.id === state.ind);
        html += `<details ${open ? "open" : ""}><summary>Related: ${SOURCE_NAMES[s]} <small>(${list.length})</small></summary><ul class="ind-list">${list.map(indButton).join("")}</ul></details>`;
      }
    });
    el.innerHTML = html || `<p class="empty">No indicators for this goal yet.</p>`;
  } else {
    el.innerHTML = store.goals.map((g) => {
      const n = store.indicatorsForGoal(g.code).length;
      return `<details data-goal="${g.code}"><summary>${g.code}. ${esc(g.name)} <small>(${n})</small></summary></details>`;
    }).join("") + (() => {
      const none = store.indicators.filter((i) => !(i.g || []).length);
      return none.length ? `<details data-goal="none"><summary>Other context indicators <small>(${none.length})</small></summary></details>` : "";
    })();
    $$("details", el).forEach((d) => d.addEventListener("toggle", () => {
      if (!d.open || d.dataset.filled) return;
      const list = d.dataset.goal === "none"
        ? store.indicators.filter((i) => !(i.g || []).length)
        : store.indicatorsForGoal(d.dataset.goal);
      d.insertAdjacentHTML("beforeend", `<ul class="ind-list">${list.map(indButton).join("")}</ul>`);
      d.dataset.filled = "1";
      bindIndButtons(d);
    }));
  }
  bindIndButtons(el);
  const cur = $(`.ind-list button[aria-current="true"]`, el);
  if (cur) cur.scrollIntoView({ block: "nearest" });
}

function trim(s, n) {
  s = s || "";
  return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s;
}

function indButton(i) {
  const years = i.y0 === i.y1 ? i.y1 : `${i.y0}–${i.y1}`;
  return `<li><button type="button" data-id="${esc(i.id)}" aria-current="${i.id === state.ind}">${esc(i.n)}<span class="src">${SOURCE_NAMES[i.s]} · ${years} · ${i.ng} places</span></button></li>`;
}

function bindIndButtons(root) {
  $$(".ind-list button", root).forEach((b) => {
    if (b.dataset.bound) return;
    b.dataset.bound = "1";
    b.addEventListener("click", () => selectIndicator(b.dataset.id, { keepGeos: true }));
  });
}

// ---------- selection ----------
async function selectIndicator(id, { keepGeos = true, keepYears = false, keepDims = false } = {}) {
  if (!id) return;
  const ind = store.byId.get(id);
  state.ind = id;
  setGoalTint(state.goal || (ind.g || [])[0] || "17");
  $("#ind-title").textContent = ind.n;
  $("#view").innerHTML = `<p class="msg">Loading…</p>`;
  try {
    series = await store.series(id);
  } catch (e) {
    $("#view").innerHTML = `<p class="msg">${esc(e.message)}</p>`;
    return;
  }
  if (state.ind !== id) return; // a newer selection won
  if (!keepDims || !series.dims.includes(state.dims)) state.dims = series.dims.includes("") ? "" : series.dims[0];
  const avail = new Set(series._rows.filter((r) => r.dims === state.dims).map((r) => r.geo));
  if (!keepGeos || !state.geos.length || !state.geos.some((g) => avail.has(g) || mapWorld(g, avail) !== g)) {
    state.geos = defaultGeos(avail);
  } else {
    state.geos = [...new Set(state.geos.map((g) => mapWorld(g, avail)))];
  }
  if (!keepYears || !state.y0) {
    state.y1 = ind.y1;
    state.y0 = Math.max(ind.y0, Math.min(2000, ind.y1 - 10));
  }
  state.mapYear = null;
  render();
  $$(".ind-list button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.id === id)));
}

// World codes differ by source; swap to the matching one when changing indicator.
function mapWorld(code, avail) {
  const worlds = ["UN-001", "WB-WLD", "OWID-World"];
  if (worlds.includes(code) && !avail.has(code)) return worlds.find((w) => avail.has(w)) || code;
  return code;
}

function defaultGeos(avail) {
  const world = ["UN-001", "WB-WLD", "OWID-World"].find((w) => avail.has(w));
  const picks = ["IND", "CHN", "USA", "BRA", "NGA", "DEU"].filter((c) => avail.has(c));
  const out = [world, ...picks].filter(Boolean).slice(0, 6);
  if (out.length) return out;
  return [...avail].filter((g) => store.geoByCode.get(g)?.type === "country").slice(0, 5);
}

// ---------- render explore ----------
function currentRows() {
  return series ? series._rows.filter((r) => r.dims === state.dims) : [];
}

function render() {
  if (!series) return;
  const ind = store.byId.get(state.ind);
  const goal = state.goal && (ind.g || []).includes(state.goal) ? state.goal : (ind.g || [])[0];
  const goalInfo = store.goals.find((g) => g.code === goal);
  $("#result-goal").innerHTML = goalInfo
    ? `<span class="sq"></span>Goal ${goal}: ${esc(goalInfo.name)}${ind.i?.length ? ` · indicator ${esc(ind.i.join(", "))}` : ""}${ind.m === "approximate" ? " · related indicator" : ""}`
    : "Context indicator";
  $("#ind-title").textContent = ind.n;
  $("#ind-meta").textContent = [
    series.unit ? `Unit: ${series.unit}` : null,
    SOURCE_NAMES[ind.s],
    `${ind.y0}–${ind.y1}`,
    `${ind.ng} places`,
    series.updated ? `updated here ${dateText(series.updated)}` : null,
  ].filter(Boolean).join("  ·  ");
  $("#y0").value = state.y0;
  $("#y1").value = state.y1;
  $("#y0").min = $("#y1").min = ind.y0;
  $("#y0").max = $("#y1").max = ind.y1;
  renderChips();
  renderDims();
  renderView();
  renderCard();
  writeHash();
}

function renderChips() {
  const avail = new Set(currentRows().map((r) => r.geo));
  $("#geo-chips").innerHTML = state.geos.map((g, i) => {
    const has = avail.has(g);
    return `<span class="chip ${has ? "" : "nodata"}" title="${has ? "" : "No data for this place in this indicator"}">` +
      `<i style="background:${has ? SERIES_COLORS[state.geos.filter((x) => avail.has(x)).indexOf(g) % SERIES_COLORS.length] : "transparent"}"></i>` +
      `${esc(store.geoName(g))}<button type="button" data-rm="${esc(g)}" aria-label="Remove ${esc(store.geoName(g))}">×</button></span>`;
  }).join("") + (state.geos.length > 1 ? `<button type="button" class="example" id="clear-geos">Clear</button>` : "");
  $$("[data-rm]").forEach((b) => b.addEventListener("click", () => {
    state.geos = state.geos.filter((g) => g !== b.dataset.rm);
    render();
  }));
  $("#clear-geos")?.addEventListener("click", () => {
    state.geos = [];
    render();
  });
}

function renderDims() {
  const block = $("#dims-block");
  if (!series || series.dims.length <= 1) {
    block.hidden = true;
    return;
  }
  block.hidden = false;
  const keys = {};
  series.dims.forEach((d) => Object.entries(parseDims(d)).forEach(([k, v]) => {
    (keys[k] ||= new Set()).add(v);
  }));
  const cur = parseDims(state.dims);
  $("#dims").innerHTML = Object.entries(keys).map(([k, vals]) => `<label>${esc(k)}
      <select data-dim="${esc(k)}">${series.dims.includes("") || !cur[k] ? `<option value="">Total / not broken down</option>` : ""}
      ${[...vals].sort().map((v) => `<option value="${esc(v)}" ${cur[k] === v ? "selected" : ""}>${esc(dimLabel(v))}</option>`).join("")}
      </select></label>`).join("");
  $$("#dims select").forEach((s) => s.addEventListener("change", () => {
    const want = {};
    $$("#dims select").forEach((x) => { if (x.value) want[x.dataset.dim] = x.value; });
    // choose the existing combination closest to what was picked
    const score = (d) => {
      const o = parseDims(d);
      let sc = 0;
      for (const [k, v] of Object.entries(want)) if (o[k] === v) sc += 2;
      sc -= Object.keys(o).filter((k) => !want[k]).length * 0.5;
      return sc;
    };
    state.dims = [...series.dims].sort((a, b) => score(b) - score(a))[0];
    render();
  }));
}

async function renderView() {
  const el = $("#view");
  const rows = currentRows();
  const ind = store.byId.get(state.ind);
  $$(".seg button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === state.view)));
  $("#map-controls").hidden = state.view !== "map";
  stopPlay();
  const geoName = (g) => store.geoName(g);
  if (state.view === "chart") {
    chart(el, { rows, geos: state.geos, geoName, unit: series.unit, y0: state.y0, y1: state.y1 });
  } else if (state.view === "table") {
    table(el, { rows, geos: state.geos, geoName, y0: state.y0, y1: state.y1, unit: series.unit });
  } else {
    const countries = new Set(store.geo.filter((g) => g.type === "country").map((g) => g.code));
    const years = [...new Set(rows.filter((r) => countries.has(r.geo)).map((r) => r.year))].sort((a, b) => a - b);
    if (!years.length) {
      el.innerHTML = `<p class="msg">This indicator has no country-level values to map.</p>`;
      $("#map-controls").hidden = true;
      return;
    }
    if (!state.mapYear || !years.includes(state.mapYear)) {
      state.mapYear = bestMapYear(rows, countries, ind.y0, state.y1) || years[years.length - 1];
    }
    const slider = $("#map-year");
    slider.min = years[0];
    slider.max = years[years.length - 1];
    slider.value = state.mapYear;
    $("#map-year-out").textContent = state.mapYear;
    el.innerHTML = `<p class="msg">Drawing map…</p>`;
    await map(el, { rows, year: state.mapYear, geoByCode: store.geoByCode, unit: series.unit, highlight: new Set(state.geos) });
  }
}

function stopPlay() {
  if (playTimer) {
    clearInterval(playTimer);
    playTimer = null;
    $("#map-play").textContent = "Play";
  }
}

function renderCard() {
  const ind = store.byId.get(state.ind);
  const cite = citation(ind, series);
  const flags = series.flags?.length ? `<dt>Flags</dt><dd>${series.flags.map(esc).join(", ")} (shown with * in the table)</dd>` : "";
  $("#ind-card").innerHTML = `
    <div>
      <h2>About this indicator</h2>
      <p class="desc">${esc(series.description || "No description provided by the source.")}</p>
      ${ind.m === "approximate" ? `<p><span class="badge">Related indicator</span> The SDG goal shown is an approximate thematic link made by this site, not an official UN mapping.</p>` : ""}
    </div>
    <div>
      <dl>
        <dt>Source</dt><dd>${esc(cite.source)}${cite.url ? ` · <a href="${esc(cite.url)}" target="_blank" rel="noopener">source page</a>` : ""}</dd>
        ${series.source_org ? `<dt>Produced by</dt><dd>${esc(series.source_org)}</dd>` : ""}
        <dt>Code</dt><dd>${esc(ind.c)}</dd>
        <dt>Release</dt><dd>${esc(series.release || "–")}</dd>
        <dt>Coverage</dt><dd>${ind.ng} places, ${ind.y0}–${ind.y1}, ${ind.nr.toLocaleString("en")} values</dd>
        ${state.dims ? `<dt>Breakdown</dt><dd>${esc(dimsText(state.dims))}</dd>` : ""}
        ${flags}
        <dt>Licence</dt><dd>${esc(series.license || "See source")}</dd>
      </dl>
    </div>`;
}

// ---------- places ----------
function geoMatches(q) {
  const n = normalize(q);
  if (!n) return [];
  const avail = new Set(currentRows().map((r) => r.geo));
  const out = [];
  for (const g of store.geo) {
    const names = [g.name, ...(g.aliases || [])].map(normalize);
    const starts = names.some((x) => x.startsWith(n));
    const has = names.some((x) => x.includes(n));
    if (!has && g.code.toLowerCase() !== n) continue;
    out.push({ g, s: (starts ? 2 : 0) + (avail.has(g.code) ? 1 : 0) + (g.type === "country" ? 0.5 : 0) });
  }
  return out.sort((a, b) => b.s - a.s || a.g.name.localeCompare(b.g.name)).slice(0, 12).map((x) => ({ ...x.g, has: avail.has(x.g.code) }));
}

function addGeos(codes) {
  state.geos = [...new Set([...state.geos, ...codes])].slice(0, 40);
  render();
}

function addRegion(code, members = false) {
  const g = store.geoByCode.get(code);
  const avail = new Set(currentRows().map((r) => r.geo));
  if (!members && avail.has(code)) return addGeos([code]);
  const list = (g?.members || []).filter((m) => avail.has(m));
  if (list.length) return addGeos(list);
  addGeos([code]);
}

function renderTree() {
  const root = $("#geo-tree");
  const make = (code, depth) => {
    const kids = (store.children.get(code) || []).filter((c) => store.geoByCode.get(c)?.type !== "aggregate");
    const g = store.geoByCode.get(code);
    const nMembers = g?.members?.length || 0;
    return `<li data-code="${esc(code)}"><div class="tree-row">
      ${kids.length ? `<button type="button" class="exp" aria-expanded="false" aria-label="Show places in ${esc(g?.name)}">▸</button>` : `<span class="exp"></span>`}
      <button type="button" class="pick">${esc(g?.name || code)}</button>
      ${g?.type === "region" && nMembers ? `<button type="button" class="all" data-all="${esc(code)}">+ ${nMembers} countries</button>` : ""}
    </div></li>`;
  };
  const top = ["UN-001"].filter((c) => store.geoByCode.has(c));
  const groups = store.geo.filter((g) => g.type === "group").map((g) => g.code);
  root.innerHTML = `<ul class="tree">${top.map((c) => make(c, 0)).join("")}${groups.map((c) => make(c, 0)).join("")}</ul>`;
  root.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-code]");
    if (!li) return;
    const code = li.dataset.code;
    if (e.target.classList.contains("exp") && e.target.tagName === "BUTTON") {
      const open = e.target.getAttribute("aria-expanded") === "true";
      e.target.setAttribute("aria-expanded", String(!open));
      e.target.textContent = open ? "▸" : "▾";
      const sub = li.querySelector(":scope > ul");
      if (sub) {
        sub.hidden = open;
      } else {
        const kids = (store.children.get(code) || []).filter((c) => store.geoByCode.get(c)?.type !== "aggregate");
        li.insertAdjacentHTML("beforeend", `<ul class="tree">${kids.map((c) => make(c)).join("")}</ul>`);
      }
    } else if (e.target.classList.contains("pick")) {
      addRegion(code);
    } else if (e.target.dataset.all) {
      addRegion(e.target.dataset.all, true);
    }
  });
}

// ---------- ask ----------
async function ask(question, opts = {}) {
  const box = $("#answer");
  box.hidden = false;
  box.innerHTML = `<p class="interp">Looking that up…</p>`;
  let res = null;
  let viaApi = false;
  if (CONFIG.apiUrl && !opts.indicator) {
    try {
      res = await askApi(CONFIG.apiUrl, question, { indicator: state.ind, geos: state.geos, y0: state.y0, y1: state.y1 });
      viaApi = true;
    } catch (e) {
      console.warn("Chat API unavailable, using the built-in engine", e);
    }
  }
  if (!res) res = await askLocal(store, question, opts);
  if (!res.ok && !res.answer) {
    box.innerHTML = `<div class="answer-body">${res.html}</div>`;
    return;
  }
  if (viaApi) {
    renderApiAnswer(box, res);
  } else {
    const c = res.citation;
    box.innerHTML = `
      <p class="interp">Showing <b>${res.interp}</b></p>
      <div class="answer-body">${res.html}</div>
      <p class="cite">Source: ${esc(c.source)}${c.org ? ` (${esc(c.org)})` : ""}, series ${esc(c.code)}${c.release ? `, release ${esc(c.release)}` : ""}.${res.flagged ? " Some values are flagged by the source, for example as estimates." : ""}</p>
      ${res.alternatives?.length ? `<p class="alts">Not the right measure? ${res.alternatives.map((id) => `<button type="button" data-alt="${esc(id)}">${esc(store.byId.get(id)?.n || id)} (${SOURCE_NAMES[store.byId.get(id)?.s] || ""})</button>`).join("")}</p>` : ""}`;
    $$("[data-alt]", box).forEach((b) => b.addEventListener("click", () => ask(question, { indicator: b.dataset.alt })));
  }
  const v = res.view || {};
  const id = res.indicator || v.indicator;
  if (id && store.byId.has(id)) {
    state.dims = res.dims ?? v.dims ?? "";
    if (v.geos?.length) state.geos = v.geos;
    const ind = store.byId.get(id);
    if (v.y0 || v.y1) {
      state.y1 = Math.min(v.y1 || ind.y1, ind.y1);
      state.y0 = Math.max(v.y0 || ind.y0, ind.y0);
      if (state.y0 > state.y1) state.y0 = state.y1;
    }
    state.view = v.mode || "chart";
    await selectIndicator(id, { keepGeos: true, keepYears: true, keepDims: true });
  }
}

function renderApiAnswer(box, res) {
  const cites = (res.citations || []).map((c) =>
    `${esc(c.source)}, ${esc(c.name || "")} [${esc(c.code)}]${c.release ? `, release ${esc(c.release)}` : ""}`).join("; ");
  box.innerHTML = `
    <p class="interp ai-badge">Answered by the AI assistant, which can only use database tools. Questions are sent to a third-party language model.</p>
    <div class="answer-body">${(res.answer || "").split(/\n{2,}/).map((p) => `<p>${esc(p)}</p>`).join("")}</div>
    ${cites ? `<p class="cite">Sources: ${cites}</p>` : ""}`;
}

// ---------- controls ----------
function bindControls() {
  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#ask-input").value.trim();
    if (q) ask(q);
  });
  $$(".example").forEach((b) => b.addEventListener("click", () => {
    $("#ask-input").value = b.textContent;
    ask(b.textContent);
  }));
  $("#ind-search").addEventListener("input", debounce(renderBrowser, 150));

  const gs = $("#geo-search");
  const results = $("#geo-results");
  let active = -1;
  const showResults = () => {
    const list = geoMatches(gs.value);
    active = -1;
    results.hidden = !list.length;
    results.innerHTML = list.map((g) => `<li><button type="button" data-code="${esc(g.code)}">${esc(g.name)}<small>${g.type === "country" ? "" : g.type}${g.has ? "" : " · no data"}</small></button></li>`).join("");
  };
  gs.addEventListener("input", debounce(showResults, 100));
  gs.addEventListener("keydown", (e) => {
    const btns = $$("button", results);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      active = Math.max(0, Math.min(btns.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
      btns.forEach((b, i) => b.classList.toggle("active", i === active));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const b = btns[Math.max(0, active)];
      if (b) b.click();
    } else if (e.key === "Escape") {
      results.hidden = true;
    }
  });
  results.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-code]");
    if (!b) return;
    const g = store.geoByCode.get(b.dataset.code);
    if (g && (g.type === "region" || g.type === "group")) addRegion(g.code);
    else addGeos([b.dataset.code]);
    gs.value = "";
    results.hidden = true;
  });
  renderTree();

  const yearChange = () => {
    const ind = store.byId.get(state.ind);
    let a = +$("#y0").value || ind.y0;
    let b = +$("#y1").value || ind.y1;
    a = Math.max(ind.y0, Math.min(a, ind.y1));
    b = Math.max(ind.y0, Math.min(b, ind.y1));
    if (a > b) [a, b] = [b, a];
    state.y0 = a;
    state.y1 = b;
    render();
  };
  $("#y0").addEventListener("change", yearChange);
  $("#y1").addEventListener("change", yearChange);
  $$(".presets button").forEach((b) => b.addEventListener("click", () => {
    const ind = store.byId.get(state.ind);
    state.y1 = ind.y1;
    state.y0 = { sdg: Math.max(ind.y0, 2015), 2000: Math.max(ind.y0, 2000), all: ind.y0 }[b.dataset.preset];
    render();
  }));
  $$(".seg button").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view;
    renderView();
    writeHash();
  }));
  $("#map-year").addEventListener("input", (e) => {
    state.mapYear = +e.target.value;
    $("#map-year-out").textContent = state.mapYear;
    drawMapOnly();
  });
  $("#map-play").addEventListener("click", () => {
    if (playTimer) return stopPlay();
    const s = $("#map-year");
    $("#map-play").textContent = "Pause";
    if (+s.value >= +s.max) s.value = s.min;
    playTimer = setInterval(() => {
      const next = +s.value + 1;
      if (next > +s.max) return stopPlay();
      s.value = next;
      state.mapYear = next;
      $("#map-year-out").textContent = next;
      drawMapOnly();
    }, 700);
  });
  $("#dl-csv").addEventListener("click", downloadCsv);
  $("#copy-link").addEventListener("click", () => copy(location.href, "#copy-link", "Link copied"));
  $("#copy-cite").addEventListener("click", () => {
    const ind = store.byId.get(state.ind);
    const c = citation(ind, series);
    const text = `${c.source}${c.org ? ` (${c.org})` : ""}. ${ind.n} [${c.code}]${c.release ? `, release ${c.release}` : ""}. Accessed ${new Date().toISOString().slice(0, 10)} via Global Sustainability Explorer, ${location.href}`;
    copy(text, "#copy-cite", "Citation copied");
  });
  $(".filters-toggle").addEventListener("click", (e) => {
    const f = $("#filters");
    f.classList.toggle("open");
    e.target.setAttribute("aria-expanded", String(f.classList.contains("open")));
  });
}

let mapDrawing = false;
async function drawMapOnly() {
  if (mapDrawing) return;
  mapDrawing = true;
  try {
    await map($("#view"), { rows: currentRows(), year: state.mapYear, geoByCode: store.geoByCode, unit: series.unit, highlight: new Set(state.geos) });
  } finally {
    mapDrawing = false;
  }
}

function copy(text, sel, done) {
  const btn = $(sel);
  const label = btn.textContent;
  navigator.clipboard?.writeText(text).then(() => {
    btn.textContent = done;
    setTimeout(() => (btn.textContent = label), 1600);
  }).catch(() => window.prompt("Copy this:", text));
}

function downloadCsv() {
  const ind = store.byId.get(state.ind);
  const c = citation(ind, series);
  const rows = state.view === "map"
    ? currentRows().filter((r) => r.year === state.mapYear && store.geoByCode.get(r.geo)?.type === "country")
    : currentRows().filter((r) => state.geos.includes(r.geo) && r.year >= state.y0 && r.year <= state.y1);
  const header = ["place", "place_code", "year", "value", "unit", "breakdown", "flag", "indicator", "indicator_code", "source", "release"];
  const lines = [header.join(",")].concat(rows.map((r) => [store.geoName(r.geo), r.geo, r.year, r.value, series.unit,
    dimsText(r.dims), r.flag, ind.n, ind.c, c.source, c.release].map(csvEscape).join(",")));
  download(`${ind.c.replace(/[^A-Za-z0-9_.-]/g, "_")}.csv`, lines.join("\n"));
}

function debounce(fn, ms) {
  let t;
  return (...a) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...a), ms);
  };
}

// ---------- news ----------
let news = null;
const newsFilter = { kind: "all", sdg: null, event: null };
async function loadNews(quiet = false) {
  if (!news) {
    try {
      news = await fetch("data/news.json").then((r) => (r.ok ? r.json() : null));
    } catch {
      news = null;
    }
  }
  const live = news?.events?.some((e) => e.status === "live");
  $(".live-dot").hidden = !live;
  if (quiet) return;
  if (!news) {
    $("#news-list").innerHTML = `<li class="news-item"><p>News has not been collected yet. It refreshes every few hours.</p></li>`;
    return;
  }
  if (news.generated) $("#news-lede").textContent = `Headlines from UN and specialist newsrooms, last refreshed ${new Date(news.generated).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}. Each item links to the original.`;
  renderEvents();
  renderNewsFilters();
  renderNews();
}

function renderEvents() {
  const order = { live: 0, upcoming: 1, past: 2 };
  const evs = [...news.events].sort((a, b) => order[a.status] - order[b.status] || a.start.localeCompare(b.start));
  $("#events").innerHTML = evs.map((e) => `<li class="event ${e.status}">
      <span class="status">${e.status === "live" ? "Happening now" : e.status === "upcoming" ? "Upcoming" : "Ended"}</span>
      <b><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.name)}</a></b>
      <span>${dateText(e.start)} – ${dateText(e.end)} · ${esc(e.place)}</span>
      <button type="button" class="example" data-event="${esc(e.short)}">News about ${esc(e.short)}</button></li>`).join("");
  $$("[data-event]").forEach((b) => b.addEventListener("click", () => {
    newsFilter.event = newsFilter.event === b.dataset.event ? null : b.dataset.event;
    renderNewsFilters();
    renderNews();
  }));
  $("#feeds").innerHTML = news.feeds.map((f) => `<li><span>${esc(f.name)}</span><span class="${f.ok ? "" : "down"}">${f.ok ? `${f.items} ${f.items === 1 ? "item" : "items"}` : "unavailable"}</span></li>`).join("");
}

function renderNewsFilters() {
  const sdgs = new Set(news.items.flatMap((i) => i.sdgs || []));
  const kinds = [["all", "All sources"], ["official", "UN system"], ["specialist", "Specialist newsrooms"]];
  $("#news-filters").innerHTML =
    kinds.map(([k, l]) => `<button type="button" data-kind="${k}" aria-pressed="${newsFilter.kind === k}">${l}</button>`).join("") +
    [...sdgs].sort((a, b) => a - b).map((g) => `<button type="button" class="sdg-f" data-sdg="${g}" aria-pressed="${newsFilter.sdg === g}"><i style="background:${SDG_COLORS[g]}"></i>SDG ${g}</button>`).join("") +
    (newsFilter.event ? `<button type="button" data-clear-event aria-pressed="true">${esc(newsFilter.event)} ×</button>` : "");
  $$("[data-kind]").forEach((b) => b.addEventListener("click", () => { newsFilter.kind = b.dataset.kind; renderNewsFilters(); renderNews(); }));
  $$("[data-sdg]").forEach((b) => b.addEventListener("click", () => { newsFilter.sdg = newsFilter.sdg === b.dataset.sdg ? null : b.dataset.sdg; renderNewsFilters(); renderNews(); }));
  $("[data-clear-event]")?.addEventListener("click", () => { newsFilter.event = null; renderNewsFilters(); renderNews(); });
}

function renderNews() {
  const liveEvents = new Set(news.events.filter((e) => e.status === "live").map((e) => e.short));
  let items = news.items.filter((i) =>
    (newsFilter.kind === "all" || i.kind === newsFilter.kind) &&
    (!newsFilter.sdg || (i.sdgs || []).includes(newsFilter.sdg)) &&
    (!newsFilter.event || i.event === newsFilter.event));
  const pinned = newsFilter.event ? [] : items.filter((i) => liveEvents.has(i.event)).slice(0, 3);
  items = [...pinned, ...items.filter((i) => !pinned.includes(i))].slice(0, 150);
  $("#news-list").innerHTML = items.length ? items.map((i) => `<li class="news-item ${pinned.includes(i) ? "pinned" : ""}">
      <h3><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a></h3>
      <div class="meta"><span>${esc(i.source)}</span><span>${dateText(i.date)}</span>
        ${i.event ? `<span class="ev">${esc(i.event)}</span>` : ""}
        ${(i.sdgs || []).length ? `<span class="sdgs">${i.sdgs.map((g) => `<b style="background:${SDG_COLORS[g]};color:${luminance(SDG_COLORS[g]) > 0.38 ? "#132A2E" : "#fff"}" title="SDG ${g}">${g}</b>`).join("")}</span>` : ""}
        ${(i.countries || []).length ? `<span>${i.countries.slice(0, 4).map((c) => esc(store.geoName(c))).join(", ")}</span>` : ""}
      </div>
      ${i.teaser ? `<p>${esc(i.teaser)}</p>` : ""}</li>`).join("")
    : `<li class="news-item"><p>No news matches these filters.</p></li>`;
}

// ---------- updates ----------
let updatesLoaded = false;
async function loadUpdates() {
  if (updatesLoaded) return;
  updatesLoaded = true;
  const [log, meta] = await Promise.all([
    fetch("data/changelog.json").then((r) => (r.ok ? r.json() : [])).catch(() => []),
    Promise.resolve(store.meta),
  ]);
  if (meta) {
    $("#sources-status").innerHTML = `<table class="status-table"><thead><tr><th>Source</th><th>Status</th><th>Release / version</th><th>Indicators</th><th>Values</th></tr></thead><tbody>
      ${Object.entries(meta.sources).map(([k, s]) => `<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a></td>
        <td class="${s.status === "ok" ? "ok" : s.status === "failed" ? "failed" : ""}">${s.status === "ok" ? "Checked" : s.status === "failed" ? "Update failed, previous data kept" : esc(s.status || "–")}</td>
        <td>${esc(s.release || "–")}</td><td>${(s.indicators ?? "–").toLocaleString?.("en") ?? s.indicators}</td><td>${(s.rows ?? 0).toLocaleString("en")}</td></tr>`).join("")}
      </tbody></table><p class="page-lede">Last check ${new Date(meta.generated).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}. ${meta.rows.toLocaleString("en")} values across ${meta.indicators.toLocaleString("en")} indicators.</p>`;
  }
  $("#changelog").innerHTML = log.length ? log.map((e) => `<li>
      <h3>${dateText(e.date)}</h3>
      <p>${e.new_indicators ? `${e.new_indicators.toLocaleString("en")} new indicators. ` : ""}${e.changed_indicators ? `${e.changed_indicators.toLocaleString("en")} indicators changed: ${e.added_rows.toLocaleString("en")} values added, ${e.revised_values.toLocaleString("en")} revised${e.removed_rows ? `, ${e.removed_rows.toLocaleString("en")} removed` : ""}.` : ""}</p>
      ${e.changed?.length ? `<ul>${e.changed.slice(0, 12).map((c) => `<li><a href="#explore?i=${encodeURIComponent(c.id)}">${esc(c.name)}</a>${c.new_latest_year ? ` (now to ${c.new_latest_year})` : ""}${c.added ? `, +${c.added} values` : ""}${c.revised ? `, ${c.revised} revised` : ""}</li>`).join("")}</ul>` : ""}
      ${e.notes?.length ? `<p class="notes">${e.notes.map(esc).join("<br>")}</p>` : ""}
    </li>`).join("") : `<li><p>No updates recorded yet.</p></li>`;
}

// ---------- about ----------
function renderAbout() {
  const m = store.meta;
  $("#about").innerHTML = `
    <h1 class="page-title">About this explorer</h1>
    <p class="page-lede">A free tool for researchers to find, compare and cite global sustainability data in one place.</p>
    <h2>What's inside</h2>
    <p>${m ? `${m.indicators.toLocaleString("en")} indicators and ${m.rows.toLocaleString("en")} values` : "Indicators"} from three open sources:</p>
    <ul>
      <li><a href="https://unstats.un.org/sdgs/dataportal">UN SDG Global Database</a>: the official data series behind the 17 Sustainable Development Goals, with breakdowns by sex, age and location where reported.</li>
      <li><a href="https://databank.worldbank.org/source/world-development-indicators">World Bank World Development Indicators</a>: indicators on energy, environment, climate change, poverty and agriculture.</li>
      <li><a href="https://ourworldindata.org">Our World in Data</a>: CO2, greenhouse-gas and energy datasets.</li>
    </ul>
    <h2>How it stays up to date</h2>
    <p>A scheduled job checks every source each week and the news feeds every few hours. It only re-downloads series whose release has changed, validates the result, and keeps the last good copy if a source fails or looks wrong. The <a href="#updates">What's new</a> page lists every change.</p>
    <h2>How answers are made</h2>
    <p>The Ask box matches your question to an indicator, places and years, then computes the answer from the stored values. It never invents a number: every figure comes from a data row and carries its source, series code and release. If it picks the wrong measure, choose another from the list under the answer.</p>
    <h2>Things to keep in mind</h2>
    <ul>
      <li>Sources define indicators differently. The same topic (for example CO2 emissions) can appear from several sources with different methods; they are kept separate on purpose.</li>
      <li>World Bank and Our World in Data indicators are linked to SDGs thematically by this site. Only UN SDG series carry an official goal, target and indicator code.</li>
      <li>Regional and income-group aggregates are shown as each source publishes them and are never mixed across sources.</li>
      <li>Values marked as estimates or modelled by the source are flagged.</li>
    </ul>
    <h2>Cite</h2>
    <p>Cite the original source shown under each indicator (use “Copy citation”). To cite the tool: Sahil (2026). <i>Global Sustainability Explorer</i>. https://github.com/sahiljangra12/global-sustainability-explorer</p>`;
}
