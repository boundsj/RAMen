import QtQuick
import QtQuick.Window
import QtTest
import qs.Commons
import "../.."

TestCase {
  id: tc
  name: "StoragePage"
  when: windowShown
  width: 800; height: 900
  Window { id: shotWindow; width: 420; height: 900; visible: true; color: Color.popups.background }
  Component { id: runtimeComponent; RamanRuntime { ipcEnabled: false } }
  Component { id: viewComponent; StorageView { width: 400; maxHeight: 750 } }
  Component {
    id: panelComponent
    QtObject {
      property var runtime: null
      property var storageSession: null
      property var storageOwner: null
      property color foreground: "#dedede"
      property bool closed: false
      function focusRegion(region) {}
      function close() { closed = true }
    }
  }
  function setup() {
    var rt = createTemporaryObject(runtimeComponent, tc)
    var panel = createTemporaryObject(panelComponent, tc, { runtime: rt })
    var view = createTemporaryObject(viewComponent, shotWindow.contentItem, { panel: panel })
    return { rt: rt, panel: panel, view: view }
  }
  function pending(view, purpose) {
    for (var id in view.session.pending) if (view.session.pending[id].purpose === purpose) return id
    return ""
  }
  function reply(view, purpose, fields) {
    var id = pending(view, purpose)
    verify(!!id, purpose + " outstanding")
    view.accept(Object.assign({requestId:id,clientId:view.session.clientId,generation:view.session.generation}, fields))
  }
  function probeOf(rt) {
    for (var i=0;i<rt.data.length;i++) if (rt.data[i].stdinEnabled===true) return rt.data[i]
    return null
  }
  function stopHelper(rt) {
    var probe=probeOf(rt)
    probe.running=false; probe.exited(1,0)
    verify(!rt.storageHelperAvailable)
  }
  function test_helper_downtime_inputs_data() {
    return [{tag:"scan",action:"scan"},{tag:"keyboard-r",action:"key"},
            {tag:"reload",action:"reload"},{tag:"reload-then-r",action:"reload-key"}]
  }
  function test_helper_downtime_inputs(data) {
    var s=setup(), v=s.view; seed(v)
    stopHelper(s.rt)
    var probe=probeOf(s.rt), writes=probe.written.length
    if(data.action==="scan") v.scan()
    else if(data.action==="key") v.handle("storageScan")
    else { v.reloadCache(); if(data.action==="reload-key") v.handle("storageScan") }
    compare(probe.written.length,writes); compare(Object.keys(v.session.pending).length,0)
    verify(!v.scanning && !v.reconciling); compare(v.scanId,""); verify(!v.entriesReady)
    verify(!v.send("storage.scan",v.scope,"scan")); compare(Object.keys(v.session.pending).length,0)
    verify(!s.rt.sendCommand({command:"storage.scan"}))
    // Recovery must follow helper startup regardless of the current UI phase.
    v.phase="scanning"
    probe.running=true
    verify(s.rt.storageHelperAvailable); verify(!v.scanning && !v.reconciling)
    tryCompare(v,"phase","discovering"); verify(!!pending(v,"cached")); compare(pending(v,"scan"),"")
    reply(v,"cached",{type:"storage-result",status:"unscanned"})
    compare(v.phase,"unscanned"); verify(v.entriesReady)
  }
  function test_initial_startup_scope_and_runtime_switch() {
    var rt=createTemporaryObject(runtimeComponent,tc)
    stopHelper(rt)
    var panel=createTemporaryObject(panelComponent,tc,{runtime:rt})
    var v=createTemporaryObject(viewComponent,shotWindow.contentItem,{panel:panel})
    compare(Object.keys(v.session.pending).length,0); compare(v.phase,"helper-failed")
    v.changeScope({path:"/synthetic/startup-scope"}); v.handle("storageScan")
    compare(Object.keys(v.session.pending).length,0); verify(!v.scanning)
    probeOf(rt).running=true
    tryVerify(function(){return !!pending(v,"cached")})
    compare(v.session.pending[pending(v,"cached")].args.path,"/synthetic/startup-scope")
    var oldClient=v.session.clientId, other=createTemporaryObject(runtimeComponent,tc)
    stopHelper(other); panel.runtime=other
    compare(v.owner,other); verify(v.session.clientId!==oldClient)
    compare(Object.keys(v.session.pending).length,0); verify(!v.entriesReady)
    v.changeScope({path:"/synthetic/replacement-scope"}); v.scan()
    probeOf(other).running=true
    tryVerify(function(){return !!pending(v,"cached")})
    compare(v.session.pending[pending(v,"cached")].args.path,"/synthetic/replacement-scope")
    compare(pending(v,"scan"),""); verify(!v.scanning)
  }
  function test_failure_states_data() {
    return [
      {tag:"missing",code:"missing-root",phase:"missing",invalid:true},
      {tag:"remounted",code:"root-changed",phase:"changed",invalid:true},
      {tag:"unsupported",code:"identity-unavailable",phase:"unsupported",invalid:true},
      {tag:"entry-limit",code:"scan-entry-limit",phase:"limited",invalid:false},
      {tag:"depth-limit",code:"scan-depth-limit",phase:"limited",invalid:false},
      {tag:"time-limit",code:"scan-time-limit",phase:"limited",invalid:false},
      {tag:"helper",code:"worker-failed",phase:"helper-failed",invalid:false}
    ]
  }
  function test_failure_states(data) {
    var v=setup().view; seed(v); v.scan()
    reply(v,"scan",{type:"storage-progress",status:"scanning",scanId:"owned",entries:11})
    var generation=v.session.generation
    reply(v,"scan",{type:"error",scanId:"owned",code:data.code,message:"Synthetic failure",commitState:"not-committed"})
    compare(v.phase,data.phase); verify(!v.scanning && !v.reconciling)
    compare(v.snapshot===null,data.invalid)
    if(data.invalid) { verify(v.session.generation>generation); compare(v.selected,null) }
    else { compare(v.snapshot.snapshotId,"old"); verify(v.status.indexOf("older")>=0) }
  }
  function test_cancel_without_cache_retains_outcome() {
    var v=setup().view
    reply(v,"cached",{type:"storage-result",status:"unscanned"})
    v.scan(); reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"owned"})
    v.cancel(); reply(v,"cancel",{type:"storage-result",status:"cancelled",scanId:"owned",previousSnapshotRetained:true})
    reply(v,"reconciled",{type:"storage-result",status:"unscanned"})
    compare(v.phase,"cancelled"); verify(v.status.indexOf("cancelled")>=0)
    verify(v.status.indexOf("no cached snapshot")>=0); compare(v.snapshot,null)
  }
  function test_unknown_cancel_without_cache_retains_uncertainty() {
    var v=setup().view
    reply(v,"cached",{type:"storage-result",status:"unscanned"})
    v.scan(); reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"owned"})
    v.cancel(); reply(v,"cancel",{type:"error",code:"unknown-scan",message:"Synthetic completion race"})
    reply(v,"reconciled",{type:"storage-result",status:"unscanned"})
    compare(v.phase,"uncertain"); verify(v.status.indexOf("outcome unknown")>=0)
    verify(v.status.indexOf("no cached snapshot")>=0); compare(v.snapshot,null)
  }
  function test_capacity_refresh_identity_and_transient_failure() {
    var v=setup().view; seed(v)
    v.snapshot=Object.assign({},v.snapshot,{identity:{device:1,inode:2,uniqueMountId:"3"}})
    reply(v,"capacity",{type:"storage-capacity",identity:{inode:2,device:1,uniqueMountId:"3"},totalBytes:100})
    compare(v.snapshot.snapshotId,"old")
    v.scan(); reply(v,"scan",{type:"storage-progress",status:"scanning",scanId:"owned",entries:10})
    v.send("storage.mounts",v.scope,"capacity")
    var scanStatus=v.status
    reply(v,"capacity",{type:"error",code:"worker-timeout",message:"Synthetic timeout"})
    compare(v.status,scanStatus); verify(v.scanning)
    v.send("storage.mounts",v.scope,"capacity")
    var generation=v.session.generation, id=pending(v,"scan")
    reply(v,"capacity",{type:"storage-capacity",identity:{device:1,inode:2,uniqueMountId:"4"},totalBytes:100})
    compare(v.phase,"changed"); compare(v.snapshot,null); compare(v.listing,null)
    verify(!v.scanning); verify(v.session.generation>generation)
    v.accept({type:"storage-result",clientId:v.session.clientId,generation:generation,requestId:id,scanId:"owned",status:"ready",snapshot:{snapshotId:"obsolete"}})
    compare(v.snapshot,null)
  }
  function test_capacity_directory_replaced_by_regular_file() {
    var v=setup().view; seed(v)
    var xhr=new XMLHttpRequest()
    xhr.open("GET",Qt.resolvedUrl("../fixtures/storage-directory-to-file.json"),false)
    xhr.send()
    var failure=JSON.parse(xhr.responseText)
    compare(failure.code,"invalid-path")
    var generation=v.session.generation
    v.action("copy")
    var actionId=pending(v,"action"), action=v.session.pending[actionId].args
    reply(v,"capacity",{type:"error",code:failure.code,message:failure.message})
    compare(v.phase,"invalid-path"); compare(v.snapshot,null); compare(v.listing,null)
    compare(v.ledgerRows.length,0); compare(v.selected,null)
    verify(v.session.generation>generation); compare(Object.keys(v.session.pending).length,0)
    var before=v.session.sequence
    v.action("open"); v.action("copy"); v.descend()
    compare(v.session.sequence,before)
    v.accept({type:"storage-action",requestId:actionId,clientId:v.session.clientId,generation:generation,
      snapshotId:action.snapshotId,nodeId:action.nodeId,action:"copy"})
    compare(v.phase,"invalid-path"); compare(v.snapshot,null)
  }
  function test_volume_list_error_keeps_selected_scope() {
    var v=setup().view; seed(v)
    var generation=v.session.generation
    reply(v,"mounts",{type:"error",code:"invalid-path",message:"Unrelated volume listing failure"})
    compare(v.snapshot.snapshotId,"old"); verify(!!v.listing); verify(!!v.selected)
    compare(v.session.generation,generation); compare(v.phase,"ready")
  }
  function test_partial_and_restart_reload() {
    var s=setup(), v=s.view
    reply(v,"cached",{type:"storage-result",status:"ready",snapshot:{snapshotId:"partial",rootNodeId:"root",coverage:"partial",capturedAt:1}})
    compare(v.phase,"partial"); verify(v.status.indexOf("lower bound")>=0)
    s.rt.storageRestarted(); compare(v.phase,"helper-failed"); compare(v.snapshot,null)
    v.reloadCache(); compare(v.phase,"discovering")
    var lostId=pending(v,"cached"), generation=v.session.generation
    s.rt.storageReady(); compare(v.phase,"discovering")
    tryVerify(function(){return v.session.generation>generation}); verify(pending(v,"cached")!==lostId)
    reply(v,"cached",{type:"storage-result",status:"unscanned"})
    compare(v.phase,"unscanned"); verify(!v.queryPending)
  }
  function test_scope_loss_is_panel_owned() {
    var a=setup()
    var otherPanel=createTemporaryObject(panelComponent,tc,{runtime:a.rt})
    var v=createTemporaryObject(viewComponent,shotWindow.contentItem,{panel:otherPanel})
    verify(v.session.clientId!==a.view.session.clientId)
    seed(v); v.scan(); reply(v,"scan",{type:"storage-progress",status:"scanning",scanId:"other-panel"})
    var generation=v.session.generation, id=pending(v,"scan")
    a.view.invalidateScope("missing-root")
    compare(v.session.generation,generation); compare(pending(v,"scan"),id); verify(v.scanning)
  }
  function test_progress_cancel_reconciliation_and_stale() {
    var s = setup(), v = s.view
    verify(pending(v,"cached") !== "")
    reply(v,"cached",{type:"storage-result",status:"unscanned"})
    compare(v.snapshot,null)
    v.scan()
    reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"scan"})
    verify(v.scanning)
    v.cancel()
    reply(v,"cancel",{type:"storage-progress",status:"cancelling",scanId:"scan"})
    verify(v.reconciling)
    verify(v.scanning)
    reply(v,"cancel",{type:"storage-result",status:"cancelled",scanId:"scan",previousSnapshotRetained:true})
    verify(!v.reconciling)
    verify(!v.scanning)
    verify(pending(v,"reconciled") !== "")
    var generation = v.session.generation
    v.changeScope({path:"/"})
    verify(v.session.generation > generation)
    v.accept({type:"storage-result",clientId:v.session.clientId,generation:generation,requestId:"stale",snapshot:{snapshotId:"stale"}})
    compare(v.snapshot,null)
  }
  function test_completed_before_cancel_data() {
    return [{tag:"durable",durability:"durable"},{tag:"pending",durability:"pending"}]
  }
  function test_completed_before_cancel(data) {
    var v=setup().view; seed(v); v.scan()
    var oldId=pending(v,"scan"), oldGeneration=v.session.generation
    reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"0123456789abcdef0123456789abcdef"})
    v.cancel()
    // Controller.handle() has already removed the completed scan. Its error
    // envelope correlates the command, but intentionally has no scanId.
    reply(v,"cancel",{type:"error",code:"unknown-scan",message:"scan is not active for this client"})
    verify(!v.scanning && !v.reconciling,"command error must settle cancellation")
    verify(!!pending(v,"reconciled"),"must reload authoritative cache")
    var cacheId=pending(v,"reconciled"), cacheGeneration=v.session.generation
    v.applyFilter("older"); v.navigate("older-node"); v.action("copy")
    compare(pending(v,"reconciled"),cacheId); compare(v.session.generation,cacheGeneration)
    compare(pending(v,"action"),""); compare(v.selected,null)
    verify(v.status.indexOf("failed")<0,"unknown scan does not prove a failed scan")
    v.accept({type:"storage-result",requestId:oldId,clientId:v.session.clientId,generation:oldGeneration,scanId:"0123456789abcdef0123456789abcdef",status:"ready",snapshot:{snapshotId:"obsolete"}})
    compare(v.snapshot.snapshotId,"old")
    reply(v,"reconciled",{type:"storage-result",status:"ready",snapshot:{snapshotId:"new",rootNodeId:"root",coverage:"complete",capturedAt:2},activationDurability:data.durability})
    compare(v.snapshot.snapshotId,"new"); compare(v.selected,null)
    reply(v,"ledger",Object.assign(children(v,{nodeId:"root",kind:"directory",knownAllocatedBytes:1,ownAllocatedBytes:0},[{nodeId:"new-file",kind:"file",name:"new file",knownAllocatedBytes:1}]),{activationDurability:data.durability}))
    assertSelection(v,"new-file")
    compare(v.listing.activationDurability,data.durability)
    verify(v.error.indexOf("unknown")>=0)
    verify(!v.queryPending)
    verify(v.status.indexOf("cancelled")<0,"must not claim cancellation won")
  }
  function memberButton(item, direction) {
    if (item.text === direction+" tiny" || item.text === direction+" members") return item
    for (var child of item.children || []) { var found=memberButton(child,direction); if(found) return found }
    return null
  }
  function test_mixed_other_complete_membership_data() {
    return [{tag:"root",nested:false,count:99},{tag:"nested",nested:true,count:99},{tag:"multiple-tail-pages",nested:false,count:159},{tag:"filtered",nested:false,count:99,filter:"small"}]
  }
  function test_mixed_other_complete_membership(data) {
    var v=setup().view; seed(v)
    var node={nodeId:data.nested ? "folder" : "root",kind:"directory",parentId:data.nested ? "root" : null,name:"folder",knownAllocatedBytes:1048576+data.count*4096,ownAllocatedBytes:0}
    var rows=[{nodeId:"big",kind:"file",name:"small big",knownAllocatedBytes:1048576}]
    for(var i=0;i<data.count;i++) rows.push({nodeId:"small-"+i,kind:"file",name:"small "+i,knownAllocatedBytes:4096})
    var source=Object.assign(children(v,node,rows.slice(0,50),0,50),{segments:rows.slice(0,59),other:{offset:59,knownAllocatedBytes:(data.count-58)*4096},total:data.count+1,filter:data.filter || ""})
    if(data.nested) { v.listing=children(v,Object.assign({},v.listing.node,{knownAllocatedBytes:node.knownAllocatedBytes,ownAllocatedBytes:0}),[node]); v.loaded={folder:source} }
    else v.listing=source
    var generation=v.session.generation
    var other=v.geometry.filter(function(s){return s.row.kind==="other" && s.parentId===node.nodeId})[0]
    compare(other.row.members.length,58)
    v.expandOther(other)
    if(data.nested) reply(v,"ledger",source)
    compare(v.ledgerRows.length,50); assertSelection(v,"small-0")
    var seen=v.ledgerRows.map(function(r){return r.nodeId})
    var next=memberButton(v,"Next"); verify(!!next); verify(next.enabled); next.clicked()
    compare(v.ledgerRows.length,8); assertSelection(v,"small-50")
    seen=seen.concat(v.ledgerRows.map(function(r){return r.nodeId}))
    verify(next.enabled,"Other still represents the 41 backend members"); next.clicked()
    var id=pending(v,"ledger"); verify(!!id); compare(v.selected,null)
    var args=v.session.pending[id].args
    compare(args.offset,59); compare(args.nodeId,node.nodeId); compare(args.snapshotId,"old"); compare(args.filter,data.filter || ""); compare(v.session.generation,generation)
    reply(v,"ledger",Object.assign({},source,{offset:59,nextOffset:data.count>108 ? 109 : null,rows:rows.slice(59,109)}))
    compare(v.ledgerRows.length,Math.min(50,data.count-58)); assertSelection(v,"small-58")
    seen=seen.concat(v.ledgerRows.map(function(r){return r.nodeId}))
    var previous=memberButton(v,"Previous")
    if(data.count>108) {
      verify(next.enabled); next.clicked()
      compare(v.session.pending[pending(v,"ledger")].args.offset,109)
      reply(v,"ledger",Object.assign({},source,{offset:109,nextOffset:data.count>158 ? 159 : null,rows:rows.slice(109,159)}))
      assertSelection(v,"small-108"); seen=seen.concat(v.ledgerRows.map(function(r){return r.nodeId}))
      if(data.count>158) {
        next.clicked(); reply(v,"ledger",Object.assign({},source,{offset:159,nextOffset:null,rows:rows.slice(159)}))
        assertSelection(v,"small-158"); seen=seen.concat(v.ledgerRows.map(function(r){return r.nodeId}))
        previous.clicked(); reply(v,"ledger",Object.assign({},source,{offset:109,nextOffset:159,rows:rows.slice(109,159)}))
        assertSelection(v,"small-108")
      }
      previous.clicked(); reply(v,"ledger",Object.assign({},source,{offset:59,nextOffset:109,rows:rows.slice(59,109)}))
      assertSelection(v,"small-58")
    } else verify(!next.enabled)
    compare(seen.length,data.count); compare(new Set(seen).size,data.count)
    verify(previous.enabled); previous.clicked()
    compare(v.ledgerRows.length,8); assertSelection(v,"small-50")
    previous.clicked(); compare(v.ledgerRows.length,50); assertSelection(v,"small-0"); verify(!previous.enabled)
    v.allEntries(); compare(v.listing.offset,0); compare(v.tinyMembers.length,0)
    // A submitted filter must supersede a pending backend member page.
    v.expandOther(other); v.nextMemberPage(); v.nextMemberPage()
    var obsolete=responseFor(v,pending(v,"ledger"),Object.assign({},source,{offset:59,nextOffset:null,rows:rows.slice(59,109)}))
    v.applyFilter("big"); v.accept(obsolete)
    compare(v.selected,null); compare(v.memberSource,null); compare(v.tinyMembers.length,0)
    reply(v,"ledger",children(v,node,[rows[0]]))
    compare(v.listing.filter,"big"); assertSelection(v,"big")
  }
  function test_budget_coalesced_self_remains_real_member() {
    var v=setup().view; seedMixed(v)
    v.expandOther({parentId:"root",row:{members:["root","big","tiny"],offset:null}})
    compare(v.ledgerRows.length,3); assertSelection(v,"root")
    v.handle("storageMove",1); assertSelection(v,"big")
    v.handle("storageMove",1); assertSelection(v,"tiny")
  }
  function test_render_matrix_selection_and_zero_members() {
    var s=setup(), v=s.view
    var meta={snapshotId:"snapshot",rootNodeId:"root",coverage:"partial",capturedAt:Date.now()/1000}
    reply(v,"capacity",{type:"storage-capacity",totalBytes:20000,usedBytes:10000,reservedBytes:2000,availableBytes:8000,scope:"Synthetic fixture"})
    reply(v,"cached",{type:"storage-result",status:"ready",snapshot:meta})
    var rows=[{nodeId:"folder",parentId:"root",name:"Projects",kind:"directory",ownAllocatedBytes:1000,apparentBytes:1000,knownAllocatedBytes:6000,coverage:"complete",actionIdentity:true,copyRepresentable:true},
      {nodeId:"file",parentId:"root",name:"odd \\x0a file",kind:"file",ownAllocatedBytes:1000,apparentBytes:3000,knownAllocatedBytes:1000,coverage:"complete",actionIdentity:true,copyRepresentable:true},
      {nodeId:"zero",parentId:"root",name:"Zero-byte entry",kind:"file",ownAllocatedBytes:0,apparentBytes:0,knownAllocatedBytes:0,coverage:"complete",actionIdentity:true,copyRepresentable:true}]
    reply(v,"ledger",{type:"storage-children",snapshotId:"snapshot",nodeId:"root",offset:0,filter:"",total:4,nextOffset:3,
      node:{nodeId:"root",name:"Fixture",parentId:null,kind:"directory",ownAllocatedBytes:1000,knownAllocatedBytes:9000,coverage:"partial"},
      rows:rows,segments:rows,other:{offset:3,count:1,knownAllocatedBytes:1000,coverage:"partial"},breadcrumbs:[{nodeId:"root",name:"Synthetic scope"}]})
    var segment=v.geometry.filter(function(x) { return x.nodeId==="file" })[0]
    v.selectMap(segment)
    compare(v.selectedId,"file")
    compare(v.selected.nodeId,"file")
    verify(v.geometry.some(function(x) { return x.row.kind==="other" && x.row.members.indexOf("zero")>=0 }))
    for (var light of [false,true]) {
      Color.setTheme(light); s.panel.foreground=Color.foreground
      for (var size of [280,420,560]) {
        shotWindow.width=size; v.width=size
        wait(30)
        saveShot(v, String(Qt.resolvedUrl("../../shots/storage-"+(light ? "light" : "dark")+"-"+size+".png")).replace(/^file:\/\//, ""))
      }
    }
    Color.setTheme(false)
  }

  // grabToImage keeps the device pixel ratio; grabImage crops at QT_SCALE_FACTOR=2.
  function saveShot(item, path) {
    var done = false
    item.grabToImage(function(result) { done = result.saveToFile(path) })
    tryVerify(function() { return done }, 4000, "saved " + path)
  }

  function test_runtime_migration_client_and_error_routing() {
    var s = setup(), other = setup()
    verify(s.view.session.clientId !== other.view.session.clientId)
    var old = s.view.session.clientId
    var pendingId = pending(s.view, "cached")
    var next = createTemporaryObject(runtimeComponent, tc)
    s.panel.runtime = next
    verify(s.view.session.clientId !== old)
    s.view.accept({type:"storage-result",clientId:old,generation:1,requestId:pendingId,snapshot:{snapshotId:"stale"}})
    compare(s.view.snapshot,null)
    verify(pending(other.view,"cached") !== "")
  }

  function test_partial_empty_accounting_and_narrow_render() {
    var s=setup(), v=s.view
    var meta={snapshotId:"snapshot",rootNodeId:"root",coverage:"partial",capturedAt:Date.now()/1000}
    reply(v,"cached",{type:"storage-result",status:"ready",snapshot:meta})
    reply(v,"ledger",{type:"storage-children",snapshotId:"snapshot",nodeId:"root",offset:0,filter:"",total:0,nextOffset:null,
      node:{nodeId:"root",name:"Empty",parentId:null,kind:"directory",ownAllocatedBytes:4096,knownAllocatedBytes:4096,coverage:"partial"},
      rows:[],segments:[],other:null,breadcrumbs:[{nodeId:"root",name:"A very long scope breadcrumb"}],activationDurability:"pending"})
    compare(v.geometry.length,1)
    compare(v.geometry[0].row.kind,"self")
    v.width=280
    wait(20)
    compare(v.geometry[0].width,280)
    saveShot(v, String(Qt.resolvedUrl("../../shots/storage-narrow.png")).replace(/^file:\/\//, ""))
  }
  function seed(v) {
    reply(v,"cached",{type:"storage-result",status:"ready",snapshot:{snapshotId:"old",rootNodeId:"root",coverage:"complete",capturedAt:1}})
    var r={nodeId:"a",parentId:"root",kind:"file",name:"a",knownAllocatedBytes:1,ownAllocatedBytes:1,coverage:"complete"}
    reply(v,"ledger",{type:"storage-children",snapshotId:"old",nodeId:"root",offset:0,filter:"",total:1,nextOffset:null,node:{nodeId:"root",parentId:null,kind:"directory",knownAllocatedBytes:10,ownAllocatedBytes:9},rows:[r],segments:[r],other:null})
  }
  function test_review_recovered_scan_committed() {
    var v=setup().view; seed(v); v.scan()
    reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"job"})
    reply(v,"scan",{type:"storage-result",status:"committed",scanId:"job",snapshotId:"new",commitState:"committed",previousSnapshotRetained:false})
    verify(pending(v,"reconciled") !== "", "must query reconciled selected ID; actual status="+v.status+" snapshot="+v.snapshot)
  }
  function test_review_recovered_scan_uncertain() {
    var v=setup().view; seed(v); v.scan()
    reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"job"})
    reply(v,"scan",{type:"storage-result",status:"uncertain",scanId:"job",snapshotId:"new",commitState:"unknown",code:"commit-uncertain",previousSnapshotRetained:false})
    verify(v.error.indexOf("uncertain")>=0, "must disclose uncertainty; actual status="+v.status+" error="+v.error)
  }
  function test_review_rescan_from_tiny_members() {
    var v=setup().view; seed(v); v.tinyMembers=["a"]; v.pageOffsets=[50]; v.tinyOffset=50; v.filter="old"; v.scan()
    reply(v,"scan",{type:"storage-progress",status:"queued",scanId:"job"})
    reply(v,"scan",{type:"storage-result",status:"ready",scanId:"job",snapshot:{snapshotId:"new",rootNodeId:"root",capturedAt:2,coverage:"complete"}})
    var r={nodeId:"b",parentId:"root",kind:"file",name:"b",knownAllocatedBytes:1,ownAllocatedBytes:1,coverage:"complete"}
    reply(v,"ledger",{type:"storage-children",snapshotId:"new",nodeId:"root",offset:0,filter:"",total:1,nextOffset:null,node:{nodeId:"root",parentId:null,kind:"directory",knownAllocatedBytes:10,ownAllocatedBytes:9},rows:[r],segments:[r],other:null})
    compare(v.ledgerRows.length,1,"new snapshot row must be visible")
    compare(v.tinyMembers.length,0)
    compare(v.tinyOffset,0)
    compare(v.pageOffsets.length,0)
    compare(v.filter,"")
    compare(v.rowIndex,0)
  }

  function findOther(item) {
    if (item.modelData && item.modelData.row && item.modelData.row.kind === "other" && item.modelData.depth === 1) return item
    for (var c of item.children || []) { var found=findOther(c); if(found) return found }
    return null
  }
  function test_review_nested_other_selection() {
    var v=setup().view; seed(v)
    var big={nodeId:"big",parentId:"folder",name:"big",kind:"file",knownAllocatedBytes:800,ownAllocatedBytes:800}
    var tiny={nodeId:"tiny",parentId:"folder",name:"tiny",kind:"file",knownAllocatedBytes:1,ownAllocatedBytes:1}
    var folder={nodeId:"folder",parentId:"root",name:"folder",kind:"directory",knownAllocatedBytes:900,ownAllocatedBytes:99}
    var child={type:"storage-children",snapshotId:"old",nodeId:"folder",offset:0,filter:"",total:2,nextOffset:null,node:folder,rows:[big,tiny],segments:[big,tiny],other:null}
    v.listing={nodeId:"root",node:{nodeId:"root",parentId:null,knownAllocatedBytes:1000,ownAllocatedBytes:100},rows:[folder],segments:[folder],other:null}
    v.loaded={folder:child}; wait(20)
    var rectangle=findOther(v);verify(!!rectangle)
    for(var item of rectangle.children) if(typeof item.clicked === "function") item.clicked(null)
    reply(v,"ledger",child)
    compare(v.ledgerRows.length,1)
    compare(v.ledgerRows[0].nodeId,"tiny")
    compare(v.selectedId,"tiny","selected action target must match visible Other member")
    compare(v.rowIndex,0)
    v.action("copy")
    compare(v.session.pending[pending(v,"action")].args.nodeId,"tiny")
  }

  function seedMixed(v) {
    seed(v)
    var big={nodeId:"big",parentId:"root",name:"big",kind:"file",knownAllocatedBytes:800,ownAllocatedBytes:800}
    var tiny={nodeId:"tiny",parentId:"root",name:"tiny",kind:"file",knownAllocatedBytes:1,ownAllocatedBytes:1}
    v.listing={nodeId:"root",node:{nodeId:"root",kind:"directory",parentId:null,knownAllocatedBytes:900,ownAllocatedBytes:99},rows:[big,tiny],segments:[big,tiny],other:null}
    return {big:big,tiny:tiny}
  }
  function test_round2_map_after_other_keeps_visible_selection() {
    var v=setup().view; seedMixed(v)
    v.expandOther(v.geometry.filter(function(s){return s.row.kind==="other"})[0])
    compare(v.ledgerRows[0].nodeId,"tiny")
    v.selectMap(v.geometry.filter(function(s){return s.nodeId==="big"})[0])
    v.action("copy")
    compare(v.session.pending[pending(v,"action")].args.nodeId,"big")
    assertSelection(v,"big")
  }
  function test_round2_filter_after_other_shows_matches() {
    var v=setup().view, rows=seedMixed(v)
    v.expandOther(v.geometry.filter(function(s){return s.row.kind==="other"})[0])
    var input=findChild(v,"storageFilterInput"); input.text="big"; input.accepted()
    reply(v,"ledger",{type:"storage-children",snapshotId:"old",nodeId:"root",offset:0,filter:"big",total:1,nextOffset:null,node:v.listing.node,rows:[rows.big],segments:[rows.big],other:null})
    compare(v.ledgerRows.length,1,"matching big row should remain visible after filtering")
    compare(v.tinyMembers.length,0)
    compare(v.tinyOffset,0)
    compare(v.pageOffsets.length,0)
    assertSelection(v,"big")
  }
  function test_round2_nested_self_can_be_selected() {
    var v=setup().view; seed(v)
    var a={nodeId:"folder",parentId:"root",kind:"directory",name:"folder",knownAllocatedBytes:500,ownAllocatedBytes:100}
    var b={nodeId:"nested",parentId:"folder",kind:"directory",name:"nested",knownAllocatedBytes:400,ownAllocatedBytes:400}
    v.listing={nodeId:"root",node:{nodeId:"root",knownAllocatedBytes:600,ownAllocatedBytes:100},rows:[a],segments:[a],other:null}
    v.loaded={folder:{nodeId:"folder",node:a,rows:[b],segments:[b],other:null},nested:{nodeId:"nested",node:b,rows:[],segments:[],other:null}}
    var segment=v.geometry.filter(function(s){return s.row.kind==="self" && s.depth===2})[0]
    verify(!!segment); v.selectMap(segment)
    verify(!!v.selected,"nested self selection must resolve into an actionable directory")
    compare(v.listing.nodeId,"nested")
    compare(v.showingSelf,true)
    assertSelection(v,"nested")
    v.up()
    compare(v.showingSelf,false)
    assertSelection(v,"nested")
    v.up()
    compare(v.session.pending[pending(v,"ledger")].args.nodeId,"folder")
  }
  function assertSelection(v, id) {
    compare(v.selectedId,id)
    verify(!!v.selected)
    compare(v.selected.nodeId,id)
    compare(v.ledgerRows[v.rowIndex].nodeId,id,"row index, visible highlight and action target agree")
    v.action("copy")
    compare(v.session.pending[pending(v,"action")].args.nodeId,id)
    reply(v,"action",{type:"storage-action",snapshotId:v.snapshot.snapshotId,nodeId:id,action:"copy"})
    v.action("open")
    compare(v.session.pending[pending(v,"action")].args.nodeId,id)
    reply(v,"action",{type:"storage-action",snapshotId:v.snapshot.snapshotId,nodeId:id,action:"open"})
  }
  function responseFor(v, id, data) {
    return Object.assign({requestId:id,clientId:v.session.clientId,generation:v.session.generation},data)
  }
  function children(v, node, rows, offset, next) {
    return {type:"storage-children",snapshotId:v.snapshot.snapshotId,nodeId:node.nodeId,
      offset:offset || 0,filter:v.filter,total:rows.length,nextOffset:next === undefined ? null : next,
      node:node,rows:rows,segments:rows,other:null}
  }
  function test_pending_nested_other_blocks_old_map_until_reply() {
    var v=setup().view, rows=seedMixed(v)
    var original=v.listing
    var folder={nodeId:"folder",parentId:"root",kind:"directory",name:"folder",knownAllocatedBytes:100,ownAllocatedBytes:99}
    var child=children(v,folder,[{nodeId:"nested-tiny",name:"nested tiny",kind:"file",knownAllocatedBytes:1}])
    v.loaded={folder:child}
    v.expandOther({parentId:"folder",row:{members:["nested-tiny"],offset:null}})
    var id=pending(v,"ledger"), obsolete=responseFor(v,id,child)
    verify(!!id)
    compare(v.selected,null,"pending navigation cannot act on old selection")
    v.action("copy"); compare(pending(v,"action"),"")
    v.selectMap(v.geometry.filter(function(s){return s.nodeId==="big"})[0])
    compare(pending(v,"ledger"),id); compare(v.selected,null)
    v.accept(obsolete)
    compare(v.listing.nodeId,"folder"); assertSelection(v,"nested-tiny")
    v.navigate("root"); reply(v,"ledger",children(v,original.node,original.rows))
    v.selectMap(v.geometry.filter(function(s){return s.nodeId==="big"})[0])
    assertSelection(v,"big")
  }
  function test_pending_map_file_blocks_old_self_then_allows_later_choice() {
    var v=setup().view, rows=seedMixed(v)
    // The bands contain a file outside the currently displayed ledger page.
    v.listing=Object.assign({},v.listing,{rows:[rows.tiny],offset:50})
    var original=v.listing
    v.selectMap(v.geometry.filter(function(s){return s.nodeId==="big"})[0])
    var id=pending(v,"ledger"), obsolete=responseFor(v,id,children(v,v.listing.node,[rows.big]))
    verify(!!id); compare(v.selected,null)
    v.selectMap(v.geometry.filter(function(s){return s.row.kind==="self"})[0])
    compare(pending(v,"ledger"),id); compare(v.selected,null)
    v.accept(obsolete); assertSelection(v,"big")
    v.selectSelf(v.listing); assertSelection(v,"root")
    v.allEntries(); assertSelection(v,"big")
  }
  function test_filter_supersedes_pending_other_and_clears_selection() {
    var v=setup().view, rows=seedMixed(v)
    v.expandOther(v.geometry.filter(function(s){return s.row.kind==="other"})[0])
    v.query("root",0,"ledger",0,"tiny")
    var id=pending(v,"ledger"), obsolete=responseFor(v,id,children(v,v.listing.node,[rows.tiny]))
    var input=findChild(v,"storageFilterInput"); input.text="big"; input.accepted()
    compare(v.selected,null)
    v.accept(obsolete); compare(v.selected,null)
    reply(v,"ledger",children(v,v.listing.node,[rows.big]))
    assertSelection(v,"big")
    input.text="missing"; input.accepted()
    reply(v,"ledger",children(v,v.listing.node,[]))
    compare(v.selected,null,"no filter matches must not select an unrelated directory")
  }
  function test_paging_from_other_and_keyboard_wait_for_pending_selection() {
    var v=setup().view, rows=seedMixed(v)
    v.expandOther(v.geometry.filter(function(s){return s.row.kind==="other"})[0])
    v.page(2)
    compare(v.tinyMembers.length,0); compare(v.selected,null)
    var later={nodeId:"later",parentId:"root",kind:"file",name:"later",knownAllocatedBytes:10}
    reply(v,"ledger",children(v,v.listing.node,[later,rows.big],2))
    assertSelection(v,"later")
    v.query("root",0,"ledger",0,"tiny")
    var id=pending(v,"ledger"), obsolete=responseFor(v,id,children(v,v.listing.node,[rows.tiny]))
    v.handle("storageMove",1)
    compare(pending(v,"ledger"),id); compare(v.selected,null)
    compare(v.rowIndex,0)
    v.accept(obsolete); assertSelection(v,"tiny")
    v.listing=children(v,v.listing.node,[rows.tiny,rows.big])
    v.handle("storageMove",1); assertSelection(v,"big")
  }
  function test_tiny_pages_and_all_entries_keep_index_and_identity() {
    var v=setup().view; seed(v)
    var rows=[]
    for(var i=0;i<55;i++) rows.push({nodeId:"tiny-"+i,parentId:"root",kind:"file",name:"tiny "+i,knownAllocatedBytes:1})
    v.listing=Object.assign({},v.listing,{rows:rows.slice(0,50),segments:rows,node:Object.assign({},v.listing.node,{knownAllocatedBytes:100000})})
    v.showTiny(); compare(v.ledgerRows.length,50); assertSelection(v,"tiny-0")
    v.tinyOffset=50; compare(v.ledgerRows.length,5); assertSelection(v,"tiny-50")
    v.handle("storageMove",4); assertSelection(v,"tiny-54")
    v.tinyOffset=0; assertSelection(v,"tiny-0")
    v.allEntries(); compare(v.tinyMembers.length,0); assertSelection(v,"tiny-0")
  }

  function test_map_directory_from_other_and_filter_from_self() {
    var v=setup().view, rows=seedMixed(v)
    var folder={nodeId:"folder",parentId:"root",name:"folder",kind:"directory",knownAllocatedBytes:100,ownAllocatedBytes:90}
    v.listing=Object.assign({},v.listing,{rows:[rows.big,rows.tiny,folder],segments:[rows.big,rows.tiny,folder]})
    v.expandOther(v.geometry.filter(function(s){return s.row.kind==="other"})[0])
    v.selectMap(v.geometry.filter(function(s){return s.nodeId==="folder" && s.row.kind==="directory"})[0])
    compare(v.tinyMembers.length,0); compare(v.selected,null)
    var child={nodeId:"child",parentId:"folder",name:"child",kind:"file",knownAllocatedBytes:10}
    reply(v,"ledger",children(v,folder,[child]))
    assertSelection(v,"child")
    v.selectSelf(v.listing); assertSelection(v,"folder")
    var input=findChild(v,"storageFilterInput"); input.text="child"; input.accepted()
    compare(v.showingSelf,false); compare(v.selected,null)
    reply(v,"ledger",children(v,folder,[child])); assertSelection(v,"child")
  }

  function test_pending_nested_other_tiny_page_keeps_required_query() {
    var v=setup().view; seed(v)
    var folder={nodeId:"folder",parentId:"root",kind:"directory",name:"folder",knownAllocatedBytes:10000,ownAllocatedBytes:9945}
    var rows=[]
    for(var i=0;i<55;i++) rows.push({nodeId:"nested-"+i,parentId:"folder",kind:"file",name:"nested "+i,knownAllocatedBytes:1})
    var child=Object.assign(children(v,folder,rows.slice(0,50)),{segments:rows})
    v.loaded={folder:child}
    v.expandOther({parentId:"folder",row:{members:rows.map(function(r){return r.nodeId}),offset:null}})
    var id=pending(v,"ledger"); verify(!!id)
    v.tinyOffset=50
    compare(pending(v,"ledger"),id,"paging members still needs the requested directory")
    compare(v.selected,null)
    reply(v,"ledger",child)
    compare(v.ledgerRows.length,5); assertSelection(v,"nested-50")
  }

  function test_round3_pending_filter_row_choice_keeps_filter_truthful() {
    var v=setup().view, rows=seedMixed(v)
    v.listing=Object.assign({},v.listing,{filter:"",offset:0,total:2,nextOffset:null})
    var input=findChild(v,"storageFilterInput"); input.text="big"; input.accepted()
    var id=pending(v,"ledger")
    verify(!!id)
    var filtered=responseFor(v,id,children(v,v.listing.node,[rows.big]))
    // Drive the actual old delegate, including a queued clicked signal.
    function findRow(item) {
      if (item.modelData && item.modelData.nodeId === "tiny" && typeof item.clicked === "function") return item
      for (var child of item.children || []) { var match=findRow(child); if(match) return match }
      return null
    }
    wait(20)
    var row=findRow(v); verify(!!row,"old tiny delegate remains displayed")
    verify(!row.enabled,"old rows are disabled while query is pending")
    mouseClick(row)
    row.clicked()
    compare(pending(v,"ledger"),id)
    compare(v.selected,null)
    v.accept(filtered)
    compare(v.listing.filter,v.filter,"displayed rows must match active literal filter")
    compare(v.ledgerRows.length,1)
    compare(v.ledgerRows[0].nodeId,"big")
  }

  function test_pending_filter_blocks_old_view_choices_data() {
    return ["row", "keyboard", "map", "other", "self", "tiny", "all", "page", "up", "copy", "open", "descend", "scan"].map(function(choice) { return {tag:choice, choice:choice} })
  }
  function test_pending_filter_blocks_old_view_choices(data) {
    var v=setup().view, rows=seedMixed(v), original=v.listing
    var big=v.geometry.filter(function(s){return s.nodeId==="big"})[0]
    var other=v.geometry.filter(function(s){return s.row.kind==="other"})[0]
    v.applyFilter("big")
    var id=pending(v,"ledger")
    switch(data.choice) {
    case "row": v.selectRow("tiny"); break
    case "keyboard": v.handle("storageMove",1); break
    case "map": v.selectMap(big); break
    case "other": v.expandOther(other); break
    case "self": v.selectSelf(original); break
    case "tiny": v.showTiny(); break
    case "all": v.allEntries(); break
    case "page": v.page(50); break
    case "up": v.up(); break
    case "copy": v.handle("storageCopy"); break
    case "open": v.handle("storageOpen"); break
    case "descend": v.handle("storageDescend"); break
    case "scan": v.handle("storageScan"); break
    }
    compare(pending(v,"ledger"),id)
    compare(v.listing,original); compare(v.selected,null)
    compare(v.tinyMembers.length,0); compare(v.showingSelf,false); compare(v.rowIndex,0)
    compare(pending(v,"action"),""); verify(!v.scanning)
    reply(v,"ledger",children(v,original.node,[rows.big]))
    compare(v.listing.filter,"big"); assertSelection(v,"big")
  }
  function test_pending_filter_new_filter_navigation_and_exit() {
    var s=setup(), v=s.view, rows=seedMixed(v), node=v.listing.node
    v.applyFilter("tiny")
    var stale=responseFor(v,pending(v,"ledger"),children(v,node,[rows.tiny]))
    v.applyFilter("big")
    v.accept(stale); compare(v.selected,null)
    reply(v,"ledger",children(v,node,[rows.big])); assertSelection(v,"big")
    v.applyFilter("tiny")
    stale=responseFor(v,pending(v,"ledger"),children(v,node,[rows.tiny]))
    v.navigate("root")
    v.accept(stale); compare(v.selected,null)
    reply(v,"ledger",children(v,node,[rows.big,rows.tiny]))
    compare(v.filter,""); v.selectRow("tiny"); assertSelection(v,"tiny")
    v.applyFilter("big"); v.handle("storageEscape"); verify(s.panel.closed)
    stale=responseFor(v,pending(v,"ledger"),children(v,node,[rows.big]))
    v.leave(); v.accept(stale); compare(pending(v,"ledger"),"")
  }
  function test_failed_filter_restores_displayed_query_and_actions() {
    var v=setup().view, rows=seedMixed(v), node=v.listing.node
    v.applyFilter("tiny"); reply(v,"ledger",children(v,node,[rows.tiny]))
    v.applyFilter("big")
    reply(v,"ledger",{type:"error",code:"query-timeout",message:"Timed out"})
    compare(v.filter,"tiny"); compare(findChild(v,"storageFilterInput").text,"tiny")
    compare(v.listing.filter,"tiny"); verify(v.error.indexOf("query-timeout")>=0)
    assertSelection(v,"tiny")
  }

}
