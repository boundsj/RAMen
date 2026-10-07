const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "Level.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/^\.pragma library\s*$/m, "");
const L = {};
vm.createContext(L);
vm.runInContext(source, L, { filename: sourcePath });

const GB = 1024 * 1024;
const mem = (usedGb, psi) => ({ total: 8 * GB, used: usedGb * GB, available: (8 - usedGb) * GB, psiSome10: psi || 0 });

assert.equal(L.level(null), "unknown");
assert.equal(L.level(mem(4)), "ok");
assert.equal(L.level(mem(6.1)), "warn");          // 76%
assert.equal(L.level(mem(7.3)), "critical");      // 91%
assert.equal(L.level(mem(3, 6)), "warn", "pressure escalates even at low usage");
assert.equal(L.level(mem(3, 25)), "critical");
assert.equal(L.level(mem(6.1), { warnPercent: 80 }), "ok", "thresholds are configurable");

// Hysteresis: hold a level until usage is clearly below its threshold.
assert.equal(L.level(mem(5.9), {}, "warn"), "warn", "73.75% stays warn after being warn");
assert.equal(L.level(mem(5.9), {}, "ok"), "ok");
assert.equal(L.level(mem(5.7), {}, "warn"), "ok", "71% drops back to ok");
assert.equal(L.level(mem(7.0), {}, "critical"), "critical", "87.5% holds critical");
assert.equal(L.level(mem(6.8), {}, "critical"), "warn", "85% drops to warn");
assert.equal(L.level(mem(7.3), {}, "ok"), "critical", "escalation is immediate");

assert.equal(L.formatKb(null), "–");
assert.equal(L.pressureText(null), "–");
assert.equal(L.formatKb(512), "512K");
assert.equal(L.formatKb(300 * 1024), "300M");
assert.equal(L.formatKb(1.25 * GB), "1.3G");
assert.equal(L.formatKb(1020 * 1024), "1.0G", "no four-digit megabytes");
assert.equal(L.formatKb(12 * GB), "12G");

const theme = L.parseThemeColors('green = "#549e6a"\nyellow = "#459451"\nbright_yellow = "#E5C736"\nred = "#FF5345"\n');
assert.equal(theme.yellow, "#459451");
const p = L.palette(theme, {});
assert.equal(p.ok, "#549e6a");
assert.equal(p.warn, "#E5C736", "a green 'yellow' is skipped for one that is actually yellow");
assert.equal(p.critical, "#FF5345");

const fallback = L.palette({ green: "#888888", yellow: "#0000ff" }, {});
assert.equal(fallback.ok, L.FALLBACK.ok, "grey is not green");
assert.equal(fallback.warn, L.FALLBACK.warn);
assert.equal(L.palette(theme, { warn: "#ffaa00" }).warn, "#ffaa00", "overrides win");

assert.equal(L.animationDuration("on", false), 300);
assert.equal(L.animationDuration("off", false), 0);
assert.equal(L.animationDuration("on", true), 0);
assert.equal(L.animationDuration(undefined, false), 300);

// App rows: unavailable memory is never a fake zero; partial subtotals are lower bounds.
assert.equal(L.footprintKb({ pss: 1000, swap: 24 }), 1024);
assert.equal(L.footprintKb({ pss: 1000, swap: null }), 1000);
assert.equal(L.footprintKb({ pss: null, swap: null }), null);
assert.equal(L.footprintKb({}), null);
assert.equal(L.footprintKb(null), null);
assert.equal(L.memoryNote({ pss: 1, swap: 0, memoryStatus: "available" }), "");
assert.equal(L.memoryNote({ pss: 1, swap: 0 }), "", "legacy rows without a status are complete");
assert.equal(L.memoryNote({ pss: 1, swap: 0, memoryStatus: "partial" }), "memory partial");
assert.equal(L.memoryNote({ pss: null, swap: null, memoryStatus: "unavailable" }), "memory unavailable");
assert.equal(L.footprintText({ pss: 2048, swap: 0, memoryStatus: "partial" }), "≥2M");
assert.equal(L.footprintText({ pss: 2048, swap: 0 }), "2M");
assert.equal(L.footprintText({ pss: null, swap: null, memoryStatus: "unavailable" }), "–");

console.log("level tests passed");
