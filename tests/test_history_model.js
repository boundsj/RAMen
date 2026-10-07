process.env.TZ = "UTC";
const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");
const M = load("HistoryModel.js");

// A real H1 reply: the first 10 s bucket contains a stall, but the next
// bucket's break flag is false. Bucket-center geometry must still leave it blank.
const { execFileSync } = require("node:child_process");
const { resolve } = require("node:path");
const gapQueries = JSON.parse(execFileSync("python3", ["-B", "-c", `
import json
from raman_history import History
h = History()
for t in [100, 102, 109, 111, 113, 115, 117, 119, 121]:
    h.ingest({"used": 500, "total": 1000, "psiSome10": 5}, t, t)
print(json.dumps([h.query(w, 121) for w in ["5m", "1h", "24h"]]))
`], { cwd: resolve(__dirname, ".."), encoding: "utf8" }));
assert.deepEqual(gapQueries[1].t, [100, 110, 120]);
assert.deepEqual(gapQueries[1].gaps, [{ start: 102, end: 109, reason: "stall" }]);
assert.deepEqual(gapQueries[1].breaks, [true, false, false]);
function checkBlankGaps(q) {
  for (const runs of [M.lineRuns(q, "used", 1000), M.bandRuns(q, "used", 1000)]) {
    for (const run of runs) {
      for (const p of run) {
        const t = q.from + p.x * (q.to - q.from);
        assert.ok(!q.gaps.some(g => t > g.start && t < g.end), "no plotted point inside a gap");
      }
      for (let i = 1; i < run.length; i++) {
        const a = q.from + run[i - 1].x * (q.to - q.from);
        const b = q.from + run[i].x * (q.to - q.from);
        assert.ok(!q.gaps.some(g => a < g.end && b > g.start), "no line or envelope segment across a gap");
      }
    }
  }
}
gapQueries.forEach(checkBlankGaps);
assert.equal(M.pointInGap(gapQueries[1], 0), true);
assert.deepEqual(plain(M.lineRuns(gapQueries[1], "used", 1000)).map(r => r.map(p => p.i)), [[1, 2]]);
assert.ok(M.readout(gapQueries[1], 0).after.includes("omitted from the plot"));
// Both centers can be outside the gap even though their connecting segment
// crosses it. Include that case for 10 s and 240 s aggregate geometry.
for (const q of gapQueries.slice(1)) {
  const between = JSON.parse(JSON.stringify(q));
  between.gaps = [{ start: M.pointTime(between, 0) + 0.25, end: M.pointTime(between, 0) + 0.75, reason: "stall" }];
  if (between.t.length < 2) {
    between.to += between.resolution;
    between.t.push(between.t[0] + between.resolution);
    between.breaks.push(false);
    for (const fields of Object.values(between.series))
      for (const values of Object.values(fields)) values.push(values[0]);
  }
  checkBlankGaps(between);
}

const GB = 1024 * 1024;
const T0 = Date.UTC(2026, 9, 3, 9, 20, 0) / 1000; // 2026-10-03 09:20:00 UTC

// A 1h reply with 10 s buckets: five points, a break before index 3, a
// missing bucket between 3 and 4 (t jumps 20 s), and an unreadable PSI at 1.
function reply(extra) {
  const base = {
    type: "history", requestId: "h:p1:1", schemaVersion: 1, demo: false,
    persistence: { owner: true, load: "ok", save: "saved", error: "", lastSavedAt: T0 },
    window: "1h", resolution: 10, from: T0 - 3600 + 50, to: T0 + 50,
    t: [T0, T0 + 10, T0 + 20, T0 + 30, T0 + 50],
    count: [5, 5, 5, 5, 5], covered: [10, 10, 10, 10, 10],
    breaks: [false, false, false, true, false],
    series: {
      psiSome10: { min: [1, null, 2, 0, 0], max: [4, null, 30, 1, 2], mean: [2, null, 12.5, 0.5, 1], last: [3, null, 2, 1, 1] },
      used: { min: [4 * GB, 4 * GB, 5 * GB, 5 * GB, 5 * GB], max: [4.5 * GB, 4.6 * GB, 6 * GB, 5.5 * GB, 5.2 * GB], mean: [4.2 * GB, 4.3 * GB, 5.5 * GB, 5.2 * GB, 5.1 * GB], last: [4.5 * GB, 4.6 * GB, 6 * GB, 5.5 * GB, 5.2 * GB] },
      available: { min: [3 * GB, 3 * GB, 1.5 * GB, 2 * GB, 2.5 * GB], max: [3.5 * GB, 3.5 * GB, 2.5 * GB, 2.5 * GB, 2.8 * GB], mean: [3.2 * GB, 3.2 * GB, 2 * GB, 2.2 * GB, 2.6 * GB], last: [3 * GB, 3 * GB, 2 * GB, 2 * GB, 2.6 * GB] },
      total: { min: Array(5).fill(8 * GB), max: Array(5).fill(8 * GB), mean: Array(5).fill(8 * GB), last: Array(5).fill(8 * GB) },
      swapUsed: { min: Array(5).fill(0), max: Array(5).fill(0), mean: Array(5).fill(0), last: Array(5).fill(0) },
      swapTotal: { min: Array(5).fill(0), max: Array(5).fill(0), mean: Array(5).fill(0), last: Array(5).fill(0) },
      zramRam: { min: Array(5).fill(0), max: Array(5).fill(0), mean: Array(5).fill(0), last: Array(5).fill(0) },
      psiFull10: { min: Array(5).fill(0), max: Array(5).fill(0), mean: Array(5).fill(0), last: Array(5).fill(0) },
    },
    gaps: [{ start: T0 + 28, end: T0 + 30, reason: "suspend" }],
    incidents: [],
  };
  return Object.assign(base, extra || {});
}

// Windows
assert.equal(M.stepWindow("1h", 1), "24h");
assert.equal(M.stepWindow("24h", 1), "24h", "no wraparound");
assert.equal(M.stepWindow("1h", -1), "5m");

// Formatting
assert.equal(M.duration(38), "38 s");
assert.equal(M.duration(250), "4 min 10 s");
assert.equal(M.duration(7500), "2 h 5 min");
assert.equal(M.clock(T0, true), "09:20:00");
assert.equal(M.percent(null), "no reading", "null is never shown as 0");
assert.equal(M.kb(undefined), "no reading");

// Lines: split at the null (index 1), the break (index 3), and the missing bucket (3 -> 4).
const r = reply();
const sc = M.scales(r);
assert.equal(sc.pressure.status, "ok");
assert.equal(sc.pressure.max, 50, "pressure scale fits the 30% peak");
assert.equal(sc.pressure.label, "0–50%");
assert.equal(sc.ram.label, "0–8.0G");
assert.equal(sc.swap.status, "none", "a real zero swap total is 'no swap', not unavailable");
const psiRuns = plain(M.lineRuns(r, "psiSome10", sc.pressure.max));
assert.deepEqual(psiRuns.map(run => run.map(p => p.i)), [[0], [2], [3], [4]],
  "no line across the null at 1, the break at 3, or the missing bucket before 4");
const usedRuns = plain(M.lineRuns(r, "used", sc.ram.max));
assert.deepEqual(usedRuns.map(run => run.map(p => p.i)), [[0, 1, 2], [3], [4]]);
assert.ok(Math.abs(usedRuns[0][2].y - 5.5 / 8) < 1e-9, "the line is the mean");
const band = plain(M.bandRuns(r, "psiSome10", sc.pressure.max));
assert.deepEqual(band.map(run => run.length), [1, 1, 1, 1]);
assert.ok(Math.abs(band[1][0].hi - 30 / 50) < 1e-9 && Math.abs(band[1][0].lo - 2 / 50) < 1e-9, "the band is min..max");
assert.equal(M.bandPolygon([{ x: 0, lo: 0.1, hi: 0.5 }, { x: 1, lo: 0.2, hi: 0.6 }]).length, 4);
// Points sit at bucket centres inside the window.
assert.ok(Math.abs(M.pointX(r, 3) - (T0 + 35 - r.from) / 3600) < 1e-9);
assert.equal(M.pointX(r, 4), 1, "the bucket still filling is pinned to 'now', never drawn in the future");

// Unavailable vs empty scales
const noPsi = reply();
noPsi.series.psiSome10 = { min: Array(5).fill(null), max: Array(5).fill(null), mean: Array(5).fill(null), last: Array(5).fill(null) };
assert.equal(M.scales(noPsi).pressure.status, "unavailable", "missing PSI is unavailable, never a flat 0%");
assert.equal(M.lineRuns(noPsi, "psiSome10", 10).length, 0);
const empty = reply({ t: [], count: [], covered: [], breaks: [], gaps: [] });
empty.series = Object.fromEntries(Object.keys(r.series).map(k => [k, { min: [], max: [], mean: [], last: [] }]));
assert.equal(M.scales(empty).pressure.status, "empty");

// Gaps and blank lead-in
const gaps = plain(M.gapBands(r));
assert.equal(gaps.length, 1);
assert.equal(gaps[0].label, "suspended");
assert.ok(M.leadingBlank(r) > 0.98, "time before the first stored point is blank");

// Page states
assert.equal(M.pageState(null).state, "loading");
assert.equal(M.pageState(null, { error: "No reply from the probe" }).state, "error");
assert.equal(M.pageState(empty).state, "empty");
const partial = M.pageState(r);
assert.equal(partial.state, "partial");
assert.ok(partial.notes.some(n => n.indexOf("Readings cover 50 s of 1 h") === 0), JSON.stringify(partial.notes));
assert.ok(partial.notes.some(n => n === "1 gap: suspended."));
const full = reply({ from: T0, to: T0 + 50, gaps: [] });
full.breaks = [false, false, false, false, false];
full.t = [T0, T0 + 10, T0 + 20, T0 + 30, T0 + 40];
assert.equal(M.pageState(full).state, "ready");
assert.ok(M.pageState(reply({ demo: true })).notes[0].indexOf("Demo data") === 0);
assert.ok(M.pageState(reply({ persistence: { owner: false, load: "none", save: "pending" } })).notes
  .some(n => n.indexOf("this session only") > 0));
assert.ok(M.pageState(reply({ persistence: { owner: true, load: "corrupt", save: "saved" } })).notes
  .some(n => n.indexOf("unusable") > 0));

// Cursor readout: mean with range, nulls named, gaps explained.
let ro = M.readout(r, 2);
assert.equal(ro.time, "09:20:20–09:20:30");
assert.equal(ro.basis, "5 readings in a 10 s bucket");
assert.equal(ro.lines[0].text, "13% avg · 2.0%–30%", "a bucket maximum is shown as a range, not as the average");
ro = M.readout(r, 1);
assert.equal(ro.lines[0].text, "no reading");
assert.equal(ro.lines[0].missing, true);
ro = M.readout(r, 3);
assert.equal(ro.after, "after a gap: suspended for 2 s");
assert.equal(M.readout(r, 4).after, "after a stretch with no readings");
const raw = reply({ window: "5m", resolution: 2, from: T0 - 300, to: T0 + 10, t: [T0, T0 + 2, T0 + 4, T0 + 6, T0 + 8], breaks: [false, false, false, false, false], gaps: [] });
assert.equal(M.readout(raw, 0).lines[0].text, "2.0%", "a raw sample has no range");
assert.equal(M.readout(raw, 0).basis, "one reading");
assert.equal(M.nearestIndex(r, 1), 4);
assert.equal(M.indexAtTime(r, T0 + 25), 2);
assert.equal(M.nearestIndex(empty, 0.5), -1);
assert.equal(M.readout(empty, 0), null);

// Ticks: 15 minute steps on round local times for 1h.
const tk = plain(M.ticks(r));
assert.ok(tk.length >= 4 && tk.every(t => /^\d\d:(00|15|30|45)$/.test(t.label)), JSON.stringify(tk));

// Incidents: receipts from stored fields only; peaks from overlapping stored readings.
const withIncidents = reply({ incidents: [
  { id: "a1", start: T0 + 18, end: T0 + 34, severity: "critical", reason: "pressure", summarySeq: 42,
    measurement: { used: 5.5 * GB, available: 2 * GB, total: 8 * GB, swapUsed: 0, swapTotal: 0, zramRam: 0, psiSome10: 24, psiFull10: 3 },
    context: { status: "observed", at: T0 + 16, apps: [{ name: "Chrome ".repeat(50), kb: 2.8 * GB }, { name: "Slack", kb: null }] } },
  { id: "b2", start: T0 + 2, end: null, severity: "warn", reason: "used", summarySeq: 7,
    measurement: { used: null, available: null, total: null, swapUsed: null, swapTotal: null, zramRam: null, psiSome10: null, psiFull10: null },
    context: { status: "not-observed" } },
] });
const rec = plain(M.receipts(withIncidents));
assert.deepEqual(rec.map(x => x.id), ["a1", "b2"], "newest first");
const a1 = rec[0];
assert.equal(a1.title, "Critical memory pressure");
assert.equal(a1.when, "09:20:18");
assert.equal(a1.duration, "16 s");
assert.equal(a1.threshold, "Threshold not recorded with this incident", "no invented threshold");
assert.equal(a1.atStart.pressure, "24%");
assert.equal(a1.atStart.available, "2.0G");
assert.equal(a1.atStart.used, "69%");
assert.equal(a1.observation, "Apps observed 2 s before the start");
assert.equal(a1.apps[0].size, "2.8G");
assert.equal(a1.apps[1].size, "no reading");
const partialApp = plain(M.receipt(withIncidents, Object.assign({}, withIncidents.incidents[0], { context: { status: "observed",
  at: T0 + 16, apps: [{ name: "Shell", kb: 2048, partial: true }, { name: "Gone", kb: null, partial: true }] } })));
assert.equal(partialApp.apps[0].size, "≥2M", "a partially readable group is a lower bound, never a complete footprint");
assert.equal(partialApp.apps[1].size, "no reading");
assert.equal(a1.peak.available, true);
assert.equal(a1.peak.pressure, "30%", "peak = highest stored bucket maximum overlapping the incident");
assert.equal(a1.peak.lowestAvailable, "1.5G");
assert.ok(a1.peak.note.indexOf("overlapping 10 s buckets") > 0);
assert.ok(a1.peak.note.includes("outside the incident"), "bucket extrema do not claim exact incident-only peaks");
const b2 = rec[1];
assert.equal(b2.ongoing, true);
assert.equal(b2.duration, "ongoing");
assert.equal(b2.observation, "No app observation was stored", "never a fabricated snapshot");
assert.deepEqual(b2.apps, []);
assert.equal(b2.atStart.pressure, "no reading");
assert.equal(b2.atStart.used, "no reading");
const outside = plain(M.receipt(withIncidents, { id: "z", start: T0 - 3000, end: T0 - 2990, severity: "warn", reason: "pressure", measurement: {}, context: {} }));
assert.equal(outside.peak.available, false, "no stored readings: say so instead of guessing");
assert.deepEqual(plain(M.incidentSpans(withIncidents)).map(s => s.id), ["a1", "b2"]);
assert.equal(plain(M.incidentSpans(withIncidents))[1].x1, 1, "an ongoing incident runs to now");
assert.equal(M.incidentAt(withIncidents, 2), "a1");
assert.equal(M.incidentAt(reply(), 2), null);
assert.equal(M.receipt(withIncidents, Object.assign({}, withIncidents.incidents[0], { threshold: "24% ≥ 20%" })).threshold, "24% ≥ 20%",
  "a stored threshold (H3) is shown when present");

// Accessible summary
assert.equal(M.summaryText(null, "loading"), "Loading history");
assert.equal(M.summaryText(r), "Latest pressure 1.0%, RAM used 5.1G, no swap, no incidents recorded");

console.log("history model tests passed");
