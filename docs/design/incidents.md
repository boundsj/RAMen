# Live incidents and alert ownership (H3)

The History view is a read-only consumer. `HistoryModel.js` now also contains
a pure live state machine, driven exclusively by current probe summaries in
`RamanRuntime.qml`. `Level.js` remains the severity/hysteresis authority.
`Service.qml` remains the single supported shell runtime, not a new singleton.

## State and clocks

Healthy → pending → active → recovered. Pending begins at a qualifying sample
and requires the configured continuous monotonic hold (default 10 s, 2–60).
The receipt starts at the first qualifying sample; its acknowledgement sample
and fresh context are from detection time. Toast text uses the referenced
detection/upgrade sample; the receipt retains its first sample for At start. If critical has not lasted its own
hold by activation, the receipt begins warning and can upgrade once after
critical dwell. Active extends one row; upgrade stores its own time/measurement.
Recovery requires Level's existing 3-point hysteresis independently for RAM and
PSI; unreadable PSI cannot erase held RAM evidence or invent RAM evidence from
a prior pressure-only warning. Critical dropping to
warning does not close an incident. An expired cooldown never repeats a
sustained alert. A recovered new incident can be recorded during cooldown,
with its same-severity toast suppressed. Warning and critical cooldowns are
separate (300 s default, 60–3600); critical upgrade bypasses a recent warning,
but not a recent critical notification.
The pending cause preserves a qualifying pressure dimension while its hysteresis
band holds, instead of naming healthy RAM. PSI that never crossed its threshold
cannot take over a RAM-driven explanation merely by sitting in that band.

Monotonic time controls dwell/cooldown, wall time labels receipts, probe session
and boot clocks identify continuity. Probe `breakBefore`, >6 s sampling gaps,
clock rollback/wall disagreement and a changed session reset pending. An active
break or unknown reading closes at the last observed sample as interrupted;
repeat detection waits for real healthy recovery. Low usage with unavailable
PSI cannot prove pressure recovery. Low measured RAM re-arms RAM independently;
known later high RAM usage can qualify without inventing a PSI recovery.
Known high RAM usage can qualify independently of
PSI. Unavailable live sources now stay null, with dashes in Memory/tooltip,
rather than displaying a measured zero. Gauge behavior still uses Level.

Config changes to collection/persistence/thresholds interrupt old continuity
and invalidate outstanding notifications. Alert mode changes invalidate toasts
without deleting incidents. Disabling collection stops sampling/detection/toasts
but retains disk state; persistence off clears the session view and neither
loads nor saves the history file. Transitioning out first flushes the preceding
persisted session. The QML probe uses a deferred configuration handshake, so
it cannot load/save history before widget settings arrive.
Detection also waits for the matching configuration acknowledgement and summary
configuration ID. Each collection/persistence transition rejects buffered readings
from the previous configuration, including deferred summaries never ingested.

## Acknowledgements and dispatch

The producer sends only sequence references, lifecycle metadata and thresholds.
Python reads measurements from its raw ring and app context from an existing
active detail subscription ≤2×2.5 s old. It never scans on incident commands.
Receipt context whitelists ≤5 names/footprints, with its actual observation time;
it is not attribution of cause and cannot become a live process identity.
Each acknowledgement supplies separate current notificationContext, rechecking
subscription and snapshot age even on critical upgrades. Historical receipt
context is never reused by a later toast or refreshed by upgrades; it expires
by its own observation timestamp under the 24-hour payload policy.
When the helper starts, queued notifications recheck the UI subscription and
observation age; closed or expired context is omitted without a scan.

Queries overlap an active receipt using its last observed tail. Only its minimal
identity/lifecycle (ID, start/lastAt/end, status, severity/reason) survives beyond
24 hours. Start measurements/thresholds expire by start time, app context by
its observation time, and upgrade measurements by upgrade time. Expiry removes
app names/footprints and nulls measurements, with explicit expired markers in
queries, persistence and read-only UI. A newer upgrade may still carry evidence
after the original start expired. Sampling, query, save and adoption enforce
payload expiry; a stopped/disabled store is pruned when next adopted, and normal
disk expiry follows the minute checkpoint cadence. Closed receipts expire
24 hours after their end. The 50-receipt/2 MiB limits remain; count
trimming and file compaction remove closed receipts before the live lifecycle.
Erase markers are observed even with persistence/collection off. A remote Clear
invalidates visible queries and the removed lifecycle without starting a scan.
Marker observation retains a pending disk-erasure obligation: the writer saves
on its next tick even when another call already consumed the marker. Failed
erasure stays pending and retries each tick/exit rather than waiting a minute.

Start/upgrade/end checkpoints attempt an immediate atomic history save; extends
update memory and the regular minute save. The incident reply is the save
acknowledgement. QML waits for it before sending a toast; a failed or disabled
save adds “not saved”. Lost/error/expired acknowledgements do not toast. Only
start/upgrade may reserve notifications; duplicate transitions reserve once.

An independent flock (`dispatch.lock`) selects one producer even with history
persistence off/private fallbacks. `dispatch.json` holds only boot identity and
two cooldown timestamps, never historical measurements or apps. A reservation
is checkpointed before the reply; history and dispatch failures are reported
separately. Unknown policy/corrupt data cannot crash telemetry. A boot identity
stamp in the stable lock inode distinguishes same-boot continuity from a new
boot. Same-boot restart/takeover suppresses each dimension until measured below
its warning hysteresis boundary, even if the last checkpoint failed. Unknown
legacy stamps remain conservative. Parsed unfinished receipts
become interrupted, never restored live objects. Epoch changes invalidate
pending acknowledgements. Restored receipts never enter the producer.

**Deliberate tradeoff:** a replacement dispatcher in the same boot will not
record/toast a fresh incident in a dimension until that dimension recovers.
Unreadable PSI stays suppressed while known low RAM can re-arm RAM-only events.
This sacrifices launch-time alerts to prevent duplicated
sustained events across failed checkpoints, session-only restarts and takeover.
Cooldown metadata remains even with history persistence off/after Clear. A
notification reservation may survive a crash before desktop delivery: delivery
is at most once, not guaranteed. No retries turn restored history into a toast.
These policy decisions need independent review and exact-head live acceptance.

## Verified host API

Primary sources inspected at Omarchy v4.0.4,
`c668141e9c42b13c80c9ca4ea108e11708c5e8a5`:

- [notification helper](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/bin/omarchy-notification-send):
  app name, urgency, timeout, title/body and discrete `--exec` argv. No shell
  string interpolation and no invented QML notification API.
- [notification service](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/shell/plugins/notifications/Service.qml):
  click executes validated argv via Util.execArgv. RAMen passes
  `omarchy-shell boundsj.raman openIncident <id>`; DND remains host-controlled.
- [notification rendering logic](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/shell/plugins/notifications/NotificationLogic.js)
  and [card](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/shell/plugins/notifications/components/NotificationCard.qml):
  bodies use StyledText and preserve formatting tags. RAMen escapes app names
  at the notification boundary, including existing entities, so they render
  literally once. Historical names remain literal data. The offscreen check
  stages this pinned host logic as a separately supplied test dependency.
- [scoped shell API](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/shell/services/PluginShellApi.qml):
  own-service lookup and summon/hide/toggle, preserving focused-monitor routing.
- [built-in schema](https://github.com/basecamp/omarchy/blob/c668141e9c42b13c80c9ca4ea108e11708c5e8a5/shell/plugins/bar/widgets/Indicators.manifest.json):
  real `boolean` controls, so historyEnabled/historyPersist use booleans.

The open-incident IPC binds its ID to the panel opened by the host, explicitly
queries History/24h even when that view is already open, and inspects only after
that view's accepted query generation. Another monitor's reply cannot consume
the route. Expired
or cleared IDs produce a readable unavailable message. Helper failure is exposed
in runtime `status`; History and manual IPC remain available on installations
lacking this helper/action support. No event subprocess runs until a toast is
actually acknowledged; regular history uses the existing Python process.

## Evidence and gates

[Local implementation evidence](../qa/h3-local-evidence.md) records actual
results and limitations. Node pure-state regressions, clock-injected Python history/lease/command tests,
Linux probe protocol tests and Qt runtime doubles exercise producer/consumer
boundaries. They are not a desktop notification test. The safe
`scripts/qa-synthetic-probe.py` requires matching artifact-owned QA state, replaces
only input sources with protected synthetic app rows, and never scans/signals
real apps. It lets the later QA agent exercise live notifications without real
memory exhaustion. Demo remains separate and sends no notifications.

[GLHF QA/capture runbook](../qa/glhf-h3.md) contains exact-head installation and
restoration, notification/action/ownership/failure matrix, restart tests,
performance measurements, physical-monitor/suspend gates and release captures.
The gallery now includes original real-widget captures; see the
[GLHF report](../qa/h3-omarchy-evidence.md) for coverage and remaining limits. No additional
upstream code/art was copied; existing Argus/Diego Peter influence and Omarchy
credits remain accurate.
