// Hero globe: a slowly turning Earth on a canvas, countries coloured by one real indicator.
// Drag to turn it. It stops turning when off screen, when the tab is hidden, and for
// visitors who prefer reduced motion.

import { geoOrthographic, geoPath, geoGraticule10 } from "../vendor/vis.bundle.js";
import { worldMap } from "./views.js";

// Colour ramp for the value (0 → 1): deep ocean blue → SDG 14 blue → green → pale lime.
const STOPS = [
  [0, [19, 32, 43]],
  [0.2, [14, 94, 134]],
  [0.4, [10, 151, 217]],
  [0.6, [63, 178, 127]],
  [0.8, [168, 224, 95]],
  [1, [228, 247, 161]],
];

export function rampColor(t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const [t0, a] = STOPS[i - 1];
      const [t1, b] = STOPS[i];
      const k = (t - t0) / (t1 - t0);
      return `rgb(${a.map((v, j) => Math.round(v + (b[j] - v) * k)).join(",")})`;
    }
  }
  return `rgb(${STOPS[STOPS.length - 1][1].join(",")})`;
}

export async function startGlobe(canvas, { valueByM49 = new Map(), max = 100 } = {}) {
  const world = await worldMap();
  const ctx = canvas.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const projection = geoOrthographic().clipAngle(90).precision(0.5);
  const path = geoPath(projection, ctx);
  const graticule = geoGraticule10();
  const sphere = { type: "Sphere" };
  const fills = world.features.map((f) => {
    const v = valueByM49.get(f.id);
    return v === undefined ? "#16181F" : rampColor(v / max);
  });

  let rot = [-15, -16, 0];
  let w = 0, h = 0, cx = 0, cy = 0, r = 0, dpr = 1;
  let visible = true, dragging = false, last = performance.now(), raf = 0;

  function layout() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth;
    h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const narrow = w < 900;
    // Desktop: a large globe right of centre. Phones: centred, behind the text.
    r = narrow ? Math.min(w * 0.62, h * 0.36) : Math.min(w * 0.29, h * 0.42);
    cx = narrow ? w * 0.5 : w * 0.69;
    cy = narrow ? h * 0.42 : h * 0.5;
    projection.scale(r).translate([cx, cy]);
    draw();
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    projection.rotate(rot);

    // atmosphere
    const glow = ctx.createRadialGradient(cx, cy, r * 0.96, cx, cy, r * 1.28);
    glow.addColorStop(0, "rgba(74, 150, 255, 0.32)");
    glow.addColorStop(0.35, "rgba(74, 150, 255, 0.10)");
    glow.addColorStop(1, "rgba(74, 150, 255, 0)");
    ctx.fillStyle = glow;
    ctx.beginPath();
    ctx.arc(cx, cy, r * 1.28, 0, 2 * Math.PI);
    ctx.fill();

    // ocean
    const ocean = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r);
    ocean.addColorStop(0, "#0E1726");
    ocean.addColorStop(1, "#03050A");
    ctx.beginPath();
    path(sphere);
    ctx.fillStyle = ocean;
    ctx.fill();

    // graticule
    ctx.beginPath();
    path(graticule);
    ctx.strokeStyle = "rgba(240, 240, 250, 0.07)";
    ctx.lineWidth = 0.6;
    ctx.stroke();

    // countries
    for (let i = 0; i < world.features.length; i++) {
      ctx.beginPath();
      path(world.features[i]);
      ctx.fillStyle = fills[i];
      ctx.fill();
    }
    ctx.beginPath();
    for (const f of world.features) path(f);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.55)";
    ctx.lineWidth = 0.5;
    ctx.stroke();

    // light from the upper left, night side lower right
    const shade = ctx.createRadialGradient(cx - r * 0.45, cy - r * 0.5, r * 0.05, cx, cy, r * 1.05);
    shade.addColorStop(0, "rgba(255, 255, 255, 0.10)");
    shade.addColorStop(0.55, "rgba(0, 0, 0, 0)");
    shade.addColorStop(1, "rgba(0, 0, 0, 0.62)");
    ctx.beginPath();
    path(sphere);
    ctx.fillStyle = shade;
    ctx.fill();
    ctx.strokeStyle = "rgba(160, 200, 255, 0.35)";
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function tick(now) {
    const dt = Math.min(64, now - last);
    last = now;
    if (!dragging) {
      rot = [rot[0] + dt * 0.006, rot[1], rot[2]];
      draw();
    }
    raf = requestAnimationFrame(tick);
  }

  function play() {
    cancelAnimationFrame(raf);
    if (reduce.matches || !visible || document.hidden) return;
    last = performance.now();
    raf = requestAnimationFrame(tick);
  }

  // drag to turn
  let start = null;
  canvas.addEventListener("pointerdown", (e) => {
    const dx = e.offsetX - cx, dy = e.offsetY - cy;
    if (dx * dx + dy * dy > r * r * 1.1) return;
    dragging = true;
    start = { x: e.clientX, y: e.clientY, rot: [...rot] };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const k = 90 / r;
    rot = [start.rot[0] + (e.clientX - start.x) * k, Math.max(-60, Math.min(60, start.rot[1] - (e.clientY - start.y) * k)), 0];
    draw();
  });
  const end = () => { dragging = false; };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);

  new ResizeObserver(layout).observe(canvas);
  new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    play();
  }).observe(canvas);
  document.addEventListener("visibilitychange", play);
  reduce.addEventListener?.("change", play);
  layout();
  play();
}
