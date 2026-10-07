# RAMen shared extension contract (F0)

This closes the bounded foundations contract against H2 merge `b47a5e5` and
H3. Memory, History and Storage pages are available. Storage (S1–S3) implements
the explicit backend commands, owned workers, page and validated path actions
described in [Storage design](storage.md) and the
[protocol](../protocol.md#storage-backend). A1 implements the Apps backend:
recent CPU accounting on the shared detail scan and the cached `apps.query`
inventory ([Apps design](apps.md), [protocol](../protocol.md#apps-backend-a1)).
A2 implements the Apps page and its membership-checked kill
([protocol](../protocol.md#apps-page-actions-a2)). A3 adds GPU attribution from
DRM fdinfo, sampled in one probe worker thread only for a visible Apps page
with `gpuMetrics` `auto` ([protocol](../protocol.md#apps-gpu-attribution-a3)).

## Data and replies

[Probe protocol](../protocol.md) defines implemented commands. Memory is kB
(1024 bytes), pressure is percent, wall time is Unix seconds, and monotonic
seconds are meaningful only within the named probe session. Preserve nulls:
unavailable is not zero; zero swap capacity means no configured swap.
Replies have a type, schema version, requestId and wall time. Error codes are
stable enums; human descriptions are ≤200 characters. Commands are ≤4096
bytes, request strings ≤64 characters and numeric IDs within JavaScript's exact
integer range. History has ≤360 aligned points, ≤50 incidents, ≤512 gaps,
≤5 contextual apps and ≤256 printable characters per display name. State
input/output is ≤2 MiB. Never persist argv, environment, PIDs or executable
paths as incident context.

Downstream replies must name units, nullability/capabilities, limits, errors,
progress and completion states before their feature ships. Unknown commands
currently return `unknown-command`; they do not start work.

## Clients, generations and subscriptions

Visible panels/views reserve keys in their current runtime. Subscriptions are
keyed, OR-combined and removed individually: closing one panel cannot cancel
another's detail demand. Visible Memory and Apps pages request the same
`detail` subscription and share that one scan; only Apps adds `gpu`, and GPU
sampling also needs `detail`.
`apps.query` reads its cached snapshots and never scans. History polls
only while its Loader exists, no faster than its window resolution. Leaving
or closing destroys its timer and pending query.

History request IDs encode client and increasing generation. Accept only the
outstanding ID and selected window, once. Window switches, close, timeouts,
clear and runtime migration invalidate prior results. Migration reserves a
new client ID in the destination runtime; old IDs cannot match another view.
Incident acknowledgements also require the dispatch epoch, current generation,
enabled policy and five-second deadline. Restored query data never enters the
live incident producer. Future jobs must additionally carry job ID/generation;
late cancelled/departed progress must not repopulate a page or enable actions.

## Ownership and lifetime

On Omarchy, `Service.qml` mounts one `RamanRuntime.qml` per shell. Widgets use
`bar.shell.serviceFor("boundsj.raman")`, retain `hostWidget`, and route panels
through scoped summon/hide/toggle. The tested Omarchy 4.0.4 host prefers an
already-open copy, otherwise the focused monitor, and allows one popup
globally. Independent concurrent-panel subscriptions remain covered by the
runtime tests; native simultaneous-panel QA requires a host permitting them. No additional
plugin-local singleton is needed. A bar lacking the API gets a private runtime
after the existing grace period; it cannot promise shared snapshots or
unambiguous multi-runtime IPC.

Python flock permits one history writer. A separate dispatch lease permits
one notifier even with persistence off/private runtimes. H3 owns dispatcher
policy, acknowledgements, duplicate-alert and restart/two-monitor acceptance.
Fallback followers may have session-only history and cannot toast. Waiting
parent + worker preserves final save when Quickshell kills the parent;
stdin/stdout closure and parent death exit the worker. No closed-panel event
may start a per-process scan.

## State, cache and privacy

History uses `$XDG_STATE_HOME/raman`, default `~/.local/state/raman`: directory
0700, atomic files 0600. Collection off stops samples/detection/toasts without
erasing saved state. Persistence off starts session-only history without
loading/saving that file; switching out first flushes the previous persisted
session. Explicit Clear publishes an erase marker and clears an idle file
immediately, or the current writer applies it on its next tick/exit. Separate
dispatch policy stores only boot identity and two cooldown timestamps, never
measurements/app names. Clear preserves notification cooldowns.
Following a marker retains pending durable erasure until a successful atomic
save; failure retries on the writer's next tick/exit. Incident measurements,
thresholds and app context have a 24-hour age limit. A long incident keeps only
its minimal ID/lifecycle after payload expiry, labelled expired in the receipt;
newer upgrade measurements expire independently. No active-incident exception
extends private observation retention indefinitely.

Storage private SQLite snapshots belong in `$XDG_CACHE_HOME/raman/storage`,
default `~/.cache/raman/storage`, separate from history state: own directories
0700, files 0600, seven-day reuse limit, three retained scopes and 128 MiB
including the candidate. An exclusive scan lease prevents concurrent writers;
byte-safe node paths remain in SQLite. Only atomic replacement of an authoritative
catalog selects a completed inventory; prepared DB/receipt files are invisible.
Activation order is independent of wall-clock time. Cancel/page leave revoke
ownership and acknowledge `cancelling`/`leaving`, then reap and reconcile off-loop:
terminal cancellation requires retained prior selection; a won commit reports
its actual selected ID. The journal/rollback files remain until commit sync;
success cleanup enforces three physical/queryable scopes. Orphan receipts never
win selection; persistent cleanup failure blocks further growth. Identity uses
boot-bound unique mount IDs, filesystem ID and root birth time; weaker identity
fails closed. Path inventories never enter history. See
[Storage lifecycle and commit boundary](storage.md#ownership-and-completion).

## Worker responsibilities

The [Storage backend and page](storage.md) own traversal, cancellation,
cache lifetime and EOF cleanup. The [Apps backend and page](apps.md) own
sampling, guarded actions and capability-aware GPU work.

Each worker needs one owner, bounded concurrency/queues, cancellation on page
leave, and reaping of all owned children on EOF/owner death. Waiting for workers
cannot block the 2-second summary or IPC. Test long jobs, repeated start/cancel,
late results, leave/re-enter, errors and EOF/reaping with test-owned children.
Preserve protected/PID checks, the two-step kill (Memory and Apps lists only)
and its timeout; Apps confirmations carry the armed membership.
Historic evidence must never carry an old identity into an action.

## Evidence and limits

PR #1, merge `3342a5e`, supplies H1
bounded persistence, clocks/privacy/writer/EOF tests.
PR #2, merge `b47a5e5`, supplies shared
service, focus, subscriptions and migration fixes. Its
Astra review
and live QA
apply to head `6245c9bea7fc1a2d8af7039543e0dcb1b54adf1f`.
Tests: `test_runtime.js`, `test_panel_nav.js`, Python history/probe and
`tests/qml/`; H3 adds `test_incidents.js`/`test_incidents.py`.

H2's three restart checks and virtual-output 1→2→1 passed. They do not prove
physical monitors/unplug, simultaneously visible Memory panels, hardware
suspend, notifications or screen-reader behavior. H3's
[GLHF runbook](../qa/glhf-h3.md) owns live incident/notification checks; H-DOC
owns final captures. Physical exceptions require Jesse's acceptance.
