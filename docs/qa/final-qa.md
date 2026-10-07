# Final pre-release hardware, visual and gallery QA (RAMEN-17)

Use this runbook for future native checks of a pinned candidate. The
[verification summary](final-qa-evidence.md) records the completed coverage and
known limits; unchecked recipes below are not claimed to have passed.
Earlier [H3](glhf-h3.md), [S1](glhf-s1.md), [S2](glhf-s2.md) and [S3](glhf-s3.md)
procedures retain their original revision context.

Coordinate monitor disconnection, suspend and removable-drive operations with
the person using the machine. Isolate test history/cache, preserve production
data, and restore normal settings and the intended installation afterward.

## Explicitly excluded

The following hardware scenarios are outside this runbook's scope. They are
not claimed as passing checks.

- Read-only-source and unsupported-filesystem hardware QA (for example a real
  read-only mount or an unsupported physical filesystem). Automated and injected
  coverage of those states remains as it is; it is not a hardware claim.
- Real stalled or uninterruptible disk-I/O recovery. The design limit stays
  documented: a worker in uninterruptible kernel I/O finishes dying only when
  the kernel returns, and the probe never waits for it.

Removable-drive identity and remount checks remain **in scope** below.

## 1. Pin, isolate and baseline

Use an unlocked Linux 6.8+ Omarchy session. Pin the candidate's full SHA
(and PR URL if one is open).

```bash
export RAMAN_FINAL_SHA=FINAL_CANDIDATE_FULL_SHA
test "$(git rev-parse HEAD)" = "$RAMAN_FINAL_SHA"
test -z "$(git status --porcelain)"
export QA_ROOT="$PWD/.agent-artifacts/final-qa-$RAMAN_FINAL_SHA"
mkdir -p "$QA_ROOT"/{backup,plugin,logs,captures,masters}
git show --no-patch --format='%H %s' HEAD > "$QA_ROOT/logs/head.txt"
{ uname -a; quickshell --version; findmnt -T "$PWD"; } > "$QA_ROOT/logs/platform.txt" 2>&1
hyprctl monitors -j > "$QA_ROOT/logs/monitors.json"
```

For tests requiring isolation, temporarily stage the candidate using
[S1's temporary live widget section](glhf-s1.md#temporary-live-widget-and-restoration),
using this `QA_ROOT`; preserve the intended normal installation at completion.
Its probe wrapper isolates `XDG_STATE_HOME` and
`XDG_CACHE_HOME`. Verify the running service uses the staged wrapper before any
scan. Do not clear production History or scan personal Home/Root.

Baseline at the final candidate, serially, preserving exit codes (supply
`RAMAN_QML_SHELL`/`RAMAN_QML_RUNNER` as needed):

```bash
for t in level design runtime panel_nav history_model incidents storage_model apps_model; do
  node "tests/test_$t.js" > "$QA_ROOT/logs/node-$t.txt" 2>&1 || echo "FAIL $t"
done
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v > "$QA_ROOT/logs/python.txt" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage.py \
  --output ".agent-artifacts/final-qa-$RAMAN_FINAL_SHA/default" > "$QA_ROOT/logs/default.json" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage.py --fanout 60000 \
  --output ".agent-artifacts/final-qa-$RAMAN_FINAL_SHA/wide" > "$QA_ROOT/logs/wide.json" 2>&1
RAMAN_QML_ARTIFACTS="$QA_ROOT" QT_QPA_PLATFORM=offscreen scripts/check-qml.sh > "$QA_ROOT/logs/qt-1x.txt" 2>&1
RAMAN_QML_ARTIFACTS="$QA_ROOT" QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 scripts/check-qml.sh > "$QA_ROOT/logs/qt-2x.txt" 2>&1
```

The live Python tests expect their children to have individual PID groups.
When running from a graphical app or terminal scope, launch the suite in a
test-owned user service so it does not inherit that app's grouping:

```bash
mkdir -p "$QA_ROOT"/{test-state,test-cache,tmp}
systemd-run --user --pipe --wait --collect --unit=ramanqa-python \
  --working-directory="$PWD" \
  --setenv=PYTHONDONTWRITEBYTECODE=1 \
  --setenv=XDG_STATE_HOME="$QA_ROOT/test-state" \
  --setenv=XDG_CACHE_HOME="$QA_ROOT/test-cache" \
  --setenv=TMPDIR="$QA_ROOT/tmp" \
  python3 -m unittest discover -s tests -v > "$QA_ROOT/logs/python-service.txt" 2>&1
```

Keep the original logs if an initial run inherited an app scope. This changes
only the test processes' grouping, not RAMen's production grouping rules.

Run `scripts/check-restart-history.py` three times against the isolated live
state (procedure in [S1](glhf-s1.md#live-checks-and-restart-receipts)) and keep
each receipt. Checker targets are unchanged: cached queries <100 ms, cancel ACK
<250 ms, owned cleanup and EOF/parent-loss cleanup <1 s, summary spacing
<2.25 s. Preserve any miss; do not raise thresholds.

## 2. Hardware checks

Record PASS / FAIL / NOT RUN per row with the SHA, hardware/session setup, UI
labels, snapshot IDs and worker PIDs where relevant.

| Check | Required observation |
| --- | --- |
| Multi-monitor shared service | One runtime/probe/IPC target and no doubled summaries or history writes with widgets on two physical outputs. Panels on both outputs keep independent page state and Storage requests: closing or changing scope on one never cancels the other's scan/query or Memory detail. `omarchy-shell boundsj.raman toggle` and `page storage` act on the focused monitor. |
| Monitor disconnect/reconnect | With a panel open on the output being removed (and separately with a Storage scan running there), unplug it. The surviving widget keeps working; `status` shows no extra probe. Reconnect: no duplicated probes, subscriptions or Storage clients; the reconnected panel opens normally. |
| Suspend/resume | Suspend with Memory open, then History open, then Storage open, then during a Storage scan. After wake: fresh Memory data, History shows a blank gap (never interpolated) with truthful continuity, Storage capacity refreshes and the scan reaches a truthful terminal state or a retryable helper/limit state. The shell stays usable; obsolete replies never replace current state. |
| Removable drive removal | Use a disposable fixture drive holding only `scripts/storage-demo.py` output. Remove it (a) while browsing a cached snapshot and (b) during a scan. Storage reports missing/changed, drops old rows and Open/Copy/Descend, and never reuses the snapshot by pathname. |
| Remount and changed identity | Reconnect the same drive: a new explicit scan succeeds. Reformat or substitute a different filesystem at the same mount point: Storage reports changed/remounted, the old cache is unavailable and actions stay disabled until an explicit rescan. |

## 3. Visual matrix

Check each page (Memory, History, Storage, Apps) and page-button navigation:

- Alternate Omarchy themes, including at least one light and one dark theme.
  Storage bands are tints of the theme accent and must stay distinct from the
  warning/critical colors; band labels must stay readable on every tint.
- Narrow/short displays and every available display scale (1×, 160% and others
  present). Labels, focus outlines, ledger scrolling, breadcrumbs, filter field
  and buttons stay legible and reachable; nothing exceeds the screen.
- Keyboard-only navigation across Memory/History/Storage/Apps, including Tab /
  Shift+Tab host behaviour and the Storage and Apps editors owning their keys.
- Apps: lens chips, search field, legend, notes, two-line rows with aligned
  RAM/CPU rails and end ticks, details scrolling and the VRAM meter stay
  legible at every scale and theme; the CPU rail stays the theme accent and
  never takes a warning/critical colour.
- `storageShowMap` off and `storageRememberScope` off/on; `animations` off.
- Wording check: cancelling a first scan with no cached snapshot currently
  reads "cancelled · previous selected snapshot retained · no cached snapshot".
  Confirm that a user understands it; record a finding if it misleads.

## 4. Storage gallery capture and publication

The [verification summary](final-qa-evidence.md) and [native gallery](../screenshots/final-qa/README.md)
include a representative Storage ready state. The extended scene matrix below
has not been executed. Capture only the actual installed RAMen widget showing original
synthetic fixtures; never competitor imagery, composites, retouching,
reconstructed UI or AI-generated images. Follow the H3 conventions in
[glhf-h3.md](glhf-h3.md#capture-plan-and-provenance): lossless native masters
first, derivatives second, provenance for both.

### Non-private fixture location

The Storage header shows the full scope path, so fixtures must not live under a
personal home path. `scripts/storage-demo.py` only writes beneath its own
checkout's `.agent-artifacts/`, so generate from a separate worktree at a
neutral, disk-backed path (tmpfs is an excluded filesystem type):

```bash
git worktree add --detach /var/tmp/raman-gallery "$RAMAN_FINAL_SHA"
cd /var/tmp/raman-gallery
findmnt -T . -o FSTYPE   # must be a supported local filesystem, not tmpfs
python3 scripts/storage-demo.py --fanout 60000 --output .agent-artifacts/gallery \
  > "$QA_ROOT/logs/gallery-fixtures.json"
python3 scripts/storage-demo.py --fanout 110000 --output .agent-artifacts/gallery-limit
python3 scripts/storage-demo.py --fanout 110000 --output .agent-artifacts/gallery-limit
```

Enter the printed absolute scopes in Storage's path field. Do not press Home or
choose a personal volume while capturing. The capacity strip shows the real test
filesystem; record that in provenance. Remove the worktree afterwards with
`git worktree remove /var/tmp/raman-gallery` once captures are verified.

### Required scenes

Before this handoff, five scene recipes were reproduced through the real probe
protocol on a Linux container filesystem: ready, partial, cancelled, entry
limit and Empty Serving Board (see
[S-DOC evidence](#s-doc-pre-handoff-protocol-check)). The Other/Tiny
(`storage-other.png`) and missing (`storage-missing.png`) recipes were not
exercised in S-DOC. Every live widget capture below is still pending final QA.

| Derivative | Scene and how to reach it |
| --- | --- |
| `storage-ready.png` | **Serving Board**, explicit Scan, map on, a directory row selected so the band outline and ledger highlight match. Dark theme. |
| `storage-ready-light.png` | Same scene in a light theme. |
| `storage-other.png` | Serving Board → **Spice jars**, open Other or Tiny entries to show real members with paging. |
| `storage-partial.png` | Serving Board after `mkdir "Locked pantry" && chmod 000 "Locked pantry"` inside it, then Rescan: partial/lower-bound wording. Restore with `chmod 700` and `rmdir` afterwards. |
| `storage-cancelled.png` | **Cancellation Kitchen**, Scan, then Cancel while counts are rising: cancelled outcome with retained (or no) prior snapshot. |
| `storage-limit.png` | Scope = the `gallery-limit` output directory itself (two generator runs, about 220,000 entries): "Storage limit reached — choose a narrower folder". |
| `storage-missing.png` | Scan a generated scope, then rename its directory: after the next capacity refresh, Storage reports missing/invalid and drops rows/actions. Rename it back afterwards. |

Optional, if captured cleanly: **Empty Serving Board** (directory overhead
only), scanning in progress (real timing only, never staged), map off, and
helper failure (Reload cache shown).

### Masters, provenance and README

- Masters: `docs/media/masters/raman-storage-<scene>-<theme>-<scale>pct.png`,
  byte-for-byte from `grim`. Locate the panel with RAMen's `geometry` IPC and
  Hyprland output metadata, or by diffing closed/open frames as
  `scripts/screenshots.sh` does; confirm no private window or path is in frame.
- Provenance: new `docs/media/masters/storage-provenance.json` using the H3
  [provenance.json](../media/masters/provenance.json) fields (reviewed head,
  platform versions, physical/virtual output, native/logical size, scale,
  theme/font, settings, capture time/command, master SHA-256, derivative recipe
  and SHA-256). Add the fixture provenance (`synthetic: true`, generator
  fanout) and note that allocation and capacity are real measurements of the
  test filesystem. Omit usernames and private paths.
- Derivatives in `docs/screenshots/` as listed above, from a recorded
  `magick` recipe.
- README: add a Storage gallery table in the Storage section. Alt text names
  what each state actually shows, including "synthetic fixture".
- Update `docs/art/README.md` and [Storage design](../design/storage.md#verification-record-and-remaining-coverage)
  to link the published masters; keep these hardware/visual results in a new
  `docs/qa/final-qa-evidence.md`.

Inspect every PNG at native resolution for legibility, focus and private
content before committing. Restore `demo off`, settings, theme, scale,
workspace and the original installation with the prepared trap.

## 5. Apps additions

Apps phases A1–A3 and A-DOC appended their final hardware, visual, GPU and
gallery items here (A1, A2, A3 and A-DOC below). Every row is NOT RUN until
executed at the final candidate.

### A1 — CPU accounting and inventory backend (RAMEN-7)

A1 adds no page or control. A2 is merged: drive `apps.query` directly
against the staged probe (below) or through the installed Apps page.
A1's automated evidence (fixtures, a Linux 6.8 container and the Qt offscreen
harness) is in [Apps design](../design/apps.md#verification-record-a1); none of
it is native Omarchy evidence. Use only test-owned processes; signal nothing
else.

Direct probe session with isolated state (from the candidate checkout):

```bash
mkdir -p "$QA_ROOT/a1-state"
{ echo "detail 1"; sleep 6
  echo '{"command":"apps.query","requestId":"a:qa:1","generation":1,"sort":"cpu","limit":10}'; sleep 1
} | XDG_STATE_HOME="$QA_ROOT/a1-state" python3 raman_probe.py > "$QA_ROOT/logs/a1-probe.jsonl"
```

Add `"query":"<name or PID>"` to search. The probe exits when the input ends.

| Check | Required observation |
| --- | --- |
| Native scan cost | Memory panel open 60 s at the candidate and at pre-A1 `78f6a13`, same workload: record probe worker CPU time, mean `apps` cadence and stdout bytes. Report the observed delta against the original ~0.2 s PSS scan claim. Container timings were within noise, but the non-dumpable `status` reads for root-owned `/proc` entries (about 19 µs each in the container) are only exercised natively, as a non-root user. |
| Closed panel | Panel closed 60 s: zero `apps` messages, no CPU baselines (the next open reports `cpu.reset: "start"`). `apps.query` replies `status: "inactive"`. |
| Low-memory CPU hog | `sh -c 'while :; do :; done' &` (test-owned): it is absent from the Memory page's top-12 list, yet `apps.query` with its PID or name finds it and `sort: "cpu"` ranks it first with `cpuCorePercent` near 100 and `cpuMachinePercent` ≈ 100 / online CPUs. Stop it with `kill %1` from that shell, not RAMen. |
| Real app groups | With a browser, an Electron app and a terminal running `claude`/a build: CPU follows scope/session groups, a build's short-lived children make `cpuStatus: "partial"` rather than spikes, and memory rows match the Memory page. |
| Non-dumpable member | In one terminal session start a dumpable 1 GiB holder so the session ranks in the Memory top 12, `python3 -c 'import time; b = b"x" * (1 << 30); time.sleep(600)' &`, then a non-dumpable sibling, `python3 -c 'import ctypes, time; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0); time.sleep(600)'`. The session row says "memory partial" with a `≥` size; `apps.query` shows `memoryStatus: "partial"` with the sibling still counted in `count` and CPU. Capture the row at native scale in a light and a dark theme, then stop both from that shell (Ctrl+C, `kill %1`). |
| Suspend/resume | Memory open across a suspend: the first scan after wake reports `cpu.reset` `suspend` or `gap` and null rates, never a spike; values return one scan later. |
| Shared scan | Memory open on two outputs (with the multi-monitor rows above): one `apps` message per 2.5 s, not two. |
| CPU topology (optional) | Only if the operator authorizes a root `cpuN/online` toggle: the next scan reports `topology` and warms up. Otherwise record NOT RUN; fixture coverage only. |

### A2 — Apps page (RAMEN-10)

A2's automated evidence (Node, Linux container Python with test-owned signals,
Qt 6.8.2 offscreen renders at 1×/2×) is in
[Apps design](../design/apps.md#verification-record-a2). None of it is native
Omarchy evidence, and the offscreen PNGs are not gallery material. Run these
on the installed candidate with isolated state (section 1), then restore.
Signal only processes you start for the test. Test-owned app groups:

```bash
# A stable app scope (one process) and a TERM-ignoring one, both test-owned.
systemd-run --user --scope --unit=app-ramanqa-target -- sleep 600 &
systemd-run --user --scope --unit=app-ramanqa-stubborn -- \
  python3 -c 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)' &
# A scope whose membership changes every second (short-lived children).
systemd-run --user --scope --unit=app-ramanqa-churn -- sh -c 'while :; do sleep 1; done' &
# A scope that forks one long-lived child into itself on SIGUSR1.
systemd-run --user --scope --unit=app-ramanqa-join -- python3 -c '
import os, signal, time
def fork(*_):
    if os.fork() == 0:
        time.sleep(600)
        os._exit(0)
signal.signal(signal.SIGUSR1, fork)
while True:
    signal.pause()' &
```

If a unit does not appear as its own Apps row (Apps search `ramanqa`/PID),
record the cgroup path and use a dedicated throwaway terminal window instead.
Clean up with `systemctl --user stop app-ramanqa-*.scope`.

| Check | Required observation |
| --- | --- |
| Page and shared scan | `page apps` opens Apps on the focused monitor; Memory and Apps open together (two outputs) still produce one `apps` message per 2.5 s; closing both stops scans (`status`: `detail: false`). |
| Rails and units | At native scale and 1.6×, 420 px and the narrowest real output: name, footprint and `CPU n%` never overlap; RAM/CPU rails align under their numbers with end ticks; the legend names "resident PSS of <RAM>" and "% of N logical CPUs"; a `sh -c 'while :; do :; done'` hog shows about 100/N % and is found by its PID. Light and dark themes. |
| Truthful states | Right after opening: "CPU warming up" note and hollow CPU rails, then values; `demo apps` shows `≥`, `–`, protected lock, "CPU partial/warming up", demo note; GPU memory lens behaviour is checked under A3 below; `appsDefaultSort` `cpu` opens on CPU. |
| Keyboard | `/` focuses search and every letter (`j k x f d r h l`) is text; Enter applies and returns to the list; Esc order disarm → leave search → close details → close panel; `k` from the top reaches the sort row then page buttons; `h`/`l` lens; `]`/`[` pages; Tab/Shift+Tab still switch bar panels from every Apps region and from the search field's neighbours. |
| Two-step kill | `app-ramanqa-target`: `x` shows `Kill?`, waiting 4 s disarms, `x x` ends it (SIGTERM). `app-ramanqa-stubborn`: `x x`, row says "Still running — kill again to force" after 4 s, `x x` then sends SIGKILL. Right-click arms `Force?`. Protected rows (Hyprland, Quickshell…) never arm. |
| Membership change | Arm `app-ramanqa-churn` and wait for one scan: the row disarms with "changed … disarmed" and nothing is signalled (`systemctl --user status` still running). A direct stale `apps.kill` through the staged probe (section 1 recipe) returns `stale-membership`. |
| Confirmation-time membership | Through the staged probe (section 1 recipe) with `detail 1`: start the `app-ramanqa-join` scope below, read its row's `generation` with `apps.query` (`focus`), then `kill -USR1` its PID (it forks a child into the scope) and send `apps.kill` with that generation at once, before the next scan: the reply is `stale-membership`, nothing is signalled (both processes still run). After the next scan, `apps.kill` with the new generation ends both. Record the `apps.kill` request-to-`killed` latency and the desktop's process count. |
| Pointer stability | With the CPU hog and churn reordering rows, hover the list: rows stop moving; click the × kill button then `Kill?` on a row while another update arrives: the clicked app is the one armed and signalled; moving the pointer away applies the newest order and keeps the selected app selected. |
| Paging and search | With more than 50 groups, `]` shows 51–…, and no row repeats or disappears between pages of one snapshot (with fewer groups record NOT RUN; fixtures cover paging); searching or changing lens disarms an armed row. |
| Details | `d`/ⓘ/double-click on a test-owned scope shows its processes and command lines; `x`/`f`/Enter do nothing there; `r` re-reads; after closing, `grep -r ramanqa "$XDG_STATE_HOME/raman"` finds nothing. |
| Demo safety | `demo apps`: killing `spin-the-noodle.sh` removes only that fake row; nothing on the desktop changes; `demo off` restores live rows. |
| History/Storage routing | On History and Storage, `x`, `f`, Enter, Space, `d`, `/` never signal or switch to Apps actions. |
| Native cost | Apps open 60 s at the candidate versus A1 merge `463f5e3`, same workload: worker CPU time, mean `apps` cadence, stdout bytes, and query latency while typing a search. |

### A3 — GPU attribution (RAMEN-13)

A3's adapter reads the kernel's DRM client usage stats (`drm-*` keys in
`/proc/PID/fdinfo`); see [Apps design](../design/apps.md#a3-gpu-attribution)
and the [protocol](../protocol.md#apps-gpu-attribution-a3). Its automated
evidence (fixture sysfs/procfs trees with amdgpu, i915, xe and
no-usage-stats drivers, the real `/proc` walk over test-owned children with no
DRM hardware, the live probe, Node and Qt offscreen renders) is in the
[verification record](../design/apps.md#verification-record-a3). **No real
GPU was available to the implementation**: every row below is required
evidence for closing A3 and Apps, not optional polish. Never install a GPU
tool for this; use only what is already present, and record what was used.
Use only test-owned GPU clients and processes.

Inventory each GPU first and paste the output into the report:

```bash
uname -r
for n in /sys/class/drm/card[0-9] /sys/class/drm/renderD*; do
  d=$(readlink -f "$n/device")
  printf '%s dev=%s device=%s driver=%s runtime=%s vram_total=%s\n' "$n" "$(cat "$n/dev")" "$d" \
    "$(basename "$(readlink -f "$d/driver")")" "$(cat "$d/power/runtime_status" 2>/dev/null)" \
    "$(cat "$d/mem_info_vram_total" 2>/dev/null)"
done
```

Raw-source comparison for one test-owned client (a `vkcube`, `glxgears`,
`mpv --hwdec=auto` or browser window already installed; record which):
`pid=<client pid>; for f in /proc/$pid/fd/*; do case $(readlink "$f") in /dev/dri/*) echo "== $f"; grep '^drm-' "/proc/$pid/fdinfo/${f##*/}";; esac; done`.

Source, driver, units and coverage checklist, per GPU:

| Check | Required observation |
| --- | --- |
| Identity | RAMen's `gpu.devices[].id`, `driver`, `pdev`, `pciId` and `nodes` (Apps → staged probe `apps.query`, or the `status` IPC plus a query) match the inventory above; `card*` and `renderD*` of one GPU are one device. |
| Client identity | The raw `drm-client-id`/`drm-pdev` keys exist for each client. Two descriptors of one client (if the client holds both `card` and `renderD` or forked) count once (`clients`); record the raw IDs. |
| Memory units and regions | Raw `drm-resident-<region>`/`drm-total-<region>` values (bytes, `KiB` or `MiB`) equal RAMen's `vramKb`/`vramAllocatedKb`/`systemKb` for that app ± one sample. Record each region name and whether RAMen classed it device-local (`vram*`, `local*`), system RAM (`memory`, `system*`, `gtt`, `cpu`) or other. On amdgpu, `drm-memory-vram` equals `drm-resident-vram` and RAMen's value is that number once, not twice. |
| Resident vs allocated | Under memory pressure or an evicting workload (if one is available), resident and allocated diverge as the raw keys do; RAMen's lens follows resident. |
| Device totals | amdgpu: `deviceVramUsedKb`/`deviceVramTotalKb` match `mem_info_vram_used`/`mem_info_vram_total` ÷ 1024 within one sample; `unattributedVramKb` is used minus the visible clients and marked `≥` when shared buffers exist or `vramOverlap` is `possible` (on a pre-6.9 amdgpu kernel without `drm-shared-*` keys, two or more resident clients make it `≥`; record `uname -r` and the raw keys). Other drivers: totals are `null` and nothing is invented. |
| Engines | With the test-owned client rendering, engine readings move for the right engine class (`gfx`/`render`/`rcs`…), stay ≤ 100 % per class, idle reads `0`, a new client reads "warming up" first, and no combined "GPU %" appears anywhere. Compare with an already installed `amdgpu_top`/`intel_gpu_top`/`nvtop` if present (record versions), else record the raw ns/cycles deltas over a timed interval. Record each driver's basis (`busy-time`, `gpu-cycles`) and capacities. |
| Shared buffers across clients | Find an app with two or more DRM clients that report `drm-shared-vram` (or `-vram0`/`-local0`) above zero (a browser's GPU process plus a second client, a Vulkan/GL app with a separate presentation client; record how found and the raw keys). RAMen shows its VRAM as `≤…` with "client sum, shared buffers may repeat", `vramOverlap: possible`, no VRAM meter, and the lens note counts it; its sum may exceed what the device reports (`deviceVramUsedKb` on amdgpu). An app with one client, or clients without shared buffers, shows an exact value and keeps its meter. If such an app also has an unreadable or newer member (or descriptors past the read limit), it reads `~…`, and stays `~…` (not `≤…`) once the reading turns stale (for example after a >10 s sample). If none occurs, record NOT OBSERVED; fixtures cover it. |
| Cross-group sharing | If any client is reachable from two app groups on this desktop (record how found, for example a launcher passing its descriptor), it appears once under `crossGroupClients` and in neither row. If none occurs naturally, record NOT OBSERVED; fixtures cover it. |
| Asleep / no wake | On a laptop or desktop whose discrete GPU runtime-suspends: with it `suspended`, open Apps for 60 s. `runtime_status` stays `suspended`, its `power/runtime_active_time` does not advance, RAMen shows it asleep (rows "GPU asleep", note "RAMen does not wake it"). Start a client on it: it wakes, readings appear within ~5 s. If no such GPU exists, record NOT RUN with the machine's runtime-PM state. |
| Unsupported | On a driver without per-client usage stats (for example NVIDIA's proprietary `nvidia-drm`) or an integrated-only machine: the GPU memory lens stays unselectable with its reason, `appsDefaultSort: gpu-memory` shows the fallback note, integrated GPU buffers appear in details as "GPU buffers in system RAM … (part of RAM, not added)", and no VRAM number is shown. Record which. |
| Permission | A non-dumpable test-owned process holding a GPU descriptor (`prctl(PR_SET_DUMPABLE, 0)` after opening `/dev/dri/renderD*`) makes its group "GPU unreadable" / partial, never zero. |
| Settings | `gpuMetrics: off` (settings UI): `status` IPC shows `gpu: false` with Apps open, the lens is dimmed with the reason "off" (no strike-through), no GPU sampling; back to `auto` resumes within ~5 s. The bar label and colours never change with GPU state. |
| Memory-only and closed | Memory page open 60 s, then panel closed 60 s: no GPU sampling (`apps` messages report `gpu.status: "inactive"`; if `strace` is already installed, `strace -f -e trace=openat -p <worker pid>` shows no `fdinfo` opens). |
| Freshness and cadence | Under a normal desktop, `gpu.ageSeconds` stays below about 7.5 s and `collectSeconds` is milliseconds; record both. Toggle the panel closed/open (and `gpuMetrics` off/on) several times within 5 s with Apps visible: successive GPU samples (`gpu.sampledAt` changes, or `fdinfo` opens if `strace` is already installed) stay at least 5 s apart. If a real stalled `fdinfo` read is ever observed (xe under heavy buffer contention), the page shows "took longer than 10 s … labelled stale", never fresh values; otherwise record NOT OBSERVED (fixtures cover it). |
| Suspend/resume | Apps open across a system suspend: the first sample after wake reports `reset` `gap` or `suspend`, engines warm up, no spikes. |
| Native cost | Apps open 60 s with `gpuMetrics` `auto` versus `off`, same workload, at the candidate: worker CPU time, the GPU sample's `counts` (processes, fd entries, DRM fds, fdinfo reads), the 2 s summary and 2.5 s `apps` cadence (unchanged), and Apps query latency while typing. Report against the container figures in the verification record. |
| Visual | GPU memory lens and a details view with the VRAM meter at native scale and 1.6×, 340/420 px and the narrowest output, light and dark: `VRAM` column, `≥` partial and `≤` overlapping values (the `demo apps` Steam Table row: no meter in its details), "GPU asleep"/"GPU unreadable"/"no GPU clients" subtitles, the device notes. `demo apps` with Apps open shows the synthetic GPU rows labelled "GPU readings are synthetic demo data". |

### A-DOC — Apps gallery capture and publication

The [native final-QA gallery](../screenshots/final-qa/README.md) now includes
a representative Apps CPU scene and current Memory/History/Storage panels.
The remaining scene matrix below is not claimed as passing coverage.
H3 images remain historical; retained Qt previews are labelled offscreen.
Capture only the actual installed RAMen widget; never use
competitor imagery, composites, retouching, reconstructed UI or AI-generated
images. Follow the H3 conventions in
[glhf-h3.md](glhf-h3.md#capture-plan-and-provenance) and section 4: lossless
native masters first, derivatives second, provenance for both.

**Privacy.** The live Apps page lists every app and terminal session you
run, with names taken from their commands. Every required scene below
therefore uses the synthetic `demo apps` (or `demo red`) scene, whose rows
and command lines are fake and which never reads a GPU. Do not publish a live
Apps list.

**What has been exercised.** A-DOC drove the underlying probe states of the
`apps-memory`, `apps-cpu`, `apps-gpu`, `apps-gpu-states`,
`apps-details-vram`, `apps-details-shared` and `apps-unsupported` scenes
through the real probe protocol in a Linux container, and rendered their
labels through `AppsModel.js` under Node (see the
[A-DOC check](#a-doc-pre-handoff-protocol-check)). That is not widget
rendering. The keyboard sequences, the capture function below, the armed,
narrow, light-theme and `gpuMetrics: off` scenes, and every capture were
**not** executed; they are planned for this pass.

Setup, after section 1's isolated install, on the same empty-workspace
convention as `scripts/screenshots.sh` (pointer parked outside the panel, so
rows never freeze under it):

```bash
ipc() { omarchy-shell boundsj.raman "$@"; }
SHOTS="$QA_ROOT/captures/apps"; mkdir -p "$SHOTS"
read -r _ _ _ BAR_H < <(ipc geometry | jq -r '"\(.x) \(.y) \(.w) \(.h)"')
SCALE=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .scale')
TOP=$(awk -v h="$BAR_H" -v s="$SCALE" 'BEGIN { printf "%d", (h + 2) * s }')
ipc close; sleep 0.5; grim "$SHOTS/closed.png"   # panel closed, same output

# shoot <master-name>: full frame, then the panel's bounding box against the
# closed frame, exactly as scripts/screenshots.sh crops (physical pixels).
shoot() {
  grim "$SHOTS/$1.full.png"
  local bbox
  bbox=$(magick "$SHOTS/$1.full.png" "$SHOTS/closed.png" -compose difference -composite \
    -crop "+0+$TOP" +repage -colorspace gray -threshold 4% -format '%@' info:)
  [[ $bbox =~ ^([0-9]+)x([0-9]+)\+([0-9]+)\+([0-9]+)$ ]] || { echo "no panel for $1 ($bbox)"; return 1; }
  magick "$SHOTS/$1.full.png" -crop "${BASH_REMATCH[1]}x${BASH_REMATCH[2]}+${BASH_REMATCH[3]}+$((BASH_REMATCH[4] + TOP))" \
    +repage "$SHOTS/$1.png"
  echo "$1 ${BASH_REMATCH[1]}x${BASH_REMATCH[2]}+${BASH_REMATCH[3]}+$((BASH_REMATCH[4] + TOP))" >> "$SHOTS/geometry.txt"
}

ipc demo apps
ipc page apps; sleep 3          # Apps on the focused monitor; focus on the page buttons
wtype j; sleep 0.3; wtype j; sleep 1                 # sort row, then the list (Memory lens)
shoot raman-apps-memory-dark-160pct
```

Lens keys from a fresh `ipc page apps`: `j` reaches the sort row, `l` moves
to CPU and again to GPU memory, `j` enters the list. In the list, `j` moves
down, `d` opens details (Esc closes), `x` arms a kill (Esc disarms). Inspect
each frame before keeping it: the intended lens is bold, the demo note is
visible and nothing private is in frame. Use the actual scale in each name.

| Derivative (`docs/screenshots/`) | Scene and how to reach it | Status |
| --- | --- | --- |
| `apps-memory.png` | `demo apps`, Memory lens, dark theme: Chrome (`VRAM ≥412M` note), Steam Table (`VRAM ≤1.4G client sum…`), Broth; legend naming resident PSS of the scene's 15G and 8 logical CPUs; RAM rails under sizes, CPU rails under CPU shares; "Demo data" note. | Required |
| `apps-memory-light.png` | Same in an installed light Omarchy theme (record its name; restore the original theme afterwards). | Required |
| `apps-cpu.png` | CPU lens: Broth `CPU ≥22%` with "CPU partial", the 896 KiB `spin-the-noodle.sh` second at 12 %, CPU rails in the accent colour. Shows the low-memory CPU hog the Memory page leaves out. | Required |
| `apps-gpu.png` | GPU memory lens: `VRAM 6.2G`, `≤1.4G`, `≥412M`, `≥24M`, `0K`, with the header notes "GPU readings are synthetic demo data", the overlap note, the cross-group note and "GPU pci:0000:0a:00.0 is asleep; RAMen does not wake it." | Required |
| `apps-gpu-states.png` | Same lens, scrolled (`j` through the list) to the unknown rows: "GPU unreadable", "GPU buffers in RAM 180M", "GPU asleep", "CPU warming up", "GPU shared with other apps · protected", sorted after the readings. | Required |
| `apps-details-vram.png` | GPU lens, Broth selected, `d`: resident 6.2G (allocated 6.5G), GPU buffers in system RAM "part of RAM, not added", engines "compute 88% · gfx 0.4%" each of its own class, the `VRAM 6.2G of 16G` meter and the device line. | Required |
| `apps-details-shared.png` | Steam Table details: "summed over 2 clients", the shared-buffers explanation, `dma warming up`, and no VRAM meter. | Required |
| `apps-armed.png` | CPU lens, `spin-the-noodle.sh` selected (`j`), `x` once: `Kill?` shown; capture within 4 s, then Esc. Do not confirm for the gallery (a confirmed demo kill only removes the fake row). | Required |
| `apps-narrow.png` | Memory lens on the narrowest real output or scale already available: two-line rows, elided names, aligned rails, nothing clipped. Changing output configuration needs the operator's approval and a restore; otherwise record NOT RUN. | Required if available |
| `apps-unsupported.png` | `demo red`, `page apps`, sort row: GPU memory chip unavailable with "No DRM GPU device was found.", CPU "no readings in this demo scene", demo note. Provenance must say this is the scene without GPU data, not a hardware result. | Optional |
| `apps-gpu-off.png` | `gpuMetrics: off` in the settings UI (restore `auto`), `demo apps`: GPU memory chip shows "GPU metrics are off in settings." and no GPU notes on rows. | Optional |

Not capturable from a demo, by design: stale GPU readings and the page-wide
"CPU warming up" header are time-dependent live states the scene does not
fake (row-level warm-up is in `apps-gpu-states`). Do not stage them. They are
checked live under A2/A3 above (not published), and covered by Python
fixtures and the Node model tests.

**Memory panel refresh.** Rerun `scripts/screenshots.sh` at the final
candidate (it rewrites `docs/screenshots/{bar,panel}-{green,yellow,red}.png`
from the built-in scenes and now shows four page buttons), or capture native
masters as H3 did. Record the run in provenance; H3 entries stay as historical
records of their revision.

**Masters, provenance and README.**

- Masters: `docs/media/masters/raman-apps-<scene>-<theme>-<scale>pct.png`,
  byte-for-byte from the capture (the `shoot` crop of a full `grim` frame).
- Provenance: new `docs/media/masters/apps-provenance.json` with the H3
  [provenance.json](../media/masters/provenance.json) fields (reviewed head,
  platform versions, physical/virtual output, native/logical size, scale,
  theme/font, RAMen settings including `appsDefaultSort`/`gpuMetrics`,
  capture time and command, master SHA-256, derivative recipe and SHA-256),
  plus `demo: true`, the scene (`apps` or `red`), lens, selected row and the
  keys pressed. State that every row, member, command line and GPU reading is
  synthetic and labelled as demo data on the page.
- Derivatives in `docs/screenshots/` as listed above, from a recorded
  `magick` recipe.
- README: an Apps gallery table in the Apps section, alt text naming what
  each state shows and "synthetic demo scene". Update the offscreen-preview
  wording there, in CREDITS.md and in the art README to point at the native
  captures.
- Link the published masters from `docs/art/README.md` and the
  [Apps design A-DOC record](../design/apps.md#a-doc-documentation-and-gallery-handoff);
  keep the results in `docs/qa/final-qa-evidence.md`.

Inspect every PNG at native resolution for legibility, focus and private
content before committing. Restore `demo off`, settings, theme, scale,
workspace and the original installation with the prepared trap.

## 6. Neon design native visual checks

At implementation handoff, the neon design (bowl gauge, vector icons, chips,
contrast-safe semantic colours, Storage and Apps layout changes, new
artwork) had only been rendered offscreen: Qt 6.8.2 in a Debian container
with Omarchy v4.0.4's UI modules and test doubles, at 1x and 2x, dark and
light stub themes, 340/420 px and a base-18 font
([previews](../previews/README.md)). The [execution record](final-qa-evidence.md)
now records the actual native observations and four representative captures,
plus the remaining coverage limits. The original matrix below stays
explicit; unexecuted checks are NOT RUN, not passes.

| Check | Required observation |
| --- | --- |
| Bar bowl at real scale | At 1x and the native scale (160% on the H3 machine) on top and side bars: the bowl reads as a bowl with chopsticks next to the tray icons, is no taller than neighbouring icons, sits on the label's centre line, and its broth level visibly differs between `demo green`, `demo yellow` and `demo red`. One wisp of steam at yellow, two at red, none at green. Hover, click and middle-click still work. |
| Bar on light and transparent bars | In an installed light theme the label and broth stay readable (amber, not pale yellow); with a transparent bar the theme colours are used unchanged. Record the theme names. |
| Font fallback | With the user's normal font (JetBrainsMono Nerd Font) and, if easily switched back, a non-Nerd monospace: kill, force kill, lock, info, search and back/next icons look identical, because none is a font glyph. |
| Memory page | Bowl hero matches the bar's level and colour; threshold labels sit under their ticks (also with custom close thresholds such as 88/90 and 99/100, which stay inside the meter); "PSS + SWAP" sits over the size column; × arms `Kill?`, right-click arms `Force?`, protected rows show the lock; `Kill?` and the % badge stay readable at rest and under the pointer on a light theme; key hints wrap rather than cut. |
| History | Accent rim under the title, accent cursor and value markers, readable dashed threshold labels on a light theme, steam markers in rows and receipts; the receipt's Back button works by pointer and Esc. |
| Storage | Sections read top to bottom (scope, filesystem, allocation); capacity strip labels match their segments; bands shade by depth with seams and no clipped labels; ledger column headers align with numbers; directories end in `/`, hidden entries show `hidden`; every button still works by pointer and the documented keys. |
| Apps | Measured columns keep names as long as the row allows at 340/420 and the native scale; the unavailable GPU lens is dimmed with its reason (no strike-through); details show Footprint/RAM/CPU/GPU/Members facts with meters; the search icon does not overlap typed text. Scrolling the Apps and Memory lists (and a History page taller than the screen) leaves no icon or chart line outside the list or page. |
| Focus and selection | Selected page/window/lens: fill, bold and the accent line; keyboard focus adds a 2 px outline; the two never get confused on any theme. |
| Motion | `animations: off` (and the shell's reduced motion, if available) stops the bowl's level/colour transitions; nothing else in the design animates. |
| Gallery publication | Capture native masters of the current design for Memory, History, Storage and Apps (sections 4 and A-DOC), with provenance; then replace or join the offscreen previews in the README, keeping their "offscreen" captions accurate. |
| Remote GitHub branding | Upload `docs/art/social-preview.png` in the repository's Settings → General → Social preview (not settable by a commit); check the README banner on GitHub's light and dark page themes. |

## S-DOC pre-handoff protocol check

On 2026-10-06 the S-DOC implementation agent ran the documented fixture scenes
through the real `raman_probe.py` JSON protocol as an unprivileged user in the
`raman-s2-fixes:local` Debian 13 container (kernel 6.8.0-100, container
filesystem), at merge `9ca8b4de2550142297e3cf4a4dcfd13f171887c5`:

| Scene | Probe outcome |
| --- | --- |
| Serving Board (fanout 60000 run) | `storage-result` ready, coverage complete |
| Serving Board + owned `chmod 000` directory | ready, coverage partial |
| Cancellation Kitchen, cancel after first progress | `cancelled`, `previousSnapshotRetained: true` (no prior snapshot existed) |
| Two `--fanout 110000` runs scanned together | error `scan-entry-limit` |
| Empty Serving Board | ready |

This confirms the scene recipes only. It is not live-widget, native filesystem,
physical hardware or visual evidence.

## A-DOC pre-handoff protocol check

On 2026-10-06 the A-DOC implementation agent drove the Apps gallery states
through the real `raman_probe.py` JSON protocol as uid 1000 in the
`raman-s2-fixes:local` Debian 13 container (Linux 6.8.0-100, Python 3.13.5,
isolated `XDG_STATE_HOME`; no Omarchy, no GPU, nothing signalled), on the
A-DOC branch from merge `4fb10ba7fc3f98afa6ddd436435f3da0e2a54cd1` with the
asleep device added to the `apps` scene:

| Recipe | Probe outcome |
| --- | --- |
| Live `detail 1` | First scan CPU `warming-up` (`reset` `start`), next scan `available`; GPU `inactive` |
| Live `gpu 1` (no DRM device) | GPU `unsupported`, `no-drm-devices`; `memoryLens` false |
| `demo apps`, `gpu 1`, Memory/CPU/GPU memory queries | 14 synthetic rows, GPU `demo`/`synthetic`, three devices (one `asleep`/`suspended`), every documented row GPU state present, CPU hog found by search and ranked second by CPU |
| `apps.details` for Steam Table, Gyoza, Broth | Overlap `possible`; `asleep` with no reading; three synthetic members |
| `demo apps`, `gpu 0` | Every row `inactive` |
| `demo red`, `gpu 1` | GPU `unsupported`, `demo-without-gpu`; CPU `unavailable` |

The replies were then rendered through `AppsModel.js` under Node to confirm
the labels listed in the gallery table. This confirms the scene recipes
only. It is not live-widget, native, visual, keyboard or GPU-hardware
evidence, and no capture command was run.

## Report

Keep exact commands, counts, timings, hardware/session setup, per-row results,
and failure/retest logs under `.agent-artifacts/`. Publish a concise summary
in `docs/qa/final-qa-evidence.md` with the tested SHA, verified behavior, actual
failures and coverage limits. Omit personal paths and session transcripts.
Preserve honest PASS / FAIL / NOT RUN distinctions; unexecuted checks do not
become passes when a release is accepted. Restore the intended live
installation, normal settings and production data after testing.
