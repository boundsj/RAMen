import QtQuick
import QtQuick.Window
import QtTest
import qs.Commons
import "../.."

// Apps page through the real Panel, PanelKeyCatcher and runtime, with the
// probe replaced by the recording Process stub: nothing is ever signalled,
// the tests only inspect the commands the page would send.
TestCase {
  id: tc
  name: "AppsPage"
  when: windowShown
  width: 900; height: 1000
  visible: true

  // grabToImage keeps the device pixel ratio; grabImage crops at QT_SCALE_FACTOR=2.
  function saveShot(item, path) {
    var done = false
    item.grabToImage(function(result) { done = result.saveToFile(path) })
    tryVerify(function() { return done }, 4000, "saved " + path)
  }

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
      shell: { serviceFor: function(id) { return id === "boundsj.raman" ? service : null } }
    })
  }
  function probeOf(rt) {
    for (var i = 0; i < rt.data.length; i++) if (rt.data[i].stdinEnabled === true) return rt.data[i]
    return null
  }
  function written(rt) { return probeOf(rt).written.map(function(l) { return l.trim() }) }
  function commands(rt, name) {
    return written(rt).filter(function(l) { return l.indexOf("{") === 0 }).map(function(l) { return JSON.parse(l) })
      .filter(function(c) { return !name || c.command === name })
  }
  function signals(rt) {
    return written(rt).filter(function(l) { return l.indexOf("kill ") === 0 || l.indexOf("apps.kill") >= 0 })
  }
  function panelOf(w) {
    for (var i = 0; i < w.data.length; i++) if (w.data[i].item && w.data[i].item.hostWidget !== undefined) return w.data[i].item
    return null
  }
  function row(id, fields) {
    return Object.assign({ id: id, name: id, kind: "app", host: "", count: 3, protected: false, generation: "g-" + id,
      pss: 400000, swap: 1024, memoryStatus: "available", memoryCoverage: { measured: 3, members: 3 },
      cpuCorePercent: 40, cpuMachinePercent: 5, cpuStatus: "available", cpuCoverage: { measured: 3, members: 3 },
      sampledAt: 1 }, fields || {})
  }
  // Reply to the newest apps.query with these rows; focus is derived from them.
  function reply(rt, rows, extra) {
    var all = commands(rt, "apps.query")
    var q = all[all.length - 1]
    var focus
    if (q.focus) {
      var found = rows.filter(function(r) { return r.id === q.focus })[0]
      focus = { id: q.focus, present: !!found, generation: found ? found.generation : null, rank: found ? rows.indexOf(found) : null, row: found || null }
    }
    var message = Object.assign({ type: "apps-page", requestId: q.requestId, schemaVersion: 1, time: 1, generation: q.generation,
      status: "ready", stale: false, demo: false, snapshot: 5, sampledAt: 1, query: q.query, sort: q.sort, offset: q.offset,
      limit: 50, total: rows.length, nextOffset: null, inventory: { complete: true, processLimit: 8192, groupLimit: 4096 },
      cpu: { status: "available", onlineCpus: 8 }, coverage: {}, units: {}, rows: rows, focus: focus }, extra || {})
    probeOf(rt).feed(JSON.stringify(message))
    return q
  }
  // A new scan: the runtime's inventory changes and the page re-queries.
  function scan(rt, snapshot) {
    probeOf(rt).feed(JSON.stringify({ type: "apps", apps: [], snapshot: snapshot, sampledAt: snapshot, inventory: {}, cpu: {}, coverage: {} }))
  }
  function setup(settings) {
    var svc = createTemporaryObject(serviceComponent, null)
    var w = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(svc) })
    tryVerify(function() { return w.runtime === svc.runtime && panelOf(w) && panelOf(w).hostWidget === w })
    if (settings) svc.runtime.settings = settings
    probeOf(svc.runtime).feed(JSON.stringify({ type: "summary", total: 16000000, used: 8000000, available: 8000000,
      swapTotal: 0, swapUsed: 0, zramRam: 0, psiSome10: 0.5 }))
    var panel = panelOf(w)
    panel.parent = tc
    w.open()
    tryVerify(function() { return panel.opened })
    wait(50)
    panel.selectPage("apps")
    tryVerify(function() { return !!panel.appsView })
    panel.focusRegion("list")
    wait(20)
    return { svc: svc, rt: svc.runtime, w: w, panel: panel, view: panel.appsView }
  }
  function seeded(rows, settings) {
    var s = setup(settings)
    reply(s.rt, rows || [row("chrome"), row("slack"), row("build", { kind: "session", host: "Ghostty" })])
    tryVerify(function() { return s.view.model.rows.length > 0 })
    return s
  }

  function test_page_shares_the_detail_scan_and_queries_the_cache() {
    var s = setup()
    compare(s.rt.detail, true, "Apps subscribes to the shared detail scan")
    var q = commands(s.rt, "apps.query")
    verify(q.length >= 1)
    compare(q[0].sort, "memory")
    compare(q[0].offset, 0)
    reply(s.rt, [row("a"), row("b")])
    compare(s.view.model.rows.length, 2)
    scan(s.rt, 6)
    compare(commands(s.rt, "apps.query").length, q.length + 1, "each new snapshot re-queries the cache once")
    s.panel.selectPage("history")
    wait(20)
    compare(s.rt.detail, false, "leaving Apps for History stops the scan")
    s.panel.selectPage("apps")
    tryVerify(function() { return !!s.panel.appsView })
    compare(s.rt.detail, true)
    s.panel.close()
    tryVerify(function() { return !s.panel.opened })
    compare(Object.keys(s.rt.subscriptions).length, 0)
  }

  function test_two_step_kill_sends_the_armed_membership() {
    var s = seeded()
    keyClick(Qt.Key_J)
    compare(s.view.model.selectedId, "chrome")
    keyClick(Qt.Key_X)
    compare(signals(s.rt).length, 0, "the first x only arms")
    compare(s.view.model.armed.id, "chrome")
    keyClick(Qt.Key_X)
    var kills = commands(s.rt, "apps.kill")
    compare(kills.length, 1)
    compare(kills[0].id, "chrome")
    compare(kills[0].membership, "g-chrome")
    compare(kills[0].signal, "TERM")
    compare(written(s.rt).filter(function(l) { return l.indexOf("kill ") === 0 }).length, 0, "no legacy kill from Apps")
    compare(s.rt.kills["chrome"].scope, "apps")
    // Enter confirms just like x; f arms a force kill.
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_Return)
    keyClick(Qt.Key_Return)
    compare(commands(s.rt, "apps.kill")[1].id, "slack")
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_F)
    compare(s.view.model.armed.force, true)
    keyClick(Qt.Key_F)
    compare(commands(s.rt, "apps.kill")[2].signal, "KILL")
  }

  function test_arm_expires_after_four_seconds() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    wait(3500)
    verify(s.view.armed, "still armed before the timeout")
    tryVerify(function() { return !s.view.armed }, 1500)
    keyClick(Qt.Key_X)
    compare(commands(s.rt, "apps.kill").length, 0, "after expiry x arms again")
  }

  // Clicking the sort chip that is already selected changes nothing, so the
  // armed kill keeps its 4-second expiry (it used to stop the timer).
  function test_selected_sort_click_keeps_arm_expiry() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    var queries = commands(s.rt, "apps.query").length
    var chip = findChild(s.view, "appsSort-memory")
    verify(chip && chip.selected, "Memory is the selected sort")
    mouseClick(chip)
    compare(commands(s.rt, "apps.query").length, queries, "no query for the current sort")
    verify(s.view.armed, "a no-op sort click does not disarm")
    tryVerify(function() { return !s.view.armed }, 4500, "the arm still expires after 4 s")
    compare(signals(s.rt).length, 0)
    // A real sort change still disarms at once.
    s.panel.focusRegion("list")
    keyClick(Qt.Key_X)
    verify(s.view.armed)
    mouseClick(findChild(s.view, "appsSort-cpu"))
    verify(!s.view.armed, "changing the sort disarms")
    compare(commands(s.rt, "apps.query").pop().sort, "cpu")
  }

  // Details closed while a request is pending, then reopened (for another
  // group or the same one): the late reply and errors of the closed request
  // never populate the new view, because generations keep counting.
  function test_details_reopen_rejects_the_closed_request_reply() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_D)
    var oldChrome = commands(s.rt, "apps.details").pop()
    compare(oldChrome.id, "chrome")
    keyClick(Qt.Key_Escape)
    verify(!s.view.detailsOpen)
    keyClick(Qt.Key_J); keyClick(Qt.Key_D)
    var slack = commands(s.rt, "apps.details").pop()
    compare(slack.id, "slack")
    verify(slack.requestId !== oldChrome.requestId, "a reopened request has a new ID")
    var late = { type: "apps-details", requestId: oldChrome.requestId, generation: oldChrome.generation, id: "chrome",
      demo: false, row: row("chrome"), membersTotal: 1, commandLimit: 512,
      members: [{ pid: 11, name: "chrome", pss: 2048, swap: 0, command: "OLD CHROME COMMAND", commandStatus: "available" }] }
    probeOf(s.rt).feed(JSON.stringify(late))
    probeOf(s.rt).feed(JSON.stringify({ type: "error", requestId: oldChrome.requestId, generation: oldChrome.generation,
      id: "chrome", code: "gone", message: "that app is no longer running" }))
    wait(20)
    compare(s.view.details.id, "slack")
    compare(s.view.details.data, null, "the late chrome reply is not shown under slack")
    compare(s.view.details.error, "", "nor its error")
    compare(s.view.details.pendingId, slack.requestId, "slack's own reply is still awaited")
    probeOf(s.rt).feed(JSON.stringify(Object.assign({}, late, { requestId: slack.requestId, generation: slack.generation,
      id: "slack", row: row("slack"), members: [] })))
    tryVerify(function() { return !!s.view.details.data && s.view.details.data.id === "slack" })
    // Same group again: chrome closed while pending, chrome reopened.
    keyClick(Qt.Key_Escape)
    keyClick(Qt.Key_K); keyClick(Qt.Key_D)
    var pendingChrome = commands(s.rt, "apps.details").pop()
    keyClick(Qt.Key_Escape)
    keyClick(Qt.Key_D)
    var chrome = commands(s.rt, "apps.details").pop()
    compare(chrome.id, "chrome")
    probeOf(s.rt).feed(JSON.stringify(Object.assign({}, late, { requestId: pendingChrome.requestId, generation: pendingChrome.generation })))
    wait(20)
    compare(s.view.details.data, null, "a reply to the closed request for the same group is not shown")
  }

  function test_membership_change_between_arm_and_confirm_disarms() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    scan(s.rt, 6)
    var q = commands(s.rt, "apps.query").pop()
    compare(q.focus, "chrome", "the armed group is tracked by every query")
    reply(s.rt, [row("slack"), row("chrome", { generation: "g-chrome-2" }), row("build")])
    verify(!s.view.armed, "changed membership disarms")
    verify(/changed/.test(s.view.model.notice))
    keyClick(Qt.Key_X)
    compare(commands(s.rt, "apps.kill").length, 0, "the next x only arms the new membership")
    keyClick(Qt.Key_X)
    compare(commands(s.rt, "apps.kill")[0].membership, "g-chrome-2")
  }

  function test_disappearing_armed_app_disarms() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    scan(s.rt, 6)
    reply(s.rt, [row("slack"), row("build")])
    verify(!s.view.armed)
    verify(/no longer running/.test(s.view.model.notice))
    compare(signals(s.rt).length, 0)
  }

  function test_rows_freeze_under_the_pointer_and_the_click_keeps_its_target() {
    var s = seeded()
    var list = findChild(s.view, "appsList")
    tryVerify(function() { return !!list.itemAtIndex(0) })
    waitForRendering(s.view)
    wait(50) // the page's first layout pass settles before the pointer arrives
    var first = list.itemAtIndex(0)
    mouseMove(first, first.width / 2, first.height / 2)
    tryVerify(function() { return s.view.model.hovering })
    compare(s.view.model.selectedId, "chrome")
    scan(s.rt, 6)
    reply(s.rt, [row("build"), row("slack"), row("chrome")])
    compare(s.view.model.rows[0].id, "chrome", "no reorder under the pointer")
    verify(!!s.view.model.queued)
    var rowY = first.mapToItem(tc, 0, 0).y
    // A disarm-style notice appears below the list: no row moves or is recreated.
    s.view.model = Object.assign({}, s.view.model, { notice: "Something changed; disarmed." })
    wait(30)
    compare(list.itemAtIndex(0), first, "state changes never recreate the rows under the pointer")
    compare(first.mapToItem(tc, 0, 0).y, rowY, "rows stay put under the pointer")
    // Right-click arms a force kill on the row actually under the pointer.
    mouseClick(first, first.width / 2, first.height / 2, Qt.RightButton)
    compare(s.view.model.armed.id, "chrome")
    compare(s.view.model.armed.force, true)
    var confirm = findChild(list.itemAtIndex(0), "appsConfirm")
    verify(confirm.visible)
    mouseClick(confirm, confirm.width / 2, confirm.height / 2)
    var kills = commands(s.rt, "apps.kill")
    compare(kills.length, 1)
    compare(kills[0].id, "chrome")
    compare(kills[0].signal, "KILL")
    mouseMove(tc, 5, 5)
    tryVerify(function() { return !s.view.model.hovering })
    compare(s.view.model.rows[0].id, "build", "leaving the list applies the queued order")
    compare(s.view.model.selectedId, "chrome", "the selection follows its app, not the index")
    compare(s.view.rowIndex, 2)
  }

  function test_search_is_input_safe_and_disarms() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    keyClick(Qt.Key_Slash)
    var field = findChild(s.view, "appsSearchInput")
    tryVerify(function() { return field.activeFocus })
    verify(!s.view.armed, "focusing search disarms")
    verify(s.view.inputOwned)
    for (var code of [Qt.Key_J, Qt.Key_K, Qt.Key_X, Qt.Key_F, Qt.Key_D, Qt.Key_R, Qt.Key_H, Qt.Key_L]) keyClick(code)
    compare(field.text, "jkxfdrhl", "every letter is text while searching")
    keyClick(Qt.Key_Space); keyClick(Qt.Key_Return)
    compare(signals(s.rt).length, 0)
    compare(s.view.detailsOpen, false)
    var q = commands(s.rt, "apps.query").pop()
    compare(q.query, "jkxfdrhl ")
    compare(q.offset, 0)
    verify(!field.activeFocus, "Enter applies the search and returns to the list")
    verify(s.panel.opened)
  }

  function test_escape_order_disarm_search_details_close() {
    var s = seeded()
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    keyClick(Qt.Key_Escape)
    verify(!s.view.armed, "Esc disarms first")
    verify(s.panel.opened)
    keyClick(Qt.Key_Slash)
    var field = findChild(s.view, "appsSearchInput")
    tryVerify(function() { return field.activeFocus })
    keyClick(Qt.Key_C)
    keyClick(Qt.Key_Escape)
    verify(!field.activeFocus, "then leaves search")
    compare(field.text, "c", "keeping the query")
    verify(s.panel.opened)
    reply(s.rt, [row("chrome")])
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_D)
    verify(s.view.detailsOpen, "d opens details")
    var details = commands(s.rt, "apps.details")
    compare(details.length, 1)
    compare(details[0].id, "chrome")
    keyClick(Qt.Key_Escape)
    verify(!s.view.detailsOpen, "then closes details")
    verify(s.panel.opened)
    keyClick(Qt.Key_Escape)
    tryVerify(function() { return !s.panel.opened }, 1000, "then closes the panel")
  }

  function test_details_are_on_demand_read_only_and_dropped() {
    var s = seeded()
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_D)
    var d = commands(s.rt, "apps.details")[0]
    var marker = "synthetic-command --private"
    probeOf(s.rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation,
      id: "chrome", demo: false, row: row("chrome"), membersTotal: 2, commandLimit: 512,
      members: [{ pid: 11, name: "chrome", pss: 2048, swap: 0, cpuMachinePercent: 1, cpuStatus: "available",
        command: marker, commandTruncated: false, commandStatus: "available" }] }))
    tryVerify(function() { return !!s.view.details.data })
    var members = findChild(s.view, "appsMembers")
    tryVerify(function() { return members.count === 1 })
    for (var code of [Qt.Key_X, Qt.Key_X, Qt.Key_F, Qt.Key_F, Qt.Key_Return, Qt.Key_Return, Qt.Key_Space])
      keyClick(code)
    compare(signals(s.rt).length, 0, "the details view never kills")
    verify(!s.view.armed)
    // A late reply for an older details request is ignored.
    keyClick(Qt.Key_R)
    var again = commands(s.rt, "apps.details")
    compare(again.length, 2, "r re-reads details on demand")
    probeOf(s.rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation, members: [] }))
    compare(s.view.details.data, null, "the older reply is not shown")
    keyClick(Qt.Key_Escape)
    verify(!s.view.detailsOpen)
    compare(s.view.details, null, "closing drops the command lines")
    compare(JSON.stringify(s.rt.kills).indexOf(marker), -1)
  }

  function test_sort_and_page_changes_disarm_and_pin() {
    var s = setup()
    reply(s.rt, [row("a"), row("b")], { total: 120, nextOffset: 50 })
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    keyClick(Qt.Key_BracketRight)
    verify(!s.view.armed, "paging disarms")
    var q = commands(s.rt, "apps.query").pop()
    compare(q.offset, 50)
    compare(q.snapshot, 5, "the next page reads the same snapshot")
    reply(s.rt, [row("c")], { offset: 50, total: 120, nextOffset: 100 })
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    keyClick(Qt.Key_K)            // to the sort lens (leaving the list disarms)
    verify(!s.view.armed)
    compare(s.panel.nav.region, "sort")
    keyClick(Qt.Key_L)
    q = commands(s.rt, "apps.query").pop()
    compare(q.sort, "cpu")
    compare(q.offset, 0, "a new lens starts at the first page")
    keyClick(Qt.Key_L)
    compare(commands(s.rt, "apps.query").pop().sort, "cpu", "the unavailable GPU lens is never requested")
    compare(signals(s.rt).length, 0)
  }

  function test_expired_snapshot_restarts_from_the_top() {
    var s = setup()
    reply(s.rt, [row("a")], { total: 120, nextOffset: 50 })
    s.view.handle("appsPage", 1)
    var q = commands(s.rt, "apps.query").pop()
    probeOf(s.rt).feed(JSON.stringify({ type: "error", requestId: q.requestId, generation: q.generation, code: "snapshot-expired",
      message: "snapshot is no longer retained" }))
    var restart = commands(s.rt, "apps.query").pop()
    compare(restart.offset, 0)
    compare(restart.snapshot, undefined)
    verify(/expired/.test(s.view.model.notice))
  }

  function test_stale_replies_never_overwrite_newer_queries() {
    var s = seeded()
    var older = commands(s.rt, "apps.query").pop()
    s.view.changeQuery("slack")
    probeOf(s.rt).feed(JSON.stringify({ type: "apps-page", requestId: older.requestId, generation: older.generation,
      status: "ready", snapshot: 9, rows: [row("old")], total: 1, offset: 0 }))
    verify(s.view.model.rows.every(function(r) { return r.id !== "old" }), "an older reply is dropped")
    reply(s.rt, [row("slack")])
    compare(s.view.model.rows[0].id, "slack")
  }

  function test_gpu_default_falls_back_visibly() {
    var s = setup({ appsDefaultSort: "gpu-memory" })
    compare(s.view.model.sort, "memory")
    verify(/GPU memory .*not available/.test(s.view.model.sortNote))
    compare(commands(s.rt, "apps.query")[0].sort, "memory")
    s.view.changeSort("gpu-memory")
    compare(s.view.model.sort, "memory")
    verify(/not available/.test(s.view.model.notice))
    // The first GPU sample finds no device with per-client VRAM: the fallback stays and says why.
    reply(s.rt, [row("a")], { gpu: { status: "unsupported", reason: "no-drm-devices", memoryLens: false, devices: [] } })
    compare(s.view.model.sort, "memory")
    verify(/No DRM GPU device.*Showing Memory/.test(s.view.model.sortNote), s.view.model.sortNote)
    compare(findChild(s.view, "appsSort-gpu-memory").available, false)
    var cpu = setup({ appsDefaultSort: "cpu" })
    compare(cpu.view.model.sort, "cpu")
    compare(commands(cpu.rt, "apps.query")[0].sort, "cpu")
  }

  // --- A3: GPU memory -------------------------------------------------------
  function gpuMeta(extra) {
    return Object.assign({ status: "available", memoryLens: true, source: "drm-fdinfo", devices: [{ id: "pci:0000:03:00.0",
      driver: "amdgpu", status: "available", memory: { dedicated: { status: "available" }, system: { status: "available" } },
      totals: { deviceVramUsedKb: 9437184, deviceVramTotalKb: 16777216, unattributedVramKb: 1048576,
        unattributedLowerBound: true, crossGroupClients: 1 } }] }, extra || {})
  }
  function gpuEntry(fields) {
    return Object.assign({ deviceId: "pci:0000:03:00.0", driver: "amdgpu", source: "drm-fdinfo", status: "available",
      vramKb: 6501171, vramAllocatedKb: 6815744, vramSharedKb: 0, vramOverlap: "none", vramStatus: "available",
      systemKb: 131072, systemSharedKb: 0, systemOverlap: "none", systemStatus: "available", clients: 1, sharedClients: 0, unidentified: 0, unread: 0,
      engines: { compute: { percent: 87.5, status: "available", basis: "busy-time", capacity: 1 },
        gfx: { percent: null, status: "warming-up", basis: "busy-time", capacity: 1 } } }, fields || {})
  }
  function gpuRow(id, fields) {
    return row(id, Object.assign({ gpu: [gpuEntry()], gpuMemoryKb: 6501171, gpuStatus: "available",
      gpuCoverage: { measured: 3, members: 3 } }, fields || {}))
  }
  function named(item, name, out) {
    out = out || []
    if (!item) return out
    if (item.objectName === name && item.visible) out.push(item)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) named(kids[i], name, out)
    return out
  }

  function test_gpu_subscription_follows_the_apps_page_and_setting() {
    var s = setup()
    compare(s.rt.gpu, true, "a visible Apps page asks for GPU readings")
    compare(s.rt.detail, true)
    verify(written(s.rt).indexOf("gpu 1") >= 0)
    s.panel.selectPage("memory")
    wait(20)
    compare(s.rt.gpu, false, "Memory alone never asks for GPU work")
    compare(s.rt.detail, true, "but keeps its scan")
    verify(written(s.rt).lastIndexOf("gpu 0") > written(s.rt).lastIndexOf("gpu 1"))
    s.panel.selectPage("apps")
    tryVerify(function() { return s.rt.gpu })
    s.rt.settings = { gpuMetrics: "off" }
    tryVerify(function() { return !s.rt.gpu })
    compare(s.rt.detail, true, "gpuMetrics off keeps the CPU/RAM scan")
    s.rt.settings = { gpuMetrics: "auto" }
    tryVerify(function() { return s.rt.gpu })
    s.w.close()
    tryVerify(function() { return !s.panel.opened })
    compare(s.rt.gpu, false, "a closed panel does no GPU work")
    compare(s.rt.detail, false)
    var status = JSON.parse(JSON.stringify(s.rt.status()))
    compare(status.runtime.gpu, false)
    compare(status.runtime.gpuMetrics, "auto")
  }

  function test_gpu_default_switches_once_readings_arrive() {
    var s = setup({ appsDefaultSort: "gpu-memory" })
    reply(s.rt, [row("a")], { gpu: { status: "warming-up", memoryLens: false, devices: [] } })
    compare(s.view.model.sort, "memory", "warming up: wait on Memory")
    compare(s.view.model.wantSort, "gpu-memory")
    scan(s.rt, 6)
    reply(s.rt, [row("a"), gpuRow("llm")], { gpu: gpuMeta() })
    compare(commands(s.rt, "apps.query").pop().sort, "gpu-memory", "switched once a device reports VRAM per client")
    reply(s.rt, [gpuRow("llm"), row("a", { gpu: [], gpuStatus: "none", gpuMemoryKb: null })], { gpu: gpuMeta() })
    compare(s.view.model.sort, "gpu-memory")
    compare(s.view.model.sortNote, "")
    verify(findChild(s.view, "appsSort-gpu-memory").available)
    var list = findChild(s.view, "appsList")
    tryVerify(function() { return list.itemAtIndex(1) !== null })
    compare(findChild(list.itemAtIndex(0), "appsLensValue").text, "VRAM 6.2G")
    compare(findChild(list.itemAtIndex(1), "appsLensValue").text, "VRAM –", "no GPU clients: never a fake zero")
    verify(/VRAM: device-local/.test(findChild(s.view, "appsLegend").text))
    // h/l in the sort row now reach the GPU lens and back.
    s.panel.focusRegion("sort")
    keyClick(Qt.Key_H)
    compare(commands(s.rt, "apps.query").pop().sort, "cpu")
  }

  function test_gpu_default_waits_for_an_armed_kill() {
    var s = setup({ appsDefaultSort: "gpu-memory" })
    reply(s.rt, [row("a"), row("b")], { gpu: { status: "warming-up", memoryLens: false, devices: [] } })
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    verify(s.view.armed)
    var before = commands(s.rt, "apps.query").length
    scan(s.rt, 6)
    reply(s.rt, [row("a"), row("b"), gpuRow("llm")], { gpu: gpuMeta() })
    verify(s.view.armed, "a lens switch never happens under an armed kill")
    compare(commands(s.rt, "apps.query").filter(function(q) { return q.sort === "gpu-memory" }).length, 0)
    keyClick(Qt.Key_X)
    var kill = commands(s.rt, "apps.kill").pop()
    compare(kill.membership, "g-a", "the armed membership is confirmed as before")
    scan(s.rt, 7)
    reply(s.rt, [row("a"), row("b"), gpuRow("llm")], { gpu: gpuMeta() })
    compare(commands(s.rt, "apps.query").pop().sort, "gpu-memory", "then it switches")
  }

  function test_gpu_off_keeps_the_lens_unavailable_and_kills_unchanged() {
    var s = setup({ gpuMetrics: "off", appsDefaultSort: "gpu-memory" })
    compare(s.rt.gpu, false, "gpuMetrics off: no GPU subscription")
    verify(written(s.rt).indexOf("gpu 1") < 0)
    compare(s.view.model.sort, "memory")
    verify(/off in settings/.test(s.view.model.sortNote))
    reply(s.rt, [gpuRow("llm"), row("a")], { gpu: gpuMeta() })
    compare(s.view.model.sort, "memory", "readings from another view never enable the lens here")
    s.view.changeSort("gpu-memory")
    verify(/off in settings/.test(s.view.model.notice))
    compare(findChild(s.view, "appsSort-gpu-memory").available, false)
    keyClick(Qt.Key_J); keyClick(Qt.Key_X); keyClick(Qt.Key_X)
    var kill = commands(s.rt, "apps.kill").pop()
    compare(kill.id, "llm")
    compare(kill.membership, "g-llm")
  }

  function test_gpu_setting_turned_off_leaves_the_gpu_lens() {
    var s = setup()
    reply(s.rt, [gpuRow("llm")], { gpu: gpuMeta() })
    s.view.changeSort("gpu-memory")
    compare(commands(s.rt, "apps.query").pop().sort, "gpu-memory")
    reply(s.rt, [gpuRow("llm")], { gpu: gpuMeta() })
    s.rt.settings = { gpuMetrics: "off" }
    tryVerify(function() { return commands(s.rt, "apps.query").pop().sort === "memory" })
    verify(/turned off/.test(s.view.model.sortNote))
  }

  function test_gpu_details_list_devices_engines_and_meter() {
    var s = setup()
    var shared = gpuRow("llm", { gpu: [gpuEntry({ sharedClients: 1, status: "partial" })], gpuStatus: "partial" })
    reply(s.rt, [shared], { gpu: gpuMeta() })
    keyClick(Qt.Key_J); keyClick(Qt.Key_D)
    var d = commands(s.rt, "apps.details").pop()
    probeOf(s.rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation, id: "llm",
      demo: false, row: shared, membersTotal: 0, commandLimit: 512, members: [] }))
    tryVerify(function() { return named(s.view, "appsGpuLine").length >= 4 })
    var lines = named(s.view, "appsGpuLine").map(function(t) { return t.text })
    verify(/^GPU pci:0000:03:00.0 \(amdgpu\): VRAM resident 6.2G \(allocated 6.5G\) · GPU buffers in system RAM 128M \(part of RAM, not added\)$/.test(lines[0]), lines[0])
    verify(/each % of its own engine class.*compute 88% · gfx warming up/.test(lines[1]), lines[1])
    verify(/1 shared with other apps \(not counted here\)/.test(lines[2]), lines[2])
    verify(/Device VRAM used 9.0G of 16G/.test(lines[3]), lines[3])
    var meters = named(s.view, "appsVramMeter")
    compare(meters.length, 1)
    compare(meters[0].fraction, 6501171 / 16777216)
    for (var code of [Qt.Key_X, Qt.Key_X, Qt.Key_F, Qt.Key_F, Qt.Key_Return]) keyClick(code)
    compare(signals(s.rt).length, 0, "GPU details stay read-only")
  }

  // Two clients of one app each referencing one shared 4 GiB buffer: a labelled
  // client-reference sum, never drawn as physical occupancy of the 8 GiB device.
  function test_gpu_overlapping_client_sum_is_labelled_without_a_meter() {
    var s = setup()
    var meta = gpuMeta({ devices: [{ id: "pci:0000:03:00.0", driver: "amdgpu", status: "available",
      totals: { deviceVramUsedKb: 4194304, deviceVramTotalKb: 8388608, vramOverlap: "possible" } }] })
    var twice = gpuRow("two", { gpuMemoryKb: 8388608, gpuMemoryOverlap: true, gpu: [gpuEntry({ vramKb: 8388608,
      vramAllocatedKb: 8388608, vramSharedKb: 8388608, vramOverlap: "possible", clients: 2 })] })
    reply(s.rt, [twice], { gpu: meta, coverage: { gpuOverlap: 1 } })
    s.view.changeSort("gpu-memory")
    reply(s.rt, [twice], { gpu: meta, coverage: { gpuOverlap: 1 } })
    var list = findChild(s.view, "appsList")
    tryVerify(function() { return list.itemAtIndex(0) !== null })
    compare(findChild(list.itemAtIndex(0), "appsLensValue").text, "VRAM ≤8.0G")
    keyClick(Qt.Key_J); keyClick(Qt.Key_D)
    var d = commands(s.rt, "apps.details").pop()
    probeOf(s.rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation, id: "two",
      demo: false, row: twice, membersTotal: 0, commandLimit: 512, members: [] }))
    tryVerify(function() { return named(s.view, "appsGpuLine").length >= 3 })
    var lines = named(s.view, "appsGpuLine").map(function(t) { return t.text }).join("\n")
    verify(/VRAM resident 8.0G summed over 2 clients/.test(lines), lines)
    verify(/sums their references, not physical VRAM/.test(lines), lines)
    verify(/Device VRAM used 4.0G of 8.0G/.test(lines), lines)
    compare(named(s.view, "appsVramMeter").length, 0, "no occupancy meter for a possibly overlapping sum")
  }

  function test_protected_rows_cannot_arm_and_kill_keys_stay_in_the_list() {
    var s = seeded([row("hypr", { protected: true }), row("app")])
    keyClick(Qt.Key_J); keyClick(Qt.Key_X); keyClick(Qt.Key_X)
    verify(!s.view.armed)
    compare(signals(s.rt).length, 0, "protected rows never arm")
    keyClick(Qt.Key_K)
    compare(s.panel.nav.region, "sort")
    for (var code of [Qt.Key_X, Qt.Key_F, Qt.Key_Return]) keyClick(code)
    keyClick(Qt.Key_K)
    compare(s.panel.nav.region, "nav")
    for (code of [Qt.Key_X, Qt.Key_F, Qt.Key_Return]) keyClick(code)
    compare(signals(s.rt).length, 0, "page buttons and the sort lens never kill")
    keyClick(Qt.Key_H)
    compare(s.panel.page, "storage")
    for (code of [Qt.Key_X, Qt.Key_F, Qt.Key_Return, Qt.Key_J, Qt.Key_X, Qt.Key_X]) keyClick(code)
    compare(signals(s.rt).length, 0, "Storage never inherits Apps kill keys")
  }

  Window { id: shotWindow; width: 640; height: 1000; visible: true; color: Color.popups.background }
  Component { id: runtimeComponent; RamanRuntime { ipcEnabled: false } }
  Component { id: viewComponent; AppsView { maxHeight: 900 } }
  Component {
    id: fakePanelComponent
    QtObject {
      property var runtime: null
      property string page: "apps"
      property var nav: ({ page: "apps", region: "list" })
      property var summary: ({ total: 16000000 })
      property color foreground: Color.foreground
      property string fontFamily: "DejaVu Sans Mono"
      property var palette: ({ ok: "#7fbf7f", warn: "#e5c07b", critical: "#e06c6c" })
      function focusRegion(region) {}
      function close() {}
    }
  }

  // Standalone view at real widths (the panel's Loader fixes its own width).
  function test_render_states_narrow_and_wide() {
    var rt = createTemporaryObject(runtimeComponent, tc)
    var panel = createTemporaryObject(fakePanelComponent, tc, { runtime: rt })
    var view = createTemporaryObject(viewComponent, shotWindow.contentItem, { panel: panel, width: 420 })
    var rows = [
      row("Chrome (63 tabs of ramen reviews)", { pss: 2306867, swap: 307200, count: 48, cpuCorePercent: 38.4, cpuMachinePercent: 4.8 }),
      row("Broth simulator (rolling boil)", { kind: "session", host: "Ghostty", cpuCorePercent: 176, cpuMachinePercent: 22,
        cpuStatus: "partial", cpuCoverage: { measured: 2, members: 3 } }),
      row("Noodle indexer (members hidden)", { memoryStatus: "partial", memoryCoverage: { measured: 2, members: 3 } }),
      row("secret-sauce-agent (memory hidden)", { pss: null, swap: null, memoryStatus: "unavailable", count: 1 }),
      row("chopstick-sync --watch", { cpuCorePercent: null, cpuMachinePercent: null, cpuStatus: "warming-up" }),
      row("spin-the-noodle.sh", { pss: 896, swap: 0, count: 1, cpuCorePercent: 99.6, cpuMachinePercent: 12.45 }),
      row("Hyprland", { kind: "process", protected: true, count: 1, cpuCorePercent: 0, cpuMachinePercent: 0 })]
    reply(rt, rows, { demo: true, total: 120, nextOffset: 50,
      inventory: { complete: false, incompleteReason: "process-limit", processLimit: 8192 } })
    compare(view.model.rows.length, rows.length)
    view.select(rows[1].id)
    view.armOrKill(view.model.rows[1], false)
    verify(view.armed)
    var out = String(Qt.resolvedUrl("../../shots/")).replace(/^file:\/\//, "")
    for (var light of [false, true]) {
      Color.setTheme(light)
      panel.foreground = Color.foreground
      for (var size of [340, 420, 640]) {
        shotWindow.width = size
        view.width = size
        wait(60)
        var list = findChild(view, "appsList")
        for (var i = 0; i < rows.length; i++) {
          var item = list.itemAtIndex(i)
          if (!item) continue
          verify(item.x >= 0 && item.x + item.width <= size + 1, "row " + i + " fits " + size)
        }
        saveShot(view, out + "apps-" + (light ? "light" : "dark") + "-" + size + ".png")
      }
    }
    Color.setTheme(false)
    panel.foreground = Color.foreground
    view.disarm("")
    view.width = 420
    shotWindow.width = 420
    view.openDetails(view.model.rows[1])
    var d = commands(rt, "apps.details").pop()
    probeOf(rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation, id: rows[1].id,
      demo: true, row: rows[1], membersTotal: 3, commandLimit: 512, members: [
        { pid: 5210, name: "broth-sim", pss: 1101004, swap: 0, cpuMachinePercent: 21.9, cpuStatus: "available",
          command: "broth-sim --simmer --pot=large --threads=2", commandStatus: "demo" },
        { pid: 5208, name: "zsh", pss: 52429, swap: 0, cpuMachinePercent: 0.1, cpuStatus: "available",
          command: "-zsh", commandStatus: "demo" },
        { pid: 5233, name: "stir", pss: 0, swap: 0, cpuMachinePercent: null, cpuStatus: "warming-up",
          command: "stir --every=30s", commandStatus: "demo" }] }))
    tryVerify(function() { return findChild(view, "appsMembers").count === 3 })
    wait(60)
    saveShot(view, out + "apps-details-420.png")
    view.closeDetails()
    // The GPU memory lens, with synthetic readings in every state, and its details.
    var gpuRows = [
      gpuRow("Broth simulator (rolling boil)", { kind: "session", host: "Ghostty", cpuCorePercent: 176, cpuMachinePercent: 22 }),
      gpuRow("Steam Table (game, paused)", { gpuMemoryKb: 1468006, gpuMemoryOverlap: true, gpu: [gpuEntry({ vramKb: 1468006,
        vramSharedKb: 524288, vramOverlap: "possible", clients: 2 })] }),
      gpuRow("Chrome (63 tabs of ramen reviews)", { gpuMemoryKb: 421888, gpuStatus: "partial",
        gpu: [gpuEntry({ vramKb: 421888, status: "partial" })], gpuCoverage: { measured: 47, members: 48 } }),
      gpuRow("miso-mixer", { gpuMemoryKb: 0, gpu: [gpuEntry({ vramKb: 0, systemKb: 4096 })] }),
      row("Electron Kettle", { gpuMemoryKb: null, gpuStatus: "available", gpu: [gpuEntry({ deviceId: "pci:0000:00:02.0",
        driver: "i915", vramKb: null, vramStatus: "unsupported", systemKb: 184320 })] }),
      row("Hyprland", { kind: "process", protected: true, count: 1, gpuMemoryKb: null, gpuStatus: "shared",
        gpu: [gpuEntry({ status: "shared", clients: 0, sharedClients: 1, vramKb: null, systemKb: null })] }),
      row("secret-sauce-agent (memory hidden)", { gpuMemoryKb: null, gpuStatus: "permission-denied", gpu: [] }),
      row("nvim tonkotsu.lua", { gpuMemoryKb: null, gpuStatus: "none", gpu: [] })]
    view.changeSort("cpu")
    reply(rt, gpuRows, { demo: true, gpu: gpuMeta({ demo: true, synthetic: true }), coverage: { gpuOverlap: 1 } })
    view.changeSort("gpu-memory")
    reply(rt, gpuRows, { demo: true, gpu: gpuMeta({ demo: true, synthetic: true }), coverage: { gpuOverlap: 1 } })
    compare(view.model.sort, "gpu-memory")
    for (light of [false, true]) {
      Color.setTheme(light)
      panel.foreground = Color.foreground
      for (size of [340, 420, 640]) {
        shotWindow.width = size
        view.width = size
        wait(60)
        list = findChild(view, "appsList")
        for (i = 0; i < gpuRows.length; i++) {
          item = list.itemAtIndex(i)
          if (item) verify(item.x >= 0 && item.x + item.width <= size + 1, "gpu row " + i + " fits " + size)
        }
        saveShot(view, out + "apps-gpu-" + (light ? "light" : "dark") + "-" + size + ".png")
      }
    }
    Color.setTheme(false)
    panel.foreground = Color.foreground
    view.width = 420
    shotWindow.width = 420
    view.openDetails(view.model.rows[0])
    d = commands(rt, "apps.details").pop()
    probeOf(rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation,
      id: gpuRows[0].id, demo: true, row: gpuRows[0], membersTotal: 0, commandLimit: 512, members: [] }))
    tryVerify(function() { return named(view, "appsVramMeter").length === 1 })
    wait(60)
    saveShot(view, out + "apps-gpu-details-420.png")
    view.closeDetails()
    view.openDetails(view.model.rows[1])
    d = commands(rt, "apps.details").pop()
    probeOf(rt).feed(JSON.stringify({ type: "apps-details", requestId: d.requestId, generation: d.generation,
      id: gpuRows[1].id, demo: true, row: gpuRows[1], membersTotal: 0, commandLimit: 512, members: [] }))
    tryVerify(function() { return /not physical VRAM/.test(named(view, "appsGpuLine").map(function(t) { return t.text }).join(" ")) })
    compare(named(view, "appsVramMeter").length, 0, "the overlapping Steam Table sum has no meter")
    wait(60)
    saveShot(view, out + "apps-gpu-overlap-details-420.png")
  }
}
