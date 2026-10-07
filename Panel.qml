import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "Level.js" as Level
import "PanelNav.js" as PanelNav
import "Design.js" as Design

// Pages behind one bar item. Memory: the memory overview plus the heaviest
// app groups, where killing is two-step: the first press arms a row ("Kill?"),
// the second sends the signal; arming expires after a few seconds. A group that
// ignores SIGTERM is offered a force kill (SIGKILL) on the next attempt.
// History: read-only charts and incident receipts (HistoryView.qml).
// Storage: read-only folder explorer (StorageView.qml). Apps: every app group
// with CPU/RAM rails, search and details (AppsView.qml), killing with the same
// two steps against the membership that was armed.
//
// One page is selected and one region has focus; PanelNav.js routes keys by
// both, and only the Memory and Apps app lists can reach the kill path.
Panel {
  id: root
  moduleName: "boundsj.raman"
  ipcTarget: "boundsj.raman"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property int animationDuration: hostWidget && hostWidget.animationDuration !== undefined
    ? hostWidget.animationDuration : Level.animationDuration(String(setting("animations", "on")),
      "reduceMotion" in Style && Style.reduceMotion)

  readonly property var summary: hostWidget ? hostWidget.summary : null
  readonly property var apps: hostWidget ? hostWidget.apps : []
  readonly property bool appsLoaded: hostWidget ? hostWidget.appsLoaded === true : false
  readonly property var kills: hostWidget ? hostWidget.kills : ({})
  readonly property string level: hostWidget ? hostWidget.level : "unknown"
  readonly property color levelColor: hostWidget ? hostWidget.levelColor : foreground
  readonly property var palette: hostWidget ? hostWidget.palette : Level.palette({}, {})
  readonly property real usedPercent: hostWidget ? hostWidget.usedPercent : 0
  readonly property var runtime: hostWidget && hostWidget.runtime ? hostWidget.runtime : null

  // Semantic colours as drawn on this card: the theme's own green/yellow/red
  // unless they need to be darker/lighter to read (light themes). `ink` is for
  // text (4.5:1), `tones` for fills and marks (3:1). Both hold on the card and
  // on the neutral tints the pages draw under them (Design.neutralSurfaces:
  // the host's row cursor, selected rows, meter tracks, History's range band).
  readonly property color surface: Color.popups.background
  readonly property var surfaces: Design.neutralSurfaces(surface, Style.hoverFillFor(foreground, Color.accent), foreground)
  readonly property color cursorSurface: surfaces[1]
  readonly property var ink: Design.tones(palette, surfaces, Design.TEXT_CONTRAST)
  readonly property var tones: Design.tones(palette, surfaces, Design.MARK_CONTRAST)
  readonly property color accentTone: Design.ensureContrast(Color.accent, surfaces, Design.MARK_CONTRAST)
  readonly property color levelInk: level === "critical" ? ink.critical : level === "warn" ? ink.warn
    : level === "ok" ? ink.ok : foreground
  readonly property color levelTone: level === "critical" ? tones.critical : level === "warn" ? tones.warn
    : level === "ok" ? tones.ok : foreground
  // Text on semantic tints, derived against the tint over its actual surface:
  // the percentage badge (on the card), and the kill confirmation at rest and
  // under the pointer, on a plain or cursor row.
  readonly property color badgeInk: Design.ensureContrast(levelInk,
    Design.tinted(levelTone, [Design.BADGE_TINT], [surface]), Design.TEXT_CONTRAST)
  readonly property color confirmInk: Design.ensureContrast(ink.critical,
    Design.tinted(tones.critical, [Design.CONFIRM_TINT, Design.CONFIRM_HOT_TINT], [surface, cursorSurface]),
    Design.TEXT_CONTRAST)
  // Fixed action slot on app rows, wide enough for the "Force?" confirmation,
  // so sizes stay aligned whichever control shows.
  readonly property real actionSlot: Math.max(Style.space(26), confirmMetrics.width + Style.space(14))
  TextMetrics {
    id: confirmMetrics
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    font.bold: true
    text: "Force?"
  }

  // --- pages, focus and data subscriptions ---------------------------------
  property var nav: PanelNav.initialState()
  readonly property string page: nav.page
  readonly property var historyView: historyLoader.item
  readonly property var storageView: storageLoader.item
  readonly property var appsView: appsLoader.item
  // An editor on the current page owns the keyboard (Storage path/filter, Apps search).
  readonly property bool inputOwned: (page === "storage" && !!storageView && storageView.inputOwned)
    || (page === "apps" && !!appsView && appsView.inputOwned)
  property var storageSession: null
  property var storageOwner: null
  property string pendingIncidentId: ""

  function deliverIncident() {
    if (!historyView || pendingIncidentId === "") return
    var id = pendingIncidentId
    pendingIncidentId = ""
    historyView.openIncident(id)
  }

  // This panel's own subscription key: another monitor's panel has its own,
  // so closing this one never stops the scan that panel is showing.
  property string clientKey: ""
  property var subscribedRuntime: null
  function syncSubscription() {
    var wants = PanelNav.subscriptionFor(root.opened, root.page, runtime ? runtime.gpuMetrics : "auto")
    if (subscribedRuntime && subscribedRuntime !== runtime && clientKey !== "") {
      subscribedRuntime.unsubscribe(clientKey)
      clientKey = ""
    }
    subscribedRuntime = runtime
    if (!runtime) {
      // An older host without a runtime: fall back to its plain detail switch.
      if (hostWidget && !("runtime" in hostWidget) && typeof hostWidget.setDetail === "function")
        hostWidget.setDetail(!!(wants && wants.detail))
      return
    }
    if (clientKey === "") clientKey = runtime.newClientId("panel")
    if (wants) runtime.subscribe(clientKey, wants)
    else runtime.unsubscribe(clientKey)
  }
  onRuntimeChanged: syncSubscription()
  Connections {
    target: root.runtime
    ignoreUnknownSignals: true
    function onGpuMetricsChanged() { root.syncSubscription() }
  }
  onPageChanged: {
    disarm()
    cursorActive = false
    syncSubscription()
  }
  Component.onDestruction: if (subscribedRuntime && clientKey !== "") subscribedRuntime.unsubscribe(clientKey)

  function selectPage(name) {
    root.nav = PanelNav.selectPage(root.nav, name)
  }

  function focusRegion(region) {
    // Pointer-selected controls must leave subsequent navigation with the
    // host dispatcher. Editors keep their own keys and are never refocused.
    if ((root.page === "storage" || root.page === "apps") && !root.inputOwned)
      keyCatcher.forceActiveFocus()
    if (root.nav.region === region && !root.nav.inspecting) return
    root.nav = { page: root.nav.page, region: region, inspecting: false }
  }

  function setInspecting(on) {
    root.nav = { page: root.nav.page, region: on ? "incidents" : root.nav.region, inspecting: !!on }
  }

  function handleKey(key) {
    var r = PanelNav.route(root.nav, key, {
      cursorActive: root.cursorActive,
      selectedIndex: root.selectedIndex,
      incidentIndex: historyView ? historyView.incidentIndex : 0,
      incidentCount: historyView ? historyView.incidentCount : 0,
      storageIndex: storageView ? storageView.rowIndex : 0,
      inputOwned: root.inputOwned,
      appsIndex: appsView ? appsView.rowIndex : -1,
      appsDetails: appsView ? appsView.detailsOpen : false,
      appsArmed: appsView ? appsView.armed : false
    })
    root.nav = r.state
    if (r.action && r.action.indexOf("storage") === 0) {
      if (storageView) storageView.handle(r.action, r.arg)
      return
    }
    if (r.action && r.action.indexOf("apps") === 0) {
      if (appsView) appsView.handle(r.action, r.arg)
      return
    }
    switch (r.action) {
    case "refresh":
      if (root.hostWidget) root.hostWidget.refresh()
      if (historyView) historyView.handle("refresh", null)
      break
    case "moveCursor": root.moveCursor(r.arg); break
    case "leaveList":
      root.cursorActive = false
      root.disarm()
      break
    case "enterList": root.cursorActive = true; break
    case "activate": if (root.cursorActive) root.armOrKill(root.selectedApp(), false); break
    case "arm":
      root.cursorActive = true
      root.armOrKill(root.selectedApp(), false)
      break
    case "armForce":
      root.cursorActive = true
      root.armOrKill(root.selectedApp(), true)
      break
    case "escape":
      if (root.armedId !== "") root.disarm()
      else root.close()
      break
    case "close": root.close(); break
    case "window": case "scrub": case "inspectAtCursor": case "moveIncident":
    case "inspect": case "back": case "focus":
      if (historyView) historyView.handle(r.action, r.arg)
      break
    }
  }

  readonly property real maxTotal: {
    var max = 0
    for (var i = 0; i < apps.length; i++) max = Math.max(max, Level.footprintKb(apps[i]) || 0)
    return max
  }

  property bool cursorActive: false
  property int selectedIndex: 0
  property string armedId: ""
  property bool armedForce: false
  // Re-evaluated on each apps snapshot so "Stopping…" can age into "force".
  property real now: Date.now()
  onAppsChanged: {
    now = Date.now()
    if (selectedIndex >= apps.length) selectedIndex = Math.max(0, apps.length - 1)
    if (armedId !== "" && indexOfApp(armedId) < 0) disarm()
  }

  onOpenedChanged: syncSubscription()

  function open() {
    cursorActive = false
    selectedIndex = 0
    disarm()
    var requested = runtime ? runtime.takeRequestedPage() : ""
    // Only the panel opened by the host can take this route. Other visible
    // panels' history replies never read the runtime's shared request.
    pendingIncidentId = runtime ? runtime.takeRequestedIncident() : ""
    root.nav = requested !== "" ? PanelNav.selectPage(PanelNav.initialState(), requested) : PanelNav.initialState()
    root.controller.show()
    deliverIncident()
  }

  function close() {
    root.controller.hide()
    disarm()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function indexOfApp(id) {
    for (var i = 0; i < apps.length; i++) if (apps[i].id === id) return i
    return -1
  }

  // "" | "stopping" | "stubborn" | "error"
  function killState(id) {
    var entry = kills ? kills[id] : null
    if (!entry) return ""
    if (entry.error) return "error"
    if (entry.signal === "TERM" && now - entry.at >= 4000) return "stubborn"
    return "stopping"
  }

  function wantsForce(app) {
    return killState(app.id) === "stubborn"
  }

  function disarm() {
    armedId = ""
    armedForce = false
    armTimer.stop()
  }

  // First call arms the row, a matching second call fires. Only the Memory
  // page can get here; History and future read-only pages never signal.
  function armOrKill(app, force) {
    if (!app || app.protected || !hostWidget || root.page !== "memory") return
    var useForce = force || wantsForce(app)
    if (armedId === app.id && (armedForce === useForce || useForce === false)) {
      hostWidget.killApp(app.id, armedForce)
      disarm()
      return
    }
    armedId = app.id
    armedForce = useForce
    armTimer.restart()
  }

  function selectedApp() {
    return selectedIndex >= 0 && selectedIndex < apps.length ? apps[selectedIndex] : null
  }

  function moveCursor(dy) {
    if (!cursorActive) {
      cursorActive = true
      return
    }
    if (apps.length === 0) return
    var next = Math.max(0, Math.min(apps.length - 1, selectedIndex + dy))
    if (next !== selectedIndex) disarm()
    selectedIndex = next
  }

  function selectRow(index) {
    if (index !== selectedIndex) disarm()
    cursorActive = true
    selectedIndex = index
    focusRegion("list")
  }

  Timer {
    id: armTimer
    interval: 4000
    onTriggered: root.disarm()
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      blocked: root.inputOwned
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        root.handleKey(dy < 0 ? "up" : dy > 0 ? "down" : dx < 0 ? "left" : "right")
      }
      onActivateRequested: root.handleKey("enter")
      onDeleteRequested: root.handleKey("delete")
      onCloseRequested: root.handleKey("escape")
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (root.page === "apps" && t === "/") root.handleKey("search")
        else if (root.page === "apps" && (t === "d" || t === "D")) root.handleKey("details")
        else if (root.page === "apps" && t === "]") root.handleKey("nextPage")
        else if (root.page === "apps" && t === "[") root.handleKey("prevPage")
        else if (root.page === "storage" && t === "\b") root.handleKey("backspace")
        else if (root.page === "storage" && t.toLowerCase() === "c") root.handleKey("cancel")
        else if (root.page === "storage" && t.toLowerCase() === "o") root.handleKey("open")
        else if (root.page === "storage" && t.toLowerCase() === "y") root.handleKey("copy")
        else if (t === "f" || t === "F") root.handleKey("force")
        else if (t === "r" || t === "R") root.handleKey("refresh")
        else if (t === "H") root.handleKey("jumpLeft")
        else if (t === "L") root.handleKey("jumpRight")
      }

      Column {
        id: column
        width: parent.width
        spacing: Style.space(12)

        // Page buttons. h/l switch pages only while this row has focus.
        Row {
          id: pageTabs
          width: parent.width
          spacing: Style.space(6)
          Accessible.role: Accessible.PageTabList
          Accessible.name: "RAMen pages"

          Repeater {
            model: PanelNav.PAGES
            PageButton {
              required property string modelData
              pageName: modelData
            }
          }
        }

        Loader {
          id: historyLoader
          width: parent.width
          active: root.opened && root.page === "history"
          visible: active
          source: Qt.resolvedUrl("HistoryView.qml")
          onLoaded: {
            item.panel = root
            root.deliverIncident()
            // Fit the page on screen; taller content scrolls inside it.
            item.maxHeight = Qt.binding(function() {
              return Math.max(Style.space(240), panel.availableCardHeight - panel.padding * 2
                - pageTabs.height - column.spacing - Style.space(8))
            })
          }
        }

        Loader {
          id: storageLoader
          width: parent.width
          active: root.opened && root.page === "storage"
          visible: active
          source: Qt.resolvedUrl("StorageView.qml")
          onLoaded: {
            item.panel = root
            item.maxHeight = Qt.binding(function() {
              return Math.max(240, panel.availableCardHeight - panel.padding * 2 - pageTabs.height - column.spacing - 8)
            })
          }
        }

        Loader {
          id: appsLoader
          width: parent.width
          active: root.opened && root.page === "apps"
          visible: active
          source: Qt.resolvedUrl("AppsView.qml")
          onLoaded: {
            item.panel = root
            item.maxHeight = Qt.binding(function() {
              return Math.max(Style.space(240), panel.availableCardHeight - panel.padding * 2
                - pageTabs.height - column.spacing - Style.space(8))
            })
          }
        }

        Column {
          id: memoryPage
          width: parent.width
          spacing: Style.space(12)
          visible: root.page === "memory"

          PanelHero {
            width: parent.width
            title: "Memory"
            meta: root.summary
              ? Level.formatKb(root.summary.used) + " of " + Level.formatKb(root.summary.total) + " used · " + Level.levelLabel(root.level)
              : "Starting probe"
            foreground: root.foreground
            fontFamily: root.fontFamily
            // The bar's bowl, large: the same level and colour as the bar item.
            iconComponent: Component {
              RamanBowl {
                objectName: "ramanHeroBowl"
                height: Math.round(Style.font.display * 1.1)
                width: implicitWidth
                fraction: root.summary ? root.usedPercent / 100 : null
                fill: root.levelTone
                line: Util.alpha(root.foreground, 0.72)
                steam: Design.steamCount(root.level)
                animationDuration: root.animationDuration
                Behavior on fill { ColorAnimation { duration: root.animationDuration } }
              }
            }
            // Same color as the bar label, so the two always agree.
            trailingControl: Component {
              BorderSurface {
                visible: !!root.summary
                implicitWidth: percentText.implicitWidth + Style.space(10)
                implicitHeight: percentText.implicitHeight + Style.space(4)
                radius: Style.cornerRadius
                color: Util.alpha(root.levelTone, Design.BADGE_TINT)
                borderSpec: Border.flat(root.levelTone, Style.normalBorderWidth)

                Text {
                  id: percentText
                  objectName: "memoryPercent"
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: Math.round(root.usedPercent) + "%"
                  color: root.badgeInk
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
              }
            }
          }

          // Used memory against total, with the current warn/critical
          // thresholds marked and labelled directly under their ticks.
          Item {
            id: usage
            objectName: "memoryMeter"
            width: parent.width
            height: meterTrack.height + Style.space(3) + tickLabelMetrics.height
            readonly property var thresholds: root.hostWidget
              ? [root.hostWidget.thresholds.warnPercent, root.hostWidget.thresholds.criticalPercent] : []
            // Label left edges: centred on each tick, packed together inside the
            // meter so close or end-of-scale thresholds (99% and 100%) never
            // overlap; widths are measured in the current font.
            readonly property var labelX: {
              var font = tickLabelMetrics.font // re-measure when the font changes
              var centers = [], widths = []
              for (var i = 0; i < thresholds.length; i++) {
                centers.push(meterTrack.width * thresholds[i] / 100)
                widths.push(Math.ceil(tickLabelMetrics.advanceWidth(thresholds[i] + "%")))
              }
              return Design.packLabels(centers, widths, Style.space(4), width)
            }
            Accessible.role: Accessible.ProgressBar
            Accessible.name: root.summary ? "Memory used " + Math.round(root.usedPercent) + " percent" : "Memory: waiting for the first reading"

            Rectangle {
              id: meterTrack
              width: parent.width
              height: Style.space(8)
              radius: height / 2
              color: Util.alpha(root.foreground, 0.1)
            }
            Rectangle {
              anchors.left: meterTrack.left
              anchors.top: meterTrack.top
              anchors.bottom: meterTrack.bottom
              width: meterTrack.width * root.usedPercent / 100
              radius: height / 2
              color: root.levelTone
              Behavior on width { NumberAnimation { duration: root.animationDuration; easing.type: Easing.OutCubic } }
              Behavior on color { ColorAnimation { duration: root.animationDuration } }
            }
            FontMetrics { id: tickLabelMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            Repeater {
              model: usage.thresholds
              Item {
                required property var modelData
                required property int index
                x: Math.round(meterTrack.width * modelData / 100)
                Rectangle {
                  x: -width / 2
                  y: -Style.space(2)
                  width: Math.max(1, Style.space(1))
                  height: meterTrack.height + Style.space(4)
                  color: Util.alpha(root.foreground, 0.45)
                }
                Text {
                  objectName: "thresholdLabel"
                  x: usage.labelX[index] - parent.x
                  y: meterTrack.height + Style.space(3)
                  textFormat: Text.PlainText
                  text: modelData + "%"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          Row {
            id: stats
            width: parent.width
            readonly property bool showZram: !!root.summary && root.summary.zramRam > 0
            readonly property int columns: showZram ? 4 : 3
            readonly property real cell: (width - spacing * (columns - 1)) / columns
            spacing: Style.space(8)

            Stat {
              width: stats.cell
              label: "AVAILABLE"
              value: root.summary ? Level.formatKb(root.summary.available) : "–"
            }
            Stat {
              width: stats.cell
              label: "SWAP"
              value: root.summary ? Level.formatKb(root.summary.swapUsed) : "–"
              hint: "Memory pushed out to swap (zram and/or swapfile)"
            }
            Stat {
              visible: stats.showZram
              width: stats.cell
              label: "ZRAM"
              value: root.summary ? Level.formatKb(root.summary.zramRam) : "–"
              hint: "Real RAM spent holding compressed swap pages"
            }
            Stat {
              width: stats.cell
              label: "PRESSURE"
              value: root.summary ? Level.pressureText(root.summary.psiSome10) : "–"
              valueColor: !root.summary ? root.foreground
                : root.summary.psiSome10 >= (root.hostWidget ? root.hostWidget.thresholds.criticalPressure : 20) ? root.ink.critical
                : root.summary.psiSome10 >= (root.hostWidget ? root.hostWidget.thresholds.warnPressure : 5) ? root.ink.warn
                : root.foreground
              hint: "Share of the last 10s that tasks stalled waiting for memory — this is the hitching"
            }
          }

          PanelSeparator { foreground: root.foreground }

          // The size column's unit, labelled directly above it.
          Item {
            width: parent.width
            height: topAppsHeader.implicitHeight
            PanelSectionHeader {
              id: topAppsHeader
              text: "TOP APPS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
            PanelSectionHeader {
              anchors.right: parent.right
              anchors.rightMargin: Style.space(8) + root.actionSlot + Style.space(8)
              text: "PSS + SWAP"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }
          }

          ListView {
            id: list
            objectName: "memoryList"
            width: parent.width
            height: Math.min(contentHeight, Style.space(440))
            spacing: Style.space(4)
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            visible: root.apps.length > 0
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            model: root.apps
            currentIndex: root.cursorActive ? root.selectedIndex : -1
            onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)

            delegate: AppRow {
              required property var modelData
              required property int index
              width: ListView.view.width
              app: modelData
              rowIndex: index
            }
          }

          Text {
            visible: root.apps.length === 0
            textFormat: Text.PlainText
            text: root.appsLoaded ? "Nothing using notable memory" : "Scanning processes…"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            text: "x kill · f force · r refresh · ↑ pages"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.Wrap
          }
        }
      }
    }
  }

  // One page button; the selected page is filled and lit, the focused row outlined.
  component PageButton: RamanChip {
    id: pageButton
    property string pageName: ""
    text: PanelNav.PAGE_LABELS[pageName]
    selected: root.page === pageName
    focused: root.nav.region === "nav" && selected
    foreground: root.foreground
    accent: root.accentTone
    fontFamily: root.fontFamily
    onClicked: root.selectPage(pageName)

    Accessible.role: Accessible.PageTab
    Accessible.name: PanelNav.PAGE_LABELS[pageName] + " page"
    Accessible.selected: selected
    Accessible.focusable: true
    Accessible.focused: focused
    Accessible.onPressAction: root.selectPage(pageName)
  }

  component Stat: Column {
    id: stat
    property string label: ""
    property string value: ""
    property string hint: ""
    property color valueColor: root.foreground
    spacing: Style.space(2)

    Text {
      textFormat: Text.PlainText
      text: stat.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 1.1
    }
    Text {
      width: stat.width
      textFormat: Text.PlainText
      text: stat.value
      color: stat.valueColor
      font.family: root.fontFamily
      font.pixelSize: Style.font.subtitle
      elide: Text.ElideRight

      MouseArea {
        id: statMouse
        anchors.fill: parent
        hoverEnabled: stat.hint !== ""
      }
      PanelToolTip {
        visible: stat.hint !== "" && statMouse.containsMouse
        text: stat.hint
        fontFamily: root.fontFamily
      }
    }
  }

  component AppRow: CursorSurface {
    id: row
    required property var app
    required property int rowIndex

    readonly property bool selected: root.cursorActive && root.selectedIndex === rowIndex
    readonly property bool armed: root.armedId === app.id
    readonly property string killStatus: root.killState(app.id)
    readonly property real total: Level.footprintKb(app) || 0
    // Fraction of physical RAM this group holds resident (drives the color).
    readonly property real share: root.summary && root.summary.total ? Math.min(1, app.pss / root.summary.total) : 0
    // Footprint relative to the heaviest app (drives the length).
    readonly property real relative: root.maxTotal > 0 ? Math.min(1, total / root.maxTotal) : 0

    readonly property string subtitle: {
      if (killStatus === "stopping") return "Stopping…"
      if (killStatus === "stubborn") return "Still running — kill again to force"
      if (killStatus === "error") return "Kill failed: " + root.kills[app.id].error
      var parts = []
      if (app.kind === "session" && app.host) parts.push("in " + app.host)
      parts.push(app.count === 1 ? "1 process" : app.count + " processes")
      if (app.swap >= 1024) parts.push(Level.formatKb(app.swap) + " swapped")
      if (Level.memoryNote(app) !== "") parts.push(Level.memoryNote(app))
      if (app.protected) parts.push("protected")
      return parts.join(" · ")
    }

    hasCursor: selected
    foreground: root.foreground
    implicitHeight: rowContent.implicitHeight + Style.spacing.rowPaddingX

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onContainsMouseChanged: if (containsMouse) root.selectRow(row.rowIndex)
      onClicked: function(mouse) {
        root.selectRow(row.rowIndex)
        if (mouse.button === Qt.RightButton) root.armOrKill(row.app, true)
      }
    }

    Item {
      id: rowContent
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(8)
      implicitHeight: Math.max(info.implicitHeight, Style.space(26))

      Column {
        id: info
        anchors.left: parent.left
        anchors.right: shareTrack.left
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(1)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: row.app.name
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: row.subtitle
          color: row.killStatus === "error" || row.killStatus === "stubborn" ? root.ink.critical : root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
      }

      // Footprint ranked against the heaviest app; tinted when the app holds
      // at least 10% / 25% of physical RAM.
      RamanMeter {
        id: shareTrack
        anchors.right: sizeText.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(40)
        fraction: row.relative
        foreground: root.foreground
        fill: row.share >= 0.25 ? root.tones.critical : row.share >= 0.1 ? root.tones.warn : Util.alpha(root.foreground, 0.55)
      }

      Text {
        id: sizeText
        width: Style.space(44)
        horizontalAlignment: Text.AlignRight
        anchors.right: action.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: Level.footprintText(row.app)
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.subtitle
        font.bold: true
      }

      // Fixed-width slot so sizes stay aligned whether or not the action shows.
      Item {
        id: action
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        width: root.actionSlot
        height: Math.max(killButton.implicitHeight, confirm.implicitHeight)

        RamanIcon {
          objectName: "memoryProtected"
          anchors.right: parent.right
          anchors.rightMargin: (killButton.implicitWidth - size) / 2
          anchors.verticalCenter: parent.verticalCenter
          visible: row.app.protected
          name: "lock"
          size: Style.font.icon
          color: root.dim
          Accessible.role: Accessible.StaticText
          Accessible.name: "Protected: cannot be killed from RAMen"
        }

        RamanIconButton {
          id: killButton
          objectName: "memoryKill"
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          visible: !row.app.protected && !row.armed && (row.hasCursor || row.killStatus !== "")
          iconName: root.wantsForce(row.app) ? "force" : "close"
          tooltipText: root.wantsForce(row.app) ? "Force kill (SIGKILL)" : "Kill (SIGTERM) · right-click to force"
          foreground: root.foreground
          hoverColor: root.ink.critical
          fontFamily: root.fontFamily
          onHovered: function(on) { if (on) root.selectRow(row.rowIndex) }
          onClicked: root.armOrKill(row.app, false)
        }

        BorderSurface {
          id: confirm
          objectName: "memoryConfirm"
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          visible: row.armed
          implicitWidth: Math.max(confirmText.implicitWidth + Style.space(14), killButton.implicitWidth)
          implicitHeight: Math.max(killButton.implicitHeight, confirmText.implicitHeight + Style.space(6))
          radius: Style.cornerRadius
          color: Util.alpha(root.tones.critical, confirmMouse.containsMouse ? Design.CONFIRM_HOT_TINT : Design.CONFIRM_TINT)
          borderSpec: Border.flat(root.tones.critical, Math.max(1, Style.normalBorderWidth))
          Accessible.role: Accessible.Button
          Accessible.name: (root.armedForce ? "Confirm force kill of " : "Confirm kill of ") + row.app.name

          Text {
            id: confirmText
            anchors.centerIn: parent
            textFormat: Text.PlainText
            text: root.armedForce ? "Force?" : "Kill?"
            color: root.confirmInk
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          MouseArea {
            id: confirmMouse
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.armOrKill(row.app, root.armedForce)
          }
        }
      }
    }
  }
}
