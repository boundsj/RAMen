import QtQuick
import QtQuick.Window
import QtTest
import "../.."

TestCase {
  id: tc
  name: "StorageProgressRecovery"
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
  function test_restore_uses_returned_inventory_scope() {
    var s=setup(), a=snap("a","paused",1,"/fixture/A"), b=snap("b","paused",2,"/fixture/B")
    verify(s.job.start({path:"/fixture/A"})); s.job.pause()
    reply(s.job,"start",{type:"storage-result",snapshot:a})
    verify(s.job.start({path:"/fixture/B"})); s.job.pause()
    reply(s.job,"start",{type:"storage-result",snapshot:b})
    // Continuing an older catalog entry does not reorder the persisted catalog.
    verify(s.job.resume(a)); s.job.pause()
    reply(s.job,"step",{type:"storage-result",snapshot:a})
    s.view.changeScope({path:"/fixture/A"})
    s.rt.storageRestarted()
    s.rt.storageReady(); wait(0)
    reply(s.job,"restore",{type:"storage-result",snapshot:b})
    verify(!s.view.snapshot || s.view.snapshot.scope === "/fixture/A", "Restoring latest B must not put B inventory under the still-selected A scope")
  }
  function test_cached_other_scope_is_ready_while_job_runs() {
    var s=setup(), b=snap("b","ready",2,"/fixture/B")
    s.view.changeScope({path:"/fixture/B"})
    var a=snap("a","paused",1,"/fixture/A")
    verify(s.job.start({path:"/fixture/A"}))
    reply(s.job,"start",{type:"storage-result",snapshot:a}); wait(0)
    reply(s.view,"jobcache",{type:"storage-result",snapshot:b})
    reply(s.view,"ledger",listing(b,node(1,"B",null,100),[]))
    reply(s.job,"step",{type:"storage-result",snapshot:Object.assign({},a,{status:"ready",updatedAt:3})})
    verify(!s.job.running)
    compare(s.view.phase,"ready","B must not remain labelled scanning after A's unrelated background job finishes")
  }
  function test_other_second_retained_page_after_members_leave_top_segments() {
    var s=setup(), first=snap("a","scanning",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    var parent=Object.assign({},node(1,"A",null,1000059),{ownAllocatedBytes:1000000}), old=[]
    for(var i=2;i<61;i++) old.push(node(i,"small-"+i,first.rootNodeId,1))
    s.view.showJob(first)
    reply(s.view,"ledger",Object.assign(listing(first,parent,old.slice(0,50)),{segments:old,total:59,nextOffset:50}))
    var other=s.view.geometry.filter(function(x){return x.row.kind==="other"})[0]
    verify(!!other); s.view.expandOther(other)
    compare(s.view.tinyMembers.length,59)
    s.view.showJob(ready)
    var current=[]
    for(var j=100;j<159;j++) current.push(Object.assign({},node(j,"large-"+j,first.rootNodeId,500),{pending:false,coverage:"complete"}))
    var refreshed=old.slice(0,50).map(function(x){return Object.assign({},x,{knownAllocatedBytes:10,pending:false,coverage:"complete"})})
    reply(s.view,"ledger",Object.assign(listing(ready,parent,current.slice(0,50)),{segments:current,members:refreshed,total:118,nextOffset:50}))
    compare(s.view.ledgerRows.length,50)
    s.view.nextMemberPage()
    verify(s.view.queryPending, "A retained page must request its IDs after reranking")
    var request=s.view.session.pending[pending(s.view,"ledger")]
    compare(request.args.memberIds.length,9)
    var last=old.slice(50).map(function(x){return Object.assign({},x,{knownAllocatedBytes:20,pending:false,coverage:"complete"})})
    reply(s.view,"ledger",Object.assign(listing(ready,parent,current.slice(0,50)),{segments:current,members:last,total:118,nextOffset:50}))
    compare(s.view.ledgerRows.length,9)
    compare(s.view.ledgerRows[0].nodeId,last[0].nodeId)
    compare(s.view.ledgerRows[0].knownAllocatedBytes,20)
    s.view.previousMemberPage()
    verify(s.view.queryPending)
    compare(s.view.session.pending[pending(s.view,"ledger")].args.memberIds.length,50)
  }
  function test_tiny_entries_requests_retained_members_after_reranking() {
    var s=setup(), first=snap("a","scanning",1,"/fixture/A"), ready=snap("a","ready",2,"/fixture/A")
    var parent=node(1,"A",null,1000001), big=node(2,"big",first.rootNodeId,1000000), tiny=node(3,"small",first.rootNodeId,1)
    s.view.showJob(first)
    reply(s.view,"ledger",listing(first,parent,[big,tiny]))
    s.view.showTiny(); compare(s.view.ledgerRows.length,1)
    s.view.showJob(ready)
    var request=s.view.session.pending[pending(s.view,"ledger")]
    compare(request.args.memberIds.length,1)
    compare(request.args.memberIds[0],tiny.nodeId)
    var current=[]
    for(var j=100;j<159;j++) current.push(Object.assign({},node(j,"larger-"+j,first.rootNodeId,100),{pending:false,coverage:"complete"}))
    reply(s.view,"ledger",Object.assign(listing(ready,parent,current.slice(0,50)),{segments:current,members:[Object.assign({},tiny,{pending:false,coverage:"complete",knownAllocatedBytes:25})],total:61,nextOffset:50}))
    compare(s.view.ledgerRows.length,1,"The selected Tiny entries view must retain its member after that ID leaves the leading segments")
    compare(s.view.ledgerRows[0].knownAllocatedBytes,25)
    compare(s.view.ledgerRows[0].pending,false)
  }
  function test_progressive_folder_invalidates_changed_capacity_identity() {
    var s=setup(), a=snap("a","ready",1,"/fixture/A")
    a.identity={device:1,inode:2,uniqueMountId:"9"}
    s.view.changeScope({path:"/fixture/A"})
    reply(s.view,"jobcache",{type:"storage-result",snapshot:a})
    reply(s.view,"ledger",listing(a,node(1,"A",null,100),[]))
    reply(s.view,"capacity",{type:"storage-capacity",scope:"/fixture/A",identity:{device:2,inode:3,uniqueMountId:"10"},totalBytes:1000,usedBytes:300,reservedBytes:0,availableBytes:700})
    compare(s.view.snapshot,null,"Replacing a progressive folder's filesystem must invalidate the old inventory just as legacy folder snapshots do")
  }
  function test_main_discovery_lists_included_roots_instead_of_home_folders() {
    var s=setup()
    s.view.changeScope({path:"/fixture/home",kind:"main"})
    compare(pending(s.view,"browse"),"")
    var roots=[{path:"/",pathBytes:"Lw=="},{path:"/home",pathBytes:"L2hvbWU="}]
    reply(s.view,"locations",{type:"storage-locations",roots:roots,note:"Primary filesystem"})
    compare(s.view.browseFolders.length,2)
    compare(s.view.browseFolders[0].path,"/")
    compare(s.view.browseFolders[1].path,"/home")
  }
}
