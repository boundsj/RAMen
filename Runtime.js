.pragma library

// Pure helpers for RamanRuntime.qml: who wants which expensive probe data,
// and which history reply a view is still waiting for. Kept free of QML types
// so tests/test_runtime.js can run them under node.

// Subscriptions are { key: { detail: bool, history: bool } }, one entry per
// visible panel (or legacy setDetail caller); a visible Apps page with
// gpuMetrics auto adds gpu: true. Every change returns a new object so QML
// bindings see it.
function setSubscription(subs, key, wants) {
  var next = {}
  for (var k in subs || {}) if (k !== key) next[k] = subs[k]
  if (key && wants && (wants.detail || wants.history || wants.storage)) {
    next[key] = { detail: !!wants.detail, history: !!wants.history }
    if (wants.storage) next[key].storage = true
    if (wants.gpu) next[key].gpu = true
  }
  return next
}

function removeSubscription(subs, key) {
  return setSubscription(subs, key, null)
}

// True while any subscriber wants the given stream. Closing one panel only
// removes its own key, so another panel's subscription keeps the stream on.
function wants(subs, field) {
  for (var k in subs || {}) if (subs[k] && subs[k][field]) return true
  return false
}

function subscriberKeys(subs) {
  return Object.keys(subs || {}).sort()
}

// History request ids carry the client and a per-client generation, so a view
// can drop any reply that is not for its newest request (window switched,
// page left, panel closed). The probe caps ids at 64 characters.
function historyRequestId(client, generation) {
  return ("h:" + String(client) + ":" + String(generation)).slice(0, 64)
}

function acceptsReply(pendingId, message) {
  return !!pendingId && !!message && message.requestId === pendingId
}

// How often a visible History page re-queries: no faster than the window's
// resolution, and never faster than the 2 s summary cadence.
var REFRESH_MS = { "5m": 2000, "1h": 10000, "24h": 60000 }

function historyRefreshMs(window) {
  return REFRESH_MS[window] || 10000
}

// Drop kill bookkeeping for groups that are gone, and stale entries. The
// Memory list is only the top twelve, so entries sent from the Apps page
// (scope "apps") are kept by age alone; the Apps page checks presence itself.
function pruneKills(kills, apps, now) {
  var present = {}
  for (var i = 0; i < (apps || []).length; i++) present[apps[i].id] = true
  var next = {}
  for (var id in kills || {}) {
    if ((present[id] || kills[id].scope === "apps") && now - kills[id].at < 30000) next[id] = kills[id]
  }
  return next
}

// apps.query (A1 backend; the Apps page is A2). The request id carries the
// client and its query generation; the generation is also sent so the reply
// echoes it. A view keeps only its newest pending id, so an older search,
// sort or page can never overwrite a newer one.
var APPS_SORTS = ["memory", "cpu", "gpu-memory"]
var APPS_PAGE = 50
var APPS_MAX_QUERY = 256
var APPS_MAX_ID = 512

function appsRequestId(client, generation) {
  return ("a:" + String(client) + ":" + String(generation)).slice(0, 64)
}

// A bounded apps.query command. Pass snapshot to page within the snapshot a
// previous reply named; omit it to read the newest one.
function appsQuery(client, generation, params) {
  var p = params || {}
  var command = {
    command: "apps.query",
    requestId: appsRequestId(client, generation),
    generation: generation,
    query: String(p.query || "").slice(0, APPS_MAX_QUERY),
    sort: APPS_SORTS.indexOf(p.sort) >= 0 ? p.sort : "memory",
    offset: Math.max(0, Math.floor(Number(p.offset) || 0)),
    limit: Math.max(1, Math.min(APPS_PAGE, Math.floor(Number(p.limit) || APPS_PAGE)))
  }
  if (typeof p.snapshot === "number" && p.snapshot > 0) command.snapshot = p.snapshot
  // The armed or selected group: the reply says where it is and whether its
  // membership changed, whichever page is shown.
  if (typeof p.focus === "string" && p.focus !== "" && p.focus.length <= APPS_MAX_ID) command.focus = p.focus
  return command
}

// A2: the membership-checked kill. `membership` is the generation of the row
// that was armed; the probe refuses the signal if the group changed since.
function appsKill(client, sequence, id, membership, force) {
  return { command: "apps.kill", requestId: ("k:" + String(client) + ":" + String(sequence)).slice(0, 64),
    generation: sequence, id: String(id), membership: String(membership), signal: force ? "KILL" : "TERM" }
}

// On-demand details for one group; the reply is never stored by the runtime.
function appsDetails(client, generation, id) {
  return { command: "apps.details", requestId: ("d:" + String(client) + ":" + String(generation)).slice(0, 64),
    generation: generation, id: String(id) }
}

function acceptsAppsReply(pendingId, generation, message) {
  return acceptsReply(pendingId, message) && message.generation === generation
}

// Errors are routed by ownership, never broadcast across unrelated pages.
function replyStream(message) {
  if (!message) return ""
  if (message.type === "apps-page" || message.type === "apps-details"
      || (message.type === "error" && /^[adk]:/.test(String(message.requestId)))) return "apps"
  if (String(message.type).indexOf("storage-") === 0 || message.clientId !== undefined) return "storage"
  if (message.type === "history" || message.type === "history-cleared"
      || (message.type === "error" && /^(h:|clear-)/.test(String(message.requestId)))) return "history"
  return ""
}
