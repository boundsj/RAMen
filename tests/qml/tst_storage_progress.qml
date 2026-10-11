import QtQuick
import QtQuick.Window
import QtTest
import "../.."

TestCase {
  id: tc
  name: "StorageProgressRaces"
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
  function test_first_start_error_reaches_view() {
    var s=setup(); unscanned(s.view,"/fixture/new")
    s.view.scan(); verify(s.job.inFlight)
    reply(s.job,"start",{type:"error",code:"worker-failed",message:"Cannot start scan worker"})
    verify(!s.job.running && !s.job.inFlight)
    verify(s.view.error.indexOf("worker-failed") >= 0,"Scan failure must be visible in the view")
  }
  function test_failed_new_scope_does_not_display_previous_job() {
    var s=setup(), a=snap("a","paused",1,"/fixture/A")
    verify(s.job.start({path:"/fixture/A"})); s.job.pause()
    reply(s.job,"start",{type:"storage-result",snapshot:a})
    unscanned(s.view,"/fixture/B")
    s.view.scan(); verify(s.job.inFlight)
    reply(s.job,"start",{type:"error",code:"cache-size-limit",message:"Disk full"})
    compare(s.view.snapshot,null,"Scope B must not show scope A's cached inventory")
  }
  function test_resume_old_job_does_not_replace_other_scope_view() {
    var s=setup(), a=snap("a","paused",1,"/fixture/A"), b=snap("b","paused",2,"/fixture/B")
    s.view.changeScope({path:"/fixture/B"})
    verify(s.job.start({path:"/fixture/B"})); s.job.pause()
    reply(s.job,"start",{type:"storage-result",snapshot:b})
    compare(s.view.snapshot.snapshotId,b.snapshotId)
    reply(s.view,"ledger",listing(b,node(1,"B",null,100),[]))
    var otherPanel=createTemporaryObject(panelComponent,tc,{runtime:s.rt})
    var otherView=createTemporaryObject(viewComponent,window.contentItem,{panel:otherPanel})
    otherView.changeScope({path:"/fixture/A"})
    reply(otherView,"jobcache",{type:"storage-result",snapshot:a})
    reply(otherView,"ledger",listing(a,node(1,"A",null,100),[]))
    verify(s.job.resume(otherView.snapshot))
    reply(s.job,"step",{type:"storage-result",snapshot:Object.assign({},a,{status:"ready",updatedAt:3})})
    compare(s.view.snapshot.snapshotId,b.snapshotId,"Resuming A in another popup must not replace B's inventory")
  }
  function test_replacement_retires_old_ledger_query() {
    var s=setup(), old=snap("a","ready",1,"/fixture/A"), fresh=snap("b","paused",2,"/fixture/A")
    s.view.showJob(old)
    reply(s.view,"ledger",listing(old,node(1,"old-root",null,100),[]))
    var oldFolder=node(7,"old-subfolder",old.rootNodeId,100)
    s.view.navigate(oldFolder.nodeId)
    verify(s.view.queryPending)
    var late=response(s.view,"ledger",listing(old,oldFolder,[]))
    s.view.showJob(fresh)
    s.view.accept(late)
    wait(0)
    var request=s.view.session.pending[pending(s.view,"ledger")]
    compare(request.args.nodeId,fresh.rootNodeId,"New inventory must load its root, not an old inventory's node ID")
  }
  function test_old_jobcache_cannot_replace_new_checkpoint() {
    var s=setup(), old=snap("a","ready",1,"/fixture/A"), fresh=snap("b","ready",2,"/fixture/A")
    s.view.changeScope({path:"/fixture/A"})
    verify(!!pending(s.view,"jobcache"))
    var late=response(s.view,"jobcache",{type:"storage-result",snapshot:old})
    s.view.showJob(fresh)
    s.view.accept(late)
    compare(s.view.snapshot.snapshotId,fresh.snapshotId,"Late discovery may not replace a newer job checkpoint")
  }
  function test_ready_refresh_survives_older_query_error() {
    var s=setup(), first=snap("a","scanning",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    s.view.showJob(first)
    s.view.showJob(ready)
    verify(s.view.refreshQueued)
    reply(s.view,"ledger",{type:"error",code:"worker-timeout",message:"Query worker timed out"})
    wait(0)
    verify(s.view.queryPending,"Queued final checkpoint refresh must run after the older query finishes with an error")
  }
  function test_main_scope_does_not_fall_back_to_legacy_home_snapshot() {
    var s=setup()
    s.view.changeScope({path:"/fixture/home",kind:"main"})
    reply(s.view,"jobcache",{type:"storage-result",snapshot:null,status:"unscanned"})
    compare(pending(s.view,"cached"), "")
    compare(s.view.phase,"unscanned")
    compare(s.view.snapshot,null)
  }
  function test_other_members_refresh_when_progress_finishes() {
    var s=setup(), first=snap("a","scanning",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    var rootNode=node(1,"A",null,1000001), big=node(2,"big",first.rootNodeId,1000000), tiny=node(3,"growing",first.rootNodeId,1)
    s.view.showJob(first)
    reply(s.view,"ledger",listing(first,rootNode,[big,tiny]))
    var segment=s.view.geometry.filter(function(segment){return segment.row.kind==="other"})[0]
    verify(!!segment)
    s.view.expandOther(segment)
    compare(s.view.ledgerRows[0].nodeId,tiny.nodeId)
    s.view.showJob(ready)
    var measured=Object.assign({},tiny,{knownAllocatedBytes:500000,pending:false,coverage:"complete"})
    reply(s.view,"ledger",listing(ready,Object.assign({},rootNode,{knownAllocatedBytes:1500000,coverage:"complete",pending:false}),[big,measured]))
    compare(s.view.ledgerRows[0].knownAllocatedBytes,500000,"Other must show the retained member's updated measured size")
  }
}
