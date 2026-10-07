import QtQuick
import QtQuick.Window
import QtTest
import qs.Commons
import "../.."

TestCase {
  id: tc
  name: "HistoryPage"
  when: windowShown
  width: 1000; height: 1400

  // The real card is its own layer-shell window; here it renders in this one.
  Window { id: shotWindow; visible: true; width: 460; height: 1400; color: "#000000" }

  Component { id: serviceComponent; Service {} }
  Component { id: widgetComponent; BarWidget {} }
  Component {
    id: barComponent
    QtObject {
      property var shell: null
      property color barForeground: Color.foreground
      property color foreground: Color.foreground
      property color urgent: Color.urgent
      property string fontFamily: "DejaVu Sans Mono"
      property bool vertical: false
      property int barSize: 26
      property string position: "top"
      property var activePopout: null
      property bool foregroundAnimationEnabled: true
      property bool centerSectionRevealHeld: false
      property bool centerHoverRevealSuppressed: false
      function registerClickTarget(t) {}
      function unregisterClickTarget(t) {}
      function showTooltip(t, x) {}
      function hideTooltip(t) {}
      function requestPopout(o) {}
      function releasePopout(o) {}
    }
  }
  function fakeBar(service) {
    return createTemporaryObject(barComponent, null, {
      shell: service === undefined ? null : { serviceFor: function(id) { return id === "boundsj.raman" ? service : null } }
    })
  }

  readonly property string outDir: Qt.resolvedUrl("../../shots/").toString().replace("file://", "")

  function fixture(name) {
    var xhr = new XMLHttpRequest()
    xhr.open("GET", Qt.resolvedUrl("../fixtures/" + name), false)
    xhr.send()
    return JSON.parse(xhr.responseText)
  }
  function probeOf(rt) {
    for (var i = 0; i < rt.data.length; i++) if (rt.data[i].stdinEnabled === true) return rt.data[i]
    return null
  }
  function written(rt) { return probeOf(rt).written.map(function(l) { return l.trim() }) }
  function kills(rt) { return written(rt).filter(function(l) { return l.indexOf("kill ") === 0 }) }
  function historyRequests(rt) {
    return written(rt).filter(function(l) { return l.indexOf("history.get") > 0 }).map(function(l) { return JSON.parse(l) })
  }
  function lastRequest(rt) { var r = historyRequests(rt); return r[r.length - 1] }
  function respond(rt, name, requestId) {
    var message = fixture(name)
    message.requestId = requestId === undefined ? lastRequest(rt).requestId : requestId
    probeOf(rt).feed(JSON.stringify(message))
  }
  function panelOf(w) {
    for (var i = 0; i < w.data.length; i++) if (w.data[i].item && w.data[i].item.hostWidget !== undefined) return w.data[i].item
    return null
  }
  function cardOf(panel) {
    for (var i = 0; i < panel.data.length; i++) if (panel.data[i].availableCardWidth !== undefined) return panel.data[i]
    return null
  }
  function setup(width) {
    Color.setTheme(false)
    var svc = createTemporaryObject(serviceComponent, null)
    var w = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(svc), settings: {} })
    tryVerify(function() { return w.runtime === svc.runtime && panelOf(w) && panelOf(w).hostWidget === w })
    var panel = panelOf(w)
    var card = cardOf(panel)
    if (width) card.availableCardWidth = width
    probeOf(svc.runtime).feed(JSON.stringify({ type: "summary", total: 7700852, used: 4135694, available: 3565158,
      swapTotal: 8388608, swapUsed: 1153434, zramRam: 409600, psiSome10: 0.6, seq: 1 }))
    svc.runtime.requestedPage = "history"
    w.open()
    tryVerify(function() { return panel.opened && panel.historyView !== null })
    wait(30)
    return { rt: svc.runtime, w: w, panel: panel, view: panel.historyView, card: card }
  }
  function shot(s, name) {
    var home = s.card.parent
    s.card.parent = shotWindow.contentItem
    wait(80)
    var done = false
    s.card.grabToImage(function(result) { done = result.saveToFile(outDir + name + ".png") })
    tryVerify(function() { return done }, 3000, "saved " + name)
    s.card.parent = home
    if (s.card.focusTarget) s.card.focusTarget.forceActiveFocus()
    wait(20)
  }

  function test_other_panel_window_does_not_change_pending_query() {
    var s = setup()
    var first = lastRequest(s.rt)
    s.rt.historyWindow = "5m"
    compare(s.view.window, "1h", "the shared preference is only read on creation")
    respond(s.rt, "demo-1h.json", first.requestId)
    compare(s.view.reply.window, s.view.window)
  }

  function test_accessibility_inspect_action() {
    var s = setup()
    respond(s.rt, "demo-1h.json")
    s.view.selectIncident(1, true)
    verify(s.panel.nav.inspecting)
    compare(s.panel.nav.region, "incidents")
    compare(s.view.selected.id, "demo-2")
    compare(kills(s.rt).length, 0)
  }

  function test_expired_receipt_evidence_is_labelled_read_only() {
    var s = setup()
    var message = fixture("demo-1h.json")
    var inc = message.incidents[0]
    inc.measurement = {}; inc.measurementStatus = "expired"
    delete inc.thresholds
    inc.context = { status: "expired" }
    inc.upgrade = { at: inc.start + 10, measurement: {}, measurementStatus: "expired" }
    message.incidents = [inc]
    message.requestId = lastRequest(s.rt).requestId
    probeOf(s.rt).feed(JSON.stringify(message))
    s.view.selectIncident(0, true)
    verify(s.panel.nav.inspecting)
    compare(s.view.selected.id, inc.id)
    verify(s.view.selected.threshold.indexOf("expired") >= 0)
    verify(s.view.selected.observation.indexOf("expired") >= 0)
    compare(s.view.selected.apps.length, 0)
    wait(30)
    verify(descendants(s.view, function(i) { return i.text === "At start (expired)" }).length > 0)
    verify(descendants(s.view, function(i) { return typeof i.text === "string" && i.text.indexOf("measurements expired (24-hour retention)") >= 0 }).length > 0)
    s.view.handle("kill", "TERM")
    compare(kills(s.rt).length, 0)
  }

  function test_stale_replies_are_dropped() {
    var s = setup()
    var first = lastRequest(s.rt)
    compare(first.window, "1h")
    s.view.handle("window", 1)
    var second = lastRequest(s.rt)
    compare(second.window, "24h")
    verify(first.requestId !== second.requestId)
    respond(s.rt, "demo-1h.json", first.requestId)
    compare(s.view.reply, null, "the 1h reply arrived after switching to 24h: dropped")
    compare(s.view.status.state, "loading")
    respond(s.rt, "demo-24h.json", second.requestId)
    compare(s.view.reply.window, "24h")
    respond(s.rt, "demo-5m.json", second.requestId)
    compare(s.view.reply.window, "24h", "a duplicate reply for an answered request is dropped")
  }

  function test_close_and_reopen_drop_late_replies() {
    var s = setup()
    var pending = lastRequest(s.rt)
    s.w.close()
    tryVerify(function() { return s.panel.historyView === null })
    respond(s.rt, "demo-1h.json", pending.requestId) // no view left: nothing happens
    s.rt.requestedPage = "history"
    s.w.open()
    tryVerify(function() { return s.panel.historyView !== null })
    respond(s.rt, "demo-1h.json", pending.requestId)
    compare(s.panel.historyView.reply, null, "a new view never accepts the old view's reply")
    respond(s.rt, "demo-1h.json")
    verify(s.panel.historyView.reply !== null)
  }

  function test_error_and_clear() {
    var s = setup()
    probeOf(s.rt).feed(JSON.stringify({ type: "error", requestId: lastRequest(s.rt).requestId, schemaVersion: 1, code: "internal", message: "command failed; see stderr" }))
    compare(s.view.status.state, "error")
    shot(s, "error")
    var before = historyRequests(s.rt).length
    probeOf(s.rt).feed(JSON.stringify({ type: "history-cleared", requestId: "clear-1", schemaVersion: 1, demo: false, persisted: true, error: "" }))
    compare(historyRequests(s.rt).length, before + 1, "a clear anywhere reloads the view")
    compare(s.view.status.state, "loading")
    shot(s, "loading")
  }

  function test_keyboard_inspect_is_read_only() {
    var s = setup()
    respond(s.rt, "demo-1h.json")
    compare(s.view.incidentCount, 3)
    compare(s.panel.nav.region, "nav")
    keyClick(Qt.Key_J); compare(s.panel.nav.region, "window")
    keyClick(Qt.Key_J); compare(s.panel.nav.region, "chart")
    var c = s.view.cursor
    keyClick(Qt.Key_H); compare(s.view.cursor, c - 1)
    keyClick(Qt.Key_L, Qt.ShiftModifier); compare(s.view.cursor, c - 1 + 12 > s.view.points - 1 ? s.view.points - 1 : c + 11)
    keyClick(Qt.Key_J); compare(s.panel.nav.region, "incidents")
    keyClick(Qt.Key_Return)
    verify(s.panel.nav.inspecting)
    compare(s.view.selected.id, "demo-3", "newest first")
    compare(s.view.reply.t[s.view.cursor] <= s.view.selected.start, true, "cursor jumps to the incident start")
    shot(s, "inspect-critical")
    keyClick(Qt.Key_J)
    compare(s.view.selected.id, "demo-2")
    var keys = [Qt.Key_X, Qt.Key_F, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Space]
    for (var i = 0; i < keys.length; i++) keyClick(keys[i])
    compare(kills(s.rt).length, 0, "nothing on History reaches the kill command")
    keyClick(Qt.Key_Escape)
    verify(!s.panel.nav.inspecting, "Esc backs out of the receipt")
    verify(s.panel.opened)
    keyClick(Qt.Key_Escape)
    tryVerify(function() { return !s.panel.opened }, 1000, "then closes")
  }

  function test_window_keys_and_session_memory() {
    var s = setup()
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_H)
    compare(lastRequest(s.rt).window, "5m")
    compare(s.rt.historyWindow, "5m", "the chosen window is remembered for this session")
    keyClick(Qt.Key_L); keyClick(Qt.Key_L); keyClick(Qt.Key_L)
    compare(lastRequest(s.rt).window, "24h", "no wraparound")
  }

  function test_unavailable_and_empty_states() {
    var s = setup()
    respond(s.rt, "live-nopsi-1h.json")
    compare(s.view.scales.pressure.status, "unavailable")
    compare(s.view.scales.swap.status, "none")
    compare(s.view.status.state, "partial")
    compare(s.view.info.lines[0].text, "no reading")
    shot(s, "live-nopsi-1h")
    s.view.handle("window", 1)
    respond(s.rt, "live-empty-24h.json")
    compare(s.view.status.state, "empty")
    shot(s, "live-empty")
  }

  function test_render_matrix() {
    var themes = [false, true]
    for (var k = 0; k < themes.length; k++) {
      var s = setup()
      Color.setTheme(themes[k])
      compare(s.w.bar.foreground, Color.foreground, "the fake bar follows the active theme")
      compare(s.panel.foreground, Color.foreground, "the panel uses the active theme foreground")
      compare(s.view.foreground, Color.foreground, "History uses the active theme foreground")
      var tag = themes[k] ? "light" : "dark"
      respond(s.rt, "demo-1h.json")
      shot(s, "demo-1h-" + tag)
      s.view.handle("window", 1)
      respond(s.rt, "demo-24h.json")
      shot(s, "demo-24h-" + tag)
      s.view.handle("window", -1); s.view.handle("window", -1)
      respond(s.rt, "demo-5m.json")
      shot(s, "demo-5m-" + tag)
      s.w.close()
      tryVerify(function() { return !s.panel.opened })
    }
    var n = setup(340)
    respond(n.rt, "longnames-1h.json")
    compare(n.card.width, 340)
    shot(n, "narrow-340-chart")
    n.view.selectIncident(0, false)
    n.panel.setInspecting(true)
    n.view.showIncident()
    shot(n, "narrow-340-longnames-inspect")
    n.w.close()
    var m = setup()
    respond(m.rt, "demo-1h.json")
    m.panel.selectPage("memory")
    shot(m, "memory-page-with-tabs")
  }
  function descendants(item, predicate) {
    var out = []
    if (predicate(item)) out.push(item)
    for (var i = 0; i < (item.children || []).length; i++) out = out.concat(descendants(item.children[i], predicate))
    return out
  }
  function test_short_screen_chart_visible_on_return() {
    var s = setup()
    s.card.availableCardHeight = 440
    respond(s.rt, "demo-1h.json")
    wait(50)
    var flick = descendants(s.view, function(i) { return i.contentY !== undefined })[0]
    keyClick(Qt.Key_J); keyClick(Qt.Key_J); keyClick(Qt.Key_J)
    wait(20)
    compare(s.panel.nav.region, "incidents")
    keyClick(Qt.Key_K)
    wait(20)
    compare(s.panel.nav.region, "chart")
    var chart = descendants(s.view, function(i) { return i.Accessible && i.Accessible.role === Accessible.Chart })[0]
    var pos = chart.mapToItem(flick, 0, 0)
    verify(pos.y >= 0 && pos.y + chart.height <= flick.height, "Keyboard-focused chart must be visible when returning from incidents")
  }
  function test_selected_incident_is_visible() {
    var s = setup()
    var message = fixture("demo-1h.json")
    var base = message.incidents[0]
    message.incidents = []
    for (var i = 0; i < 30; i++) {
      var row = JSON.parse(JSON.stringify(base))
      row.id = "review-" + i
      row.start += i
      message.incidents.push(row)
    }
    message.requestId = lastRequest(s.rt).requestId
    s.card.availableCardHeight = 600
    probeOf(s.rt).feed(JSON.stringify(message))
    wait(30)
    keyClick(Qt.Key_J); keyClick(Qt.Key_J); keyClick(Qt.Key_J)
    wait(20)
    compare(s.panel.nav.region, "incidents")
    var flick = descendants(s.view, function(i) { return i.contentY !== undefined })[0]
    var row = descendants(s.view, function(i) { return i.rowIndex === 0 && i.receipt !== undefined })[0]
    var pos = row.mapToItem(flick, 0, 0)
    verify(pos.y >= 0 && pos.y + row.height <= flick.height, "The selected incident should be visible when entering the region")
    for (var k = 1; k < 30; k++) {
      keyClick(Qt.Key_J)
      var selectedRow = descendants(s.view, function(i) { return i.rowIndex === k && i.receipt !== undefined })[0]
      tryVerify(function() {
        var p = selectedRow.mapToItem(flick, 0, 0)
        return p.y >= -0.01 && p.y + selectedRow.height <= flick.height + 0.01
      }, 1000, "selected row " + k + " stays visible after layout settles")
    }
    keyClick(Qt.Key_Return)
    wait(20)
    verify(s.panel.nav.inspecting)
    keyClick(Qt.Key_Escape)
    wait(20)
    var lastRow = descendants(s.view, function(i) { return i.rowIndex === 29 && i.receipt !== undefined })[0]
    verify(lastRow.mapToItem(flick, 0, 0).y >= 0, "back returns to the selected row")
  }
  function test_runtime_migration_uses_fresh_history_client() {
    var s = setup()
    s.view.setWindow("5m")
    var late = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(undefined), settings: {} })
    tryVerify(function() { return late.useLocalRuntime && late.runtime !== null }, 4000)
    var p = panelOf(late)
    late.runtime.requestedPage = "history"
    late.open()
    tryVerify(function() { return p.historyView !== null })
    var v = p.historyView
    v.setWindow("24h")
    var oldKey = v.clientKey
    late.bar = fakeBar({runtime:s.rt})
    tryVerify(function() { return late.runtime === s.rt && v.runtime === s.rt })
    var incoming = fixture("demo-5m.json")
    incoming.requestId = s.view.pendingId
    probeOf(s.rt).feed(JSON.stringify(incoming))
    compare(v.reply, null, "a migrated view rejects another panel's reply")
    verify(v.clientKey !== s.view.clientKey, "fresh ID in the destination runtime")
    verify(v.pendingId !== s.view.pendingId)
    var own = fixture("demo-24h.json")
    own.requestId = v.pendingId
    probeOf(s.rt).feed(JSON.stringify(own))
    compare(v.reply.window, "24h", "migration requests its own window immediately")
  }
  function test_incident_ipc_selection_read_only() {
    var s = setup()
    s.rt.panelRouter = { open: function() { s.w.open() } }
    var data = fixture("demo-24h.json")
    s.rt.openIncident(data.incidents[0].id)
    respond(s.rt, "demo-24h.json")
    compare(s.view.selectedId, data.incidents[0].id)
    compare(s.panel.nav.inspecting, true)
    compare(s.rt.requestedIncidentId, "")
    var before = kills(s.rt).length
    s.view.handle("inspect")
    compare(kills(s.rt).length, before)
  }

  function test_open_incident_existing_history_window() {
    var s = setup()
    s.rt.panelRouter = { open: function() { s.w.open() } }
    s.view.setWindow("5m")
    respond(s.rt, "demo-5m.json")
    var retained = fixture("demo-24h.json").incidents[0].id
    s.rt.openIncident(retained)
    compare(s.view.window, "24h", "existing target view switches to retention window")
    compare(lastRequest(s.rt).window, "24h")
    respond(s.rt, "demo-24h.json")
    compare(s.view.selectedId, retained)
    compare(s.panel.nav.inspecting, true)
    compare(kills(s.rt).length, 0)
  }

  function test_other_panel_cannot_consume_receipt_request() {
    var s = setup()
    respond(s.rt, "demo-1h.json")
    var w2 = createTemporaryObject(widgetComponent, tc, { bar: fakeBar({runtime:s.rt}), settings: {} })
    tryVerify(function() { return panelOf(w2) && panelOf(w2).runtime === s.rt })
    s.rt.panelRouter = { open: function() { w2.open() } }
    var retained = fixture("demo-24h.json").incidents[0].id
    s.rt.openIncident(retained)
    tryVerify(function() { return panelOf(w2).historyView !== null })
    var target = panelOf(w2).historyView
    compare(target.wantedIncidentId, retained, "only host-selected panel owns the route")
    var oldTargetRequest = target.pendingId
    s.view.request()
    respond(s.rt, "demo-1h.json", s.view.pendingId)
    compare(target.wantedIncidentId, retained)
    compare(s.panel.nav.inspecting, false, "other monitor stays unselected")
    target.request()
    respond(s.rt, "demo-24h.json", oldTargetRequest)
    compare(target.reply, null, "old target generation cannot consume route")
    respond(s.rt, "demo-24h.json", target.pendingId)
    compare(target.selectedId, retained)
    compare(panelOf(w2).nav.inspecting, true)
    compare(target.wantedIncidentId, "")
  }

}
