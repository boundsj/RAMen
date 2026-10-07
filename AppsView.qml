import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "AppsModel.js" as AM
import "Level.js" as Level
import "Design.js" as Design

// Apps: the grouped app ledger with a resource lens. One row per app scope or
// terminal session, two lines each: name, footprint (PSS + swap) and machine
// CPU share (VRAM in the GPU memory lens); then what the group is, a
// resident-RAM rail (PSS / physical RAM) and a CPU rail (share of all logical
// CPUs). The rails have separate labels and denominators and are never
// combined into one score; GPU memory is a separate per-device reading.
//
// Rows come from apps.query pages of the probe's cached inventory, so search,
// sort and paging never rescan. Selection and the armed kill follow a group
// ID; see AppsModel.js for the freeze/queue rules and membership-checked kill.
Item {
  id: root
  property var panel: null
  readonly property var runtime: panel ? panel.runtime : null
  property var owner: null
  property string client: ""
  property var model: AM.initialState("memory")
  property var details: null
  property int detailsGeneration: 0 // never reset by closing details: see AM.detailsRequest
  property real now: Date.now()
  property real maxHeight: 600

  readonly property bool inputOwned: searchInput.activeFocus
  readonly property bool detailsOpen: details !== null
  readonly property int rowIndex: AM.selectedIndex(model)
  readonly property bool armed: !!model.armed
  readonly property var kills: owner ? owner.kills : ({})
  readonly property real totalKb: panel && panel.summary && panel.summary.total ? panel.summary.total : 0
  readonly property color foreground: panel ? panel.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: panel && panel.fontFamily ? panel.fontFamily : Style.font.family
  readonly property var palette: panel ? panel.palette : Level.palette({}, {})
  // Contrast-safe semantic colours (see Panel.qml): text and marks.
  readonly property color surface: Color.popups.background
  readonly property var ink: panel && panel.ink ? panel.ink : Design.tones(palette, surface, Design.TEXT_CONTRAST)
  readonly property var tones: panel && panel.tones ? panel.tones : Design.tones(palette, surface, Design.MARK_CONTRAST)
  readonly property color accentTone: panel && panel.accentTone !== undefined ? panel.accentTone
    : Design.ensureContrast(Color.accent, surface, Design.MARK_CONTRAST)
  // "Kill?" on its tint, at rest and under the pointer (see Panel.qml).
  readonly property color confirmInk: panel && panel.confirmInk !== undefined ? panel.confirmInk
    : Design.ensureContrast(ink.critical, Design.tinted(tones.critical,
      [Design.CONFIRM_TINT, Design.CONFIRM_HOT_TINT], [surface]), Design.TEXT_CONTRAST)
  readonly property bool regionFocused: !!panel && panel.page === "apps"
  readonly property var notes: AM.statusNotes(model, now)
  readonly property var detailRow: {
    if (!details) return null
    if (model.focus && model.focus.id === details.id) return model.focus.row
    return details.data ? details.data.row : null
  }
  // Aligned columns: footprint above the RAM rail, the lens value above the
  // CPU rail. Each is as wide as its longest value needs (measured in the
  // current font), growing on wide panels, so the app name keeps the rest.
  readonly property bool emphasizedLens: model.sort !== "memory"
  readonly property real valueWidth: Math.max(sizeMetrics.width + Style.space(6), railMinimum, Math.min(Style.space(128), width * 0.17))
  readonly property real cpuWidth: Math.max(lensMetrics.width + Style.space(2), railMinimum, Math.min(Style.space(128), width * 0.17))
  readonly property real railMinimum: railLabelMetrics.width + Style.space(36)
  TextMetrics { id: sizeMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.subtitle; font.bold: true; text: "≥999M" }
  TextMetrics {
    id: lensMetrics
    font.family: root.fontFamily
    font.pixelSize: root.emphasizedLens ? Style.font.subtitle : Style.font.bodySmall
    font.bold: root.emphasizedLens
    text: root.model.sort === "gpu-memory" ? "VRAM ≤9.9G" : "CPU ≥9.9%"
  }
  TextMetrics { id: railLabelMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.caption; text: "RAM" }
  // Details + kill buttons, or the "Force?" confirmation, in one fixed slot.
  readonly property real actionWidth: Math.max(Style.space(50), confirmMetrics.width + Style.space(14))
  TextMetrics { id: confirmMetrics; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true; text: "Force?" }

  implicitHeight: Math.min(maxHeight, content.implicitHeight)

  onRuntimeChanged: attach()
  Component.onCompleted: attach()

  // A runtime migration gets a fresh client: old request IDs can never match.
  function attach() {
    if (owner === runtime) return
    armTimer.stop()
    details = null
    owner = runtime
    model = AM.initialState(owner ? owner.appsDefaultSort : "memory", owner ? owner.gpuMetrics : "auto")
    if (!owner) return
    client = owner.newClientId("apps")
    send({}, false)
  }

  function send(changes, user) {
    if (!owner) return
    var r = AM.request(model, changes, user)
    var id = owner.requestApps(client, r.state.generation, r.params)
    model = AM.pending(r.state, id, Date.now())
  }

  function liveRefresh() {
    if (owner && AM.canRefresh(model, Date.now())) send({}, false)
  }

  function accept(message) {
    if (message.type === "apps-details" || (message.type === "error" && /^d:/.test(String(message.requestId)))) {
      var d = AM.acceptDetails(details, message)
      if (d) details = d
      return
    }
    if (message.type === "error" && /^k:/.test(String(message.requestId))) {
      if (String(message.requestId).indexOf("k:" + client + ":") === 0)
        model = AM.copy(model, { notice: "Kill request rejected: " + String(message.message || message.code).slice(0, 160) })
      return
    }
    var r = AM.accept(model, message)
    if (!r.accepted) return
    model = r.state
    now = Date.now()
    if (!model.armed) armTimer.stop()
    if (r.restart) {
      var notice = model.notice
      send({ offset: 0 }, true)
      model = AM.copy(model, { notice: notice })
    } else if (r.sort) {
      send({ sort: r.sort, offset: 0 }, false) // the default GPU lens, now that it has readings
    }
    Qt.callLater(ensureSelectedVisible)
  }

  // gpuMetrics changed while open: a GPU lens that is now off returns to Memory.
  function gpuSettingChanged() {
    if (!owner) return
    var r = AM.setGpuSetting(model, owner.gpuMetrics)
    model = r.state
    if (r.sort) {
      var note = model.sortNote
      send({ sort: r.sort, offset: 0 }, true)
      armTimer.stop()
      model = AM.copy(model, { sortNote: note })
    }
  }

  function ensureSelectedVisible() {
    if (rowIndex >= 0 && list.visible) list.positionViewAtIndex(rowIndex, ListView.Contain)
  }

  Connections {
    target: root.owner
    function onAppsMessage(message) { root.accept(message) }
    function onAppsInventoryChanged() { if (root.owner && root.owner.appsInventory) root.liveRefresh() }
    function onGpuMetricsChanged() { root.gpuSettingChanged() }
  }

  // --- user actions ---------------------------------------------------------
  function changeSort(key) {
    if (!AM.sortAvailable(key, model)) {
      model = AM.copy(model, { notice: "GPU memory is not available: " + AM.gpuLens(model).reason })
      return
    }
    // Choosing the current sort changes nothing: an armed kill keeps its expiry.
    if (key === model.sort) return
    send({ sort: key, offset: 0 }, true)
    armTimer.stop()
  }

  function changeQuery(text) {
    if (text === model.query) return
    send({ query: text, offset: 0 }, true)
    armTimer.stop()
  }

  function page(step) {
    var change = AM.pageChange(model, step)
    if (!change) return
    send(change, true)
    armTimer.stop()
  }

  function select(id) {
    model = AM.select(model, id)
    if (!model.armed) armTimer.stop()
  }

  function disarm(notice) {
    model = AM.disarm(model, notice).state
    armTimer.stop()
    Qt.callLater(ensureSelectedVisible)
  }

  function killStatus(id) { return AM.killState(kills, id, now) }

  // The same two-step TERM/KILL as the Memory page, against the armed membership.
  function armOrKill(row, force) {
    if (!row || !owner || !panel || panel.page !== "apps" || detailsOpen) return
    var r = AM.armOrConfirm(model, row, force, killStatus(row.id) === "stubborn")
    model = r.state
    if (r.kill) {
      armTimer.stop()
      owner.killAppMember(client, r.kill.id, r.kill.membership, r.kill.force)
      now = Date.now()
      Qt.callLater(ensureSelectedVisible)
    } else if (model.armed) {
      armTimer.restart()
    }
  }

  function setHovering(on) {
    model = AM.setHovering(model, on)
    if (!on) Qt.callLater(ensureSelectedVisible)
  }

  function openDetails(row) {
    if (!row || !owner) return
    disarm("")
    select(row.id)
    setHovering(false)
    var next = AM.detailsRequest(detailsGeneration, row.id)
    detailsGeneration = next.generation
    next.pendingId = owner.requestAppDetails(client, next.generation, row.id)
    details = next
  }

  function closeDetails() {
    details = null
    Qt.callLater(ensureSelectedVisible)
  }

  function handle(action, arg) {
    switch (action) {
    case "appsRefresh":
      if (owner) owner.refresh()
      if (details) openDetails({ id: details.id })
      break
    case "appsSearch":
      disarm("")
      searchInput.forceActiveFocus()
      break
    case "appsSort": changeSort(AM.stepSort(model.sort, arg, model)); break
    case "appsEnterList": if (rowIndex < 0) model = AM.move(model, 1); break
    case "appsLeaveList": disarm(""); break
    case "appsMove":
      model = AM.move(model, arg)
      if (!model.armed) armTimer.stop()
      Qt.callLater(ensureSelectedVisible)
      break
    case "appsActivate":
    case "appsArm":
      if (rowIndex < 0) { model = AM.move(model, 1); break }
      armOrKill(AM.selectedRow(model), false)
      break
    case "appsArmForce":
      if (rowIndex < 0) { model = AM.move(model, 1); break }
      armOrKill(AM.selectedRow(model), true)
      break
    case "appsDetails": openDetails(AM.selectedRow(model)); break
    case "appsCloseDetails": closeDetails(); break
    case "appsScrollDetails":
      members.contentY = Math.max(0, Math.min(Math.max(0, members.contentHeight - members.height),
        members.contentY + arg * Style.space(36)))
      break
    case "appsPage": page(arg); break
    case "appsDisarm": disarm(""); break
    }
  }

  Timer {
    id: armTimer
    interval: AM.ARM_MS
    onTriggered: root.disarm("")
  }

  // Ages "Stopping…" into "kill again to force" while a kill is outstanding.
  Timer {
    interval: 1000
    repeat: true
    running: Object.keys(root.kills).length > 0
    onTriggered: root.now = Date.now()
  }

  Timer {
    id: searchDelay
    interval: 150
    onTriggered: root.changeQuery(searchInput.text)
  }

  component AppsText: Text {
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component AppsButton: RamanButton {
    focusPolicy: Qt.NoFocus
    foreground: root.foreground
    fontFamily: root.fontFamily
  }

  // A labelled slim meter with end ticks: one resource, one denominator.
  component Rail: Item {
    id: rail
    property string label: ""
    property var fraction: null
    property color fill: Util.alpha(root.foreground, 0.55)
    implicitHeight: Math.max(railLabel.implicitHeight, Style.space(10))

    Text {
      id: railLabel
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: rail.label
      color: root.dim
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }
    RamanMeter {
      anchors.left: railLabel.right
      anchors.leftMargin: Style.space(6)
      anchors.right: parent.right
      anchors.rightMargin: Style.space(2)
      anchors.verticalCenter: parent.verticalCenter
      fraction: rail.fraction
      fill: rail.fill
      foreground: root.foreground
      endTicks: true
    }
  }

  Column {
    id: content
    width: parent.width
    spacing: Style.space(8)

    // Resource lens. GPU memory is selectable only while a device reports VRAM per client.
    Row {
      id: sortRow
      width: parent.width
      spacing: Style.space(6)
      Accessible.role: Accessible.PageTabList
      Accessible.name: "Sort apps by"

      AppsText {
        anchors.verticalCenter: parent.verticalCenter
        text: "Sort"
        color: root.dim
        font.pixelSize: Style.font.caption
      }

      Repeater {
        model: AM.SORTS
        RamanChip {
          id: chip
          required property var modelData
          objectName: "appsSort-" + modelData.key
          text: modelData.label + (available ? "" : root.model.gpuSetting === "off" ? " · off" : " · n/a")
          selected: root.model.sort === modelData.key
          available: AM.sortAvailable(modelData.key, root.model)
          focused: root.regionFocused && root.panel.nav.region === "sort" && selected
          foreground: root.foreground
          accent: root.accentTone
          fontFamily: root.fontFamily
          Accessible.role: Accessible.PageTab
          Accessible.name: modelData.label + (available ? "" : " (unavailable: " + AM.gpuLens(root.model).reason + ")")
          Accessible.selected: selected
          Accessible.onPressAction: root.changeSort(modelData.key)
          onClicked: {
            root.changeSort(chip.modelData.key)
            root.panel.focusRegion("sort")
          }
          PanelToolTip {
            visible: !chip.available && chip.hovered
            text: "GPU memory: " + AM.gpuLens(root.model).reason
            fontFamily: root.fontFamily
          }
        }
      }
    }

    TextField {
      id: searchInput
      objectName: "appsSearchInput"
      width: parent.width
      maximumLength: 256
      placeholderText: "Search name, session or PID   ( / )"
      leftPadding: Style.space(8) + searchIcon.width + Style.space(6)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      color: root.foreground
      placeholderTextColor: root.dim
      selectByMouse: true
      background: Rectangle {
        radius: Style.cornerRadius
        color: Util.alpha(root.foreground, 0.06)
        border.width: searchInput.activeFocus ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
        border.color: Util.alpha(root.foreground, searchInput.activeFocus ? 0.8 : 0.25)
        RamanIcon {
          id: searchIcon
          anchors.left: parent.left
          anchors.leftMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          name: "search"
          size: Style.font.bodySmall
          color: root.dim
        }
      }
      onActiveFocusChanged: if (activeFocus) root.disarm("")
      onTextEdited: searchDelay.restart()
      onAccepted: {
        searchDelay.stop()
        root.changeQuery(text)
        focus = false
        root.panel.focusRegion("list")
      }
      Keys.priority: Keys.AfterItem
      Keys.onEscapePressed: function(event) {
        // Leaves search (the query stays); Escape again then follows the page order.
        searchDelay.stop()
        root.changeQuery(text)
        focus = false
        root.panel.focusRegion("list")
        event.accepted = true
      }
    }

    AppsText {
      id: legendText
      objectName: "appsLegend"
      width: parent.width
      text: AM.legend(root.model.meta, root.totalKb, root.model.sort)
      color: root.dim
      font.pixelSize: Style.font.caption
      wrapMode: Text.WordWrap
    }

    // Notes above the list change only with applied pages or user actions, so
    // they never shift rows under the pointer; transient messages go below.
    Column {
      id: notesColumn
      width: parent.width
      spacing: Style.space(2)
      Repeater {
        model: root.notes.concat(root.model.sortNote ? [root.model.sortNote] : [])
        AppsText {
          required property var modelData
          width: notesColumn.width
          text: modelData
          color: /^(Incomplete|Error|Kill request)/.test(modelData) ? root.ink.warn : root.dim
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }

    ListView {
      id: list
      objectName: "appsList"
      width: parent.width
      readonly property real chrome: sortRow.height + searchInput.height + legendText.height + notesColumn.height
        + messages.height + footer.height + (hints.visible ? hints.height : 0) + content.spacing * 7
      height: Math.min(contentHeight, Math.max(Style.space(120), root.maxHeight - chrome))
      visible: !root.detailsOpen && root.model.rows.length > 0
      spacing: Style.space(3)
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      interactive: contentHeight > height
      ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
      model: root.model.rows
      currentIndex: root.rowIndex
      Accessible.role: Accessible.List
      Accessible.name: "Apps, " + AM.rangeText(root.model)

      // Freeze ordering while the pointer is over the list.
      HoverHandler {
        id: listHover
        onHoveredChanged: root.setHovering(hovered)
      }

      delegate: AppsRow {
        required property var modelData
        required property int index
        width: ListView.view.width
        app: modelData
        rowIndex: index
      }
    }

    AppsText {
      visible: !root.detailsOpen && root.model.rows.length === 0
      text: root.model.status === "ready" ? (root.model.query ? "No app matches “" + root.model.query + "”" : "No apps")
        : "Scanning processes…"
      color: root.dim
    }

    // Expanded details for one app: evidence, read on request.
    Column {
      id: detailsView
      objectName: "appsDetails"
      width: parent.width
      visible: root.detailsOpen
      spacing: Style.space(6)

      Row {
        width: parent.width
        spacing: Style.space(8)
        AppsButton { text: "Back (Esc)"; iconName: "back"; onClicked: root.closeDetails() }
        AppsButton { text: "Refresh (r)"; onClicked: root.handle("appsRefresh", null) }
      }
      AppsText {
        width: parent.width
        text: root.detailRow ? root.detailRow.name : (root.details && root.details.data ? root.details.data.id : "")
        font.pixelSize: Style.font.body
        font.bold: true
        elide: Text.ElideRight
      }
      AppsText {
        width: parent.width
        visible: !!root.details && !!root.model.focus && root.model.focus.id === root.details.id && !root.model.focus.present
        text: "No longer running."
        color: root.ink.warn
      }
      AppsText {
        width: parent.width
        wrapMode: Text.WordWrap
        text: root.detailRow ? AM.subtitle(root.detailRow) : ""
        color: root.dim
        font.pixelSize: Style.font.caption
      }
      PanelSeparator { foreground: root.foreground; visible: !!root.detailRow }
      Fact {
        visible: !!root.detailRow
        label: "Footprint"
        value: root.detailRow ? AM.footprintBreakdown(root.detailRow) : ""
      }
      Fact {
        visible: !!root.detailRow
        label: "RAM"
        value: root.detailRow ? AM.ramText(root.detailRow, root.totalKb) + " of "
          + (root.totalKb > 0 ? Level.formatKb(root.totalKb) : "physical RAM") + " resident (PSS only; swap is not resident)" : ""
        meter: root.detailRow ? AM.ramFraction(root.detailRow, root.totalKb) : null
        showMeter: true
        meterFill: root.detailRow && root.detailRow.pss / Math.max(1, root.totalKb) >= 0.25 ? root.tones.critical
          : root.detailRow && root.detailRow.pss / Math.max(1, root.totalKb) >= 0.1 ? root.tones.warn : Util.alpha(root.foreground, 0.55)
      }
      Fact {
        visible: !!root.detailRow
        label: "CPU"
        value: root.detailRow ? AM.cpuBreakdown(root.detailRow, root.model.meta).replace(/^CPU /, "") : ""
        meter: root.detailRow ? AM.cpuFraction(root.detailRow) : null
        showMeter: true
        meterFill: root.accentTone
      }
      // GPU: one block per device (source, semantics, engines, coverage).
      Item {
        id: gpuFact
        width: parent.width
        visible: gpuLines.count > 0 || vramMeters.count > 0
        height: gpuColumn.implicitHeight
        AppsText {
          text: "GPU"
          color: root.dim
          font.pixelSize: Style.font.caption
          font.bold: true
          font.letterSpacing: 0.8
        }
        Column {
          id: gpuColumn
          x: root.factLabelWidth
          width: parent.width - x
          spacing: Style.space(2)
          Repeater {
            id: gpuLines
            model: root.detailRow ? AM.gpuLines(root.detailRow, root.model.meta) : []
            AppsText {
              required property var modelData
              objectName: "appsGpuLine"
              width: gpuColumn.width
              wrapMode: Text.WordWrap
              text: modelData
              color: /^ /.test(modelData) ? root.dim : root.foreground
              font.pixelSize: /^ /.test(modelData) ? Style.font.caption : Style.font.bodySmall
            }
          }
          Repeater {
            id: vramMeters
            model: root.detailRow ? AM.vramMeters(root.detailRow, root.model.meta) : []
            Rail {
              required property var modelData
              objectName: "appsVramMeter"
              width: gpuColumn.width
              label: modelData.text
              fraction: modelData.fraction
              fill: root.accentTone
            }
          }
        }
      }
      PanelSeparator { foreground: root.foreground }
      AppsText {
        width: parent.width
        wrapMode: Text.WordWrap
        color: root.dim
        font.pixelSize: Style.font.caption
        text: !root.details ? ""
          : root.details.error ? "Details failed: " + root.details.error
          : !root.details.data ? "Reading members…"
          : "MEMBERS " + root.details.data.membersTotal + (root.details.data.members.length < root.details.data.membersTotal
            ? " (showing the " + root.details.data.members.length + " largest)" : "")
            + " · command lines read just now, not saved"
      }
      ListView {
        id: members
        objectName: "appsMembers"
        width: parent.width
        height: Math.min(contentHeight, Math.max(Style.space(100), root.maxHeight - list.chrome - Style.space(150)))
        clip: true
        spacing: Style.space(4)
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
        model: root.details && root.details.data ? root.details.data.members : []
        delegate: Column {
          required property var modelData
          width: ListView.view.width
          spacing: Style.space(1)
          AppsText {
            width: parent.width
            text: AM.memberText(modelData)
            elide: Text.ElideRight
          }
          AppsText {
            width: parent.width
            text: AM.commandText(modelData)
            color: root.dim
            font.pixelSize: Style.font.caption
            elide: Text.ElideMiddle
          }
        }
      }
    }

    Column {
      id: messages
      width: parent.width
      spacing: Style.space(2)
      Repeater {
        model: (root.model.notice ? [root.model.notice] : []).concat(root.model.error ? ["Error: " + root.model.error] : [])
        AppsText {
          required property var modelData
          width: messages.width
          text: modelData
          color: /^(Error|Kill request)/.test(modelData) ? root.ink.warn : root.dim
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }
      }
    }

    Item {
      id: footer
      width: parent.width
      implicitHeight: Math.max(prevButton.implicitHeight, rangeLabel.implicitHeight)
      height: implicitHeight
      visible: !root.detailsOpen

      AppsText {
        id: rangeLabel
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: AM.rangeText(root.model) + (root.model.queued ? (root.armed ? " · updates wait for the armed kill" : " · updates paused under the pointer") : "")
        color: root.dim
        font.pixelSize: Style.font.caption
      }
      Row {
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(6)
        AppsButton { id: prevButton; text: "Prev"; iconName: "back"; enabled: root.model.offset > 0; onClicked: root.page(-1) }
        AppsButton { text: "Next"; iconName: "next"; enabled: root.model.nextOffset !== null; onClicked: root.page(1) }
      }
    }

    AppsText {
      id: hints
      width: parent.width
      visible: !root.detailsOpen
      text: "/ search · d details · x kill · f force · [ ] pages · ↑ sort"
      color: root.dim
      font.pixelSize: Style.font.caption
      wrapMode: Text.Wrap
    }
  }

  // A labelled fact in details: label column, wrapped value, optional meter.
  readonly property real factLabelWidth: Style.space(78)
  component Fact: Item {
    id: fact
    property string label: ""
    property string value: ""
    property var meter: null
    property bool showMeter: false
    property color meterFill: root.foreground
    width: parent ? parent.width : 0
    height: Math.max(factLabel.implicitHeight, factValue.implicitHeight + (showMeter ? factMeter.height + Style.space(4) : 0))
    AppsText {
      id: factLabel
      text: fact.label.toUpperCase()
      color: root.dim
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 0.8
    }
    AppsText {
      id: factValue
      x: root.factLabelWidth
      width: parent.width - x
      wrapMode: Text.WordWrap
      text: fact.value
    }
    RamanMeter {
      id: factMeter
      visible: fact.showMeter
      x: root.factLabelWidth + Style.space(1)
      y: factValue.implicitHeight + Style.space(4)
      width: Math.min(Style.space(200), parent.width - x - Style.space(2))
      fraction: fact.meter
      fill: fact.meterFill
      foreground: root.foreground
      endTicks: true
    }
  }

  component AppsRow: CursorSurface {
    id: row
    required property var app
    required property int rowIndex

    readonly property bool selected: root.model.selectedId === app.id
    readonly property bool armed: !!root.model.armed && root.model.armed.id === app.id
    readonly property string killStatus: root.killStatus(app.id)
    readonly property bool memoryLens: root.model.sort === "memory"
    readonly property bool gpuLens: root.model.sort === "gpu-memory"
    readonly property real share: root.totalKb > 0 && app.pss !== null && app.pss !== undefined ? Math.min(1, app.pss / root.totalKb) : 0
    readonly property string subtitle: {
      if (killStatus === "stopping") return "Stopping…"
      if (killStatus === "stubborn") return "Still running — kill again to force"
      if (killStatus === "error") return "Not killed: " + root.kills[app.id].error
      return AM.subtitle(app, root.model.sort)
    }

    hasCursor: selected
    foreground: root.foreground
    implicitHeight: lines.implicitHeight + Style.space(10)
    Accessible.role: Accessible.ListItem
    Accessible.name: AM.accessibleRow(app, root.totalKb, root.model.sort)
    Accessible.selected: selected

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton | Qt.RightButton
      onContainsMouseChanged: if (containsMouse) root.select(row.app.id)
      onClicked: function(mouse) {
        root.select(row.app.id)
        root.panel.focusRegion("list")
        if (mouse.button === Qt.RightButton) root.armOrKill(row.app, true)
      }
      onDoubleClicked: root.openDetails(row.app)
    }

    Column {
      id: lines
      anchors.left: parent.left
      anchors.right: actions.left
      anchors.leftMargin: Style.space(10)
      anchors.rightMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(2)

      Item {
        width: parent.width
        height: Math.max(nameText.implicitHeight, sizeText.implicitHeight)
        AppsText {
          id: nameText
          anchors.left: parent.left
          anchors.right: sizeText.left
          anchors.rightMargin: Style.space(6)
          anchors.verticalCenter: parent.verticalCenter
          text: row.app.name
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }
        AppsText {
          id: sizeText
          anchors.right: cpuText.left
          anchors.verticalCenter: parent.verticalCenter
          width: root.valueWidth
          horizontalAlignment: Text.AlignRight
          rightPadding: Style.space(6)
          text: Level.footprintText(row.app)
          font.pixelSize: Style.font.subtitle
          font.bold: row.memoryLens
        }
        AppsText {
          id: cpuText
          objectName: "appsLensValue"
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          width: root.cpuWidth
          horizontalAlignment: Text.AlignRight
          // The lens's second number: CPU share, or VRAM in the GPU memory lens.
          text: row.gpuLens ? "VRAM " + AM.gpuShort(row.app) : "CPU " + AM.cpuShort(row.app)
          font.pixelSize: row.memoryLens ? Style.font.bodySmall : Style.font.subtitle
          font.bold: !row.memoryLens
          color: (row.gpuLens ? row.app.gpuMemoryKb : row.app.cpuMachinePercent) === null
            || (row.gpuLens ? row.app.gpuMemoryKb : row.app.cpuMachinePercent) === undefined ? root.dim : root.foreground
        }
      }

      Item {
        width: parent.width
        height: Math.max(subText.implicitHeight, ramRail.implicitHeight)
        AppsText {
          id: subText
          anchors.left: parent.left
          anchors.right: ramRail.left
          anchors.rightMargin: Style.space(6)
          anchors.verticalCenter: parent.verticalCenter
          text: row.subtitle
          color: row.killStatus === "error" || row.killStatus === "stubborn" ? root.ink.critical : root.dim
          font.pixelSize: Style.font.caption
          elide: Text.ElideRight
        }
        Rail {
          id: ramRail
          anchors.right: cpuRail.left
          anchors.rightMargin: Style.space(6)
          anchors.verticalCenter: parent.verticalCenter
          width: root.valueWidth - Style.space(6)
          label: "RAM"
          fraction: AM.ramFraction(row.app, root.totalKb)
          fill: row.share >= 0.25 ? root.tones.critical : row.share >= 0.1 ? root.tones.warn : Util.alpha(root.foreground, 0.55)
        }
        Rail {
          id: cpuRail
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          width: root.cpuWidth
          label: "CPU"
          fraction: AM.cpuFraction(row.app)
          fill: root.accentTone
        }
      }
    }

    // Fixed-width slot so values stay aligned whether or not actions show.
    Item {
      id: actions
      anchors.right: parent.right
      anchors.rightMargin: Style.space(6)
      anchors.verticalCenter: parent.verticalCenter
      width: root.actionWidth
      height: Math.max(detailsButton.implicitHeight, confirm.implicitHeight)

      RamanIconButton {
        id: detailsButton
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        visible: row.hasCursor && !row.armed
        iconName: "info"
        tooltipText: "Details (d)"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.openDetails(row.app)
      }

      RamanIcon {
        objectName: "appsProtected"
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
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: !row.app.protected && !row.armed && (row.hasCursor || row.killStatus !== "")
        iconName: row.killStatus === "stubborn" ? "force" : "close"
        tooltipText: row.killStatus === "stubborn" ? "Force kill (SIGKILL)" : "Kill (SIGTERM) · right-click to force"
        foreground: root.foreground
        hoverColor: root.ink.critical
        fontFamily: root.fontFamily
        onHovered: function(on) { if (on) root.select(row.app.id) }
        onClicked: root.armOrKill(row.app, false)
      }

      BorderSurface {
        id: confirm
        objectName: "appsConfirm"
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        visible: row.armed
        implicitWidth: Math.max(confirmText.implicitWidth + Style.space(14), killButton.implicitWidth)
        implicitHeight: Math.max(killButton.implicitHeight, confirmText.implicitHeight + Style.space(6))
        radius: Style.cornerRadius
        color: Util.alpha(root.tones.critical, confirmMouse.containsMouse ? Design.CONFIRM_HOT_TINT : Design.CONFIRM_TINT)
        borderSpec: Border.flat(root.tones.critical, Math.max(1, Style.normalBorderWidth))
        Accessible.role: Accessible.Button
        Accessible.name: (root.model.armed && root.model.armed.force ? "Confirm force kill of " : "Confirm kill of ") + row.app.name
        Accessible.onPressAction: root.armOrKill(row.app, !!root.model.armed && root.model.armed.force)

        Text {
          id: confirmText
          anchors.centerIn: parent
          textFormat: Text.PlainText
          text: root.model.armed && root.model.armed.force ? "Force?" : "Kill?"
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
          onClicked: root.armOrKill(row.app, !!root.model.armed && root.model.armed.force)
        }
      }
    }
  }
}
