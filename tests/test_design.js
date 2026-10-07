const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");

const D = load("Design.js");
const I = load("Icons.js");
const L = load("Level.js");

// --- contrast-safe tones -------------------------------------------------------
assert.equal(Math.round(D.contrast("#000000", "#ffffff")), 21);
assert.equal(D.hex({ r: 1, g: 0.5, b: 0 }), "#ff8000");
assert.equal(D.hex("#ff7fbf7f"), "#7fbf7f", "Qt's #aarrggbb strings are accepted");

const DARK = "#101315", LIGHT = "#e6e7ed";
const fallback = L.palette({}, {});
for (const name of ["ok", "warn", "critical"]) {
  assert.equal(D.ensureContrast(fallback[name], DARK, D.TEXT_CONTRAST), fallback[name],
    name + ": a colour that already reads on a dark surface is unchanged");
}
const light = D.tones(fallback, LIGHT, D.TEXT_CONTRAST);
const lightMarks = D.tones(fallback, LIGHT, D.MARK_CONTRAST);
for (const name of ["ok", "warn", "critical"]) {
  assert.ok(D.contrast(light[name], LIGHT) >= 4.5, name + " text reaches 4.5:1 on a light surface");
  assert.ok(D.contrast(lightMarks[name], LIGHT) >= 3, name + " marks reach 3:1 on a light surface");
  assert.ok(Math.abs(L.hue(light[name]) - L.hue(fallback[name])) < 2, name + " keeps its hue");
}
// The yellow that failed on light surfaces (1.4:1) becomes a readable amber.
assert.ok(D.contrast(fallback.warn, LIGHT) < 2);
assert.ok(L.inHue(light.warn, 30, 65), "still passes Level's yellow hue band");
assert.ok(L.inHue(light.critical, 340, 20), "still passes Level's red hue band");
assert.ok(D.contrast(light.warn, LIGHT) < 6, "only as much darker as needed");
// Bright colour on a dark surface that is too dim gets lighter, not darker.
const dim = D.ensureContrast("#5a2020", DARK, 4.5);
assert.ok(D.luminance(dim) > D.luminance("#5a2020"));
assert.equal(D.ensureContrast("not a colour", DARK, 4.5), "not a colour");

// --- tints: text derived against the composited surface ------------------------------
assert.equal(D.over("#80ff0000", "#000000"), "#800000", "Qt's #aarrggbb alpha composites over the surface");
assert.equal(D.over({ r: 1, g: 0, b: 0, a: 1 }, "#ffffff", 0.5), "#ff8080", "a QML color, times an explicit alpha");
assert.equal(D.over({ r: 0, g: 0, b: 0, a: 0.5 }, "#ffffff", 0.5), "#bfbfbf", "both alphas apply");
assert.equal(D.tinted("#ff0000", [0.2, 0.4], ["#000000", "#ffffff"]).length, 4);
const two = D.ensureContrast("#e06c6c", ["#e6e7ed", "#c0c0c0"], 4.5);
for (const bg of ["#e6e7ed", "#c0c0c0"]) assert.ok(D.contrast(two, bg) >= 4.5, "every listed surface: " + bg);
assert.equal(D.ensureContrast("#e06c6c", ["#101315"], 4.5), D.ensureContrast("#e06c6c", "#101315", 4.5), "a list of one is one surface");

// The reviewed regression: on the light stub theme, critical ink #a24e4e read
// 4.555:1 on the card but 3.33:1 on the hovered "Kill?" tint and 3.95:1 in
// the percentage badge. Re-derive exactly as Panel.qml does, for both stub
// themes and for a host with a heavier, accent-coloured row cursor.
function panelColours(bg, fg, cursorFill) {
  const surfaces = D.neutralSurfaces(bg, cursorFill, fg);
  const ink = D.tones(fallback, surfaces, D.TEXT_CONTRAST), tones = D.tones(fallback, surfaces, D.MARK_CONTRAST);
  const confirmOn = D.tinted(tones.critical, [D.CONFIRM_TINT, D.CONFIRM_HOT_TINT], [bg, surfaces[1]]);
  const confirmInk = D.ensureContrast(ink.critical, confirmOn, D.TEXT_CONTRAST);
  const badge = {};
  for (const name of ["ok", "warn", "critical"]) {
    const on = D.tinted(tones[name], [D.BADGE_TINT], [bg]);
    badge[name] = { on: on, ink: D.ensureContrast(ink[name], on, D.TEXT_CONTRAST) };
  }
  return { surfaces, ink, tones, confirmOn, confirmInk, badge, accent: D.ensureContrast("#7aa2f7", surfaces, D.MARK_CONTRAST) };
}
const before = { ink: "#a24e4e", tone: "#cf6464" };
assert.ok(D.contrast(before.ink, D.over(before.tone, D.over({ r: 0x34 / 255, g: 0x3b / 255, b: 0x58 / 255, a: 0.08 }, LIGHT), 0.3)) < 3.4,
  "the old ink failed on the hovered confirmation");
const themes = {
  light: [LIGHT, "#343b58", { r: 0x34 / 255, g: 0x3b / 255, b: 0x58 / 255, a: 0.08 }],
  dark: [DARK, "#cacccc", { r: 0xca / 255, g: 0xcc / 255, b: 0xcc / 255, a: 0.08 }],
  lightAccentCursor: [LIGHT, "#343b58", { r: 0x7a / 255, g: 0xa2 / 255, b: 0xf7 / 255, a: 0.2 }],
  darkAccentCursor: [DARK, "#cacccc", { r: 0x7a / 255, g: 0xa2 / 255, b: 0xf7 / 255, a: 0.2 }]
};
for (const [theme, [bg, fg, cursor]] of Object.entries(themes)) {
  const c = panelColours(bg, fg, cursor);
  assert.equal(c.surfaces.length, 4);
  assert.equal(c.surfaces[0], bg);
  for (const name of ["ok", "warn", "critical"]) {
    for (const s of c.surfaces) {
      assert.ok(D.contrast(c.ink[name], s) >= 4.5, theme + " " + name + " text on " + s + ": " + D.contrast(c.ink[name], s));
      assert.ok(D.contrast(c.tones[name], s) >= 3, theme + " " + name + " mark on " + s);
    }
    assert.ok(Math.abs(L.hue(c.ink[name]) - L.hue(fallback[name])) < 3, theme + " " + name + " keeps its hue");
    for (const s of c.badge[name].on)
      assert.ok(D.contrast(c.badge[name].ink, s) >= 4.5, theme + " " + name + " badge: " + D.contrast(c.badge[name].ink, s));
  }
  for (const s of c.confirmOn)
    assert.ok(D.contrast(c.confirmInk, s) >= 4.5, theme + " Kill? on " + s + ": " + D.contrast(c.confirmInk, s));
  assert.equal(c.confirmOn.length, 4, "rest and hover, on a plain and a cursor row");
  assert.ok(L.inHue(c.confirmInk, 340, 20), theme + " Kill? stays red: " + c.confirmInk);
  for (const s of c.surfaces) assert.ok(D.contrast(c.accent, s) >= 3, theme + " accent mark on " + s);
  // The kill button's hover: its icon (a mark) on the host's hover fill of that colour, on a cursor row.
  assert.ok(D.contrast(c.ink.critical, D.over(c.ink.critical, c.surfaces[1], 0.08)) >= 3, theme + " kill icon on hover");
}
// Dark themes keep the theme's own colours wherever they already read.
assert.equal(panelColours(DARK, "#cacccc", themes.dark[2]).ink.warn, fallback.warn);

// --- threshold labels: packed inside the meter, never overlapping -----------------
function checkPacked(centers, widths, gap, extent, label) {
  const x = D.packLabels(centers, widths, gap, extent);
  const order = centers.map((_, i) => i).sort((a, b) => centers[a] - centers[b] || a - b);
  for (const i of order) assert.ok(x[i] >= 0 && x[i] + widths[i] <= extent + 1e-9, label + ": label " + i + " inside the meter " + x);
  for (let k = 1; k < order.length; k++)
    assert.ok(x[order[k]] >= x[order[k - 1]] + widths[order[k - 1]] + gap - 1e-9, label + ": no overlap " + x);
  return x;
}
const W = 400, lw = (pct) => String(pct).length * 8 + 8; // monospace caption: digits + "%"
for (const [warn, crit] of [[88, 90], [99, 100], [100, 99], [100, 100], [1, 2], [1, 1], [2, 1], [50, 51], [1, 100], [75, 90], [97, 98]]) {
  checkPacked([W * warn / 100, W * crit / 100], [lw(warn), lw(crit)], 4, W, warn + "/" + crit);
}
assert.deepEqual(plain(D.packLabels([300, 360], [24, 24], 4, 400)), [288, 348], "far apart: each centred on its tick");
assert.deepEqual(plain(D.packLabels([396, 400], [24, 32], 4, 400)), [340, 368], "99/100: the last fits, the first moves left");
assert.deepEqual(plain(D.packLabels([400, 396], [32, 24], 4, 400)), [368, 340], "order follows position, not index");
assert.deepEqual(plain(D.packLabels([4, 8], [16, 16], 4, 400)), [0, 20], "1/2: the first fits at 0, the second moves right");
assert.deepEqual(plain(D.packLabels([10, 10], [20, 20], 4, 30)), [0, 10], "cannot fit side by side: the first stays at 0");
assert.deepEqual(plain(D.packLabels([], [], 4, 400)), []);

// --- Shapes in scrolling views: hidden while wholly outside the viewport ----------
{
  // A list 100 px tall scrolled by 40: rows at content y 0, 50, 100, 150 (50 tall).
  const content = { x: 0, y: -40, width: 200, height: 200, parent: null };
  const view = { x: 0, y: 0, width: 200, height: 100, contentItem: content, contentX: 0, contentY: 40, parent: null };
  content.parent = view;
  const row = (y) => ({ x: 0, y, width: 200, height: 50, parent: content });
  const icon = (r, y) => ({ x: 170, y, width: 16, height: 16, parent: { x: 0, y: 0, width: 200, height: 50, parent: r } });
  assert.equal(D.scroller(icon(row(0), 17)), view, "the nearest scrolling ancestor");
  assert.equal(D.scroller({ parent: { parent: null } }), null);
  assert.equal(D.scroller({ parent: { contentItem: {}, parent: null } }), null, "a Control's contentItem is not a scroller");
  assert.ok(D.inViewport(icon(row(50), 17), view), "a row in view");
  assert.ok(!D.inViewport(icon(row(0), 17), view), "scrolled above: 0+17+16 <= 40");
  assert.ok(D.inViewport(icon(row(0), 30), view), "partly visible: Qt clips it");
  assert.ok(!D.inViewport(icon(row(138), 17), view), "a row peeking 2 px into the bottom: its icon is below");
  assert.ok(D.inViewport(icon(row(110), 10), view), "bottom edge, partly visible");
  assert.ok(D.inViewport(icon(row(0), 0), null), "no view: drawn");
  assert.ok(D.inViewport({ x: 0, y: 0, width: 1, height: 1, parent: null }, view), "not (yet) inside the view: drawn");
  content.x = -150;
  assert.ok(!D.inViewport({ x: 100, y: 60, width: 40, height: 10, parent: content }, view), "scrolled sideways past it");
}

// --- bowl gauge: a linear level ----------------------------------------------------
const B = I.BOWL;
assert.equal(D.brothY(0), B.baseY);
assert.equal(D.brothY(1), B.rimY);
assert.equal(D.brothY(0.5), (B.baseY + B.rimY) / 2, "level is linear in the reading");
assert.equal(D.brothY(-3), B.baseY);
assert.equal(D.brothY(7), B.rimY);
assert.equal(D.brothY(null), B.baseY);
assert.deepEqual([D.steamCount("ok"), D.steamCount("warn"), D.steamCount("critical"), D.steamCount("unknown")], [0, 1, 2, 0]);

// The level (a position) carries the reading; the filled *area* of a bowl is
// not linear. Measure that area against the level so the design note can
// state it: the base is steep enough that area stays close to the level.
function cubic(p, t) {
  const u = 1 - t;
  return [0, 1].map((k) => u * u * u * p[0][k] + 3 * u * u * t * p[1][k] + 3 * u * t * t * p[2][k] + t * t * t * p[3][k]);
}
function halfWidthAt(y) {
  let lo = 0, hi = 1;
  for (let i = 0; i < 50; i++) { const mid = (lo + hi) / 2; if (cubic(B.left, mid)[1] < y) lo = mid; else hi = mid; }
  return B.width / 2 - cubic(B.left, (lo + hi) / 2)[0];
}
function area(fromY, toY) {
  const n = 2000; let sum = 0;
  for (let i = 0; i < n; i++) sum += 2 * halfWidthAt(fromY + (toY - fromY) * (i + 0.5) / n) * (toY - fromY) / n;
  return sum;
}
const whole = area(B.rimY, B.baseY);
const ratios = {};
for (const f of [0.25, 0.5, 0.75, 0.9]) ratios[f] = area(D.brothY(f), B.baseY) / whole / f;
assert.ok(ratios[0.9] > 0.975 && ratios[0.9] <= 1.0, "near the rim area tracks level: " + ratios[0.9]);
assert.ok(ratios[0.75] > 0.95, "from 75% (warn/critical) area is within 5% of the level: " + ratios[0.75]);
assert.ok(ratios[0.5] > 0.87, "at 50% within 13%: " + ratios[0.5]);
if (process.env.RAMAN_PRINT_BOWL) console.log("area/level", ratios);

// --- icons --------------------------------------------------------------------
const allowed = /^[MLHVCQAZ0-9 .\-]+$/;
for (const name of Object.keys(I.ICONS)) {
  const spec = I.icon(name);
  assert.ok(spec && allowed.test(spec.stroke), name + " uses plain absolute SVG path commands");
  const numbers = spec.stroke.replace(/[A-Z]/g, " ").trim().split(/\s+/).map(Number);
  assert.ok(numbers.every((n) => Number.isFinite(n) && n >= 0 && n <= I.BOX), name + " stays inside the 16-unit box");
}
assert.equal(I.icon("nope"), null);
assert.equal(I.icon("toString"), null, "no prototype names");
assert.ok(I.icon("close").stroke !== I.icon("force").stroke, "kill and force kill differ in shape, not only colour");
assert.equal(I.bowlBody(), "M1.5 4.5 C1.5 11.6 3.2 13.5 9 13.5 C14.8 13.5 16.5 11.6 16.5 4.5");
for (const path of [B.rim, B.foot, B.chopsticks].concat(B.steam)) assert.ok(allowed.test(path));

console.log("design: ok");
