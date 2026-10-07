const assert = require("node:assert/strict");
const { load, plain } = require("./load_js");
const H = load("HistoryModel.js");
const opts = { alertHoldSeconds: 10, alertCooldownSeconds: 60, alertMode: "warning-and-critical" };
const sample = (t, used = 50, psi = 0, extra = {}) => Object.assign({monotonic:t,time:1000+t,seq:t+1,
  session:"test",total:100,used,available:100-used,psiSome10:psi,dispatchOwner:true,demo:false},extra);
let state, events;
function reset(blocked = false) { state = H.incidentState(blocked); events = []; }
function tick(t, used = 50, psi = 0, extra = {}, options = opts) {
  const r = H.incidentStep(state, sample(t,used,psi,extra), {}, options);state = r.state;events.push(...plain(r.events));return plain(r.events);
}
reset(); for(let t=0;t<=20;t+=2)tick(t);assert.equal(events.length,0);
reset();tick(0,80);tick(2,80);tick(4,50);assert.equal(events.length,0);
reset();for(let t=0;t<=10;t+=2)tick(t,80);assert.equal(state.phase,"active");assert.equal(events[0].action,"start");assert.equal(events[0].notify,true);
const key=events[0].key;for(let t=12;t<=22;t+=2)tick(t,95);assert.equal(events.filter(e=>e.action==="upgrade").length,1);
assert.equal(events.find(e=>e.action==="upgrade").key,key);assert.equal(events.find(e=>e.action==="upgrade").notify,true);
for(let t=24;t<=120;t+=2)tick(t,95);assert.equal(events.filter(e=>e.notify).length,2,"sustained critical never repeats after cooldown");
tick(122,74);assert.equal(state.phase,"active","hysteresis requires <72% to recover");tick(124,71);assert.equal(events.at(-1).action,"recover");
reset();for(let t=0;t<=10;t+=2)tick(t,80);tick(12,50);for(let t=14;t<=24;t+=2)tick(t,80);
assert.equal(events.filter(e=>e.action==="start").length,2);assert.equal(events.filter(e=>e.notify).length,1,"same severity cooldown across recovered incidents");
reset();for(let t=0;t<=10;t+=2)tick(t,80);tick(12,50,null);assert.equal(events.at(-1).action,"interrupt");assert.equal(events.at(-1).summarySeq,11,"unknown closes at last valid sample");
reset();for(let t=0;t<=10;t+=2)tick(t,80,null);assert.equal(state.phase,"active","known RAM breach qualifies without PSI");
reset();tick(0,80);tick(2,80);tick(20,80);assert.equal(state.pending,null,"gap resets dwell and skips first post-gap reading");
for(let t=22;t<=32;t+=2)tick(t,80);assert.equal(events[0].startSeq,23);
for (const extra of [{breakBefore:true},{time:900},{session:"new"}]) {
 reset();for(let t=0;t<=10;t+=2)tick(t,80);tick(12,80,0,extra);assert.equal(events.at(-1).action,"interrupt");
}
reset(true);for(let t=0;t<=20;t+=2)tick(t,95);assert.equal(events.length,0);tick(22,50);for(let t=24;t<=34;t+=2)tick(t,95);assert.equal(events[0].severity,"critical");
for (const extra of [{demo:true},{dispatchOwner:false}]) {reset();for(let t=0;t<=20;t+=2)tick(t,95,0,extra);assert.equal(events.length,0);}
reset();for(let t=0;t<=10;t+=2)tick(t,80,0,{}, {...opts,alertMode:"off"});assert.equal(events[0].notify,false,"events collected with alerts off");
reset();for(let t=0;t<=20;t+=2)tick(t,95,0,{}, {...opts,historyEnabled:false});assert.equal(events.length,0);
assert.deepEqual(plain(H.incidentSettings({alertHoldSeconds:999,alertCooldownSeconds:-3})),{enabled:true,persist:true,mode:"off",hold:60,cooldown:60});
assert.equal(H.incidentSettings({alertHoldSeconds:NaN,alertCooldownSeconds:null}).hold,10);
const ack = {persisted:false,incident:{severity:"critical",reason:"pressure",measurement:{psiSome10:24,available:640*1024},context:{status:"not-observed"}}};
assert.match(H.incidentToast(ack,10).body,/not saved/);assert.doesNotMatch(H.incidentToast(ack,10).body,/Observed/);
console.log("incident state machine tests passed");

reset();for(let t=0;t<=10;t+=2)tick(t,95);tick(12,50);
for(let t=14;t<=24;t+=2)tick(t,80);for(let t=26;t<=36;t+=2)tick(t,95);
assert.equal(events.filter(e=>e.severity==="critical"&&e.notify).length,1,"upgrade still obeys prior same-severity critical cooldown");
assert.match(H.pageState({enabled:false,persistence:{save:"off"},t:[]}).notes[0],/collection is off/);
assert.match(H.pageState({enabled:true,persistence:{save:"off"},t:[]}).notes[0],/persistence is off/);

assert.match(H.incidentToast({...ack,measurement:{psiSome10:31,available:1024}},10).body,/31%/,"toast uses acknowledged detection sample rather than earlier start");

reset(true);for(let t=0;t<=30;t+=2)tick(t,50,null);
assert.equal(state.blockedPressure,true,"missing PSI never proves pressure recovery");
assert.equal(state.blockedUsed,false,"known low RAM re-arms independent RAM detection");
for(let t=32;t<=122;t+=2)tick(t,95,null);
assert.equal(events.filter(e=>e.action==="start").length,1,"later RAM breach works after restart without PSI");
assert.equal(events[0].severity,"critical");
assert.equal(events[0].reason,"used");
assert.equal(events[0].notify,true);
reset(true);for(let t=0;t<=90;t+=2)tick(t,95,null);
assert.equal(events.length,0,"RAM already high at takeover remains suppressed");
reset(true);for(let t=0;t<=30;t+=2)tick(t,50,null);
for(let t=32;t<=50;t+=2)tick(t,50,25);
assert.equal(events.length,0,"unobserved pressure recovery must not re-arm pressure");
tick(52,50,0);for(let t=54;t<=64;t+=2)tick(t,50,25);
assert.equal(events[0].reason,"pressure");

reset();tick(0,50,6);for(let t=2;t<=10;t+=2)tick(t,50,3);
assert.equal(events[0].reason,"pressure","PSI hysteresis qualifies the warning throughout dwell");
const causeAck={persisted:true,incident:{severity:"warn",reason:events[0].reason},
 measurement:{total:100,used:50,available:50,psiSome10:3}};
assert.match(H.incidentToast(causeAck,10).body,/Memory pressure 3.0%/);
assert.doesNotMatch(H.incidentToast(causeAck,10).body,/RAM used/);
reset();for(let t=0;t<=10;t+=2)tick(t,80,3);
assert.equal(events[0].reason,"used","PSI that never breached cannot steal RAM causality");

const oldContext={status:"observed",at:1000,apps:[{name:"Old Browser",kb:1024}]};
const upgradeAck={...ack,incident:{...ack.incident,context:oldContext},notificationContext:{status:"not-observed"}};
assert.doesNotMatch(H.incidentToast(upgradeAck,10).body,/Old Browser/,"upgrade cannot reuse historical context");
assert.match(H.incidentToast({...upgradeAck,notificationContext:{...oldContext,apps:[{name:"Fresh Editor",kb:2048}]}},10).body,/Fresh Editor/);
const observedAck={...upgradeAck,notificationContext:oldContext};
assert.match(H.incidentToast(observedAck,10,{detail:true,now:1005000}).body,/Old Browser/);
for(const delivery of [{detail:false,now:1001000},{detail:true,now:1005001},{detail:true,now:999000}])
 assert.doesNotMatch(H.incidentToast(observedAck,10,delivery).body,/Old Browser/,"dispatch rechecks subscription and age after queueing");

// Round-two trigger: null PSI must not discard independently held RAM dwell.
reset();tick(0,80,null);for(let t=2;t<=10;t+=2)tick(t,74,null);
assert.equal(state.phase,"active");assert.equal(events[0].startSeq,1);
assert.equal(events[0].reason,"used");
tick(12,74,null);assert.equal(events.at(-1).action,"extend");
tick(14,72,null);assert.equal(events.at(-1).action,"extend","hysteresis boundary is inclusive");
tick(16,71,null);assert.equal(events.at(-1).action,"interrupt","missing PSI cannot prove total recovery");
reset();for(let t=0;t<=10;t+=2)tick(t,95,null);
tick(12,89,null);assert.equal(state.phase,"active");
for(let t=14;t<=24;t+=2)tick(t,95,null);
assert.equal(events.filter(e=>e.action==="start").length,1,"critical RAM stays the same incident");
reset();tick(0,74,6);tick(2,74,null);
assert.equal(state.pending,null,"PSI warning cannot invent held RAM evidence");
reset();tick(0,74,6);for(let t=2;t<=10;t+=2)tick(t,74,3);
assert.equal(events[0].reason,"pressure");
reset();tick(0,80,0);for(let t=2;t<=10;t+=2)tick(t,74,null);
assert.equal(events[0].reason,"used","PSI loss does not remove measured RAM qualification");

const markupAck={...observedAck,notificationContext:{status:"observed",at:1000,
 apps:[{name:'<b>Fake system warning</b> &amp; spoof "quoted" \'single\' <img src="file:///private">',kb:1024}]}};
const markupBody=H.incidentToast(markupAck,10,{detail:true,now:1001000}).body;
assert.match(markupBody,/&lt;b&gt;Fake system warning&lt;\/b&gt; &amp;amp; spoof &quot;quoted&quot; &#39;single&#39;/);
assert.doesNotMatch(markupBody,/<[^>]*>/,"host StyledText must receive no executable app markup");
assert.equal(markupAck.notificationContext.apps[0].name.startsWith("<b>"),true,"receipt name remains literal historical data");
const expired=H.receipt({t:[],incidents:[],to:91000},{id:"long",start:1000,end:null,severity:"critical",reason:"used",
 measurement:{},measurementStatus:"expired",context:{status:"expired"}});
assert.match(expired.threshold,/expired/);assert.match(expired.observation,/expired/);
assert.equal(expired.atStart.used,"no reading");assert.equal(expired.apps.length,0);
