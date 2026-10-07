import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import "StorageModel.js" as SM
import "Level.js" as Level
import "Design.js" as Design

// A visible page owns one client. Only Scan recurses; all browsing queries
// read bounded snapshots in the backend's short worker lane.
Item {
  id: root
  property var panel: null
  readonly property var runtime: panel ? panel.runtime : null
  property var owner: null
  property var session: null
  property var scope: ({ path: Quickshell.env("HOME") })
  property string scopeLabel: capacity && capacity.scope ? capacity.scope : scope.path || "Selected volume"
  property var capacity: null
  property var mounts: []
  property var snapshot: null
  property var listing: null
  property var loaded: ({})
  property string selectedId: ""
  property var crumbs: []
  property string filter: ""
  property string status: "Discovering capacity"
  property string error: ""
  property string phase: "discovering"
  property string terminalStatus: ""
  property string scanId: ""
  property bool scanning: false
  property bool reconciling: false
  property int rowIndex: 0
  property var pageOffsets: []
  property var tinyMembers: []
  property int tinyOffset: 0
  property bool showingSelf: false
  property var memberSource: null
  property var memberContinuation: null
  property bool memberTail: false
  property var memberTailPages: []
  readonly property bool memberView: !!memberSource || tinyMembers.length > 0
  readonly property bool previousMembers: memberTail ? memberTailPages.length > 0 || tinyMembers.length > 0 : tinyOffset > 0
  readonly property bool nextMembers: memberTail ? !!listing && listing.nextOffset !== null : tinyOffset + 50 < tinyMembers.length || memberContinuation !== null
  readonly property var ledgerRows: visibleRows()
  property real now: Date.now()
  property real maxHeight: 600
  readonly property bool inputOwned: pathInput.activeFocus || filterInput.activeFocus
  readonly property bool cachePending: pendingPurpose("reconciled")
  readonly property bool queryPending: pendingLedger()
  readonly property bool helperAvailable: !!owner && owner.storageHelperAvailable
  readonly property bool entriesReady: helperAvailable && !queryPending && !scanning && !reconciling
  readonly property var selected: entriesReady ? SM.selected(ledgerRows, selectedId) : null
  readonly property color foreground: panel ? panel.foreground : "white"
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property var geometry: listing ? SM.bands(listing, loaded, width, Style.space(28)) : []
  readonly property int bandRows: {
    var deepest = -1
    for (var i = 0; i < geometry.length; i++) deepest = Math.max(deepest, geometry[i].depth)
    return deepest + 1
  }
  implicitHeight: Math.min(maxHeight, content.implicitHeight)

  readonly property color surface: Color.popups.background
  readonly property string fontFamily: panel && panel.fontFamily ? panel.fontFamily : Style.font.family
  readonly property color accentTone: panel && panel.accentTone !== undefined ? panel.accentTone
    : Design.ensureContrast(Color.accent, surface, Design.MARK_CONTRAST)
  readonly property var ink: panel && panel.ink ? panel.ink
    : Design.tones(panel && panel.palette ? panel.palette : Level.palette({}, {}), surface, Design.TEXT_CONTRAST)

  // Folder bands: the accent, strongest at the top level and lighter with
  // depth, alternating between neighbours so adjacent bands stay distinct.
  // "Folder itself" and "Other" are neutral. Width alone encodes size.
  function bandColor(segment, index) {
    if (segment.row.kind === "other" || segment.row.kind === "self") return Design.hex(Design.mix(surface, foreground, 0.2))
    var strength = [0.82, 0.48, 0.28][Math.min(2, segment.depth)] - (index % 2 ? 0.14 : 0)
    return Design.hex(Design.mix(surface, accentTone, strength))
  }
  function bandInk(fill) {
    return Design.contrast(foreground, fill) >= Design.contrast(surface, fill) ? foreground : surface
  }
  // Position of each band among its siblings, for the alternating shade.
  readonly property var bandIndex: {
    var seen = {}, out = []
    for (var i = 0; i < geometry.length; i++) {
      var key = geometry[i].depth + ":" + geometry[i].parentId
      seen[key] = (seen[key] || 0) + 1
      out.push(seen[key] - 1)
    }
    return out
  }

  component StorageText: Text {
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    textFormat: Text.PlainText
    color: root.foreground
  }
  component StorageCaption: StorageText {
    font.pixelSize: Style.font.caption
    color: root.dim
    wrapMode: Text.Wrap
  }
  component StorageHeader: PanelSectionHeader {
    foreground: root.foreground
    fontFamily: root.fontFamily
  }
  component StorageButton: RamanButton {
    id: control
    foreground: root.foreground
    fontFamily: root.fontFamily
    Connections {
      target: control
      function onClicked() { Qt.callLater(root.restoreNavigationFocus) }
    }
  }
  component StorageField: TextField {
    id: field
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    placeholderTextColor: root.dim
    selectByMouse: true
    background: Rectangle {
      radius: Style.cornerRadius
      color: Util.alpha(root.foreground, 0.06)
      border.width: field.activeFocus ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
      border.color: Util.alpha(root.foreground, field.activeFocus ? 0.8 : 0.25)
    }
  }
  function restoreNavigationFocus() {
    if (panel && panel.nav && !inputOwned) panel.focusRegion(panel.nav.region)
  }
  function persist() { if (panel) panel.storageSession = session }
  function send(command, args, purpose, selection) {
    if (!helperAvailable || !session) return false
    var result = SM.request(session, command, args, purpose)
    if (!SM.commandFits(result.message)) { error = "Request exceeds the 4096-byte protocol limit; choose a shorter path/filter."; return false }
    if (selection) result.state.pending[result.message.requestId].selection = selection
    if (!owner.sendCommand(result.message)) return false
    session = result.state
    persist()
    return true
  }
  function advance() { session = SM.advance(session); persist() }
  function leave() {
    if (!owner || !session) return
    advance()
    send("storage.leave", {}, "leave")
    session = Object.assign({}, session, { pending: {} })
    persist()
  }
  function attach() {
    if (owner === runtime) return
    leave()
    owner = runtime
    listing = null; snapshot = null; resetNavigation(); scanId = ""; scanning = false; reconciling = false
    if (!owner || !panel) return
    if (panel.storageOwner !== owner) {
      panel.storageOwner = owner
      panel.storageSession = SM.state(owner.newClientId("st"))
    }
    session = panel.storageSession
    if (owner.storageRememberScope && owner.storageScope) scope = owner.storageScope
    else scope = { path: Quickshell.env("HOME") }
    discover()
  }
  function discover() {
    if (!helperAvailable) {
      phase = "helper-failed"; terminalStatus = "Storage helper unavailable — waiting for startup"
      status = terminalStatus; error = "No request sent; cached discovery will retry when the helper starts."
      return
    }
    phase = "discovering"; terminalStatus = ""
    status = "Discovering capacity / cached snapshot"; error = ""
    send("storage.mounts", scope, "capacity")
    send("storage.cached", scope, "cached")
    send("storage.mounts", { offset: 0, limit: 50 }, "mounts")
  }
  function invalidateScope(code) {
    var failure = SM.failure(code)
    // Retire every outstanding query/action/scan before dropping its identity.
    // The controller only cancels work owned by this client.
    leave()
    scanning = false; reconciling = false; scanId = ""
    snapshot = null; listing = null; capacity = null; resetNavigation()
    phase = failure.state; terminalStatus = failure.text; status = terminalStatus
  }
  function reloadCache() {
    leave()
    scanning = false; reconciling = false; scanId = ""
    listing = null; snapshot = null; capacity = null; resetNavigation()
    discover()
  }
  function recoverHelper(readyOwner) {
    if (owner === readyOwner && helperAvailable) reloadCache()
  }
  function changeScope(value) {
    leave()
    advance()
    scope = value
    if (owner.storageRememberScope) owner.storageScope = scope
    snapshot = null; listing = null; loaded = ({}); capacity = null; crumbs = []; filter = ""
    scanning = false; reconciling = false; scanId = ""; resetNavigation()
    discover()
  }
  function resetNavigation() {
    loaded = ({}); pageOffsets = []; clearMemberView()
    rowIndex = 0; selectedId = ""; crumbs = []; filter = ""; filterInput.text = ""
  }
  function visibleRows() {
    if (!listing) return []
    if (showingSelf) return [listing.node]
    if (tinyMembers.length && !memberTail) {
      var source = memberSource || listing
      var rows = source.segments.filter(function(r) { return tinyMembers.indexOf(r.nodeId) >= 0 })
      if (tinyMembers.indexOf(source.nodeId) >= 0) rows.unshift(source.node)
      return rows.slice(tinyOffset, tinyOffset + 50)
    }
    return !filter && !listing.rows.length ? [listing.node] : listing.rows
  }
  function syncSelection(preferred) {
    // Derive directly: a property-change handler can run before ledgerRows
    // has reevaluated its binding (notably when paging tiny members).
    var rows = visibleRows()
    var index = -1
    for (var i = 0; i < rows.length; i++) if (rows[i].nodeId === preferred) index = i
    rowIndex = index >= 0 ? index : 0
    selectedId = rows.length ? rows[rowIndex].nodeId : ""
  }
  // Only a replacement query supersedes pending ledger data. Old rows and
  // bands stay visible but cannot change selection or actions until it settles.
  // Band replies only update geometry, so they can remain outstanding.
  function invalidateLedger() {
    var pending = {}
    for (var id in session.pending) if (session.pending[id].purpose !== "ledger") pending[id] = session.pending[id]
    session = Object.assign({}, session, { pending: pending }); persist()
  }
  function clearMemberView() {
    memberSource = null; memberContinuation = null; memberTail = false; memberTailPages = []
    tinyMembers = []; tinyOffset = 0; showingSelf = false
  }
  function selectRow(id) {
    if (!entriesReady) return
    syncSelection(id)
    ledger.positionViewAtIndex(rowIndex, ListView.Contain)
  }
  function selectSelf(source) {
    if (!source || !entriesReady) return
    clearMemberView()
    if (listing.nodeId !== source.nodeId) pageOffsets = []
    // A self segment represents its directory, not a synthetic action node.
    // Cached band replies contain the same validated node/rows as ledger replies.
    listing = source; showingSelf = true
    crumbs = source.breadcrumbs || [{ nodeId: source.nodeId, name: source.node.name }]
    selectRow(source.nodeId)
    panel.focusRegion("ledger")
  }
  function allEntries() {
    if (!entriesReady) return
    if (memberSource) listing = memberSource
    clearMemberView(); syncSelection(selectedId)
  }
  function showTiny() {
    if (!entriesReady) return
    clearMemberView()
    tinyMembers = listing.segments.filter(function(r) { return root.width * r.knownAllocatedBytes / Math.max(1, listing.node.knownAllocatedBytes) < 3 }).map(function(r) { return r.nodeId })
    syncSelection(tinyMembers[0])
  }
  function page(offset) { if (!entriesReady) return; clearMemberView(); query(listing.nodeId, offset) }
  function applyFilter(text) {
    if (scanning || reconciling || cachePending || !listing) return
    advance(); clearMemberView(); pageOffsets = []; selectedId = ""; rowIndex = 0
    filter = text; query(listing.nodeId, 0)
  }
  onTinyOffsetChanged: {
    // A nested Other query may still be resolving this member set's directory.
    // Keep that request; its reply selects only from the latest visible slice.
    if (session && !pendingLedger()) syncSelection(selectedId)
  }
  function expandOther(segment) {
    if (!entriesReady) return
    var source = listing.nodeId === segment.parentId ? listing : loaded[segment.parentId]
    if (!source) return
    clearMemberView()
    memberSource = source
    memberContinuation = segment.row.offset === undefined ? null : segment.row.offset
    tinyMembers = segment.row.members || []; pageOffsets = []
    // Nested map data has its own committed filter. Query that exact directory,
    // keeping the band's explicit IDs separate from its backend continuation.
    filter = source.filter || ""; filterInput.text = filter
    if (!tinyMembers.length && memberContinuation !== null) {
      memberTail = true; query(segment.parentId, memberContinuation)
    } else if (listing.nodeId !== segment.parentId) query(segment.parentId, 0, "ledger", 0, tinyMembers[0])
    if (!pendingLedger()) syncSelection(tinyMembers[0])
  }
  function nextMemberPage() {
    if (!entriesReady || !nextMembers) return
    if (memberTail) {
      memberTailPages = memberTailPages.concat([listing.offset])
      query(memberSource.nodeId, listing.nextOffset)
    } else if (tinyOffset + 50 < tinyMembers.length) tinyOffset += 50
    else { memberTail = true; query(memberSource.nodeId, memberContinuation) }
  }
  function previousMemberPage() {
    if (!entriesReady || !previousMembers) return
    if (memberTail && memberTailPages.length) {
      var offsets = memberTailPages.slice(), offset = offsets.pop()
      memberTailPages = offsets; query(memberSource.nodeId, offset)
    } else if (memberTail) {
      listing = memberSource; memberTail = false
      tinyOffset = Math.floor((tinyMembers.length - 1) / 50) * 50
      syncSelection("")
    } else tinyOffset = Math.max(0, tinyOffset - 50)
  }
  function pendingPurpose(purpose) {
    if (!session) return false
    for (var id in session.pending) if (session.pending[id].purpose === purpose) return true
    return false
  }
  function pendingLedger() { return pendingPurpose("ledger") || pendingPurpose("reconciled") }
  function query(nodeId, offset, purpose, depth, selection) {
    if (!snapshot) return
    if (purpose !== "band") {
      if (scanning || reconciling || cachePending) return
      invalidateLedger(); selectedId = ""; rowIndex = 0
    }
    var args = { snapshotId: snapshot.snapshotId, offset: offset || 0, limit: 50, filter: purpose === "band" ? "" : filter }
    if (nodeId) args.nodeId = nodeId
    if (purpose === "band") args.level = depth || 1
    send("storage.children", args, purpose || "ledger", selection)
  }
  function selectMap(segment) {
    if (!entriesReady) return
    panel.focusRegion("ledger")
    if (segment.row.kind === "directory") { navigate(segment.nodeId); return }
    var source = listing.nodeId === segment.parentId ? listing : loaded[segment.parentId]
    if (!source) return
    if (segment.row.kind === "self") { selectSelf(source); return }
    clearMemberView()
    if (listing.nodeId === segment.parentId && SM.selected(listing.rows, segment.nodeId)) {
      selectRow(segment.nodeId)
    } else {
      var offset = 0
      for (var j = 0; j < source.segments.length; j++) if (source.segments[j].nodeId === segment.nodeId) offset = j
      pageOffsets = []; query(segment.parentId, offset, "ledger", 0, segment.nodeId)
    }
  }
  function scan() {
    if (!entriesReady || !owner) return
    advance(); loaded = ({})
    phase = "scanning"; terminalStatus = ""
    scanning = true; scanId = ""; error = ""; status = "Scan queued" + (snapshot ? " · previous snapshot shown" : " · total unknown")
    if (!send("storage.scan", scope, "scan")) { scanning = false; status = "Scan not started" }
  }
  function cancel() {
    if (!scanning || !scanId || reconciling) return
    advance(); reconciling = true; status = "Cancelling — reconciling selected snapshot"
    send("storage.cancel", { scanId: scanId }, "cancel")
  }
  function navigate(node, name) {
    if (!snapshot || scanning || reconciling || cachePending) return
    advance(); filter = ""; filterInput.text = ""; pageOffsets = []; clearMemberView(); loaded = ({})
    selectedId = node; error = ""; query(node, 0)
  }
  function up() {
    if (!entriesReady) return
    if (memberView || showingSelf) { allEntries(); return }
    if (!listing || !listing.node.parentId) return
    navigate(listing.node.parentId)
  }
  function descend() { if (selected && selected.kind === "directory") navigate(selected.nodeId) }
  function action(operation) {
    if (!selected || scanning || reconciling || !snapshot) return
    send("storage.action", { snapshotId: snapshot.snapshotId, nodeId: selected.nodeId, action: operation }, "action")
  }
  function handle(actionName, arg) {
    if (inputOwned) return
    if (actionName === "storageMove" && entriesReady && ledgerRows.length) {
      rowIndex = Math.max(0, Math.min(ledgerRows.length - 1, rowIndex + arg))
      selectRow(ledgerRows[rowIndex].nodeId)
    } else if (actionName === "storageDescend") descend()
    else if (actionName === "storageUp") up()
    else if (actionName === "storageScan") scan()
    else if (actionName === "storageCancel") cancel()
    else if (actionName === "storageOpen") action("open")
    else if (actionName === "storageCopy") action("copy")
    else if (actionName === "storageEscape") { if (queryPending) panel.close(); else if (listing && listing.node.parentId || memberView || showingSelf) up(); else panel.close() }
  }
  function accept(message) {
    var result = SM.accept(session, message)
    if (!result) return
    session = result.state; persist()
    var purpose = result.entry.purpose
    if (message.type === "error") {
      error = message.code + ": " + message.message
      var failure = SM.failure(message.code)
      // Volume-list discovery is independent of the selected scope.
      if (purpose !== "mounts" && purpose !== "band" && purpose !== "action"
          && !(purpose === "capacity" && (scanning || reconciling || snapshot || terminalStatus))) {
        phase = failure.state; status = failure.text
      }
      if (purpose === "ledger") {
        // A failed query leaves the old listing visible with its committed filter.
        filter = listing ? listing.filter || "" : ""; filterInput.text = filter
        clearMemberView(); syncSelection("")
      }
      if (purpose === "scan" || purpose === "cancel") {
        scanning = false; reconciling = false; scanId = ""
        if (purpose === "cancel" && message.code === "unknown-scan") {
          status = "Scan no longer active · outcome unknown; reloading selected cache"
          phase = "uncertain"; terminalStatus = status
          error = "Cancellation outcome unknown; the scan may already have completed. Reloading authoritative cache."
          send("storage.cached", scope, "reconciled")
        } else {
          terminalStatus = failure.text + (snapshot ? " · older snapshot shown" : " · no previous snapshot")
          status = terminalStatus
          // An interrupted commit may have changed authoritative selection.
          if (message.commitState === "unknown" || message.commitState === "committed") send("storage.cached", scope, "reconciled")
        }
      }
      if (purpose !== "mounts" && failure.invalidates) invalidateScope(message.code)
      return
    }
    if (message.type === "storage-progress") {
      if (purpose !== "scan" && purpose !== "cancel") return
      scanId = message.scanId || scanId
      status = message.status + " · " + (message.entries || 0) + " entries · unreadable " + (message.unreadable || 0) + " · excluded " + (message.excluded || 0) + " · changed " + (message.changed || 0) + (snapshot ? " · older snapshot shown" : "")
      return
    }
    if (message.type === "storage-capacity") {
      if (purpose === "mounts") {
        if (!message.offset) mounts = []
        mounts = mounts.concat(message.mounts || [])
        if (message.nextOffset !== null && message.nextOffset !== undefined) send("storage.mounts", { offset: message.nextOffset, limit: 50 }, "mounts")
      } else {
        if (snapshot && snapshot.identity && !SM.sameIdentity(snapshot.identity, message.identity)) {
          invalidateScope("root-changed")
          error = "Filesystem/root identity changed; a matching path cannot validate the old snapshot."
          return
        }
        capacity = message
      }
    } else if (message.type === "storage-result") {
      if (purpose === "scan" || purpose === "cancel") {
        scanning = false; reconciling = false; scanId = ""
        status = message.status + " · " + (message.previousSnapshotRetained ? "previous selected snapshot retained" : "selection reconciled")
        phase = message.status; terminalStatus = status
        if (message.status === "uncertain") error = "Commit outcome uncertain; cached selection may have pending durability. " + (message.code || "")
        if (purpose === "scan") send("storage.mounts", scope, "capacity")
        // All recovered terminal outcomes name the authoritative selection,
        // sometimes without metadata. Reload it rather than inventing unscanned.
        if (!message.snapshot) { send("storage.cached", scope, "reconciled"); return }
      }
      if (purpose === "reconciled" && status.indexOf("Scan no longer active") === 0) {
        status = "Selected cache reloaded · cancellation outcome unknown"
        terminalStatus = status
        error = "Cancellation outcome unknown; showing the authoritative cached selection."
      }
      if (message.snapshot) {
        if (message.snapshot.coverage === "partial" && (purpose === "scan" || purpose === "cached")) {
          phase = "partial"
          terminalStatus = "Ready with unreadable/excluded content · known allocation is a lower bound"
          status = terminalStatus
        }
        if (purpose === "cached" && !terminalStatus) {
          phase = message.snapshot.coverage === "partial" ? "partial" : "ready"
          status = phase === "partial" ? "Ready with unreadable/excluded content · known allocation is a lower bound" : "Cached snapshot ready"
        }
        if (message.snapshot.coverage === "partial" && (purpose === "reconciled" || purpose === "cancel"))
          status = terminalStatus + " · partial inventory, known allocation is a lower bound"
        resetNavigation(); listing = null; snapshot = message.snapshot
        query(snapshot.rootNodeId, 0)
      } else {
        resetNavigation(); snapshot = null; listing = null
        if (!terminalStatus) { phase = "unscanned"; status = "Unscanned — choose Scan" }
        else status = terminalStatus + " · no cached snapshot"
      }
    } else if (message.type === "storage-children") {
      if (purpose === "band") {
        var next = Object.assign({}, loaded); next[message.nodeId] = message; loaded = next
        if (owner.storageShowMap && result.entry.args.level === 1) {
          var child = message.segments.filter(function(r) { return r.kind === "directory" })[0]
          if (child) query(child.nodeId, 0, "band", 2)
        }
      }
      else {
        listing = message; syncSelection(result.entry.selection || selectedId)
        crumbs = message.breadcrumbs || [{ nodeId: message.nodeId, name: message.node.name }]
        var dirs = message.segments.filter(function(r) { return r.kind === "directory" }).slice(0, 3)
        if (owner.storageShowMap && !filter) for (var i = 0; i < dirs.length; i++) query(dirs[i].nodeId, 0, "band")
      }
    } else if (message.type === "storage-action") {
      if (message.action === "copy") status = "Path copied"
      else status = "Directory handed to file manager"
    }
  }
  onRuntimeChanged: attach()
  Component.onCompleted: attach()
  Component.onDestruction: leave()
  Connections {
    target: root.owner
    function onStorageMessage(message) { root.accept(message) }
    function onStorageReady() {
      // Startup is a transport event, independent of presentation/input state.
      // Let transport property bindings settle before testing availability.
      Qt.callLater(root.recoverHelper, root.owner)
    }
    function onStorageRestarted() {
      root.advance(); root.scanning = false; root.reconciling = false; root.scanId = ""
      root.listing = null; root.snapshot = null; root.capacity = null; root.resetNavigation()
      root.phase = "helper-failed"; root.terminalStatus = "Storage helper stopped — waiting for restart"
      root.status = root.terminalStatus; root.error = "Helper restart invalidated pending work; no automatic scan."
    }
  }
  Timer { interval: 30000; running: !!root.owner; repeat: true; onTriggered: { root.now = Date.now(); root.send("storage.mounts", root.scope, "capacity") } }

  ScrollView {
    anchors.fill: parent
    contentWidth: availableWidth
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    palette.text: root.foreground
    palette.buttonText: root.foreground
    palette.button: Util.alpha(root.foreground, 0.14)
    palette.base: Util.alpha(root.foreground, 0.06)
    palette.highlight: root.accentTone
    palette.highlightedText: root.foreground
    clip: true
    Column {
      id: content
      width: parent.width
      spacing: Style.space(8)

      // --- scope -------------------------------------------------------------
      Text {
        width: parent.width
        textFormat: Text.PlainText
        elide: Text.ElideMiddle
        text: root.scopeLabel
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.title
        font.bold: true
        Accessible.role: Accessible.Heading
        Accessible.name: "Storage: " + root.scopeLabel
      }
      Flow {
        width: parent.width; spacing: Style.space(6)
        StorageButton { text: "Home"; onClicked: root.changeScope({ path: Quickshell.env("HOME") }) }
        StorageButton { text: "Root"; onClicked: root.changeScope({ path: "/" }) }
        ComboBox {
          id: volumes
          width: Math.min(Style.space(180), content.width)
          height: Math.max(Style.space(26), implicitContentHeight + Style.space(10))
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          background: Rectangle {
            radius: Style.cornerRadius
            color: Util.alpha(root.foreground, volumes.hovered ? 0.15 : 0.05)
            border.width: volumes.visualFocus ? Math.max(2, Style.normalBorderWidth * 2) : Style.normalBorderWidth
            border.color: Util.alpha(root.foreground, volumes.visualFocus ? 1 : 0.25)
          }
          contentItem: StorageText { text: volumes.displayText; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; leftPadding: Style.space(9); rightPadding: Style.space(22) }
          popup.background: Rectangle { color: root.surface; radius: Style.cornerRadius; border.width: 1; border.color: Util.alpha(root.foreground, 0.35) }
          delegate: ItemDelegate {
            required property var modelData
            width: volumes.width
            contentItem: StorageText { text: modelData.path + (modelData.excluded ? " · " + modelData.excluded : ""); elide: Text.ElideRight }
          }
          model: root.mounts
          textRole: "path"
          displayText: "Local volume"
          onActivated: function(index) { var m = root.mounts[index]; root.changeScope({ pathBytes: m.pathBytes }) }
        }
      }
      Row {
        width: parent.width; spacing: Style.space(6)
        StorageField {
          id: pathInput
          objectName: "storagePathInput"
          width: Math.max(Style.space(80), parent.width - pathGo.width - parent.spacing)
          placeholderText: "Absolute folder path"
          Accessible.name: "Storage folder path"
          onAccepted: { root.changeScope({ path: text }); focus = false }
          Keys.priority: Keys.AfterItem
          Keys.onEscapePressed: function(event) { focus = false; event.accepted = true }
        }
        StorageButton { id: pathGo; text: "Go"; onClicked: { root.changeScope({ path: pathInput.text }); pathInput.focus = false } }
      }

      // --- filesystem capacity (never scans) ---------------------------------
      PanelSeparator { foreground: root.foreground }
      Item {
        width: parent.width
        height: capacityHeader.implicitHeight
        StorageHeader { id: capacityHeader; text: "FILESYSTEM" }
        StorageHeader {
          anchors.right: parent.right
          visible: !!root.capacity && root.capacity.totalBytes !== undefined
          text: root.capacity && root.capacity.totalBytes !== undefined
            ? SM.bytes(root.capacity.totalBytes).toUpperCase() + (root.capacity.readOnly ? " · READ ONLY" : "") : ""
        }
      }
      // Used, reserved and available, left to right on one track: the filled
      // part is what is in use; available is the empty track.
      Item {
        id: capacityStrip
        width: parent.width; height: Style.space(8)
        readonly property var parts: root.capacity && root.capacity.totalBytes !== undefined
          ? [root.capacity.usedBytes, root.capacity.reservedBytes] : []
        Accessible.role: Accessible.ProgressBar
        Accessible.name: root.capacity && root.capacity.totalBytes !== undefined ? "Filesystem: " + SM.bytes(root.capacity.usedBytes) + " used, "
          + SM.bytes(root.capacity.reservedBytes) + " reserved, " + SM.bytes(root.capacity.availableBytes) + " available of "
          + SM.bytes(root.capacity.totalBytes) + (root.capacity.readOnly ? ", read only" : "") : "Filesystem capacity unavailable"
        Rectangle { anchors.fill: parent; radius: height / 2; color: Util.alpha(root.foreground, 0.1) }
        Rectangle {
          // One rounded shape for used + reserved, then reserved painted over its tail.
          visible: capacityStrip.parts.length > 0
          height: parent.height; radius: height / 2
          width: visible ? parent.width * (capacityStrip.parts[0] + capacityStrip.parts[1]) / Math.max(1, root.capacity.totalBytes) : 0
          color: Util.alpha(root.foreground, 0.32)
          Rectangle {
            height: parent.height; radius: height / 2
            width: capacityStrip.parts.length ? capacityStrip.width * capacityStrip.parts[0] / Math.max(1, root.capacity.totalBytes) : 0
            color: root.accentTone
          }
        }
      }
      Flow {
        width: parent.width; spacing: Style.space(10)
        Repeater {
          model: root.capacity && root.capacity.totalBytes !== undefined ? [
            { swatch: root.accentTone, text: SM.bytes(root.capacity.usedBytes) + " used" },
            { swatch: Util.alpha(root.foreground, 0.32), text: SM.bytes(root.capacity.reservedBytes) + " reserved" },
            { swatch: Util.alpha(root.foreground, 0.1), text: SM.bytes(root.capacity.availableBytes) + " available" }] : []
          Row {
            required property var modelData
            spacing: Style.space(5)
            Rectangle {
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(8); height: width; radius: Style.space(2)
              color: modelData.swatch
              border.width: 1; border.color: Util.alpha(root.foreground, 0.25)
            }
            StorageText { text: modelData.text; font.pixelSize: Style.font.caption }
          }
        }
        StorageCaption { visible: !root.capacity || root.capacity.totalBytes === undefined; text: "Filesystem capacity unavailable" }
      }

      // --- scoped allocation (scanned on request) --------------------------
      PanelSeparator { foreground: root.foreground }
      StorageHeader { text: "ALLOCATION" }
      Flow {
        width: parent.width; spacing: Style.space(4)
        visible: root.crumbs.length > 0
        Repeater {
          model: root.crumbs
          StorageButton { required property var modelData; text: modelData.name + " ›"; fontSize: Style.font.caption; width: Math.min(implicitWidth, content.width); onClicked: root.navigate(modelData.nodeId) }
        }
      }
      // Known allocation first, then how old and how complete it is.
      Row {
        width: parent.width
        spacing: Style.space(8)
        visible: !!root.listing
        StorageText {
          anchors.baseline: allocationNote.baseline
          text: root.listing ? (root.listing.node.coverage === "partial" ? "≥ " : "") + SM.bytes(root.listing.node.knownAllocatedBytes) : ""
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }
        StorageCaption {
          id: allocationNote
          width: parent.width - x
          text: root.listing ? "known allocation · " + SM.age(root.snapshot, root.now) + " · folder itself " + SM.bytes(root.listing.node.ownAllocatedBytes)
            + (root.listing.node.coverage === "partial" ? " · lower bound, unreadable/excluded entries" : "")
            + (root.listing.activationDurability === "pending" ? " · activation durability pending" : "") : ""
        }
      }
      Flow {
        width: parent.width; spacing: Style.space(6)
        StorageButton { text: root.snapshot ? "Rescan" : "Scan"; primary: true; enabled: root.entriesReady; onClicked: root.scan() }
        StorageButton { text: "Cancel"; enabled: root.scanning && !!root.scanId && !root.reconciling; onClicked: root.cancel() }
        StorageButton { text: "Reload cache"; enabled: root.helperAvailable && !root.scanning && !root.reconciling; onClicked: root.reloadCache() }
        StorageButton { text: "Up"; enabled: root.entriesReady && !!root.listing && !!root.listing.node.parentId; onClicked: root.up() }
      }
      // State badge, activity and the backend's own status words.
      Row {
        width: parent.width
        spacing: Style.space(6)
        Rectangle {
          id: phaseBadge
          anchors.verticalCenter: parent.verticalCenter
          width: phaseText.implicitWidth + Style.space(10); height: phaseText.implicitHeight + Style.space(4)
          radius: Style.cornerRadius
          color: "transparent"
          border.width: 1; border.color: Util.alpha(root.foreground, 0.3)
          StorageText { id: phaseText; anchors.centerIn: parent; text: root.phase.toUpperCase(); font.pixelSize: Style.font.caption; font.bold: true; font.letterSpacing: 0.8; color: root.dim }
        }
        BusyIndicator {
          id: busy
          anchors.verticalCenter: parent.verticalCenter
          running: root.scanning || root.reconciling || root.queryPending || root.phase === "discovering"
          visible: running
          width: visible ? Style.space(18) : 0; height: Style.space(18)
          Accessible.name: root.scanning ? "Storage scan pending; total unknown" : "Storage work pending"
        }
        StorageCaption {
          anchors.verticalCenter: parent.verticalCenter
          width: parent.width - phaseBadge.width - parent.spacing - (busy.visible ? busy.width + parent.spacing : 0)
          color: root.foreground
          text: root.queryPending ? "Loading entries · previous rows unavailable for interaction" : root.status
        }
      }
      StorageCaption { width: parent.width; visible: !!root.error; color: root.ink.warn; text: root.error }
      Item {
        width: parent.width; height: visible ? root.bandRows * Style.space(28) : 0
        visible: !!root.listing && !!root.owner && root.owner.storageShowMap && !root.filter && root.bandRows > 0
        Repeater {
          model: root.geometry
          Rectangle {
            id: band
            required property var modelData
            required property int index
            readonly property color tone: root.bandColor(modelData, root.bandIndex[index] || 0)
            readonly property bool selectedBand: root.selectedId === modelData.nodeId && modelData.row.kind !== "other"
            x: modelData.x; y: modelData.y
            // A one-pixel seam between neighbours; the width is otherwise exact.
            width: Math.max(1, modelData.width - (modelData.width > 3 ? 1 : 0)); height: modelData.height
            radius: Math.min(Style.cornerRadius, width / 2, height / 2)
            color: tone
            border.width: selectedBand ? Math.max(2, Style.normalBorderWidth * 2) : 0
            border.color: root.foreground
            clip: true
            StorageText {
              anchors.fill: parent; anchors.leftMargin: Style.space(5); anchors.rightMargin: Style.space(3)
              visible: band.width > Style.space(28)
              verticalAlignment: Text.AlignVCenter
              text: modelData.name; elide: Text.ElideRight
              font.pixelSize: Style.font.caption
              color: root.bandInk(band.tone)
            }
            MouseArea {
              anchors.fill: parent
              enabled: root.entriesReady
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                root.panel.focusRegion("ledger")
                if (modelData.row.kind === "other") {
                  root.expandOther(modelData)
                } else root.selectMap(modelData)
              }
            }
          }
        }
      }
      StorageField {
        id: filterInput; maximumLength: 128; objectName: "storageFilterInput"; width: parent.width
        placeholderText: "Filter this folder (literal name substring)"
        leftPadding: Style.space(8) + filterIcon.width + Style.space(6)
        Accessible.name: "Filter current directory"
        RamanIcon { id: filterIcon; x: Style.space(8); anchors.verticalCenter: parent.verticalCenter; name: "search"; size: Style.font.bodySmall; color: root.dim }
        onAccepted: {
          if (root.scanning || root.reconciling || !root.listing) return
          root.applyFilter(text); focus = false
        }
        Keys.priority: Keys.AfterItem
        Keys.onEscapePressed: function(event) { focus = false; event.accepted = true }
      }
      // Ledger column headers: names, then right-aligned numbers.
      Item {
        id: ledgerHeader
        width: parent.width; height: nameHeader.implicitHeight
        readonly property real shareWidth: Style.space(44)
        readonly property real sizeWidth: Style.space(92)
        StorageHeader { id: nameHeader; x: Style.space(8); text: "NAME (HIDDEN INCLUDED)" }
        StorageHeader { anchors.right: parent.right; anchors.rightMargin: Style.space(8) + ledgerHeader.shareWidth; width: ledgerHeader.sizeWidth; horizontalAlignment: Text.AlignRight; text: "ALLOCATED" }
        StorageHeader { anchors.right: parent.right; anchors.rightMargin: Style.space(8); width: ledgerHeader.shareWidth; horizontalAlignment: Text.AlignRight; text: "SHARE" }
      }
      ListView {
        id: ledger; objectName: "storageLedger"; width: parent.width; height: Math.min(Style.space(240), contentHeight); clip: true
        spacing: Style.space(1)
        model: root.ledgerRows
        delegate: ItemDelegate {
          id: entryControl
          enabled: root.entriesReady
          required property var modelData; required property int index
          width: ledger.width
          leftPadding: Style.space(8); rightPadding: Style.space(8); topPadding: Style.space(4); bottomPadding: Style.space(4)
          contentItem: Item {
            implicitHeight: entryName.implicitHeight
            StorageText {
              id: entryName
              width: Math.min(implicitWidth, parent.width - ledgerHeader.sizeWidth - ledgerHeader.shareWidth - hiddenTag.width - Style.space(4))
              text: entryControl.modelData.name + (entryControl.modelData.kind === "directory" ? "/" : "")
              elide: Text.ElideRight
            }
            StorageText {
              id: hiddenTag
              anchors.left: entryName.right; anchors.leftMargin: Style.space(5); anchors.baseline: entryName.baseline
              visible: !!entryControl.modelData.hidden
              width: visible ? implicitWidth : 0
              text: "hidden"; font.pixelSize: Style.font.caption; color: root.dim
            }
            StorageText {
              anchors.right: parent.right; anchors.rightMargin: ledgerHeader.shareWidth
              width: ledgerHeader.sizeWidth; horizontalAlignment: Text.AlignRight; elide: Text.ElideLeft
              text: (entryControl.modelData.coverage === "partial" ? "≥ " : "") + SM.bytes(entryControl.modelData.knownAllocatedBytes)
            }
            StorageText {
              anchors.right: parent.right
              width: ledgerHeader.shareWidth; horizontalAlignment: Text.AlignRight
              color: root.dim
              text: (root.listing.node.knownAllocatedBytes ? Math.round(100 * entryControl.modelData.knownAllocatedBytes / root.listing.node.knownAllocatedBytes) : 0) + "%"
            }
          }
          background: Rectangle {
            radius: Style.cornerRadius
            color: entryControl.highlighted ? Util.alpha(root.foreground, 0.12) : entryControl.hovered ? Util.alpha(root.foreground, 0.06) : "transparent"
            border.width: entryControl.highlighted ? Math.max(1, Style.normalBorderWidth) : 0
            border.color: Util.alpha(root.foreground, root.panel && root.panel.nav && root.panel.nav.region === "ledger" ? 0.9 : 0.4)
          }
          highlighted: root.selectedId === modelData.nodeId
          text: (modelData.hidden ? "[hidden] " : "") + modelData.name + " · " + modelData.kind + " · " + (modelData.coverage === "partial" ? "≥ " : "") + SM.bytes(modelData.knownAllocatedBytes) + " · " + (root.listing.node.knownAllocatedBytes ? Math.round(100 * modelData.knownAllocatedBytes / root.listing.node.knownAllocatedBytes) : 0) + "%" + (modelData.sharedLink ? " · shared link" : "") + (modelData.reason ? " · " + modelData.reason : "")
          Accessible.name: text
          onClicked: { root.selectRow(modelData.nodeId); root.panel.focusRegion("ledger") }
          onDoubleClicked: root.descend()
        }
      }
      StorageCaption {
        width: parent.width
        text: root.selected ? root.selected.name + " · " + root.selected.kind + " · own allocation " + SM.bytes(root.selected.ownAllocatedBytes) + " · apparent entry size " + SM.bytes(root.selected.apparentBytes) + " · " + root.selected.coverage + (root.selected.reason ? " · " + root.selected.reason : "") : "Select an entry to inspect it"
      }
      StorageCaption { visible: !!root.listing && root.listing.total === 0; text: "No matching entries; folder self allocation remains above." }
      Flow {
        width: parent.width; spacing: Style.space(6)
        StorageButton { text: "Previous rows"; iconName: "back"; enabled: !root.memberView && root.entriesReady && root.pageOffsets.length > 0; onClicked: { var offsets = root.pageOffsets.slice(); var offset = offsets.pop(); root.pageOffsets = offsets; root.page(offset) } }
        StorageButton { text: "Next rows"; iconName: "next"; enabled: !root.memberView && root.entriesReady && !!root.listing && root.listing.nextOffset !== null; onClicked: { root.pageOffsets = root.pageOffsets.concat([root.listing.offset]); root.page(root.listing.nextOffset) } }
        StorageButton { text: "Previous members"; iconName: "back"; visible: root.memberView; enabled: root.entriesReady && root.previousMembers; onClicked: root.previousMemberPage() }
        StorageButton { text: "Next members"; iconName: "next"; visible: root.memberView; enabled: root.entriesReady && root.nextMembers; onClicked: root.nextMemberPage() }
        StorageButton { text: "Folder itself"; enabled: root.entriesReady && !!root.listing; onClicked: root.selectSelf(root.listing) }
        StorageButton { text: "All entries"; visible: root.memberView || root.showingSelf; enabled: root.entriesReady; onClicked: root.allEntries() }
        StorageButton { text: "Tiny entries"; enabled: root.entriesReady && !!root.listing; onClicked: { root.showTiny() } }
        StorageButton { text: "Other members"; enabled: root.entriesReady && !!root.listing && !!root.listing.other; onClicked: { root.pageOffsets = root.pageOffsets.concat([root.listing.offset]); root.page(root.listing.other.offset) } }
      }
      Flow {
        width: parent.width; spacing: Style.space(6)
        StorageButton { text: "Descend"; enabled: root.entriesReady && !!root.selected && root.selected.kind === "directory"; onClicked: root.descend() }
        StorageButton { text: "Open directory / parent"; enabled: root.entriesReady && !!root.selected && root.selected.actionIdentity !== false; onClicked: root.action("open") }
        StorageButton { text: "Copy path"; enabled: root.entriesReady && !!root.selected && root.selected.actionIdentity !== false && root.selected.copyRepresentable !== false; onClicked: root.action("copy") }
      }
      StorageCaption { width: parent.width; visible: !!root.selected && (root.selected.actionIdentity === false || root.selected.copyRepresentable === false); color: root.ink.warn; text: root.selected && root.selected.actionIdentity === false ? "Legacy snapshot: rescan to enable validated path actions." : "This path contains non-UTF8 bytes; clipboard text cannot represent it. Browse and open-parent still use byte identity." }
      StorageCaption { width: parent.width; text: "Allocation is scoped metadata, separate from filesystem capacity. It does not predict space freed. No file is executed." }
    }
  }
}
