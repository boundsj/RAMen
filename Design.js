.pragma library
.import "Icons.js" as Icons

// Presentation helpers shared by the QML views: contrast-safe tones of the
// theme's semantic colours, and the bowl gauge's level. Pure JavaScript (no
// QML types), so tests/test_design.js runs it under node.
//
// Level.palette picks which theme colour is "green", "yellow" and "red" (with
// its hue checks). These helpers never change that choice or its hue; they
// only darken (on a light surface) or lighten (on a dark one) a colour that
// would otherwise be too faint to read, e.g. a theme's yellow on a light bar.

// WCAG 2.x thresholds: body text, and large text or graphical marks.
var TEXT_CONTRAST = 4.5
var MARK_CONTRAST = 3

// Semantic tints that text sits on: the kill confirmation (at rest, under the
// pointer) and the Memory percentage badge. Text on them is derived against
// the tint composited over its surface, not against the bare card.
var CONFIRM_TINT = 0.18
var CONFIRM_HOT_TINT = 0.3
var BADGE_TINT = 0.14

// "#rrggbb" / "#aarrggbb" / {r, g, b} with 0..1 channels (a QML color) -> {r, g, b}.
function rgb(c) {
  var v = rgba(c)
  return v ? { r: v.r, g: v.g, b: v.b } : null
}

// As rgb(), keeping alpha (1 unless given).
function rgba(c) {
  if (c && typeof c === "object" && c.r !== undefined)
    return { r: Number(c.r), g: Number(c.g), b: Number(c.b), a: c.a === undefined ? 1 : Number(c.a) }
  var s = String(c || "")
  var m = s.match(/^#([0-9a-fA-F]{2})?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/)
  if (!m) return null
  return { r: parseInt(m[2], 16) / 255, g: parseInt(m[3], 16) / 255, b: parseInt(m[4], 16) / 255,
    a: m[1] === undefined ? 1 : parseInt(m[1], 16) / 255 }
}

function hex(c) {
  var v = rgb(c)
  if (!v) return ""
  function two(x) { var n = Math.max(0, Math.min(255, Math.round(x * 255))); return (n < 16 ? "0" : "") + n.toString(16) }
  return "#" + two(v.r) + two(v.g) + two(v.b)
}

function luminance(c) {
  var v = rgb(c)
  if (!v) return 0
  function lin(x) { return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4) }
  return 0.2126 * lin(v.r) + 0.7152 * lin(v.g) + 0.0722 * lin(v.b)
}

function contrast(a, b) {
  var x = luminance(a), y = luminance(b)
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

function mix(a, b, t) {
  var x = rgb(a), y = rgb(b)
  return { r: x.r + (y.r - x.r) * t, g: x.g + (y.g - x.g) * t, b: x.b + (y.b - x.b) * t }
}

// The opaque colour seen where `top` (with its alpha, times `alpha` if given)
// is drawn over the opaque `bottom`, e.g. a Util.alpha() tint on the card.
function over(top, bottom, alpha) {
  var t = rgba(top), b = rgb(bottom)
  if (!t || !b) return hex(bottom) || bottom
  return hex(mix(b, t, t.a * (alpha === undefined ? 1 : alpha)))
}

// `color` at each alpha in `alphas`, over each surface in `bottoms`.
function tinted(color, alphas, bottoms) {
  var out = []
  for (var i = 0; i < bottoms.length; i++)
    for (var j = 0; j < alphas.length; j++) out.push(over(color, bottoms[i], alphas[j]))
  return out
}

// The colour itself when it already reaches `ratio` against every surface in
// `backgrounds` (one colour or a list); otherwise the least mix toward black
// (light surfaces) or white (dark ones) that does. Scaling toward black or
// white keeps the hue. Aims a hair above the ratio, so 8-bit compositing of
// tinted surfaces on screen cannot round the result back under it.
var ROUNDING_MARGIN = 0.1
function ensureContrast(color, backgrounds, ratio) {
  var c = rgb(color)
  var list = [].concat(backgrounds).map(rgb).filter(function(b) { return !!b })
  if (!c || !list.length) return hex(color) || color
  var target = (ratio || TEXT_CONTRAST) + ROUNDING_MARGIN
  function reads(x) {
    for (var k = 0; k < list.length; k++) if (contrast(x, list[k]) < target) return false
    return true
  }
  if (reads(c)) return hex(c)
  var light = 0
  for (var k = 0; k < list.length; k++) light += luminance(list[k]) / list.length
  var toward = light > 0.18 ? { r: 0, g: 0, b: 0 } : { r: 1, g: 1, b: 1 }
  var lo = 0, hi = 1
  for (var i = 0; i < 24; i++) {
    var mid = (lo + hi) / 2
    if (reads(mix(c, toward, mid))) hi = mid
    else lo = mid
  }
  // 8-bit rounding can land just under the target; step on until it holds.
  var out = hex(mix(c, toward, hi))
  while (!reads(out) && hi < 1) {
    hi = Math.min(1, hi + 0.004)
    out = hex(mix(c, toward, hi))
  }
  return out
}

// The opaque surfaces semantic colours are drawn on in the panel: the card,
// the host's row cursor (`cursorFill` over the card), selected rows and meter
// tracks (12% foreground on a cursor row) and History's range band (16%).
function neutralSurfaces(surface, cursorFill, foreground) {
  var cursor = over(cursorFill, surface)
  return [hex(surface), cursor, over(foreground, cursor, 0.12), over(foreground, surface, 0.16)]
}

// {ok, warn, critical} readable on every surface in `backgrounds`:
// TEXT_CONTRAST for labels, MARK_CONTRAST for fills, meters and icons.
function tones(palette, backgrounds, ratio) {
  var p = palette || {}
  return {
    ok: ensureContrast(p.ok, backgrounds, ratio),
    warn: ensureContrast(p.warn, backgrounds, ratio),
    critical: ensureContrast(p.critical, backgrounds, ratio)
  }
}

// Left edges for labels centred under their marks (`centers`, any order),
// kept inside [0, extent] and at least `gap` apart: pushed right past their
// left neighbours, then pulled back left from the far edge, so labels near
// the end shift earlier ones left instead of overlapping them. Only when all
// of them cannot fit side by side does the first stay at 0 and overlap.
function packLabels(centers, widths, gap, extent) {
  var n = centers.length, x = [], order = []
  for (var i = 0; i < n; i++) order.push(i)
  order.sort(function(a, b) { return centers[a] - centers[b] || a - b })
  var g = gap || 0
  for (var k = 0; k < n; k++) {
    var j = order[k]
    x[j] = Math.max(0, Math.min(extent - widths[j], centers[j] - widths[j] / 2))
    if (k > 0) x[j] = Math.max(x[j], x[order[k - 1]] + widths[order[k - 1]] + g)
  }
  for (k = n - 1; k >= 0; k--) {
    j = order[k]
    var limit = k === n - 1 ? extent - widths[j] : x[order[k + 1]] - g - widths[j]
    x[j] = Math.max(0, Math.min(x[j], limit))
  }
  return x
}

// --- Shapes in scrolling views ---------------------------------------------
// Qt's software scene graph (offscreen renders, QT_QUICK_BACKEND=software)
// paints a Shape that lies wholly outside its clip with no clip at all: the
// lock of a row scrolled just past a list lands on the controls below it.
// Shapes in a Flickable or ListView therefore hide while wholly outside its
// viewport. Duck-typed (x, y, width, height, parent, contentItem), so a QML
// binding that calls these follows scrolling and layout alike.

// The nearest Flickable/ListView ancestor of `item` (it has a content item
// that scrolls), or null.
function scroller(item) {
  for (var p = item ? item.parent : null; p; p = p.parent)
    if (p.contentItem && p.contentY !== undefined && p.contentX !== undefined) return p
  return null
}

// Whether `item` overlaps the visible part of `view` (true without a view, or
// while `item` is not inside it).
function inViewport(item, view) {
  if (!item || !view || !view.contentItem) return true
  var x = 0, y = 0
  for (var p = item; p !== view.contentItem; p = p.parent) {
    if (!p) return true
    x += p.x
    y += p.y
  }
  var left = -view.contentItem.x, top = -view.contentItem.y
  return x + item.width > left && x < left + view.width && y + item.height > top && y < top + view.height
}

// The bowl gauge's broth surface, in bowl units: a linear position scale from
// the inner base (0) to the rim (1). The % label beside it carries the value.
function brothY(fraction) {
  var f = Math.max(0, Math.min(1, Number(fraction) || 0))
  var b = Icons.BOWL
  return b.baseY - f * (b.baseY - b.rimY)
}

// Steam above the bowl: a shape cue that does not rely on colour.
// Healthy: none; tight: one wisp; critical: two.
function steamCount(level) {
  return level === "critical" ? 2 : level === "warn" ? 1 : 0
}
