import QtQuick
import QtTest
import "../.."

TestCase {
  name: "Runtime"

  Component { id: runtimeComponent; RamanRuntime {} }
  Component { id: serviceComponent; Service {} }

  function probeOf(rt) {
    for (var i = 0; i < rt.data.length; i++) if (rt.data[i].stdinEnabled === true) return rt.data[i]
    return null
  }
  function lines(rt) { return probeOf(rt).written.map(function(l) { return l.trim() }) }

  function test_subscriptions_across_panels() {
    var rt = createTemporaryObject(runtimeComponent, null)
    verify(rt)
    compare(rt.detail, false)
    var a = rt.newClientId("panel"), b = rt.newClientId("panel")
    verify(a !== b)
    rt.subscribe(a, { detail: true })
    rt.subscribe(b, { detail: true })
    compare(rt.detail, true)
    compare(lines(rt).filter(function(l) { return l === "detail 1" }).length, 1, "detail is enabled once")
    rt.unsubscribe(a)
    compare(rt.detail, true, "the other panel keeps its subscription")
    compare(lines(rt).indexOf("detail 0"), -1)
    rt.subscribe(b, { history: true })
    compare(rt.detail, false)
    compare(lines(rt)[lines(rt).length - 1], "detail 0")
  }

  function test_apps_dropped_without_detail() {
    var rt = createTemporaryObject(runtimeComponent, null)
    probeOf(rt).feed(JSON.stringify({ type: "apps", apps: [{ id: "x", pss: 1, swap: 0 }] }))
    compare(rt.apps.length, 0, "a late scan after the last panel closed is ignored")
    rt.subscribe("p", { detail: true })
    probeOf(rt).feed(JSON.stringify({ type: "apps", apps: [{ id: "x", pss: 1, swap: 0 }] }))
    compare(rt.apps.length, 1)
    compare(rt.appsLoaded, true)
    rt.unsubscribe("p")
    compare(rt.appsLoaded, false)
  }

  function test_apps_queries_have_their_own_stream() {
    var rt = createTemporaryObject(runtimeComponent, null)
    var apps = [], history = [], storage = []
    rt.appsMessage.connect(function(m) { apps.push(m) })
    rt.historyMessage.connect(function(m) { history.push(m) })
    rt.storageMessage.connect(function(m) { storage.push(m) })
    var id = rt.requestApps("v1", 2, { query: "chrome", sort: "cpu", limit: 999 })
    compare(id, "a:v1:2")
    var sent = JSON.parse(lines(rt)[lines(rt).length - 1])
    compare(sent.command, "apps.query")
    compare(sent.sort, "cpu")
    compare(sent.generation, 2)
    compare(sent.limit, 50)
    compare(lines(rt).indexOf("detail 1"), -1, "a query never enables the scan by itself")
    probeOf(rt).feed(JSON.stringify({ type: "apps-page", requestId: id, generation: 2, status: "inactive", rows: [] }))
    probeOf(rt).feed(JSON.stringify({ type: "error", requestId: "a:v1:3", code: "bad-query", message: "x" }))
    compare(apps.length, 2)
    compare(history.length, 0, "Apps errors never reach History")
    compare(storage.length, 0)
    compare(rt.incidentError, "", "or the incident producer")
  }

  function test_apps_inventory_metadata_follows_detail() {
    var rt = createTemporaryObject(runtimeComponent, null)
    rt.subscribe("p", { detail: true })
    probeOf(rt).feed(JSON.stringify({ type: "apps", apps: [], snapshot: 7, sampledAt: 1791063600,
      inventory: { complete: true, groups: 40 }, cpu: { status: "available", onlineCpus: 8 }, coverage: {} }))
    compare(rt.appsInventory.snapshot, 7)
    compare(rt.appsInventory.cpu.onlineCpus, 8)
    rt.unsubscribe("p")
    compare(rt.appsInventory, null, "closing the last detail subscriber drops the snapshot reference")
  }

  function test_history_messages_routed() {
    var rt = createTemporaryObject(runtimeComponent, null)
    var got = []
    rt.historyMessage.connect(function(m) { got.push(m) })
    var id = rt.requestHistory("v1", 3, "1h")
    compare(id, "h:v1:3")
    var sent = JSON.parse(lines(rt)[lines(rt).length - 1])
    compare(sent.command, "history.get")
    compare(sent.window, "1h")
    compare(sent.requestId, id)
    probeOf(rt).feed(JSON.stringify({ type: "history", requestId: id, t: [] }))
    probeOf(rt).feed(JSON.stringify({ type: "summary", total: 100, used: 50, available: 50, psiSome10: 0 }))
    compare(got.length, 1)
    compare(rt.summary.used, 50)
    compare(rt.level, "ok")
  }

  function test_thresholds_from_settings() {
    var rt = createTemporaryObject(runtimeComponent, null)
    rt.settings = { warnPercent: 40 }
    probeOf(rt).feed(JSON.stringify({ type: "summary", total: 100, used: 50, available: 50, psiSome10: 0 }))
    compare(rt.level, "warn")
  }

  function test_service_routes_panel_ipc_through_host() {
    var calls = []
    var svc = createTemporaryObject(serviceComponent, null)
    svc.shell = {
      summon: function(id) { calls.push("summon " + id); return true },
      hide: function(id) { calls.push("hide " + id); return true },
      toggle: function(id) { calls.push("toggle " + id); return true }
    }
    compare(svc.runtime.mode, "service")
    svc.runtime.routePanel("toggle")
    svc.runtime.routePanel("open")
    svc.runtime.routePanel("close")
    compare(calls, ["toggle boundsj.raman", "summon boundsj.raman", "hide boundsj.raman"])
  }
  function notifierOf(rt) {
    for (var i = 0; i < rt.data.length; i++)
      if (rt.data[i].written !== undefined && rt.data[i].stdinEnabled === false) return rt.data[i]
    return null
  }
  function live(rt, t, extra) {
    rt.handleLine(JSON.stringify(Object.assign({ type: "summary", total: 100, used: 95, available: 5,
      psiSome10: 25, monotonic: t, time: 1000 + t, seq: t + 1, session: "test", demo: false,
      historyConfigId: rt.configurationId,
      dispatchOwner: true, dispatch: { owner: true, epoch: "epoch1", blocked: false, noticeElapsed: {} } }, extra || {})))
  }
  function configured(rt) {
    rt.handleLine(JSON.stringify({ type: "history-configured", requestId: rt.configurationId,
      enabled: rt.historySettings.enabled, persist: rt.historySettings.persist }))
  }
  function startIncident(rt) {
    rt.settings = { alertMode: "warning-and-critical", alertHoldSeconds: 2 }
    rt.configureHistory()
    configured(rt)
    live(rt, 0); live(rt, 2)
    var commands = lines(rt).filter(function(l) { return l.indexOf("incident.record") >= 0 })
    return JSON.parse(commands[commands.length - 1])
  }
  function ack(rt, event, extra) {
    rt.handleLine(JSON.stringify(Object.assign({ type: "incident", requestId: event.requestId,
      notify: true, epoch: "epoch1", demo: false, persisted: true,
      incident: { id: "test:1", severity: "critical", reason: "pressure",
        measurement: { total: 100, used: 95, psiSome10: 25, available: 5 }, context: { status: "not-observed" } } }, extra || {})))
  }
  function test_ack_before_toast_and_duplicate_drop() {
    var rt = createTemporaryObject(runtimeComponent, null)
    var event = startIncident(rt), notice = notifierOf(rt)
    compare(event.action, "start")
    compare(notice.startedCommands.length, 0, "no toast before acknowledgement")
    ack(rt, event, { persisted: false })
    compare(notice.startedCommands.length, 1)
    verify(notice.command[notice.command.length - 7].indexOf("not saved") >= 0 || notice.command.join(" ").indexOf("not saved") >= 0)
    compare(notice.command.slice(-5), ["--exec", "omarchy-shell", "boundsj.raman", "openIncident", "test:1"])
    ack(rt, event)
    compare(notice.startedCommands.length, 1, "duplicate acknowledgement cannot toast")
    for (var t = 4; t <= 80; t += 2) live(rt, t)
    compare(notice.startedCommands.length, 1, "no repeat when cooldown expires")
    compare(lines(rt).filter(function(l) { return l.indexOf("detail 1") >= 0 }).length, 0, "closed-panel incident never scans")
  }
  function test_invalidated_acks_demo_owner_config_clear_and_timeout() {
    var cases = ["demo", "owner", "settings", "clear", "timeout", "error"]
    for (var i = 0; i < cases.length; i++) {
      var rt = createTemporaryObject(runtimeComponent, null), event = startIncident(rt)
      if (cases[i] === "demo") live(rt, 4, { demo: true })
      if (cases[i] === "owner") live(rt, 4, { dispatchOwner: false, dispatch: { owner: false, epoch: "", blocked: true } })
      if (cases[i] === "settings") rt.settings = { alertMode: "off" }
      if (cases[i] === "clear") rt.clearHistory()
      if (cases[i] === "timeout") {
        var pending = Object.assign({}, rt.pendingIncidents)
        pending[event.requestId].deadlineWall = Date.now() - 1
        rt.pendingIncidents = pending
      }
      if (cases[i] === "error") rt.handleLine(JSON.stringify({ type: "error", requestId: event.requestId, message: "test failure" }))
      ack(rt, event)
      compare(notifierOf(rt).startedCommands.length, 0, cases[i])
    }
  }
  function test_takeover_waits_for_real_recovery_and_ipc_routes() {
    var rt = createTemporaryObject(runtimeComponent, null)
    rt.settings = { alertMode: "critical", alertHoldSeconds: 2 }
    rt.configureHistory()
    configured(rt)
    for (var t = 0; t <= 10; t += 2)
      live(rt, t, { dispatch: { owner: true, epoch: "restart", blocked: true, noticeElapsed: {} } })
    compare(lines(rt).filter(function(l) { return l.indexOf("incident.record") >= 0 }).length, 0)
    live(rt, 12, { used: 50, psiSome10: 0, dispatch: { epoch: "restart" } })
    live(rt, 14, { dispatch: { epoch: "restart" } }); live(rt, 16, { dispatch: { epoch: "restart" } })
    compare(rt.incidentState.phase, "active")
    var calls = 0
    rt.panelRouter = { open: function() { calls += 1 } }
    rt.openIncident("test:1")
    compare(calls, 1)
    compare(rt.requestedPage, "history")
    compare(rt.requestedIncidentId, "test:1")
    compare(rt.historyWindow, "24h")
  }

  function test_alert_policy_change_keeps_active_incident() {
    var rt = createTemporaryObject(runtimeComponent, null)
    startIncident(rt)
    var key = rt.incidentState.active.key
    rt.settings = { alertMode: "off", alertHoldSeconds: 2 }
    compare(rt.incidentState.phase, "active")
    compare(rt.incidentState.active.key, key)
    live(rt, 4)
    compare(rt.incidentState.active.key, key)
    var latest = JSON.parse(lines(rt)[lines(rt).length - 1])
    compare(latest.action, "extend")
    compare(latest.notify, false)
    rt.settings = { alertMode: "off", alertHoldSeconds: 2, warnPercent: 60 }
    compare(rt.incidentState.active, null, "a real threshold change interrupts old continuity")
  }

  function test_null_psi_keeps_ram_dwell_and_active_hysteresis() {
    var rt = createTemporaryObject(runtimeComponent, null)
    rt.settings = { alertMode: "warning-and-critical", alertHoldSeconds: 2 }
    rt.configureHistory(); configured(rt)
    live(rt, 0, { used: 80, psiSome10: null })
    live(rt, 2, { used: 74, psiSome10: null })
    var commands = lines(rt).filter(function(l) { return l.indexOf("incident.record") >= 0 })
    compare(commands.length, 1)
    compare(JSON.parse(commands[0]).action, "start")
    compare(JSON.parse(commands[0]).reason, "used")
    var key = rt.incidentState.active.key
    live(rt, 4, { used: 74, psiSome10: null })
    compare(rt.incidentState.active.key, key)
    commands = lines(rt).filter(function(l) { return l.indexOf("incident.record") >= 0 })
    compare(JSON.parse(commands[commands.length - 1]).action, "extend")
    live(rt, 6, { used: 71, psiSome10: null })
    compare(rt.incidentState.active, null)
  }

  function test_configuration_ack_and_sample_generation_gate_dwell() {
    var rt = createTemporaryObject(runtimeComponent, null)
    rt.settings = { alertMode: "critical", alertHoldSeconds: 2 }
    rt.configureHistory()
    var first = rt.configurationId
    live(rt, 0, { historyConfigId: null }) // buffered --defer-history reading
    compare(rt.incidentState.pending, null)
    live(rt, 2) // matching sample cannot bypass acknowledgement
    compare(rt.incidentState.pending, null)
    configured(rt)
    live(rt, 4, { historyConfigId: null })
    compare(rt.incidentState.pending, null)
    live(rt, 6); live(rt, 8)
    compare(rt.incidentState.phase, "active")
    var start = JSON.parse(lines(rt).filter(function(l) { return l.indexOf('"action":"start"') >= 0 })[0])
    compare(start.startSeq, 7, "only ingested post-configuration samples start dwell")
    rt.settings = { alertMode: "critical", alertHoldSeconds: 2, historyPersist: false }
    compare(rt.configured, false)
    live(rt, 10, { historyConfigId: first })
    rt.handleLine(JSON.stringify({type: "history-configured", requestId: first, enabled: true, persist: true}))
    compare(rt.configured, false, "old configuration acknowledgement is rejected")
    configured(rt)
    live(rt, 12, { historyConfigId: first })
    compare(rt.incidentState.pending, null)
    live(rt, 14, { used: 50, psiSome10: 0 }); live(rt, 16); live(rt, 18)
    compare(rt.incidentState.phase, "active", "new generation can detect after recovery")
  }

  function test_remote_clear_invalidates_removed_active_receipt() {
    var rt = createTemporaryObject(runtimeComponent, null)
    var event = startIncident(rt)
    rt.handleLine(JSON.stringify({ type: "history-cleared", requestId: null, remote: true }))
    compare(rt.incidentState.active, null)
    ack(rt, event)
    compare(notifierOf(rt).startedCommands.length, 0)
    live(rt, 4)
    compare(rt.incidentState.pending, null, "clear does not repeat the sustained incident")
    live(rt, 6, { used: 50, psiSome10: 0 }); live(rt, 8); live(rt, 10)
    compare(rt.incidentState.phase, "active")
  }

  function test_queued_notification_rechecks_context_at_delivery() {
    var cases = ["closed", "stale", "fresh"]
    for (var i = 0; i < cases.length; i++) {
      var rt = createTemporaryObject(runtimeComponent, null)
      var event = startIncident(rt), notice = notifierOf(rt)
      rt.subscribe("memory", { detail: true })
      notice.running = true // another helper is still running
      ack(rt, event, { notificationContext: { status: "observed", at: Date.now() / 1000,
        apps: [{ name: "Synthetic Browser", kb: 1024 }] } })
      compare(rt.notificationQueue.length, 1)
      if (cases[i] === "closed") rt.unsubscribe("memory")
      if (cases[i] === "stale") rt.notificationQueue[0].ack.notificationContext.at -= 6
      notice.running = false
      notice.exited(0, 0)
      compare(rt.notificationQueue.length, 0)
      compare(notice.startedCommands.length, 2)
      compare(notice.command.join(" ").indexOf("Observed: Synthetic Browser") >= 0, cases[i] === "fresh", cases[i])
    }
  }

}
