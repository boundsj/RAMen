# Probe protocol

`raman_probe.py` is started by `RamanRuntime.qml` and talks over pipes. It reads
one command per line on stdin and writes one JSON object per line on stdout.
Diagnostics go to stderr. The probe exits when stdin closes.

The probe runs as two processes. Quickshell stops a `Process` with
`QProcess::kill()` (SIGKILL) on shell restart or reload, which nothing in that
process can intercept, so the process it starts only waits for a forked worker:
- The parent passes SIGTERM and SIGHUP to the worker and exits with the
  worker's status.
- The worker does all the work. It saves unsaved history before exiting when
  stdin closes, when its parent dies, when stdout is closed, or on
  SIGTERM/SIGHUP.

After a shell restart, the old worker's save completes and releases the writer
lock, and the new probe adopts the file.

Memory quantities are kB (1024 bytes). Pressure values are percent (PSI
`avg10`). Times are Unix wall-clock seconds; `monotonic` is producer monotonic seconds, scoped by `session`.

## Legacy commands and messages

These are unchanged plain-word lines:

| Command | Effect |
| --- | --- |
| `detail 1` / `detail 0` | Start/stop the per-app scan (at least one visible Memory subscriber / none) |
| `gpu 1` / `gpu 0` | Start/stop GPU client sampling (a visible Apps page with `gpuMetrics` `auto` / none). It runs only while `detail` is on too, never in a demo; see [GPU attribution](#apps-gpu-attribution-a3) |
| `refresh` | Emit a summary (and apps, if detail is on) now |
| `kill TERM\|KILL <id>` | Signal an app group from the last scan (the Memory page; see [`apps.kill`](#appskill) for the membership-checked form) |
| `demo <scene>` / `demo off` | Replay a scene from `docs/demo-scenes.json` / go live |

Outputs: `summary` every 2 s, `apps` every 2.5 s while detail is on, and
`killed` after a kill. `apps` holds the Memory page's top 12 groups with at
least 1 MiB of known footprint; since A1 it also names the inventory snapshot
that scan published (see [Apps backend](#apps-backend-a1)). Summary fields include `seq` (increasing per probe), `session` (random probe ID),
`monotonic`, `demo`, `breakBefore`, `dispatchOwner` and `dispatch` (see below).
Unreadable live sources now stay `null`, including PSI; clients must not turn
missing PSI into healthy recovery. History likewise preserves nulls.

## JSON-object commands

A line whose first non-space character is `{` is a JSON object. Use this form
for anything with parameters; the whitespace-split parser stays for the legacy
words above.

```json
{"command":"history.get","requestId":"h1","window":"1h"}
{"command":"history.clear","requestId":"h2"}
```

- `requestId` is optional. When present it must be a string of at most 64
  characters or an integer. Replies echo it so late replies can be discarded.
- Lines longer than 4096 bytes are dropped with a `too-large` error.
- JSON commands never run a process scan. The only one that signals apps is
  `apps.kill`, after re-reading the armed group's membership (identity and
  cgroup only, no memory or CPU) and re-checking each member. Storage
  cancellation terminates only the probe's owned storage worker. `apps.query`
  reads a cached snapshot; when that is stale it can only move the next regular
  scan forward.

Every reply carries `type`, `requestId` (or `null`), `schemaVersion` (`1`),
and `time`.

### Errors

```json
{"type":"error","requestId":"h1","schemaVersion":1,"time":1791063598.4,
 "code":"bad-window","message":"window must be one of 5m, 1h, 24h"}
```

| `code` | Meaning |
| --- | --- |
| `bad-json` | The line started with `{` but is not valid JSON |
| `bad-request` | Invalid `requestId` |
| `bad-window` | `history.get` window is not `5m`, `1h` or `24h` |
| `unknown-command` | Unrecognized `command` |
| `too-large` | Line over 4096 bytes (`requestId` is `null`) |
| `internal` | The command failed unexpectedly (details on stderr); the probe keeps running |

`message` is at most 200 characters.

### `history.get`

| Window | Points | Resolution | Source |
| --- | --- | --- | --- |
| `5m` | ≤150 | each 2 s summary | memory only |
| `1h` | ≤360 | 10 s buckets | persisted |
| `24h` | ≤360 | 240 s (four 60 s buckets merged) | persisted |

A reply never has more than 360 points. The reply is columnar: index *i* of
every array describes point *i*.

```json
{"type":"history","requestId":"h1","schemaVersion":1,"time":1791063598.4,
 "demo":false,
 "persistence":{"owner":true,"load":"ok","save":"saved","error":"","lastSavedAt":1791063560.1},
 "window":"1h","resolution":10,"from":1791059998.4,"to":1791063598.4,
 "metrics":["used","available","total","swapUsed","swapTotal","zramRam","psiSome10","psiFull10"],
 "units":{"used":"kB","psiSome10":"%","...":"..."},
 "t":[1791060000,1791060010],
 "count":[5,5],
 "covered":[10.0,10.0],
 "breaks":[false,false],
 "series":{"used":{"min":[...],"max":[...],"mean":[...],"last":[...]},"...":{}},
 "gaps":[{"start":1791061000.2,"end":1791061600.9,"reason":"suspend"}],
 "incidents":[]}
```

- `t` is the bucket start; for `5m` it is the sample time. For `5m` only, the
  reply also has `seq`, the summary sequence number of each sample.
- `count` is the number of summaries in the bucket. `covered` is the seconds
  they represent.
- `series[metric]` gives `min`, `max`, a time-weighted `mean` and the `last`
  value for each point. Each sample is weighted by the monotonic time since the
  previous sample; the first sample after a break counts as 2 s. The `mean`
  is the representative value. Show maxima as maxima, never as averages.
- `null` means no reading. An unreadable source (for example no PSI, or a zram
  device whose `mm_stat` cannot be read) is never reported as 0. `zramRam` is 0
  only when there is no zram device.
- `breaks[i]` is true when continuity broke before point *i*. Do not draw a
  line from point *i-1* to point *i*. Missing time between points is blank;
  never interpolate across it.
- `gaps` explains breaks. Gaps are listed when they end inside the window.

  | `reason` | Detected from |
  | --- | --- |
  | `stall` | No summary for more than 6 s of monotonic time |
  | `suspend` | The boot clock (CLOCK_BOOTTIME) ran ahead of the monotonic clock |
  | `clock` | The wall clock jumped forward, or went backwards (newer data is dropped), including across a restart |
  | `restart` | Saved history ends more than 6 s before this probe started |
  | `reboot` | Same, but the saved boot ID differs |

  Where no boot clock exists, a suspend shows as `clock`.
- `incidents` is the stored incident list that overlaps the window. It stays
  includes sustained live incidents. Each stored incident has an `id` that stays
  the same across saves, plus `start`, `end`, `severity` (`warn` or
  `critical`), `reason` (`pressure` or `used`), `summarySeq` and the
  `measurement` metrics. It also has a `context`: either
  `{"status":"not-observed"}`, `{"status":"observed","at":…,"apps":[{"name","kb"}]}`,
  or `{"status":"expired"}` after its observation leaves the 24-hour horizon.
  Expired start/upgrade payloads have `measurementStatus:"expired"` and null
  metric values; expired start payloads have no thresholds and summarySeq is null.
  `apps` holds at most five entries, and names are capped at 256 printable
  characters. Command lines, environment and PIDs are never stored.
- `persistence.load`:
  - `none` / `empty` / `ok`: no load attempt yet, no saved file, or the file was
    loaded.
  - `corrupt` / `oversized` / `incompatible`: the saved file was ignored,
    history started empty, and the next save replaces the file.
  - `unreadable`: the file exists but could not be read. It is left alone, and
    takeover is retried a minute later (see Persistence).
- `persistence.save`: `pending`, `saved`, `off` or `error` (with `error` text).
  `owner` is false for a probe that is not the history writer (see below).

### `history.clear`

```json
{"type":"history-cleared","requestId":"h2","schemaVersion":1,"time":1791063600.0,
 "demo":false,"persisted":true,"error":""}
```

Clears all RAMen history (samples, buckets, gaps, incidents) and starts a fresh
ring; live sampling continues. It never deletes anything outside the history
state directory. `persisted: false` with an `error` means the clear could not
be recorded on disk.

## Persistence

History lives in `$XDG_STATE_HOME/raman/` (normally `~/.local/state/raman/`).
The directory is mode 0700; files are 0600 and are replaced atomically.

| File | Purpose |
| --- | --- |
| `history.json` | The 10 s and 60 s rings, gaps and incidents. Schema-versioned, at most 2 MiB |
| `history.lock` | flock held by the one probe that writes history |
| `dispatch.lock` | One notification producer and boot stamp, independent of history persistence |
| `dispatch.json` | Boot ID and warn/critical cooldown timestamps only; no measurements/apps |
| `cleared.json` | `{token, clearedAt}` published by the last clear |

- **Saves:** at most once a minute, plus incident start/upgrade/end checkpoints, clear and exit, including a
  shell restart (see the two-process note above). Raw 2 s samples are memory
  only.
- **Retention:** 24 hours of samples and receipt payload, 50 incidents and 512 gaps.
  Active receipts use lastAt for query overlap and retain only stable ID and
  minimal lifecycle timing/status/severity/reason beyond 24 hours. Measurements
  and thresholds expire by start time, context by its own observation time,
  upgrade measurements by upgrade time. Expired evidence is explicitly labelled;
  queries and serialization enforce expiry even without a new sample. Normal
  disk expiry follows minute saves; stopped/disabled history is pruned on adoption.
  Closed receipts expire 24 hours after their end.
  Count limits/compaction remove closed receipts before active ones. If the file would exceed
  2 MiB, the oldest records are dropped first.
- **Bad files:** a corrupt, oversized or unknown-version file is ignored, never
  a crash. Write failures are reported in `persistence` and retried a minute
  later. Live telemetry is unaffected either way.
- **One writer:** Omarchy's built-in bar mounts one `Service.qml` runtime per
  shell; monitor widgets share its probe, history requests and IPC handler.
  A bar without the service API can start a private runtime per surface,
  and a reloaded shell's new probe can overlap the old one. Only the probe that
  holds `history.lock` writes. The others keep session-only history and retry
  the lock every tick. The first to get the lock adopts the saved file:
  - in each ring, saved buckets strictly before its own first bucket are kept,
    then its own buckets, so buckets stay unique and ordered even after a clock
    rollback. A saved bucket sharing the first slot is merged in only if it
    ends before the probe's first sample;
  - the boundary is judged from the saved data:
    - a saved gap spanning the probe's first sample is clipped there and keeps
      its reason;
    - a saved bucket that spans it means the old writer was still sampling, so
      there is no gap;
    - otherwise a `restart`/`reboot` gap starts at the last saved sample before
      it, if that is more than 6 s earlier;
    - saved data stamped later than the current wall clock gives a `clock` gap.
- **Clears across probes:** a clear on any probe writes `cleared.json`. Every
  probe reads that small file once per tick (usually absent) and, for a token it
  has not applied, drops data observed before `clearedAt`; the writer then saves
  at once. It checks again just before every save, including the final save on
  exit. `history.json` records
  the `clearToken` it reflects. A probe adopting a file whose token differs
  from `cleared.json` drops the data the clear covered.
- **Load failures:** if the file exists but cannot be read (I/O or permission
  error), or adopting it fails, the probe releases the lock rather than
  overwriting it, and retries a minute later.

Demo mode uses a separate in-memory history. Its fake clock advances exactly
2 s per demo summary. Demo history is never saved, and a clear during a demo
clears only the demo history. Live history keeps sampling during a demo and
continues afterwards with no demo readings in it.

## H2 UI requests and shell IPC

Each panel registers a unique subscription key with the runtime. The app scan
is enabled while **any** subscriber wants `detail`; removing one key cannot
remove another panel's subscription. History does not request detail. Its view
exists only while the History page is open and visible; destruction removes
its timers and reply listener. View request IDs contain a unique client key
and generation. Only the newest pending ID is accepted; switching windows,
clearing history, timeout or destruction invalidates older replies. Duplicate
replies are dropped. Windows remain independent across simultaneously open
panels; the shared window preference initializes newly created views only.
Runtime migration invalidates the old request, allocates a client key from the
destination runtime and queries the view's current window immediately. A
history reply naming a different window is also rejected.

Visible History views query at 2 s / 10 s / 60 s for 5m / 1h / 24h respectively,
with at most one timer-driven request pending per view. Unanswered requests
time out after 5 s and retry at the window cadence. Queries never scan apps
or signal. `history-cleared` makes visible views discard old data and reload.

Shell commands include `page memory|history|storage` to open that page on the
host-selected monitor (`storage` was added with the Storage page). The tested
Omarchy 4.0.4 host prefers an already-open RAMen copy; otherwise it uses the
focused monitor. Its bar permits one popup globally. `status` includes `runtime.mode` (`service` or `widget`),
`probePid` (waiting parent), attached widget count, subscription keys and
the combined `detail` flag. Existing open/close/toggle/refresh/demo/clearHistory
commands remain. Process commands still belong solely to the Memory app list.

## Synthetic History scene

`demo history` uses the checked-in `docs/demo-scenes.json` scene. Its optional
`timeline` contains phases with `minutes`, `from`/`to` metric maps for linear
ramps, and `set` for constant values (`null` is unreadable). Used RAM is derived
from total minus available. `pauseMinutes` leaves a gap; `reason: suspend`
advances the synthetic boot clock faster than monotonic time, while other
pauses yield a stall. Phases replay normal 2 s ingestion into demo-only rings;
the final fake time is anchored to entering demo, then advances 2 s per summary.

Optional `incidents` specify `startMinute` and `endMinute` offsets from the
timeline start, severity/reason, and `observedApps` (top fake app count). The
seed stores only synthetic metrics and sanitized fake app context, with stable
demo IDs and no fabricated historical threshold. This is fixture generation,
not a live incident producer or `incident.record` implementation. It performs
no process scans, signals, notifications or production writes. Existing
green/yellow/red demos without a timeline still start with an empty demo ring.

## H3 additive commands

### `history.configure`

```json
{"command":"history.configure","requestId":"config:1","enabled":true,"persist":false}
```

Both fields must be booleans (`bad-settings` otherwise). Reply type
`history-configured` echoes `enabled` and `persist`. Collection disabled clears
the session view and stops samples/incidents/toasts without deleting disk state.
Persistence disabled starts fresh session-only history without loading/saving
history.json; switching out first flushes the previous persisted session.
Re-enabling persistence can adopt saved history, without replay. `history.get`
adds `enabled`; disabled/session-only replies use persistence `save: off`.
The runtime starts the probe with `--defer-history` and configures only after
widget settings arrive, preventing initial unwanted production reads/writes.
Live summaries carry `historyConfigId`, the most recently applied configuration
requestId (null before configuration or in a standalone probe). Production waits
for the matching `history-configured` reply and accepts only summaries bearing
that ID. Buffered deferred/older-configuration summaries cannot start dwell;
collection/persistence transitions invalidate both pending production and acks.
Standalone probes retain history-on defaults for existing lifecycle tools.

Explicit `history.clear` still erases saved state in disabled/session-only mode:
an idle file is cleared without loading it, otherwise its writer applies the
marker on next tick/exit. Clear does not reset notification cooldowns. Clear in
demo affects only demo state.
The writer retains pending disk erasure even when marker observation precedes
its persistence tick; failed replacements retry on subsequent ticks/exit.
A follower's `persisted:true` Clear acknowledges the durable marker, not a
synchronous history.json replacement by another process. Writer replacement
failures remain visible in its persistence status.
Every live probe follows erase markers regardless of collection/persistence
settings, without loading history.json. Applying another probe's marker emits
an unsolicited `history-cleared` with `requestId: null, remote: true`, invalidating
view queries, pending acknowledgements and the removed active lifecycle.

### `incident.record`

```json
{"command":"incident.record","requestId":"i:1:2","epoch":"dispatch-epoch",
 "key":"probe-session:pending-seq","action":"start","summarySeq":1042,
 "startSeq":1037,"severity":"critical","reason":"pressure","notify":true,
 "thresholds":{"warnPercent":75,"criticalPercent":90,"warnPressure":5,"criticalPressure":20}}
```

This is a runtime producer command, never a historical action. `summarySeq`
must be a positive integer referencing this probe's retained raw sample;
`startSeq` (start only, defaults to summarySeq) must exist and be no later.
Measurements/context come from Python, not a QML measurement payload. `epoch`
must match the current dispatch owner. `key` is ≤64 characters, nonempty; it
becomes the stable receipt ID (defaults to session:seq). Valid actions:

| Action | Effect | Checkpoint / possible toast |
| --- | --- | --- |
| `start` | One receipt, start from first qualifying seq | immediate / once if notify |
| `extend` | Update lastAt, same receipt | minute save / never |
| `upgrade` | Warning→critical once; own measurement/time | immediate / once if notify |
| `recover` | Set end, status recovered | immediate / never |
| `interrupt` | Set end at last observed sample, status interrupted | immediate / never |

For an interruption whose raw reference has expired during a long gap, the
existing receipt's lastAt closes continuity. No new reading is fabricated.
Thresholds whitelist four values in 1–100; absent/invalid ones remain null.
Severity is `warn|critical`, reason `pressure|used`. Only an already active
detail subscription and monotonic snapshot age ≤2×DETAIL_INTERVAL (5 s) can
supply context. JSON commands never call a scan or signal.

```json
{"type":"incident","requestId":"i:1:2","schemaVersion":1,"time":1791063600,
 "demo":false,"epoch":"dispatch-epoch","action":"start","notify":true,
 "persisted":true,"policySaved":true,"error":"","units":{"used":"kB","psiSome10":"%"},
 "incident":{"id":"probe-session:pending-seq","start":1791063590,"end":null,
 "lastAt":1791063600,"status":"active","severity":"critical","reason":"pressure",
 "summarySeq":1037,"measurement":{"used":7600000,"available":620000,"total":8220000},
 "thresholds":{"warnPercent":75,"criticalPercent":90,"warnPressure":5,"criticalPressure":20},
 "context":{"status":"not-observed"}}}
```

The reply also carries top-level `measurement` from summarySeq for toast text;
the receipt's measurement remains the first qualifying start sample. Replies
carry `notificationContext`, separately validated against current detail
subscription/snapshot age, using the same bounded context shape. Toasts use
this field only; they never reuse historical `incident.context`. The receipt's
original context is not refreshed by an upgrade and expires by observation time
after 24 hours. App names are escaped for the host's StyledText renderer at the
toast boundary. Delivery rechecks the UI's
current detail subscription and the observation timestamp's age, so a queued
notification drops context that expired or whose panel closed. All
measurement keys follow the metric list; sample values may be null. An
upgrade adds `upgrade: {at, measurement}` to the same receipt. The reply's
`persisted` acknowledges this checkpoint, not an old successful save; false
includes session-only/non-writer/failed-save cases. `policySaved` acknowledges
the cooldown reservation separately. Runtime toasts follow a successful
incident reply even on history write failure, with “not saved”; error/missing
acknowledgements do not toast. Extend replies say persisted false because they
make no immediate checkpoint. A duplicate start reserves no second toast;
a repeated upgrade fails `bad-upgrade`. Parsed unfinished restored receipts
end interrupted at lastAt and never notify.

Additional bounded error codes: `bad-settings`, `bad-incident`, `bad-seq`,
`unknown-seq`, `unknown-incident`, `closed-incident`, `bad-upgrade`,
`history-disabled`, `not-owner`, `demo-only`. Existing error envelopes and bounds
apply. Demo incident commands are rejected; its canned receipts are read-only.

### Dispatch metadata and acknowledgement invalidation

Summary `dispatch` is `{owner, epoch, blocked, noticeElapsed, error}`.
`noticeElapsed` has warn/critical seconds since reservations in this boot, or
null. A follower cannot produce incidents. A new owner's epoch invalidates old
acknowledgements. QML initializes cooldowns from elapsed time; samples remain
session-scoped. Restarts/takeovers with a same-boot dispatch lock stamp suppress
each dimension until its measured value falls below the warning hysteresis
boundary, even with persistence off or failed checkpoints. Missing PSI leaves
pressure suppressed; low known RAM re-arms RAM so later RAM breaches can qualify
independently. An old-boot stamp does not suppress a new boot; unknown/legacy
stamps remain conservative. This suppresses launch-time sustained alerts.
`dispatch.lock` holds boot identity only, stamped in place to preserve flock.
`dispatch.json` holds only boot identity and two timestamps; it is not history
and contains no app context. No restored receipt is replayed as a live event.

QML accepts notifier replies only for a pending request, current epoch and
configuration generation within five seconds, with current ownership and
non-demo/enabled policy. Collection/persistence/threshold changes, alert-mode
changes, clear, demo and restart invalidate queued notifications. Delivery is
at most once: a crash after reservation but before desktop delivery may lose
that toast. Helper exit failure is reported by runtime status, not retried.

### User IPC

`omarchy-shell boundsj.raman openIncident <id>` selects History/24h and inspects
by ID through that same host routing. If expired/cleared, it explains the missing
receipt. Omarchy v4.0.4's notification helper is invoked with discrete
`--exec omarchy-shell boundsj.raman openIncident <id>` argv; manual IPC is the
fallback on unsupported helper/action versions. `status.runtime` includes
history settings, phase/blocked state, incident/helper errors and dispatch
metadata. See [incident design](design/incidents.md) for primary API revisions
and [shared contracts](design/shared-contracts.md) for implemented Storage and
future Apps ownership/cancellation/state-cache requirements.

## Storage backend

These are the commands behind the Storage page, delivered in S1 (backend), S2
(page, scope bytes and path actions) and S3 (UI failure and recovery states;
the wire contract did not change in S3). The page compares selected-capacity
`identity` against displayed snapshot `identity` as well as relying on backend
query/action validation. Missing/remounted/unsupported identity invalidates that
client's outstanding generation and rows; transient capacity errors preserve
active scan/cancel outcomes. Terminal cancellation survives cache reconciliation
with no snapshot. Helper startup retries capacity/cache discovery only; scans
remain explicit. Accepted verification is summarised in the
[Storage design](design/storage.md#verification-record-and-remaining-coverage);
remaining hardware checks are in [final QA](qa/final-qa.md).

These requests are separate from `omarchy-shell` methods. No storage
job is launched at probe startup, by Memory/History, or by ordinary `refresh`.
Only explicit `storage.scan` recurses. Demo mode does not redirect storage
requests to fake paths: test clients must use their own directories/cache.

```json
{"command":"storage.mounts","requestId":"m1","clientId":"storage-1","generation":1,"offset":0,"limit":50}
{"command":"storage.mounts","requestId":"m2","clientId":"storage-1","generation":1,"path":"/home/example/My Projects"}
{"command":"storage.cached","requestId":"s0","clientId":"storage-1","generation":1,"path":"/home/example/My Projects"}
{"command":"storage.scan","requestId":"s1","clientId":"storage-1","generation":2,"path":"/home/example/My Projects"}
{"command":"storage.cancel","requestId":"s2","clientId":"storage-1","generation":3,"scanId":"32-character-owned-scan-id"}
{"command":"storage.children","requestId":"s3","clientId":"storage-1","generation":3,"snapshotId":"32-character-snapshot-id","offset":0,"limit":50,"filter":""}
{"command":"storage.leave","requestId":"s4","clientId":"storage-1","generation":4}
```

`clientId` is required, 1–64 printable characters. `generation` is a required
integer in 0..2^53−1, increasing when path/snapshot/page ownership changes.
At most 64 distinct clients are retained per probe lifetime. Cancel and leave
must advance the generation. Older generations receive `stale-generation`;
already-running/queued old replies are discarded. Cancel requires the scan ID
owned by that client and also works while that scan is queued. Leave cancels
all of the departing client's workers/queued requests. It is S2's required
page-close/runtime-migration hook; closing one client does not affect another.
Use unique request IDs and accept only outstanding current IDs/generations.
Controller command-validation errors echo request/client/generation but may omit
`scanId`; a correlated error without it is terminal for that command. Explicit
mismatching scan IDs remain invalid. Cancel after a completed and reaped scan
returns `unknown-scan` without a scan ID. This does not prove cancellation or
failure: clients reload `storage.cached` to learn the authoritative selection
and retain any reported activation durability uncertainty.

The existing 4096-byte command limit applies, including JSON encoding. Paths
must be absolute, nonempty, NUL-free and at most 4096 filesystem bytes. Scope
resolution stays in the worker. `scanId`, `snapshotId` and `nodeId` are backend
32-character lowercase hexadecimal IDs; the examples above contain descriptive
placeholders. Scan IDs are created in the immediate queued reply. Node IDs
remain stable for the same root/filesystem identity and path bytes; always
query with the snapshot ID too. Navigation uses IDs, never display labels.

Accepted jobs immediately return `storage-progress` with `status:"queued"`
and the client/generation/request/scan IDs; then one terminal response or error
arrives asynchronously. A scanner and one short-query worker can run together;
at most eight requests wait. Discovery, statvfs, identity/cache validation and
SQLite queries run in short workers, never in the summary/stdin loop. Reaping
also remains asynchronous. Progress `status:"scanning"` is at most twice per
second and contains entries, unreadable/excluded/changed counts and elapsed
seconds. The total entry count is unknown until completion; no percent is sent.

| Request | Terminal reply | Data |
| --- | --- | --- |
| `storage.mounts` | `storage-capacity` | `units:"bytes"`, paged `mounts`, offset/total/nextOffset; with path, selected scope/identity and capacity |
| `storage.cached` | `storage-result` | `status:"ready"`, snapshot ID/metadata, or `status:"unscanned"` and null snapshot ID; never starts a scan |
| `storage.scan` | `storage-result` | `status:"ready"`, scan/snapshot IDs, metadata and bytes units after valid activation |
| `storage.cancel` | `storage-progress`, then `storage-result` | active scan ACK `cancelling`; reconciled `cancelled`, `committed` or `uncertain` |
| `storage.leave` | `storage-progress`, then `storage-result` | active scan ACK `leaving`; reconciled `left`, `committed` or `uncertain` |
| `storage.children` | `storage-children` | current node, snapshot metadata, rows/segments/Other, literal filter and continuation offsets |

Mount rows contain mount ID, safe display `path`, base64 `pathBytes`, filesystem,
device (not a unique physical drive), `excluded` reason, nullable capacity and
optional error. Pseudo/network/RAM-backed/unknown mounts have null capacity.
Overmounted entries are labelled rather than being attributed another mount's
capacity. Capacities use `f_frsize` (fallback `f_bsize`): `totalBytes`,
`freeBytes` (physical), `availableBytes` (user), `usedBytes` (total−free),
`reservedBytes` (free−available), plus `readOnly`. Values remain nonnegative;
multiple mounts/subvolumes must not be summed as machine capacity.

Snapshot metadata includes schema/snapshot/root-node IDs, scope display/base64
bytes/scope key, root device/inode, boot ID, filesystem ID, unique mount ID and
root birth time, capture time,
`allocationMethod:"st_blocks*512"`, complete/partial coverage, entry/counter
totals, at most 32 warnings, allocated/known bytes and scan limits. Do not
publish this private path inventory or put it in History. Current root and
mount identity are checked again for each cached/children response; age is
limited to seven days. Snapshot ID alone never authorizes a live file action.

Each row/segment contains node/parent IDs, printable name, kind
(`directory`, `file`, `symlink`, `special`, `unknown`), own allocated bytes,
own apparent bytes, known subtree allocated bytes, nullable subtree allocated
bytes, coverage, reason, shared-link and hidden flags. Own allocation is null
for entries whose own stat is unavailable or excluded. A stat-success/open-denied
directory retains measured own allocation; its descendants remain unknown.
Partial subtree allocation is null while
`knownAllocatedBytes` remains a lower bound. Shared hardlink references have
zero attributed allocation, not zero apparent size. Directory own allocation
is in `node.ownAllocatedBytes`; `childrenKnownAllocatedBytes` and segments cover
children only. Filesystem usage and these scoped allocations do not reconcile
all used space or predict bytes freed by deletion.

`storage.children` defaults to the snapshot root; `nodeId` can select a stored
directory by byte-safe identity. `offset` is 0..200000 and `limit` 1..50.
`filter` is a literal case-insensitive display substring, at most 128 printable
characters; `filterScope:"direct-children"` describes its scope. Ordering is
known allocation descending, then byte name and ID. Rows are a page; segments
represent the first 59 matching entries independently of the page. If more
exist, Other supplies the remaining count, known/nullable allocation, coverage,
filter and starting offset, making at most 60 segments including Other. Browse
Other by requesting the same filter at its offset. Every line, including all
metadata and escaped labels, is <=128 KiB with newline; page/segment lengths
can reduce to fit, and `nextOffset` follows the actual rows supplied.

Cancel/page leave invalidates ownership before ready worker messages are
processed. Workers need current-generation authorization to publish a prepared
private candidate; authorization and receipt publication never select it.
Only atomic replacement of the bounded authoritative activation catalog is
commit. Activation order is explicit and independent of wall-clock timestamps.
Readers and GC follow that catalog; unpublished/evicted IDs fail with
`snapshot-not-active` even if a private DB exists.

Active scan cancel/leave immediately acknowledges `storage-progress` with
`status:"cancelling"`/`"leaving"`, preserving the request/client/generation/scan
identity. It does not claim previous retention or completed cancellation. After
TERM, KILL escalation at 200 ms and asynchronous reap, a bounded owned worker
reconciles the catalog off the probe loop. Terminal `storage-result` carries
`status:"cancelled"`/`"left"`, `commitState:"not-committed"` and
`previousSnapshotRetained:true` only if prior data is still selected. If commit
already won it returns `status:"committed"`, `commitState:"committed"`, actual
`snapshotId`, `previousSnapshotRetained:false` and `cleanupPending:false`.
Recovery failure returns `status:"uncertain"`, `commitState:"unknown"`, the
currently selected ID, `cleanupPending:true` and a stable failure code.
Queued scans which never started need no reconciliation and can return terminal
cancellation immediately. Normal ready scans carry `commitState:"committed"`.
Repeated cancel/leave during queued/running/reaping recovery keeps the original
candidate/prior identity and acknowledges pending work for the latest request.
Its terminal result uses that request/generation. A normal generation advance
silences old delivery without discarding mandatory recovery. Worker deadlines
and TERM/KILL/reap apply independently of which generation may receive replies.
Unexpected scanner exit or timeout also reconciles first: pre-commit failure
returns an error with retained selected prior data; post-commit success returns
the actual committed ID. Old-generation results are suppressed.

Candidate/catalog/journal contents and preparation are durable before atomic
catalog replacement. Previous inventory files remain until commit sync succeeds;
successful cleanup immediately enforces at most three physical/queryable scopes.
Orphan receipts never become selected, including when the next scan fails.
Persistent durability/cleanup failure blocks a new candidate until repair.
Cached/children replies include `activationDurability:"durable"` or `"pending"`
while an interrupted post-commit journal awaits resolution. Immutable-file
fingerprints/hash permit bounded validation reuse. Linux 6.8 unique mount IDs
and root birth time are required; unavailable safe identity fails closed.
EOF/parent death gets at most 900 ms of cleanup; uninterruptible kernel I/O may
finish dying later without blocking the probe. A later exclusive writer resolves
its journal before creating another candidate.

Storage errors echo client/generation/scan ID where applicable, with the usual
schema/time/request and <=200-character description. Stable codes:
`bad-request`, `invalid-path`, `unsupported-filesystem`, `unsupported-platform`,
`mount-limit`, `identity-unavailable`, `missing-volume`, `missing-root`, `root-changed`, `unknown-node`,
`unknown-scan`, `stale-generation`, `busy`, `cache-busy`, `unsafe-cache`,
`invalid-snapshot`, `stale-snapshot`, `snapshot-not-active`, `commit-uncertain`,
`cleanup-failed`, `scan-entry-limit`, `scan-depth-limit`,
`scan-time-limit`, `cache-size-limit`, `worker-timeout`, `worker-failed`,
`owner-timeout`, `storage-unavailable` and `too-large`. Internal limits remain
constants: 200000 entries, 128 levels, 120 seconds, three scopes, 128 MiB and
seven-day reuse; queries/discovery time out at 10 seconds. Storage settings are UI/session preferences; no cleanup/file execution commands
are implemented. See [design](design/storage.md).

### S2 path actions and scope bytes

Any scope request can supply `pathBytes` (base64 filesystem bytes) instead of
`path`. The controller validates an absolute, NUL-free path within the existing
4096-byte command/path limits and converts it losslessly for its worker. This
is how volume selection avoids turning printable display labels into paths.
`storage-children` adds `breadcrumbs: [{nodeId, name}]`, ordered scope-root to
current directory; navigation always sends stored IDs with the snapshot ID.

```json
{"command":"storage.action","requestId":"s:panel:8","clientId":"storage-1","generation":4,"snapshotId":"0123456789abcdef0123456789abcdef","nodeId":"abcdef0123456789abcdef0123456789","action":"copy"}
```

`action` is `open|copy` only. This short-worker command uses the same keyed
client/generation/outstanding-request rules as queries. It first validates the
active snapshot/current root, then walks byte components from an opened root
without following symlinks, comparing each node's stored device/inode/ctime.
Directories are opened with no-follow and mount validation. A file, symlink or
special entry opens its validated **parent directory**, never the entry itself.
Open uses `org.freedesktop.portal.OpenURI.OpenFile` with a pinned **directory**
FD in a GUnixFDList (D-Bus SCM_RIGHTS), never a process-relative `/proc/self/fd`
path or the selected file. System GIO is loaded through stdlib ctypes. The method
receipt only names a request; `Response(0)` is required for `opened`. Missing
session bus/GIO/portal backend, refusal, cancellation and timeout report
`action-unavailable`. Unfinished requests are closed, with a bounded response
wait of five seconds. `OpenDirectory` is deliberately not used because that API
opens the *containing* directory rather than the supplied directory itself.

Copy sends validated UTF-8 path bytes on stdin to fixed
`wl-copy --type text/plain;charset=utf-8` argv. No shell parses a path. Clipboard
helpers have a five-second timeout, inherit the owned worker process group and
receive no probe/history descriptors. Leave/EOF/owner death clean unfinished
owned helper descendants; TERM escalates within 200 ms, while the unreaped group
leader anchors its PGID. Successful portal file-manager dispatches belong to
the desktop, outside that group, and survive page leave/probe exit. A portal
response does not attest an external file manager's later path navigation.
No privileged/file-content/deletion action exists.

Children rows/nodes also carry `actionIdentity` (legacy S1 lacks identity and
requires a rescan) and `copyRepresentable` (full stored path is valid UTF-8).
These are representation prerequisites, not proof of current live identity or
desktop availability; every actual action revalidates in its worker.

The `storage-action` terminal reply echoes snapshot/node/action, base64
`pathBytes`, and `status: opened|copied`. It does not return a lossy display label
as an actionable path. Stable errors add `node-changed` (rescan after identity
change) and `action-unavailable` (missing/refusing desktop helper, non-UTF8
clipboard path, or legacy snapshot without action identity). New schema-1
snapshots include a private optional `action_identity` table. Existing S1
snapshots remain queryable and disclose the rescan requirement on action;
absence of identity never authorizes a live action.

Storage reply routing is separate from History/incident errors. Each visible
panel retains its own storage client across page visits; generations advance
on scope/navigation/filter/cancel/leave. Outstanding request IDs never repeat,
progress ACKs remain outstanding, terminal replies are consumed once, and stale
client/generation/request/scan/snapshot/node/filter/offset mismatches are ignored.
A page departure sends leave to its old runtime before migration/destroy.

## Apps backend (A1)

Recent CPU accounting and a complete, cached app inventory for the Apps page.
Design and semantics: [Apps design](design/apps.md). The page's own commands
(query focus, `apps.kill`, `apps.details`) are in [Apps page actions
(A2)](#apps-page-actions-a2). GPU fields are described in [GPU attribution
(A3)](#apps-gpu-attribution-a3).

### Detail scan and the `apps` message

The `detail` subscription drives one per-process scan every 2.5 s, shared by
Memory and Apps. With detail off nothing per-process is read and the CPU
baselines and inventory are dropped, so reopening starts with a warm-up.
Demo scenes publish their fake rows as a demo snapshot (`demo: true`); they
never scan. CPU is `unavailable` unless the scene names `cpu.onlineCpus`, as
the `apps` scene does (then `available`, with the scene's own rates).

Each scan publishes a snapshot and emits the legacy message with additive
fields:

```json
{"type":"apps","apps":[...12 rows...],"snapshot":42,"sampledAt":1791063600.2,
 "inventory":{"processes":312,"groups":97,"complete":true,"incompleteReason":null,
  "memoryUnavailable":2,"processLimit":8192,"groupLimit":4096},
 "cpu":{"status":"available","reset":null,"onlineCpus":8,"clockTicks":100,"anomalies":0},
 "coverage":{"memoryPartial":1,"memoryUnavailable":1,"cpuPartial":3,"cpuUnavailable":0}}
```

- `snapshot` increases for the probe's lifetime; `sampledAt` is Unix seconds
  at the end of that scan.
- `inventory.complete` is false when `incompleteReason` is `process-limit`
  (more than 8192 owned processes), `group-limit` (more than 4096 groups; the
  kept groups alternate between the memory and CPU rankings) or
  `proc-unreadable`. Never treat an incomplete snapshot as a full process list.
- `cpu.status` is `warming-up` when `cpu.reset` names why baselines were
  dropped in this scan: `start` (first scan, reopen, restart), `gap` (more than
  7.5 s since the previous scan), `suspend` (boot clock ahead of monotonic by
  more than 1 s) or `topology` (online CPU list changed). `unavailable` means
  no `SC_CLK_TCK`. `anomalies` counts processes rebaselined this scan for a
  backwards counter or an impossible rate.
- `coverage` counts groups whose memory or CPU reading is partial or absent;
  A3 adds `gpuReadings`, `gpuPartial` and `gpuOverlap` (rows whose
  `gpuMemoryKb` is a possibly overlapping client-reference sum).

### Group rows

`apps` rows and `apps.query` rows share these fields (`apps` rows have no
`sampledAt`):

```json
{"id":"app-Hyprland-google\\x2dchrome-2.scope","generation":"5c1f0e9b2a7d4e10",
 "kind":"app","name":"Chrome","host":"","count":41,"protected":false,
 "pss":2900000,"swap":120000,"memoryStatus":"partial",
 "memoryCoverage":{"measured":40,"members":41},
 "cpuCorePercent":175.0,"cpuMachinePercent":21.875,"cpuStatus":"available",
 "cpuCoverage":{"measured":41,"members":41},"sampledAt":1791063600.2}
```

| Field | Meaning |
| --- | --- |
| `id`, `kind`, `name`, `host`, `count`, `protected` | Unchanged grouping: app scope, terminal `session` (with `host`) or single `process`. `count` includes members whose memory is unreadable. Names are printable, ≤256 characters |
| `generation` | Opaque hash of the group's `(pid, start ticks)` membership; changes when members join, exit or are reused |
| `pss`, `swap` | kB; subtotal of members with readable `smaps_rollup`; `null` when none is readable. Never a fake zero |
| `memoryStatus` | `available`, `partial` (subtotal is a lower bound) or `unavailable` |
| `cpuCorePercent` | % of one logical CPU (100 = one busy core), subtotal of members with a rate; `null` while none has one. Unrounded |
| `cpuMachinePercent` | `cpuCorePercent` / online logical CPUs; `null` with it |
| `cpuStatus` | `available`, `partial` (some members warming up), `warming-up` (none measured yet) or `unavailable` |
| `memoryCoverage`, `cpuCoverage` | `{measured, members}` |

CPU uses `utime + stime` of each process (never `cutime`/`cstime`), baselines
keyed by `(pid, start ticks)`, `SC_CLK_TCK` and the monotonic time between two
reads of that process. A first reading, reused PID, counter rollback or rate
above 1.1× all online CPUs reports `null` instead of a spike. A rescan less
than 1 s after the previous one reuses the last full-interval rate. Processes
of this user are included even when `smaps_rollup` is denied (non-dumpable;
their real, effective and saved uids must all match). Kernel threads and
zombies are skipped.

The Memory page keeps ranking by known footprint (`pss + swap`) with the
1 MiB / top-12 cut. A group with no readable memory never appears there, and a
`partial` row is labelled as a lower bound. Incident `context.apps` entries
from such a row carry `"partial": true`; `kb` is then a known lower bound.

### `apps.query`

```json
{"command":"apps.query","requestId":"a:apps-1:7","generation":7,
 "query":"chrome","sort":"cpu","offset":0,"limit":50,"snapshot":42}
```

| Field | Rule |
| --- | --- |
| `query` | Optional string, ≤256 characters. Literal case-insensitive substring of `name` or `host`; a query of 1–10 ASCII digits also matches a member PID exactly (other Unicode digits such as `²` are literal text only). Command lines are not searched |
| `sort` | `memory` (known footprint, default), `cpu` (`cpuCorePercent`) or `gpu-memory` (`gpuMemoryKb`, A3). `null` values sort last; ties break by `id` |
| `offset`, `limit` | Integers 0..4096 and 1..50 (default 0 and 50) |
| `snapshot` | Optional positive integer: page within that retained snapshot. Omit for the newest |
| `generation` | Optional integer 0..2^53−1, echoed so clients drop older replies |

The reply is one `apps-page` line of at most 128 KiB:

```json
{"type":"apps-page","requestId":"a:apps-1:7","schemaVersion":1,"time":1791063601.0,
 "generation":7,"status":"ready","stale":false,"demo":false,"snapshot":42,
 "sampledAt":1791063600.2,"query":"chrome","sort":"cpu","offset":0,"limit":50,
 "total":3,"nextOffset":null,"inventory":{...},"cpu":{...},"coverage":{...},
 "units":{"pss":"kB","swap":"kB","cpuCorePercent":"% of one logical CPU",
  "cpuMachinePercent":"% of online logical CPUs","sampledAt":"Unix seconds"},
 "rows":[...]}
```

- `status` is `ready`, `pending` (detail is on but the first scan has not
  finished) or `inactive` (no detail subscription; nothing is scanned for the
  query). Non-ready replies have no snapshot and no rows.
- `total` counts matches in that snapshot. `nextOffset` follows the rows
  actually supplied (fewer than `limit` only if the byte bound required it),
  or is `null` at the end.
- `stale` is true when the snapshot is more than 5 s old; the probe then runs
  its next scan immediately. A query never starts a second scan.
- The newest four snapshots (about 10 s) are retained for pinned paging. A
  pinned snapshot that has been dropped returns error `snapshot-expired` (with
  `generation` and `snapshot`); restart at offset 0 without `snapshot`.

Errors use the usual envelope: `bad-query`, `bad-sort`, `bad-page`,
`bad-request` (snapshot or generation not an allowed integer) and
`snapshot-expired`. The generation is validated first and every other error
echoes it (`null` when omitted), so `Runtime.acceptsAppsReply` accepts the
error for its pending request; an invalid generation is not echoed. Clients use `a:` request IDs (`Runtime.appsRequestId`);
the runtime routes `apps-page` replies and `a:` errors to `appsMessage`, never
to History, Storage or the incident producer.

## Apps page actions (A2)

The Apps page adds three things: a `focus` field on `apps.query`, the
membership-checked `apps.kill`, and on-demand `apps.details`. The legacy
`kill TERM|KILL <id>` line is unchanged and still used by the Memory page.

### Query `focus`

`"focus": "<group id>"` (optional, printable, ≤512 characters) adds a `focus`
object to the `apps-page` reply, wherever that group ranks:

```json
"focus":{"id":"app-x.scope","present":true,"generation":"5c1f0e9b2a7d4e10","rank":63,"row":{...}}
```

`present` is false (with `generation`/`rank`/`row` null) when the group is not
in that snapshot. `rank` is its index among the query's matches, or `null`
when the search filters it out. The page sends its armed group (or else its
selected group) as `focus`, so a changed membership or a vanished group is
noticed even while the shown rows are frozen or the group moved to another
page. An invalid `focus` is `bad-request`, echoing the generation.

### `apps.kill`

```json
{"command":"apps.kill","requestId":"k:apps-1:3","generation":3,"id":"app-x.scope",
 "membership":"5c1f0e9b2a7d4e10","signal":"TERM"}
```

| Field | Rule |
| --- | --- |
| `id` | Group ID from an `apps-page` row; printable, 1–512 characters |
| `membership` | The `generation` of the row the user armed (string, 1–64 characters) |
| `signal` | `TERM` or `KILL` |
| `generation` | Optional client integer 0..2^53−1, echoed |

The probe takes the group from its newest scan (only while a detail
subscription is active) and refuses unless that scan's membership generation
equals `membership`. It never retargets a changed group. Because that scan can
be up to 2.5 s old, the probe then rebuilds the group's membership from `/proc`
at confirmation: one pass over this user's processes reading only `stat` and
`cgroup` (no memory or CPU reads, once per confirmed `apps.kill`, never per
query), grouped exactly as a scan groups them. A member that joined, exited,
was reused or moved to another group or session since the scan makes that
generation differ, and the kill is refused as `stale-membership`. If not every
process could be considered (`/proc` unreadable, or more than the scan's
8192-process limit) the reply is `unverified` and nothing is signalled. The
same holds for any single process whose ownership, identity or cgroup cannot
be established: an unreadable or malformed `stat` or `cgroup` of one of this
user's processes, or an unreadable or malformed `status` of a root-owned entry
(a non-dumpable process of the user looks root-owned) might hide a member, so
it is never treated as absent. Only processes that are positively not members
are left out: exited ones (including one exiting mid-read), kernel threads,
zombies, and entries owned by another user, which are judged by their owner
alone without reading their files. Each process's directory stays open while
its files are read, so they all describe the same process. A group or member
that is protected now refuses the kill.

The verified members are then signalled one at a time. A preflight first
refuses the whole group, signalling nothing, if any member is protected. Then
each member is pinned with a pidfd, re-checked (same start time, not a zombie,
still this user's by real, effective and saved uid, not protected: a protected
name, or the probe's own worker or waiting parent) and signalled through that
pidfd, so a PID reused after its check cannot receive the signal. Exited or
reused members are skipped; members no longer owned count as failed; a member
that became protected after the preflight stops the remaining signals
(`error` `protected`). If `pidfd_open` fails for any reason other than "no
such process" or a kernel without it (`ENOSYS`), for example descriptor
exhaustion, that member fails instead of being signalled by number.

Limits: where `/proc` is mounted `hidepid=noaccess` (`hidepid=1`), a non-root
probe cannot read root-owned processes' `status`, so every `apps.kill` is
`unverified` there; with `hidepid=invisible` (`hidepid=2`) the kernel hides the
user's own non-dumpable processes entirely, so neither a scan nor this check
sees them. The membership check and the signals are milliseconds apart, not
atomic. A process forked into the group in that interval is not signalled
(nothing outside the verified set ever is), and one that exits in it is
skipped. On kernels without `pidfd_open` (before Linux 5.3) each member is
re-checked immediately before `kill(2)`, which leaves only that system call's
own instant for a PID to be reused.

Every outcome is one `killed` reply (so legacy kill bookkeeping still works):

```json
{"type":"killed","requestId":"k:apps-1:3","schemaVersion":1,"time":1791063602.0,"generation":3,
 "id":"app-x.scope","signal":"TERM","membership":"5c1f0e9b2a7d4e10","currentMembership":"5c1f0e9b2a7d4e10",
 "code":"sent","error":"","sent":41,"failed":0,"skipped":0,"demo":false}
```

| `code` | Meaning |
| --- | --- |
| `sent` | At least one member was signalled (`failed`/`skipped` count the rest) |
| `stale-membership` | The group changed since it was armed (in the newest scan or in `/proc` at confirmation); nothing signalled. `currentMembership` names the new generation |
| `unverified` | The current membership could not be established completely (`/proc` unreadable, over the process limit, or a process whose ownership, `stat` or `cgroup` was unreadable or malformed); nothing signalled |
| `gone` | Not in the newest scan, no scan running, or every member had exited |
| `protected` | The group, or one of its members now, is protected; nothing signalled (or, if a member became protected mid-way, `sent` with `error` `protected` and the rest not signalled) |
| `failed` | Members remained but none could be signalled (`error` says why, for example not owned) |
| `demo` | Demo mode: the fake row was removed; nothing was signalled |

Malformed requests are `bad-request` errors (echoing a valid `generation`).
The probe rescans about 0.6 s after any `apps.kill`, as after a legacy kill.
The two-step arm/confirm, the 4-second arm expiry and the TERM-then-KILL
escalation are the client's; the backend only ever signals what one confirmed
request names.

In demo mode the scene's fake rows have fake memberships (`demo-N`); a kill
with a matching membership removes only that row, a mismatch is
`stale-membership`, and a protected fake row is `protected`.

### `apps.details`

```json
{"command":"apps.details","requestId":"d:apps-1:2","generation":2,"id":"app-x.scope"}
```

Reply (one line, at most 128 KiB):

```json
{"type":"apps-details","requestId":"d:apps-1:2","schemaVersion":1,"time":1791063603.0,"generation":2,
 "id":"app-x.scope","demo":false,"snapshot":42,"sampledAt":1791063600.2,"row":{...},
 "membersTotal":41,"commandLimit":512,"units":{...},
 "members":[{"pid":4101,"name":"chrome","state":"S","pss":612352,"swap":0,"memoryStatus":"available",
  "cpuCorePercent":9.8,"cpuMachinePercent":1.225,"cpuStatus":"available",
  "command":"/opt/google/chrome/chrome --type=renderer …","commandTruncated":true,"commandStatus":"available"}]}
```

- `row` is the group's row in the newest snapshot. `members` are its 40
  largest members by footprint (then CPU), from that same scan; `membersTotal`
  counts all of them. Member CPU and memory follow the row semantics.
- Command lines are read from `/proc/PID/cmdline` when the request arrives,
  only for the listed members, and only if the PID still has the scanned start
  time (`commandStatus` `exited` otherwise, `unavailable` when empty or
  unreadable, `demo` for scene text). They are printable, at most 512
  characters (`commandTruncated`; at most 2049 bytes are read), and never
  stored, cached, logged or written to history. The detail scan also reads
  each process's `cmdline`, but only to choose its display name (an
  untruncated `argv[0]` or an interpreter's script); it keeps no argument
  list in snapshots, replies or history, and queries never search one.
- Errors: `inactive` (no detail subscription), `gone` (group not in the newest
  snapshot) and `bad-request`; all echo a valid `generation`.

Client routing: `a:`, `k:` and `d:` errors and `apps-details` replies go to
the Apps page's `appsMessage`; `killed` replies update the shared kill
bookkeeping (`kills`) as legacy kills do, with Apps entries marked
`scope: "apps"` so they are not pruned for being outside the Memory top 12.

## Apps GPU attribution (A3)

Per-app GPU memory and engine use, from the kernel's
[DRM client usage stats](https://docs.kernel.org/gpu/drm-usage-stats.html)
(`drm-*` keys in `/proc/PID/fdinfo/FD`). Design and sources:
[Apps design](design/apps.md#a3-gpu-attribution). No vendor tool runs and
nothing is installed; `nvidia-smi` is not used.

### When it runs

Sampling is active only while the `gpu` line is `1`, the `detail` line is
`1` and no demo is playing. `RamanRuntime.qml` sends `gpu 1` while any visible
Apps page with `gpuMetrics` `auto` subscribes; a Memory-only panel, a closed
panel and `gpuMetrics` `off` send `gpu 0`. Each change drops engine baselines
and the last sample and bumps an internal generation, so a result from before
the change is discarded. The time of the last sample start is kept across
these changes, so closing and reopening Apps, or `gpu 0`/`gpu 1` or a demo in
between, never starts samples less than 5 s apart.

When active, a sample is started after a detail scan if none is running and
the previous one started at least 5 s ago. It runs in one worker thread of the
probe, never two: a sample still running after 10 s is reported as an error
and no second one starts until it returns. When it does return (or any sample
whose own reads took longer than 10 s) its readings are kept but stay `stale`
with reason `sample-timeout` until a timely sample replaces them; the next
sample starts at once if its 5 s have passed. The probe's main loop, the 2-second
summary and JSON commands never wait for it. Its result is attributed to the
groups of each following detail scan.

A sample reads, for each process of the newest scan: the `/proc/PID`
directory (kept open; its `stat` start time must equal the scan's, so a reused
PID is not attributed), the `fd` symlinks (only targets under `/dev/dri/`),
`stat` of those descriptors (character device major 226, mapped through
`/sys/class/drm/*/dev` to a device), and the descriptor's `fdinfo` (at most
16 KiB). Before every `fdinfo` read the device's
`power/runtime_status` is read; a `suspended`, `suspending` or `resuming`
device is **asleep** and none of its clients are read (xe's `show_fdinfo`
takes a runtime-PM reference, so reading would wake it). An unreadable
status is an error and is not read either; a device without runtime PM is
always readable. Device identity (`/sys/class/drm`, `device`, `driver`,
`subsystem`, `vendor`, `device`) and amdgpu's `mem_info_vram_total` are
sysfs reads that never touch the hardware; `mem_info_vram_used` is read only
for an awake amdgpu device. With no DRM device no process is examined.

Bounds per sample: 16 devices, 4096 descriptors per process, 65536 descriptor
entries, 1024 `fdinfo` reads, 16 engines and 16 regions per client. Hitting
one sets `gpu.incomplete` (`fd-limit`, `drm-fd-limit`, `device-limit`).

### Clients, memory and engines

- A client is `(device, drm-client-id)`. Descriptors and processes sharing
  it count once. The same ID on two devices is two clients. A descriptor
  without `drm-client-id` is never counted (`unidentified`). A client whose
  `drm-pdev` disagrees with the device behind its descriptor is an error.
- A client reached from processes in more than one group is **shared**: it is
  counted once in its device's totals (`crossGroupClients`,
  `crossGroupVramKb`) and in no group's numbers; those groups report
  `sharedClients`.
- Memory regions: `vram*`, `local*` are device-local (VRAM; on an integrated
  GPU a firmware carve-out of RAM); `memory`, `system*`, `gtt`, `cpu` are
  system RAM in GPU buffers; others (`stolen*`, `gds`, ...) are never summed.
  Resident is `drm-resident-<region>`, or amdgpu's deprecated alias
  `drm-memory-<region>` when the former is absent; they are never added.
  Allocated is `drm-total-<region>`. A class's sum is `null` unless every
  region in it reports the key.
- Engines, per client and engine name: `drm-engine-<e>` ns over the
  monotonic time between two reads (a client read taking longer than 0.1 s
  gives that client's time-based engines `error`, reason `slow-read`, and no
  baseline: the counter was taken at an unknown moment of it), else `drm-cycles-<e>` over
  `drm-total-cycles-<e>`, else `drm-cycles-<e>` over `drm-maxfreq-<e>` × time;
  divided by `drm-engine-capacity-<e>` (1 when absent). A first reading or a
  capacity change is `warming-up`; a counter below the previous reading keeps
  the previous (larger) value until it catches up and reads `stale`; zero
  capacity, or more than 110 % of capacity, is `error` (the latter
  rebaselines). Engines are never summed across names into a GPU percentage.
- Engine baselines reset after more than 15 s between samples or a suspend.
- Memory sums over clients: the kernel counts a buffer shared between DRM
  files (`drm-shared-<region>`) in every client that holds it and prints no
  buffer identity, so RAMen cannot deduplicate buffers. When two or more of
  the summed clients hold resident memory and report shared buffers (or no
  `drm-shared` key), the sum may count one buffer once per client: it is kept
  as a client-reference total and marked `vramOverlap`/`systemOverlap`
  `possible`, never presented as unique or physical occupancy.

### Row fields

`apps-page` rows, `focus.row`, `apps-details` `row` and legacy `apps` rows add:

```json
{"gpuMemoryKb":6501171,"gpuMemoryOverlap":false,"gpuStatus":"available","gpuCoverage":{"measured":3,"members":3},
 "gpu":[{"deviceId":"pci:0000:03:00.0","driver":"amdgpu","source":"drm-fdinfo","status":"available",
   "vramKb":6501171,"vramAllocatedKb":6815744,"vramSharedKb":0,"vramOverlap":"none",
   "vramStatus":"available","vramSemantics":"resident",
   "systemKb":131072,"systemAllocatedKb":131072,"systemSharedKb":0,"systemOverlap":"none","systemStatus":"available",
   "engines":{"compute":{"percent":87.5,"status":"available","basis":"busy-time","capacity":1}},
   "clients":1,"sharedClients":0,"unidentified":0,"unread":0}]}
```

| Field | Meaning |
| --- | --- |
| `gpu` | One entry per device this group has DRM descriptors on (at most 16); never a cross-device sum |
| `deviceId` | `<bus>:<device>` from sysfs, for example `pci:0000:03:00.0` |
| `status` | `available`, `partial` (some descriptors asleep/unread/errored or some clients shared), `shared` (only shared clients), `asleep`, `unsupported` (no usage stats or no client ID), `error`, `stale` |
| `vramKb` / `vramAllocatedKb` | kB of device-local memory resident / requested, summed over this group's own clients on the device; `null` when not reported. `vramStatus` says why |
| `vramSharedKb` | kB of those clients' buffers shared with another DRM file (`drm-shared-*`, summed); `null` when a client prints no such key. Such buffers are also counted by their other holders |
| `vramOverlap` | `possible` when two or more summed clients hold resident memory and shared buffers (or no `drm-shared` key): a buffer they share counts once per client, so `vramKb` is a client-reference total that may exceed the app's unique VRAM. `none` when each buffer is counted once. No buffer IDs exist to deduplicate further |
| `systemKb` / `systemAllocatedKb` | kB of system RAM in this group's GPU buffers. Part of RAM; never added to `pss`, `swap` or any RAM figure. `systemSharedKb`/`systemOverlap` as for VRAM |
| `engines` | `{name: {percent, status, basis, capacity}}`; `percent` is % of that engine class, `null` unless `status` is `available` or `partial` (some clients warming up) |
| `clients`, `sharedClients`, `unidentified`, `unread` | Own clients counted; clients shared with other groups (not counted); descriptors without client ID; descriptors not read at the per-sample limit |
| `gpuMemoryKb` | Sum of `vramKb` over devices (same resident semantics), the `gpu-memory` sort key; `null` without a reading. `0` is a measured zero |
| `gpuMemoryOverlap` | `true` when any summed device entry has `vramOverlap` `possible`: `gpuMemoryKb` (and its rank) is then an upper bound of the app's own VRAM, labelled `≤` in the UI and never drawn as a share of the device |
| `gpuComplete` | `true` when every member's descriptors were read and every device entry counted all of the group's clients (otherwise a fresh row is `partial`); judged before staleness, so a `stale` row keeps it and its `gpuMemoryKb` stays a lower bound (`≥`, or `~` with `gpuMemoryOverlap`) when `false`. `null` without a sample |
| `gpuStatus` | `inactive` (not sampling), `warming-up` (no sample yet, or members newer than it), `none` (all members read, no DRM descriptors), `available`, `partial` (some members unread or some devices partial), `shared`, `asleep`, `unsupported`, `permission-denied` (no member's descriptors readable, for example non-dumpable processes), `stale`, `error` |
| `gpuCoverage` | `{measured, members}`: members whose descriptors were read in the sample |

### Snapshot `gpu`

`apps` messages and `apps-page` replies carry a `gpu` object (`null` on
non-ready replies):

```json
"gpu":{"status":"available","reason":null,"source":"drm-fdinfo","intervalSeconds":5.0,
 "sampledAt":1791063600.2,"ageSeconds":2.6,"collectSeconds":0.031,"reset":null,"incomplete":null,
 "memoryLens":true,"anomalies":0,
 "counts":{"processes":312,"fdEntries":9120,"drmFds":14,"fdinfoReads":14,"unknownDevices":0,"mismatches":0},
 "units":{...},
 "devices":[{"id":"pci:0000:03:00.0","driver":"amdgpu","pdev":"0000:03:00.0","pciId":"1002:73bf",
   "nodes":["card1","renderD129"],"runtimeStatus":"active","status":"available","clients":7,
   "memory":{"dedicated":{"status":"available","semantics":"resident","regions":["vram"],"seen":true},
             "system":{"status":"available","semantics":"resident","regions":["cpu","gtt"],"seen":true}},
   "engines":{"status":"available","names":["gfx"],"seen":["gfx"]},
   "totals":{"vramResidentKb":8650752,"vramSharedKb":65536,"vramOverlap":"possible","crossGroupClients":1,"crossGroupVramKb":32768,
     "deviceVramUsedKb":9437184,"deviceVramTotalKb":16777216,"unattributedVramKb":786432,
     "unattributedLowerBound":true}}]}
```

- `status`: `inactive`, `warming-up` (no sample yet), `available`, `stale`
  (values kept and labelled: the newest sample began reading more than 15 s
  ago, `reason` `old-sample`; or the newest attempt failed, `sample-failed`;
  or the newest sample ran past 10 s, `sample-timeout`, until a timely one
  replaces it), `error` (no sample, and the attempt failed or timed out;
  `reason` `sample-failed` / `sample-timeout`) or `unsupported` (`reason`
  `no-drm-devices`).
- `sampledAt` is Unix seconds when the newest sample began reading (its
  oldest reading), not when the probe received it; `ageSeconds` is the
  monotonic time since then and `collectSeconds` how long its reads took.
  Freshness is judged from that start, so a sample that stalls is never shown
  as fresh.
- `memoryLens` is true when a device has given a device-local resident
  reading during this probe's life and is now available or asleep. The
  Apps page offers the `gpu-memory` lens only then.
- Device `status`: `available`, `no-clients` (no client of this user is open
  on it, so its capabilities are unknown), `asleep`, `error` (power state
  unreadable). Memory metric `status` adds `unsupported` (clients read, region
  or resident key absent); `seen` is whether any client ever showed it.
- `totals`: `vramResidentKb` sums every distinct visible client once
  (including shared ones); `vramOverlap` is `possible` when buffers shared
  between those clients may be in that sum more than once. `deviceVramUsedKb` / `deviceVramTotalKb` come
  from amdgpu's `mem_info_vram_used` / `mem_info_vram_total` (bytes ÷ 1024);
  other drivers give `null`. `unattributedVramKb` is used minus
  `vramResidentKb` (other users, the kernel, invisible processes); it is a
  lower bound (`unattributedLowerBound`) when the visible sum may count a
  buffer more than once: clients report shared buffers, which each holder
  counts, or `vramOverlap` is `possible` (several resident clients without
  `drm-shared` keys, as legacy amdgpu prints, may share one allocation). It is
  `null` when that overlap makes the difference negative. Device and client reads are milliseconds apart.
- `reset` is `start`, `gap` or `suspend` when engine baselines were dropped
  by the sample.

Demo scenes never sample. With `gpu 1` the `apps` scene publishes its
synthetic rows and `gpu` object with `"demo":true,"synthetic":true` (an
amdgpu-like and an i915-like device, plus a runtime-suspended xe-like device
reported `asleep` with no clients or totals); other scenes report
`unsupported` (`demo-without-gpu`); with `gpu 0` they report `inactive`.

The `apps-page` `units` add `gpuMemoryKb` and `gpu`; `gpu.units` names each
GPU field's unit and meaning (`null` while `inactive`, to keep Memory-only
messages small). Kill, details and query actions are unchanged:
GPU readings never feed membership, protection or signals.
