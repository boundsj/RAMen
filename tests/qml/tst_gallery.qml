import QtQuick
import QtQuick.Window
import QtTest
import qs.Commons
import "../.."
import "../../Design.js" as Design

// Gallery: the whole panel card and the bar item in fixed, named states, for
// offscreen previews and design review. Data is the probe's own demo-scene
// output (tests/make_qml_fixtures.py); the probe is the recording Process
// stub, so nothing is scanned or signalled. Every capture also checks that no
// visible text box runs past the card's edges, that no Shape outside its
// clip (a scrolled-out row's icon) paints anything, and that semantic text
// reaches 4.5:1 on the surface it is drawn on. grabToImage keeps the runner's
// device pixel ratio, so QT_SCALE_FACTOR=2 produces true 2x captures in
// shots/gallery/. These are Qt offscreen renders, not native Omarchy captures.
TestCase {
  id: tc
  name: "Gallery"
  when: windowShown
  width: 1000; height: 1400
  visible: true

  Window { id: shotWindow; visible: true; width: 1000; height: 1500; color: "transparent" }

  // Omarchy's bar font is the fontconfig "monospace" alias.
  readonly property string galleryFont: "monospace"
  readonly property string outDir: String(Qt.resolvedUrl("../../shots/gallery/")).replace(/^file:\/\//, "")
  property var overflowLog: []

  Component { id: serviceComponent; Service {} }
  Component { id: widgetComponent; BarWidget {} }
  Component {
    id: barComponent
    QtObject {
      property var shell: null
      property color barForeground: Color.foreground
      property color foreground: Color.foreground
      property color background: Color.background
      property bool transparent: false
      property color urgent: Color.urgent
      property string fontFamily: tc.galleryFont
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
  Component { id: stripComponent; Rectangle { color: Color.background } }

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
  function feed(rt, message) { probeOf(rt).feed(JSON.stringify(message)) }
  function commands(rt, name) {
    return probeOf(rt).written.map(function(l) { return l.trim() })
      .filter(function(l) { return l.indexOf("{") === 0 }).map(function(l) { return JSON.parse(l) })
      .filter(function(c) { return c.command === name })
  }
  function last(rt, name) { var all = commands(rt, name); return all[all.length - 1] }
  function signalled(rt) {
    return probeOf(rt).written.filter(function(l) { return l.indexOf("kill ") === 0 || l.indexOf("apps.kill") >= 0 })
  }
  function panelOf(w) {
    for (var i = 0; i < w.data.length; i++) if (w.data[i].item && w.data[i].item.hostWidget !== undefined) return w.data[i].item
    return null
  }
  function cardOf(panel) {
    for (var i = 0; i < panel.data.length; i++) if (panel.data[i].availableCardWidth !== undefined) return panel.data[i]
    return null
  }

  // A larger shell font (base-size 18) also widens the panel: the "wide" case.
  property var savedStyle: null
  function setScale(factor) {
    var f = Style.font
    if (!savedStyle) savedStyle = { scale: Style.scale, caption: f.caption, bodySmall: f.bodySmall, body: f.body,
      subtitle: f.subtitle, title: f.title, heading: f.heading, display: f.display, icon: f.icon }
    Style.scale = savedStyle.scale * factor
    for (var key of ["caption", "bodySmall", "body", "subtitle", "title", "heading", "display", "icon"])
      f[key] = Math.round(savedStyle[key] * factor)
  }
  function cleanup() {
    if (savedStyle) setScale(1)
    Color.setTheme(false)
  }

  function setup(opts) {
    Color.setTheme(!!opts.light)
    var svc = createTemporaryObject(serviceComponent, null)
    // Animations off: captures show the settled state, never a transition.
    var w = createTemporaryObject(widgetComponent, tc, { bar: createTemporaryObject(barComponent, null, {
      shell: { serviceFor: function(id) { return id === "boundsj.raman" ? svc : null } } }),
      settings: Object.assign({ animations: "off" }, opts.settings || {}) })
    tryVerify(function() { return w.runtime === svc.runtime && panelOf(w) && panelOf(w).hostWidget === w })
    var panel = panelOf(w)
    var card = cardOf(panel)
    card.availableCardWidth = opts.width || 420
    card.availableCardHeight = opts.height || 1000
    var scene = opts.scene || "green"
    feed(svc.runtime, fixture("gallery-" + scene + "-summary.json"))
    if (opts.page && opts.page !== "memory") svc.runtime.requestedPage = opts.page
    w.open()
    tryVerify(function() { return panel.opened })
    wait(50)
    if (opts.apps !== false && scene !== "apps") feed(svc.runtime, fixture("gallery-" + scene + "-apps.json"))
    return { svc: svc, rt: svc.runtime, w: w, panel: panel, card: card }
  }

  // Every visible, non-empty text box must lie within the card: horizontally
  // always, vertically unless a scrolling (clipping) ancestor owns it.
  function overflows(root, bound) {
    var out = []
    function walk(item, clipped) {
      if (!item || item.visible === false || item.opacity === 0) return
      if (item.text !== undefined && typeof item.text === "string" && item.text !== ""
          && item.font !== undefined && item.width > 0) {
        var p = item.mapToItem(bound, 0, 0)
        if (p.x < -1 || p.x + item.width > bound.width + 1)
          out.push(JSON.stringify(item.text.slice(0, 40)) + " x=" + Math.round(p.x) + " w=" + Math.round(item.width))
        else if (!clipped && p.y + item.height > bound.height + 1)
          out.push(JSON.stringify(item.text.slice(0, 40)) + " below the card: y=" + Math.round(p.y) + " h=" + Math.round(item.height))
      }
      var inner = clipped || item.clip === true
      var kids = item.children || []
      var hasContent = false
      for (var i = 0; i < kids.length; i++) {
        if (kids[i] === item.contentItem) hasContent = true
        walk(kids[i], inner)
      }
      if (item.contentItem && !hasContent) walk(item.contentItem, inner)
    }
    walk(root, false)
    return out
  }

  // Shapes (vector icons, chart marks) lying wholly outside a clipping
  // ancestor, such as the lock of a row below a list's viewport.
  function clippedShapes(root) {
    var out = []
    function box(item) {
      var a = item.mapToItem(root, 0, 0), b = item.mapToItem(root, item.width, item.height)
      return { x0: Math.min(a.x, b.x), y0: Math.min(a.y, b.y), x1: Math.max(a.x, b.x), y1: Math.max(a.y, b.y) }
    }
    function walk(item, clips) {
      if (!item || item.visible === false || item.opacity === 0) return
      if (item.preferredRendererType !== undefined && item.width > 0 && item.height > 0) {
        var r = box(item)
        for (var c of clips)
          if (r.x1 <= c.x0 || r.x0 >= c.x1 || r.y1 <= c.y0 || r.y0 >= c.y1) { out.push(item); break }
      }
      var inner = item.clip === true ? clips.concat([box(item)]) : clips
      var kids = item.children || [], hasContent = false
      for (var i = 0; i < kids.length; i++) {
        if (kids[i] === item.contentItem) hasContent = true
        walk(kids[i], inner)
      }
      if (item.contentItem && !hasContent) walk(item.contentItem, inner)
    }
    walk(root, [])
    return out
  }
  // Hiding them must not change a byte of the capture (grabToImage, the same
  // full render the previews use).
  property int probes: 0
  function captureBytes(item) {
    var file = outDir + "../clip-probe-" + (probes++) + ".png"
    var done = false
    item.grabToImage(function(result) { done = result.saveToFile(file) })
    tryVerify(function() { return done }, 4000, "captured " + file)
    var xhr = new XMLHttpRequest()
    xhr.open("GET", "file://" + file, false)
    xhr.responseType = "arraybuffer"
    xhr.send()
    return new Uint8Array(xhr.response)
  }
  function clipLeaks(card) {
    var hidden = clippedShapes(card)
    if (!hidden.length) return ""
    var shown = captureBytes(card)
    for (var h of hidden) h.opacity = 0
    var without = captureBytes(card)
    for (h of hidden) h.opacity = 1
    var same = shown.length === without.length && shown.length > 0
    for (var i = 0; same && i < shown.length; i++) same = shown[i] === without[i]
    return same ? "" : hidden.length + " Shapes outside their clip paint pixels"
  }

  // Semantic ink (green/yellow/red text, the badge and "Kill?") against the
  // opaque colour under it: the card, then each filled ancestor composited.
  function inkFailures(root, panel) {
    if (!panel || !panel.ink) return []
    var inks = [panel.ink.ok, panel.ink.warn, panel.ink.critical, panel.confirmInk, panel.badgeInk].map(Design.hex)
    var out = []
    function under(item) {
      var chain = []
      for (var p = item.parent; p && p !== root.parent; p = p.parent) chain.unshift(p)
      var c = Design.hex(Color.popups.background)
      for (var q of chain)
        if (q.visible !== false && q.color !== undefined && q.radius !== undefined && q.border !== undefined && q.opacity > 0)
          c = Design.over(Qt.rgba(q.color.r, q.color.g, q.color.b, q.color.a * q.opacity), c)
      return c
    }
    function walk(item) {
      if (!item || item.visible === false || item.opacity === 0) return
      if (typeof item.text === "string" && item.text !== "" && item.font !== undefined && item.color !== undefined
          && inks.indexOf(Design.hex(item.color)) >= 0) {
        var bg = under(item), ratio = Design.contrast(Design.hex(item.color), bg)
        if (ratio < 4.5) out.push(JSON.stringify(item.text.slice(0, 30)) + " " + Design.hex(item.color) + " on " + bg + " " + ratio.toFixed(2))
      }
      var kids = item.children || [], hasContent = false
      for (var i = 0; i < kids.length; i++) {
        if (kids[i] === item.contentItem) hasContent = true
        walk(kids[i])
      }
      if (item.contentItem && !hasContent) walk(item.contentItem)
    }
    walk(root)
    return out
  }

  function save(item, name) {
    var done = false
    item.grabToImage(function(result) { done = result.saveToFile(outDir + name + ".png") })
    tryVerify(function() { return done }, 4000, "saved " + name)
  }

  function shot(s, name) {
    var home = s.card.parent
    s.card.parent = shotWindow.contentItem
    s.card.x = 0; s.card.y = 0
    waitForRendering(s.card)
    wait(120)
    var bad = overflows(s.card, s.card)
    if (bad.length) overflowLog = overflowLog.concat([name + ": " + bad.join("; ")])
    var leak = clipLeaks(s.card)
    if (leak) overflowLog = overflowLog.concat([name + ": " + leak])
    var faint = inkFailures(s.card, s.panel)
    if (faint.length) overflowLog = overflowLog.concat([name + ": text under 4.5:1: " + faint.join("; ")])
    save(s.card, name)
    s.card.parent = home
    if (s.card.focusTarget) s.card.focusTarget.forceActiveFocus()
    wait(20)
  }

  function expectNoOverflow(prefix) {
    var mine = overflowLog.filter(function(l) { return l.indexOf(prefix) === 0 })
    compare(mine.length, 0, "drawn outside the card or a clip, or unreadable: " + mine.join(" | "))
  }

  // --- bar item ---------------------------------------------------------------
  function barShot(scene, name, light, vertical, label) {
    Color.setTheme(!!light)
    var svc = createTemporaryObject(serviceComponent, null)
    var bar = createTemporaryObject(barComponent, null, { vertical: !!vertical,
      shell: { serviceFor: function(id) { return id === "boundsj.raman" ? svc : null } } })
    var strip = createTemporaryObject(stripComponent, shotWindow.contentItem)
    var w = createTemporaryObject(widgetComponent, strip, { bar: bar, settings: label ? { label: label } : {} })
    tryVerify(function() { return w.runtime === svc.runtime })
    if (scene) feed(svc.runtime, fixture("gallery-" + scene + "-summary.json"))
    wait(400) // past the gauge's 300 ms transition
    var pad = 10
    if (vertical) {
      strip.width = 28 + pad * 2; strip.height = w.implicitHeight + pad * 2
      w.width = 28; w.height = w.implicitHeight
    } else {
      strip.width = w.implicitWidth + pad * 2; strip.height = 26 + pad * 2
      w.width = w.implicitWidth; w.height = 26
    }
    w.x = pad; w.y = pad
    waitForRendering(strip)
    wait(60)
    save(strip, name)
  }

  function test_bar() {
    barShot("green", "bar-green-dark")
    barShot("yellow", "bar-yellow-dark")
    barShot("red", "bar-red-dark")
    barShot("green", "bar-green-light", true)
    barShot("yellow", "bar-yellow-light", true)
    barShot("red", "bar-red-light", true)
    barShot("red", "bar-red-vertical-dark", false, true)
    barShot("yellow", "bar-yellow-gauge-only-dark", false, false, "none")
    barShot("", "bar-starting-dark")
  }

  // --- Memory -----------------------------------------------------------------
  function armFirst(s) {
    s.panel.selectRow(0)
    s.panel.armOrKill(s.panel.apps[0], false)
    compare(s.panel.armedId, s.panel.apps[0].id)
  }
  function test_memory() {
    var s = setup({ scene: "green" }); shot(s, "memory-green-420-dark")
    s = setup({ scene: "yellow" }); shot(s, "memory-yellow-420-dark")
    s = setup({ scene: "red" }); armFirst(s); shot(s, "memory-red-armed-420-dark")
    compare(signalled(s.rt).length, 0, "arming never signals")
    s = setup({ scene: "red", light: true }); armFirst(s); shot(s, "memory-red-armed-420-light")
    s = setup({ scene: "yellow", light: true }); shot(s, "memory-yellow-420-light")
    s = setup({ scene: "red", width: 340 }); s.panel.selectRow(6); shot(s, "memory-red-protected-340-dark")
    s = setup({ scene: "green" }); s.panel.focusRegion("nav"); shot(s, "memory-green-tabs-focus-420-dark")
    s = setup({ scene: "green", apps: false }); shot(s, "memory-scanning-420-dark")
    // The closest allowed thresholds at the end of the scale: labels packed inside the meter.
    s = setup({ scene: "red", settings: { warnPercent: 99, criticalPercent: 100 } }); shot(s, "memory-thresholds-99-100-420-dark")
    setScale(1.5)
    s = setup({ scene: "red", width: 700 }); armFirst(s); shot(s, "memory-red-armed-wide-dark")
    setScale(1)
    expectNoOverflow("memory-")
  }

  // --- History ----------------------------------------------------------------
  function history(opts, file) {
    var s = setup(Object.assign({ scene: "yellow", page: "history" }, opts))
    tryVerify(function() { return !!s.panel.historyView })
    s.view = s.panel.historyView
    if (file) {
      var message = fixture(file)
      if (message.window && message.window !== s.view.window) {
        s.view.setWindow(message.window)
      }
      message.requestId = last(s.rt, "history.get").requestId
      feed(s.rt, message)
      tryVerify(function() { return s.view.reply !== null })
    }
    return s
  }
  function test_history() {
    var s = history({}, "demo-1h.json"); shot(s, "history-1h-420-dark")
    s = history({ light: true }, "demo-1h.json"); shot(s, "history-1h-420-light")
    s = history({}, "demo-5m.json"); shot(s, "history-5m-420-dark")
    s = history({}, "demo-24h.json"); shot(s, "history-24h-420-dark")
    s = history({}, "demo-1h.json"); s.view.selectIncident(0, true); shot(s, "history-receipt-420-dark")
    s = history({ light: true }, "demo-1h.json"); s.view.selectIncident(0, true); shot(s, "history-receipt-420-light")
    s = history({ width: 340 }, "demo-1h.json")
    s.panel.focusRegion("chart"); s.view.moveCursor(-120)
    shot(s, "history-chart-focus-340-dark")
    s = history({}, "live-empty-1h.json"); shot(s, "history-empty-420-dark")
    s = history({}, "live-nopsi-1h.json"); shot(s, "history-no-psi-420-dark")
    setScale(1.5)
    s = history({ width: 700 }, "demo-1h.json"); shot(s, "history-1h-wide-dark")
    setScale(1)
    compare(signalled(s.rt).length, 0)
    expectNoOverflow("history-")
  }

  // --- Storage ------------------------------------------------------------------
  function storageReply(view, purpose, fields) {
    var id = ""
    for (var key in view.session.pending) if (view.session.pending[key].purpose === purpose) id = key
    verify(!!id, purpose + " outstanding")
    view.accept(Object.assign({ requestId: id, clientId: view.session.clientId, generation: view.session.generation }, fields))
  }
  // Original synthetic "Serving Board" material (see scripts/storage-demo.py).
  function servingBoard() {
    var mib = 1048576
    function entry(id, name, kind, bytes, extra) {
      return Object.assign({ nodeId: id, parentId: "root", name: name, kind: kind, ownAllocatedBytes: kind === "file" ? bytes : 4096,
        apparentBytes: bytes, knownAllocatedBytes: bytes, coverage: "complete", actionIdentity: true, copyRepresentable: true }, extra || {})
    }
    var rows = [
      entry("broth", "broth-stock", "directory", 412 * mib),
      entry("noodles", "noodles", "directory", 236 * mib),
      entry("toppings", "toppings", "directory", 118 * mib, { coverage: "partial" }),
      entry("menu", "menu-photos.tar", "file", 64 * mib),
      entry("hidden", ".tare", "directory", 22 * mib, { hidden: true }),
      entry("notes", "recipe notes.md", "file", 1.5 * mib),
      entry("crumbs", "crumbs", "file", 12288)]
    return { rows: rows, total: 812 * mib + 12288 }
  }
  function storage(opts) {
    var s = setup(Object.assign({ scene: "green", page: "storage" }, opts))
    tryVerify(function() { return !!s.panel.storageView })
    var v = s.view = s.panel.storageView
    storageReply(v, "capacity", { type: "storage-capacity", totalBytes: 512e9, usedBytes: 371e9, reservedBytes: 9e9,
      availableBytes: 132e9, scope: "Serving Board", readOnly: false })
    if (opts.unscanned) {
      storageReply(v, "cached", { type: "storage-result", status: "ready", snapshot: null })
      return s
    }
    storageReply(v, "cached", { type: "storage-result", status: "ready",
      snapshot: { snapshotId: "gallery", rootNodeId: "root", coverage: "partial", capturedAt: Date.now() / 1000 - 600 } })
    var board = servingBoard()
    var root = { nodeId: "root", name: "Serving Board", parentId: null, kind: "directory", ownAllocatedBytes: 4096,
      knownAllocatedBytes: board.total, coverage: "partial" }
    storageReply(v, "ledger", { type: "storage-children", snapshotId: "gallery", nodeId: "root", offset: 0, filter: "",
      total: board.rows.length, nextOffset: null, node: root, rows: board.rows, segments: board.rows, other: null,
      breadcrumbs: [{ nodeId: "root", name: "Serving Board" }] })
    var sub = function(id, names) {
      var bytes = board.rows.filter(function(r) { return r.nodeId === id })[0].knownAllocatedBytes
      var share = [0.55, 0.3, 0.15]
      var kids = names.map(function(n, i) { return { nodeId: id + "-" + i, parentId: id, name: n, kind: "file",
        knownAllocatedBytes: Math.round(bytes * share[i]), ownAllocatedBytes: Math.round(bytes * share[i]), coverage: "complete" } })
      return { type: "storage-children", snapshotId: "gallery", nodeId: id, offset: 0, filter: "", total: kids.length,
        nextOffset: null, node: board.rows.filter(function(r) { return r.nodeId === id })[0], rows: kids, segments: kids, other: null }
    }
    // Band replies for the first directories, as the view requests them.
    var bands = { broth: ["tonkotsu", "shoyu", "miso"], noodles: ["wavy", "straight", "thick"], toppings: ["chashu", "nori", "egg"] }
    for (var i = 0; i < 6; i++) {
      var pendingBand = ""
      for (var key in v.session.pending) if (v.session.pending[key].purpose === "band") pendingBand = key
      if (!pendingBand) break
      var nodeId = v.session.pending[pendingBand].args.nodeId
      storageReply(v, "band", sub(nodeId, bands[nodeId] || ["a", "b", "c"]))
    }
    s.panel.focusRegion("ledger")
    v.selectRow("broth")
    return s
  }
  function test_storage() {
    var s = storage({}); shot(s, "storage-ready-420-dark")
    s = storage({ light: true }); shot(s, "storage-ready-420-light")
    s = storage({ width: 340 }); shot(s, "storage-ready-340-dark")
    s = storage({})
    s.view.scan()
    storageReply(s.view, "scan", { type: "storage-progress", status: "scanning", scanId: "0123456789abcdef0123456789abcdef",
      entries: 18342, unreadable: 2, excluded: 1, changed: 0 })
    shot(s, "storage-scanning-420-dark")
    s = storage({ unscanned: true }); shot(s, "storage-unscanned-420-dark")
    setScale(1.5)
    s = storage({ width: 700 }); shot(s, "storage-ready-wide-dark")
    setScale(1)
    compare(signalled(s.rt).length, 0)
    expectNoOverflow("storage-")
  }

  // --- Apps ---------------------------------------------------------------------
  function appsReply(s, file, extra) {
    var q = last(s.rt, "apps.query")
    var page = fixture(file)
    feed(s.rt, Object.assign(page, { requestId: q.requestId, generation: q.generation, query: q.query, offset: q.offset }, extra || {}))
  }
  function apps(opts, sort) {
    var s = setup(Object.assign({ scene: "apps", page: "apps" }, opts))
    tryVerify(function() { return !!s.panel.appsView })
    s.view = s.panel.appsView
    appsReply(s, "gallery-apps-gpu-memory.json")
    tryVerify(function() { return s.view.model.rows.length > 0 })
    if (sort && sort !== "memory") {
      s.view.changeSort(sort)
      appsReply(s, "gallery-apps-gpu-" + sort + ".json")
      tryVerify(function() { return s.view.model.sort === sort && s.view.model.rows.length > 0 })
    }
    s.panel.focusRegion("list")
    return s
  }
  function rowNamed(s, prefix) {
    return s.view.model.rows.filter(function(r) { return r.name.indexOf(prefix) === 0 })[0]
  }
  function details(s, prefix) {
    var row = rowNamed(s, prefix)
    s.view.openDetails(row)
    var d = last(s.rt, "apps.details")
    var reply = fixture("gallery-apps-gpu-details-" + row.id.replace(":", "-") + ".json")
    feed(s.rt, Object.assign(reply, { requestId: d.requestId, generation: d.generation }))
    tryVerify(function() { return !!s.view.details && !!s.view.details.data })
  }
  function test_apps() {
    var s = apps({}); s.view.select(rowNamed(s, "Broth").id); s.view.armOrKill(rowNamed(s, "Broth"), false)
    verify(s.view.armed); shot(s, "apps-memory-armed-420-dark")
    compare(signalled(s.rt).length, 0, "arming never signals")
    s = apps({ light: true }); s.view.select(rowNamed(s, "Chrome").id); shot(s, "apps-memory-420-light")
    s = apps({}, "cpu"); s.panel.focusRegion("sort"); shot(s, "apps-cpu-sort-focus-420-dark")
    s = apps({}, "gpu-memory"); s.view.select(rowNamed(s, "Steam").id); shot(s, "apps-gpu-420-dark")
    s = apps({ width: 340 }); s.view.select(rowNamed(s, "Hyprland").id); shot(s, "apps-memory-protected-340-dark")
    s = apps({}); details(s, "Broth"); shot(s, "apps-details-420-dark")
    s = apps({ light: true }); details(s, "Broth"); shot(s, "apps-details-420-light")
    s = apps({}, "gpu-memory"); details(s, "Steam"); shot(s, "apps-gpu-overlap-details-420-dark")
    s = apps({})
    s.view.changeQuery("wasabi")
    appsReply(s, "gallery-apps-gpu-memory.json", { rows: [], total: 0, nextOffset: null })
    shot(s, "apps-search-empty-420-dark")
    setScale(1.5)
    s = apps({ width: 700 }); s.view.select(rowNamed(s, "Chrome").id); shot(s, "apps-memory-wide-dark")
    setScale(1)
    compare(signalled(s.rt).length, 0)
    expectNoOverflow("apps-")
  }
}
