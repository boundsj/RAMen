import QtQuick
import QtTest
import qs.Commons
import "../.."

TestCase {
  id: tc
  name: "PanelNavigation"
  when: windowShown
  width: 900; height: 900
  visible: true

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
  function apps(rt) {
    probeOf(rt).feed(JSON.stringify({ type: "apps", apps: [
      { id: "a1", name: "Chrome", kind: "app", host: "", count: 9, pss: 900000, swap: 0, protected: false },
      { id: "a2", name: "Slack", kind: "app", host: "", count: 4, pss: 400000, swap: 0, protected: false }] }))
  }
  function setup() {
    var svc = createTemporaryObject(serviceComponent, null)
    var w = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(svc) })
    tryVerify(function() { return w.runtime === svc.runtime && panelOf(w) && panelOf(w).hostWidget === w })
    probeOf(svc.runtime).feed(JSON.stringify({ type: "summary", total: 8000000, used: 4000000, available: 4000000, swapTotal: 0, swapUsed: 0, zramRam: 0, psiSome10: 0.5 }))
    return { svc: svc, rt: svc.runtime, w: w, panel: panelOf(w) }
  }
  function openPanel(s) {
    s.w.open()
    tryVerify(function() { return s.panel.opened })
    wait(50) // focus is forced on the next tick
  }

  function test_memory_kill_still_two_step() {
    var s = setup()
    openPanel(s)
    compare(s.rt.detail, true, "Memory page subscribes to app detail")
    apps(s.rt)
    keyClick(Qt.Key_J)            // activate cursor
    keyClick(Qt.Key_X)            // arm
    compare(kills(s.rt).length, 0, "first x only arms")
    compare(s.panel.armedId, "a1")
    keyClick(Qt.Key_X)            // confirm
    compare(kills(s.rt), ["kill TERM a1"])
  }

  function test_memory_rows_never_turn_missing_memory_into_zero() {
    var s = setup()
    openPanel(s)
    probeOf(s.rt).feed(JSON.stringify({ type: "apps", snapshot: 3, apps: [
      { id: "p1", name: "Shell", kind: "app", host: "", count: 3, pss: 500000, swap: 0, protected: false,
        memoryStatus: "partial", memoryCoverage: { measured: 2, members: 3 } },
      { id: "p2", name: "Legacy", kind: "app", host: "", count: 1, pss: 200000, swap: null, protected: false }] }))
    compare(s.panel.apps.length, 2)
    compare(s.panel.maxTotal, 500000, "partial subtotal ranks by its known lower bound")
    verify(!isNaN(s.panel.maxTotal))
  }

  function test_storage_cached_open_input_keys_and_leave() {
    var s = setup()
    // The test KeyboardPanel is an Item, not a separately visible window.
    s.panel.parent.visible = true
    openPanel(s)
    s.panel.selectPage("storage")
    tryVerify(function() { return !!s.panel.storageView })
    var view = s.panel.storageView
    var commands = written(s.rt).filter(function(l) { return l.indexOf("{") === 0 }).map(function(l) { return JSON.parse(l) })
    verify(commands.some(function(c) { return c.command === "storage.cached" }))
    verify(!commands.some(function(c) { return c.command === "storage.scan" }))
    for (var key of ["delete", "force", "enter", "cancel", "open", "copy"]) s.panel.handleKey(key)
    compare(kills(s.rt).length, 0)
    var field = findChild(view, "storagePathInput")
    field.forceActiveFocus()
    verify(field.activeFocus, "field has focus")
    verify(view.inputOwned, "Storage recognizes editor focus")
    for (var code of [Qt.Key_J, Qt.Key_K, Qt.Key_H, Qt.Key_L, Qt.Key_R, Qt.Key_C, Qt.Key_O, Qt.Key_Y]) keyClick(code)
    compare(field.text, "jkhlrcoy")
    var client = view.session.clientId
    var before = written(s.rt).length
    s.panel.handleKey("refresh")
    compare(written(s.rt).length, before)
    s.panel.selectPage("memory")
    wait(20)
    commands = written(s.rt).filter(function(l) { return l.indexOf("{") === 0 }).map(function(l) { return JSON.parse(l) })
    verify(commands.some(function(c) { return c.command === "storage.leave" }))
    s.panel.selectPage("storage")
    tryVerify(function() { return !!s.panel.storageView })
    compare(s.panel.storageSession.clientId, client)
    s.panel.close()
  }

  function test_storage_ledger_returns_to_page_navigation() {
    var s = setup()
    s.panel.parent = tc
    openPanel(s)
    s.panel.selectPage("storage")
    tryVerify(function() { return !!s.panel.storageView })
    s.panel.handleKey("down")
    compare(s.panel.nav.region, "ledger")
    s.panel.handleKey("up")
    compare(s.panel.nav.region, "nav")
    s.panel.handleKey("left")
    compare(s.panel.page, "history")
    compare(kills(s.rt).length, 0)
  }
  function storageReply(view,purpose,fields) {
    var id=""
    for(var key in view.session.pending) if(view.session.pending[key].purpose===purpose) id=key
    verify(!!id,purpose+" outstanding")
    view.accept(Object.assign({requestId:id,clientId:view.session.clientId,generation:view.session.generation},fields))
  }
  function test_storage_clicked_ledger_arrow_navigation_data() {
    return [{tag:"arrows",up:Qt.Key_Up,left:Qt.Key_Left},{tag:"letters",up:Qt.Key_K,left:Qt.Key_H},
            {tag:"button-then-arrows",button:"Folder itself",up:Qt.Key_Up,left:Qt.Key_Left},
            {tag:"path-editor",editor:"storagePathInput"},
            {tag:"button-then-filter-editor",button:"Folder itself",editor:"storageFilterInput"}]
  }
  function storageButton(item,text) {
    if(item.text===text) return item
    for(var child of item.children || []) { var found=storageButton(child,text); if(found) return found }
    return null
  }
  function test_storage_clicked_ledger_arrow_navigation(data) {
    var s=setup(); s.panel.parent=tc; openPanel(s); s.panel.selectPage("storage")
    tryVerify(function(){return !!s.panel.storageView})
    var v=s.panel.storageView
    storageReply(v,"cached",{type:"storage-result",status:"ready",snapshot:{snapshotId:"synthetic",rootNodeId:"root",coverage:"complete",capturedAt:1}})
    var row={nodeId:"folder",name:"Synthetic folder",kind:"directory",coverage:"complete",knownAllocatedBytes:4096,ownAllocatedBytes:4096}
    storageReply(v,"ledger",{type:"storage-children",snapshotId:"synthetic",nodeId:"root",offset:0,filter:"",total:1,nextOffset:null,
      node:{nodeId:"root",name:"Synthetic scope",kind:"directory",parentId:null,coverage:"complete",knownAllocatedBytes:4096,ownAllocatedBytes:0},rows:[row],segments:[row],other:null})
    var ledger=findChild(v,"storageLedger")
    tryVerify(function(){return !!ledger.itemAtIndex(0)})
    var delegate=ledger.itemAtIndex(0)
    // Native Qt takes click focus here; request the same policy explicitly
    // so the offscreen platform does not leave the ancestor focused instead.
    delegate.focusPolicy=Qt.StrongFocus
    wait(50)
    mouseClick(delegate,delegate.width/2,delegate.height/2)
    compare(s.panel.nav.region,"ledger"); compare(v.selectedId,"folder")
    if(data.button) {
      var button=storageButton(v,data.button); verify(!!button); verify(button.enabled)
      button.focusPolicy=Qt.StrongFocus
      mouseClick(button,button.width/2,button.height/2)
      if(!data.editor) wait(20)
    }
    if(data.editor) {
      var field=findChild(v,data.editor)
      mouseClick(field,field.width/2,field.height/2)
      verify(field.activeFocus); field.text="synthetic"; field.cursorPosition=5
      wait(20) // Pending button-focus restoration must respect the editor.
      verify(field.activeFocus); verify(v.inputOwned)
      var before=written(s.rt).length
      keyClick(Qt.Key_Up); keyClick(Qt.Key_Left)
      compare(s.panel.nav.region,"ledger"); compare(field.cursorPosition,4)
      keyClick(Qt.Key_R)
      compare(field.text,"syntrhetic"); compare(written(s.rt).length,before)
      compare(kills(s.rt).length,0)
      return
    }
    keyClick(data.up); compare(s.panel.nav.region,"nav")
    keyClick(data.left); compare(s.panel.page,"history")
    compare(kills(s.rt).length,0)
  }

  function test_history_never_signals() {
    var s = setup()
    openPanel(s)
    apps(s.rt)
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_X)            // arm Chrome on Memory
    compare(s.panel.armedId, "a1")
    keyClick(Qt.Key_K)            // to page buttons (disarms)
    compare(s.panel.nav.region, "nav")
    compare(s.panel.armedId, "")
    keyClick(Qt.Key_L)            // History
    compare(s.panel.page, "history")
    compare(s.rt.detail, false, "History does not keep the app scan running")
    var keys = [Qt.Key_X, Qt.Key_F, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Space, Qt.Key_J, Qt.Key_X, Qt.Key_X,
                Qt.Key_J, Qt.Key_F, Qt.Key_F, Qt.Key_Return, Qt.Key_Return, Qt.Key_K, Qt.Key_X, Qt.Key_X]
    for (var i = 0; i < keys.length; i++) keyClick(keys[i])
    compare(kills(s.rt).length, 0, "no key on History reaches the kill command")
    compare(s.panel.armedId, "")
    // Mouse page switch back to Memory: the earlier arming does not survive.
    s.panel.selectPage("memory")
    compare(s.panel.armedId, "")
    compare(s.rt.detail, true)
  }

  function test_page_keys_only_from_page_buttons() {
    var s = setup()
    openPanel(s)
    apps(s.rt)
    keyClick(Qt.Key_J)
    keyClick(Qt.Key_L)
    keyClick(Qt.Key_H)
    compare(s.panel.page, "memory", "h/l in the app list do not switch pages")
    compare(s.panel.selectedIndex, 0)
  }

  function test_close_clears_subscriptions_and_escape_order() {
    var s = setup()
    openPanel(s)
    apps(s.rt)
    keyClick(Qt.Key_J); keyClick(Qt.Key_X)
    keyClick(Qt.Key_Escape)
    compare(s.panel.armedId, "", "Esc disarms first")
    verify(s.panel.opened)
    keyClick(Qt.Key_Escape)
    tryVerify(function() { return !s.panel.opened })
    compare(s.rt.detail, false)
    compare(Object.keys(s.rt.subscriptions).length, 0, "a closed panel holds no subscription")
  }

  function test_two_panels_keep_each_others_scan() {
    var svc = createTemporaryObject(serviceComponent, null)
    var a = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(svc) })
    var b = createTemporaryObject(widgetComponent, tc, { bar: fakeBar(svc) })
    tryVerify(function() { return panelOf(a) && panelOf(b) && panelOf(a).runtime === svc.runtime && panelOf(b).runtime === svc.runtime })
    panelOf(a).open(); panelOf(b).open()
    tryVerify(function() { return panelOf(a).opened && panelOf(b).opened })
    compare(Object.keys(svc.runtime.subscriptions).length, 2)
    panelOf(a).close()
    compare(svc.runtime.detail, true, "closing monitor A's panel keeps monitor B's scan")
    panelOf(b).selectPage("history")
    compare(svc.runtime.detail, false)
    panelOf(b).close()
    compare(Object.keys(svc.runtime.subscriptions).length, 0)
  }

  function test_ipc_page_request_opens_history() {
    var s = setup()
    s.rt.requestedPage = "history"
    openPanel(s)
    compare(s.panel.page, "history")
    compare(s.rt.requestedPage, "", "the request is consumed once")
  }
}
