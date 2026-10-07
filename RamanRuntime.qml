import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import "Level.js" as Level
import "Runtime.js" as Runtime
import "HistoryModel.js" as HM

// The one live RAMen runtime: the probe process, its latest snapshots, the
// severity level, theme colors, kill bookkeeping, history requests and the
// IPC target. Service.qml mounts one per shell (Omarchy's `service` plugin
// kind), so every monitor's bar widget and panel share one probe. A widget
// whose bar offers no service creates a private one instead (BarWidget.qml).
//
// Expensive probe work is subscription-driven: each visible panel holds a
// key, and the per-app scan runs while any key asks for detail. Closing one
// panel removes only its own key.
Item {
  id: root
  visible: false

  // "service" when mounted by Service.qml, "widget" for a private fallback.
  property string mode: "widget"
  property bool ipcEnabled: true
  // { open(), close(), toggle() } that reaches the right panel; set by the owner.
  property var panelRouter: null

  // The widget's inline shell.json entry. allowMultiple is false, so every
  // monitor's widget pushes the same object.
  property var settings: ({})
  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  readonly property var thresholds: ({
    warnPercent: Number(setting("warnPercent", 75)),
    criticalPercent: Number(setting("criticalPercent", 90)),
    warnPressure: Number(setting("warnPressure", 5)),
    criticalPressure: Number(setting("criticalPressure", 20))
  })

  // --- probe state ------------------------------------------------------
  property var summary: null
  property var apps: []
  property bool appsLoaded: false
  // Metadata of the newest app inventory snapshot ({snapshot, sampledAt,
  // inventory, cpu, coverage}); null while detail is off. Apps views re-query
  // when its snapshot ID changes.
  property var appsInventory: null
  // id -> { signal, at, error } for kills sent from the panel.
  property var kills: ({})

  property string level: "unknown"
  onSummaryChanged: level = Level.level(summary, thresholds, level)
  property var previousThresholds: null
  onThresholdsChanged: {
    if (previousThresholds && JSON.stringify(previousThresholds) !== JSON.stringify(thresholds))
      resetIncidents(true)
    previousThresholds = thresholds
    level = Level.level(summary, thresholds)
  }
  readonly property real usedPercent: Level.usedPercent(summary)

  property var themeColors: ({})
  readonly property var palette: Level.palette(themeColors, {
    ok: String(setting("colorOk", "")),
    warn: String(setting("colorWarn", "")),
    critical: String(setting("colorCritical", ""))
  })

  // Only live summary messages enter the producer; history replies never do.
  readonly property var historySettings: HM.incidentSettings(settings)
  property bool configured: false
  property string configurationId: ""
  property var incidentState: HM.incidentState(false)
  property string dispatchEpoch: ""
  property int incidentGeneration: 0
  property int incidentRequest: 0
  property var pendingIncidents: ({})
  property string incidentError: ""
  property string notificationError: ""
  property var notificationQueue: []

  function configureHistory() {
    configured = false
    if (!probe.running) return
    configurationId = "config:" + (++incidentRequest)
    sendCommand({ command: "history.configure", requestId: configurationId,
      enabled: historySettings.enabled, persist: historySettings.persist })
  }

  function resetIncidents(closeActive) {
    if (closeActive && incidentState && incidentState.active && summary && !summary.demo) {
      var active = incidentState.active
      sendCommand({ command: "incident.record", requestId: "end:" + (++incidentRequest),
        action: "interrupt", key: active.key, summarySeq: summary.seq,
        severity: active.severity, reason: active.reason, epoch: dispatchEpoch, notify: false })
    }
    var lastNotice = incidentState ? incidentState.lastNotice : null
    var blocked = incidentState ? (incidentState.blocked || (closeActive && !!incidentState.active)) : false
    incidentState = HM.incidentState(blocked, lastNotice)
    incidentGeneration += 1
    pendingIncidents = ({})
    notificationQueue = []
  }

  property var previousHistorySettings: null
  onHistorySettingsChanged: {
    var old = previousHistorySettings
    if (old && (old.enabled !== historySettings.enabled || old.persist !== historySettings.persist))
      resetIncidents(true)
    else if (old && old.mode !== historySettings.mode) {
      incidentGeneration += 1
      pendingIncidents = ({})
      notificationQueue = []
    }
    previousHistorySettings = historySettings
    if (configurationId !== "" && (!old || old.enabled !== historySettings.enabled || old.persist !== historySettings.persist))
      configureHistory()
  }

  function produceIncident(message) {
    var d = message.dispatch
    if (!configured || message.historyConfigId !== configurationId || !d) return
    if (message.demo !== (summary ? summary.demo : false)) resetIncidents(true)
    if (d.epoch !== dispatchEpoch) {
      resetIncidents(false)
      dispatchEpoch = d.epoch || ""
      var elapsed = d.noticeElapsed || {}
      incidentState = HM.incidentState(d.blocked, {
        warn: typeof elapsed.warn === "number" ? message.monotonic - elapsed.warn : null,
        critical: typeof elapsed.critical === "number" ? message.monotonic - elapsed.critical : null
      })
    }
    var result = HM.incidentStep(incidentState, message, thresholds, settings)
    incidentState = result.state
    for (var i = 0; i < result.events.length; i++) {
      var event = result.events[i]
      var id = "i:" + incidentGeneration + ":" + (++incidentRequest)
      if (event.notify) {
        var next = Object.assign({}, pendingIncidents)
        next[id] = { event: event, epoch: dispatchEpoch, generation: incidentGeneration,
          deadline: message.monotonic + 5, deadlineWall: Date.now() + 5000 }
        pendingIncidents = next
      }
      sendCommand(Object.assign({}, event, { command: "incident.record", requestId: id,
        epoch: dispatchEpoch, thresholds: thresholds }))
    }
    // Expired acknowledgements never toast retroactively.
    var pending = {}
    for (var key in pendingIncidents)
      if (pendingIncidents[key].deadline >= message.monotonic) pending[key] = pendingIncidents[key]
    pendingIncidents = pending
  }

  Timer {
    interval: 1000
    running: Object.keys(root.pendingIncidents).length > 0
    repeat: true
    onTriggered: {
      var next = {}, expired = false
      for (var key in root.pendingIncidents) {
        if (Date.now() <= root.pendingIncidents[key].deadlineWall) next[key] = root.pendingIncidents[key]
        else expired = true
      }
      root.pendingIncidents = next
      if (expired) root.incidentError = "Incident acknowledgement timed out; no notification sent."
    }
  }

  function acceptIncident(message) {
    if (message.type === "incident") incidentError = message.error || ""
    var entry = pendingIncidents[message.requestId]
    if (!entry) {
      if (message.type === "error" && /^(i:|end:)/.test(String(message.requestId)))
        incidentError = String(message.message || message.code)
      return
    }
    var next = Object.assign({}, pendingIncidents)
    delete next[message.requestId]
    pendingIncidents = next
    if (message.type === "error") { incidentError = String(message.message || message.code); return }
    if (!message.notify || message.demo || !message.incident || entry.epoch !== dispatchEpoch
        || message.epoch !== dispatchEpoch || entry.generation !== incidentGeneration
        || !historySettings.enabled || historySettings.mode === "off" || !summary
        || summary.demo || !summary.dispatchOwner || summary.monotonic > entry.deadline
        || Date.now() > entry.deadlineWall) return
    if (historySettings.mode === "critical" && message.incident.severity !== "critical") return
    incidentError = message.error || ""
    notificationQueue = notificationQueue.concat([{ id: message.incident.id, ack: message, hold: entry.event.hold,
      severity: message.incident.severity, epoch: dispatchEpoch }]).slice(-2)
    dispatchNotification()
  }

  function dispatchNotification() {
    if (notifier.running || !notificationQueue.length) return
    var entry = notificationQueue[0]
    notificationQueue = notificationQueue.slice(1)
    if (!summary || summary.demo || !summary.dispatchOwner || entry.epoch !== dispatchEpoch
        || !historySettings.enabled || historySettings.mode === "off") return
    var toast = HM.incidentToast(entry.ack, entry.hold, { detail: root.detail, now: Date.now() })
    notifier.command = ["omarchy-notification-send", "--app-name", "RAMen", "-u",
      entry.severity === "critical" ? "critical" : "normal", "-t", "10000",
      toast.title, toast.body, "--exec", "omarchy-shell", "boundsj.raman", "openIncident", entry.id]
    notifier.running = true
  }

  Process {
    id: notifier
    onExited: function(code, status) {
      root.notificationError = code === 0 ? "" : "Notification helper failed (exit " + code + "). Open History or use openIncident IPC."
      root.dispatchNotification()
    }
  }

  // --- subscriptions ----------------------------------------------------
  property var subscriptions: ({})
  readonly property bool detail: Runtime.wants(subscriptions, "detail")
  // GPU client sampling: only a visible Apps page with gpuMetrics auto asks for it.
  readonly property bool gpu: Runtime.wants(subscriptions, "gpu")
  property int _clients: 0
  readonly property string clientNamespace: Date.now().toString(36) + Math.random().toString(36).slice(2, 10)

  // A fresh key for a panel or view; keys are never reused within a runtime.
  function newClientId(prefix) {
    _clients += 1
    return String(prefix || "c").slice(0, 12) + clientNamespace + ":" + _clients
  }

  function subscribe(key, wants) {
    subscriptions = Runtime.setSubscription(subscriptions, key, wants)
  }

  function unsubscribe(key) {
    subscriptions = Runtime.removeSubscription(subscriptions, key)
  }

  onDetailChanged: {
    if (!detail) {
      appsLoaded = false
      appsInventory = null
    }
    send("detail " + (detail ? "1" : "0"))
  }
  onGpuChanged: send("gpu " + (gpu ? "1" : "0"))

  // The History window last chosen in this session (any panel).
  property string historyWindow: "1h"
  property var storageScope: null
  // memory | cpu | gpu-memory; AppsModel.defaultSort makes an unavailable one fall back visibly.
  readonly property string appsDefaultSort: String(setting("appsDefaultSort", "memory"))
  // off | auto: auto samples GPU clients only while an Apps page is visible.
  readonly property string gpuMetrics: String(setting("gpuMetrics", "auto")) === "off" ? "off" : "auto"
  readonly property bool storageShowMap: setting("storageShowMap", true) !== false
  readonly property bool storageRememberScope: setting("storageRememberScope", true) !== false
  signal storageMessage(var message)
  signal storageRestarted()
  signal storageReady()
  readonly property bool storageHelperAvailable: probe.running && probe.processId > 0

  // A page asked for over IPC; the next panel to open shows it.
  property string requestedPage: ""
  property string requestedIncidentId: ""
  function openIncident(id) {
    requestedIncidentId = String(id).slice(0, 64)
    historyWindow = "24h"
    requestedPage = "history"
    routePanel("open")
  }
  function takeRequestedPage() {
    var page = requestedPage
    requestedPage = ""
    return page
  }
  function takeRequestedIncident() {
    var id = requestedIncidentId
    requestedIncidentId = ""
    return id
  }

  // Bar widgets currently attached (one per monitor), for status/geometry.
  property var widgets: []
  function attach(widget) {
    if (widgets.indexOf(widget) < 0) widgets = widgets.concat([widget])
    if (configurationId === "") configureHistory()
  }
  function detach(widget) {
    widgets = widgets.filter(function(w) { return w !== widget })
  }

  // --- commands -----------------------------------------------------------
  readonly property string probePath: decodeURIComponent(String(Qt.resolvedUrl("raman_probe.py")).replace(/^file:\/\//, ""))

  function send(line) {
    if (!probe.running) return false
    probe.write(line + "\n")
    return true
  }

  // JSON-object commands (docs/protocol.md); the legacy commands stay plain words.
  function sendCommand(message) { return send(JSON.stringify(message)) }

  function refresh() { send("refresh") }

  // Clears saved and in-memory history (in every RAMen probe); live sampling continues.
  function clearHistory() {
    resetIncidents(true)
    sendCommand({ command: "history.clear", requestId: "clear-" + Date.now() })
  }

  // Ask for one history window. Returns the request id; the reply arrives
  // through historyMessage, and the caller drops replies with any other id.
  function requestHistory(client, generation, window) {
    var id = Runtime.historyRequestId(client, generation)
    if (!probe.running) return ""
    sendCommand({ command: "history.get", requestId: id, window: window })
    return id
  }

  // Replies to JSON commands (history, history-cleared, error), for whichever
  // view sent them.
  signal historyMessage(var message)

  // One page of the cached app inventory (docs/protocol.md, apps.query). It
  // never starts a process scan: a view must hold a detail subscription, and
  // Memory and Apps share that one scan. Returns the request id; the reply
  // arrives through appsMessage and views keep only their newest id.
  function requestApps(client, generation, params) {
    if (!probe.running) return ""
    var command = Runtime.appsQuery(client, generation, params)
    sendCommand(command)
    return command.requestId
  }
  signal appsMessage(var message)

  // Legacy kill (the Memory page): signals the group from the last scan.
  function killApp(id, force) {
    var next = Object.assign({}, root.kills)
    next[id] = { signal: force ? "KILL" : "TERM", at: Date.now(), error: "" }
    root.kills = next
    send("kill " + (force ? "KILL" : "TERM") + " " + id)
  }

  // The Apps page's kill: the probe signals only if the group's membership is
  // still the generation that was armed, re-checking every PID first.
  property int _appsKills: 0
  function killAppMember(client, id, membership, force) {
    if (!probe.running) return false
    var next = Object.assign({}, root.kills)
    next[id] = { signal: force ? "KILL" : "TERM", at: Date.now(), error: "", scope: "apps" }
    root.kills = next
    return sendCommand(Runtime.appsKill(client, ++_appsKills, id, membership, force))
  }

  // On-demand details for one group (command lines read now, never stored here).
  function requestAppDetails(client, generation, id) {
    if (!probe.running) return ""
    var command = Runtime.appsDetails(client, generation, id)
    sendCommand(command)
    return command.requestId
  }

  function handleLine(line) {
    var message
    try { message = JSON.parse(line) } catch (e) { return }
    var stream = Runtime.replyStream(message)
    if (stream === "storage") {
      root.storageMessage(message)
      return
    }
    if (stream === "apps") {
      root.appsMessage(message)
      return
    }
    if (message.type === "summary") {
      root.produceIncident(message)
      root.summary = message
    } else if (message.type === "apps") {
      if (!root.detail) return // a late scan from before the last panel closed
      root.apps = message.apps || []
      root.appsLoaded = true
      root.appsInventory = message.snapshot === undefined ? null : {
        snapshot: message.snapshot, sampledAt: message.sampledAt, inventory: message.inventory,
        cpu: message.cpu, coverage: message.coverage, gpu: message.gpu || null }
      root.kills = Runtime.pruneKills(root.kills, root.apps, Date.now())
    } else if (message.type === "killed") {
      var next = Object.assign({}, root.kills)
      var entry = next[message.id] || { signal: message.signal, at: Date.now() }
      entry.error = message.error || (message.failed > 0 && message.sent === 0 ? "permission denied" : "")
      next[message.id] = entry
      root.kills = next
    } else if (message.type === "incident") {
      root.acceptIncident(message)
    } else if (message.type === "history-configured") {
      if (message.requestId === configurationId && message.enabled === historySettings.enabled
          && message.persist === historySettings.persist) configured = true
    } else if (message.type === "history" || message.type === "history-cleared" || message.type === "error") {
      if (message.type === "error") root.acceptIncident(message)
      if (message.type === "history-cleared") {
        if (root.incidentState.active) root.incidentState.blocked = true
        root.resetIncidents(false)
      }
      if (Runtime.replyStream(message) === "history") root.historyMessage(message)
    }
  }

  function status() {
    return {
      level: root.level,
      summary: root.summary,
      runtime: {
        mode: root.mode,
        probePid: probe.running ? probe.processId : null,
        widgets: root.widgets.length,
        subscribers: Runtime.subscriberKeys(root.subscriptions),
        detail: root.detail,
        gpu: root.gpu,
        gpuMetrics: root.gpuMetrics,
        history: root.historySettings,
        incidentPhase: root.incidentState.phase,
        incidentBlocked: root.incidentState.blocked,
        incidentError: root.incidentError,
        notificationError: root.notificationError,
        dispatch: root.summary ? root.summary.dispatch : null
      }
    }
  }

  Process {
    id: probe
    command: ["python3", root.probePath, "--defer-history"]
    running: true
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }
    onStarted: {
      root.resetIncidents(false)
      root.dispatchEpoch = ""
      if (root.widgets.length > 0 || root.configurationId !== "") root.configureHistory()
      if (root.gpu) root.send("gpu 1")
      if (root.detail) root.send("detail 1")
      root.storageReady()
    }
    onExited: {
      root.configured = false
      root.configurationId = ""
      root.resetIncidents(false)
      root.appsLoaded = false
      root.appsInventory = null
      root.storageRestarted()
      restartTimer.start()
    }
  }

  Timer {
    id: restartTimer
    interval: 3000
    onTriggered: probe.running = true
  }

  FileView {
    id: themeFile
    path: Quickshell.env("HOME") + "/.local/state/omarchy/current/theme/colors.toml"
    watchChanges: true
    printErrors: false
    onLoaded: root.themeColors = Level.parseThemeColors(text())
    onFileChanged: reload()
  }

  // Theme switches swap the directory under the path; Color reloading is the
  // reliable signal that it happened.
  Connections {
    target: Color
    function onUrgentChanged() { themeFile.reload() }
    function onAccentChanged() { themeFile.reload() }
  }

  function routePanel(method) {
    if (root.panelRouter && typeof root.panelRouter[method] === "function") root.panelRouter[method]()
  }

  IpcHandler {
    target: "boundsj.raman"
    enabled: root.ipcEnabled

    function open(): void { root.routePanel("open") }
    function close(): void { root.routePanel("close") }
    function show(): void { root.routePanel("open") }
    function hide(): void { root.routePanel("close") }
    function toggle(): void { root.routePanel("toggle") }
    // Open the panel on a page: "memory", "history", "storage" or "apps".
    function page(name: string): void {
      root.requestedPage = name
      root.routePanel("open")
    }
    function openIncident(id: string): void { root.openIncident(id) }
    function refresh(): void { root.refresh() }
    // Replay a canned scene from docs/demo-scenes.json ("off" to go live).
    function demo(scene: string): void { root.resetIncidents(true); root.send("demo " + scene) }
    function clearHistory(): void { root.clearHistory() }
    // Screen-space bounds of the first bar item (logical px), for scripts/screenshots.sh.
    function geometry(): string {
      var widget = root.widgets.length > 0 ? root.widgets[0] : null
      return widget && typeof widget.geometry === "function" ? widget.geometry() : "{}"
    }
    function status(): string {
      return JSON.stringify(root.status())
    }
  }
}
