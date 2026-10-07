import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "Level.js" as Level
import "Design.js" as Design

// RAM at a glance: the bowl mark as a gauge (broth level = memory used) plus a
// label, green/yellow/red from used memory and kernel memory pressure, with
// steam over the bowl when tight or critical. Clicking opens the panel (Memory,
// History, Storage and Apps). The probe and all live data belong to one RamanRuntime: the shared
// one from Service.qml when the bar offers it, otherwise a private one. This
// widget keeps the hostWidget facade that Panel.qml reads.
BarWidget {
  id: root
  moduleName: "boundsj.raman"

  // --- runtime ------------------------------------------------------------
  // Omarchy's built-in bar hands widgets a facade with serviceFor(ownId). A
  // replacement bar may not; after a short grace period this widget then runs
  // a private runtime (the probe's history lock still allows one writer).
  property var sharedRuntime: null
  property int serviceAttempts: 0
  readonly property bool useLocalRuntime: !sharedRuntime && serviceAttempts >= 5
  readonly property var runtime: sharedRuntime || (localRuntime.item ? localRuntime.item : null)

  function findSharedRuntime() {
    if (root.sharedRuntime) return
    var shell = root.bar ? root.bar.shell : null
    var service = shell && typeof shell.serviceFor === "function" ? shell.serviceFor(root.moduleName) : null
    if (service && service.runtime) root.sharedRuntime = service.runtime
  }

  Timer {
    id: serviceTimer
    interval: 300
    repeat: true
    running: !root.sharedRuntime && root.serviceAttempts < 5
    onTriggered: {
      root.findSharedRuntime()
      if (!root.sharedRuntime) root.serviceAttempts += 1
    }
  }

  Loader {
    id: localRuntime
    active: root.useLocalRuntime
    sourceComponent: Component { RamanRuntime { mode: "widget" } }
    onLoaded: item.panelRouter = { open: root.open, close: root.close, toggle: root.togglePanel }
  }

  property var attachedRuntime: null
  onRuntimeChanged: {
    if (attachedRuntime && attachedRuntime !== runtime) {
      if (detailKey !== "") attachedRuntime.unsubscribe(detailKey)
      attachedRuntime.detach(root)
    }
    attachedRuntime = runtime
    if (!runtime) return
    runtime.settings = root.settings
    runtime.attach(root)
    if (root.detailKey !== "") {
      // Keys belong to a runtime's namespace, even after a null-runtime gap.
      root.detailKey = runtime.newClientId("widget")
      runtime.subscribe(root.detailKey, { detail: true })
    }
  }
  Component.onDestruction: if (attachedRuntime) {
    if (detailKey !== "") attachedRuntime.unsubscribe(detailKey)
    attachedRuntime.detach(root)
  }

  // --- hostWidget facade (Panel.qml and older callers) --------------------
  readonly property string labelMode: String(setting("label", "percent"))
  readonly property int animationDuration: Level.animationDuration(String(setting("animations", "on")),
    "reduceMotion" in Style && Style.reduceMotion)
  readonly property var thresholds: runtime ? runtime.thresholds : ({
    warnPercent: Number(setting("warnPercent", 75)),
    criticalPercent: Number(setting("criticalPercent", 90)),
    warnPressure: Number(setting("warnPressure", 5)),
    criticalPressure: Number(setting("criticalPressure", 20))
  })
  readonly property var summary: runtime ? runtime.summary : null
  readonly property var apps: runtime ? runtime.apps : []
  readonly property bool appsLoaded: runtime ? runtime.appsLoaded : false
  readonly property var kills: runtime ? runtime.kills : ({})
  readonly property string appsDefaultSort: String(setting("appsDefaultSort", "memory"))
  readonly property string gpuMetrics: String(setting("gpuMetrics", "auto")) === "off" ? "off" : "auto"
  readonly property bool storageShowMap: setting("storageShowMap", true) !== false
  readonly property bool storageRememberScope: setting("storageRememberScope", true) !== false
  readonly property bool detail: runtime ? runtime.detail : false
  readonly property string level: runtime ? runtime.level : "unknown"
  readonly property real usedPercent: Level.usedPercent(summary)
  readonly property var palette: runtime ? runtime.palette : Level.palette({}, {})
  readonly property color levelColor: level === "critical" ? palette.critical
    : level === "warn" ? palette.warn
    : level === "ok" ? palette.ok
    : (bar ? bar.barForeground : Color.foreground)
  // The level colour, only as much darker/lighter as the bar's own background
  // needs for the label (text) and the broth (a mark). A transparent bar has
  // no known background, so it keeps the theme colour.
  readonly property var barBackground: bar && bar.background !== undefined && !bar.transparent ? bar.background : null
  readonly property color levelInk: barBackground && level !== "unknown"
    ? Design.ensureContrast(levelColor, barBackground, Design.TEXT_CONTRAST) : levelColor
  readonly property color levelTone: barBackground && level !== "unknown"
    ? Design.ensureContrast(levelColor, barBackground, Design.MARK_CONTRAST) : levelColor

  readonly property string labelText: {
    if (!summary) return ""
    if (labelMode === "none") return ""
    if (labelMode === "used") return Level.formatKb(summary.used)
    if (labelMode === "available") return Level.formatKb(summary.available)
    return Math.round(usedPercent) + "%"
  }

  readonly property string tooltip: {
    if (!summary) return "Memory: starting probe…"
    return "Memory " + Level.levelLabel(level).toLowerCase() + " · "
      + Level.formatKb(summary.used) + " / " + Level.formatKb(summary.total)
      + " (" + Math.round(usedPercent) + "%)"
      + " · swap " + Level.formatKb(summary.swapUsed)
      + " · pressure " + Level.pressureText(summary.psiSome10)
  }

  function send(line) { if (runtime) runtime.send(line) }
  function sendCommand(message) { if (runtime) runtime.sendCommand(message) }
  function refresh() { if (runtime) runtime.refresh() }
  function clearHistory() { if (runtime) runtime.clearHistory() }
  function killApp(id, force) { if (runtime) runtime.killApp(id, force) }

  // Legacy: a caller that toggles app detail directly. Panels subscribe
  // through the runtime with their own keys instead.
  property string detailKey: ""
  function setDetail(on) {
    if (!runtime || typeof runtime.subscribe !== "function") return
    if (on) {
      if (detailKey === "") detailKey = runtime.newClientId("widget")
      runtime.subscribe(detailKey, { detail: true })
    } else if (detailKey !== "") {
      runtime.unsubscribe(detailKey)
      detailKey = ""
    }
  }

  // Screen-space bounds of the bar item (logical px), for scripts/screenshots.sh.
  function geometry() {
    var p = button.mapToGlobal(0, 0)
    return JSON.stringify({ x: Math.round(p.x), y: Math.round(p.y), w: Math.round(button.width), h: Math.round(button.height) })
  }

  // --- panel wiring (same shape as boundsj.coffee) ------------------------
  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: {
    injectPanel()
    findSharedRuntime()
  }
  onSettingsChanged: {
    injectPanel()
    if (runtime) runtime.settings = root.settings
  }
  Component.onCompleted: findSharedRuntime()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    tooltipText: root.tooltip
    fixedWidth: root.vertical ? -1 : content.implicitWidth + scaledHorizontalMargin * 2
    fixedHeight: root.vertical ? content.implicitHeight + scaledVerticalPadding * 2 : -1
    horizontalMargin: 6

    onPressed: function(b) {
      if (b === Qt.LeftButton) root.togglePanel()
      else if (b === Qt.MiddleButton) root.refresh()
    }

    Grid {
      id: content
      anchors.centerIn: parent
      columns: root.vertical ? 1 : 2
      spacing: Style.space(4)
      horizontalItemAlignment: Grid.AlignHCenter
      verticalItemAlignment: Grid.AlignVCenter

      // The bowl gauge: broth rises with memory used; steam when tight/critical.
      RamanBowl {
        id: gauge
        objectName: "ramanGauge"
        height: Style.bar.iconCanvas
        width: implicitWidth
        fraction: root.summary ? root.usedPercent / 100 : null
        fill: root.levelTone
        line: Util.alpha(button.foreground, 0.72)
        steam: Design.steamCount(root.level)
        animationDuration: root.animationDuration
        Behavior on fill { ColorAnimation { duration: root.animationDuration } }
      }

      Text {
        visible: root.labelText !== ""
        textFormat: Text.PlainText
        text: root.labelText
        color: root.levelInk
        font.family: button.fontFamily
        font.pixelSize: root.vertical ? Style.font.caption : Style.font.body
        renderType: Text.NativeRendering

        Behavior on color { ColorAnimation { duration: root.animationDuration } }
      }
    }
  }
}
