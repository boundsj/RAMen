import QtQuick
import QtTest
import qs.Commons
import "../.."
import "../../Design.js" as Design
import "../../Level.js" as Level

// The visual design layer through the real BarWidget, Panel and runtime (the
// probe is the recording Process stub): the bowl gauge's meaning, contrast on
// light themes, vector icons instead of font glyphs, the two-step kill through
// the new buttons, target sizes, reduced motion and keyboard focus.
TestCase {
  id: tc
  name: "DesignLayer"
  when: windowShown
  width: 900; height: 1000
  visible: true

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
  function probeOf(rt) {
    for (var i = 0; i < rt.data.length; i++) if (rt.data[i].stdinEnabled === true) return rt.data[i]
    return null
  }
  function written(rt) { return probeOf(rt).written.map(function(l) { return l.trim() }) }
  function kills(rt) { return written(rt).filter(function(l) { return l.indexOf("kill ") === 0 }) }
  function panelOf(w) {
    for (var i = 0; i < w.data.length; i++) if (w.data[i].item && w.data[i].item.hostWidget !== undefined) return w.data[i].item
    return null
  }
  function summary(rt, usedPct, psi) {
    probeOf(rt).feed(JSON.stringify({ type: "summary", total: 8000000, used: 80000 * usedPct, available: 8000000 - 80000 * usedPct,
      swapTotal: 0, swapUsed: 0, zramRam: 0, psiSome10: psi || 0.5 }))
  }
  function setup(settings, barProps) {
    var svc = createTemporaryObject(serviceComponent, null)
    var bar = createTemporaryObject(barComponent, null, Object.assign({
      shell: { serviceFor: function(id) { return id === "boundsj.raman" ? svc : null } } }, barProps || {}))
    var w = createTemporaryObject(widgetComponent, tc, { bar: bar, settings: settings || {} })
    tryVerify(function() { return w.runtime === svc.runtime && panelOf(w) && panelOf(w).hostWidget === w })
    return { svc: svc, rt: svc.runtime, w: w, bar: bar, panel: panelOf(w) }
  }
  function openWithApps(s) {
    summary(s.rt, 50)
    s.panel.parent = tc
    s.w.open()
    tryVerify(function() { return s.panel.opened })
    wait(50)
    probeOf(s.rt).feed(JSON.stringify({ type: "apps", apps: [
      { id: "a1", name: "Chrome", kind: "app", host: "", count: 9, pss: 900000, swap: 0, protected: false },
      { id: "a2", name: "Hyprland", kind: "process", host: "", count: 1, pss: 100000, swap: 0, protected: true }] }))
    wait(30)
  }
  function all(item, test, out) {
    out = out || []
    if (!item) return out
    if (test(item)) out.push(item)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) all(kids[i], test, out)
    if (item.contentItem && item.contentItem !== item) {
      var listed = false
      for (var k = 0; k < kids.length; k++) if (kids[k] === item.contentItem) listed = true
      if (!listed) all(item.contentItem, test, out)
    }
    return out
  }
  function named(root, name) { return all(root, function(i) { return i.objectName === name && i.visible }) }
  function cleanup() { Color.setTheme(false) }

  function test_bar_gauge_is_a_linear_level_with_steam_for_tight_and_critical() {
    var s = setup()
    var gauge = findChild(s.w, "ramanGauge")
    verify(gauge, "the bar draws the bowl gauge")
    compare(gauge.hasReading, false, "no reading yet: outline only, never an empty-as-zero fill")
    summary(s.rt, 40)
    tryCompare(gauge, "level", 0.4, 1000)
    compare(gauge.steam, 0)
    summary(s.rt, 80)
    compare(s.w.level, "warn")
    compare(gauge.steam, 1, "tight shows one wisp")
    summary(s.rt, 50, 30)
    compare(s.w.level, "critical", "pressure alone makes it critical")
    compare(gauge.fraction, 0.5, "the level still shows used memory, not pressure")
    compare(gauge.steam, 2, "critical shows two wisps, not only a colour change")
  }

  function test_light_bar_reads_and_keeps_theme_hue() {
    Color.setTheme(true)
    var s = setup({}, { background: "#e6e7ed", foreground: "#343b58", barForeground: "#343b58" })
    summary(s.rt, 80)
    compare(s.w.level, "warn")
    verify(Design.contrast(s.w.levelInk, s.bar.background) >= 4.5, "label " + s.w.levelInk)
    verify(Design.contrast(s.w.levelTone, s.bar.background) >= 3, "broth " + s.w.levelTone)
    verify(Math.abs(Level.hue(String(s.w.levelInk)) - Level.hue(String(s.w.levelColor))) < 2, "same hue, only darker")
    var clear = setup({}, { transparent: true })
    summary(clear.rt, 80)
    compare(String(clear.w.levelInk), String(clear.w.levelColor), "an unknown (transparent) background keeps the theme colour")
  }

  function test_no_page_depends_on_nerd_font_glyphs() {
    var s = setup()
    openWithApps(s)
    s.panel.selectRow(0)
    var glyph = /[-]|[\uDB80-\uDBFF][\uDC00-\uDFFF]/
    for (var page of ["memory", "history", "storage", "apps"]) {
      s.panel.selectPage(page)
      wait(60)
      var texts = all(s.panel.parent, function(i) {
        return i.visible && typeof i.text === "string" && i.font !== undefined && glyph.test(i.text)
      })
      compare(texts.map(function(t) { return t.text }), [], page + " shows no private-use (icon font) glyphs")
    }
  }

  function test_memory_kill_through_vector_buttons() {
    var s = setup()
    openWithApps(s)
    var list = named(s.panel.parent, "memoryList")[0]
    verify(list)
    var first = list.itemAtIndex(0)
    mouseMove(first, first.width / 2, first.height / 2)
    tryCompare(s.panel, "selectedIndex", 0)
    var kill = named(first, "memoryKill")[0]
    verify(kill, "the selected row shows its kill button")
    compare(kill.iconName, "close")
    verify(kill.width >= Style.space(24) && kill.height >= Style.space(24), "a 24 px target")
    mouseClick(kill, kill.width / 2, kill.height / 2)
    compare(kills(s.rt).length, 0, "the first click only arms")
    var confirm = named(first, "memoryConfirm")[0]
    verify(confirm)
    compare(all(confirm, function(i) { return i.text === "Kill?" }).length, 1)
    mouseClick(confirm, confirm.width / 2, confirm.height / 2)
    compare(kills(s.rt), ["kill TERM a1"])
    // Right-click arms a force kill, confirmed as "Force?".
    mouseClick(first, first.width / 2, first.height / 2, Qt.RightButton)
    compare(s.panel.armedForce, true)
    verify(all(named(first, "memoryConfirm")[0], function(i) { return i.text === "Force?" }).length === 1)
    keyClick(Qt.Key_Escape)
    // The protected row shows a lock and never a kill button.
    var second = list.itemAtIndex(1)
    mouseMove(second, second.width / 2, second.height / 2)
    tryCompare(s.panel, "selectedIndex", 1)
    compare(named(second, "memoryProtected").length, 1)
    compare(named(second, "memoryKill").length, 0)
    mouseClick(second, second.width / 2, second.height / 2, Qt.RightButton)
    compare(s.panel.armedId, "", "protected rows never arm")
    compare(kills(s.rt).length, 1)
  }

  function test_reduced_motion_reaches_the_bowls() {
    var s = setup({ animations: "off" })
    openWithApps(s)
    compare(findChild(s.w, "ramanGauge").animationDuration, 0)
    var hero = named(s.panel.parent, "ramanHeroBowl")[0]
    verify(hero)
    compare(hero.animationDuration, 0)
    compare(hero.fraction, 0.5)
  }

  function test_page_buttons_show_selection_and_keyboard_focus() {
    var s = setup()
    openWithApps(s)
    var tabs = all(s.panel.parent, function(i) { return i.pageName !== undefined && i.visible })
    compare(tabs.length, 4)
    var memory = tabs.filter(function(t) { return t.pageName === "memory" })[0]
    verify(memory.selected)
    verify(!memory.focused)
    s.panel.focusRegion("nav")
    verify(memory.focused)
    verify(memory.border.width >= 2, "focused: a 2 px outline")
    keyClick(Qt.Key_L)
    compare(s.panel.page, "history")
    verify(tabs.filter(function(t) { return t.pageName === "history" })[0].focused)
  }

  // Close and end-of-scale settings (1-100, either order): both labels stay
  // inside the meter and apart, the earlier one moving left when needed.
  function test_threshold_labels_never_overlap_data() {
    return [[88, 90], [99, 100], [100, 99], [100, 100], [97, 98], [1, 2], [2, 1], [1, 1], [1, 100], [75, 90]]
      .map(function(t) { return { tag: t[0] + "/" + t[1], warn: t[0], critical: t[1] } })
  }
  function test_threshold_labels_never_overlap(data) {
    var s = setup({ warnPercent: data.warn, criticalPercent: data.critical })
    openWithApps(s)
    var meter = named(s.panel.parent, "memoryMeter")[0]
    verify(meter)
    var left = meter.mapToItem(tc, 0, 0).x, right = left + meter.width
    var labels = named(s.panel.parent, "thresholdLabel")
    compare(labels.map(function(l) { return l.text }), [data.warn + "%", data.critical + "%"])
    var boxes = labels.map(function(l) { var p = l.mapToItem(tc, 0, 0); return { x: p.x, w: l.implicitWidth, text: l.text } })
    for (var b of boxes) verify(b.x >= left - 0.5 && b.x + b.w <= right + 0.5, b.text + " inside the meter: " + JSON.stringify(boxes))
    boxes.sort(function(a, b) { return a.x - b.x })
    verify(boxes[1].x >= boxes[0].x + boxes[0].w + Style.space(4) - 0.5, "labels never touch: " + JSON.stringify(boxes))
    if (Math.abs(data.warn - data.critical) >= 30) {
      // Far apart: each is centred on its own tick.
      for (var l of labels) {
        var tick = left + meter.width * Number(l.text.replace("%", "")) / 100
        var mid = l.mapToItem(tc, 0, 0).x + l.implicitWidth / 2
        if (tick - l.implicitWidth / 2 >= left && tick + l.implicitWidth / 2 <= right) fuzzyCompare(mid, tick, 1.5)
      }
    }
  }

  // --- semantic text on tinted surfaces -------------------------------------
  // The opaque colour behind an item: the card, then each filled ancestor's
  // colour composited over it, as the scene graph draws them.
  function surfaceUnder(item) {
    var chain = []
    for (var p = item.parent; p; p = p.parent) chain.unshift(p)
    var c = Design.hex(Color.popups.background)
    for (var q of chain) {
      if (q.visible === false) continue
      var isFill = q.color !== undefined && q.radius !== undefined && q.border !== undefined
      if (isFill && q.opacity > 0) c = Design.over(Qt.rgba(q.color.r, q.color.g, q.color.b, q.color.a * q.opacity), c)
    }
    return c
  }
  function textIn(root, value) {
    return all(root, function(i) { return i.visible && i.text === value && i.font !== undefined })[0]
  }
  function readsOn(text, what) {
    var bg = surfaceUnder(text)
    var ratio = Design.contrast(Design.hex(text.color), bg)
    verify(ratio >= 4.5, what + ": " + Design.hex(text.color) + " on " + bg + " = " + ratio.toFixed(3))
    return ratio
  }
  function themes() { return [{ tag: "dark", light: false }, { tag: "light", light: true }] }

  function test_memory_kill_confirmation_reads_at_rest_and_under_the_pointer_data() { return themes() }
  function test_memory_kill_confirmation_reads_at_rest_and_under_the_pointer(data) {
    Color.setTheme(data.light)
    var s = setup()
    openWithApps(s)
    var first = named(s.panel.parent, "memoryList")[0].itemAtIndex(0)
    mouseMove(first, first.width / 4, first.height / 2)
    tryCompare(s.panel, "selectedIndex", 0)
    s.panel.armOrKill(s.panel.apps[0], false)
    var confirm = named(first, "memoryConfirm")[0]
    verify(confirm)
    wait(120) // past the row cursor's colour transition
    verify(first.hasCursor, "armed on a cursor row")
    var text = textIn(confirm, "Kill?")
    fuzzyCompare(confirm.color.a, Design.CONFIRM_TINT, 0.01)
    readsOn(text, "Kill? at rest")
    mouseMove(confirm, confirm.width / 2, confirm.height / 2)
    tryVerify(function() { return Math.abs(confirm.color.a - Design.CONFIRM_HOT_TINT) < 0.01 }, 1000, "the pointer deepens the tint")
    wait(120)
    readsOn(text, "Kill? under the pointer")
    compare(kills(s.rt).length, 0, "hovering never signals")
  }

  function test_percentage_badge_reads_on_its_tint_data() { return themes() }
  function test_percentage_badge_reads_on_its_tint(data) {
    Color.setTheme(data.light)
    var s = setup({ animations: "off" })
    openWithApps(s)
    var badge = named(s.panel.parent, "memoryPercent")[0]
    verify(badge)
    for (var step of [[50, "ok"], [80, "warn"], [95, "critical"]]) {
      summary(s.rt, step[0])
      tryCompare(s.w, "level", step[1])
      wait(30)
      verify(badge.parent.color.a > 0.1, "the badge is tinted")
      readsOn(badge, "badge " + step[1])
    }
  }

  // Apps: the same confirmation, through the page's own rows.
  function appsRow(id, fields) {
    return Object.assign({ id: id, name: id, kind: "app", host: "", count: 3, protected: false, generation: "g-" + id,
      pss: 400000, swap: 1024, memoryStatus: "available", memoryCoverage: { measured: 3, members: 3 },
      cpuCorePercent: 40, cpuMachinePercent: 5, cpuStatus: "available", cpuCoverage: { measured: 3, members: 3 },
      sampledAt: 1 }, fields || {})
  }
  function appsReply(rt, rows) {
    var all = written(rt).filter(function(l) { return l.indexOf("{") === 0 }).map(function(l) { return JSON.parse(l) })
      .filter(function(c) { return c.command === "apps.query" })
    var q = all[all.length - 1]
    probeOf(rt).feed(JSON.stringify({ type: "apps-page", requestId: q.requestId, schemaVersion: 1, time: 1, generation: q.generation,
      status: "ready", stale: false, demo: false, snapshot: 5, sampledAt: 1, query: q.query, sort: q.sort, offset: q.offset,
      limit: 50, total: rows.length, nextOffset: null, inventory: { complete: true, processLimit: 8192, groupLimit: 4096 },
      cpu: { status: "available", onlineCpus: 8 }, coverage: {}, units: {}, rows: rows }))
  }
  function openApps(s, rows) {
    summary(s.rt, 50)
    s.panel.parent = tc
    s.w.open()
    tryVerify(function() { return s.panel.opened })
    wait(50)
    s.panel.selectPage("apps")
    tryVerify(function() { return !!s.panel.appsView })
    appsReply(s.rt, rows)
    tryVerify(function() { return s.panel.appsView.model.rows.length === rows.length })
    s.panel.focusRegion("list")
    wait(30)
    return s.panel.appsView
  }

  function test_apps_kill_confirmation_reads_at_rest_and_under_the_pointer_data() { return themes() }
  function test_apps_kill_confirmation_reads_at_rest_and_under_the_pointer(data) {
    Color.setTheme(data.light)
    var s = setup()
    var view = openApps(s, [appsRow("chrome"), appsRow("slack")])
    var list = named(view, "appsList")[0]
    var first = list.itemAtIndex(0)
    mouseMove(first, first.width / 4, first.height / 2)
    tryVerify(function() { return view.model.selectedId === "chrome" })
    view.armOrKill(view.model.rows[0], false)
    verify(view.armed)
    var confirm = named(first, "appsConfirm")[0]
    verify(confirm)
    wait(120)
    var text = textIn(confirm, "Kill?")
    readsOn(text, "Apps Kill? at rest")
    mouseMove(confirm, confirm.width / 2, confirm.height / 2)
    tryVerify(function() { return Math.abs(confirm.color.a - Design.CONFIRM_HOT_TINT) < 0.01 }, 1000, "the pointer deepens the tint")
    wait(120)
    readsOn(text, "Apps Kill? under the pointer")
    compare(written(s.rt).filter(function(l) { return l.indexOf("apps.kill") >= 0 }).length, 0, "hovering never signals")
  }

  // --- rows scrolled out of a list ------------------------------------------
  // Qt's software scene graph (the offscreen renderer here) paints a Shape that
  // lies wholly outside its clip with no clip at all. Rows wholly past a list
  // are culled, but a row whose top edge peeks into the viewport is drawn, and
  // its lock sits below the list: it must not land on the controls below.
  // A full render (grabToImage, as the gallery uses) shows the leak.
  property int captures: 0
  function capture(item) {
    var file = String(Qt.resolvedUrl("../../shots/")).replace(/^file:\/\//, "") + "design-capture-" + (captures++) + ".png"
    var done = false
    item.grabToImage(function(result) { done = result.saveToFile(file) })
    tryVerify(function() { return done }, 4000, "captured " + file)
    var xhr = new XMLHttpRequest()
    xhr.open("GET", "file://" + file, false)
    xhr.responseType = "arraybuffer"
    xhr.send()
    return new Uint8Array(xhr.response)
  }
  function sameBytes(a, b) {
    if (a.length !== b.length) return false
    for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
    return true
  }
  function lockPaintsOutside(list, lockName, index) {
    list.positionViewAtIndex(index, ListView.Beginning)
    var item = list.itemAtIndex(index)
    verify(item, "the protected row exists")
    for (var k = 0; k < 5; k++) { list.contentY = item.y - list.height + Style.space(2); wait(30) }
    var bottom = list.mapToItem(tc, 0, 0).y + list.height
    verify(item.mapToItem(tc, 0, 0).y < bottom, "the row's top edge is inside the viewport")
    var lock = named(item, lockName)[0]
    verify(lock, "the row shows its lock")
    var p = lock.mapToItem(tc, 0, 0)
    verify(p.y >= bottom, "its lock lies wholly outside the list: " + p.y + " >= " + bottom)
    var shown = capture(tc)
    verify(shown.length > 1000, "a PNG capture")
    lock.opacity = 0
    var hidden = capture(tc)
    lock.opacity = 1
    return !sameBytes(shown, hidden)
  }
  function shapesOutside(root, view) {
    // Every Shape wholly outside `view`, culled or not.
    return all(root, function(i) {
      if (i.preferredRendererType === undefined || i.width <= 0) return false
      var a = i.mapToItem(view, 0, 0), b = i.mapToItem(view, i.width, i.height)
      return Math.max(a.y, b.y) <= 0 || Math.min(a.y, b.y) >= view.height
    })
  }
  function test_scrolled_history_paints_nothing_outside_the_page() {
    var s = setup({ animations: "off" })
    summary(s.rt, 50)
    s.panel.parent = tc
    s.rt.requestedPage = "history"
    s.w.open()
    tryVerify(function() { return s.panel.opened && !!s.panel.historyView })
    wait(50)
    var view = s.panel.historyView
    var xhr = new XMLHttpRequest()
    xhr.open("GET", Qt.resolvedUrl("../fixtures/demo-1h.json"), false)
    xhr.send()
    var message = JSON.parse(xhr.responseText)
    if (message.window && message.window !== view.window) view.setWindow(message.window)
    var asks = written(s.rt).filter(function(l) { return l.indexOf("history.get") >= 0 })
    message.requestId = JSON.parse(asks[asks.length - 1]).requestId
    probeOf(s.rt).feed(JSON.stringify(message))
    tryVerify(function() { return view.reply !== null })
    view.maxHeight = Style.space(160)
    var flick = all(view, function(i) { return i.contentY !== undefined && i.clip === true })[0]
    verify(flick)
    tryVerify(function() { return flick.height > 0 && flick.contentHeight > flick.height + Style.space(150) }, 1000, "the page scrolls")
    // Scroll the lowest chart plot to just above the viewport, under the page
    // buttons, where an unclipped Shape would land.
    var plots = all(view, function(i) { return i.preferredRendererType !== undefined && i.height > Style.space(20) })
    var lowest = null, lowestY = -1
    for (var plot of plots) {
      var y = plot.mapToItem(flick.contentItem, 0, 0).y
      if (y > lowestY) { lowest = plot; lowestY = y }
    }
    verify(lowest, "chart plots")
    flick.contentY = lowestY + lowest.height + Style.space(2)
    wait(50)
    var outside = shapesOutside(view, flick)
    verify(outside.indexOf(lowest) >= 0, "the plot lies wholly outside")
    verify(lowest.mapToItem(tc, 0, 0).y > 0, "just above the page, inside the card")
    var shown = capture(tc)
    for (var o of outside) o.opacity = 0
    var hidden = capture(tc)
    for (o of outside) o.opacity = 1
    verify(sameBytes(shown, hidden), "scrolled-out Shapes paint nothing over the page")
  }

  function test_scrolled_out_memory_rows_paint_nothing_outside_the_list() {
    var s = setup({ animations: "off" })
    summary(s.rt, 50)
    s.panel.parent = tc
    s.w.open()
    tryVerify(function() { return s.panel.opened })
    wait(50)
    var apps = []
    for (var i = 0; i < 16; i++)
      apps.push({ id: "a" + i, name: "App " + i, kind: "app", host: "", count: 1, pss: 900000 - i * 40000, swap: 0, protected: i >= 9 })
    probeOf(s.rt).feed(JSON.stringify({ type: "apps", apps: apps }))
    var list = named(s.panel.parent, "memoryList")[0]
    tryVerify(function() { return list.count === 16 })
    verify(list.contentHeight > list.height, "the list scrolls")
    verify(!lockPaintsOutside(list, "memoryProtected", 12), "a scrolled-out lock paints nothing")
  }
  function test_scrolled_out_apps_rows_paint_nothing_outside_the_list() {
    var s = setup({ animations: "off" })
    var rows = []
    for (var i = 0; i < 24; i++) rows.push(appsRow("app" + i, { pss: 900000 - i * 30000, protected: i >= 6 }))
    var view = openApps(s, rows)
    view.maxHeight = Style.space(420)
    var list = named(view, "appsList")[0]
    tryVerify(function() { return list.contentHeight > list.height }, 1000, "the list scrolls")
    verify(!lockPaintsOutside(list, "appsProtected", 12), "a scrolled-out lock paints nothing")
  }
}
