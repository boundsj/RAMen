# S3 exact-head Linux and Omarchy handoff

> **Current status (2026-10-06).** S3 was accepted and merged in PR #6 as `9ca8b4d` after the correction retest. Its outstanding hardware, visual
> and Storage gallery items moved to the consolidated [final QA runbook](final-qa.md)
> at Jesse's direction. Read-only-source/unsupported-filesystem hardware QA and
> stalled/uninterruptible-I/O checks are excluded, not deferred. The text below
> is the historical procedure and evidence for its named revisions.

S3 closes functional Storage failure/race/responsiveness coverage. Final gallery,
publication and feature-wide documentation reconciliation remain S-DOC. This
runbook is for a separate authorized QA thread; it does not authorize remote
access, temporary installation, privileged mounts, hardware removal, suspend,
merge, ticket closure or permanent deployment. Parent owns review and PR; Jesse
owns acceptance and merge. Container/offscreen tests are not live Omarchy proof.

## Pin and isolate

Use an unlocked Linux 6.8+ Omarchy session. Parent supplies the independently
reviewed full SHA and open PR URL. Check both before and after QA:

```bash
export RAMAN_S3_SHA=REVIEWED_FULL_SHA
export RAMAN_S3_PR=OPEN_PR_URL
test "$(git rev-parse HEAD)" = "$RAMAN_S3_SHA"
test "$(gh pr view "$RAMAN_S3_PR" --json headRefOid --jq .headRefOid)" = "$RAMAN_S3_SHA"
test -z "$(git status --porcelain)"
export QA_ROOT="$PWD/.agent-artifacts/s3-glhf-$RAMAN_S3_SHA"
mkdir -p "$QA_ROOT"/{backup,plugin,logs,captures}
git show --no-patch --format='%H %s' HEAD > "$QA_ROOT/logs/head.txt"
uname -a > "$QA_ROOT/logs/platform.txt"
quickshell --version >> "$QA_ROOT/logs/platform.txt" 2>&1
findmnt -T "$PWD" >> "$QA_ROOT/logs/platform.txt"
hyprctl monitors -j > "$QA_ROOT/logs/monitors.json"
```

Record filesystem/kernel capabilities, Omarchy revision, physical versus virtual
outputs, scale, theme and the actual portal/clipboard helper versions. No
capability failure counts as successful filesystem coverage.

Before authorized live installation, use the **Temporary live widget and
restoration** section of [S1's runbook](glhf-s1.md#temporary-live-widget-and-restoration)
with this checkout and this `QA_ROOT`. Its archive stages the exact HEAD and its
probe wrapper isolates both `XDG_STATE_HOME` and `XDG_CACHE_HOME` beneath the task.
Retain the restoration trap; preserve the original installation type/target,
settings, workspace/theme/scale and live History/cache. Terminal-only XDG exports
do not isolate a shell restart. Verify the actual installed service/runtime uses
the staged wrapper before any scan. Do not clear production History or scan
personal Home/Root. Every UI scope below is test-owned.

## Automated and responsiveness checks

Run serially on the native filesystem, preserving failures and exit codes:

```bash
node tests/test_level.js > "$QA_ROOT/logs/node-level.txt"
node tests/test_runtime.js > "$QA_ROOT/logs/node-runtime.txt"
node tests/test_panel_nav.js > "$QA_ROOT/logs/node-nav.txt"
node tests/test_history_model.js > "$QA_ROOT/logs/node-history.txt"
node tests/test_incidents.js > "$QA_ROOT/logs/node-incidents.txt"
node tests/test_storage_model.js > "$QA_ROOT/logs/node-storage.txt"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v \
  > "$QA_ROOT/logs/python.txt" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage.py \
  --output ".agent-artifacts/s3-glhf-$RAMAN_S3_SHA/default" \
  > "$QA_ROOT/logs/default.json" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage.py --fanout 60000 \
  --output ".agent-artifacts/s3-glhf-$RAMAN_S3_SHA/wide" \
  > "$QA_ROOT/logs/wide.json" 2>&1
RAMAN_QML_ARTIFACTS="$QA_ROOT" QT_QPA_PLATFORM=offscreen \
  scripts/check-qml.sh > "$QA_ROOT/logs/qt-1x.txt" 2>&1
RAMAN_QML_ARTIFACTS="$QA_ROOT" QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR=2 \
  scripts/check-qml.sh > "$QA_ROOT/logs/qt-2x.txt" 2>&1
```

Supply `RAMAN_QML_SHELL` and `RAMAN_QML_RUNNER` as needed. All relevant suites
must pass; report the optional desktop fixture's dependency skip explicitly.
The checker must exit 0 with `status:PASS`, matching SHA/source hashes, every
cached query <100 ms, cancel ACK <250 ms, owned scan cleanup <1 s measured from
Cancel send, EOF/parent-loss cleanup <1 s, and preserved two-second summary
cadence (<2.25 s maximum spacing in this workload). Cancellation ACK is not the
terminal catalog-reconciliation response. Preserve any miss; do not raise
thresholds or attribute it to Docker/hardware without evidence.

Uninterruptible kernel I/O cannot obey a process cleanup deadline until the
kernel returns. Record that limitation separately; never force an indefinite
QML/probe wait or claim injected sleeps prove that hardware case. Container
parent-loss checks require a reaping PID 1 (`docker run --init`); a zombie is
already exited but still unreaped, and is not a passing cleanup result.

## Original real-widget fixtures

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/storage-demo.py --fanout 60000 \
  --output ".agent-artifacts/s3-glhf-$RAMAN_S3_SHA/demo" \
  > "$QA_ROOT/logs/demo.json"
omarchy-shell boundsj.raman page storage
```

Choose each printed absolute scope in Storage and explicitly Scan. **Serving
Board** supplies original uneven Noodle Workshop/Market Basket/Recipe Archive
bands, hidden/empty/sparse/hardlinked/symlink entries, Unicode/newline/shell-looking
names and 99 Spice jars plus a large member. **Empty Serving Board** tests only
real directory overhead; it is not promised to allocate zero bytes.
**Cancellation Kitchen** supplies a wide interruptible metadata workload.
`provenance.json` identifies synthetic content; allocation comes from this
filesystem, not canned totals. This generator never scans, installs, opens or
signals anything and creates a fresh artifact directory on each run. Built-in
Memory/History demos remain isolated and unchanged; restore `demo off`.

Run these through the installed bar/service and actual widget. Offscreen renders
and generated fixtures alone do not satisfy real-widget acceptance. Save
exploratory captures under `QA_ROOT/captures`, labelled synthetic. Do not publish
private path prefixes or personal windows; final original masters/gallery belong
to S-DOC. Keep fixture provenance and actual observed allocation alongside the
captures. No upstream screenshot or composition is reused.

## Failure and interaction matrix

Record PASS/FAIL/NOT RUN with the exact head, actual UI label, authoritative
snapshot ID and owned worker PIDs where applicable. Repeat relevant cases at
narrow/short and normal widths, light/dark themes and HiDPI; preserve restoration.

| Trigger | Required observation |
| --- | --- |
| First open; cached reopen; Memory/History switch | Discovering spinner, then unscanned or aged compatible cache. No recursive scan before explicit Scan. Capacity refresh only while Storage is visible. |
| Explicit scan with unknown entry total | Spinner/counts, no invented progress percent. Prior snapshot visibly older and path actions unavailable while pending. |
| Unreadable/excluded/disappearing descendants | Partial/lower-bound wording, counters/reasons and directory overhead; no inaccessible-as-zero claim. Unit injections are not native permission/mount evidence. Use an unprivileged owned fixture for real denial, restoring its permissions afterward. |
| Cancel before completion, including no previous cache | ACK remains pending, then cancelled with retained prior snapshot or explicit no cached snapshot. Outcome must survive cache reload. |
| Completion before Cancel; queued/running reconciliation | Actual cache reloaded; committed/uncertain/unknown outcome stays truthful. Never claim cancellation won merely because the old generation was discarded. |
| Rapid scopes, filter/navigation, close/page switch/reopen | Only newest matching client/generation/request/node/offset/filter is installed. Late progress/result/action replies cannot restore an obsolete view. Closing one panel leaves another panel's query/scan and Memory detail intact. |
| Remove/replace owned scope; remount at same path | Missing/changed message, old identity data/actions dropped immediately when refresh/query detects it. No silent reuse by pathname. New scan remains explicit. Include a capacity refresh during scan/cancel; transient helper timeout must not overwrite that active/terminal outcome. |
| Unsupported identity/filesystem; read-only filesystem | Explicit capability reason. Read-only capacity label is visible; read-only source data can still be inventoried if identity/permissions support it. No privileged fallback or source writes. |
| Entry/depth/time/cache ceiling; helper crash/timeout | Specific limit/helper state, narrower scope or retry guidance, prior valid selection retained where known. No fabricated successful scan. Use task-owned/injected faults; label them accurately. |
| Helper exits/restarts | Pending work retired, helper failure visible, helper startup reloads cached discovery without automatic scan; Reload cache retries manually. While stopped, try Scan/r, Reload then r, scope changes and page reopen: no phantom request or stuck scan. Startup must rediscover the current scope regardless of its earlier display phase. Old helper replies cannot become active. |
| Ledger/map/self/tiny/Other | IDs, highlight and Open/Copy agree. All 99 small members reachable and reversible across explicit/continuation pages. Full keyboard and buttons offer equivalent access with map off. |
| Keys/editor/focus | j/k, Enter/l, h/Backspace, r/c/o/y, Esc work in their regions; row-zero Up returns to page buttons. Editors own all editing keys. Tab/Shift+Tab retain host behavior. Storage never arms a kill. |
| Open/Copy | Real file manager opens selected directory or file's parent via validated FD/portal; copy checked byte-for-byte with wl-paste. No file execution, shell expansion, display-label path reconstruction or non-UTF8 clipboard-success claim. Missing/refused helpers and changed ancestors fail explicitly. |
| Installed multi-monitor service | One authoritative runtime/probe/IPC target, no doubled summaries/writes. Two actual panels do not cancel each other's owned work. Virtual/offscreen checks are labelled; physical simultaneous-panel/unplug proof remains open when unavailable. |

Use injected tests for limits, metadata/remount faults and worker failure where
native safe reproduction is unavailable. Physical removable media, privileged
bind/subvolume/read-only mounts, suspend and uninterruptible I/O need applicable
authorization/capability; leave them NOT RUN unless exercised. Hardware limits
and missing mandatory evidence require Jesse's explicit scoped acceptance,
not a silent waiver.

Keep Memory bar/hysteresis/protected/PID safety and History read-only navigation
intact; verify demo kills only remove fake rows. No real kill test may target
unowned apps. Because this milestone includes lifecycle acceptance, run the
[three S1 restart-history checks](glhf-s1.md#live-checks-and-restart-receipts)
against the isolated live state, retaining each receipt and checking no old
probe/storage workers linger. Confirm summary cadence and UI input responsiveness
through full scans, cancellation and page close in the real shell.

## Result and restoration

Recheck checkout/PR SHA; attach exact commands, counts, measurements and failures,
platform limitations and provenance. Restore original plugin/settings/theme/
scale/workspace and live data using the prepared trap. Verify byte-identical
settings and plugin target, no QA-owned workers, normal Memory/History and no
accidental source/state/cache deletion. Three restart receipts, real-widget
scenes, installed-service interaction and native timings must be recorded or
explicitly left pending. QA readiness is not accepted merge or issue closure.

Copy-paste brief for the separate thread: “At the reviewed full SHA and open PR
supplied by the parent, execute docs/qa/glhf-s3.md on authorized Linux/Omarchy.
Use only owned synthetic fixtures and isolated XDG stores. Preserve evidence
and original installation/settings/data; restore afterward. Report all S3
failure/race/keyboard/action/service cases, two serial latency workloads and
three restart receipts with exact-head results. Distinguish injected/virtual
checks and untested physical/kernel cases. No merge, permanent deployment,
ticket closure or final gallery publication.”

## Published QA follow-up

Native QA at 64684ba
reported two P2 gaps. Its passing native/backend/live results and user-deferred
hardware coverage remain tied to that old head; see the [implementation response](s3-local-evidence.md#published-native-qa-and-response).
The correction head requires independent review and a pinned native retest:

1. Scan an owned directory, rename it and create a regular file at its former
   path. Wait for selected-capacity refresh: invalid-path must clear old rows,
   selection and Open/Copy/Descend availability, retire outstanding replies and
   visibly require a valid directory. Remove only the owned replacement file,
   restore the directory, and explicitly scan to recover. Do not count a
   rejected backend action alone as correct UI invalidation.
2. Click the first actual ledger delegate, then press Up and Left: page buttons
   must take navigation and History must open. Repeat K/H and after clicking
   Folder itself/All entries or other non-editor Storage controls. Test normal
   native pointer focus, not only direct `handleKey` calls. This integration
   failure was also present on the base revision.
3. Immediately focus path/filter after a control click: arrows and r/c/o/y must
   edit, never navigate/scan/open/copy. Check Tab/Shift+Tab host behavior, map/
   ledger identity and actual validated actions after the focus correction.

Record correction SHA, commands and outcomes separately from old-head QA.
Unchanged backend results may be linked with their actual source/revision;
never relabel old-head execution as a new-head run. Keep restoration and the
existing pending hardware/acceptance boundaries intact. No merge or ticket
closure is authorized by these corrections.
