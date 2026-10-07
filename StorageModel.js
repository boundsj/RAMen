.pragma library

// Bytes, identities and geometry only; labels never become navigation paths.
function bytes(value) {
  if (value === null || value === undefined) return "unknown"
  var n = Math.max(0, Number(value)), units = ["B", "KiB", "MiB", "GiB", "TiB"]
  var i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return (i ? n.toFixed(1) : String(n)) + " " + units[i]
}
function age(snapshot, now) {
  if (!snapshot) return "Unscanned — choose Scan"
  var seconds = Math.max(0, Math.floor(now / 1000 - snapshot.capturedAt))
  return snapshot.coverage + " scan · " + (seconds < 60 ? seconds + "s" : seconds < 3600 ? Math.floor(seconds / 60) + "m" : Math.floor(seconds / 3600) + "h") + " ago"
}
function sameIdentity(a, b) {
  if (!a || !b) return false
  var keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every(function(k) { return a[k] === b[k] })
}
function failure(code) {
  if (code === "invalid-path")
    return { state: "invalid-path", invalidates: true, text: "Invalid scope — choose an absolute directory path" }
  if (["missing-root", "missing-volume"].indexOf(code) >= 0)
    return { state: "missing", invalidates: true, text: "Scope missing — reconnect or select a folder, then scan explicitly" }
  if (code === "root-changed")
    return { state: "changed", invalidates: true, text: "Scope changed or remounted — scan explicitly; old cache unavailable" }
  if (["unsupported-platform", "unsupported-filesystem", "identity-unavailable"].indexOf(code) >= 0)
    return { state: "unsupported", invalidates: true, text: "Scope unsupported — safe storage identity or filesystem support unavailable" }
  if (["snapshot-not-active", "invalid-snapshot", "unsafe-cache"].indexOf(code) >= 0)
    return { state: "cache-invalid", invalidates: true, text: "Cached snapshot unavailable — reload the selected cache or scan explicitly" }
  if (code === "stale-snapshot")
    return { state: "cache-invalid", invalidates: true, text: "Cached snapshot expired or exceeds limits — scan explicitly" }
  if (/limit$/.test(code))
    return { state: "limited", invalidates: false, text: "Storage limit reached — choose a narrower folder" }
  if (["worker-failed", "worker-timeout", "storage-unavailable", "owner-timeout"].indexOf(code) >= 0)
    return { state: "helper-failed", invalidates: false, text: "Storage helper failed — retry" }
  return { state: "failed", invalidates: false, text: "Storage request failed" }
}
function selected(rows, id) {
  for (var i = 0; i < (rows || []).length; i++) if (rows[i].nodeId === id) return rows[i]
  return null
}
function colorIndex(id) {
  var hash = 0
  for (var i = 0; i < String(id).length; i++) hash = (hash * 31 + String(id).charCodeAt(i)) >>> 0
  return hash % 5
}
// Three fixed-height levels in one frame. Each child occupies its share of
// its parent's known bytes, including measured directory self-allocation.
// Unknown allocation is never fabricated; zero entries remain in the ledger.
// Tiny entries and the backend continuation become one real-members Other.
function bands(reply, loaded, width, height) {
  var out = [], budget = 60
  function level(data, x, w, depth) {
    if (!data || depth >= 3 || budget <= 0) return
    var total = Math.max(0, Number(data.node.knownAllocatedBytes) || 0)
    if (!total || !w) return
    var entries = [], tiny = [], tinyBytes = 0
    var self = Math.max(0, Number(data.node.ownAllocatedBytes) || 0)
    if (self) entries.push({ kind: "self", nodeId: data.nodeId, name: "Folder itself", knownAllocatedBytes: self })
    for (var i = 0; i < data.segments.length; i++) {
      var row = data.segments[i]
      if (w * row.knownAllocatedBytes / total < 3) { tiny.push(row.nodeId); tinyBytes += row.knownAllocatedBytes }
      else entries.push(row)
    }
    if (data.other || tiny.length) entries.push({ kind: "other", name: "Other", nodeId: data.nodeId,
      knownAllocatedBytes: tinyBytes + (data.other ? data.other.knownAllocatedBytes : 0),
      members: tiny, offset: data.other ? data.other.offset : null, filter: data.filter })
    // Preserve accounting if the shared map segment budget is exhausted.
    if (entries.length > budget) {
      var omitted = entries.splice(Math.max(0, budget - 1)), amount = 0, members = []
      for (var q = 0; q < omitted.length; q++) { amount += omitted[q].knownAllocatedBytes; members = members.concat(omitted[q].members || [omitted[q].nodeId]) }
      entries.push({ kind: "other", name: "Other", nodeId: data.nodeId, knownAllocatedBytes: amount, members: members, offset: data.other ? data.other.offset : null, filter: data.filter })
    }
    var cursor = x
    for (var j = 0; j < entries.length && budget > 0; j++) {
      var entry = entries[j], size = w * entry.knownAllocatedBytes / total
      out.push({ x: cursor, y: depth * height, width: size, height: height - 4,
        nodeId: entry.nodeId, parentId: data.nodeId, row: entry, depth: depth,
        color: colorIndex(entry.nodeId), name: entry.name })
      budget--
      cursor += size
    }
    // Only cached child replies are drawn, no recursive filesystem work.
    for (var k = 0; k < out.length; k++) {
      var segment = out[k]
      if (segment.depth === depth && segment.row.kind === "directory" && segment.parentId === data.nodeId)
        level(loaded[segment.nodeId], segment.x, segment.width, depth + 1)
    }
  }
  level(reply, 0, Math.max(0, width), 0)
  return out
}
function state(client) { return { clientId: client, generation: 1, sequence: 0, pending: {} } }
function advance(s) { return { clientId: s.clientId, generation: s.generation + 1, sequence: s.sequence, pending: {} } }
function request(s, command, args, purpose) {
  var next = { clientId: s.clientId, generation: s.generation, sequence: s.sequence + 1, pending: Object.assign({}, s.pending) }
  var id = "s:" + s.clientId + ":" + next.sequence
  next.pending[id] = { command: command, purpose: purpose || command, args: args || {}, scanId: args ? args.scanId : null }
  return { state: next, message: Object.assign({}, args || {}, { command: command, clientId: s.clientId, generation: s.generation, requestId: id }) }
}
function accept(s, message) {
  var entry = message && s.pending[message.requestId]
  if (!entry || message.clientId !== s.clientId || message.generation !== s.generation) return null
  // Controller command validation errors are correlated before a scan can be
  // resolved and therefore omit scanId. Never forgive an explicit wrong ID.
  if (entry.scanId && message.scanId !== entry.scanId
      && !(message.type === "error" && message.scanId === undefined)) return null
  if (message.type === "storage-children" && (message.snapshotId !== entry.args.snapshotId
      || (entry.args.nodeId && message.nodeId !== entry.args.nodeId)
      || message.offset !== entry.args.offset || message.filter !== entry.args.filter)) return null
  if (message.type === "storage-action" && (message.snapshotId !== entry.args.snapshotId
      || message.nodeId !== entry.args.nodeId || message.action !== entry.args.action)) return null
  if (message.type !== "error" && message.type !== "storage-progress") {
    var expected = { "storage.mounts": "storage-capacity", "storage.children": "storage-children", "storage.action": "storage-action" }[entry.command] || "storage-result"
    if (message.type !== expected) return null
  }
  var next = Object.assign({}, s, { pending: Object.assign({}, s.pending) })
  if (message.type === "storage-progress") next.pending[message.requestId] = Object.assign({}, entry, { scanId: message.scanId })
  else delete next.pending[message.requestId]
  return { state: next, entry: entry }
}

function commandFits(message) {
  try { return encodeURIComponent(JSON.stringify(message)).replace(/%[0-9A-F]{2}/g, "x").length + 1 <= 4096 }
  catch (e) { return false }
}
