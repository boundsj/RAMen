import QtQuick
import QtQuick.Window
import QtTest
import "../.."

TestCase {
  id: tc
  name: "ProgressiveStorage"
  when: windowShown
  Window { id: window; width: 420; height: 900; visible: true }
  Component { id: runtimeComponent; RamanRuntime { ipcEnabled: false } }
  Component { id: viewComponent; StorageView { width: 400; maxHeight: 750 } }
  Component {
    id: panelComponent
    QtObject {
      property var runtime: null
      property var storageSession: null
      property var storageOwner: null
      property color foreground: "#dedede"
      function focusRegion(region) {}
      function close() {}
    }
  }
  function setup() {
    var rt=createTemporaryObject(runtimeComponent,tc)
    var panel=createTemporaryObject(panelComponent,tc,{runtime:rt})
    var view=createTemporaryObject(viewComponent,window.contentItem,{panel:panel})
    return {rt:rt,panel:panel,view:view,job:rt.storageJob}
  }
  function answer(job, purpose, fields) {
    var id=""
    for (var key in job.session.pending) if(job.session.pending[key].purpose===purpose) id=key
    verify(!!id,purpose+" pending")
    job.accept(Object.assign({requestId:id,clientId:job.session.clientId,generation:job.session.generation},fields))
  }
  function initial(job) {
    if(job.inFlight) answer(job,"restore",{type:"storage-result",status:"unscanned",snapshot:null})
  }
  function snapshot(status) {
    return {job:true,snapshotId:"a".repeat(32),rootNodeId:"0".repeat(31)+"1",status:status,coverage:"partial",entries:12,pendingFolders:3,pauseReason:"",knownAllocatedBytes:100,roots:[],capturedAt:1,updatedAt:1}
  }
  function test_closing_popup_does_not_cancel_explicit_job() {
    var s=setup(); initial(s.job)
    verify(s.job.start({path:"/fixture"}))
    answer(s.job,"start",{type:"storage-result",snapshot:snapshot("paused")})
    tryVerify(function(){return s.job.inFlight})
    s.view.destroy(); wait(0)
    verify(s.job.running); verify(s.job.inFlight)
    answer(s.job,"step",{type:"storage-result",snapshot:snapshot("scanning")})
    tryVerify(function(){return s.job.inFlight}); verify(s.job.running)
    s.job.pause(); verify(!s.job.running)
    answer(s.job,"step",{type:"storage-result",snapshot:snapshot("scanning")})
    verify(s.job.inFlight)
    answer(s.job,"pause",{type:"storage-result",snapshot:snapshot("paused")})
    verify(!s.job.inFlight); compare(s.job.snapshot.status,"paused")
  }
  function test_restoring_checkpoint_never_autostarts_scan() {
    var s=setup()
    if(!s.job.inFlight) s.job.restore()
    answer(s.job,"restore",{type:"storage-result",snapshot:snapshot("scanning")})
    wait(0); verify(!s.job.running); verify(!s.job.inFlight)
    verify(s.job.resume(s.job.snapshot)); verify(s.job.inFlight)
    answer(s.job,"step",{type:"storage-result",snapshot:snapshot("ready")})
    verify(!s.job.running); verify(!s.job.inFlight)
  }
  function test_error_stops_continuation_but_keeps_inventory() {
    var s=setup(); initial(s.job)
    verify(s.job.start({path:"/fixture"}))
    answer(s.job,"start",{type:"storage-result",snapshot:snapshot("paused")})
    tryVerify(function(){return s.job.inFlight})
    answer(s.job,"step",{type:"error",code:"node-changed",message:"Changed folder"})
    verify(!s.job.running); verify(!s.job.inFlight)
    compare(s.job.snapshot.entries,12); verify(s.job.error.indexOf("node-changed")>=0)
  }
  function test_new_scan_is_blocked_until_pause_checkpoint_finishes() {
    var s=setup(); initial(s.job)
    verify(s.job.start({path:"/fixture"}))
    verify(!s.job.start({path:"/other"}))
    answer(s.job,"start",{type:"storage-result",snapshot:snapshot("paused")})
    tryVerify(function(){return s.job.inFlight})
    s.job.pause(); verify(!s.job.start({path:"/other"}))
    answer(s.job,"step",{type:"storage-result",snapshot:snapshot("scanning")})
    answer(s.job,"pause",{type:"storage-result",snapshot:snapshot("paused")})
    verify(s.job.start({path:"/other"}))
  }
  function test_final_checkpoint_is_refreshed_after_older_page_query() {
    var s=setup(); initial(s.job)
    var first=snapshot("scanning"), ready=Object.assign({},first,{status:"ready",updatedAt:2,entries:25,pendingFolders:0})
    s.view.showJob(first)
    verify(s.view.queryPending)
    s.view.showJob(ready)
    verify(s.view.refreshQueued)
    var id=""
    for(var key in s.view.session.pending) if(s.view.session.pending[key].purpose==="ledger") id=key
    var node={nodeId:first.rootNodeId,parentId:null,name:"fixture",kind:"directory",coverage:"partial",knownAllocatedBytes:100,ownAllocatedBytes:0}
    var reply={type:"storage-children",clientId:s.view.session.clientId,generation:s.view.session.generation,requestId:id,
      snapshot:first,snapshotId:first.snapshotId,nodeId:node.nodeId,node:node,offset:0,filter:"",total:0,nextOffset:null,rows:[],segments:[],breadcrumbs:[]}
    s.view.accept(reply)
    compare(s.view.snapshot.updatedAt,2)
    tryVerify(function(){return s.view.queryPending})
    for(var next in s.view.session.pending) if(s.view.session.pending[next].purpose==="ledger") id=next
    s.view.accept(Object.assign({},reply,{requestId:id,snapshot:ready}))
    compare(s.view.snapshot.entries,25)
    tryVerify(function(){return !s.view.queryPending}); verify(!s.view.refreshQueued)
  }
}
