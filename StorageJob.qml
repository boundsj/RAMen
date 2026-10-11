import QtQuick
import "StorageModel.js" as SM

// Session-owned job, independent of any popup or monitor. Only an explicit
// Start/Continue schedules another bounded worker slice. Closing a view does
// not revoke that choice; Pause waits for the current checkpoint to commit.
Item {
  id: root
  property var runtime: null
  property var session: null
  property var scope: null
  property var requestedScope: null
  property var snapshot: null
  property bool running: false
  property bool inFlight: false
  property string error: ""
  signal updated(var snapshot, var scope)

  function send(command, args, purpose) {
    if (!runtime || !runtime.storageHelperAvailable) return false
    if (!session) session = SM.state(runtime.newClientId("job"))
    var result = SM.request(session, command, args, purpose)
    if (!SM.commandFits(result.message) || !runtime.sendCommand(result.message)) return false
    session = result.state; inFlight = true
    return true
  }
  function restore() {
    running = false
    if (!inFlight) send("storage.job", {}, "restore")
  }
  function start(value) {
    if (running || inFlight) return false
    requestedScope = value; error = ""; running = true
    if (!send("storage.start", value, "start")) {
      running = false; error = "storage-unavailable: helper not ready or request too large"
      updated(null, requestedScope); requestedScope = null
      return false
    }
    return true
  }
  function resume(value, valueScope) {
    if (running || inFlight || !value || !value.job || value.pauseReason) return false
    snapshot = value; scope = valueScope || SM.scopeFrom(value); running = true; error = ""
    nextStep()
    return true
  }
  function pause() {
    running = false
    if (!inFlight && snapshot && snapshot.status !== "ready") send("storage.pause", { jobId: snapshot.snapshotId }, "pause")
  }
  function nextStep() {
    if (running && !inFlight && snapshot) {
      if (!send("storage.step", { jobId: snapshot.snapshotId }, "step")) {
        running = false; error = "storage-unavailable: helper not ready"
        updated(snapshot, scope)
      }
    }
  }
  function accept(message) {
    var result = session ? SM.accept(session, message) : null
    if (!result) return
    session = result.state
    if (message.type === "storage-progress") return
    inFlight = false
    if (message.type === "error") {
      running = false; error = message.code + ": " + message.message
      updated(result.entry.purpose === "start" ? null : snapshot, result.entry.purpose === "start" ? requestedScope : scope)
      requestedScope = null
      return
    }
    if (message.snapshot) {
      snapshot = message.snapshot
      scope = result.entry.purpose === "start" ? requestedScope : scope || SM.scopeFrom(snapshot)
      requestedScope = null
    }
    if (snapshot && (snapshot.status === "ready" || snapshot.pauseReason)) running = false
    updated(snapshot, scope)
    if (running) Qt.callLater(root.nextStep)
    else if (snapshot && snapshot.status === "scanning" && result.entry.purpose === "step") pause()
  }
  Connections {
    target: root.runtime
    function onStorageMessage(message) { root.accept(message) }
    function onStorageReady() { Qt.callLater(root.restore) }
    function onStorageRestarted() {
      root.running = false; root.inFlight = false
      if (root.session) root.session = SM.advance(root.session)
      root.snapshot = null; root.error = "The helper restarted. Saved progress can be continued explicitly."
      root.updated(null, root.scope)
    }
  }
  Component.onCompleted: if (runtime && runtime.storageHelperAvailable) restore()
}
