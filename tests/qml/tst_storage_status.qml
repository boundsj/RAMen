import QtQuick
import QtQuick.Window
import QtTest
import "../.."

TestCase {
  id: tc
  name: "StorageProgressStatus"
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
  function pending(object, purpose) {
    for (var key in object.session.pending) if (object.session.pending[key].purpose === purpose) return key
    return ""
  }
  function response(object, purpose, fields) {
    var key = pending(object, purpose)
    verify(!!key, purpose + " pending")
    return Object.assign({requestId:key,clientId:object.session.clientId,generation:object.session.generation},fields)
  }
  function reply(object, purpose, fields) { object.accept(response(object,purpose,fields)) }
  function setup() {
    var rt=createTemporaryObject(runtimeComponent,tc)
    var panel=createTemporaryObject(panelComponent,tc,{runtime:rt})
    var view=createTemporaryObject(viewComponent,window.contentItem,{panel:panel})
    wait(0)
    if(rt.storageJob.inFlight) reply(rt.storageJob,"restore",{type:"storage-result",snapshot:null,status:"unscanned"})
    return {rt:rt,panel:panel,view:view,job:rt.storageJob}
  }
  function snap(letter, status, time, path) {
    return {job:true,snapshotId:letter.repeat(32),rootNodeId:"0".repeat(31)+"1",status:status,
      scope:path,kind:"folder",coverage:status==="ready" ? "complete" : "partial",entries:12,pendingFolders:status==="ready" ? 0 : 3,
      pauseReason:"",knownAllocatedBytes:100,roots:[],capturedAt:1,updatedAt:time}
  }
  function node(number, name, parent, bytes) {
    return {nodeId:("0".repeat(32)+number.toString(16)).slice(-32),parentId:parent,name:name,kind:"directory",coverage:"partial",
      knownAllocatedBytes:bytes,ownAllocatedBytes:0,pending:true,actionIdentity:true,copyRepresentable:true}
  }
  function listing(snapshot, folder, rows) {
    return {type:"storage-children",snapshot:snapshot,snapshotId:snapshot.snapshotId,nodeId:folder.nodeId,node:folder,
      offset:0,filter:"",total:rows.length,nextOffset:null,rows:rows,segments:rows,breadcrumbs:[],other:null}
  }
  function unscanned(v, path) {
    v.changeScope({path:path})
    reply(v,"jobcache",{type:"storage-result",snapshot:null,status:"unscanned"})
    reply(v,"cached",{type:"storage-result",snapshot:null,status:"unscanned"})
    compare(v.phase,"unscanned")
  }
  function test_ready_refresh_recovery_clears_failure_state() {
    var s=setup(), first=snap("a","scanning",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    s.view.showJob(first)
    s.view.showJob(ready)
    verify(s.view.refreshQueued)
    reply(s.view,"ledger",{type:"error",code:"worker-timeout",message:"Older query timed out"})
    wait(0)
    verify(s.view.queryPending)
    reply(s.view,"ledger",listing(ready,Object.assign({},node(1,"A",null,100),{pending:false,coverage:"complete"}),[]))
    wait(0)
    compare(s.view.snapshot.status,"ready")
    compare(s.view.listing.snapshot.updatedAt,2)
    compare(s.view.phase,"ready","Successful final checkpoint refresh must restore READY after the older query error")
    compare(s.view.error,"")
  }
  function test_successful_continue_clears_prior_job_failure() {
    var s=setup(), paused=snap("a","paused",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    s.view.changeScope({path:"/fixture/A"})
    verify(s.job.start({path:"/fixture/A"}))
    reply(s.job,"start",{type:"storage-result",snapshot:paused})
    wait(0)
    reply(s.view,"ledger",listing(paused,node(1,"A",null,100),[]))
    reply(s.job,"step",{type:"error",code:"worker-timeout",message:"Interrupted slice"})
    reply(s.view,"ledger",listing(paused,node(1,"A",null,100),[]))
    verify(s.view.error.indexOf("worker-timeout")>=0)
    verify(s.job.resume(s.view.snapshot,s.view.scope))
    reply(s.job,"step",{type:"storage-result",snapshot:ready})
    reply(s.view,"ledger",listing(ready,Object.assign({},node(1,"A",null,100),{pending:false,coverage:"complete"}),[]))
    compare(s.view.phase,"ready")
    compare(s.view.error,"","Completed explicit retry must clear the superseded job error")
  }
  function test_cached_main_caption_uses_inventory_roots() {
    var s=setup(), main=Object.assign({},snap("a","ready",1,"Main storage"),{
      kind:"main",scopeBytes:"L2ZpeHR1cmUvaG9tZQ==",scopeNote:"Saved primary filesystem",roots:[{path:"/fixture/system",pathBytes:"L2ZpeHR1cmUvc3lzdGVt"}]})
    s.view.changeScope({pathBytes:main.scopeBytes,kind:"main"})
    reply(s.view,"locations",{type:"storage-locations",note:"Next scan",roots:main.roots.concat([{path:"/fixture/new-root"}])})
    reply(s.view,"jobcache",{type:"storage-result",snapshot:main})
    compare(s.view.mainPlan.roots.length,1)
    compare(s.view.mainPlan.note,"Saved primary filesystem")
    compare(s.view.mainStorage.roots.length,2)
  }
}
