const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");
const N = load("PanelNav.js");

const KEYS = ["up", "down", "left", "right", "enter", "delete", "force", "refresh", "escape", "jumpLeft", "jumpRight",
  "search", "details", "nextPage", "prevPage", "backspace", "cancel", "open", "copy"];
const CONTEXTS = [
  { cursorActive: false, selectedIndex: 0, incidentIndex: 0, incidentCount: 0, appsIndex: -1 },
  { cursorActive: true, selectedIndex: 0, incidentIndex: 0, incidentCount: 3, appsIndex: 0, appsArmed: true },
  { cursorActive: true, selectedIndex: 2, incidentIndex: 2, incidentCount: 3, appsIndex: 2 },
  { cursorActive: true, selectedIndex: 2, appsIndex: 2, appsDetails: true },
  { cursorActive: true, selectedIndex: 2, appsIndex: 2, inputOwned: true },
];

// Safety: across every page, region, inspect mode, context and key, only the
// Memory app list and the Apps list (details closed, no editor focused) can
// produce an action that reaches a kill command.
let combos = 0;
for (const page of N.PAGES) {
  for (const region of N.REGIONS[page]) {
    for (const inspecting of [false, true]) {
      for (const ctx of CONTEXTS) {
        for (const key of KEYS) {
          const r = N.route({ page, region, inspecting }, key, ctx);
          combos++;
          const signals = N.SIGNAL_ACTIONS.indexOf(r.action) >= 0;
          if (signals) {
            assert.ok(page === "memory" || page === "apps", `${page}/${region} ${key} must not signal`);
            assert.equal(region, "list", `${page}/${region} ${key} must not signal`);
            if (page === "apps") {
              assert.ok(!ctx.appsDetails, `apps details ${key} must not signal`);
              assert.ok(!ctx.inputOwned, `apps search ${key} must not signal`);
            }
            if (page === "memory") assert.ok(["activate", "arm", "armForce"].indexOf(r.action) >= 0);
            if (page === "apps") assert.ok(/^apps/.test(r.action), "Apps never borrows Memory's kill actions");
          }
          if (page === "storage" || page === "history") assert.ok(!/^apps/.test(r.action || ""), `${page} never reaches Apps`);
          if (page === "history") assert.equal(r.state.page === "memory" && signals, false);
        }
      }
    }
  }
}
assert.ok(combos > 300);

// x and f on History never do anything at all.
for (const region of N.REGIONS.history) {
  for (const key of ["delete", "force"]) {
    assert.equal(N.route({ page: "history", region }, key, CONTEXTS[1]).action, null, `history/${region} ${key}`);
  }
}

// Memory list keeps the original two-step kill keys.
const list = { page: "memory", region: "list" };
assert.equal(N.route(list, "delete").action, "arm");
assert.equal(N.route(list, "force").action, "armForce");
assert.equal(N.route(list, "enter").action, "activate");
assert.equal(N.route(list, "down").action, "moveCursor");
assert.equal(N.route(list, "down").arg, 1);
assert.equal(N.route(list, "up", { cursorActive: true, selectedIndex: 3 }).arg, -1);
assert.equal(N.route(list, "escape").action, "escape", "Esc disarms first, then closes (panel decides)");

// Page navigation only from the focused page buttons.
assert.equal(N.route(list, "left").action, null, "h/l in the app list do not switch pages");
assert.equal(N.route(list, "right").action, null);
let r = N.route(list, "up", { cursorActive: false });
assert.equal(r.state.region, "nav");
assert.equal(r.action, "leaveList");
r = N.route(r.state, "right");
assert.equal(r.action, "page");
assert.equal(r.state.page, "history");
assert.equal(r.state.region, "nav");
assert.equal(N.route(r.state, "right").state.page, "storage");
assert.equal(N.route({ page: "storage", region: "nav" }, "right").state.page, "apps", "Apps follows Storage");
assert.equal(N.route({ page: "apps", region: "nav" }, "right").action, null, "no wraparound past last page");
assert.equal(N.route({ page: "memory", region: "nav" }, "left").action, null);
assert.equal(N.route({ page: "memory", region: "nav" }, "enter").action, null);
assert.equal(N.route({ page: "memory", region: "nav" }, "down").state.region, "list");

// History regions: window, chart scrub, receipts, inspect, back.
let h = { page: "history", region: "nav", inspecting: false };
h = N.route(h, "down").state;
assert.equal(h.region, "window");
assert.equal(N.route(h, "right").action, "window");
assert.equal(N.route(h, "right").arg, 1);
h = N.route(h, "down").state;
assert.equal(h.region, "chart");
assert.equal(N.route(h, "left").action, "scrub");
assert.equal(N.route(h, "left").arg, -1);
assert.equal(N.route(h, "enter").action, "inspectAtCursor");
assert.equal(N.route(h, "jumpRight").arg, N.JUMP, "H/L scrub faster on the chart");
assert.equal(N.route({ page: "history", region: "window" }, "jumpRight").action, null, "and nowhere else");
assert.equal(N.route(list, "jumpLeft").action, null);
assert.equal(N.route(h, "down", { incidentCount: 0 }).state.region, "chart", "no receipts: focus stays on the chart");
h = N.route(h, "down", { incidentCount: 2 }).state;
assert.equal(h.region, "incidents");
assert.equal(N.route(h, "down", { incidentIndex: 0, incidentCount: 2 }).action, "moveIncident");
assert.equal(N.route(h, "up", { incidentIndex: 1, incidentCount: 2 }).action, "moveIncident");
assert.equal(N.route(h, "up", { incidentIndex: 0, incidentCount: 2 }).state.region, "chart");
assert.equal(N.route(h, "enter", { incidentCount: 0 }).action, null);
r = N.route(h, "enter", { incidentCount: 2 });
assert.equal(r.action, "inspect");
assert.equal(r.state.inspecting, true);
assert.equal(N.route(r.state, "left").action, null);
assert.equal(N.route(r.state, "enter").action, null);
r = N.route(r.state, "escape");
assert.equal(r.action, "back", "Esc backs out of a receipt first");
assert.equal(r.state.inspecting, false);
assert.equal(N.route(r.state, "escape").action, "close", "then closes");
assert.equal(N.route({ page: "history", region: "chart" }, "refresh").action, "refresh");

// Mouse/IPC page selection.
assert.deepEqual(plain(N.selectPage(N.initialState(), "history")), { page: "history", region: "nav", inspecting: false });
assert.equal(N.selectPage(N.initialState(), "storage").page, "storage");

// Subscriptions follow visibility and page.
assert.equal(N.subscriptionFor(false, "memory"), null);
assert.deepEqual(plain(N.subscriptionFor(true, "memory")), { detail: true, history: false });
assert.deepEqual(plain(N.subscriptionFor(true, "history")), { detail: false, history: true }, "History needs no app scan");

// Apps: page buttons -> sort lens -> list; kill keys only in the list.
let a = { page: "apps", region: "nav", inspecting: false };
assert.equal(N.route(a, "left").state.page, "storage");
a = N.route(a, "down").state;
assert.equal(a.region, "sort");
assert.equal(N.route(a, "right").action, "appsSort");
assert.equal(N.route(a, "left").arg, -1);
for (const key of ["delete", "force", "enter"]) assert.equal(N.route(a, key).action, null, `sort ${key} never kills`);
assert.equal(N.route(a, "up").state.region, "nav");
r = N.route(a, "down", { appsIndex: -1 });
assert.equal(r.action, "appsEnterList");
a = r.state;
assert.equal(a.region, "list");
assert.equal(N.route(a, "delete", { appsIndex: 0 }).action, "appsArm");
assert.equal(N.route(a, "enter", { appsIndex: 0 }).action, "appsActivate");
assert.equal(N.route(a, "force", { appsIndex: 0 }).action, "appsArmForce");
assert.equal(N.route(a, "details", { appsIndex: 0 }).action, "appsDetails");
assert.equal(N.route(a, "down", { appsIndex: 0 }).action, "appsMove");
assert.equal(N.route(a, "up", { appsIndex: 3 }).arg, -1);
assert.equal(N.route(a, "left", { appsIndex: 3 }).action, null, "h/l in the Apps list do not switch pages");
r = N.route(a, "up", { appsIndex: 0 });
assert.equal(r.state.region, "sort");
assert.equal(r.action, "appsLeaveList", "leaving the list disarms");
assert.equal(N.route(a, "nextPage").arg, 1);
assert.equal(N.route(a, "prevPage").arg, -1);
assert.equal(N.route(a, "search").action, "appsSearch");
assert.equal(N.route({ page: "apps", region: "nav" }, "search").action, "appsSearch", "/ works from any Apps region");
assert.equal(N.route(a, "refresh").action, "appsRefresh");
// While the search field has focus, every key belongs to the field.
for (const key of KEYS) assert.equal(N.route(a, key, { inputOwned: true, appsIndex: 0 }).action, null, `typing ${key}`);
// Escape order: disarm, then details, then close (search handles its own Escape).
assert.equal(N.route(a, "escape", { appsArmed: true }).action, "appsDisarm");
assert.equal(N.route(a, "escape", { appsDetails: true }).action, "appsCloseDetails");
assert.equal(N.route(a, "escape", { appsDetails: true, appsArmed: true }).action, "appsDisarm");
assert.equal(N.route(a, "escape", {}).action, "close");
assert.equal(N.route(a, "details", { appsDetails: true }).action, "appsCloseDetails", "d toggles details");
assert.equal(N.route(a, "down", { appsDetails: true }).action, "appsScrollDetails");
for (const key of ["delete", "force", "enter", "nextPage"])
  assert.equal(N.route(a, key, { appsDetails: true }).action, null, `details view ${key} is read-only`);
// Other pages never react to Apps-only keys.
for (const page of ["memory", "history", "storage"])
  for (const region of N.REGIONS[page])
    for (const key of ["search", "details", "nextPage", "prevPage"])
      assert.ok(!/^apps/.test(N.route({ page, region }, key, {}).action || ""), `${page}/${region} ${key}`);
assert.deepEqual(plain(N.subscriptionFor(true, "apps")), { detail: true, history: false, gpu: true }, "Apps shares the Memory scan and asks for GPU readings");
assert.deepEqual(plain(N.subscriptionFor(true, "apps", "auto")), { detail: true, history: false, gpu: true });
assert.deepEqual(plain(N.subscriptionFor(true, "apps", "off")), { detail: true, history: false, gpu: false }, "gpuMetrics off: no GPU work");
assert.deepEqual(plain(N.subscriptionFor(true, "memory", "auto")), { detail: true, history: false }, "Memory never asks for GPU work");
assert.equal(N.subscriptionFor(false, "apps", "auto"), null, "a closed panel asks for nothing");
assert.equal(N.selectPage(N.initialState(), "apps").page, "apps");

console.log("panel navigation tests passed");

// Storage can leave the first ledger row, then switch pages without climbing
// its filesystem hierarchy. Editing keys remain owned by inputs.
let storageFocus = { page: 'storage', region: 'nav' };
storageFocus = N.route(storageFocus, 'down', {}).state;
assert.equal(storageFocus.region, 'ledger');
storageFocus = N.route(storageFocus, 'up', { storageIndex: 0 }).state;
assert.equal(storageFocus.region, 'nav');
assert.equal(N.route(storageFocus, 'left', {}).action, 'page');
assert.equal(N.route({page:'storage',region:'ledger'}, 'up', {storageIndex:1}).action, 'storageMove');
