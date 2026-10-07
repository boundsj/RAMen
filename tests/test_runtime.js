const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");
const R = load("Runtime.js");

// Subscriptions: one key per visible panel; streams stay on while any key wants them.
let subs = {};
assert.equal(R.wants(subs, "detail"), false);
subs = R.setSubscription(subs, "panel-DP-1", { detail: true });
subs = R.setSubscription(subs, "panel-HDMI-1", { detail: true });
assert.equal(R.wants(subs, "detail"), true);
subs = R.removeSubscription(subs, "panel-DP-1");
assert.equal(R.wants(subs, "detail"), true, "closing one monitor's panel keeps the other's detail scan");
subs = R.setSubscription(subs, "panel-HDMI-1", { history: true });
assert.equal(R.wants(subs, "detail"), false, "switching that panel to History drops the app scan");
assert.equal(R.wants(subs, "history"), true);
subs = R.removeSubscription(subs, "panel-HDMI-1");
assert.deepEqual(plain(subs), {}, "closing the last panel clears every expensive subscription");

// Changes return new objects (QML only notices reassignment) and do not mutate.
const before = { a: { detail: true, history: false } };
const after = R.setSubscription(before, "b", { detail: true });
assert.notEqual(after, before);
assert.deepEqual(plain(Object.keys(before)), ["a"]);
assert.deepEqual(plain(R.subscriberKeys(after)), ["a", "b"]);
assert.deepEqual(plain(R.setSubscription(after, "a", { detail: false, history: false })), { b: { detail: true, history: false } },
  "a subscription that wants nothing is removed");
assert.deepEqual(plain(R.removeSubscription(after, "missing")), plain(after));

// Request ids: the newest generation wins; anything else is stale.
const id1 = R.historyRequestId("p1-history", 1);
const id2 = R.historyRequestId("p1-history", 2);
assert.notEqual(id1, id2);
assert.equal(R.acceptsReply(id2, { type: "history", requestId: id2 }), true);
assert.equal(R.acceptsReply(id2, { type: "history", requestId: id1 }), false, "a reply for an older window is dropped");
assert.equal(R.acceptsReply("", { type: "history", requestId: "" }), false, "nothing pending accepts nothing (closed view)");
assert.equal(R.acceptsReply(id2, { type: "error", requestId: null }), false);
assert.ok(R.historyRequestId("x".repeat(80), 123).length <= 64, "the probe rejects ids over 64 characters");

assert.equal(R.historyRefreshMs("5m"), 2000);
assert.equal(R.historyRefreshMs("1h"), 10000);
assert.equal(R.historyRefreshMs("24h"), 60000);

// Kill bookkeeping keeps only present, recent groups. Apps-page kills are
// kept by age: their group can be far outside the Memory top twelve.
const kills = { a: { at: 1000 }, b: { at: 1000 }, c: { at: 0 }, d: { at: 1000, scope: "apps" }, e: { at: 0, scope: "apps" } };
assert.deepEqual(plain(Object.keys(R.pruneKills(kills, [{ id: "a" }, { id: "c" }], 30999))), ["a", "d"]);

// apps.query: bounded commands, newest-only replies and their own reply stream.
const aq = R.appsQuery("apps-1", 4, { query: "x".repeat(300), sort: "gpu", offset: -3, limit: 500, snapshot: 9 });
assert.equal(aq.command, "apps.query");
assert.equal(aq.requestId, "a:apps-1:4");
assert.equal(aq.generation, 4);
assert.equal(aq.query.length, 256, "search text is bounded to 256 characters");
assert.equal(aq.sort, "memory", "unsupported sorts fall back to memory");
assert.equal(aq.offset, 0);
assert.equal(aq.limit, 50, "pages are at most 50 rows");
assert.equal(aq.snapshot, 9, "paging can pin the snapshot a reply named");
const latest = R.appsQuery("apps-1", 5, { query: "chrome", sort: "cpu", offset: 50, limit: 10 });
assert.equal(latest.sort, "cpu");
assert.equal(latest.limit, 10);
assert.equal(R.appsQuery("apps-1", 6, { sort: "gpu-memory" }).sort, "gpu-memory", "A3: the GPU memory lens");

// A3: GPU sampling follows the Apps page's own gpu flag; Memory panels never set it.
let gsubs = R.setSubscription({}, "panel-memory", { detail: true, history: false });
assert.equal(R.wants(gsubs, "gpu"), false, "a Memory panel asks for no GPU work");
gsubs = R.setSubscription(gsubs, "panel-apps", { detail: true, history: false, gpu: true });
assert.equal(R.wants(gsubs, "gpu"), true);
assert.deepEqual(plain(gsubs["panel-apps"]), { detail: true, history: false, gpu: true });
gsubs = R.setSubscription(gsubs, "panel-apps", { detail: true, history: false, gpu: false });
assert.equal(R.wants(gsubs, "gpu"), false, "gpuMetrics off keeps the scan but stops GPU work");
assert.equal(R.wants(gsubs, "detail"), true);
gsubs = R.removeSubscription(R.setSubscription(gsubs, "panel-apps", { detail: true, gpu: true }), "panel-apps");
assert.equal(R.wants(gsubs, "gpu"), false, "closing the Apps panel stops GPU work");
assert.equal(R.wants(gsubs, "detail"), true, "another monitor's Memory panel keeps its scan");
assert.equal(latest.snapshot, undefined, "no snapshot reads the newest one");
assert.ok(R.appsRequestId("y".repeat(80), 1).length <= 64);
assert.equal(R.acceptsAppsReply(latest.requestId, 5, { type: "apps-page", requestId: latest.requestId, generation: 5 }), true);
assert.equal(R.acceptsAppsReply(latest.requestId, 5, { type: "apps-page", requestId: aq.requestId, generation: 4 }), false,
  "an older search/sort/page reply cannot overwrite a newer one");
assert.equal(R.acceptsAppsReply(latest.requestId, 5, { type: "apps-page", requestId: latest.requestId, generation: 4 }), false);
assert.equal(R.acceptsAppsReply(latest.requestId, 5,
  { type: "error", requestId: latest.requestId, generation: 5, code: "bad-page" }), true,
  "a validation error echoing the pending generation reaches its request");
assert.equal(R.acceptsAppsReply(latest.requestId, 5, { type: "error", requestId: latest.requestId, code: "bad-page" }),
  false, "an error without the generation is dropped");
assert.equal(R.replyStream({ type: "apps-page", requestId: "a:x:1" }), "apps");
assert.equal(R.replyStream({ type: "error", requestId: "a:x:1", code: "bad-query" }), "apps");
assert.equal(R.replyStream({ type: "error", requestId: "h:x:1" }), "history", "History errors stay with History");
assert.equal(R.replyStream({ type: "storage-children", clientId: "s" }), "storage");
assert.equal(R.replyStream({ type: "apps" }), "", "legacy scans keep their own path");

// A2: focus tracking, the membership-checked kill and on-demand details.
assert.equal(R.appsQuery("c", 1, { focus: "app-x.scope" }).focus, "app-x.scope");
assert.equal(R.appsQuery("c", 1, { focus: "" }).focus, undefined);
assert.equal(R.appsQuery("c", 1, { focus: "x".repeat(513) }).focus, undefined, "focus IDs are bounded");
const kill = R.appsKill("apps-1", 3, "app-x.scope", "5c1f0e9b2a7d4e10", true);
assert.deepEqual(plain(kill), { command: "apps.kill", requestId: "k:apps-1:3", generation: 3, id: "app-x.scope",
  membership: "5c1f0e9b2a7d4e10", signal: "KILL" });
assert.equal(R.appsKill("c", 1, "x", "g", false).signal, "TERM");
assert.ok(R.appsKill("z".repeat(80), 1, "x", "g", false).requestId.length <= 64);
const details = R.appsDetails("apps-1", 2, "pid:7");
assert.deepEqual(plain(details), { command: "apps.details", requestId: "d:apps-1:2", generation: 2, id: "pid:7" });
assert.equal(R.replyStream({ type: "apps-details", requestId: "d:x:1" }), "apps");
assert.equal(R.replyStream({ type: "error", requestId: "d:x:1", code: "gone" }), "apps");
assert.equal(R.replyStream({ type: "error", requestId: "k:x:1", code: "bad-request" }), "apps");
assert.equal(R.replyStream({ type: "killed", requestId: "k:x:1", code: "stale-membership" }), "",
  "kill outcomes update the shared kill bookkeeping, like legacy kills");
assert.ok(JSON.stringify(kill).length < 4096);

console.log("runtime tests passed");
