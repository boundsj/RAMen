# Apps design: CPU, RAM and GPU attribution for grouped apps

RAMen's memory ranking misses a small process that saturates a CPU. The Apps
feature lets you pick the resource you care about (memory, CPU or video
memory) while keeping RAMen's app-scope and terminal-session grouping. It was
delivered in milestones:

| Milestone | Scope | State |
| --- | --- | --- |
| A1 / RAMEN-7 | Recent CPU accounting, separate memory/CPU coverage, complete bounded inventory, `apps.query` | Implemented |
| A2 / RAMEN-10 | Apps page: resource rails, search/sort/paging, details, stable selection, membership-aware kill confirmation, keyboard | Implemented ([below](#a2-the-apps-page)); native Omarchy QA in [final QA](../qa/final-qa.md#a2--apps-page-ramen-10) |
| A3 / RAMEN-13 | GPU/VRAM capability model and the DRM fdinfo attribution adapter | Implemented ([below](#a3-gpu-attribution)); real-GPU verification in [final QA](../qa/final-qa.md#a3--gpu-attribution-ramen-13) |
| A-DOC / RAMEN-16 | Final docs, [design rationale](#original-design), demo scene, credits/notices, gallery capture recipes | Delivered ([record](#a-doc-documentation-and-gallery-handoff)); native screenshots are captured and published in [final QA](../qa/final-qa.md#a-doc--apps-gallery-capture-and-publication) |

A1 added no page, setting or control; the Memory page only gained truthful
partial-memory rows (below). A2 adds the Apps page on top of A1's inventory.
Wire details are in the protocol: [A1 backend](../protocol.md#apps-backend-a1)
and [A2 page actions](../protocol.md#apps-page-actions-a2).

## One detail scan

The probe already ran a per-process scan every 2.5 s while any visible Memory
panel held a `detail` subscription. A1 extends that same scan; it does not add
a second one. Memory and Apps subscribers share it through the existing keyed
`detail` subscription (`Runtime.setSubscription`), so an Apps view subscribes
`{detail: true}` exactly as Memory does. With no subscriber the probe reads no
per-process files, keeps no CPU baselines and holds no inventory.

Each scan:

1. Lists `/proc`, keeps processes of this user, and reads `stat`,
   `smaps_rollup`, `cgroup` and `cmdline` once per process. The argument list
   only picks the display name (an untruncated `argv[0]`, or the script an
   interpreter runs) and is dropped with the scan: no snapshot, search, reply
   or history holds it. Full command lines appear only in [details](#details).
2. Turns `utime + stime` into a recent CPU rate (below).
3. Groups processes exactly as before: one group per systemd app scope, one per
   terminal shell session, and one per remaining process. Protected-process
   rules, names and IDs are unchanged.
4. Publishes the whole group list as an immutable inventory **snapshot**, then
   emits the legacy `apps` message: the Memory page's top-12 rows of at least
   1 MiB, plus the snapshot's ID and metadata.

`apps.query` never scans: it filters, sorts and pages a retained snapshot. A
burst of keystrokes is a burst of cheap in-memory queries. If the newest
snapshot is older than 5 s, a query asks the main loop for one refresh; scans
stay serial in the probe loop, so there is never a parallel scan.

## CPU semantics

- **Counters.** `/proc/PID/stat` is parsed with the existing
  last-closing-parenthesis rule, so names containing `)` or spaces cannot shift
  fields. `utime + stime` covers the whole thread group. `cutime`/`cstime`
  (reaped children) are never added: children are scanned on their own, and
  adding them would double-count.
- **Identity.** Baselines are keyed by `(pid, start ticks)`. A reused PID is a
  new process with no baseline. A process absent from a scan loses its
  baseline.
- **Rate.** `(ticks − baseline ticks) / SC_CLK_TCK / elapsed × 100`, where
  elapsed is the monotonic time between the two reads of that process's `stat`.
  `cpuCorePercent` is 100 for one fully busy logical CPU (a two-thread process
  can show 200). `cpuMachinePercent` divides by the online logical CPU count
  from `/sys/devices/system/cpu/online` (`SC_NPROCESSORS_ONLN` as fallback).
  Values are unrounded.
- **Warm-up and resets.** A first reading has no rate: `null` with status
  `warming-up`. The tracker resets all baselines when the panel's detail
  subscription closes (so reopening warms up again), when the probe restarts,
  after more than 7.5 s between scans (`gap`), when the boot clock outran the
  monotonic clock by more than 1 s (`suspend`), and when the online CPU list
  changes (`topology`). A scan reports the reason in `cpu.reset`.
- **Anomalies.** A counter that goes backwards, or a rate above 1.1× all
  online CPUs, rebaselines that process and reports `null` for this scan. It
  never shows as a spike or a negative value.
- **Fast rescans.** A kill or `refresh` can rescan within a second. Then the
  process keeps its older baseline and reports its last full-interval rate,
  because 0.3 s of 10 ms ticks would be too coarse.
- **Idle.** A measured idle process is `0.0`, distinct from `null`.

## Memory, CPU and coverage are independent

Before A1 a process whose `smaps_rollup` could not be read was dropped. Now it
stays in its group with memory `unavailable` and still contributes CPU. A
non-dumpable process of this user (one that disables tracing, as `ssh-agent`
does) has a root-owned `/proc` entry; the scanner then checks the real,
effective and saved user IDs in `status`, so these processes are included while
setuid programs such as `sudo` are not. Kernel threads, zombies and processes
whose address space is already gone are skipped.

Each group carries both readings and their coverage:

- `pss` / `swap` are the subtotal of members with readable memory, `null` when
  none has. `memoryStatus` is `available`, `partial` (a known lower bound) or
  `unavailable`; `memoryCoverage` counts measured members.
- `cpuCorePercent` is the subtotal of members with a rate. `cpuStatus` is
  `available`, `partial` (for example a child that just joined is warming up),
  `warming-up` or `unavailable`; `cpuCoverage` counts measured members.

A joining child therefore lowers coverage instead of adding its lifetime ticks
as a spike, and an exiting child simply drops out.

Truthful legacy Memory display: the Memory preview still ranks by known
footprint and needs at least 1 MiB of it, so a group with no readable memory is
never shown there as zero. A partial row keeps its count of all members, shows
its size as `≥` and says "memory partial" (`Level.footprintText` /
`Level.memoryNote`). Incident context records `partial: true` for such a group,
and receipts and toasts show its size with `≥`.

## Inventory and queries

The inventory is complete within internal ceilings: 8192 owned processes per
scan and 4096 groups per snapshot. Reaching a ceiling marks the snapshot
`complete: false` with `incompleteReason` (`process-limit`, `group-limit`; an
unreadable `/proc` gives `proc-unreadable`). The group ceiling keeps the top
groups of the memory and CPU rankings alternately, so neither lens silently
loses its leaders. The 1 MiB / top-12 cut applies to the Memory preview only.

Queries: at most 50 rows per page; at most 256 characters of literal,
case-insensitive search over group name and session host, plus an exact
member-PID match for a query of 1–10 ASCII digits (other Unicode digits such as
`²` stay literal name text; command lines are never searched).
`sort` is `memory` (known footprint) or `cpu` (core percent); unavailable
values sort last and ties break by group ID, so pages are stable. Every reply
names its snapshot. Passing that `snapshot` back pins later pages to it; the
last four snapshots (about 10 s) are retained, after which a pinned request
fails `snapshot-expired` and the client restarts from offset 0 on the newest
snapshot. Pages never silently duplicate or skip rows. A client also sends its
own query `generation` and accepts only the newest pending request id and
generation (`Runtime.appsQuery`, `Runtime.acceptsAppsReply`). Validation errors
echo that generation, so the client accepts them too; an invalid generation is
not echoed.

Each group row has a membership `generation`: an opaque hash of its
`(pid, start ticks)` members. The Apps page sends it with kill confirmations
so a changed group cannot be silently retargeted (A2, below). The legacy
`kill` command keeps its protected/self checks and PID start-time validation.

## Runtime helpers for A2

`RamanRuntime.qml` adds `requestApps(client, generation, params)` returning
the request id, an `appsMessage` signal for `apps-page` replies and apps
errors (request ids beginning `a:`), and `appsInventory` (the newest
snapshot's ID, time, inventory, CPU metadata and coverage, `null` while detail
is off). `Runtime.js` builds bounded commands and routes replies. A2 adds
`killAppMember` and `requestAppDetails` (below).

## Verification record (A1)

Automated, at the A1 commit:

- `tests/test_apps.py`: CPU units and denominators, one busy core and two
  threads, idle zero, joins/exits/reused PIDs in a terminal session, names with
  parentheses, missing and permission-denied PSS, non-dumpable versus setuid
  ownership, counter rollback, anomalies, gap/suspend/topology/reopen resets,
  fast rescans, kernel threads/zombies, process and group ceilings, query
  validation/paging/ties/nulls/byte bound, pinned and expired snapshots, stale
  refresh scheduling, and queries never reading `/proc` or signalling. Live
  tests start the real probe with only test-owned children: a low-memory
  `sh` busy loop outside the top-12 Memory preview behind 13 larger children
  is found by PID search and ranks first by CPU; closing and reopening detail
  warms up again; 30 rapid queries cause no extra scan; a non-dumpable child
  keeps CPU with memory unavailable.
- `tests/test_level.js`, `tests/test_runtime.js`, `tests/test_history_model.js`
  and the offscreen Qt tests cover null-safe Memory rows, query/reply routing
  and the inventory reference following the detail subscription.

These ran on macOS (Node, pure Python), in a Linux 6.8 container (full Python
suite, including procfs and live probe tests) and in the Qt 6.8.2 offscreen
harness. They are not live Omarchy evidence. Container scan timings were
within noise of the pre-A1 scan; native cost is a [final QA](../qa/final-qa.md#5-apps-additions)
item.

## A2: the Apps page

The page is **Apps**, the last page button (Memory / History / Storage /
Apps), also opened with `omarchy-shell boundsj.raman page apps`. It holds the
same `detail` subscription as Memory, so the two share one scan; leaving it or
closing the panel removes the subscription. Code: `AppsView.qml` (rendering
and wiring), `AppsModel.js` (pure state, rails and labels, tested under Node),
routing in `PanelNav.js`, and the commands in `Runtime.js`/`RamanRuntime.qml`.

### Layout: a grouped ledger with paired rails

```text
Sort [Memory] [CPU] [GPU memory · n/a]
[Search name, session or PID   ( / )                     ]
Size: PSS + swapped · RAM rail: resident PSS of 15G · CPU: % of 8 logical CPUs
Chrome (63 tabs …)                    2.5G     CPU 4.8%
48 processes                RAM |▬───|   CPU |▪────|    [i] [x]
Broth simulator (rolling boil)        392M     CPU ≥22%
3 processes · in Ghostty · CPU partial  RAM |·────|  CPU |▬▬──|   Kill?
```

- One row per app scope or terminal session (A1 grouping), two lines, so the
  420-pixel panel never squeezes four numeric columns. Line one: the name,
  the **footprint** (PSS + swap, the Memory page's number) and the group's
  **share of the machine's CPU**. Line two: what the group is (processes,
  session host, coverage notes, protected), then two slim rails aligned under
  those numbers.
- **RAM rail** = resident PSS ÷ physical RAM (`MemTotal`). Swap is not
  resident, so it is in the footprint number but not in this rail.
- **CPU rail** = `cpuMachinePercent` ÷ 100, its own denominator: all online
  logical CPUs. The details view also gives core-equivalents ("176% of one
  CPU").
- The rails have their own labels and denominators and are never added,
  compared or combined into a score. The header legend states both
  denominators. Rails have rounded ends and short end ticks: the paired
  RAM-stick/chopstick rhythm from RAMen's art. The RAM fill reuses the Memory
  page's share colours (warn at 10 %, critical at 25 % of RAM); CPU uses the
  theme accent, a category colour, never warning/critical.
- The sort lens changes ranking and emphasis (the lens's number is bold), not
  identity: the same groups, the same IDs.
- Narrow panels keep both lines and elide the name; rails widen on wide
  panels. Untrusted names and command lines render as plain text.

### Truthful readings

- Unknown is never zero. Missing memory shows `–` and an outlined, empty rail;
  partial memory shows `≥` and "memory partial". Missing CPU shows `CPU –`, a
  hollow rail and "CPU warming up" / "CPU unavailable" in the subtitle; a
  partial CPU subtotal shows `≥`. A measured idle group is `0.0%`.
- Header notes: CPU warming up after a reset, an incomplete inventory
  (process/group ceilings or unreadable `/proc`), a stale snapshot and demo
  data. A2 showed the GPU memory lens struck through and unselectable; A3
  makes it selectable only when a device reports per-client VRAM (below).

### Search, sort and paging

`/` focuses search (literal name/session text, or a member PID). Typing
re-queries the cached inventory after 150 ms; Enter applies it and returns to
the list; Escape leaves search keeping the query. The lens is chosen with the
chips or `h`/`l` in the sort row. Pages are 50 rows (`]`/`[` or the buttons);
the next page pins the snapshot the current page came from, an expired pin
restarts at the first page with a note, and every new scan refreshes the
current page. A client generation per query drops late replies
(`AppsModel.accept`).

### Stable selection and the frozen pointer

- The selection is a group ID (`selectedId`), never a row index. After a
  reorder the same app stays selected and the list keeps it in view; if it
  leaves the page it stays the selection (nothing on the page acts in its
  place).
- While the pointer is over the list, or a kill is armed, live updates are
  **queued** instead of reordering rows under an imminent click; the footer
  says updates are paused. Leaving the list or disarming applies the newest
  queued page. The user's own search, sort and page changes apply at once
  (they disarm first). Transient messages (disarm reasons, errors) appear
  below the list so they cannot shift rows under the pointer either.
- Each query names the armed (else the selected) group as `focus`, so a
  queued reply still reveals a changed membership or a vanished group and
  disarms at once with a note. A group that only moved to another page is not
  mistaken for gone.

### Guarded kill: same two steps, checked membership

- Keys and timings are the Memory page's: `x`/Enter arms SIGTERM ("Kill?"),
  `f` or a right-click arms SIGKILL ("Force?"), the same key again confirms,
  Esc or 4 s disarms, and a group still running 4 s after SIGTERM is offered
  SIGKILL next. Protected rows show the lock and never arm.
- Arming records the row's membership `generation`. Confirming sends
  `apps.kill` with that **armed** generation; the probe refuses a group whose
  newest scan or current `/proc` membership differs (`stale-membership`). The
  current membership is rebuilt at confirmation from `stat` and `cgroup` only
  (one pass, no memory/CPU reads), so a child that joined since the last scan
  refuses the kill too; an incomplete read is `unverified`, including any
  process whose ownership, `stat` or `cgroup` is unreadable or malformed (only
  exited, kernel, zombie and other users' processes count as non-members).
  Each verified PID is then re-checked immediately before its own signal
  (pidfd, start time, owner, protection), and a failed `pidfd_open` never
  falls back to a numeric signal.
  Check and signals are milliseconds apart, not atomic; see the
  [protocol](../protocol.md#appskill) for the remaining limits.
- Disarm triggers: Esc, the 4-second timeout, moving the selection, leaving
  the list, focusing search, any search/sort/page change, opening details, the
  armed group disappearing or its membership changing, leaving the page and
  closing the panel.
- The Memory page keeps the legacy `kill` command and behaviour.
- No bulk, pause, restart or service actions exist.

### Details

`d`, the ⓘ button or a double-click opens one group's details in place of the
list: footprint split into resident PSS and swap, the RAM rail value and
denominator, CPU as machine share and core-equivalents with coverage, and its
largest 40 members with PID, name, size, CPU and command line. Details are
read once on request (`r` re-reads), are read-only (Enter/`x`/`f` do
nothing there), and are dropped when closed. Each command line is read fresh
from `/proc/PID/cmdline` (at most 2049 bytes) for the listed members only, kept
only if the PID still has its scanned start time, shown as at most 512
printable characters, and never stored, cached, logged or written to history.
Details request generations keep counting for the view's lifetime (closing
does not reset them), and a reply is shown only for the pending request ID,
generation and group ID, so a late reply or error for a closed request never
appears under a reopened one.

### Keyboard

| Region | Keys |
| --- | --- |
| Page buttons | `h`/`l` pages, `j` to the sort row |
| Sort row | `h`/`l` lens, `k` page buttons, `j` list |
| List | `j`/`k` move (k at the top: sort row), `x`/Enter arm/confirm, `f` force, `d` details, `]`/`[` pages |
| Anywhere on Apps | `/` search, `r` refresh |
| Search field | Every key is text; Enter applies, Esc leaves |
| Details | `j`/`k` scroll, `d` or Esc close, `r` re-read |

Escape order: disarm, then leave search, then close details, then close the
panel. Tab/Shift+Tab still switch Omarchy bar panels. History and Storage
never route any key to a kill (`tests/test_panel_nav.js` checks every page,
region, context and key).

### Demo scene

`omarchy-shell boundsj.raman demo apps` replays an original synthetic scene
from `docs/demo-scenes.json` (noodle-kitchen names, eight logical CPUs):
partial and unavailable memory, CPU warming up and partial, protected rows,
an idle zero, synthetic member command lines for details, and a
`spin-the-noodle.sh` CPU hog with 896 KiB of memory, below the Memory
preview's 1 MiB cut, that the CPU lens ranks near the top. Demo kills remove
only a fake row whose fake membership matches; nothing is signalled. The
Memory page in a demo now applies the live preview rule (scene order, at least
1 MiB, twelve rows), which leaves the existing scenes unchanged.

A3 adds a synthetic `gpu` block to the same scene (an `amdgpu`-like discrete
device with a reported VRAM total and an `i915`-like integrated one) and GPU
readings to existing rows: a 6.2 GiB VRAM broth simulator with engine shares,
partial Chrome, a measured `0` mixer, integrated-GPU buffers in RAM, a
compositor whose only client is shared with Quickshell, and unreadable,
warming-up and no-client rows. A-DOC adds a third, runtime-suspended
`xe`-like device and a "Gyoza render queue" row whose only client is on it,
so the page's asleep wording ("GPU asleep", "RAMen does not wake it") can be
shown and captured; like a live sleeping device it has no reading, no client
count and no invented total. They appear only while the Apps page has GPU
metrics on (`gpu 1`), marked `demo`/`synthetic`; a demo never reads a real
GPU. Other scenes report GPU `unsupported` (`demo-without-gpu`). Stale GPU
readings and a page-wide CPU warm-up are time-dependent live states the scene
does not fake; Python fixtures and the Node model tests cover their wording.

The page's rationale and its comparison with the studied plugins are in
[Original design](#original-design).

## Verification record (A2)

Automated, at the A2 commit:

- `tests/test_apps_model.js`: default-sort fallback, stale replies, ID
  selection across reorders and pages, hover freeze/queue/reconcile, user
  changes while frozen, arm/confirm with the armed membership, disarm on
  membership change/disappearance/inactive/moves/search/sort/page, force and
  stubborn escalation, kill states and timings, pinned paging and expiry,
  labels/rails/coverage text and details acceptance (group identity, and
  generations that survive close/reopen).
- `tests/test_panel_nav.js`: Apps regions, keys and Escape order; every
  page × region × context × key, where only Memory's list and Apps' list
  (details closed, no editor) can reach a kill action.
- `tests/test_runtime.js`: focus/kill/details commands, reply routing and
  kill bookkeeping for Apps rows outside the Memory top 12.
- `tests/test_apps_actions.py`: request validation; focus presence/rank (no
  `/proc` reads); fake-`/proc` scans where a joining child or reused PID
  changes membership between arm and confirm (refused, nothing signalled) and
  the same members are accepted; joins, exits, reuse, moves and re-parenting
  after the last scan with no second scan (refused at confirmation), the
  verified members being what is signalled, `unverified` at the process limit
  or unreadable `/proc`, `unverified` (nothing signalled) when a newly joined
  child's `stat`, `cgroup`, directory, owner or root-owned `status` is
  unreadable (real `EACCES` as non-root, injected errors otherwise) or
  malformed, while exited-mid-read, kernel, zombie and other users' processes
  (never read) still verify, late protection; vanished/protected/inactive
  refusals; `member_verdict` identity/protection/owner; `signal_group` with
  patched process primitives (a PID reused, foreign or protected between the
  preflight and its signal, the numeric fallback re-checking right before
  each `kill`, pidfd failures never falling back); bounded details with on-demand command reads
  and identity checks; the demo scene and demo kills. Linux tests signal only
  test-owned children: start-time mismatch skipped, a member that became
  "protected" (a `pipewire`-named `sleep`) refuses the whole group, legacy
  reply shape; with `RAMAN_TEST_CGROUP_ROOT` (a writable cgroup2 root, for
  example a privileged container), a real `app-…scope` whose test-owned shell
  forks a child (or loses one) after the scan, refused without signalling; and
  against the real probe, a TERM-ignoring child gets SIGTERM
  then SIGKILL through `apps.kill`, a stale membership and a vanished child
  are never signalled, details show a child's command line which never reaches
  the state directory, scan internals never leave the probe, and the probe's
  worker and parent are protected.
- `tests/qml/tst_apps.qml` (Qt offscreen, real Panel/PanelKeyCatcher with a
  recording process stub): shared scan, two-step membership kill by keys,
  4-second expiry, membership change and disappearance disarms, pointer
  freeze with the right-click/confirm target kept, input-safe search, Escape
  order, read-only and dropped details, stale replies (including a late reply
  after close and reopen, for another group or the same one), sort/page
  disarm, a click on the already selected sort keeping the 4-second expiry, and
  pinning, expired snapshots, GPU fallback, protected rows and no kill from
  sort/nav/Storage, plus renders at 340/420/640 px in dark and light themes.

These ran on macOS (Node; Python without `/proc`), in the
`raman-s2-fixes:local` Debian 13 container (Linux 6.8, Python 3.13.5; full
Python suite as root and as uid 1000) and in its Qt 6.8.2 offscreen harness at
1× and 2×. Offscreen renders are not native screenshots, and none of this is
Omarchy evidence: real input focus, pointer, theme, narrow-output and
multi-monitor checks are [final QA](../qa/final-qa.md#a2--apps-page-ramen-10)
items. Container scan timing with the added member list was within noise of
A1 (403 groups: 28.7–31.2 ms versus 29.6–38.3 ms medians; focus queries
≤0.5 ms).

After the first A2 review the same container runs also cover the
confirmation-time membership check (the real `app-…scope` join/exit tests run
as root in a privileged container with a private cgroup namespace; one
non-dumpable-memory test skips there under `CAP_SYS_PTRACE` and runs in the
unprivileged root and uid 1000 runs). The confirmation pass took a median
11.6 ms (max 21.4 ms) with 403 processes, against 31.2 ms for a full scan;
it runs once per confirmed kill.

## A3: GPU attribution

A3 adds per-app GPU memory and engine readings from one production adapter:
the kernel's DRM client usage stats. Code: `raman_gpu.py` (collection,
parsing, attribution, sampler lifecycle), its wiring in `raman_probe.py` and
`raman_apps.py`, and the lens, labels and details in `AppsModel.js` /
`AppsView.qml`. Wire format: [protocol](../protocol.md#apps-gpu-attribution-a3).

### Primary sources

Written from these, not from another monitor's implementation (fetched
2026-10-06; Linux `master` at `69f80fef3153299d9c72c53d1d71eef6354b6926`):

- [DRM client usage stats](https://docs.kernel.org/gpu/drm-usage-stats.html)
  (`Documentation/gpu/drm-usage-stats.rst`): key format, `drm-client-id`
  identity and its uniqueness rule, `drm-pdev`, engine time/cycles/capacity
  semantics including "stay with the larger previous value", memory
  `total/shared/resident/purgeable/active` and the reserved `memory` region,
  amdgpu's deprecated `drm-memory-*` alias.
- `drivers/gpu/drm/drm_file.c`: `drm_show_fdinfo` (driver, client ID from a
  global counter, PCI address) and `drm_fdinfo_print_size` (bytes, KiB or
  MiB).
- [i915](https://docs.kernel.org/gpu/i915.html#i915-drm-client-usage-stats-implementation)
  and `i915_drm_client.c`, `intel_memory_region.c`: engines `render`, `copy`,
  `video`, `video-enhance`, `compute` in ns with capacity; regions
  `system0`, `local0`, `stolen-system0`, `stolen-local0`.
- [xe](https://docs.kernel.org/gpu/xe/xe-drm-usage-stats.html) and
  `xe_drm_client.c`: `drm-cycles-*` with `drm-total-cycles-*` and capacity;
  regions `system`, `gtt`, `vram0…`, `stolen`; and that `show_run_ticks`
  takes a runtime-PM reference and forcewake, so reading xe fdinfo wakes the
  device.
- `amdgpu_fdinfo.c`: regions `vram`, `gtt`, `cpu` (+ `gds`, `gws`, `oa`,
  `doorbell`, `mmioremap`), both `drm-resident-*` and `drm-memory-*`,
  `amd-*` keys, engines `gfx`, `compute`, `dma`, `dec`, `enc`, `enc_1`,
  `jpeg`, `vpe` in ns, printed only once used. `amdgpu_vram_mgr.c`:
  `mem_info_vram_total`/`mem_info_vram_used` are TTM counters (no hardware
  access).

### Identity and deduplication

A client is one open DRM file: `(device, drm-client-id)`. The device comes
from the descriptor itself (character device major 226 → `/sys/class/drm/*/dev`
→ the bus device), so `card1` and `renderD129` of one GPU are one device and
the same client ID on two devices is two clients; a `drm-pdev` that
disagrees with that device is an error. Several descriptors or processes
holding one client (dup, fork, descriptor passing) count it once. A
descriptor without `drm-client-id` cannot be deduplicated, so it is never
counted and is reported (`unidentified`).

Attribution follows RAMen's groups at each scan. A client seen only within
one group belongs to it. A client reachable from two groups (a launcher that
passed its descriptor, say) is ambiguous: it is counted once in the device's
`crossGroupVramKb` and in neither row, and both rows say `sharedClients`.
Processes newer than the last GPU sample, or whose descriptors are
unreadable, lower the row's `gpuCoverage`; a row with no readable member is
`permission-denied`, never zero. A reused PID (start ticks differ) is not
attributed.

### Memory semantics

- **Device-local (VRAM)**: regions `vram*` and `local*`. This is the GPU
  memory lens and the `VRAM` numbers, using **resident** bytes
  (`drm-resident-*`, else amdgpu's alias; never both). On an integrated AMD
  GPU this region is a firmware carve-out of system RAM; it is still the
  device's own region and is never added to RAM figures.
- **System RAM in GPU buffers**: `memory`, `system*`, `gtt`, `cpu`. Shown in
  details as "GPU buffers in system RAM … (part of RAM, not added)". These
  pages are part of the machine's RAM use and may or may not be inside a
  process's PSS; RAMen does not add them to footprints or rails.
- **Other** regions (stolen, GDS, doorbell…) are listed, never summed.
- **Allocated** (`drm-total-*`) is reported beside resident, never instead of
  it. A class whose regions do not all report a key is `null`, not partial
  zero.
- **Device totals**: amdgpu's `mem_info_vram_used`/`_total` give a device
  total; RAMen reports the part not attributed to this user's visible
  clients (other users, the kernel, firmware, invisible processes). Clients'
  shared buffers are counted by each holder (`drm-shared-*`), so the
  difference is a lower bound when any are shared, or when several resident
  clients print no `drm-shared` key (legacy amdgpu, Linux 6.8) and may share
  one allocation, and is withheld when the overlap makes it negative. Other drivers expose no total; none is invented.
- **Shared buffers across clients.** The kernel counts a buffer shared
  between DRM files in every client holding it (`drm-shared-*`, the spec's
  [memory section](https://docs.kernel.org/gpu/drm-usage-stats.html#memory);
  `drm_show_memory_stats` and the drivers' `is_shared_for_memory_stats`
  checks) and prints no buffer identity, so RAMen cannot deduplicate buffers.
  A sum over two or more clients that both hold resident memory and shared
  buffers (or print no `drm-shared` key) may count one buffer once per client:
  it stays a client-reference total marked `vramOverlap: possible` (row
  `gpuMemoryOverlap`), shown as `≤` (an upper bound of the app's own VRAM; `~`
  when also partial: row `gpuComplete` false, which a stale reading keeps),
  labelled "client sum, shared buffers may repeat", given
  no VRAM meter, and counted in `coverage.gpuOverlap` so the lens says some
  ranks may be overstated. A buffer reached by only one summed client is
  counted once there and is reported as shared (`vramSharedKb`). Two clients
  of one app each referencing one 4 GiB buffer on an 8 GiB device therefore
  read `VRAM ≤8.0G`, "summed over 2 clients … not physical VRAM", beside the
  device's own "VRAM used 4.0G of 8.0G", not as a full device meter.

### Engines

Per client and engine: busy ns over the monotonic interval between that
client's two reads; or GPU cycles over total cycles (GPU clock domain); or
cycles over max frequency × interval; divided by capacity (1 when absent).
Group values sum their own clients per engine name on one device (a share of
that engine class). Engines are shown separately in details, each against its
own class, and never summed or clamped into a GPU percentage; the lens sorts
by memory. First readings and capacity changes warm up; a counter below its
previous value is `stale` and the previous, larger value is kept until it
catches up (the spec's rule); zero capacity and readings above 110 % are
errors (the latter rebaselines). A client whose `fdinfo` read took longer
than 0.1 s has no usable time for busy-time engines (the counter was taken at
an unknown moment of the read; xe's `show_fdinfo` can sleep on buffer locks,
though its GPU-cycle basis carries its own reference): those engines are
`error` (`slow-read`) and rebaseline. Baselines reset after 15 s without a sample
(`gap`), a suspend, or any deactivation.

### Power, cadence and lifecycle

- **Never wake a sleeping GPU.** Identity, driver, PCI IDs and amdgpu's VRAM
  total are sysfs attributes and symlinks, cached per device. Before each
  client's `fdinfo` read the device's `power/runtime_status` is checked; a
  `suspended`/`suspending`/`resuming` device is `asleep`, and its clients are
  only known to exist (from `/proc` symlinks and `stat`, which do not open the
  device). The check and the read are microseconds apart; a device that
  suspends in between would be resumed by that one read.
- **Only for a visible Apps page.** The page's subscription carries
  `gpu: true` unless `gpuMetrics` is `off`; Memory panels never do. The probe
  samples only while `gpu` and `detail` are both on and no demo plays.
- **Cadence and worker.** At most every 5 s, after a detail scan, in one
  daemon worker thread. The 5 s spacing is between physical sample starts and
  survives closing/reopening, `gpuMetrics` off/on and demos, which only drop
  baselines and the last sample. The main loop never waits for it: the
  summary, IPC and JSON commands continue while it reads. A worker still
  running after 10 s is reported (`error`, or `stale` with the previous
  values) and no second one starts until it returns; its late result, and any
  sample whose reads took over 10 s, is kept but stays `stale`
  (`sample-timeout`) until a timely sample replaces it. Freshness is measured
  from when a sample began reading (`sampledAt`, `ageSeconds`), never from
  when it was handed over, so a stalled sample is never presented as fresh
  (stale after 15 s). Each sample carries the sampler's
  generation; deactivation, reopening, a demo and probe restart bump or
  discard it, so a late result never lands in a newer session. The thread
  dies with the probe; stdin EOF, parent death and SIGTERM exit as before.
- **Bounds.** 16 devices, 4096 descriptors per process, 65536 descriptor
  entries and 1024 `fdinfo` reads (16 KiB each) per sample; 16 engines and
  16 regions per client. Hitting one marks the sample incomplete and the
  affected rows partial. Replies stay within the 128 KiB page bound.
- **Privacy.** `drm-client-name` (set by applications) is never read into
  RAMen; no GPU reading is persisted or added to history.

### Page behaviour

- The **GPU memory** chip is selectable once a device has given a
  device-local resident reading (`gpu.memoryLens`). Then the row's right
  number is `VRAM` (bold) and its CPU share moves to line two; RAM and CPU
  rails stay as they are. In the other lenses a GPU-using row's line two adds
  `VRAM x`, "GPU buffers in RAM x", "GPU asleep", "GPU unreadable" or "GPU
  shared with other apps". Rows without a reading sort last and show `VRAM –`;
  a measured zero is `0K`; `≥` marks partial coverage and `≤` a possibly
  overlapping client-reference sum (see shared buffers above).
- `appsDefaultSort: gpu-memory` opens on Memory with a note, then switches
  once the first GPU sample shows the lens works (never under the pointer or
  an armed kill: it waits), or keeps Memory and states the reason (no DRM
  device, no VRAM per client, GPU metrics off). Turning `gpuMetrics` off
  while the GPU lens is shown returns to Memory with a note.
- Details add one block per device: resident and allocated VRAM, GPU buffers
  in system RAM, per-engine shares with their basis, client/shared/unread
  counts, the driver's device total and the unattributed remainder, shared
  buffers and whether the sum may repeat them, and a VRAM meter against the
  device total where the driver reports one and the reading counts each
  buffer once.
- Selection, the frozen pointer, pinned paging, the two-step kill with its
  armed membership, protection and details are unchanged; GPU data never
  feeds membership, protection or signals.

### Support and limits

- **Supported**: any DRM driver that prints `drm-client-id` and memory or
  engine keys. From the sources: amdgpu (VRAM, GTT, engines), xe (VRAM tiles,
  system/GTT, cycles), i915 (engines; `local*` VRAM on discrete parts,
  `system0` on integrated), and drivers that use the core
  `drm_show_memory_stats` (`memory` region only, so no VRAM lens).
- **Not supported**: NVIDIA's proprietary driver. RAMen has no `nvidia-smi`
  adapter; a device whose clients print no usage keys is `unsupported`, so
  RAMen claims nothing for it. Compute-only or vendor-tool coverage is not
  attempted.
- Only this user's processes are read. Other users' clients, kernel and
  firmware allocations appear only in a driver's device total.
- `hidepid` and non-dumpable processes: their descriptors are unreadable, so
  those members are `permission-denied` (coverage drops), never zero.
- Integrated-only machines have no VRAM lens; their GPU buffers are system
  RAM shown in details.

## Verification record (A3)

Automated, at the A3 commit:

- `tests/test_gpu.py` (fixture sysfs and procfs trees, any platform): the
  documented i915/xe/amdgpu key formats with RAMen's own values; amdgpu's
  `drm-memory-*` alias never added; non-DRM, no-usage-stats and malformed
  input (bad units, negative values, path-like `drm-pdev`, overlong key
  lists, `drm-client-name` never kept); region classes; engines (busy time,
  capacity, GPU cycles, max frequency, cycles alone unsupported, counter
  behind keeps the larger value and catches up, capacity change, zero
  capacity, anomaly); one client over `card`/`renderD` and a forked child
  counted once; a client shared by groups counted once on the device and in
  no row; the same client ID on two devices; missing client ID and a
  no-stats `nvidia` device never counted; successful zero versus no clients
  and the `gpu-memory` sort (zero before unknown); partial and
  permission-denied coverage; a suspended device never read (no `fdinfo`,
  no `mem_info_vram_used`) and a device going to sleep mid-sample; an
  unreadable power state; no runtime PM; reused PID and `drm-pdev` mismatch;
  device residual exact, lower-bound and withheld; descriptor/read/size
  bounds; no DRM device means no process walk; engine gap reset; sampler
  cadence (no resample at 2.5 s), one worker at a time with the 10 s timeout
  and a late same-generation result, deactivate/reopen dropping an old
  generation without a duplicate worker, failures, stale labelling, suspend
  reset; probe wiring (gpu only with detail, never in a demo), the synthetic
  demo, query sorting and the reply bound. Linux-only: the real `/proc` walk
  over test-owned children (start-time check, a non-dumpable child is
  `permission-denied`), and the real probe (Memory alone reports `inactive`,
  `gpu 1` reports `unsupported`/`no-drm-devices` without hardware, `gpu-memory`
  queries, `gpu 0`, gpu without detail stays inactive, demos never sample).
- `tests/test_apps_model.js`, `tests/test_runtime.js`,
  `tests/test_panel_nav.js`: lens availability, default-lens switch and
  waiting, fallback reasons, setting changes, labels, details lines and
  meters; `gpu` subscriptions only from Apps.
- `tests/qml/tst_apps.qml`: subscription follows page/setting/close, the
  default lens switching (and waiting for an armed kill), off keeping the lens
  unavailable with the kill path unchanged, details per device with the
  meter, and GPU-lens renders at 340/420/640 px in dark and light themes.

These ran on macOS (Node; Python fixtures), in the `raman-s2-fixes:local`
Debian 13 container (Linux 6.8, Python 3.13.5; full suite as root, privileged
and unprivileged, and as uid 1000) and in its Qt 6.8.2 offscreen harness at 1×
and 2×. **No GPU hardware was available**: no real DRM client was read, and
the container has no `/sys/class/drm` devices. The fixtures follow the
documented formats; real driver output, units, power behaviour and cost on
GPUs are [final QA](../qa/final-qa.md#a3--gpu-attribution-ramen-13) items.

Container cost (uid 1000, 4 CPUs, 402 test-owned processes with 3259
descriptors, a fixture sysfs device so the real walk runs): the GPU walk took
31 ms median (50 ms max) per sample, in the worker thread at most every 5 s,
against a 69 ms detail scan; attribution into groups took 0.5 ms per scan.
400 fixture DRM descriptors (100 shared clients) took 47 ms to read and parse
and 2.3 ms to attribute. With `detail 1` the live probe's summary interval was
2.004 s mean / 2.012 s max with `gpu 0` and the same with `gpu 1`. Memory-only
and closed panels do no GPU work (`gpu.status` `inactive`, no samples).


## Original design

The Apps page answers "which of my apps is using the resource I care about
right now?" without giving up what makes RAMen's Memory page useful: one row
per app or terminal session, PSS-based sizes, and a kill that cannot hit the
wrong thing.

### Information hierarchy

1. **What is measured, against what.** The header holds the lens chips
   (Memory / CPU / GPU memory), the search field and a one-line legend that
   names every denominator: size is PSS + swapped, the RAM rail is resident
   PSS of this machine's RAM, CPU is a share of N logical CPUs, and (in the
   GPU lens) VRAM is device-local resident memory, never added to RAM.
2. **Why a number may be incomplete.** Notes sit under the header, most
   important first: demo data, an incomplete inventory, CPU warm-up, an older
   scan, and in the GPU lens synthetic, stale, overlapping, shared and asleep
   GPU states. They are page-wide, so rows are not repeated with them.
3. **The app.** Each row is two lines. Line one is identity and the two
   numbers that matter: name, footprint and CPU share (or VRAM in the GPU
   lens), the chosen lens's number in bold. Line two says what the group is
   and how much of it was measured (process count, terminal host, `memory
   partial`, `CPU warming up`, GPU notes, `protected`) beside the two rails.
4. **Evidence on request.** Per-process rows, command lines, the resident
   PSS / swap split, core-equivalents and per-device GPU blocks are one
   keypress away in details, never a wall of PID rows by default.
5. **State of the list.** Range and pager sit in the footer; transient
   messages (disarm reasons, errors, paused updates) sit below the list, so
   they never push rows under the pointer.

### Geometry

- Two-line rows keep the 420-pixel panel readable: the name elides first,
  while the value and CPU columns hold fixed widths (20 % of the panel,
  clamped to 64–128 and 66–128 layout units) and a fixed 50-unit action slot,
  so numbers stay aligned whether or not a row shows its buttons.
- The rails sit under the numbers they explain: RAM under the footprint, CPU
  under the CPU share. Each is a 4-unit track with fully rounded ends and two
  short end ticks (1 unit wide, standing 2 units above and below the track):
  the paired RAM-stick/chopstick rhythm of RAMen's artwork, drawn at run time.
- A missing reading is an outlined, empty track, never a zero-length fill. A
  non-zero fill is at least as long as the track is tall, so a small reading
  stays visible but is never confused with a full one.
- Colour carries category, not ranking: the RAM fill reuses the Memory page's
  share colours (warning at 10 %, critical at 25 % of RAM); CPU is always the
  theme accent, so a busy CPU never looks like a memory warning.
- There is no VRAM rail in the list. Details draw a VRAM meter against the
  device's own total, only where the driver reports one and the reading
  counts each buffer once.

### Interaction

- The lens changes ranking and emphasis, never identity: the same groups and
  IDs in every lens, so switching from Memory to CPU keeps the selection.
- Selection follows a group ID through reorders and pages. While the pointer
  is over the list or a kill is armed, updates queue; leaving or disarming
  applies the newest one.
- Kill keeps the Memory page's two steps, keys and 4-second timing, and binds
  the confirmation to the armed group's exact membership; details use a new,
  separate key (`d`) instead of reusing a kill key.
- Escape unwinds one layer at a time: disarm, leave search, close details,
  close the panel.

### Influences and comparison

The roadmap's competitive study (2026-10-03) reviewed two Omarchy plugins for
this feature. To describe them here, A-DOC read their READMEs and the files
the study had read (SysMon's `DetailPopup.qml` and `procprobe.sh`; User
Services' `vram.sh`) at the revisions below, fetched 2026-10-06. No code,
layout, text or image from either is in RAMen, and no screenshot of either is
published. The table describes those revisions, not later ones.

| | SysMon (`gdeyoung.sysmon`, `95743fdacd925bbd9b0af7b6a5ea960943b40a52`) | User Services (`io.github.gabepsilva.user-services`, `cb925915f08e0049dc31b35219542dc6c5754fcf`) | RAMen Apps |
| --- | --- | --- | --- |
| Idea RAMen took | Flip one process list between CPU and memory ordering | Attribute video memory to a useful workload group rather than a PID | A resource lens over RAMen's existing app groups; per-app VRAM over the same groups |
| Unit of a row | A process: a short top-CPU list from `ps` in a Processes tab | A systemd user service, with start/stop/restart switches | An app scope or terminal session, every group searchable and paged; no lifecycle controls |
| CPU | `ps` `%CPU` | — (CPU time in its details view) | Recent rate from `utime + stime` deltas keyed by `(pid, start ticks)`, machine and core units, explicit warm-up and coverage |
| Memory | `ps` `%MEM` | cgroup `anon + shmem + kernel` per service | PSS + swap footprint, resident PSS rail, partial/unavailable coverage |
| GPU | Device-level load and VRAM/GTT meters (amdgpu sysfs) | Per-process `drm-total-vram` (or amdgpu's legacy `drm-memory-vram`) from DRM fdinfo, plus `nvidia-smi` compute processes | Per-device resident VRAM (allocated beside it), clients deduplicated by `(device, drm-client-id)` across processes, cross-group clients only on the device, overlapping shared-buffer sums marked `≤`, per-engine shares, sleeping GPUs not read, no NVIDIA adapter |
| Kill | TERM, then Force kill (9) if the process survives a refresh | — (service stop) | Two-step TERM/KILL with 4 s expiry, protected rows, membership-checked confirmation, pidfd-pinned signals |

Argus (`io.github.diegopluna.argus`) was the roadmap's optional reference for
capability-aware GPU process data; its GPU material was not consulted for
Apps, so it is credited only for History. Memory Usage
(`dev.egoist.memory-usage`) was listed as an optional later inspiration and
was not used.

## A-DOC: documentation and gallery handoff

A-DOC reconciled the README, this design, the [protocol](../protocol.md),
the settings table, [CREDITS.md](../../CREDITS.md),
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md), LICENSE and
[artwork provenance](../art/README.md) with the shipped code. One correction
of substance: earlier text said command lines are read only for details; the
scan also reads each process's argument list to choose its display name, and
the docs now say so (nothing about it is kept).

It added the asleep GPU device and row to the `apps` demo scene with a
regression assertion (`tests/test_gpu.py`). On 2026-10-06 it drove the
gallery scenes through the real `raman_probe.py` JSON protocol as uid 1000 in
the `raman-s2-fixes:local` Debian 13 container (Linux 6.8.0-100, Python
3.13.5; no Omarchy, no GPU, nothing signalled):

| Recipe | Probe outcome |
| --- | --- |
| Live `detail 1` | First scan `cpu.status` `warming-up` (`reset` `start`), the next `available`; GPU `inactive` |
| Live `gpu 1`, no DRM device | GPU `unsupported`, `no-drm-devices`; `memoryLens` false |
| `demo apps`, `gpu 1`, each lens | 14 synthetic rows, `demo`/`synthetic` GPU, `memoryLens` true; GPU order Broth 6.2 GiB, Steam Table (overlap `possible`), partial Chrome, measured `0` mixer, then unknowns; row states `available`, `partial`, `permission-denied`, `none`, `asleep`, `warming-up`, `shared`; the 896 KiB CPU hog second by CPU, found by search, absent from the 12-row Memory preview |
| `apps.details` | Steam Table: one device entry with overlap `possible`; Gyoza: `asleep`, no reading; Broth: three synthetic members |
| `demo apps`, `gpu 0` | Every row `inactive`; no synthetic GPU reading |
| `demo red`, `gpu 1` | GPU `unsupported`, `demo-without-gpu`; CPU `unavailable` |

The same replies were fed through `AppsModel.js` under Node to confirm the
labels the gallery should show (`≤1.4G` with "client sum, shared buffers may
repeat" and no meter, `≥412M`, `0K`, "GPU asleep", "GPU unreadable", "GPU
shared with other apps", "CPU warming up", the asleep and cross-group notes).
This historical check covers recipes only, not widget rendering, native,
visual or GPU evidence. Final QA subsequently published a representative
[native CPU-lens capture](../screenshots/final-qa/README.md), with
[masters and provenance](../media/masters/final-qa/provenance.json), plus
current Memory/History/Storage captures. The [execution record](../qa/final-qa-evidence.md)
preserves what ran; the wider native gallery and remaining A1/A2/A3 hardware
checks remain unverified.

## Credits

SysMon (Greg DeYoung) and User Services (Gabriel da Silva) are the Apps
feature's credited influences: SysMon for switching one list between CPU and
memory ordering, User Services for attributing video memory to a useful
workload group. Both are **inspiration, not adaptation**: A1's backend was
written from `proc(5)` and kernel semantics; A2's page, rails, interaction
and protocol were designed for RAMen; A3's adapter was written from the
kernel sources listed under [Primary sources](#primary-sources). The A1–A3
implementers did not consult either project's code, assets, layouts or
text; the comparison above was written from the roadmap's saved copies. See
[CREDITS.md](../../CREDITS.md) for project, author, package and revision
details and [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md) for the
statement that no third-party material is incorporated.
