const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");
const A = load("AppsModel.js");

function row(id, fields) {
  return Object.assign({ id: id, name: id.toUpperCase(), kind: "app", host: "", count: 2, protected: false,
    generation: "g-" + id, pss: 100000, swap: 0, memoryStatus: "available",
    memoryCoverage: { measured: 2, members: 2 }, cpuCorePercent: 50, cpuMachinePercent: 6.25,
    cpuStatus: "available", cpuCoverage: { measured: 2, members: 2 } }, fields || {});
}

// Send one request and return the state waiting for it, plus its params.
function send(state, changes, user) {
  const r = A.request(state, changes, user);
  const id = "a:t:" + r.state.generation;
  return { state: A.pending(r.state, id, 1000), params: plain(r.params), id: id };
}

function page(sent, rows, extra) {
  return Object.assign({ type: "apps-page", requestId: sent.id, generation: sent.state.generation, status: "ready",
    snapshot: 7, sampledAt: 10, stale: false, demo: false, offset: 0, total: rows.length, nextOffset: null,
    inventory: { complete: true, processLimit: 8192, groupLimit: 4096 },
    cpu: { status: "available", onlineCpus: 8 }, coverage: {}, rows: rows }, extra || {});
}

function ids(state) { return state.rows.map((r) => r.id); }

// --- default sort setting: GPU memory falls back visibly --------------------
assert.deepEqual(plain(A.defaultSort(undefined)), { sort: "memory", note: "", want: "" });
assert.deepEqual(plain(A.defaultSort("cpu")), { sort: "cpu", note: "", want: "" });
assert.equal(A.defaultSort("gpu-memory").sort, "memory");
assert.equal(A.defaultSort("gpu-memory").want, "gpu-memory", "switch once the lens is known to work");
assert.match(A.defaultSort("gpu-memory").note, /GPU memory .*not available.*Memory/);
assert.match(A.defaultSort("gpu-memory", "off").note, /off in settings/);
assert.equal(A.defaultSort("gpu-memory", "off").want, "", "gpuMetrics off: never switches");
assert.equal(A.defaultSort("bogus").sort, "memory");
assert.equal(A.initialState("gpu-memory").sortNote, A.defaultSort("gpu-memory").note, "the fallback is shown");
assert.equal(A.sortAvailable("gpu-memory"), false, "no GPU readings: no GPU lens");
assert.equal(A.sortAvailable("gpu-memory", A.initialState("memory")), false);
assert.equal(A.stepSort("memory", 1), "cpu");
assert.equal(A.stepSort("cpu", 1), "cpu", "the unavailable GPU lens is skipped");
assert.equal(A.stepSort("cpu", -1), "memory");
let g = send(A.initialState("gpu-memory"), { sort: "gpu-memory" }, true);
assert.equal(g.params.sort, "memory", "an unavailable sort is never requested");
g = send(A.initialState("gpu-memory"), { sort: "cpu" }, true);
assert.equal(g.state.sortNote, "", "choosing a lens retires the fallback note");
assert.equal(g.state.wantSort, "", "and the pending default lens");

// --- requests and stale replies --------------------------------------------
let s = A.initialState("memory");
let first = send(s, {}, false);
assert.deepEqual(first.params, { query: "", sort: "memory", offset: 0 });
let second = send(first.state, { query: "chrome", offset: 0 }, true);
assert.equal(second.params.query, "chrome");
let r = A.accept(second.state, page(first, [row("old")]));
assert.equal(r.accepted, false, "an older query's reply never overwrites a newer one");
r = A.accept(second.state, Object.assign(page(second, [row("chrome")]), { generation: 1 }));
assert.equal(r.accepted, false, "the generation must match too");
r = A.accept(second.state, page(second, [row("chrome")]));
assert.equal(r.accepted, true);
s = r.state;
assert.deepEqual(ids(s), ["chrome"]);
assert.equal(s.pendingId, "");
assert.equal(A.accept(s, page(second, [row("x")])).accepted, false, "each reply is accepted once");
assert.equal(send(s, { query: "q".repeat(300) }, true).params.query.length, 256);

// Lost requests (probe restart) do not block live refreshes forever.
let waiting = send(A.initialState("memory"), {}, false).state;
assert.equal(A.canRefresh(waiting, 2000), false);
assert.equal(A.canRefresh(waiting, 1000 + A.PENDING_TIMEOUT_MS + 1), true);

// --- stable identity: selection is an ID, not an index ----------------------
let live = send(A.initialState("cpu"), {}, false);
s = A.accept(live.state, page(live, [row("a"), row("b"), row("c")])).state;
s = A.move(s, 1);
assert.equal(s.selectedId, "a", "j from no selection picks the first row");
s = A.move(s, 1);
assert.equal(s.selectedId, "b");
live = send(s, {}, false);
assert.equal(live.params.focus, "b", "queries report where the selected group is");
s = A.accept(live.state, page(live, [row("b"), row("c"), row("a")])).state;
assert.equal(s.selectedId, "b", "a reorder keeps the same app selected");
assert.equal(A.selectedIndex(s), 0, "at its new position");
live = send(s, {}, false);
s = A.accept(live.state, page(live, [row("c"), row("a")])).state;
assert.equal(s.selectedId, "b", "a group off this page stays the selection");
assert.equal(A.selectedIndex(s), -1);
assert.equal(A.selectedRow(s), null, "but nothing on the page can be acted on in its place");

// --- hovering freezes the order; leaving applies the queued update --------
live = send(A.initialState("memory"), {}, false);
s = A.accept(live.state, page(live, [row("a"), row("b")])).state;
s = A.select(A.setHovering(s, true), "b");
live = send(s, {}, false);
r = A.accept(live.state, page(live, [row("b"), row("a"), row("new")]));
s = r.state;
assert.deepEqual(ids(s), ["a", "b"], "rows do not move under the pointer");
assert.ok(s.queued, "the update waits");
assert.equal(A.selectedIndex(s), 1);
s = A.setHovering(s, false);
assert.deepEqual(ids(s), ["b", "a", "new"], "leaving the list applies the newest queued order");
assert.equal(s.queued, null);
assert.equal(s.selectedId, "b");

// User changes apply at once, even under the pointer, and disarm first.
s = A.setHovering(s, true);
s = A.armOrConfirm(s, s.rows[0], false, false).state;
assert.equal(s.armed.id, "b");
let user = send(s, { query: "a" }, true);
assert.equal(user.state.armed, null, "a search change disarms");
s = A.accept(user.state, page(user, [row("a")])).state;
assert.deepEqual(ids(s), ["a"], "the user's own query is shown while hovering");
for (const change of [{ sort: "cpu" }, { offset: 50, pin: 7 }]) {
  let armed = A.armOrConfirm(A.setHovering(s, false), s.rows[0], false, false).state;
  assert.ok(armed.armed);
  assert.equal(send(armed, change, true).state.armed, null, JSON.stringify(change) + " disarms");
}

// --- arming: membership-aware, frozen, disarmed by change or disappearance ---
function armedState() {
  let l = send(A.initialState("memory"), {}, false);
  let st = A.accept(l.state, page(l, [row("a"), row("b"), row("p", { protected: true })])).state;
  return A.armOrConfirm(st, st.rows[1], false, false).state;
}
s = armedState();
assert.deepEqual(plain(s.armed), { id: "b", generation: "g-b", force: false, name: "B" });
assert.equal(A.armOrConfirm(s, s.rows[2], false, false).state.armed.id, "b", "protected rows never arm");
assert.equal(A.armOrConfirm(s, row("x", { generation: null }), false, false).kill, null, "no membership, no kill");
live = send(s, {}, false);
assert.equal(live.params.focus, "b", "while armed, the armed group is tracked");
r = A.accept(live.state, page(live, [row("b"), row("a")], { focus: { id: "b", present: true, generation: "g-b", rank: 0 } }));
assert.equal(r.state.armed.id, "b", "same membership stays armed");
assert.deepEqual(ids(r.state), ["a", "b", "p"], "and the armed row does not move");
let confirm = A.armOrConfirm(r.state, r.state.rows[1], false, false);
assert.deepEqual(plain(confirm.kill), { id: "b", membership: "g-b", force: false }, "confirm uses the armed membership");
assert.equal(confirm.state.armed, null);
assert.deepEqual(ids(confirm.state), ["b", "a"], "disarming applies the queued order");

// Membership changed between arm and confirm: the queued update disarms.
s = armedState();
live = send(s, {}, false);
r = A.accept(live.state, page(live, [row("b", { generation: "g-b2" }), row("a")],
  { focus: { id: "b", present: true, generation: "g-b2", rank: 0 } }));
assert.equal(r.state.armed, null, "a changed group is disarmed, never retargeted");
assert.match(r.state.notice, /changed.*disarmed/);
assert.deepEqual(ids(r.state), ["b", "a"]);
assert.equal(A.armOrConfirm(r.state, r.state.rows[0], false, false).kill, null, "the next press only re-arms");

// The armed group vanished (or moved off-page: focus still finds it).
s = armedState();
live = send(s, {}, false);
r = A.accept(live.state, page(live, [row("a")], { focus: { id: "b", present: false, generation: null, rank: null } }));
assert.equal(r.state.armed, null);
assert.match(r.state.notice, /no longer running/);
s = armedState();
live = send(s, {}, false);
r = A.accept(live.state, page(live, [row("a")], { focus: { id: "b", present: true, generation: "g-b", rank: 60 } }));
assert.equal(r.state.armed.id, "b", "a group ranked onto another page is not mistaken for gone");
s = armedState();
live = send(s, {}, false);
r = A.accept(live.state, Object.assign(page(live, []), { status: "inactive" }));
assert.equal(r.state.armed, null, "no scan to verify against: disarm");

// Moving the selection or arming another row disarms; force re-arms as force.
s = armedState();
assert.equal(A.move(s, -1).armed, null);
assert.equal(A.select(s, "b").armed.id, "b", "re-selecting the armed row keeps it armed");
r = A.armOrConfirm(s, s.rows[1], true, false);
assert.equal(r.kill, null, "f on a TERM-armed row re-arms for SIGKILL first");
assert.equal(r.state.armed.force, true);
r = A.armOrConfirm(r.state, r.state.rows[1], false, false);
assert.deepEqual(plain(r.kill), { id: "b", membership: "g-b", force: true }, "x confirms the armed force kill");
r = A.armOrConfirm(A.disarm(s, "").state, s.rows[1], false, true);
assert.equal(r.state.armed.force, true, "a stubborn group escalates to SIGKILL");

// Kill bookkeeping states, same timings as the Memory page.
assert.equal(A.killState({}, "a", 0), "");
assert.equal(A.killState({ a: { signal: "TERM", at: 0, error: "" } }, "a", 3999), "stopping");
assert.equal(A.killState({ a: { signal: "TERM", at: 0, error: "" } }, "a", 4000), "stubborn");
assert.equal(A.killState({ a: { signal: "KILL", at: 0, error: "" } }, "a", 9000), "stopping");
assert.equal(A.killState({ a: { signal: "TERM", at: 0, error: "membership changed since armed" } }, "a", 1), "error");
assert.equal(A.ARM_MS, 4000);

// --- paging pins the shown snapshot; an expired pin restarts from the top ---
live = send(A.initialState("memory"), {}, false);
s = A.accept(live.state, page(live, [row("a")], { total: 120, nextOffset: 50 })).state;
assert.equal(A.pageChange(s, -1), null);
assert.deepEqual(plain(A.pageChange(s, 1)), { offset: 50, pin: 7 });
let next = send(s, A.pageChange(s, 1), true);
assert.equal(next.params.snapshot, 7);
assert.equal(next.params.offset, 50);
r = A.accept(next.state, { type: "error", requestId: next.id, generation: next.state.generation, code: "snapshot-expired" });
assert.equal(r.restart, true);
assert.equal(r.state.offset, 0);
assert.match(r.state.notice, /expired/);
r = A.accept(next.state, { type: "error", requestId: next.id, generation: next.state.generation, code: "bad-page", message: "offset" });
assert.equal(r.state.error, "offset");
live = send(s, {}, false);
assert.equal(live.params.snapshot, undefined, "live refreshes read the newest snapshot");

// --- labels: units, denominators and truthful missing values ---------------
assert.equal(A.cpuText(row("a", { cpuMachinePercent: 12.5 })), "13%");
assert.equal(A.cpuText(row("a", { cpuMachinePercent: 0 })), "0.0%", "a measured idle app is zero");
assert.equal(A.cpuText(row("a", { cpuMachinePercent: 0.04 })), "<0.1%");
assert.equal(A.cpuText(row("a", { cpuMachinePercent: null, cpuStatus: "warming-up" })), "warming up");
assert.equal(A.cpuText(row("a", { cpuMachinePercent: null, cpuStatus: "unavailable" })), "–");
assert.equal(A.cpuText(row("a", { cpuMachinePercent: 3.2, cpuStatus: "partial" })), "≥3.2%");
assert.equal(A.cpuCoreText(row("a", { cpuCorePercent: 175 })), "175% of one CPU");
assert.equal(A.cpuShort(row("a", { cpuMachinePercent: null, cpuStatus: "warming-up" })), "–", "the row stays compact");
assert.equal(A.subtitle(row("a", { cpuMachinePercent: null, cpuStatus: "warming-up" })), "2 processes · CPU warming up",
  "and says why its CPU is missing");
assert.equal(A.subtitle(row("a", { cpuMachinePercent: null, cpuStatus: "unavailable" })), "2 processes · CPU unavailable");
assert.equal(A.cpuFraction(row("a", { cpuMachinePercent: null })), null, "no reading is not an empty rail");
assert.equal(A.cpuFraction(row("a", { cpuMachinePercent: 0 })), 0);
assert.equal(A.cpuFraction(row("a", { cpuMachinePercent: 250 })), 1);
assert.equal(A.ramFraction(row("a", { pss: 1000, swap: 9000 }), 10000), 0.1, "the RAM rail is resident PSS only");
assert.equal(A.ramFraction(row("a", { pss: null, swap: null }), 10000), null);
assert.equal(A.ramFraction(row("a"), 0), null, "no physical total, no rail");
assert.equal(A.ramText(row("a", { pss: 500, memoryStatus: "partial" }), 10000), "≥5.0%");
assert.equal(A.subtitle(row("s", { kind: "session", host: "Ghostty", count: 1 })), "1 process · in Ghostty");
assert.equal(A.subtitle(row("p", { pss: null, memoryStatus: "unavailable", protected: true, cpuStatus: "partial" })),
  "2 processes · memory unavailable · CPU partial · protected");
assert.match(A.legend({ cpu: { onlineCpus: 8 } }, 16 * 1024 * 1024), /PSS \+ swapped.*resident PSS of 16G.*% of 8 logical CPUs/);
assert.match(A.accessibleRow(row("a"), 1000000), /size 98M PSS plus swap, resident RAM 10% of physical memory, CPU 6.3% of all CPUs/);
assert.match(A.footprintBreakdown(row("a", { pss: 2048, swap: 1024 })), /3M = resident PSS 2M \+ swapped 1M/);
assert.match(A.footprintBreakdown(row("a", { memoryStatus: "partial", memoryCoverage: { measured: 2, members: 3 } })), /lower bound: 2 of 3 measured/);
assert.match(A.cpuBreakdown(row("a", { cpuCorePercent: 175, cpuMachinePercent: 21.875 }), { cpu: { onlineCpus: 8 } }),
  /CPU 22% of 8 CPUs = 175% of one CPU/);

function notes(meta, status) { return A.statusNotes(Object.assign(A.initialState("memory"), { meta: meta, status: status || "ready" })); }
assert.deepEqual(plain(notes(null, "pending")), ["Scanning processes…"]);
assert.match(notes({ inventory: { complete: false, incompleteReason: "process-limit", processLimit: 8192 } }).join(), /Incomplete: more than 8192 processes/);
assert.match(notes({ inventory: { complete: false, incompleteReason: "group-limit", groupLimit: 4096 } }).join(), /4096 groups/);
assert.match(notes({ cpu: { status: "warming-up" } }).join(), /CPU warming up/);
assert.match(notes({ demo: true, cpu: { status: "available" } }).join(), /Demo data/);
assert.match(notes({ stale: true }).join(), /older scan/);
assert.equal(A.rangeText(Object.assign(A.initialState(), { status: "ready", total: 0, query: "zz" })), "No matching apps");
assert.equal(A.rangeText(Object.assign(A.initialState(), { status: "ready", total: 120, offset: 50, rows: new Array(50) })), "51–100 of 120");

// --- details: on demand, only the newest request -----------------------------
let d = A.detailsRequest(0, "a");
d.pendingId = "d:t:1";
assert.equal(A.acceptDetails(d, { type: "apps-details", requestId: "d:t:0", generation: 1, id: "a" }), null);
assert.equal(A.acceptDetails(d, { type: "apps-details", requestId: "d:t:1", generation: 0, id: "a" }), null);
assert.equal(A.acceptDetails(d, { type: "apps-details", requestId: "d:t:1", generation: 1, id: "b", members: [] }), null,
  "a reply about another group is never shown");
assert.equal(A.acceptDetails(d, { type: "apps-details", requestId: "d:t:1", generation: 1, members: [] }), null);
let got = A.acceptDetails(d, { type: "apps-details", requestId: "d:t:1", generation: 1, id: "a", members: [] });
assert.equal(got.data.requestId, "d:t:1");
assert.equal(A.acceptDetails(d, { type: "error", requestId: "d:t:1", generation: 1, id: "a", code: "gone", message: "that app is no longer running" }).error,
  "that app is no longer running");
assert.equal(A.acceptDetails(d, { type: "error", requestId: "d:t:1", generation: 1, id: "b", code: "gone", message: "x" }), null);
assert.equal(A.acceptDetails(d, { type: "error", requestId: "d:t:1", generation: 1, code: "bad-request", message: "bad id" }).error,
  "bad id", "validation errors carry no id and match by request ID");
assert.equal(A.detailsRequest(got.generation, "b").generation, 2);

// Close while A is pending, then open B (or A again): the late A reply and
// its errors never land under the new request, because generations keep
// counting across close/reopen (the request ID would otherwise repeat).
{
  const Runtime = load("Runtime.js");
  let last = 0;
  const open = (id) => {
    const next = A.detailsRequest(last, id);
    last = next.generation;
    next.pendingId = Runtime.appsDetails("apps-1", next.generation, id).requestId;
    return next;
  };
  const oldA = open("a");
  const nowB = open("b"); // the view dropped A on close; B is a fresh request
  assert.notEqual(nowB.pendingId, oldA.pendingId);
  const lateA = { type: "apps-details", requestId: oldA.pendingId, generation: oldA.generation, id: "a",
    row: { id: "a" }, members: [{ command: "old private command" }] };
  assert.equal(A.acceptDetails(nowB, lateA), null, "late A reply is rejected under B");
  assert.equal(A.acceptDetails(nowB, { type: "error", requestId: oldA.pendingId, generation: oldA.generation, id: "a",
    code: "gone", message: "gone" }), null, "late A error is rejected under B");
  const againA = open("a");
  assert.equal(A.acceptDetails(againA, lateA), null, "late A reply is rejected under a reopened A");
  assert.equal(A.acceptDetails(againA, Object.assign({}, lateA, { requestId: againA.pendingId, generation: againA.generation })).data.id, "a");
}
assert.equal(A.memberText({ pid: 7, name: "sh", pss: 1024, swap: 0, cpuMachinePercent: 12.5 }), "7 · sh · 1M · CPU 13%");
assert.equal(A.memberText({ pid: 7, name: "sh", pss: null, cpuMachinePercent: null, cpuStatus: "warming-up" }), "7 · sh · – · CPU warming up");
assert.equal(A.commandText({ command: "sh -c x", commandTruncated: true }), "sh -c x …");
assert.equal(A.commandText({ command: null, commandStatus: "exited" }), "(exited)");
assert.equal(A.commandText({ command: null, commandStatus: "unavailable" }), "(command line unavailable)");

// --- A3: the GPU memory lens -------------------------------------------------
const AMD = "pci:0000:03:00.0";
function gpuMeta(fields) {
  return Object.assign({ status: "available", memoryLens: true, devices: [{ id: AMD, status: "available",
    totals: { deviceVramUsedKb: 9 * 1048576, deviceVramTotalKb: 16 * 1048576, unattributedVramKb: 1048576,
      unattributedLowerBound: true, crossGroupClients: 1 } }] }, fields || {});
}
function vramEntry(fields) {
  return Object.assign({ deviceId: AMD, driver: "amdgpu", status: "available", vramKb: 2097152, vramAllocatedKb: 2359296,
    vramSharedKb: 0, vramOverlap: "none", vramStatus: "available", systemKb: 65536, systemSharedKb: 0, systemOverlap: "none",
    systemStatus: "available", clients: 1, sharedClients: 0, unidentified: 0,
    unread: 0, engines: { gfx: { percent: 12.5, status: "available", basis: "busy-time", capacity: 1 },
      dma: { percent: null, status: "warming-up", basis: "busy-time", capacity: 1 } } }, fields || {});
}
function gpuRow(id, fields) {
  return row(id, Object.assign({ gpu: [vramEntry()], gpuMemoryKb: 2097152, gpuStatus: "available",
    gpuCoverage: { measured: 2, members: 2 } }, fields || {}));
}

// Lens availability follows the newest applied page, and never for gpuMetrics off.
let gs = A.initialState("gpu-memory");
let gq = send(gs, {}, false);
let gr = A.accept(gq.state, page(gq, [row("a")], { gpu: { status: "warming-up", memoryLens: false, devices: [] } }));
assert.equal(gr.sort, "", "still warming up: keep Memory and wait");
assert.equal(gr.state.wantSort, "gpu-memory");
assert.match(A.statusNotes(Object.assign({}, gr.state, { sort: "gpu-memory" }), 0).join(" "), /Reading GPU clients/);
gq = send(gr.state, {}, false);
gr = A.accept(gq.state, page(gq, [gpuRow("llm"), row("a")], { gpu: gpuMeta() }));
assert.equal(gr.sort, "gpu-memory", "the default GPU lens switches once a device reports VRAM");
assert.equal(gr.state.sortNote, "");
assert.equal(A.sortAvailable("gpu-memory", gr.state), true);
assert.equal(A.stepSort("cpu", 1, gr.state), "gpu-memory");
let sw = send(gr.state, { sort: "gpu-memory", offset: 0 }, false);
assert.equal(sw.params.sort, "gpu-memory");

// The switch waits while the pointer is over the list (no reorder under a click).
let hs = A.setHovering(A.initialState("gpu-memory"), true);
let hq = send(hs, {}, false);
let hr = A.accept(hq.state, page(hq, [gpuRow("llm")], { gpu: gpuMeta() }));
assert.equal(hr.sort, "", "frozen: the default lens waits");
assert.equal(hr.state.wantSort, "gpu-memory");

// Integrated-only (no VRAM per client) or no device: visible fallback, no switch.
for (const [meta, why] of [[gpuMeta({ memoryLens: false }), /dedicated \(VRAM\)/],
                           [{ status: "unsupported", reason: "no-drm-devices", memoryLens: false, devices: [] }, /No DRM GPU/]]) {
  let fq = send(A.initialState("gpu-memory"), {}, false);
  let fr = A.accept(fq.state, page(fq, [row("a")], { gpu: meta }));
  assert.equal(fr.sort, "");
  assert.equal(fr.state.wantSort, "");
  assert.match(fr.state.sortNote, why);
  assert.match(fr.state.sortNote, /Showing Memory/);
}
const off = A.initialState("gpu-memory", "off");
assert.equal(off.wantSort, "");
let oq = send(off, {}, false);
let or = A.accept(oq.state, page(oq, [gpuRow("llm")], { gpu: gpuMeta() }));
assert.equal(or.sort, "", "gpuMetrics off never uses the lens, even with readings");
assert.equal(A.sortAvailable("gpu-memory", or.state), false);
let turned = A.setGpuSetting(Object.assign({}, gr.state, { sort: "gpu-memory" }), "off");
assert.equal(turned.sort, "memory", "turning GPU metrics off leaves the GPU lens");
assert.match(turned.state.sortNote, /turned off/);
assert.equal(A.setGpuSetting(gr.state, "auto").state, gr.state, "no change, no new state");

// A chosen GPU lens stays while its device sleeps; rows say why.
const asleepState = Object.assign({}, gr.state, { sort: "gpu-memory",
  meta: Object.assign({}, gr.state.meta, { gpu: gpuMeta({ memoryLens: false, devices: [{ id: AMD, status: "asleep", totals: {} }] }) }) });
assert.equal(A.sortAvailable("gpu-memory", asleepState), true);
assert.match(A.statusNotes(asleepState, 0).join(" "), /asleep; RAMen does not wake it/);

// Labels: VRAM is per device, partial is a lower bound, unknown is never zero.
assert.equal(A.gpuShort(gpuRow("x")), "2.0G");
assert.equal(A.gpuShort(gpuRow("x", { gpuStatus: "partial" })), "≥2.0G");
assert.equal(A.gpuShort(gpuRow("x", { gpuMemoryKb: 0 })), "0K", "a measured zero");
assert.equal(A.gpuShort(row("x", { gpuMemoryKb: null, gpuStatus: "none" })), "–");
assert.equal(A.gpuNote(gpuRow("x")), "VRAM 2.0G");
assert.equal(A.gpuNote(row("x", { gpuStatus: "none", gpu: [] })), "", "no GPU clients: nothing to say");
assert.equal(A.gpuNote(row("x", { gpuStatus: "unsupported", gpu: [] })), "", "page-wide states are header notes");
assert.equal(A.gpuNote(row("x", { gpuStatus: "permission-denied", gpu: [] })), "GPU unreadable");
assert.equal(A.gpuNote(gpuRow("x", { gpuMemoryKb: null, gpuStatus: "asleep", gpu: [vramEntry({ status: "asleep", vramKb: null, systemKb: null })] })), "GPU asleep");
assert.equal(A.gpuNote(gpuRow("x", { gpuMemoryKb: null, gpu: [vramEntry({ vramKb: null })] })), "GPU buffers in RAM 64M",
  "integrated GPU: system-RAM buffers, labelled as RAM");
assert.equal(A.subtitle(gpuRow("x")), "2 processes · VRAM 2.0G");
assert.equal(A.subtitle(gpuRow("x"), "gpu-memory"), "2 processes · CPU 6.3%", "the GPU lens moves CPU to the subtitle");
assert.equal(A.subtitle(row("x", { gpuStatus: "none", gpu: [], gpuMemoryKb: null }), "gpu-memory"), "2 processes · CPU 6.3% · no GPU clients");
assert.match(A.legend({ cpu: { onlineCpus: 8 } }, 16 * 1048576, "gpu-memory"), /VRAM: device-local GPU memory resident.*never added to RAM/);
assert.doesNotMatch(A.legend({ cpu: { onlineCpus: 8 } }, 16 * 1048576, "memory"), /VRAM/);
assert.match(A.accessibleRow(gpuRow("x"), 1000000, "gpu-memory"), /video memory 2.0G/);
const lines = A.gpuLines(gpuRow("x", { gpu: [vramEntry({ sharedClients: 1, status: "partial" })] }), { gpu: gpuMeta() });
assert.match(lines[0], /^GPU pci:0000:03:00.0 \(amdgpu\): VRAM resident 2.0G \(allocated 2.3G\) · GPU buffers in system RAM 64M \(part of RAM, not added\)$/);
assert.match(lines[1], /each % of its own engine class.*dma warming up · gfx 13%/);
assert.doesNotMatch(lines.join(" "), /GPU load|total GPU/i, "never a combined GPU percentage");
assert.match(lines[2], /1 shared with other apps \(not counted here\)/);
assert.match(lines[3], /Device VRAM used 9.0G of 16G; not attributed to this user's visible clients ≥1.0G/);
assert.match(A.gpuLines(gpuRow("x", { gpu: [vramEntry({ status: "shared", clients: 0, sharedClients: 2, vramKb: null })] }), null)[0],
  /2 clients shared with other apps, counted once on the device, not here/);
assert.match(A.gpuLines(gpuRow("x", { gpu: [vramEntry({ status: "asleep", clients: 0, vramKb: null })] }), null)[0], /asleep; RAMen does not wake it/);
assert.match(A.gpuLines(gpuRow("x", { gpu: [vramEntry({ status: "unsupported", clients: 0, unidentified: 1, vramKb: null })] }), null)[0], /without a client ID cannot be counted/);
assert.match(A.gpuLines(row("x", { gpu: [], gpuStatus: "none" }), null)[0], /No GPU clients/);
assert.match(A.gpuLines(gpuRow("x", { gpuStatus: "partial", gpuCoverage: { measured: 1, members: 2 } }), null).slice(-1)[0], /1 of 2 measured/);
assert.match(A.statusNotes(Object.assign({}, gr.state, { sort: "gpu-memory" }), 0).join(" "),
  /1 client\(s\) shared by several apps are counted once on the device/);
assert.match(A.statusNotes(Object.assign({}, gr.state, { sort: "gpu-memory",
  meta: Object.assign({}, gr.state.meta, { gpu: gpuMeta({ demo: true, status: "stale" }) }) }), 0).join(" "),
  /synthetic demo data.*older than 15 s/);

const meters = A.vramMeters(gpuRow("x"), { gpu: gpuMeta() });
assert.equal(meters.length, 1);
assert.equal(meters[0].fraction, 2097152 / (16 * 1048576));
assert.match(meters[0].text, /VRAM 2.0G of 16G on pci:0000:03:00.0/);
assert.deepEqual(plain(A.vramMeters(gpuRow("x"), { gpu: gpuMeta({ devices: [{ id: AMD, totals: { deviceVramTotalKb: null } }] }) })), [],
  "no device total: no meter, never a guessed denominator");

// Shared buffers across distinct clients: fdinfo has no buffer IDs, so a sum
// over two clients that both hold shared buffers may count one buffer twice
// (two clients each referencing the same 4 GiB buffer on an 8 GiB device). The
// row keeps the client-reference total but labels it, and never draws it as
// physical occupancy.
const DEV8 = gpuMeta({ devices: [{ id: AMD, status: "available", totals: { deviceVramUsedKb: 4 * 1048576,
  deviceVramTotalKb: 8 * 1048576, vramOverlap: "possible" } }], });
const twice = vramEntry({ vramKb: 8 * 1048576, vramAllocatedKb: 8 * 1048576, vramSharedKb: 8 * 1048576,
  vramOverlap: "possible", clients: 2 });
const ov = gpuRow("ov", { gpu: [twice], gpuMemoryKb: 8 * 1048576, gpuMemoryOverlap: true });
assert.equal(A.gpuShort(ov), "≤8.0G", "a client-reference sum is at most the app's own VRAM, never shown as exact");
assert.equal(A.gpuShort(gpuRow("x", { gpuMemoryOverlap: true, gpuStatus: "partial" })), "~2.0G", "partial and overlapping: no bound");
assert.equal(A.gpuNote(ov), "VRAM ≤8.0G client sum, shared buffers may repeat");
// Completeness is kept apart from freshness: a stale sum that missed members or
// clients (gpuComplete false) is never an upper bound or an exact figure.
const missed = { gpuComplete: false, gpuStatus: "stale", gpuCoverage: { measured: 2, members: 3 } };
const staleOv = gpuRow("so", Object.assign({ gpu: [Object.assign({}, twice, { status: "stale" })],
  gpuMemoryKb: 8 * 1048576, gpuMemoryOverlap: true }, missed));
assert.equal(A.gpuShort(staleOv), "~8.0G", "stale, incomplete and overlapping: a bound in neither direction");
assert.equal(A.gpuNote(staleOv), "VRAM ~8.0G client sum, shared buffers may repeat (stale)");
assert.match(A.accessibleRow(staleOv, 1000000, "gpu-memory"), /video memory ~8.0G \(client sum/);
assert.match(A.gpuLines(staleOv, { gpu: DEV8 }).slice(-1)[0], /GPU read for 2 of 3 measured processes/);
assert.equal(A.gpuShort(gpuRow("sl", missed)), "≥2.0G", "stale and incomplete: still a lower bound");
assert.equal(A.gpuShort(gpuRow("sc", { gpuComplete: true, gpuStatus: "stale", gpuMemoryOverlap: true })), "≤2.0G");
assert.equal(A.gpuShort(gpuRow("se", { gpuComplete: true, gpuStatus: "stale" })), "2.0G");
assert.equal(A.gpuShort(gpuRow("pf", { gpuComplete: false, gpuStatus: "partial" })), "≥2.0G");
assert.equal(A.gpuShort(gpuRow("old", { gpuStatus: "partial" })), "≥2.0G", "rows without the field: partial says it");
assert.equal(A.subtitle(ov, "gpu-memory"), "2 processes · CPU 6.3% · VRAM client sum, shared buffers may repeat");
assert.match(A.accessibleRow(ov, 1000000, "gpu-memory"), /video memory ≤8.0G \(client sum, shared buffers may repeat\)/);
const ovLines = A.gpuLines(ov, { gpu: DEV8 });
assert.match(ovLines[0], /VRAM resident 8.0G summed over 2 clients \(allocated 8.0G\)/);
assert.match(ovLines[1], /shared buffers \(8.0G\); one they share counts once per client, so this sums their references, not physical VRAM/);
assert.match(ovLines.join("\n"), /Device VRAM used 4.0G of 8.0G/, "the driver's physical total stays beside it");
assert.deepEqual(plain(A.vramMeters(ov, { gpu: DEV8 })), [], "no occupancy meter for a possibly overlapping sum");
// One client holding buffers shared with other files: counted once here, labelled, metered.
const one = gpuRow("one", { gpu: [vramEntry({ vramSharedKb: 1048576 })] });
assert.equal(A.gpuShort(one), "2.0G");
assert.match(A.gpuLines(one, { gpu: gpuMeta() })[1], /Includes buffers shared with other DRM files \(1.0G\), also counted by their other holders/);
assert.equal(A.vramMeters(one, { gpu: gpuMeta() }).length, 1);
// Several devices: only the device whose reading counts each buffer once gets a meter.
const INTEL = "pci:0000:00:02.0";
const multi = gpuRow("multi", { gpuMemoryKb: 10 * 1048576, gpuMemoryOverlap: true,
  gpu: [twice, vramEntry({ deviceId: INTEL, driver: "xe", vramKb: 2097152 })] });
const multiMeta = gpuMeta({ devices: [{ id: AMD, totals: { deviceVramTotalKb: 8 * 1048576 } },
  { id: INTEL, totals: { deviceVramTotalKb: 16 * 1048576 } }] });
assert.deepEqual(plain(A.vramMeters(multi, { gpu: multiMeta })).map(m => m.deviceId), [INTEL]);
assert.equal(A.gpuShort(multi), "≤10G", "one overlapping device makes the row's sum an upper bound");
// Sorting label: the lens says when some rows rank by an overlapping sum.
assert.match(A.legend({ cpu: { onlineCpus: 8 }, coverage: { gpuOverlap: 1 } }, 1, "gpu-memory"),
  /≤ sums several clients' references \(~ when also missing some\)/);
assert.doesNotMatch(A.legend({ cpu: { onlineCpus: 8 }, coverage: { gpuOverlap: 0 } }, 1, "gpu-memory"), /≤/);
const ovState = Object.assign({}, gr.state, { sort: "gpu-memory",
  meta: Object.assign({}, gr.state.meta, { coverage: { gpuOverlap: 2 } }) });
assert.match(A.statusNotes(ovState, 0).join(" "), /2 apps' VRAM sum several clients that share buffers \(≤, or ~ when also missing some\)/);

// Freshness: a sample that ran past the timeout is stale with its reason, never fresh.
assert.match(A.statusNotes(Object.assign({}, gr.state, { sort: "gpu-memory",
  meta: Object.assign({}, gr.state.meta, { gpu: gpuMeta({ status: "stale", reason: "sample-timeout" }) }) }), 0).join(" "),
  /took longer than 10 s; its readings are labelled stale/);
assert.equal(A.gpuLens(Object.assign({}, gr.state, { meta: { gpu: { status: "error", reason: "sample-timeout",
  memoryLens: false, devices: [] } } })).reason, "GPU sampling timed out.");

console.log("apps model tests passed");
