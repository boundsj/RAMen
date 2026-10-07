.pragma library
.import "Level.js" as Level

// Pure state and labels for AppsView.qml, kept free of QML types so
// tests/test_apps_model.js can run them under node.
//
// The page shows one apps.query page of the probe's cached inventory. Rows
// are identified by group ID, never by index: the selection, the armed kill
// and the details view all follow an ID. While the pointer is over the list
// or a kill is armed, live updates are queued instead of reordering rows
// underneath an imminent click; they are applied when the pointer leaves or
// the kill is disarmed. A queued update can still disarm: every query names
// the armed group as its `focus`, and a changed membership generation or a
// vanished group disarms at once. Only user changes (search, sort, page)
// replace the rows while frozen, and those disarm first.

var PAGE = 50
var ARM_MS = 4000
var STUBBORN_MS = 4000
var PENDING_TIMEOUT_MS = 5000

// GPU memory is a lens only while the probe reports a device whose clients
// give device-local (VRAM) resident readings; see gpuLens.
var SORTS = [
  { key: "memory", label: "Memory" },
  { key: "cpu", label: "CPU" },
  { key: "gpu-memory", label: "GPU memory" }
]

// { available, reason } of the GPU memory lens for this state. Unavailable
// when gpuMetrics is off, before the first GPU sample, or when no device
// reports dedicated memory per client (integrated-only, unsupported drivers).
function gpuLens(state) {
  if (!state) return { available: false, reason: "GPU readings are not available." }
  if (state.gpuSetting === "off") return { available: false, reason: "GPU metrics are off in settings." }
  var g = state.meta ? state.meta.gpu : null
  if (!g || g.status === "inactive") return { available: false, reason: "GPU readings have not started." }
  if (g.memoryLens) return { available: true, reason: "" }
  if (g.status === "warming-up") return { available: false, reason: "Reading GPU clients…" }
  if (g.status === "error") return { available: false, reason: g.reason === "sample-timeout"
    ? "GPU sampling timed out." : "GPU sampling failed." }
  if (g.status === "unsupported") return { available: false, reason: "No DRM GPU device was found." }
  return { available: false, reason: "No GPU reports dedicated (VRAM) memory per app." }
}

function sortAvailable(key, state) {
  if (key === "memory" || key === "cpu") return true
  // An already chosen GPU lens stays put while its device sleeps; rows say why.
  return key === "gpu-memory" && (gpuLens(state).available || (!!state && state.sort === "gpu-memory"))
}

// The appsDefaultSort setting: memory (default) | cpu | gpu-memory. GPU memory
// starts on Memory with a note and switches once the first GPU sample shows
// the lens is available (wantSort); an unknown choice falls back to memory.
function defaultSort(setting, gpuSetting) {
  var value = setting === undefined || setting === null || setting === "" ? "memory" : String(setting)
  if (value === "memory" || value === "cpu") return { sort: value, note: "", want: "" }
  if (value === "gpu-memory" && gpuSetting === "off")
    return { sort: "memory", note: "GPU memory is not available: GPU metrics are off in settings. Showing Memory.", want: "" }
  if (value === "gpu-memory")
    return { sort: "memory", note: "GPU memory is not available yet (reading GPU clients); showing Memory.", want: "gpu-memory" }
  return { sort: "memory", note: "Unknown default sort; showing Memory.", want: "" }
}

// The next available sort `step` places away, skipping unavailable ones.
function stepSort(sort, step, state) {
  var keys = SORTS.filter(function(s) { return sortAvailable(s.key, state) }).map(function(s) { return s.key })
  var at = keys.indexOf(sort)
  if (at < 0) return keys[0]
  return keys[Math.max(0, Math.min(keys.length - 1, at + step))]
}

function copy(state, changes) {
  var next = {}
  for (var k in state) next[k] = state[k]
  for (var c in changes || {}) next[c] = changes[c]
  return next
}

function initialState(setting, gpuSetting) {
  var gpu = gpuSetting === "off" ? "off" : "auto"
  var chosen = defaultSort(setting, gpu)
  return {
    gpuSetting: gpu, wantSort: chosen.want,
    query: "", sort: chosen.sort, offset: 0, pin: null,
    generation: 0, pendingId: "", pendingAt: 0, userPending: false,
    status: "loading", rows: [], meta: null, total: 0, nextOffset: null, focus: null,
    queued: null, selectedId: "", hovering: false, armed: null,
    notice: "", sortNote: chosen.note, error: ""
  }
}

function frozen(state) {
  return !!state.armed || !!state.hovering
}

// --- requests ---------------------------------------------------------------

// A new query. `changes` may set query, sort or offset (user actions) and
// pin (the snapshot to page within). User changes disarm first. Returns
// { state, params }; the caller sends params with state.generation and then
// records the request id with pending().
function request(state, changes, user) {
  var c = changes || {}
  var next = copy(state, { generation: state.generation + 1, userPending: !!user || !!state.userPending })
  if (user) {
    next = disarm(next, "").state
    next.userPending = true
    if (c.sort !== undefined) {
      next.sortNote = "" // the user chose a lens; the fallback note has done its job
      next.wantSort = ""
    }
  }
  if (c.query !== undefined) next.query = String(c.query).slice(0, 256)
  if (c.sort !== undefined && sortAvailable(c.sort, next)) next.sort = c.sort
  if (c.offset !== undefined) next.offset = Math.max(0, Math.floor(c.offset))
  next.pin = c.pin === undefined ? null : c.pin
  var focus = next.armed ? next.armed.id : next.selectedId
  var params = { query: next.query, sort: next.sort, offset: next.offset }
  if (typeof next.pin === "number") params.snapshot = next.pin
  if (focus) params.focus = focus
  return { state: next, params: params }
}

function pending(state, requestId, now) {
  return copy(state, { pendingId: requestId || "", pendingAt: now || 0,
    userPending: requestId ? state.userPending : false })
}

// A live refresh is skipped while a request is outstanding, unless it has
// been lost (probe restart) for longer than PENDING_TIMEOUT_MS.
function canRefresh(state, now) {
  return state.pendingId === "" || now - state.pendingAt > PENDING_TIMEOUT_MS
}

// A page step of +1/-1 within the shown snapshot. null when there is none.
function pageChange(state, step) {
  if (step > 0 && state.nextOffset === null) return null
  if (step < 0 && state.offset === 0) return null
  var offset = step > 0 ? state.nextOffset : Math.max(0, state.offset - PAGE)
  return { offset: offset, pin: state.meta ? state.meta.snapshot : null }
}

// --- replies ----------------------------------------------------------------

function accepts(state, message) {
  return !!message && state.pendingId !== "" && message.requestId === state.pendingId
    && message.generation === state.generation
}

function applyReply(state, message) {
  return copy(state, {
    status: message.status, rows: (message.rows || []).slice(), total: message.total || 0,
    offset: message.offset || 0, nextOffset: message.nextOffset === undefined ? null : message.nextOffset,
    focus: message.focus || null, queued: null, error: "",
    meta: message.status === "ready" ? {
      snapshot: message.snapshot, sampledAt: message.sampledAt, stale: !!message.stale, demo: !!message.demo,
      inventory: message.inventory, cpu: message.cpu, coverage: message.coverage, units: message.units,
      gpu: message.gpu || null
    } : null
  })
}

// The default GPU memory lens, once known: { state, sort } where sort is
// "gpu-memory" when the view should switch now (never under the pointer or an
// armed kill; it waits), or "" with the fallback note explaining why not.
function resolveWant(state) {
  if (state.wantSort !== "gpu-memory" || state.status !== "ready") return { state: state, sort: "" }
  var lens = gpuLens(state)
  if (lens.available) {
    if (frozen(state) || state.pendingId !== "") return { state: state, sort: "" }
    return { state: copy(state, { wantSort: "", sortNote: "" }), sort: "gpu-memory" }
  }
  var g = state.meta ? state.meta.gpu : null
  if (state.gpuSetting === "off" || (g && g.status !== "warming-up" && g.status !== "inactive"))
    return { state: copy(state, { wantSort: "", sortNote: "GPU memory is not available: " + lens.reason + " Showing Memory." }),
      sort: "" }
  return { state: state, sort: "" }
}

// Accept one apps-page or apps error. Returns { state, accepted, restart, sort }.
// restart is true when a pinned snapshot expired: query again from the top;
// sort names a default lens to switch to now (resolveWant).
function accept(state, message) {
  var result = acceptReply(state, message)
  if (!result.accepted || result.restart) return result
  var want = resolveWant(result.state)
  result.state = want.state
  result.sort = want.sort
  return result
}

function acceptReply(state, message) {
  if (!accepts(state, message)) return { state: state, accepted: false, restart: false }
  var user = state.userPending
  var next = copy(state, { pendingId: "", userPending: false })
  if (message.type === "error") {
    if (message.code === "snapshot-expired")
      return { state: copy(next, { offset: 0, pin: null, notice: "That snapshot expired; back to the first page." }),
        accepted: true, restart: true }
    return { state: copy(next, { error: String(message.message || message.code).slice(0, 200) }),
      accepted: true, restart: false }
  }
  if (next.armed) {
    var f = message.focus
    var name = next.armed.name
    if (message.status !== "ready") next = disarm(next, "Scan unavailable; disarmed.").state
    else if (!f || f.id !== next.armed.id || !f.present) next = disarm(next, name + " is no longer running; disarmed.").state
    else if (f.generation !== next.armed.generation)
      next = disarm(next, name + " changed (processes started or exited); disarmed. Review and arm again.").state
  }
  if (frozen(next) && !user && message.status === "ready") return { state: copy(next, { queued: message }), accepted: true, restart: false }
  return { state: applyReply(next, message), accepted: true, restart: false }
}

// Pointer over the list (true) or gone (false). Leaving applies queued rows.
function setHovering(state, on) {
  var next = copy(state, { hovering: !!on })
  return reconcile(next)
}

// gpuMetrics changed while the page is open: a lens that is now off falls back.
function setGpuSetting(state, setting) {
  var gpu = setting === "off" ? "off" : "auto"
  if (gpu === state.gpuSetting) return { state: state, sort: "" }
  var next = copy(state, { gpuSetting: gpu })
  if (gpu === "off" && next.sort === "gpu-memory")
    return { state: copy(next, { sortNote: "GPU metrics were turned off; showing Memory.", wantSort: "" }), sort: "memory" }
  return { state: next, sort: "" }
}

function reconcile(state) {
  if (frozen(state) || !state.queued) return state
  return applyReply(state, state.queued)
}

// --- selection and the two-step kill ---------------------------------------

function indexOf(rows, id) {
  for (var i = 0; i < (rows || []).length; i++) if (rows[i].id === id) return i
  return -1
}

function selectedIndex(state) {
  return state.selectedId ? indexOf(state.rows, state.selectedId) : -1
}

function selectedRow(state) {
  var i = selectedIndex(state)
  return i >= 0 ? state.rows[i] : null
}

function disarm(state, notice) {
  var next = copy(state, { armed: null })
  if (notice !== undefined && notice !== null) next.notice = notice
  return { state: reconcile(next) }
}

function select(state, id) {
  if (id === state.selectedId) return state
  var next = state.armed ? disarm(state, "").state : state
  return copy(next, { selectedId: id || "" })
}

// j/k: from no selection, the first row; otherwise one row up or down.
function move(state, step) {
  if (!state.rows.length) return state
  var at = selectedIndex(state)
  var target = at < 0 ? 0 : Math.max(0, Math.min(state.rows.length - 1, at + step))
  return select(state, state.rows[target].id)
}

// "" | "stopping" | "stubborn" | "error", as on the Memory page.
function killState(kills, id, now) {
  var entry = kills ? kills[id] : null
  if (!entry) return ""
  if (entry.error) return "error"
  if (entry.signal === "TERM" && now - entry.at >= STUBBORN_MS) return "stubborn"
  return "stopping"
}

// First press arms the row with its current membership generation; a
// matching second press confirms against that same armed generation.
// Returns { state, kill } where kill is null or { id, membership, force }.
function armOrConfirm(state, row, force, stubborn) {
  if (!row || row.protected || !row.generation) return { state: state, kill: null }
  var useForce = !!force || !!stubborn
  var armed = state.armed
  if (armed && armed.id === row.id && (armed.force === useForce || !useForce)) {
    var kill = { id: armed.id, membership: armed.generation, force: armed.force }
    return { state: disarm(state, "").state, kill: kill }
  }
  var next = copy(state, { selectedId: row.id, notice: "",
    armed: { id: row.id, generation: row.generation, force: useForce, name: row.name } })
  return { state: next, kill: null }
}

// --- labels and rails ------------------------------------------------------

function percent(value) {
  var v = Number(value)
  if (v > 0 && v < 0.1) return "<0.1%"
  return (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + "%"
}

// CPU as a share of the whole machine; "≥" when some members are unmeasured.
function cpuText(row) {
  if (!row) return "–"
  if (row.cpuMachinePercent === null || row.cpuMachinePercent === undefined)
    return row.cpuStatus === "warming-up" || row.cpuStatus === "partial" ? "warming up" : "–"
  return (row.cpuStatus === "partial" ? "≥" : "") + percent(row.cpuMachinePercent)
}

// The row's compact CPU value; why it is missing goes in the subtitle.
function cpuShort(row) {
  if (!row || row.cpuMachinePercent === null || row.cpuMachinePercent === undefined) return "–"
  return cpuText(row)
}

function cpuCoreText(row) {
  if (!row || row.cpuCorePercent === null || row.cpuCorePercent === undefined) return ""
  return (row.cpuStatus === "partial" ? "≥" : "") + percent(row.cpuCorePercent) + " of one CPU"
}

// Rail fill 0..1, or null when there is no reading (drawn hollow, never empty-as-zero).
function cpuFraction(row) {
  if (!row || row.cpuMachinePercent === null || row.cpuMachinePercent === undefined) return null
  return Math.max(0, Math.min(1, Number(row.cpuMachinePercent) / 100))
}

// Resident RAM rail: PSS (not swap) over physical RAM.
function ramFraction(row, totalKb) {
  if (!row || row.pss === null || row.pss === undefined || !(totalKb > 0)) return null
  return Math.max(0, Math.min(1, Number(row.pss) / totalKb))
}

function ramText(row, totalKb) {
  var f = ramFraction(row, totalKb)
  if (f === null) return "–"
  return (row.memoryStatus === "partial" ? "≥" : "") + percent(f * 100)
}

// --- GPU ----------------------------------------------------------------------

function known(value) {
  return value !== null && value !== undefined
}

// Whether the row's GPU sums leave out members or clients. gpuComplete is
// judged before staleness, so a stale reading keeps it; without the field the
// fresh "partial" status says the same.
function gpuIncomplete(row) {
  return row.gpuComplete === false || (!known(row.gpuComplete) && row.gpuStatus === "partial")
}

// Device-local memory (VRAM) resident for the row, summed over devices; "≥"
// when some members or clients could not be counted, "≤" when the sum adds
// several clients' references to buffers they may share (gpuMemoryOverlap:
// no buffer IDs, so a shared buffer can count once per client), "~" when both
// apply and the figure is a bound in neither direction. Staleness keeps them.
function gpuShort(row) {
  if (!row || !known(row.gpuMemoryKb)) return "–"
  var lower = gpuIncomplete(row)
  return (lower && row.gpuMemoryOverlap ? "~" : lower ? "≥" : row.gpuMemoryOverlap ? "≤" : "")
    + Level.formatKb(row.gpuMemoryKb)
}

// Short label for an overlapping client-reference total.
var GPU_OVERLAP_TEXT = "client sum, shared buffers may repeat"

function gpuSystemKb(row) {
  var total = null
  var list = row && row.gpu ? row.gpu : []
  for (var i = 0; i < list.length; i++) if (known(list[i].systemKb)) total = (total || 0) + list[i].systemKb
  return total
}

var GPU_STATE_TEXT = {
  "warming-up": "GPU reading…", "asleep": "GPU asleep", "permission-denied": "GPU unreadable",
  "shared": "GPU shared with other apps", "unsupported": "GPU not reported", "stale": "GPU stale",
  "error": "GPU error", "partial": "GPU partial"
}

// Short GPU text for a row's subtitle ("" when there is nothing to say).
function gpuNote(row) {
  if (!row || !row.gpuStatus || row.gpuStatus === "inactive" || row.gpuStatus === "none") return ""
  if (known(row.gpuMemoryKb)) return "VRAM " + gpuShort(row) + (row.gpuMemoryOverlap ? " " + GPU_OVERLAP_TEXT : "")
    + (row.gpuStatus === "stale" ? " (stale)" : "")
  var system = gpuSystemKb(row)
  if (system !== null && (row.gpuStatus === "available" || row.gpuStatus === "partial"))
    return "GPU buffers in RAM " + (gpuIncomplete(row) ? "≥" : "") + Level.formatKb(system)
  // Page-wide states (no device, warming up, stale, failed) are header notes, not repeated per row.
  if (!row.gpu || row.gpu.length === 0) return row.gpuStatus === "permission-denied" ? GPU_STATE_TEXT["permission-denied"] : ""
  return GPU_STATE_TEXT[row.gpuStatus] || ""
}

function percentOrState(reading) {
  if (reading.status === "available" || reading.status === "partial")
    return (reading.status === "partial" ? "≥" : "") + percent(reading.percent)
  return reading.status === "warming-up" ? "warming up" : reading.status
}

// Details lines for one row: one block per device, then how coverage limits it.
function gpuLines(row, meta) {
  var lines = []
  if (!row) return lines
  var devices = {}
  var listed = meta && meta.gpu && meta.gpu.devices ? meta.gpu.devices : []
  for (var d = 0; d < listed.length; d++) devices[listed[d].id] = listed[d]
  if (!row.gpu || row.gpu.length === 0) {
    var why = { "inactive": "GPU readings are off for this view.", "none": "No GPU clients (DRM descriptors) in this app.",
      "warming-up": "GPU: not read yet (new processes are read every 5 s).",
      "permission-denied": "GPU: its processes' descriptors could not be read.",
      "partial": "GPU: no clients in the processes that could be read.", "unsupported": "GPU: no DRM device.",
      "stale": "GPU readings are stale.", "error": "GPU sampling failed." }[row.gpuStatus]
    if (why) lines.push(why)
    return lines
  }
  for (var i = 0; i < row.gpu.length; i++) {
    var e = row.gpu[i]
    var name = "GPU " + e.deviceId + (e.driver ? " (" + e.driver + ")" : "")
    if (e.status === "asleep") { lines.push(name + ": asleep; RAMen does not wake it to read clients."); continue }
    if (e.status === "shared") {
      lines.push(name + ": " + e.sharedClients + " client" + (e.sharedClients === 1 ? "" : "s")
        + " shared with other apps, counted once on the device, not here.")
      continue
    }
    if (e.status === "unsupported" && e.clients === 0) {
      lines.push(name + (e.unidentified ? ": clients without a client ID cannot be counted." : ": its driver reports no per-client usage."))
      continue
    }
    if (e.status === "error" && e.clients === 0) { lines.push(name + ": could not be read."); continue }
    var parts = []
    var sum = e.clients > 1 ? " summed over " + e.clients + " clients" : ""
    parts.push(known(e.vramKb) ? "VRAM resident " + Level.formatKb(e.vramKb) + (e.vramOverlap === "possible" ? sum : "")
      + (known(e.vramAllocatedKb) ? " (allocated " + Level.formatKb(e.vramAllocatedKb) + ")" : "")
      : "no dedicated VRAM reported")
    if (known(e.systemKb)) parts.push("GPU buffers in system RAM " + Level.formatKb(e.systemKb)
      + (e.systemOverlap === "possible" ? sum : "") + " (part of RAM, not added)")
    lines.push(name + ": " + parts.join(" · "))
    // Shared buffers: counted by every DRM file holding them; fdinfo has no buffer IDs.
    if (known(e.vramKb) && e.vramOverlap === "possible")
      lines.push("  These clients hold shared buffers" + (known(e.vramSharedKb) ? " (" + Level.formatKb(e.vramSharedKb) + ")" : "")
        + "; one they share counts once per client, so this sums their references, not physical VRAM.")
    else if (known(e.vramKb) && e.vramSharedKb > 0)
      lines.push("  Includes buffers shared with other DRM files (" + Level.formatKb(e.vramSharedKb) + "), also counted by their other holders.")
    if (known(e.systemKb) && e.systemOverlap === "possible")
      lines.push("  GPU buffers in system RAM: shared buffers may count once per client.")
    var engineNames = Object.keys(e.engines || {}).sort()
    if (engineNames.length) {
      lines.push("  Engines (each % of its own engine class): " + engineNames.map(function(n) {
        return n + " " + percentOrState(e.engines[n])
      }).join(" · "))
    }
    var notes = []
    if (e.clients) notes.push(e.clients + " client" + (e.clients === 1 ? "" : "s"))
    if (e.sharedClients) notes.push(e.sharedClients + " shared with other apps (not counted here)")
    if (e.unidentified) notes.push(e.unidentified + " without client ID (not counted)")
    if (e.unread) notes.push(e.unread + " not read (limit)")
    if (e.status === "stale") notes.push("stale")
    if (notes.length) lines.push("  " + notes.join(" · "))
    var dev = devices[e.deviceId]
    if (dev && dev.totals && known(dev.totals.deviceVramUsedKb)) {
      var t = dev.totals
      lines.push("  Device VRAM used " + Level.formatKb(t.deviceVramUsedKb)
        + (known(t.deviceVramTotalKb) ? " of " + Level.formatKb(t.deviceVramTotalKb) : "")
        + (known(t.unattributedVramKb) ? "; not attributed to this user's visible clients " + (t.unattributedLowerBound ? "≥" : "")
          + Level.formatKb(t.unattributedVramKb) : ""))
    }
  }
  if (row.gpuCoverage && row.gpuCoverage.measured < row.gpuCoverage.members)
    lines.push("GPU read for " + coverageText(row.gpuCoverage) + " processes; the rest are unreadable or newer than the last GPU sample.")
  return lines
}

// Expanded VRAM meters for details: one per device whose total VRAM the driver
// reports (amdgpu sysfs); fraction = this app's resident VRAM / that total.
// Only for a reading that counts each buffer once (vramOverlap "none"): a sum
// of several clients' references to shared buffers is not physical occupancy.
function vramMeters(row, meta) {
  var totals = {}
  var listed = meta && meta.gpu && meta.gpu.devices ? meta.gpu.devices : []
  for (var d = 0; d < listed.length; d++)
    if (listed[d].totals && known(listed[d].totals.deviceVramTotalKb) && listed[d].totals.deviceVramTotalKb > 0)
      totals[listed[d].id] = listed[d].totals.deviceVramTotalKb
  var meters = []
  var list = row && row.gpu ? row.gpu : []
  for (var i = 0; i < list.length; i++) {
    var e = list[i]
    if (!known(e.vramKb) || !totals[e.deviceId] || e.vramOverlap !== "none") continue
    meters.push({ deviceId: e.deviceId, fraction: Math.max(0, Math.min(1, e.vramKb / totals[e.deviceId])),
      text: "VRAM " + Level.formatKb(e.vramKb) + " of " + Level.formatKb(totals[e.deviceId]) + " on " + e.deviceId })
  }
  return meters
}

function coverageText(coverage) {
  return coverage ? coverage.measured + " of " + coverage.members + " measured" : ""
}

// Second line of a row: what the group is and what its numbers are. In the
// GPU memory lens the CPU share moves here; otherwise a GPU note appears.
function subtitle(row, sort) {
  if (!row) return ""
  var parts = []
  parts.push(row.count === 1 ? "1 process" : row.count + " processes")
  if (row.kind === "session" && row.host) parts.push("in " + row.host)
  var note = Level.memoryNote(row)
  if (note !== "") parts.push(note)
  if (row.cpuStatus === "partial") parts.push(row.cpuMachinePercent === null || row.cpuMachinePercent === undefined
    ? "CPU warming up" : "CPU partial")
  else if (row.cpuStatus === "warming-up") parts.push("CPU warming up")
  else if (row.cpuStatus === "unavailable") parts.push("CPU unavailable")
  if (sort === "gpu-memory") {
    if (known(row.cpuMachinePercent)) parts.push("CPU " + cpuText(row))
    if (!known(row.gpuMemoryKb)) {
      var gpu = gpuNote(row)
      parts.push(gpu !== "" ? gpu : row.gpuStatus === "none" ? "no GPU clients" : "")
    } else if (row.gpuMemoryOverlap) parts.push("VRAM " + GPU_OVERLAP_TEXT)
  } else {
    var note2 = gpuNote(row)
    if (note2 !== "") parts.push(note2)
  }
  if (row.protected) parts.push("protected")
  return parts.filter(function(p) { return p !== "" }).join(" · ")
}

function accessibleRow(row, totalKb, sort) {
  return row.name + ", size " + Level.footprintText(row) + " PSS plus swap, resident RAM " + ramText(row, totalKb)
    + " of physical memory, CPU " + cpuText(row) + " of all CPUs"
    + (known(row.gpuMemoryKb) ? ", video memory " + gpuShort(row) + (row.gpuMemoryOverlap ? " (" + GPU_OVERLAP_TEXT + ")" : "") : "")
    + ", " + subtitle(row, sort)
}

// Header lines: what each number is measured against.
function legend(meta, totalKb, sort) {
  var cpus = meta && meta.cpu && meta.cpu.onlineCpus ? meta.cpu.onlineCpus + " logical CPUs" : "all logical CPUs"
  return "Size: PSS + swapped · RAM rail: resident PSS of " + (totalKb > 0 ? Level.formatKb(totalKb) : "physical RAM")
    + " · CPU: % of " + cpus
    + (sort === "gpu-memory" ? " · VRAM: device-local GPU memory resident (DRM fdinfo), never added to RAM"
      + (meta && meta.coverage && meta.coverage.gpuOverlap ? "; ≤ sums several clients' references (~ when also missing some)" : "") : "")
}

// Explicit coverage/warm-up/limit states, most important first.
function statusNotes(state, now) {
  var notes = []
  var meta = state.meta
  if (state.status === "loading" || state.status === "pending") notes.push("Scanning processes…")
  else if (state.status === "inactive") notes.push("Waiting for the app scan to start.")
  if (!meta) return notes
  if (meta.demo) notes.push("Demo data: kills only remove the fake row.")
  if (meta.inventory && meta.inventory.complete === false) {
    var reason = meta.inventory.incompleteReason
    notes.push(reason === "process-limit" ? "Incomplete: more than " + meta.inventory.processLimit + " processes; not every process is listed."
      : reason === "group-limit" ? "Incomplete: more than " + meta.inventory.groupLimit + " groups; the top memory and CPU groups are kept."
      : "Incomplete: /proc could not be read.")
  }
  if (meta.cpu) {
    if (meta.cpu.status === "warming-up") notes.push("CPU warming up: rates appear after the next scan (about 2.5 s).")
    else if (meta.cpu.status === "unavailable" && !meta.demo) notes.push("CPU readings are unavailable on this system.")
    else if (meta.cpu.status === "unavailable") notes.push("CPU: no readings in this demo scene.")
  }
  if (meta.stale) notes.push("Showing an older scan; a fresh one is on its way.")
  if (state.sort === "gpu-memory") notes = notes.concat(gpuNotes(state))
  return notes
}

// GPU lens notes: why readings are missing, old or limited.
function gpuNotes(state) {
  var notes = []
  var g = state.meta ? state.meta.gpu : null
  var lens = gpuLens(state)
  if (!lens.available) notes.push("GPU memory: " + lens.reason)
  if (!g) return notes
  if (g.demo) notes.push("GPU readings are synthetic demo data.")
  if (g.status === "stale") notes.push(g.reason === "sample-timeout" ? "A GPU sample took longer than 10 s; its readings are labelled stale until a timely one."
    : g.reason === "sample-failed" ? "The last GPU sample failed; earlier readings are labelled stale."
    : "GPU readings are older than 15 s and labelled stale.")
  if (g.incomplete) notes.push("Incomplete GPU sample (" + g.incomplete + "); some descriptors were not read.")
  var overlapping = state.meta.coverage ? state.meta.coverage.gpuOverlap : 0
  if (overlapping) notes.push("GPU memory: " + overlapping + (overlapping === 1 ? " app's VRAM sums" : " apps' VRAM sum")
    + " several clients that share buffers (≤, or ~ when also missing some); a shared buffer counts once per client, so the order may overstate them.")
  var devices = g.devices || []
  for (var i = 0; i < devices.length; i++) {
    if (devices[i].status === "asleep") notes.push("GPU " + devices[i].id + " is asleep; RAMen does not wake it.")
    if (devices[i].totals && devices[i].totals.crossGroupClients)
      notes.push("GPU " + devices[i].id + ": " + devices[i].totals.crossGroupClients
        + " client(s) shared by several apps are counted once on the device, not in any row.")
  }
  return notes
}

function rangeText(state) {
  if (!state.total) return state.status === "ready" ? (state.query ? "No matching apps" : "No apps") : ""
  var first = state.offset + 1
  var last = state.offset + state.rows.length
  return first + "–" + last + " of " + state.total
}

// --- details ----------------------------------------------------------------

// Details are requested on demand (d / Details) and live only in the view.
// lastGeneration is the view's newest details generation, kept across
// close/reopen, so a late reply to a closed request can never match a later
// one (its request ID would repeat otherwise).
function detailsRequest(lastGeneration, id) {
  return { id: id, generation: (Number(lastGeneration) || 0) + 1, pendingId: "", data: null, error: "" }
}

// Only the pending request's reply, and only for the group it asked about.
// Errors without an id (validation failures) match by request ID alone.
function acceptDetails(details, message) {
  if (!details || !message || message.requestId !== details.pendingId || message.generation !== details.generation)
    return null
  if (message.type === "error" ? message.id !== undefined && message.id !== details.id : message.id !== details.id)
    return null
  if (message.type === "error")
    return copy(details, { pendingId: "", error: String(message.message || message.code).slice(0, 200) })
  return copy(details, { pendingId: "", data: message, error: "" })
}

function memberText(member) {
  var size = member.pss === null || member.pss === undefined ? "–" : Level.formatKb(member.pss + (member.swap || 0))
  var cpu = member.cpuMachinePercent === null || member.cpuMachinePercent === undefined
    ? (member.cpuStatus === "warming-up" ? "CPU warming up" : "CPU –") : "CPU " + percent(member.cpuMachinePercent)
  return member.pid + " · " + member.name + " · " + size + " · " + cpu
}

function commandText(member) {
  if (member.commandStatus === "exited") return "(exited)"
  if (member.command === null || member.command === undefined || member.command === "") return "(command line unavailable)"
  return member.command + (member.commandTruncated ? " …" : "")
}

function footprintBreakdown(row) {
  if (!row) return ""
  if (row.pss === null || row.pss === undefined) return "Memory unavailable (" + coverageText(row.memoryCoverage) + ")"
  var text = Level.footprintText(row) + " = resident PSS " + Level.formatKb(row.pss) + " + swapped " + Level.formatKb(row.swap || 0)
  if (row.memoryStatus === "partial") text += " (lower bound: " + coverageText(row.memoryCoverage) + ")"
  return text
}

function cpuBreakdown(row, meta) {
  if (!row) return ""
  var cpus = meta && meta.cpu && meta.cpu.onlineCpus ? meta.cpu.onlineCpus : null
  var machine = cpuText(row)
  if (row.cpuCorePercent === null || row.cpuCorePercent === undefined) return "CPU " + machine + " (" + coverageText(row.cpuCoverage) + ")"
  return "CPU " + machine + " of " + (cpus ? cpus + " CPUs" : "the machine") + " = " + cpuCoreText(row)
    + (row.cpuStatus === "partial" ? " (" + coverageText(row.cpuCoverage) + ")" : "")
}
