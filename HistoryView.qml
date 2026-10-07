import QtQuick
import QtQuick.Shapes
import qs.Commons
import "Level.js" as Level
import "Runtime.js" as Runtime
import "HistoryModel.js" as HM
import "Design.js" as Design

// The History page: pressure episodes first, then synchronized pressure, RAM
// and swap tracks with one shared cursor, then read-only incident receipts.
// It only reads history (history.get); nothing on this page can signal a
// process. The panel creates it while the page is visible and destroys it on
// close, so its refresh timer and pending request go with it.
//
// Every request carries this view's client id and a new generation; a reply
// for any older request (window switched, page left) is dropped.
Item {
  id: root

  property var panel: null
  readonly property var runtime: panel ? panel.runtime : null
  readonly property color foreground: panel ? panel.foreground : Color.foreground
  readonly property color dim: panel ? panel.dim : Qt.darker(Color.foreground, 1.45)
  readonly property string fontFamily: panel ? panel.fontFamily : Style.font.family
  readonly property var palette: panel ? panel.palette : Level.palette({}, {})
  // Contrast-safe semantic colours (see Panel.qml): text and marks.
  readonly property color surface: Color.popups.background
  readonly property var ink: panel && panel.ink ? panel.ink : Design.tones(palette, surface, Design.TEXT_CONTRAST)
  readonly property var tones: panel && panel.tones ? panel.tones : Design.tones(palette, surface, Design.MARK_CONTRAST)
  readonly property color accentTone: panel && panel.accentTone !== undefined ? panel.accentTone
    : Design.ensureContrast(Color.accent, surface, Design.MARK_CONTRAST)
  readonly property var thresholds: panel && panel.hostWidget && panel.hostWidget.thresholds
    ? panel.hostWidget.thresholds : ({ warnPressure: 5, criticalPressure: 20 })
  readonly property string region: panel ? panel.nav.region : "nav"
  readonly property bool inspecting: panel ? panel.nav.inspecting === true : false

  // Tallest the page may be inside the panel; taller content scrolls.
  property real maxHeight: 100000
  implicitHeight: Math.min(content.implicitHeight, maxHeight)

  // --- data -----------------------------------------------------------------
  // Snapshot the shared preference on creation. Another panel choosing a
  // window must not change this view without invalidating its pending reply.
  property string window: "1h"
  property string clientKey: ""
  property int generation: 0
  property string pendingId: ""
  property string wantedIncidentId: ""
  property string wantedRequestId: ""
  property var reply: null
  property string error: ""

  readonly property int points: HM.pointCount(reply)
  readonly property var scales: HM.scales(reply)
  readonly property var status: HM.pageState(reply, { error: error })
  readonly property var receipts: HM.receipts(reply)
  readonly property int incidentCount: receipts.length
  property string selectedId: ""
  readonly property int incidentIndex: {
    for (var i = 0; i < receipts.length; i++) if (receipts[i].id === selectedId) return i
    return 0
  }
  readonly property var selected: incidentCount > 0 ? receipts[incidentIndex] : null

  // Shared cursor: a point index. It follows the newest point until moved.
  property int cursor: -1
  property bool follow: true
  readonly property var info: HM.readout(reply, cursor)
  readonly property real cursorX: cursor >= 0 && reply ? HM.pointX(reply, cursor) : -1

  function request() {
    if (!runtime) return
    if (clientKey === "") clientKey = runtime.newClientId("history")
    generation += 1
    pendingId = runtime.requestHistory(clientKey, generation, window)
    wantedRequestId = wantedIncidentId !== "" ? pendingId : ""
    if (pendingId === "") {
      error = "The RAMen probe is not running, so there is no history to show."
      return
    }
    timeout.restart()
  }

  function accept(message) {
    if (message.type === "history-cleared") {
      // Any clear (IPC or another panel) invalidates what is on screen.
      reply = null
      error = ""
      cursor = -1
      follow = true
      request()
      return
    }
    if (!Runtime.acceptsReply(pendingId, message)) return // stale or someone else's
    if (message.type === "history" && message.window !== window) return
    var wanted = message.requestId === wantedRequestId ? wantedIncidentId : ""
    pendingId = ""
    timeout.stop()
    if (message.type === "error") {
      error = "History query failed: " + String(message.message || message.code)
      return
    }
    error = ""
    var previousT = cursor >= 0 && reply && reply.t ? reply.t[cursor] : null
    reply = message
    var n = HM.pointCount(message)
    if (!n) cursor = -1
    else if (follow || previousT === null) cursor = n - 1
    else cursor = HM.indexAtTime(message, previousT)
    if (selectedId === "" || !receipts.some(function(r) { return r.id === selectedId }))
      selectedId = receipts.length ? receipts[0].id : ""
    if (wanted !== "") {
      var found = receipts.some(function(r) { return r.id === wanted })
      wantedIncidentId = ""
      wantedRequestId = ""
      if (found) {
        selectedId = wanted
        if (panel) panel.setInspecting(true)
        showIncident()
      } else error = "That incident is no longer retained. Current history is shown."
    }
    if (inspecting && !selected && panel) panel.setInspecting(false)
  }

  function setWindow(id) {
    if (id === window) return
    wantedIncidentId = ""
    wantedRequestId = ""
    window = id
    if (runtime) runtime.historyWindow = id
    reply = null
    error = ""
    cursor = -1
    follow = true
    selectedId = ""
    if (inspecting && panel) panel.setInspecting(false)
    request()
  }

  function openIncident(id) {
    setWindow("24h")
    wantedIncidentId = String(id)
    // Always request a new generation, including an existing 24h view.
    request()
  }

  function moveCursor(step) {
    if (!points) return
    if (cursor < 0) cursor = points - 1
    cursor = Math.max(0, Math.min(points - 1, cursor + step))
    follow = cursor === points - 1
  }

  function selectIncident(index, inspect) {
    if (!incidentCount) return
    var i = Math.max(0, Math.min(incidentCount - 1, index))
    selectedId = receipts[i].id
    if (inspect) {
      if (panel) panel.setInspecting(true)
      showIncident()
    }
  }

  function showIncident() {
    if (!selected || !reply) return
    cursor = HM.indexAtTime(reply, selected.start)
    follow = false
    scrollToRegion()
  }

  // Actions routed by PanelNav.js for this page. None of them signal.
  function handle(action, arg) {
    if (action === "refresh") request()
    else if (action === "window") setWindow(HM.stepWindow(window, arg))
    else if (action === "scrub") moveCursor(arg)
    else if (action === "focus" && arg === "chart" && cursor < 0) moveCursor(0)
    else if (action === "moveIncident") {
      selectIncident(incidentIndex + arg, false)
      if (inspecting) showIncident()
    } else if (action === "inspect") showIncident()
    else if (action === "inspectAtCursor") {
      var id = HM.incidentAt(reply, cursor)
      if (id === null) return
      selectedId = id
      if (panel) panel.setInspecting(true)
      showIncident()
    }
    scrollToRegion()
  }

  function scrollToRegion() {
    // Visibility and Repeater changes settle after the action returns.
    Qt.callLater(ensureFocusedVisible)
  }

  function ensureFocusedVisible() {
    content.forceLayout()
    var target = region === "chart" ? chart
      : inspecting ? inspectedReceipt
      : region === "incidents" ? receiptRepeater.itemAt(incidentIndex) : null
    if (region === "nav" || region === "window") {
      flick.contentY = 0
      return
    }
    if (!target || (region === "chart" && !points)) return
    var top = target.mapToItem(flick.contentItem, 0, 0).y
    var bottom = top + target.height
    var next = flick.contentY
    if (target.height > flick.height || top < next) next = top
    else if (bottom > next + flick.height) next = bottom - flick.height
    flick.contentY = Math.max(0, Math.min(next, Math.max(0, flick.contentHeight - flick.height)))
  }

  onRegionChanged: scrollToRegion()
  onInspectingChanged: scrollToRegion()

  Connections {
    target: root.runtime
    function onHistoryMessage(message) { root.accept(message) }
  }

  Timer {
    id: refresher
    interval: Runtime.historyRefreshMs(root.window)
    running: true
    repeat: true
    onTriggered: if (root.pendingId === "") root.request()
  }

  // A request with no reply in 5 s is reported; its late reply is then dropped.
  Timer {
    id: timeout
    interval: 5000
    onTriggered: {
      root.pendingId = ""
      root.error = "No reply from the RAMen probe. It may be restarting; this view retries."
    }
  }

  // Client IDs belong to one runtime's namespace. A fallback-to-service
  // migration must drop the old request before listening to the new owner.
  onRuntimeChanged: {
    pendingId = ""
    timeout.stop()
    clientKey = ""
    reply = null
    error = ""
    cursor = -1
    selectedId = ""
    follow = true
    if (runtime) {
      if (generation === 0) window = runtime.historyWindow || "1h"
      request()
    }
  }
  Component.onCompleted: if (runtime && generation === 0) request()

  // --- layout ---------------------------------------------------------------
  readonly property real labelSize: Style.font.caption
  readonly property real gutter: Style.space(10)

  Flickable {
    id: flick
    anchors.fill: parent
    contentWidth: width
    contentHeight: content.implicitHeight
    clip: true
    interactive: contentHeight > height
    boundsBehavior: Flickable.StopAtBounds

    Column {
      id: content
      width: flick.width
      spacing: Style.space(10)

      // Title with a bowl-rim underline, and the window buttons.
      Item {
        width: parent.width
        height: Math.max(titleText.implicitHeight + rim.height + Style.space(4), windows.height)

        Text {
          id: titleText
          textFormat: Text.PlainText
          text: "History"
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
          Accessible.role: Accessible.Heading
          Accessible.name: "Memory history"
        }

        BowlRim {
          id: rim
          // Hidden while scrolled wholly out of sight (Design.inViewport).
          visible: Design.inViewport(rim, flick)
          anchors.top: titleText.bottom
          anchors.topMargin: Style.space(2)
          width: titleText.implicitWidth + Style.space(10)
          height: Style.space(5)
          color: root.accentTone
        }

        Row {
          id: windows
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(4)
          Accessible.role: Accessible.Grouping
          Accessible.name: "Time window"

          Repeater {
            model: HM.WINDOWS
            WindowButton {
              required property var modelData
              windowId: modelData.id
              label: modelData.label
            }
          }
        }
      }

      // State line: loading / error / empty / partial, plus persistence notes.
      Column {
        width: parent.width
        spacing: Style.space(2)
        visible: stateText.text !== "" || root.status.notes.length > 0

        Text {
          id: stateText
          width: parent.width
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          visible: text !== ""
          text: root.status.state === "loading" ? "Loading history…"
            : root.status.state === "empty" ? "No readings in this window yet. RAMen records while the shell is running."
            : ""
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Repeater {
          model: root.status.notes
          Text {
            required property string modelData
            width: parent.width
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            text: (root.status.state === "error" ? "⚠ " : "") + modelData
            color: root.status.state === "error" ? root.ink.critical : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }

      // The chart block: episodes, three tracks, time axis, one cursor.
      Item {
        id: chart
        width: parent.width
        height: chartColumn.implicitHeight
        visible: root.points > 0
        readonly property bool focused: root.region === "chart" && !root.inspecting
        Accessible.role: Accessible.Chart
        Accessible.name: "Memory history chart, " + HM.WINDOWS[HM.windowIndex(root.window)].label
        Accessible.description: HM.summaryText(root.reply, root.status.state)

        Rectangle {
          anchors.fill: parent
          anchors.margins: -Style.space(4)
          radius: Style.cornerRadius
          color: "transparent"
          border.width: chart.focused ? Math.max(2, Style.normalBorderWidth * 2) : 0
          border.color: root.foreground
        }

        Column {
          id: chartColumn
          width: parent.width
          spacing: Style.space(4)

          // Episodes lane: stored incidents only, never inferred ones.
          Item {
            id: lane
            width: parent.width
            height: Style.space(18)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              visible: root.incidentCount === 0
              textFormat: Text.PlainText
              text: "No incidents recorded in this window"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: root.labelSize
            }

            Repeater {
              model: HM.incidentSpans(root.reply)
              Item {
                required property var modelData
                readonly property bool isSelected: root.selected !== null && root.selected.id === modelData.id
                readonly property color tone: modelData.severity === "critical" ? root.tones.critical : root.tones.warn
                x: modelData.x0 * lane.width
                width: Math.max(Style.space(4), (modelData.x1 - modelData.x0) * lane.width)
                height: lane.height

                RamanIcon {
                  x: -width / 2
                  anchors.top: parent.top
                  name: "steam"
                  size: Style.space(10)
                  color: parent.tone
                }
                // Critical is solid, warning is outlined: shape, not only color.
                Rectangle {
                  anchors.bottom: parent.bottom
                  anchors.bottomMargin: Style.space(1)
                  width: parent.width
                  height: Style.space(6)
                  radius: height / 2
                  color: modelData.severity === "critical" ? parent.tone : Util.alpha(parent.tone, 0.18)
                  border.width: parent.isSelected ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
                  border.color: parent.isSelected ? root.foreground : parent.tone
                }
                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: {
                    root.selectedId = modelData.id
                    if (root.panel) root.panel.setInspecting(true)
                    root.showIncident()
                  }
                }
              }
            }
          }

          Track {
            trackKey: "pressure"
            metric: "psiSome10"
            title: "MEMORY PRESSURE"
            hint: "Share of the last 10 s that tasks stalled waiting for memory (PSI some)"
            plotHeight: Style.space(64)
            references: root.scales.pressure.status === "ok" ? [
              { value: root.thresholds.warnPressure, color: root.tones.warn, ink: root.ink.warn, label: "warn " + root.thresholds.warnPressure + "%" },
              { value: root.thresholds.criticalPressure, color: root.tones.critical, ink: root.ink.critical, label: "critical " + root.thresholds.criticalPressure + "%" }
            ] : []
          }
          Track {
            trackKey: "ram"
            metric: "used"
            title: "RAM USED"
            hint: "Used memory: total minus available"
            plotHeight: Style.space(46)
          }
          Track {
            trackKey: "swap"
            metric: "swapUsed"
            title: "SWAP USED"
            hint: "Memory pushed out to swap (zram and/or swapfile)"
            plotHeight: Style.space(34)
          }

          // Time axis.
          Item {
            id: axis
            width: parent.width
            height: axisLabel.implicitHeight
            Text { id: axisLabel; visible: false; text: "00:00"; font.pixelSize: root.labelSize; font.family: root.fontFamily }
            Repeater {
              model: root.reply ? HM.ticks(root.reply) : []
              Text {
                required property var modelData
                x: Math.max(0, Math.min(axis.width - implicitWidth, modelData.x * axis.width - implicitWidth / 2))
                textFormat: Text.PlainText
                text: modelData.label
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: root.labelSize
              }
            }
          }
        }

        // Shared cursor across the lane and every track.
        Rectangle {
          visible: root.cursorX >= 0
          x: Math.round(root.cursorX * chart.width)
          y: 0
          width: Math.max(1, Style.normalBorderWidth)
          height: axis.y
          color: Util.alpha(root.accentTone, 0.9)
        }

        MouseArea {
          anchors.fill: parent
          anchors.topMargin: lane.height
          hoverEnabled: true
          acceptedButtons: Qt.NoButton
          onPositionChanged: function(mouse) {
            if (!root.reply) return
            root.cursor = HM.nearestIndex(root.reply, mouse.x / chart.width)
            root.follow = root.cursor === root.points - 1
            if (root.panel && !root.inspecting) root.panel.focusRegion("chart")
          }
        }
      }

      // Cursor readout: what the line and band mean at this point.
      Column {
        width: parent.width
        spacing: Style.space(1)
        visible: root.info !== null
        Accessible.role: Accessible.StaticText
        Accessible.name: root.info ? root.info.time + ", " + root.info.lines.map(function(l) { return l.title + " " + l.text }).join(", ") : ""

        Text {
          width: parent.width
          textFormat: Text.PlainText
          wrapMode: Text.Wrap
          text: root.info ? root.info.time + " · " + root.info.basis + (root.info.after ? " · " + root.info.after : "") : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }
        Repeater {
          model: root.info ? root.info.lines : []
          Row {
            required property var modelData
            width: parent.width
            spacing: Style.space(6)
            Text {
              width: Style.space(64)
              textFormat: Text.PlainText
              text: modelData.key === "pressure" ? "Pressure" : modelData.key === "ram" ? "RAM" : "Swap"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            Text {
              width: parent.width - Style.space(70)
              textFormat: Text.PlainText
              elide: Text.ElideRight
              text: modelData.key === "swap" && root.scales.swap.status === "none" ? "no swap configured" : modelData.text
              color: modelData.missing ? root.dim : root.foreground
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.italic: modelData.missing
            }
          }
        }
      }

      Text {
        width: parent.width
        visible: root.points > 0
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: (root.reply && root.reply.resolution <= 2 ? "Line: readings" : "Line: time-weighted average · band: lowest to highest")
          + " · dashed: current thresholds · blank: no readings"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      // Incident receipts, or one receipt being inspected.
      Text {
        textFormat: Text.PlainText
        text: "INCIDENTS"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 1.1
        visible: root.status.state !== "loading"
      }

      Text {
        width: parent.width
        visible: root.status.state !== "loading" && root.incidentCount === 0
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: "None recorded in this window. Brief spikes stay on the chart. Sustained incidents are recorded while history collection is enabled."
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }

      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: !root.inspecting && root.incidentCount > 0
        Accessible.role: Accessible.List
        Accessible.name: "Incidents"

        Repeater {
          id: receiptRepeater
          model: root.inspecting ? [] : root.receipts
          ReceiptRow {
            required property var modelData
            required property int index
            width: parent.width
            receipt: modelData
            rowIndex: index
          }
        }
      }

      Inspect {
        id: inspectedReceipt
        width: parent.width
        visible: root.inspecting && root.selected !== null
        receipt: root.selected
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: root.inspecting ? "j/k other incidents · Esc back · read-only"
          : "k/j regions · h/l scrub · H/L faster · Enter inspect · Esc close · read-only"
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  // --- components -----------------------------------------------------------

  component WindowButton: RamanChip {
    id: wb
    property string windowId: ""
    property string label: ""
    text: label
    selected: root.window === windowId
    focused: root.region === "window" && selected
    foreground: root.foreground
    accent: root.accentTone
    fontFamily: root.fontFamily
    fontSize: Style.font.caption
    padX: Style.space(7)
    Accessible.role: Accessible.RadioButton
    Accessible.name: label + " window"
    Accessible.checkable: true
    Accessible.checked: selected
    Accessible.onPressAction: root.setWindow(windowId)
    onClicked: {
      root.setWindow(wb.windowId)
      if (root.panel) root.panel.focusRegion("window")
    }
  }

  // One metric on its own labelled scale. Line = mean, band = min..max; runs
  // break at nulls, continuity breaks and missing time.
  component Track: Column {
    id: track
    property string trackKey: ""
    property string metric: ""
    property string title: ""
    property string hint: ""
    property real plotHeight: 40
    property var references: []
    readonly property var scale: root.scales[trackKey]
    readonly property bool drawable: scale.status === "ok"
    readonly property real w: width
    readonly property real h: plotHeight
    readonly property var runs: drawable ? HM.lineRuns(root.reply, metric, scale.max) : []
    readonly property var bands: drawable ? HM.bandRuns(root.reply, metric, scale.max) : []
    width: parent.width
    spacing: Style.space(2)
    Accessible.role: Accessible.Graphic
    Accessible.name: title.toLowerCase() + (scale.label ? ", scale " + scale.label : "")
      + (scale.status === "unavailable" ? ", unavailable" : scale.status === "none" ? ", no swap configured" : "")

    function px(p) { return Qt.point(p.x * w, h - 1 - p.y * (h - 2)) }

    Item {
      width: parent.width
      height: trackTitle.implicitHeight
      Text {
        id: trackTitle
        textFormat: Text.PlainText
        text: track.title
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: root.labelSize
        font.bold: true
        font.letterSpacing: 1.0
        MouseArea { id: titleMouse; anchors.fill: parent; hoverEnabled: true }
        PanelToolTipHint { shown: titleMouse.containsMouse; text: track.hint }
      }
      Text {
        anchors.right: parent.right
        textFormat: Text.PlainText
        text: track.scale.status === "ok" ? "scale " + track.scale.label
          : track.scale.status === "none" ? "no swap configured"
          : track.scale.status === "unavailable" ? "unavailable on this system" : ""
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: root.labelSize
      }
    }

    Item {
      id: plot
      width: parent.width
      height: track.drawable ? track.plotHeight : Style.space(4)
      clip: true
      // Hidden (not collapsed) while scrolled wholly out: see Design.inViewport.
      opacity: Design.inViewport(plot, Design.scroller(plot)) ? 1 : 0

      // Baseline and top of scale.
      Rectangle { width: parent.width; height: 1; y: parent.height - 1; color: Util.alpha(root.foreground, 0.18) }
      Rectangle { visible: track.drawable; width: parent.width; height: 1; color: Util.alpha(root.foreground, 0.07) }

      // Time with no readings yet at the start of the window.
      Rectangle {
        visible: track.drawable && root.reply !== null
        width: root.reply ? HM.leadingBlank(root.reply) * parent.width : 0
        height: parent.height
        color: Util.alpha(root.foreground, 0.03)
      }

      // Recorded gaps: blank, with dashed edges.
      Repeater {
        model: track.drawable ? HM.gapBands(root.reply) : []
        GapBand {
          required property var modelData
          x: modelData.x0 * track.w
          width: Math.max(1, (modelData.x1 - modelData.x0) * track.w)
          height: track.h
          reason: track.trackKey === "pressure" ? modelData.label : ""
        }
      }

      Shape {
        anchors.fill: parent
        visible: track.drawable
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true

        // Range band.
        ShapePath {
          strokeColor: "transparent"
          strokeWidth: 0
          fillColor: Util.alpha(root.foreground, 0.16)
          PathMultiline {
            paths: track.bands.filter(function(r) { return r.length > 1 }).map(function(r) {
              return HM.bandPolygon(r).map(function(p) { return track.px(p) })
            })
          }
        }
        // Mean line.
        ShapePath {
          strokeColor: Util.alpha(root.foreground, 0.9)
          strokeWidth: Math.max(1.25, Style.space(1) * 1.25)
          fillColor: "transparent"
          joinStyle: ShapePath.RoundJoin
          capStyle: ShapePath.RoundCap
          PathMultiline {
            paths: track.runs.filter(function(r) { return r.length > 1 }).map(function(r) {
              return r.map(function(p) { return track.px(p) })
            })
          }
        }
      }

      // Single readings between gaps still show: a dot, and a bar for its range.
      Repeater {
        model: track.drawable ? track.runs.filter(function(r) { return r.length === 1 }) : []
        Rectangle {
          required property var modelData
          readonly property point at: track.px(modelData[0])
          width: Style.space(3); height: width; radius: width / 2
          x: at.x - width / 2; y: at.y - height / 2
          color: root.foreground
        }
      }
      Repeater {
        model: track.drawable ? track.bands.filter(function(r) { return r.length === 1 }) : []
        Rectangle {
          required property var modelData
          readonly property point high: track.px({ x: modelData[0].x, y: modelData[0].hi })
          readonly property point low: track.px({ x: modelData[0].x, y: modelData[0].lo })
          width: Style.space(2); x: high.x - width / 2; y: high.y
          height: Math.max(1, low.y - high.y)
          color: Util.alpha(root.foreground, 0.3)
        }
      }

      // Current threshold settings, dashed and labelled.
      Repeater {
        model: track.drawable ? track.references.filter(function(r) { return r.value < track.scale.max }) : []
        Item {
          required property var modelData
          width: track.w
          height: track.h
          readonly property real ly: track.px({ x: 0, y: modelData.value / track.scale.max }).y
          Shape {
            anchors.fill: parent
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
              strokeColor: modelData.color
              strokeWidth: 1
              strokeStyle: ShapePath.DashLine
              dashPattern: [3, 3]
              fillColor: "transparent"
              startX: 0; startY: ly
              PathLine { x: track.w; y: ly }
            }
          }
          Text {
            anchors.right: parent.right
            y: Math.max(0, ly - implicitHeight)
            textFormat: Text.PlainText
            text: modelData.label
            color: modelData.ink
            font.family: root.fontFamily
            font.pixelSize: Math.max(8, root.labelSize - 1)
          }
        }
      }

      // Cursor value marker.
      Rectangle {
        readonly property var v: root.cursor >= 0 && track.drawable && !HM.pointInGap(root.reply, root.cursor)
          ? HM.value(root.reply, track.metric, "mean", root.cursor) : null
        visible: v !== null
        readonly property point at: v !== null ? track.px({ x: root.cursorX, y: Math.min(1, v / track.scale.max) }) : Qt.point(0, 0)
        width: Style.space(5); height: width; radius: width / 2
        x: at.x - width / 2; y: at.y - height / 2
        color: Color.popups.background
        border.width: Math.max(1, Style.space(1) * 1.5)
        border.color: root.accentTone
      }
    }
  }

  component GapBand: Item {
    id: gap
    property string reason: ""
    Rectangle { anchors.fill: parent; color: Util.alpha(root.foreground, 0.05) }
    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      ShapePath {
        strokeColor: Util.alpha(root.foreground, 0.45)
        strokeWidth: 1
        strokeStyle: ShapePath.DashLine
        dashPattern: [2, 3]
        fillColor: "transparent"
        startX: 0.5; startY: 0
        PathLine { x: 0.5; y: gap.height }
        PathMove { x: gap.width - 0.5; y: 0 }
        PathLine { x: gap.width - 0.5; y: gap.height }
      }
    }
    Text {
      visible: gap.reason !== "" && implicitWidth + 4 < gap.width
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: gap.reason
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Math.max(8, root.labelSize - 1)
    }
  }

  component ReceiptRow: Rectangle {
    id: row
    property var receipt: null
    property int rowIndex: 0
    readonly property bool isSelected: root.selected !== null && root.selected.id === receipt.id
    readonly property bool focused: root.region === "incidents" && isSelected
    readonly property color tone: receipt.severity === "critical" ? root.tones.critical : root.tones.warn
    implicitHeight: rowText.implicitHeight + Style.space(10)
    radius: Style.cornerRadius
    color: isSelected ? Util.alpha(root.foreground, rowMouse.containsMouse ? 0.12 : 0.08)
      : Util.alpha(root.foreground, rowMouse.containsMouse ? 0.06 : 0)
    border.width: focused ? Math.max(2, Style.normalBorderWidth * 2) : 0
    border.color: root.foreground
    Accessible.role: Accessible.ListItem
    Accessible.name: receipt.title + ", " + receipt.when + ", " + receipt.duration
    Accessible.selected: isSelected
    Accessible.onPressAction: root.selectIncident(rowIndex, true)

    RamanIcon {
      id: rowMarker
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      name: "steam"
      size: Style.space(14)
      color: row.tone
    }

    Column {
      id: rowText
      anchors.left: rowMarker.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(1)
      Text {
        width: parent.width
        textFormat: Text.PlainText
        elide: Text.ElideRight
        text: row.receipt.when + " · " + row.receipt.title
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }
      Text {
        width: parent.width
        textFormat: Text.PlainText
        elide: Text.ElideRight
        text: row.receipt.duration + " · pressure " + row.receipt.atStart.pressure + " at start · "
          + (row.receipt.apps.length ? "apps observed" : "no app observation")
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    MouseArea {
      id: rowMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: {
        root.selectIncident(row.rowIndex, false)
        if (root.panel) root.panel.setInspecting(true)
        root.showIncident()
      }
    }
  }

  // A stored incident in detail. Read-only: no action here touches a process.
  component Inspect: Rectangle {
    id: inspect
    property var receipt: null
    readonly property color tone: receipt && receipt.severity === "critical" ? root.tones.critical : root.tones.warn
    implicitHeight: inspectColumn.implicitHeight + Style.space(16)
    radius: Style.cornerRadius
    color: Util.alpha(root.foreground, 0.05)
    border.width: Math.max(2, Style.normalBorderWidth * 2)
    border.color: Util.alpha(tone, 0.8)
    Accessible.role: Accessible.Pane
    Accessible.name: receipt ? "Incident: " + receipt.title + ", started " + receipt.when + ", " + receipt.duration : ""

    Column {
      id: inspectColumn
      x: Style.space(8)
      y: Style.space(8)
      width: parent.width - Style.space(16)
      spacing: Style.space(4)

      Item {
        width: parent.width
        height: Math.max(inspectTitle.implicitHeight, backButton.height)
        RamanIcon {
          id: inspectMarker
          anchors.verticalCenter: parent.verticalCenter
          name: "steam"
          size: Style.space(13)
          color: inspect.tone
        }
        Text {
          id: inspectTitle
          anchors.left: inspectMarker.right
          anchors.leftMargin: Style.space(6)
          anchors.right: backButton.left
          anchors.rightMargin: Style.space(6)
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          elide: Text.ElideRight
          text: inspect.receipt ? inspect.receipt.title : ""
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }
        RamanButton {
          id: backButton
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: "Back · Esc"
          iconName: "back"
          focusPolicy: Qt.NoFocus
          foreground: root.foreground
          fontFamily: root.fontFamily
          fontSize: Style.font.caption
          Accessible.name: "Back to incidents"
          onClicked: if (root.panel) root.panel.setInspecting(false)
        }
      }

      Fact { label: "Started"; value: inspect.receipt ? inspect.receipt.when + " · " + (inspect.receipt.ongoing ? "still ongoing" : "lasted " + inspect.receipt.duration) : "" }
      Fact {
        visible: !!inspect.receipt && !!inspect.receipt.upgrade
        label: "Upgrade"
        value: inspect.receipt && inspect.receipt.upgrade ? "Critical at " + HM.clock(inspect.receipt.upgrade.at, true)
          + (inspect.receipt.upgrade.measurementStatus === "expired" ? " · measurements expired (24-hour retention)"
            : " · pressure " + HM.percent(inspect.receipt.upgrade.measurement.psiSome10)
              + " · available " + HM.kb(inspect.receipt.upgrade.measurement.available)) : ""
      }
      Fact { label: "Threshold"; value: inspect.receipt ? inspect.receipt.threshold : "" }
      Fact {
        label: inspect.receipt && inspect.receipt.measurementStatus === "expired" ? "At start (expired)" : "At start"
        value: inspect.receipt ? "pressure " + inspect.receipt.atStart.pressure + " · available " + inspect.receipt.atStart.available
          + " · used " + inspect.receipt.atStart.used : ""
      }
      Fact {
        label: "Peak"
        value: !inspect.receipt ? "" : inspect.receipt.peak.available
          ? "pressure " + inspect.receipt.peak.pressure + " · used " + inspect.receipt.peak.used + " · lowest available " + inspect.receipt.peak.lowestAvailable
          : inspect.receipt.peak.note
        note: inspect.receipt && inspect.receipt.peak.available ? inspect.receipt.peak.note : ""
      }
      Fact { label: "Apps"; value: inspect.receipt ? inspect.receipt.observation : "" }

      Repeater {
        model: inspect.receipt ? inspect.receipt.apps : []
        Item {
          required property var modelData
          width: inspectColumn.width
          height: appName.implicitHeight
          Text {
            id: appName
            x: Style.space(72)
            width: parent.width - x - appSize.implicitWidth - Style.space(8)
            textFormat: Text.PlainText
            elide: Text.ElideRight
            text: modelData.name
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            Accessible.role: Accessible.StaticText
            Accessible.name: modelData.name + ", " + modelData.size
            MouseArea { id: appHover; anchors.fill: parent; hoverEnabled: true }
            PanelToolTipHint { shown: appHover.containsMouse && appName.truncated; text: modelData.name }
          }
          Text {
            id: appSize
            anchors.right: parent.right
            textFormat: Text.PlainText
            text: modelData.size
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
        }
      }

      Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: (inspect.receipt && inspect.receipt.apps.length ? "Observed apps are context recorded at the time, not a cause. " : "")
          + "This is a historical observation: nothing here can stop a process. Current apps are on the Memory page."
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }

  component Fact: Row {
    id: fact
    property string label: ""
    property string value: ""
    property string note: ""
    width: parent ? parent.width : 0
    spacing: Style.space(8)
    Text {
      width: Style.space(64)
      textFormat: Text.PlainText
      text: fact.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
    Column {
      width: parent.width - Style.space(72)
      Text {
        width: parent.width
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: fact.value
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
      Text {
        width: parent.width
        visible: text !== ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        text: fact.note
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Math.max(8, Style.font.caption - 1)
      }
    }
  }

  // Bowl-rim underline for the page title: a shallow curve, decoration only.
  component BowlRim: Shape {
    id: bowl
    property color color: root.foreground
    preferredRendererType: Shape.CurveRenderer
    antialiasing: true
    ShapePath {
      strokeColor: bowl.color
      strokeWidth: Math.max(1, Style.space(1) * 1.5)
      fillColor: "transparent"
      capStyle: ShapePath.RoundCap
      startX: 1; startY: 1
      PathQuad { x: bowl.width - 1; y: 1; controlX: bowl.width / 2; controlY: bowl.height * 1.6 }
    }
  }

  component PanelToolTipHint: Rectangle {
    id: hint
    property bool shown: false
    property string text: ""
    visible: shown && text !== ""
    z: 10
    y: -height - Style.space(4)
    width: Math.min(root.width, hintText.implicitWidth + Style.space(12))
    height: hintText.height + Style.space(6)
    radius: Style.cornerRadius
    color: Color.popups.background
    border.width: 1
    border.color: Util.alpha(root.foreground, 0.3)
    Text {
      id: hintText
      anchors.centerIn: parent
      width: parent.width - Style.space(12)
      textFormat: Text.PlainText
      wrapMode: Text.Wrap
      text: hint.text
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
  }
}
