# Storage design and verification

Storage is RAMen's third panel page. It was delivered in three merged
milestones: S1 (explicit filesystem discovery, a cancellable allocation scan
and private paged snapshots; PR #4),
S2 (the page, original folder bands, ledger, validated path actions and session
settings; PR #5) and S3 (failure,
partial, remount and race states, helper recovery and synthetic fixtures;
PR #6). Memory and History keep
their existing controls. Nothing scans recursively until the user chooses Scan
or Rescan, which sends a `storage.scan` request naming one directory.

## Original design and influences

**Problem.** A capacity percentage says a drive is full but not what to
inspect. Storage connects one filesystem's capacity to the large folders and
files inside a scope the user chooses, without claiming that the two numbers
reconcile.

**Information hierarchy**, top to bottom in the shared panel (`Style.space(420)`
wide, fitted to the screen):

1. Scope header (`Storage · <scope>`), scope buttons (Home, Root, a local-volume
   list and an absolute-path field).
2. Filesystem capacity as its own line and a thin strip: used, reserved and
   available bytes of **one filesystem**.
3. Breadcrumbs from the scope root, then scan age/coverage and the scoped known
   allocation, including the folder's own allocation and lower-bound wording
   for partial coverage.
4. Scan / Rescan, Cancel, Reload cache and Up, then a status line. Scanning
   shows an activity indicator and counts, never a percentage.
5. Folder bands: up to three fixed-height levels in one horizontal frame.
6. The ledger (name with a `[hidden]` marker, allocation with `≥` for lower
   bounds, share), row/member paging and Folder itself / All entries / Tiny
   entries / Other members views, then the selected entry's details and
   Descend, Open directory / parent and Copy path.

**Geometry.** Width is the only encoded quantity: each entry takes its share of
its parent's known allocated bytes, including the directory's own allocation
("Folder itself"), so child proportions never silently renormalise away bytes.
Band height marks hierarchy level only. Entries narrower than three pixels and
the backend continuation collapse into one **Other** segment whose real members
can be listed. Rounded band ends (4 px radius) recall noodles on RAMen's serving
board; rounding is decoration and does not change encoded width. Band colours
are five stable tints of the active theme accent, chosen by hashing node IDs.
They are categorical, never a warning/critical or importance scale. Selection
is a 2 px foreground outline matched by the ledger highlight, so colour is not
the only signal.

**Interaction.** Clicking a directory band recentres on it; clicking a file band
selects its ledger row; Other opens its members. Every map action has a ledger
or button equivalent, and the map can be turned off (`storageShowMap`) without
losing any capability. Keyboard routing lives in `PanelNav.js`; no Storage key
or button can reach the Memory kill path.

**Influences and differences.** The workflow was inspired by two Omarchy
plugins, reviewed on 2026-10-03 for behaviour only (no code, assets, text or
screenshots were copied; see [CREDITS](../../CREDITS.md)):

- Disk Lens / Maarten Tolhuijs (`io.github.mtolhuys.disk-lens`, revision
  `7f0ea22`): on-demand scans, a treemap with a linked list, filtering, cached
  navigation and file-manager handoff.
- Omadisk / Kennet Postigo (`postman.omadisk`, revision `bcae90c`): a sunburst
  with a synchronised folder list, breadcrumbs and cached navigation.

Substantive differences in the delivered RAMen design:

1. **Layout.** Nested horizontal bands of fixed height in one frame, not a
   rectangle mosaic (treemap) or radial wedges (sunburst). Only width carries
   size; the ledger beneath carries exact numbers and share.
2. **Data semantics.** Filesystem capacity and scoped allocation are separate,
   labelled quantities. Allocation is `st_blocks * 512` with hardlinks counted
   once; directory self-allocation, unreadable/excluded coverage, lower bounds
   and Other stay explicit. Scan age is always shown; partial coverage and
   pending activation durability are labelled whenever they apply.
3. **Safety and workflow.** Read-only: there is no delete, trash, cleanup or
   privileged scan. Open/copy act only on backend-validated byte paths, and a
   selected file opens its parent directory instead of executing. Scans are
   cancellable, run in owned workers and never start automatically.
4. **Accessibility.** Ledger-first keyboard navigation with a button equivalent
   for every map action, plain-text names, and an outline as well as colour for
   selection.

The roadmap also listed OmaTree (bounded persistent snapshots) and Disks
(filesystem context); their materials were not consulted for this
implementation and they are not credited as influences. The original plan's
treemap preference was intentionally replaced by this band layout to give
RAMen a distinct presentation.

## Capacity and scope

`storage.mounts` reads bounded Linux mountinfo in a short worker and returns
at most 50 mounts per page. Octal escapes preserve mount points, roots and
sources as bytes. Local filesystem types are allowlisted; pseudo, network,
RAM-backed, automount and unknown types disclose their exclusion. Overlay is
supported for container fixtures; it is not a physical device. Mounts and
subvolumes sharing storage never become an invented machine-wide sum.

Selected capacity uses `f_frsize`, falling back to `f_bsize`, consistently:
total = blocks × unit; physical free = bfree × unit; available = bavail × unit;
used = total − physical free; reserved/unavailable = free − available. Values
are nonnegative and clamped to total/free. Read-only is reported separately.
Capacity reads and root validation can block in the kernel, so both remain
outside the probe's stdin and two-second summary path.

## Allocation and incomplete coverage

The walk uses `lstat`/`stat(..., follow_symlinks=False)`, bounded `scandir`
fanout, byte-sorted depth-first traversal and directory file descriptors opened
with `O_NOFOLLOW`. An opened directory's device/inode must match its observed
entry. Linux `fdinfo` mount IDs detect a bind mount or same-device subvolume
inserted during traversal. Mountinfo boundaries are also pruned; Root therefore
shows a Home subvolume as excluded coverage. RAMen's own storage cache is
excluded if it falls under the selected scope.

Allocation is `st_blocks * 512`, including directory own allocation and symlink
metadata. Apparent size is a distinct own-entry value. Device/inode hardlinks
count allocation once at the first byte-sorted traversal path; later references
are marked shared links with zero attributed allocation and their apparent
size retained. Symlinks are never traversed. No file content is opened.

Unavailable/vanished entries remain nodes with reasons and null own allocation;
unreadable directories retain known overhead while their subtree coverage is
partial. Excluded mounts have unknown allocation. `knownAllocatedBytes` is a
nonnegative lower bound; `allocatedBytes` is null for incomplete subtrees.
Warnings are bounded to 32; unreadable/excluded/changed counters retain total
counts. The children reply carries directory own allocation, complete/partial
rows and an Other summary, so a future map can account for every known byte.
Other is a real paged continuation, with the same literal display filter scoped
to direct children. Stable node IDs hash root identity, scope bytes and relative
path bytes; lossy/plain display labels never become navigation paths.

This is a best-effort metadata walk, not a filesystem transaction. Compression,
reflinks/shared extents, snapshots, permissions, mounts, deleted-open files and
concurrent changes make subtree allocation differ from filesystem used bytes.
Neither quantity predicts exactly how many bytes deleting a file would free.

## Private snapshots and limits

Versioned stdlib SQLite stores only metadata and byte-safe paths beneath
`$XDG_CACHE_HOME/raman/storage`, default `~/.cache/raman/storage`. RAMen's own
directories must be owned by the current user and exactly 0700; files must be
regular, owned, single-link and 0600. Unsafe existing stores fail explicitly.
Directory descriptors anchor all file operations; no-follow opens and an
already-open snapshot FD avoid symlink replacement between checks and reads.
Snapshot reads use SQLite read-only/immutable mode. Full integrity, schema,
accounting and precomputed child-total validation runs on first load. Subsequent
workers reuse a probe-private validation token only while the opened file's
exact device/inode/size/mtime/ctime/owner/mode/link fingerprint and metadata hash
match. Tokens never come from client input; they are bounded to three snapshots.
Reads still validate capture age, private activation receipt, authoritative
catalog membership and current root identity. Catalog reads contain at most
three entries; no directory inventory is elected from file timestamps. Indexed ordering and stored child totals avoid whole-inventory work
for an unfiltered page. Filtered counts scan only that directory's children.

Root identity combines boot ID, filesystem ID, Linux `STATX_MNT_ID_UNIQUE` and
root birth time with the observed device/inode and mountinfo details. The
[Linux statx contract](https://man7.org/linux/man-pages/man2/statx.2.html) provides
mount IDs that are not reused during a boot; the boot binding prevents reuse
across reboots. Birth time distinguishes recycled root inodes, and filesystem
ID detects a replacement volume. Ordinary mount IDs and source paths alone
are insufficient. This implementation fails closed with `identity-unavailable`
on kernels before Linux 6.8 or filesystems without root birth time; it never
reuses an inventory under weaker identity. This conservative capability limit
also applies to selected-capacity identity checks. A matching path alone is
insufficient. Snapshots do not share History's state or Clear action.

Internal ceilings are 200,000 entries (including root), depth 128 below root,
120 seconds including worker startup, three retained scopes, 128 MiB including
the new candidate, and seven days for cache reuse. Queries/discovery have a
10-second worker deadline. These are implementation constants, not settings
or filesystem performance promises. A full cache that cannot hold both the
prior snapshot and candidate fails without evicting that valid prior result;
select a narrower folder or remove RAMen's storage cache explicitly. Cached lookup selects one authoritative committed
snapshot per scope, ordered by an explicit catalog revision/list rather than
wall-clock capture time. `capturedAt` labels age; a backward clock adjustment
cannot make an older activation win.

One nonblocking flock lease covers preparation, catalog activation and cleanup
across all probe runtimes sharing the cache. A contender receives `cache-busy`.
SQLite page counts enforce the candidate budget, with reserved bytes for the
bounded catalog/journal/receipts. Preparing a complete or explicit partial walk
flushes its DB and receipt, then prepares/fsyncs a replacement catalog and an
activation journal containing the previous/proposed catalogs. Directory sync
makes all this preparation durable while readers continue selecting the prior
catalog. A prepared DB/receipt is never browsable, even by its snapshot ID.

Atomic replacement of `catalog.json` is the sole commit linearization point.
It selects the new inventory and at most two other scopes. The worker then
syncs that replacement before deleting any superseded/evicted DB or receipt.
Successful completion includes cleanup: at most three physical inventories
remain, and evicted IDs are rejected even if cleanup is temporarily blocked.
GC follows only the catalog; it never elects orphan receipts or changes active
selection because a later scan starts and fails.

A crash after catalog replacement can leave its durability unresolved. The
journal and prior files remain until a worker obtains the nonblocking writer
lease, syncs the current catalog and finishes catalog-directed GC. Before
replacement, recovery preserves prior selection and removes the orphan;
after replacement, it completes the committed selection. Persistent sync or
cleanup failure returns `commit-uncertain`/`cleanup-failed` with explicit
uncertainty, blocks further candidate growth and retains the journal/required
rollback data. `activationDurability:"pending"` labels cached browsing while
post-commit durability is unresolved. No success is reported before bounded
recovery/cleanup succeeds; a later writer must repair it before scanning.

## Ownership and completion

The probe owns one scanner and one short-query worker lane, an eight-request
queue and at most 64 client generation records. Subprocesses use fresh Python
exec with `-S` (stdlib-only, no site initialization), `close_fds`, dedicated command/result pipes and their own process
session. They inherit no probe stdin/stdout, history writer or dispatcher lock.
Short queries skip controller-only process-creation/ID imports and the scan's
commit-control queue; their ownership watchdog and fresh-process isolation
remain active.
A worker watchdog watches command-pipe EOF and parent identity every 100 ms,
including while filesystem metadata is blocked. It exits if ownership dies.

Every client sends `clientId`, increasing `generation`, and unique `requestId`.
New generations invalidate older jobs and queued replies for that client.
Cancel must name that client's scan ID; cancel and page leave advance the
generation, revoke queued work and TERM owned workers immediately. Active-scan
requests acknowledge with `storage-progress` status `cancelling`/`leaving`;
this acknowledgement makes no claim about the committed snapshot. The probe
escalates to KILL after 200 ms and reaps without blocking summaries. After
reaping, an owned reconciliation worker resolves the authoritative catalog and
cleanup off the probe loop. Terminal `cancelled`/`left` requires prior selection
to remain; when atomic replacement already won, terminal `committed` includes
the actual selected snapshot ID. Persistent recovery failure yields `uncertain`
with the selected ID and an explicit code. `previousSnapshotRetained` describes
selected prior data, rather than mere physical file existence. Queued scans
which never started can be cancelled immediately.

A worker first reports a private prepared candidate. The probe processes ready
stdin before authorizing publication for the current owned generation.
Authorization alone is not completion. Old-generation progress/results never
reach the client; reconciliation preserves candidate/prior context while queued, running or
reaping. Repeated cancel/leave updates the latest delivery request/generation;
it cannot discard mandatory recovery or claim cancellation before resolution.
Normal generation advance suppresses stale delivery while recovery continues.
Worker deadlines apply independently of delivery generation and are not reset
by repeated requests. Unexpected
scanner exit/timeout follows the same reconciliation path, so a crash after
atomic catalog replacement cannot be misreported as an aborted scan.

EOF/parent death closes both lanes and allows at most 900 ms for cleanup. A
worker stuck in uninterruptible kernel I/O can only finish dying when the
kernel returns; the probe never waits indefinitely. After a direct probe-worker
SIGKILL, the watchdog exits and the OS's reparenting/init process owns reaping.
Interrupted publication is resolved from the durable journal by the next
exclusive writer. Current root identity is rechecked immediately before catalog
replacement and on every cache query.

Progress carries entries/counters/elapsed time, never fabricated percent
complete, and is forwarded at most twice per second. All storage output lines
are actually serialized under 128 KiB; at most 50 ledger rows and 60 map
segments including Other are supplied. Large labels reduce page length with
correct continuation offsets. The page accepts only its outstanding request,
scan ID and client generation, sends `storage.leave` on page/owner/runtime
departure, and keeps all process-signal paths out of Storage.

## S1 automated coverage

`tests/test_storage.py` covers real Linux allocated files, sparse/hardlinked
entries, cycles, hidden/Unicode/newline/non-UTF8 names, private cache, paging,
warnings, simulated permissions/change/bind/remount races, limits and previous
snapshot preservation. Actual probe pipe tests cover discovery/scan/cache,
clear-history isolation, crash/recovery, cancellation/leave/EOF, waiting-parent
death, queue/generation ownership, prepared cancellation and descriptor
isolation. Large fanout exercises real progress and summary/refresh response.

Container tests do not prove real removable media, privileged bind/subvolume
fixtures, uninterruptible I/O or physical monitors. See the
[verification record](#verification-record-and-remaining-coverage).

## S2 page and model

`StorageModel.js` owns byte formatting, stable categorical colors, fixed-height
band geometry and outstanding request identity. Up to three snapshot hierarchy
levels share a frame. Parent own allocation reserves its proportional width;
child widths use known allocation in that subtree. Partial values are labelled
lower bounds, zero/unknown entries remain inspectable in the ledger, and tiny
entries/Other retain real IDs and backend continuation offsets. One bounded
ledger page has 50 rows; all visible map levels share a 60-segment budget.
Other retains its source directory reply for explicit members and its backend
continuation separately. Member paging crosses that boundary in both directions
without entering unrelated ledger pages; coalesced directory self allocation
remains an actionable directory row. All entries restores the source listing.
Only a few large directories are prefetched from immutable snapshots for deeper
bands; prefetched data never launches recursive filesystem work.

Selection resolves only against displayed ledger rows. Map selection exits any
Other restriction; selecting Folder itself installs its cached directory reply
and displays that directory as a single actionable row, also available through
the Folder itself button. All entries returns to children. Empty unfiltered
directories retain their own ledger row; a filter with no matches selects nothing.
Filters reset member, offset and selection state. Pending ledger queries disable
old row/map/view choices, paging, rescans and path actions; local selection cannot
cancel submitted data. New filters, explicit breadcrumb navigation and scope
changes supersede outstanding queries; page exit still leaves the client. Errors
restore the retained listing’s committed filter before re-enabling interaction.
Tiny-page changes retain any request needed to resolve their directory and select
from the latest slice when it arrives. Pending ledger queries clear selection
until their matching reply arrives. Later selections remain usable after it settles.

`StorageView.qml` owns its panel's client, increasing generations, unique
outstanding requests and scan ID. Its page Loader owns capacity refresh and
sends leave on destruction; runtime migration leaves the old owner and reserves
a distinct namespaced client in the destination. Page reopen reuses the panel's
client, avoiding exhaustion of S1's 64-client lifetime bound. Scan is explicit;
cache/discovery/browsing requests never scan. Cancel ACKs keep the page pending
until reconciled terminal outcomes, then reload selected cache rather than
assuming that the previous snapshot won. Fully correlated command errors may
omit scanId; explicit mismatching IDs, stale generations and unrelated requests
remain rejected. An unknown-scan Cancel settles scan flags, blocks actions during
authoritative cache reload, and reports an unknown cancellation outcome rather
than a failed or cancelled scan. Cache activation durability remains visible.

Scope memory is shared, session-only, and enabled by default. It remembers
chosen scope while enabled, not filter/focused node, and starts at the snapshot
root on reopen. No path inventory enters History. `PanelNav.js` routes Storage
keys into read-only navigation/action names; the existing Memory signal guard
remains in `Panel.qml`. Text editors block the host key catcher.

New scans record optional private per-node action identity without invalidating
S1 cached browsing. Live actions validate the current root and each byte-path
ancestor through no-follow directory FDs; stored snapshot IDs or display labels
alone cannot authorize them. Ctime comparison conservatively requires a rescan
after metadata changes. Open transfers only a pinned directory FD to the desktop
portal's directory-capable OpenFile API using system GIO through stdlib ctypes;
copy uses `wl-copy` stdin. Open requires a successful portal Response, not just
a launch receipt. Unavailable portal capabilities fail explicitly. Unfinished
clipboard helpers stay in the owned worker group and are cleaned up on leave,
EOF and owner death. Completed desktop handoffs remain outside that group. The
portal may canonicalize a path later, which RAMen cannot attest. Non-UTF8 names navigate normally but cannot claim a
UTF-8 clipboard capability. See the protocol and [S2 runbook](../qa/glhf-s2.md).

## S3 failure and recovery states

The page distinguishes discovery, unscanned, scanning, partial ready, cancelled,
missing, changed/remounted, unsupported identity/filesystem, invalid path, limits
and helper failure. Progress has only entries/counters and an activity indicator;
read-only capacity remains explicit and does not prohibit a read-only inventory.
Partial rows and bands use known allocation, with lower-bound coverage wording.
Reconciliation without a snapshot preserves its terminal outcome rather than
silently rewriting a cancelled scan as unscanned.

Every selected-capacity refresh compares a returned root identity with the
displayed snapshot. Confirmed replacement/missing/unsupported identity retires
that panel's generation and owned work, clears cached rows/actions and requires
explicit scanning. No cache file is deleted. Stale replies cannot restore the
retired view. Unrelated volume-list errors and transient capacity failures do
not invalidate compatible data or overwrite an active scan/cancel outcome.
Backend identity validation on every query/action remains authoritative between
refreshes; periodic UI discovery is not an atomic filesystem guarantee.

Helper exit clears pending requests/data. Helper startup reloads only capacity
and compatible cache, including requests made while its pipe was unavailable.
Scan/actions and Reload cache wait for transport availability; unavailable sends
never register pending requests. Startup recovery is independent of the displayed
phase and runs after transport bindings settle, only for the current owner.
Reload cache provides a manual retry without recursive work. Client ownership
is unchanged: each panel's leave/invalidation affects only its own work.

## Synthetic demo fixtures

Storage has no canned demo scene: `demo green|yellow|red|history` affects only
Memory/History, and demo mode never redirects storage requests. Instead,
`scripts/storage-demo.py` creates original **Serving Board**, **Empty Serving
Board** and **Cancellation Kitchen** trees beneath the checkout's ignored
`.agent-artifacts/`, plus a `provenance.json`. Users choose a printed scope and
explicitly Scan, so the real widget measures real allocation and validated
actions work; there are no fabricated snapshots or invented totals. Serving
Board covers uneven folders, hidden/empty/sparse/hardlinked/symlink entries,
Unicode, newline, shell-looking and non-UTF-8 names, and 99 tiny Spice jars
beside one large member. The generator never scans, installs, opens, signals or
writes outside its output directory, and refuses non-Linux hosts. Names,
content and composition are original. The [final QA runbook](../qa/final-qa.md#4-storage-gallery-capture-and-publication)
shows how to reach the ready, partial, cancelled, limit and missing states from
these fixtures safely.

## Verification record and remaining coverage

Accepted, merged evidence (each result belongs to the revision it names):

- S1 (PR #4): native QA PASS
  at `50d5f77`, 133 Python tests and native Btrfs cached queries 38.05–50.35 ms;
  [implementation evidence](../qa/s1-local-evidence.md).
- S2 (PR #5): native QA
  at `86264e8` (160% UI, byte-exact clipboard, Nautilus parent open, 50k Btrfs
  cached pages 33.00–51.80 ms) and exact-head retest
  at `168be4a`. The earlier 81–157 ms Docker cached-query miss stays unexplained.
- S3 (PR #6, merged as `9ca8b4d`):
  native QA at 64684ba
  on Linux 7.2.5/Btrfs, Omarchy 4.0.4, Quickshell 0.3.1 at 160%: 146 of 147
  Python tests (one optional portal skip), 113 Qt checks per scale, 50k/60k
  workloads with cached queries 38.29–44.42 ms, cancel ACK 0.26 ms, owned
  cleanup 237/241 ms, three live restart-history passes and installed-service
  scan/cache/cancel/Other/partial/settings/open/copy checks. The
  correction retest at 1d1dafe
  passed both fixes in the installed widget plus six Node suites and 120 Qt
  checks per scale. [S3 implementation evidence](../qa/s3-local-evidence.md)
  keeps the container results and preserved failures. The storage backend and
  checker at `9ca8b4d` are byte-identical to the natively measured `64684ba`.

The [final-QA execution record](../qa/final-qa-evidence.md) now records the
attended shared-service, removal, same-drive remount and Storage-idle suspend
results, monitor reconnect failures and coverage limits. A [native ready-state capture](../screenshots/final-qa/README.md) is
published with [provenance](../media/masters/final-qa/provenance.json).
The extended gallery and different-filesystem substitution were not executed;
an unexecuted check is not a passing result.

Excluded from hardware scope (scope update),
not deferred or passed: read-only-source/unsupported-filesystem hardware QA and
real stalled/uninterruptible disk-I/O recovery. Their design limits above still
apply.
