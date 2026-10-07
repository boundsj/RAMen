.pragma library
.import "Level.js" as Level
.import "Level.js" as Level

// Pure helpers for HistoryView.qml: turn one `history` reply (docs/protocol.md)
// into what the page draws and says. Kept free of QML types so
// tests/test_history_model.js can check them under node.
//
// Truthfulness rules this file enforces:
// - the line is the time-weighted mean; the band is the bucket min..max;
// - a point whose value is null is not drawn (no reading is not zero);
// - no line joins two points across a continuity break, or across missing
//   time between points; that time stays blank;
// - incident peaks come only from stored readings that overlap the incident,
//   and say so; thresholds that were not stored are reported as such.

var WINDOWS = [
  { id: "5m", label: "5 min", span: 300 },
  { id: "1h", label: "1 hour", span: 3600 },
  { id: "24h", label: "24 hours", span: 86400 }
]

function windowIndex(id) {
  for (var i = 0; i < WINDOWS.length; i++) if (WINDOWS[i].id === id) return i
  return 1
}

function stepWindow(id, step) {
  var i = Math.max(0, Math.min(WINDOWS.length - 1, windowIndex(id) + step))
  return WINDOWS[i].id
}

// --------------------------------------------------------------- formatting

function pad2(n) { return (n < 10 ? "0" : "") + n }

// Local wall-clock label. Seconds only where the window resolves them.
function clock(t, withSeconds) {
  var d = new Date(t * 1000)
  var text = pad2(d.getHours()) + ":" + pad2(d.getMinutes())
  return withSeconds ? text + ":" + pad2(d.getSeconds()) : text
}

function duration(seconds) {
  var s = Math.max(0, Math.round(Number(seconds) || 0))
  if (s < 60) return s + " s"
  var m = Math.floor(s / 60)
  if (m < 60) return s % 60 ? m + " min " + (s % 60) + " s" : m + " min"
  var h = Math.floor(m / 60)
  return m % 60 ? h + " h " + (m % 60) + " min" : h + " h"
}

function percent(value) {
  if (value === null || value === undefined) return "no reading"
  return Level.pressureText(value)
}

function kb(value) {
  if (value === null || value === undefined) return "no reading"
  return Level.formatKb(value)
}

function formatMetric(kind, value) {
  return kind === "pressure" ? percent(value) : kb(value)
}

// --------------------------------------------------------------- reply shape

function pointCount(reply) {
  return reply && reply.t ? reply.t.length : 0
}

function series(reply, metric, field) {
  var s = reply && reply.series ? reply.series[metric] : null
  return s && s[field] ? s[field] : []
}

function value(reply, metric, field, i) {
  var v = series(reply, metric, field)[i]
  return v === undefined ? null : v
}

// Points further apart than this are not joined: the time between them has
// no readings. 5m samples arrive every 2 s, and the probe flags stalls over
// 6 s as breaks anyway; buckets are `resolution` apart.
function maxStep(reply) {
  var r = Number(reply && reply.resolution) || 2
  return r <= 2 ? 6 : r * 1.5
}

function joined(reply, i) {
  if (i <= 0) return false
  if (reply.breaks && reply.breaks[i]) return false
  // A stored break can be inside an aggregate bucket. Neither its center
  // nor a segment through a recorded gap represents observed continuity.
  if (pointInGap(reply, i - 1) || pointInGap(reply, i)) return false
  var a = pointTime(reply, i - 1), b = pointTime(reply, i)
  var gaps = reply.gaps || []
  for (var g = 0; g < gaps.length; g++)
    if (a < gaps[g].end && b > gaps[g].start) return false
  return reply.t[i] - reply.t[i - 1] <= maxStep(reply) + 1e-6
}

// Horizontal position (0..1) of the middle of point i within the window.
function xOf(reply, t) {
  var span = reply.to - reply.from
  return span > 0 ? (t - reply.from) / span : 0
}

function pointTime(reply, i) {
  var half = reply.resolution > 2 ? reply.resolution / 2 : 0
  return Math.max(reply.from, Math.min(reply.to, reply.t[i] + half))
}

function pointX(reply, i) {
  return xOf(reply, pointTime(reply, i))
}

function pointInGap(reply, i) {
  var t = pointTime(reply, i)
  var gaps = reply.gaps || []
  for (var g = 0; g < gaps.length; g++)
    if (t > gaps[g].start && t < gaps[g].end) return true
  return false
}

// --------------------------------------------------------------- scales

function niceCeil(v) {
  if (!(v > 0)) return 1
  var p = Math.pow(10, Math.floor(Math.log(v) / Math.LN10))
  var steps = [1, 2, 2.5, 5, 10]
  for (var i = 0; i < steps.length; i++) if (steps[i] * p >= v - 1e-9) return steps[i] * p
  return 10 * p
}

function maxOf(values) {
  var best = null
  for (var i = 0; i < values.length; i++) {
    var v = values[i]
    if (v !== null && v !== undefined && (best === null || v > best)) best = v
  }
  return best
}

// Each track's own labelled scale. status: "ok" | "unavailable" (every
// reading null) | "none" (a real zero total, e.g. no swap configured) |
// "empty" (no points at all).
function scales(reply) {
  var n = pointCount(reply)
  var result = {}
  var psi = maxOf(series(reply, "psiSome10", "max"))
  if (!n) result.pressure = { status: "empty", max: 10, label: "" }
  else if (psi === null) result.pressure = { status: "unavailable", max: 10, label: "" }
  else {
    var top = Math.min(100, Math.max(10, niceCeil(psi)))
    result.pressure = { status: "ok", max: top, label: "0–" + top + "%" }
  }
  var total = maxOf(series(reply, "total", "max"))
  var used = maxOf(series(reply, "used", "max"))
  if (!n) result.ram = { status: "empty", max: 1, label: "" }
  else if (used === null || total === null) result.ram = { status: "unavailable", max: 1, label: "" }
  else result.ram = { status: "ok", max: Math.max(total, used, 1), label: "0–" + Level.formatKb(Math.max(total, used)) }
  var swapTotal = maxOf(series(reply, "swapTotal", "max"))
  var swapUsed = maxOf(series(reply, "swapUsed", "max"))
  if (!n) result.swap = { status: "empty", max: 1, label: "" }
  else if (swapTotal === null || swapUsed === null) result.swap = { status: "unavailable", max: 1, label: "" }
  else if (swapTotal === 0 && swapUsed === 0) result.swap = { status: "none", max: 1, label: "" }
  else result.swap = { status: "ok", max: Math.max(swapTotal, swapUsed, 1), label: "0–" + Level.formatKb(Math.max(swapTotal, swapUsed)) }
  return result
}

var TRACKS = [
  { key: "pressure", metric: "psiSome10", kind: "pressure", title: "Memory pressure" },
  { key: "ram", metric: "used", kind: "kb", title: "RAM used" },
  { key: "swap", metric: "swapUsed", kind: "kb", title: "Swap used" }
]

// --------------------------------------------------------------- geometry

function clamp01(v) { return Math.max(0, Math.min(1, v)) }

// Line runs for one metric: arrays of {x, y} (both 0..1, y up), split at
// nulls, breaks and missing time. A lone point becomes a one-point run, which
// the view draws as a dot so a single reading is still visible.
function lineRuns(reply, metric, scaleMax) {
  var runs = []
  var current = null
  var mean = series(reply, metric, "mean")
  for (var i = 0; i < pointCount(reply); i++) {
    var v = mean[i]
    if (v === null || v === undefined || pointInGap(reply, i)) { current = null; continue }
    if (!current || !joined(reply, i) || mean[i - 1] === null || mean[i - 1] === undefined) {
      current = []
      runs.push(current)
    }
    current.push({ x: pointX(reply, i), y: clamp01(v / scaleMax), i: i })
  }
  return runs
}

// Min..max bands, split the same way: arrays of {x, lo, hi}.
function bandRuns(reply, metric, scaleMax) {
  var runs = []
  var current = null
  var lo = series(reply, metric, "min")
  var hi = series(reply, metric, "max")
  for (var i = 0; i < pointCount(reply); i++) {
    if (lo[i] === null || lo[i] === undefined || hi[i] === null || hi[i] === undefined || pointInGap(reply, i)) { current = null; continue }
    if (!current || !joined(reply, i) || lo[i - 1] === null || lo[i - 1] === undefined) {
      current = []
      runs.push(current)
    }
    current.push({ x: pointX(reply, i), lo: clamp01(lo[i] / scaleMax), hi: clamp01(hi[i] / scaleMax) })
  }
  return runs
}

// Closed polygon (upper edge forward, lower edge back) for one band run.
function bandPolygon(run) {
  var pts = []
  for (var i = 0; i < run.length; i++) pts.push({ x: run[i].x, y: run[i].hi })
  for (var j = run.length - 1; j >= 0; j--) pts.push({ x: run[j].x, y: run[j].lo })
  return pts
}

// Recorded gaps clipped to the window, with their reason, as 0..1 spans.
function gapBands(reply) {
  var bands = []
  var gaps = reply && reply.gaps ? reply.gaps : []
  for (var i = 0; i < gaps.length; i++) {
    var g = gaps[i]
    var a = clamp01(xOf(reply, g.start)), b = clamp01(xOf(reply, g.end))
    if (b <= a) continue
    bands.push({ x0: a, x1: b, reason: g.reason, start: g.start, end: g.end, label: GAP_LABELS[g.reason] || g.reason })
  }
  return bands
}

var GAP_LABELS = {
  stall: "sampler paused",
  suspend: "suspended",
  clock: "clock changed",
  restart: "RAMen not running",
  reboot: "rebooted"
}

// The part of the window before the first stored point: no readings exist
// yet (history was cleared, or RAMen was not running). 0..1, or 0.
function leadingBlank(reply) {
  if (!pointCount(reply)) return 1
  return clamp01(xOf(reply, reply.t[0]))
}

// Time axis ticks at round local times.
var TICK_STEP = { "5m": 60, "1h": 900, "24h": 21600 }

function ticks(reply) {
  var step = TICK_STEP[reply.window] || 900
  var out = []
  var offset = new Date(reply.from * 1000).getTimezoneOffset() * 60
  var first = Math.ceil((reply.from - offset) / step) * step + offset
  for (var t = first; t <= reply.to + 1e-6; t += step)
    out.push({ x: xOf(reply, t), label: clock(t, false) })
  return out
}

// --------------------------------------------------------------- cursor

function nearestIndex(reply, x) {
  var n = pointCount(reply)
  if (!n) return -1
  var best = 0, bestD = Infinity
  for (var i = 0; i < n; i++) {
    var d = Math.abs(pointX(reply, i) - x)
    if (d < bestD) { best = i; bestD = d }
  }
  return best
}

function indexAtTime(reply, t) {
  var n = pointCount(reply)
  if (!n) return -1
  var best = 0
  for (var i = 0; i < n; i++) if (reply.t[i] <= t) best = i
  return best
}

// What the shared cursor says about point i.
function readout(reply, i) {
  if (i < 0 || i >= pointCount(reply)) return null
  var raw = reply.resolution <= 2
  var lines = []
  for (var k = 0; k < TRACKS.length; k++) {
    var tr = TRACKS[k]
    var mean = value(reply, tr.metric, "mean", i)
    var lo = value(reply, tr.metric, "min", i), hi = value(reply, tr.metric, "max", i)
    var text
    if (mean === null) text = "no reading"
    else if (raw || lo === hi) text = formatMetric(tr.kind, mean)
    else text = formatMetric(tr.kind, mean) + " avg · " + formatMetric(tr.kind, lo) + "–" + formatMetric(tr.kind, hi)
    lines.push({ key: tr.key, title: tr.title, text: text, missing: mean === null })
  }
  var t = reply.t[i]
  var when = raw ? clock(t, true)
    : clock(t, reply.resolution < 60) + "–" + clock(t + reply.resolution, reply.resolution < 60)
  var count = reply.count ? reply.count[i] : 0
  var basis = raw ? "one reading" : (count === 1 ? "1 reading" : count + " readings") + " in a " + duration(reply.resolution) + " bucket"
  var after = ""
  if (pointInGap(reply, i)) after = "bucket center falls in a gap; omitted from the plot"
  else if (i > 0 && !joined(reply, i)) {
    var gap = gapBefore(reply, i)
    after = gap ? "after a gap: " + (GAP_LABELS[gap.reason] || gap.reason) + " for " + duration(gap.end - gap.start)
      : "after a stretch with no readings"
  }
  return { time: when, basis: basis, after: after, lines: lines }
}

function gapBefore(reply, i) {
  var gaps = reply.gaps || []
  var best = null
  for (var g = 0; g < gaps.length; g++) {
    // The gap that ended after point i-1 began and by the end of point i.
    if (gaps[g].end <= reply.t[i] + reply.resolution && gaps[g].end > reply.t[i - 1]) best = gaps[g]
  }
  return best
}

// --------------------------------------------------------------- page state

// "loading" | "error" | "empty" | "partial" | "ready", with plain notes.
function pageState(reply, opts) {
  var o = opts || {}
  if (o.error) return { state: "error", notes: [o.error] }
  if (!reply) return { state: "loading", notes: [] }
  var notes = []
  var p = reply.persistence || {}
  if (reply.demo) notes.push("Demo data on a fake clock; nothing here is saved.")
  else if (reply.enabled === false) notes.push("History collection is off; live memory status continues.")
  else if (p.save === "off") notes.push("Session-only history; persistence is off.")
  else if (p.load === "unreadable") notes.push("Saved history could not be read; showing this session only.")
  else if (p.load === "corrupt" || p.load === "oversized" || p.load === "incompatible")
    notes.push("Saved history was unusable and was set aside; it starts again from now.")
  else if (p.owner === false) notes.push("Another RAMen process saves history; this view shows this session only.")
  if (!reply.demo && p.save === "error") notes.push("Saving history failed" + (p.error ? ": " + p.error : "") + ".")
  if (!pointCount(reply)) return { state: "empty", notes: notes }
  var covered = 0
  for (var i = 0; i < reply.covered.length; i++) covered += reply.covered[i] || 0
  var span = Math.max(1, reply.to - reply.from)
  var share = Math.min(1, covered / span)
  var gaps = gapBands(reply)
  if (share < 0.9) notes.push("Readings cover " + duration(covered) + " of " + duration(span) + ".")
  if (gaps.length) {
    var reasons = {}
    for (var g = 0; g < gaps.length; g++) reasons[gaps[g].label] = true
    notes.push((gaps.length === 1 ? "1 gap" : gaps.length + " gaps") + ": " + Object.keys(reasons).join(", ") + ".")
  }
  return { state: share < 0.9 || gaps.length ? "partial" : "ready", notes: notes, coverage: share }
}

// --------------------------------------------------------------- incidents

var SEVERITY_LABELS = { warn: "Warning", critical: "Critical" }
var REASON_LABELS = { pressure: "memory pressure", used: "memory use" }

// Read-only receipts, newest first, built only from stored fields and the
// stored readings that overlap each incident.
function receipts(reply) {
  var list = (reply && reply.incidents ? reply.incidents : []).slice()
  list.sort(function(a, b) { return b.start - a.start })
  var out = []
  for (var i = 0; i < list.length; i++) out.push(receipt(reply, list[i]))
  return out
}

// A partially readable group's size is only a known lower bound.
function appSize(app) {
  return (app.partial === true && app.kb !== null && app.kb !== undefined ? "≥" : "") + kb(app.kb)
}

function receipt(reply, inc) {
  var end = inc.end === null || inc.end === undefined ? null : inc.end
  var m = inc.measurement || {}
  var peak = peaks(reply, inc.start, end === null ? reply.to : end)
  var ctx = inc.context || { status: "not-observed" }
  var observed = ctx.status === "observed"
  var apps = []
  if (observed) for (var a = 0; a < (ctx.apps || []).length; a++)
    apps.push({ name: String(ctx.apps[a].name || ""), size: appSize(ctx.apps[a]) })
  var age = observed && ctx.at !== null && ctx.at !== undefined ? inc.start - ctx.at : null
  var usedPct = m.used !== null && m.used !== undefined && m.total ? Math.round(m.used * 100 / m.total) + "%" : "no reading"
  return {
    id: String(inc.id),
    start: inc.start,
    end: end,
    ongoing: end === null,
    severity: inc.severity,
    title: (SEVERITY_LABELS[inc.severity] || inc.severity) + " " + (REASON_LABELS[inc.reason] || inc.reason),
    when: clock(inc.start, true),
    duration: end === null ? "ongoing" : duration(end - inc.start) + (inc.status === "interrupted" ? " · interrupted" : ""),
    threshold: inc.measurementStatus === "expired" ? "Start measurements and thresholds expired (24-hour retention)"
      : inc.thresholds ? "RAM warn/critical " + inc.thresholds.warnPercent + "/" + inc.thresholds.criticalPercent
      + "% · PSI warn/critical " + inc.thresholds.warnPressure + "/" + inc.thresholds.criticalPressure + "%"
      : inc.threshold !== undefined && inc.threshold !== null ? String(inc.threshold) : "Threshold not recorded with this incident",
    status: inc.status || (end === null ? "active" : "recovered"),
    measurementStatus: inc.measurementStatus || "stored",
    upgrade: inc.upgrade || null,
    atStart: {
      pressure: percent(m.psiSome10),
      available: kb(m.available),
      used: usedPct
    },
    peak: peak,
    observation: observed
      ? (age === null ? "Apps observed (time not recorded)"
        : "Apps observed " + (age >= 0 ? duration(age) + " before" : duration(-age) + " after") + " the start")
      : ctx.status === "expired" ? "App observation expired (24-hour retention)" : "No app observation was stored",
    apps: apps,
    seq: inc.summarySeq
  }
}

// Peaks from stored readings overlapping [start, end]. These are bucket
// maxima/minima, labelled as such; null when no stored reading overlaps.
function peaks(reply, start, end) {
  var n = pointCount(reply)
  var res = reply ? reply.resolution || 2 : 2
  var psi = null, used = null, availMin = null, points = 0
  for (var i = 0; i < n; i++) {
    var t0 = reply.t[i], t1 = reply.t[i] + (res > 2 ? res : 0)
    if (t1 < start || t0 > end) continue
    points++
    var p = value(reply, "psiSome10", "max", i)
    if (p !== null && (psi === null || p > psi)) psi = p
    var u = value(reply, "used", "max", i)
    if (u !== null && (used === null || u > used)) used = u
    var av = value(reply, "available", "min", i)
    if (av !== null && (availMin === null || av < availMin)) availMin = av
  }
  if (!points) return { available: false, note: "No stored readings in this window cover this incident." }
  return {
    available: true,
    pressure: percent(psi),
    used: kb(used),
    lowestAvailable: kb(availMin),
    note: res > 2
      ? "Extrema of overlapping " + duration(res) + " buckets in this window; a bucket may include readings outside the incident."
      : "Highest and lowest stored readings during the incident in this window."
  }
}

// Incident spans as 0..1 on the time axis (an ongoing one runs to "now").
function incidentSpans(reply) {
  var out = []
  var list = reply && reply.incidents ? reply.incidents : []
  for (var i = 0; i < list.length; i++) {
    var inc = list[i]
    var end = inc.end === null || inc.end === undefined ? reply.to : inc.end
    var a = clamp01(xOf(reply, inc.start)), b = clamp01(xOf(reply, end))
    out.push({ id: String(inc.id), x0: a, x1: Math.max(a, b), severity: inc.severity, ongoing: inc.end === null || inc.end === undefined })
  }
  return out
}

// The incident under the cursor point, if any (newest wins).
function incidentAt(reply, i) {
  if (i < 0 || i >= pointCount(reply)) return null
  var t0 = reply.t[i], t1 = reply.t[i] + Math.max(reply.resolution || 2, 2)
  var list = receipts(reply)
  for (var k = 0; k < list.length; k++) {
    var end = list[k].end === null ? reply.to : list[k].end
    if (list[k].start < t1 && end >= t0) return list[k].id
  }
  return null
}

// One-line accessible summary of the page for screen readers and tooltips.
function summaryText(reply, state) {
  if (!reply) return state === "error" ? "History unavailable" : "Loading history"
  var sc = scales(reply)
  var parts = []
  var last = pointCount(reply) - 1
  if (last >= 0) {
    parts.push("Latest pressure " + percent(value(reply, "psiSome10", "mean", last)))
    parts.push("RAM used " + kb(value(reply, "used", "mean", last)))
    parts.push(sc.swap.status === "none" ? "no swap" : "swap used " + kb(value(reply, "swapUsed", "mean", last)))
  }
  var n = reply.incidents ? reply.incidents.length : 0
  parts.push(n === 0 ? "no incidents recorded" : n === 1 ? "1 incident" : n + " incidents")
  return parts.join(", ")
}

// Live producer. The probe supplies monotonic clocks, source nulls and breaks;
// chart replies never enter this path. A caller replaces state after each step.
function incidentSettings(settings) {
  var s = settings || {}
  function bounded(value, fallback, lo, hi) {
    var n = Number(value)
    return value === undefined || value === null || !isFinite(n) ? fallback : Math.max(lo, Math.min(hi, Math.round(n)))
  }
  return {
    enabled: s.historyEnabled !== false && s.historyEnabled !== "off",
    persist: s.historyPersist !== false && s.historyPersist !== "off",
    mode: ["off", "critical", "warning-and-critical"].indexOf(s.alertMode) >= 0 ? s.alertMode : "off",
    hold: bounded(s.alertHoldSeconds, 10, 2, 60),
    cooldown: bounded(s.alertCooldownSeconds, 300, 60, 3600)
  }
}

function incidentState(blocked, lastNotice) {
  return { phase: "healthy", previous: "unknown", pending: null, active: null,
    usedLevel: "unknown", pressureLevel: "unknown",
    criticalSince: null, last: null, blocked: !!blocked,
    blockedUsed: !!blocked, blockedPressure: !!blocked,
    lastNotice: lastNotice || { warn: null, critical: null } }
}

function incidentStep(state, sample, thresholds, options) {
  var s = JSON.parse(JSON.stringify(state || incidentState(false)))
  var events = [], o = incidentSettings(options)
  var mono = sample && sample.monotonic
  var valid = sample && typeof mono === "number" && isFinite(mono)
    && typeof sample.total === "number" && sample.total > 0
    && typeof sample.used === "number" && sample.used >= 0
  var broken = !valid || sample.breakBefore || (s.last &&
    (mono < s.last.mono || mono - s.last.mono > 6 || sample.time < s.last.wall
     || Math.abs((sample.time - s.last.wall) - (mono - s.last.mono)) > 2
     || sample.session !== s.last.session))
  function finish(status) {
    var hadActive = !!s.active
    if (status === "interrupt" && s.active) {
      s.blocked = true; s.blockedUsed = true; s.blockedPressure = true
    }
    if (s.active) events.push({ action: status, key: s.active.key, summarySeq: status === "interrupt" && s.last ? s.last.seq : sample.seq,
      severity: s.active.severity, reason: s.active.reason, notify: false })
    s.active = null; s.pending = null; s.criticalSince = null
    s.phase = status === "recover" && hadActive ? "recovered" : "healthy"
  }
  if (!o.enabled || sample.demo || !sample.dispatchOwner) {
    // Configuration/demo/ownership transitions are also handled by runtime reset.
    return { state: incidentState(s.blocked, s.lastNotice), events: [] }
  }
  // Suppress each launch-time dimension until that dimension recovers.
  // Low RAM can re-arm RAM without claiming that unreadable PSI recovered.
  if (s.blocked && valid && !broken) {
    if (Level.usedPercent(sample) < ((thresholds || {}).warnPercent === undefined ? 75 : thresholds.warnPercent) - Level.HYSTERESIS_PERCENT)
      s.blockedUsed = false
    if (typeof sample.psiSome10 === "number" &&
        sample.psiSome10 < ((thresholds || {}).warnPressure === undefined ? 5 : thresholds.warnPressure) - Level.HYSTERESIS_PERCENT)
      s.blockedPressure = false
    s.blocked = s.blockedUsed || s.blockedPressure
  }
  var evidence = Object.assign({}, sample)
  if (s.blockedUsed) evidence.used = 0
  if (s.blockedPressure) evidence.psiSome10 = null
  var raw = valid ? Level.rawLevel(evidence, thresholds, 0) : "unknown"
  // Hysteresis belongs to each measured dimension. Unknown PSI cannot erase
  // held RAM evidence, nor may a prior PSI warning create held RAM evidence.
  var usedLevel = valid ? Level.level({ total: evidence.total, used: evidence.used, psiSome10: 0 }, thresholds, s.usedLevel) : "unknown"
  var pressureKnown = typeof evidence.psiSome10 === "number" && isFinite(evidence.psiSome10)
  var pressureLevel = valid && pressureKnown ? Level.level({ total: evidence.total, used: 0, psiSome10: evidence.psiSome10 }, thresholds, s.pressureLevel) : "unknown"
  if (valid && !pressureKnown && Level.RANK[usedLevel] < Level.RANK.warn)
    raw = "unknown"
  if (broken || raw === "unknown") {
    finish("interrupt")
    s.previous = "unknown"
    s.usedLevel = "unknown"; s.pressureLevel = "unknown"
    s.last = valid ? { mono: mono, wall: sample.time, session: sample.session, seq: sample.seq } : null
    return { state: s, events: events }
  }
  s.usedLevel = usedLevel; s.pressureLevel = pressureLevel
  var level = Level.RANK[pressureLevel] > Level.RANK[usedLevel] ? pressureLevel : usedLevel
  s.previous = level
  s.last = { mono: mono, wall: sample.time, session: sample.session, seq: sample.seq }
  if (level === "ok") {
    finish("recover")
    return { state: s, events: events }
  }
  function notice(severity, upgrade) {
    var allowed = o.mode === "warning-and-critical" || (o.mode === "critical" && severity === "critical")
    var last = s.lastNotice[severity]
    if (allowed && (last === null || mono - last >= o.cooldown)) {
      s.lastNotice[severity] = mono
      return true
    }
    return false
  }
  function reason(severity) {
    var t = thresholds || {}
    var limit = severity === "critical" ? (t.criticalPressure === undefined ? 20 : t.criticalPressure)
      : (t.warnPressure === undefined ? 5 : t.warnPressure)
    // Only retain PSI in its hysteresis band if it already qualified this
    // dwell; healthy PSI in the band must not steal a RAM-driven explanation.
    return (severity === "warn" && Level.RANK[pressureLevel] >= Level.RANK.warn)
      || (pressureKnown && evidence.psiSome10 >= limit) ? "pressure" : "used"
  }
  if (!s.active) {
    if (!s.pending) s.pending = { since: mono, seq: sample.seq, severity: raw,
      reason: reason("warn"), criticalSince: raw === "critical" ? mono : null }
    s.pending.reason = reason("warn")
    if (raw === "critical") {
      if (s.pending.criticalSince === null) s.pending.criticalSince = mono
    } else s.pending.criticalSince = null
    s.phase = "pending"
    if (mono - s.pending.since >= o.hold) {
      var severity = s.pending.criticalSince !== null && mono - s.pending.criticalSince >= o.hold ? "critical" : "warn"
      var key = String(sample.session) + ":" + String(s.pending.seq)
      s.active = { key: key, severity: severity, reason: reason(severity) }
      s.phase = "active"
      s.criticalSince = s.pending.criticalSince
      events.push({ action: "start", key: key, summarySeq: sample.seq, startSeq: s.pending.seq,
        severity: severity, reason: s.active.reason, notify: notice(severity, false), hold: o.hold })
      s.pending = null
    }
  } else {
    s.phase = "active"
    if (raw === "critical") {
      if (s.criticalSince === null) s.criticalSince = mono
    } else s.criticalSince = null
    var action = "extend", shouldNotify = false
    if (s.active.severity === "warn" && s.criticalSince !== null && mono - s.criticalSince >= o.hold) {
      s.active.severity = "critical"; s.active.reason = reason("critical")
      action = "upgrade"; shouldNotify = notice("critical", true)
    }
    events.push({ action: action, key: s.active.key, summarySeq: sample.seq,
      severity: s.active.severity, reason: s.active.reason, notify: shouldNotify, hold: o.hold })
  }
  return { state: s, events: events }
}

// The host renders notification bodies as StyledText. Escape display data once
// at this boundary, independently of discrete argv's shell safety.
function notificationText(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;")
}

function incidentToast(ack, hold, delivery) {
  var inc = ack.incident, m = ack.measurement || (inc.upgrade ? inc.upgrade.measurement : inc.measurement) || {}, body = inc.reason === "pressure"
    ? "Memory pressure " + percent(m.psiSome10) : "RAM used " + (m.total ? Math.round(m.used * 100 / m.total) + "%" : "unavailable")
  body += " for " + hold + " s · " + kb(m.available) + " available"
  // Receipt context remains historical. Each acknowledgement validates current
  // subscription/freshness separately; an absent field is never a fresh scan.
  var ctx = ack.notificationContext || {}
  if (delivery) {
    var age = delivery.now / 1000 - ctx.at
    if (!delivery.detail || typeof ctx.at !== "number" || !isFinite(age) || age < 0 || age > 5)
      ctx = {}
  }
  if (ctx.status === "observed" && ctx.apps && ctx.apps.length)
    body += " · Observed: " + ctx.apps.map(function(a) { return notificationText(a.name) + " " + appSize(a) }).join(", ")
  if (!ack.persisted) body += " · not saved"
  return { title: "RAMen · " + (inc.severity === "critical" ? "Critical" : "Tight"), body: body }
}
