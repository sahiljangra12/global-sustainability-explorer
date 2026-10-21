// Number, unit and file helpers.

export function isPercent(unit) {
  const u = (unit || "").toLowerCase();
  return u === "%" || u.startsWith("percent") || u === "percentage" || /^% of/.test(u) ||
    /\(%\)$/.test(u);
}

export function fmt(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const a = Math.abs(v);
  let s;
  if (a >= 1e12) s = (v / 1e12).toLocaleString("en", { maximumFractionDigits: 2 }) + " trillion";
  else if (a >= 1e9) s = (v / 1e9).toLocaleString("en", { maximumFractionDigits: 2 }) + " billion";
  else if (a >= 1e6) s = (v / 1e6).toLocaleString("en", { maximumFractionDigits: 2 }) + " million";
  else if (a >= 100) s = v.toLocaleString("en", { maximumFractionDigits: 0 });
  else if (a >= 1) s = v.toLocaleString("en", { maximumFractionDigits: 2 });
  else s = v.toLocaleString("en", { maximumSignificantDigits: 3 });
  if (isPercent(unit)) return s + "%";
  return s;
}

export function unitSuffix(unit) {
  if (!unit || isPercent(unit)) return "";
  return " " + unit;
}

export function fmtWithUnit(v, unit) {
  return fmt(v, unit) + unitSuffix(unit);
}

export function pctChange(a, b) {
  if (!a) return null;
  return ((b - a) / Math.abs(a)) * 100;
}

export function cagr(a, b, years) {
  if (a <= 0 || b <= 0 || years <= 0) return null;
  return (Math.pow(b / a, 1 / years) - 1) * 100;
}

export function csvEscape(v) {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function download(filename, text, type = "text/csv") {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 0);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

export function dateText(iso) {
  if (!iso) return "";
  // Date-only strings ("2026-10-19") are calendar dates: read them as local noon so no
  // timezone shifts them to the previous day.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

// Mix two hex colours in sRGB (t = share of a). Used for chart colours, which must be
// plain hex rather than CSS color-mix() expressions.
export function mixHex(a, b, t) {
  const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [x, y] = [p(a), p(b)];
  return "#" + x.map((v, i) => Math.round(v * t + y[i] * (1 - t)).toString(16).padStart(2, "0")).join("");
}

export function luminance(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
