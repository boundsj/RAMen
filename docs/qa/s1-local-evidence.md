# Storage S1 local verification

This implementation adds the backend without enabling a Storage page. Implementation-host
evidence uses macOS tools and Linux Docker fixtures. Separate native Omarchy
QA results are recorded below with their tested revision and remaining gate.
The exact reviewed commit is supplied in the open PR handoff; no merge,
deployment or issue closure is claimed here.

## Automated checks

On 2026-10-04, all five existing Node suites passed on the implementation host.
Six pure Storage parsing/transport validation tests passed natively on macOS.
The original reviewed head passed **131 tests** in Linux Docker, Python 3.12.13,
with procps installed and test-owned XDG state/cache. The 42 Storage checks
include real sparse/hardlink/symlink/byte-path allocation fixtures and actual
probe pipes; permissions/vanish/mount/bind/remount changes are injected rather
than pretending this container can reproduce physical media or privileged
mount behavior.

Covered outcomes: deterministic allocation and own-directory overhead, literal
directory filters/Other/pagination/serialized limits, incomplete coverage,
root/mount identity, cache ownership/modes/version/age/size/three scopes and
concurrent-writer lease, entry/depth/time limits, previous snapshot retention,
scan crash/orphan recovery, queued/foreign/stale-generation cancellation,
prepared-candidate cancellation, subprocess spawn failure, inherited lock
descriptor isolation, live worker summary/refresh/progress, leave/EOF and
waiting-parent death cleanup. Round-one review reproduced five gaps in the first head: prior-file loss on a
post-rename directory-sync failure, recyclable filesystem identity, a full
Unicode envelope exceeding the wire bound, lost observed directory overhead,
and whole-inventory validation on every query. Regression checks now include
both renames, DB/receipt/directory-sync and cleanup faults; real cancel, leave,
EOF, scanner SIGKILL and waiting-parent death paused after both publication
renames; non-recycled identity/capability collisions; immutable validation reuse
and mutation detection; and maximal Unicode request/client IDs with partial
warning metadata. Round-two review caught remaining selection bugs: a published
receipt could elect an interrupted candidate, four scopes left four physical
inventories, and backward wall-clock time could select the older scan. The
current head uses an authoritative activation catalog and durable journal.
Tests now assert selected IDs before/after forced cancel/SIGKILL on both sides
of catalog commit, reject unpublished/evicted IDs directly, and prove a later
failed scan cannot elect an orphan. Successful four-scope completion leaves
three physical inventories; post-commit sync/cleanup faults report uncertainty
and prevent further candidate growth until recovery. Clock rollback preserves
explicit activation order. Round-three regressions cover repeated cancel/leave
while recovery is queued, running, terminal-but-not-reaped and reaping after a
resource deadline. Every latest request receives the actual committed ID;
normal generation advance cannot discard recovery or disable its deadline,
and TERM/KILL/reap releases the scan lane for a subsequent successful scan.

Test worker ceilings/faults use Python injection;
the product protocol cannot raise scan ceilings or invoke arbitrary helpers.

An initial full-suite container attempt mounted the entire repo read-only;
four existing incident tests correctly failed when writing their ignored
fixture artifacts. The final run mounted only `.agent-artifacts` writable and
all tests passed. This was a harness permission correction, not a waived failure.
The first no-init Docker lifecycle measurement left an exited orphan probe as
a zombie under its test PID 1; the final executable checker uses Docker
`--init` so orphan reaping models Linux's ordinary init responsibility.

No QML/JS/manifest implementation was changed. Existing UI routing/Memory-only
kill and incident helpers retain their regression suites. Separate native QA at
`a41fd51a621343b678488ab1c6242c5b6ec7d08c`
completed three live restart checks and the existing Memory/History smoke checks;
that evidence applies to that revision, not automatically to later changes.

## Executable Linux measurements

`scripts/check-storage.py` reported **PASS_WITH_TARGET_MISS** in an init-equipped Linux Docker container
using copied implementation modules and synthetic fixtures under its own
`.agent-artifacts`. It did not install or contact a shell. The copied tree
reported `sha:null`; its recorded source hashes identify the tested files and
must match the reviewed checkout. A real GLHF checkout must record its SHA.
These are single-run fixture measurements, not general disk performance claims.

| Measurement | Observed | Target |
| --- | --- | --- |
| Cached page, 174 direct fixture entries, fresh query worker | 109.11 ms; target missed | <100 ms |
| Cached pages, 60,175-entry snapshot, five fresh workers | 120.96, 110.11, 110.19, 105.74, 101.03 ms; target missed | <100 ms |
| Active scan cancel acknowledgement | 0.72 ms | <250 ms |
| Cancel acknowledgement through reconciled terminal result | 145.92 ms | Truthful selected catalog outcome |
| Refresh while scanner is active | 0.57 ms | Responsive |
| Full walk, 60,175 entries | 3.5377 s | <120 s ceiling |
| Scan progress | 6 messages; 0.5000 s minimum spacing | At most twice/second |
| Largest observed summary interval | 2.0036 s | No scan-induced skipped two-second cadence |
| Largest observed storage line | 33,469 bytes including newline | <=131,072 bytes |
| EOF → probe/scanner reap | 16.66 ms | <1 s for interruptible work |
| Waiting-parent SIGKILL → probe/scanner cleanup | 66.79 ms | Responsive owner-death cleanup |

The final serial checker **does not pass the 100-ms cached-page target**. An
earlier catalog-head run also missed it (113.57 ms small; 124.69–170.56 ms large).
Round-two final measurements also missed (107.79 ms small; 144.90–224.38 ms
large). All misses remain evidence and a native GLHF/reviewer performance gate.
They are not waived as Docker overhead. Round-one's passing 94.32–98.39-ms
large pages do not establish performance of this catalog head.

A round-two function-level profile of the unchanged cache/query functions used
five actual
60,001-entry probe requests with an injected module worker. Children computation,
including catalog/schema/root checks and indexed page queries, took
1.64–1.80 ms; trusted schema/metadata validation took 0.23–0.25 ms and used
`full=False` every time. Full validation occurred once (~188 ms). That transport
measured 96.32, 95.89, 102.79, 96.54 and 96.06 ms, with one target miss.
This isolates bounded catalog/indexed work from startup/transport variance;
it does not replace the slower default-worker checker or claim its target
passes. Cold cache loads after restart still perform full validation. GLHF
must measure the default workers on the exact reviewed head.

The checker also proves selected-capacity free/available/reserved equations,
selected prior data after reconciled pre-commit cancellation and leave, no per-app messages with
detail disabled, byte-safe fixture handling and bounded Other/map/ledger pages.
State/cache/fixtures are private/test-owned; no paths or source contents appear
in this tracked evidence. Raw logs live in ignored
`.agent-artifacts/s1-implementation/`.

## Native QA failure and startup follow-up (2026-10-04)

Separate GLHF QA
tested `a41fd51a621343b678488ab1c6242c5b6ec7d08c` on Linux
7.2.5-3-omarchy, native Btrfs, Omarchy 4.0.4-1, Quickshell 0.3.1,
Python 3.14.7 and Node 26.8.1. Its unchanged default-worker checker failed the
<100-ms cached-page gate in both runs:

| Native entries | Small page (ms) | Five large pages (ms) |
| --- | --- | --- |
| 60,175 | 87.59 | 101.49, 104.60, 102.47, 81.55, 126.40 |
| 50,175 | 100.09 | 90.86, 106.55, 102.95, 96.89, 90.91 |

This is native evidence, not a Docker-only failure. QA also reproduced a test
assertion defect: a legitimate descriptor in a checkout whose ancestor contains
`/state/` was rejected as inherited history. The assertion now checks the actual
fixture history directory and its exact descendant boundary; dispatch/history
lock assertions remain active. With `TMPDIR` under `.agent-artifacts/state/tmp`,
the original test failed and the corrected test passed.

Implementation-host profiling separated actual default-worker startup, response
and reaping. Under a concurrent diagnostic run, launch to worker input took
36–165 ms, children computation 4–13 ms, and response to reap 6–30 ms.
No fixed 50-ms probe or 100-ms watchdog delay was demonstrated. The fresh
interpreter imports controller-only process creation/UUID modules on every page.
The follow-up defers those imports, creates the commit-control queue only for
scans, and keeps fresh exec, inherited-descriptor isolation, the EOF/owner
watchdog, deadlines and reconciliation unchanged. A blocked short query now has
an explicit EOF ownership regression. Cache validation is unchanged.

Three paired serial checker runs used the original Storage module versus the
candidate in otherwise identical copied trees, default workers, 60,175 entries,
Python 3.14.8, Docker `--init`, four ARM CPUs and Docker's local filesystem.
Each run measured a small page and five large pages; no serial local run missed
100 ms before or after. Across the three runs, small-page median was
45.97 → 36.82 ms; large-page median was 42.74 → 39.16 ms. Large-page ranges
were 38.10–54.77 ms before and 33.09–62.67 ms after. Noise means this is a
modest measured startup optimization, not proof that the native failure is
resolved or that every trial improved. Native GLHF default-worker retesting is
required; the <100-ms gate remains open. The checker preserves its JSON fields
and `PASS_WITH_TARGET_MISS` status, but now exits 1 on such a miss; only `PASS`
exits 0. A focused regression checks the exit result and retained JSON evidence.

The original native QA passed the other 130 Python tests and five Node suites,
backend functional/lifecycle checks, real Memory/History safety smoke checks,
three restart-history runs and restoration. The path-dependent assertion was
the one Python failure, with no skips. Optional offscreen QML stopped during
setup because the installed Omarchy package lacked the expected license file;
no offscreen pass was claimed. These live results remain tied to the original
SHA. Raw implementation-host comparison, descriptor reproduction and profiling
logs are in ignored `.agent-artifacts/qa-fix/`.

The follow-up passed all **133 Linux Python tests without skips** on Python
3.14.8 with `TMPDIR` beneath an artifact `/state/` ancestor, all five Node
suites, seven native pure Storage checks and whitespace checks. A final default
checker run on 60,175 entries reported `PASS`: small page 35.63 ms, five large
pages 48.46/55.54/61.47/53.16/48.23 ms, cancel ACK 0.23 ms, reconciled result
45.13 ms, refresh 0.22 ms, EOF cleanup 16.57 ms and owner-death cleanup
80.77 ms. Summary spacing peaked at 2.0003 s and progress spacing was at least
0.5000 s. Its copied-tree SHA is null; source hashes identify the tested files.
These local passing results do not close the native gate above.

## Remaining review and QA gates

Independent exact-head review/fixes and the separate
[GLHF Omarchy runbook](glhf-s1.md) are required before acceptance or merge.
That runbook includes isolated stores, restoration, the executable backend
checker, existing Memory/History safety/no hidden worker checks and **three**
live restart-history runs. The macOS implementation host cannot run these live
checks. Separate GLHF
QA completed them at the original SHA above; the revised startup path requires
a new exact-head backend run before performance acceptance. Real removable-media
loss, actual Btrfs/bind mount swaps, uninterruptible
kernel I/O, physical monitors/simultaneous panels and hardware suspend remain
explicit limitations; container injection does not replace hardware evidence.
S2 owns the Storage page, navigation/no-signal integration, bands/ledger and
settings; S3 owns complete live Storage failure/responsiveness integration.
Final UI/gallery documentation belongs to those milestones and the Storage
docs chore. The PR stays open and the tickets stay In progress for QA.
