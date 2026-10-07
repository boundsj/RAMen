.pragma library

// Pure helpers shared by BarWidget.qml and Panel.qml. Kept free of QML types
// so tests/test_level.js can run them under node.

var FALLBACK = { ok: "#7fbf7f", warn: "#e5c07b", critical: "#e06c6c" }

// An enum works on Omarchy versions without a boolean settings schema or a
// reduced-motion token. A shell's reduced-motion preference takes precedence.
function animationDuration(mode, reduceMotion) {
  return mode === "off" || reduceMotion ? 0 : 300
}

function usedPercent(summary) {
  if (!summary || !summary.total) return 0
  return Math.max(0, Math.min(100, summary.used * 100 / summary.total))
}

var RANK = { unknown: -1, ok: 0, warn: 1, critical: 2 }
var HYSTERESIS_PERCENT = 3

// "ok" | "warn" | "critical" | "unknown". Memory pressure (PSI) is what you
// actually feel as hitching, so it escalates the level even when the used
// percentage still looks fine — and on zram systems used% alone stays high.
// Pass the previous level to get hysteresis: stepping down needs usage to
// fall a few points under the threshold, so the gauge doesn't flicker when
// usage sits on the line.
function level(summary, thresholds, previous) {
  var raw = rawLevel(summary, thresholds, 0)
  if (!previous || RANK[previous] === undefined || RANK[raw] >= RANK[previous]) return raw
  var held = rawLevel(summary, thresholds, HYSTERESIS_PERCENT)
  return RANK[held] >= RANK[previous] ? previous : held
}

function rawLevel(summary, thresholds, slack) {
  if (!summary || !summary.total) return "unknown"
  var t = thresholds || {}
  var warnPct = t.warnPercent !== undefined ? t.warnPercent : 75
  var critPct = t.criticalPercent !== undefined ? t.criticalPercent : 90
  var warnPsi = t.warnPressure !== undefined ? t.warnPressure : 5
  var critPsi = t.criticalPressure !== undefined ? t.criticalPressure : 20
  var pct = usedPercent(summary) + slack
  var psi = (Number(summary.psiSome10) || 0) + slack
  if (pct >= critPct || psi >= critPsi) return "critical"
  if (pct >= warnPct || psi >= warnPsi) return "warn"
  return "ok"
}

function levelLabel(value) {
  if (value === "critical") return "Critical"
  if (value === "warn") return "Tight"
  if (value === "ok") return "Healthy"
  return "Waiting"
}

// kB -> "1.2G" / "640M" / "12M".
function formatKb(kb) {
  if (kb === null || kb === undefined) return "–"
  var value = Number(kb) || 0
  if (value >= 1000 * 1024) { // "1.0G" rather than "1020M"
    var gb = value / (1024 * 1024)
    return (gb >= 10 ? gb.toFixed(0) : gb.toFixed(1)) + "G"
  }
  if (value >= 1024) return Math.round(value / 1024) + "M"
  return Math.round(value) + "K"
}

// An app row's PSS + swap in kB, or null when its memory is unavailable.
// Never turns a missing reading into 0.
function footprintKb(app) {
  if (!app || app.pss === null || app.pss === undefined) return null
  return Number(app.pss) + (Number(app.swap) || 0)
}

// "" for a complete reading; otherwise what the row's numbers really are.
function memoryNote(app) {
  var status = app ? app.memoryStatus : undefined
  if (status === "partial") return "memory partial"
  if (status === "unavailable" || (app && footprintKb(app) === null)) return "memory unavailable"
  return ""
}

// Footprint label: "–" when unavailable, "≥" for a partial (lower-bound) subtotal.
function footprintText(app) {
  var kb = footprintKb(app)
  if (kb === null) return "–"
  return (app.memoryStatus === "partial" ? "≥" : "") + formatKb(kb)
}

function pressureText(psi) {
  if (psi === null || psi === undefined) return "–"
  var value = Number(psi) || 0
  return (value >= 10 ? value.toFixed(0) : value.toFixed(1)) + "%"
}

// Theme colors.toml -> { name: "#rrggbb" }.
function parseThemeColors(text) {
  var result = {}
  var lines = String(text || "").split("\n")
  for (var i = 0; i < lines.length; i++) {
    var match = lines[i].match(/^\s*([A-Za-z0-9_-]+)\s*=\s*["']?(#[0-9A-Fa-f]{6})/)
    if (match) result[match[1]] = match[2]
  }
  return result
}

function hue(hex) {
  var m = String(hex || "").match(/^#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$/)
  if (!m) return -1
  var r = parseInt(m[1], 16) / 255, g = parseInt(m[2], 16) / 255, b = parseInt(m[3], 16) / 255
  var max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
  if (d < 0.08) return -1 // grey: no usable hue
  var h
  if (max === r) h = ((g - b) / d) % 6
  else if (max === g) h = (b - r) / d + 2
  else h = (r - g) / d + 4
  h *= 60
  return h < 0 ? h + 360 : h
}

function inHue(hex, from, to) {
  var h = hue(hex)
  if (h < 0) return false
  return from <= to ? (h >= from && h <= to) : (h >= from || h <= to)
}

function firstInHue(colors, names, from, to, fallback) {
  for (var i = 0; i < names.length; i++) {
    var value = colors[names[i]]
    if (value && inHue(value, from, to)) return value
  }
  return fallback
}

// Green/yellow/red from the active theme. Themes do not always keep these
// names honest (some ship a green "yellow"), so each candidate must actually
// sit in the expected hue band; otherwise a neutral fallback is used.
function palette(colors, overrides) {
  var c = colors || {}
  var o = overrides || {}
  return {
    ok: o.ok || firstInHue(c, ["green", "bright_green", "color2", "color10"], 80, 170, FALLBACK.ok),
    warn: o.warn || firstInHue(c, ["bright_yellow", "yellow", "color11", "color3", "orange"], 30, 65, FALLBACK.warn),
    critical: o.critical || firstInHue(c, ["red", "color1", "bright_red", "color9"], 340, 20, FALLBACK.critical)
  }
}
