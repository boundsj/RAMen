const assert = require('node:assert/strict');
const { load, plain } = require('./load_js');
const M = load('StorageModel.js'), N = load('PanelNav.js'), R = load('Runtime.js');
const row = (id, bytes, kind='file') => ({nodeId:id, name:id, kind, knownAllocatedBytes:bytes, ownAllocatedBytes:bytes, coverage:'complete'});
const child = {nodeId:'child', node:row('child',60,'directory'), segments:[row('nested',40)], rows:[], filter:'', other:null};
child.node.ownAllocatedBytes=20;
const root = {nodeId:'root', node:row('root',100,'directory'), segments:[row('child',60,'directory'),row('tiny',1)], rows:[], filter:'', other:{offset:2, knownAllocatedBytes:29}};
root.node.ownAllocatedBytes=10;
let bands=M.bands(root,{child},1000,28);
assert.equal(bands.filter(x=>x.depth===0).reduce((v,x)=>v+x.width,0),1000);
assert.equal(bands.find(x=>x.name==='Folder itself').width,100);
assert.equal(bands.find(x=>x.nodeId==='child'&&x.depth===0).width,600);
assert.equal(bands.find(x=>x.nodeId==='nested').width,400);
assert.ok(bands.every(x=>x.height===24&&x.x+x.width<=1000&&x.depth<3));
assert.equal(bands.find(x=>x.row.kind==='other').row.offset,2);
let tiny=M.bands(root,{},100,28).find(x=>x.row.kind==='other');
assert.deepEqual(plain(tiny.row.members),['tiny']);
assert.equal(tiny.width,30);
assert.deepEqual(plain(M.bands({...root,node:row('root',0)}, {},100,28)),[]);
const many={...root,segments:Array.from({length:59},(_,i)=>row('r'+i,1)),other:{offset:59,knownAllocatedBytes:31}};
assert.ok(M.bands(many,{},10000,28).length<=60);
assert.equal(M.bands(many,{},10000,28).reduce((sum,x)=>sum+x.width,0),10000);
assert.equal(M.bytes(null),'unknown'); assert.equal(M.bytes(0),'0 B');
assert.equal(M.selected([row('same display',1)],'different'),null);
let s=M.state('one'), q=M.request(s,'storage.scan',{path:'/tmp'},'scan');
s=q.state;
let ack={type:'storage-progress',requestId:q.message.requestId,clientId:'one',generation:1,status:'queued',scanId:'scan'};
let a=M.accept(s,ack); assert.ok(a); s=a.state;
assert.equal(M.accept(s,{...ack,clientId:'other'}),null);
assert.equal(M.accept(s,{...ack,scanId:'other'}),null);
const before=s; s=M.advance(s);
assert.equal(M.accept(s,ack),null);
q=M.request(s,'storage.cancel',{scanId:'scan'},'cancel');s=q.state;
a=M.accept(s,{...q.message,type:'storage-progress',status:'cancelling'});assert.ok(a);s=a.state;
assert.ok(s.pending[q.message.requestId],'ack is not terminal');
a=M.accept(s,{...q.message,type:'storage-result',status:'committed'});s=a.state;
assert.equal(M.accept(s,{...q.message,type:'storage-result',status:'committed'}),null,'terminal only once');
q=M.request(s,'storage.children',{snapshotId:'snap',nodeId:'node',offset:17,filter:'%_'},'ledger');s=q.state;
const reply={...q.message,type:'storage-children'};
assert.equal(M.accept(s,{...reply,offset:18}),null);
assert.equal(M.accept(s,{...reply,snapshotId:'old'}),null);
assert.equal(M.accept(s,{...reply,nodeId:'other'}),null);
assert.equal(M.accept(s,{...reply,filter:''}),null);
assert.ok(M.accept(s,reply));
assert.equal(R.replyStream({type:'error',clientId:'one',requestId:'s:one:1'}),'storage');
assert.equal(R.replyStream({type:'error',requestId:'i:2'}),'');
assert.equal(R.replyStream({type:'error',requestId:'h:one:1'}),'history');
const subs=R.setSubscription(R.setSubscription({},'a',{detail:true}),'b',{storage:true});
assert.equal(R.wants(R.removeSubscription(subs,'b'),'detail'),true);
for(const region of N.REGIONS.storage) for(const key of ['up','down','left','right','enter','delete','force','refresh','escape','cancel','open','copy','backspace']) {
  assert.equal(N.SIGNAL_ACTIONS.includes(N.route({page:'storage',region},key,{}).action),false);
  assert.equal(N.route({page:'storage',region},key,{inputOwned:true}).action,null);
}
assert.equal(N.route({page:'storage',region:'ledger'},'refresh').action,'storageScan');
assert.deepEqual(plain(N.subscriptionFor(true,'storage')),{storage:true});
console.log('storage model, generation and no-signal tests passed');

assert.equal(M.commandFits({path:'雪'.repeat(1400)}),false);
assert.equal(M.commandFits({path:'a'.repeat(4000)}),true);
assert.equal(M.commandFits({path:'a'.repeat(4096)}),false);

// Fully correlated controller errors have no scan identity. Successful replies
// and errors with an explicit mismatching identity still require the owned scan.
q=M.request(M.advance(s),'storage.cancel',{scanId:'scan'},'cancel'); s=q.state;
const commandError={type:'error',requestId:q.message.requestId,clientId:s.clientId,generation:s.generation,code:'unknown-scan',message:'scan is not active for this client'};
assert.equal(M.accept(s,{...commandError,clientId:'other'}),null);
assert.equal(M.accept(s,{...commandError,generation:s.generation-1}),null);
assert.equal(M.accept(s,{...commandError,requestId:'unrelated'}),null);
assert.equal(M.accept(s,{...commandError,scanId:'other'}),null);
assert.equal(M.accept(s,{...commandError,type:'storage-result',status:'cancelled'}),null);
assert.ok(M.accept(s,commandError));

// A nested level may have only one segment left in the shared budget. Its
// coalesced band must preserve self, explicit entries and the continuation.
const nestedBudget={nodeId:'nested-budget',node:row('nested-budget',100,'directory'),segments:[row('visible',40),row('tiny-budget',1)],rows:[],filter:'',other:{offset:2,knownAllocatedBytes:49}};
nestedBudget.node.ownAllocatedBytes=10;
const topBudget={nodeId:'top',node:row('top',5900,'directory'),segments:[row('nested-budget',100,'directory'),...Array.from({length:57},(_,i)=>row('top-'+i,100))],rows:[],filter:'',other:null};
topBudget.node.ownAllocatedBytes=100;
const budgetBands=M.bands(topBudget,{'nested-budget':nestedBudget},5900,28);
assert.equal(budgetBands.length,60);
const budgetOther=budgetBands.find(x=>x.depth===1);
assert.equal(budgetOther.row.kind,'other');
assert.deepEqual(plain(budgetOther.row.members),['nested-budget','visible','tiny-budget']);
assert.equal(budgetOther.row.offset,2);
assert.equal(budgetOther.row.knownAllocatedBytes,100);

assert.equal(M.sameIdentity({device:1,inode:2},{inode:2,device:1}),true);
assert.equal(M.sameIdentity({uniqueMountId:'1'},{uniqueMountId:'2'}),false);
assert.equal(M.sameIdentity(null,{}),false);
assert.equal(M.sameIdentity({device:1},{device:1,inode:2}),false);
for (const code of ['missing-root','missing-volume','root-changed','stale-snapshot','identity-unavailable','unsupported-filesystem','snapshot-not-active','invalid-snapshot','unsafe-cache'])
  assert.equal(M.failure(code).invalidates,true,code);
for (const code of ['scan-time-limit','scan-entry-limit','scan-depth-limit','cache-size-limit']) {
  assert.equal(M.failure(code).state,'limited');
  assert.equal(M.failure(code).invalidates,false);
}
assert.equal(M.failure('worker-failed').state,'helper-failed');
assert.equal(M.failure('invalid-path').state,'invalid-path');
assert.equal(M.failure('invalid-path').invalidates,true);
