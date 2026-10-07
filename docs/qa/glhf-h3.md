# GLHF: H3 QA and real capture runbook

**Pending gate. Execute in a separate GLHF thread after independent review.**
[Implementation-host evidence](h3-local-evidence.md) records the local baseline.
No implementation-host test, container, offscreen render, or H2 QA result is
H3 live Omarchy evidence. Do not merge, permanently deploy, mark issues Done,
launch another thread, access another machine, or suspend hardware from this
runbook without the applicable user authorization. Temporary QA installation
and synthetic notification checks belong to that separate authorized thread.

## Exact revision and evidence folder

The parent supplies the OPEN PR URL and independently reviewed full SHA in the
handoff. Do not substitute today's main or automatically accept a changed PR
head. In a dedicated GLHF checkout of boundsj/RAMen:

```bash
export PR_URL='https://github.com/boundsj/RAMen/pull/REPLACE'
export REVIEWED_SHA='REPLACE_WITH_FULL_ACCEPTED_REVIEW_HEAD'
test "$(gh pr view "$PR_URL" --json headRefOid --jq .headRefOid)" = "$REVIEWED_SHA"
git fetch origin
git checkout --detach "$REVIEWED_SHA"
test "$(git rev-parse HEAD)" = "$REVIEWED_SHA"
test -z "$(git status --porcelain)"
export QA_ROOT="$PWD/.agent-artifacts/glhf-h3-$REVIEWED_SHA"
mkdir -p "$QA_ROOT"/{logs,backup,state,plugin,captures,provenance}
chmod 700 "$QA_ROOT" "$QA_ROOT/state"
git rev-parse HEAD > "$QA_ROOT/logs/head.txt"
date --iso-8601=seconds > "$QA_ROOT/logs/start.txt"
```

Record any pending-review objection before starting; stop if the reviewed head
and PR head disagree. Re-check both after QA and before publishing evidence.
Every failure/report/capture names this exact head, not just a branch.

## Environment and dependencies

```bash
uname -a > "$QA_ROOT/logs/uname.txt"
cat /etc/os-release > "$QA_ROOT/logs/os-release.txt"
pacman -Q omarchy hyprland quickshell qt6-base qt6-declarative python nodejs \
  > "$QA_ROOT/logs/packages.txt" 2>&1
hyprctl version > "$QA_ROOT/logs/hyprland-version.txt"
quickshell --version > "$QA_ROOT/logs/quickshell-version.txt" 2>&1
python3 --version > "$QA_ROOT/logs/python-version.txt"
node --version > "$QA_ROOT/logs/node-version.txt"
git -C "$OMARCHY_PATH" rev-parse HEAD > "$QA_ROOT/logs/omarchy-head.txt"
hyprctl -j monitors > "$QA_ROOT/backup/monitors.json"
hyprctl -j activeworkspace > "$QA_ROOT/backup/workspace.json"
hyprctl -j cursorpos > "$QA_ROOT/backup/pointer.json"
```

Required: Python stdlib, Node, `sleep`, `pgrep` (procps), git, gh, jq, `omarchy-shell`,
`omarchy-notification-send`, busctl, running Hyprland/Omarchy. Offscreen checks
need Qt 6.8+ `qmltestrunner` and Omarchy UI sources. Capture needs grim,
ImageMagick (`magick`), wtype, a clean desktop and the actual display. Check:

```bash
for tool in python3 node sleep pgrep git gh jq omarchy-shell omarchy-notification-send \
  busctl grim magick wtype; do command -v "$tool" || exit 1; done
omarchy-shell shell ping
```

Verify the **installed** notification helper supports discrete `--exec` argv
and its server executes that hint on click (compare the primary source linked
in [incident design](../design/incidents.md)). If unavailable, record the
notification limitation and use `openIncident` IPC; never invent a callback.
Capture theme name, colors, font, monitor scale/resolution, animation preference,
DND state and whether outputs are physical or virtual. Keep raw environment
and private settings in local artifacts; redact before posting.

## Preserve and restore before touching the installation

1. Inspect `~/.config/omarchy/plugins/boundsj.raman` with `ls -ld` and `readlink`.
   Record whether absent, symlink or directory. Back it up with `cp -a` without
   dereferencing a symlink. Record its target and resolved revision. Do not
   replace an unknown directory or uncommitted installation without preserving
   it and resolving its ownership with Jesse.
2. Copy `~/.config/omarchy/shell.json` byte-for-byte to backup; record SHA256.
   Back up `$XDG_STATE_HOME/raman` (default `~/.local/state/raman`) with private
   modes. Existing probe writes can race a snapshot: stop the shell safely
   while unlocked, wait for its original probe pair to exit, then make the
   final consistent backup. Do not erase production state.
3. Preserve workspace, pointer, scale, theme and output config. Do not switch
   away from unsaved work or capture private windows. A headless output is a
   virtual check only, never proof of physical monitor behavior.
4. Write a restoration script under `$QA_ROOT` using the recorded exact plugin
   type/target, settings backup and original workspace/scale/theme. Install a
   shell `trap` before QA changes. On exit: `demo off`, close panel, restore
   original plugin (or remove if previously absent), byte-identical shell
   settings, original theme/output/scale/workspace/pointer, remove only QA-created
   outputs, restart onto original installation and confirm live readings. Verify
   settings hash and plugin target match backups. Record production history's
   original clearToken and bucket continuity; never restore an older history
   over legitimately newer production data without Jesse's direction.

After inspecting the installation and confirming no unsaved work/locked session,
use this baseline backup and restoration guard in Bash before installation:

```bash
export INSTALLED="$HOME/.config/omarchy/plugins/boundsj.raman"
export LIVE_RAMAN_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/raman"
test -f "$HOME/.config/omarchy/shell.json"
cp -a "$HOME/.config/omarchy/shell.json" "$QA_ROOT/backup/shell.json"
sha256sum "$QA_ROOT/backup/shell.json" > "$QA_ROOT/backup/settings.sha256"
if test -e "$INSTALLED" || test -L "$INSTALLED"; then
  cp -a "$INSTALLED" "$QA_ROOT/backup/plugin-copy"
  readlink "$INSTALLED" > "$QA_ROOT/backup/plugin-link.txt" || true
  touch "$QA_ROOT/backup/plugin-was-present"
fi
if omarchy-hyprland-session-locked; then
  echo 'STOP: unlock before shell replacement'; exit 1
fi
while quickshell kill -p "$OMARCHY_PATH/shell" --any-display; do :; done
# Wait for the original RAMen parent/worker to exit before copying state.
pgrep -af raman_probe.py || true
# If any original pair remains, wait for it; do not kill unrelated processes.
if test -d "$LIVE_RAMAN_STATE"; then
  cp -a "$LIVE_RAMAN_STATE" "$QA_ROOT/backup/production-history"
fi
cat > "$QA_ROOT/restore-installation.sh" <<'SH'
#!/bin/bash
set -eu
omarchy-shell boundsj.raman demo off || true
omarchy-shell boundsj.raman close || true
if test -L "$INSTALLED" && test "$(readlink "$INSTALLED")" = "$QA_ROOT/plugin"; then
  rm "$INSTALLED"
fi
if test -e "$QA_ROOT/backup/plugin-held" || test -L "$QA_ROOT/backup/plugin-held"; then
  if test -e "$INSTALLED" || test -L "$INSTALLED"; then
    echo 'STOP: unexpected plugin appeared; preserve it and restore manually'; exit 1
  fi
  mv "$QA_ROOT/backup/plugin-held" "$INSTALLED"
fi
cp -a "$QA_ROOT/backup/shell.json" "$HOME/.config/omarchy/shell.json"
cmp "$QA_ROOT/backup/shell.json" "$HOME/.config/omarchy/shell.json"
omarchy restart shell
omarchy-shell boundsj.raman demo off || true
omarchy-shell boundsj.raman status || true
# Restore any changed theme/output/scale/workspace/pointer from recorded backups.
# Never replace newer production history with an older backup automatically.
SH
chmod 700 "$QA_ROOT/restore-installation.sh"
trap 'bash "$QA_ROOT/restore-installation.sh"' EXIT HUP INT TERM
```

The `plugin-copy` backup preserves a symlink without dereferencing it. Moving
the original aside at installation (below) permits exact restoration of a
relative symlink/directory. Validate production history continuity and manually
restore any desktop settings you changed; the guard restores plugin/shell.json.
Keep the guard installed for the whole QA shell session; use a dedicated terminal.

Do not rely on `XDG_STATE_HOME=... omarchy restart shell`: that script launches
from the compositor's canonical environment and does not inherit a terminal's
transient variables. Use the temporary probe wrapper below to isolate **only
RAMen**, leaving the shell's other plugins/state untouched.

## Temporary test-owned installation

Copy tracked product files into a QA plugin, keeping the original source head
unchanged. Do not alter its manifest ID or create a competing live IPC target.
The existing installation is preserved before replacing it temporarily.

```bash
cp ./*.qml ./*.js ./*.py manifest.json "$QA_ROOT/plugin/"
mkdir -p "$QA_ROOT/plugin/docs"
cp docs/demo-scenes.json "$QA_ROOT/plugin/docs/"
export RAMAN_QA_REPO="$PWD"
export RAMAN_QA_PLUGIN="$QA_ROOT/plugin"
export RAMAN_QA_STATE="$QA_ROOT/state"
export RAMAN_QA_PRODUCER=synthetic
python3 - <<'PY'
import os
from pathlib import Path
repo, plugin, state = (Path(os.environ[k]).resolve() for k in
                       ('RAMAN_QA_REPO', 'RAMAN_QA_PLUGIN', 'RAMAN_QA_STATE'))
synthetic = os.environ['RAMAN_QA_PRODUCER'] == 'synthetic'
entry = repo / ('scripts/qa-synthetic-probe.py' if synthetic else 'raman_probe.py')
wrapper = ('import os, runpy, sys\n'
           + 'sys.path.insert(0, ' + repr(str(repo)) + ')\n'
           + 'os.environ["XDG_STATE_HOME"] = ' + repr(str(state)) + '\n'
           + ('os.environ["RAMAN_QA_STATE_HOME"] = ' + repr(str(state)) + '\n'
              + 'os.environ["RAMAN_QA_SCENE"] = "cycle"\n' if synthetic else '')
           + 'runpy.run_path(' + repr(str(entry)) + ', run_name="__main__")\n')
(plugin / 'raman_probe.py').write_text(wrapper)
PY
```

Preserve the generated wrapper as evidence; only its environment/source
selection differs from the reviewed probe. Confirm it names this checkout and
QA state, with no production state path. Replace the preserved installed plugin
with a symlink to `$QA_ROOT/plugin`:

```bash
if test -e "$INSTALLED" || test -L "$INSTALLED"; then
  mv "$INSTALLED" "$QA_ROOT/backup/plugin-held"
fi
ln -s "$QA_ROOT/plugin" "$INSTALLED"
```

Enable RAMen temporarily if needed through
`omarchy plugin enable boundsj.raman right --after omarchy.tray`, and restart
shell. Keep the temporary shell.json diff. Verify `status` shows one service
runtime and one waiting-parent/worker pair, with state files only in
`$QA_ROOT/state/raman`. Never add synthetic command handling to production code.

The QA producer substitutes numeric readings and protected synthetic app rows;
it never scans/signals real processes. Its cycle is 8 s healthy, 18 s warning,
22 s critical, 8 s low RAM with missing PSI, then healthy through second 80.
Fixed cases first emit **12 seconds of healthy readings in the same dispatcher
lifetime**, then stay at the selected level. This recovery is necessary after
the earlier cycle created dispatch state; restarting directly into an elevated
constant would remain suppressed. To select a case, use this exact wrapper edit
(only in the preserved temporary QA plugin):

```bash
export RAMAN_QA_CASE=critical  # healthy, warning, critical or missing-psi
python3 - <<'PY'
import os, re
from pathlib import Path
scene = os.environ['RAMAN_QA_CASE']
assert scene in ('healthy', 'warning', 'critical', 'missing-psi')
wrapper = Path(os.environ['RAMAN_QA_PLUGIN']) / 'raman_probe.py'
text, count = re.subn(r'os.environ\["RAMAN_QA_SCENE"\] = [^\n]*',
                     'os.environ["RAMAN_QA_SCENE"] = ' + repr(scene), wrapper.read_text())
assert count == 1, 'expected the generated synthetic QA wrapper'
wrapper.write_text(text)
PY
omarchy restart shell
omarchy-shell boundsj.raman demo off
omarchy-shell boundsj.raman status > "$QA_ROOT/logs/fixed-start-status.json"
```

Enable warning-and-critical (or critical for that case), hold 2–10 s, and
cooldown 60 s in the temporary settings **before** this restart. Observe the
healthy warmup after the matching configuration handshake, then the new elevated
incident after its hold. Confirm one receipt/ID and the initial visible toast,
with saved/session-only status and notification time in evidence, **before**
starting a no-repeat interval or making a notification capture. If an earlier
same-severity cooldown suppresses that first toast, leave the wrapper at healthy
for more than 60 s, then restart with the case above; do not delete dispatch
metadata to bypass the tested policy. A no-repeat check with no first toast is
FAIL/NOT RUN, never PASS. `RAMAN_QA_WARMUP_SECONDS` may be set in the wrapper
to 8–120 for slower startup; record the value.

All demo commands remain
separately isolated and send **no toasts**. This producer exercises the actual
live state machine safely; it is labelled synthetic QA, never a performance or
physical memory-pressure test.

## Automated checks and expected results

```bash
node tests/test_level.js && node tests/test_runtime.js && node tests/test_panel_nav.js \
  && node tests/test_history_model.js && node tests/test_incidents.js \
  | tee "$QA_ROOT/logs/node.txt"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests \
  > "$QA_ROOT/logs/python.txt" 2>&1
RAMAN_QML_SHELL="$OMARCHY_PATH/shell" scripts/check-qml.sh \
  > "$QA_ROOT/logs/qt-1x.txt" 2>&1
QT_SCALE_FACTOR=2 RAMAN_QML_SHELL="$OMARCHY_PATH/shell" scripts/check-qml.sh \
  > "$QA_ROOT/logs/qt-2x.txt" 2>&1
git diff --check
```

Each command must exit 0. Expected at implementation handoff: five Node success
messages, 82 Python tests OK, 46 Qt tests at each scale. Preserve command exit
statuses (use Bash `set -o pipefail` for pipelines), totals and warnings; do not
hide Qt 6.11 palette-name warnings. A revised head may legitimately change
counts; compare to its PR. Regressions include clocks, dwell/cooldown, context,
nulls, safety, command bounds, corrupted state, ownership and persistence.

## Live matrix: record each row separately

Use widget settings or edit only the temporary RAMen entry in shell.json;
restart when needed and record the actual settings from `status`. Default
history on/persist on/alerts off; hold 10 s, cooldown 300 s. Use hold 2–10 s and
cooldown 60 s for bounded QA. Numeric out-of-range imported settings clamp to
2–60 and 60–3600; schema controls show those bounds.

| Check | Steps and expected evidence |
| --- | --- |
| Stable/brief/sustained | Healthy creates no receipt. A warning <hold remains chart-only. Continuous warning ≥hold creates one receipt, then extends its lastAt; no row/toast per tick. Use settings hold 60 to make the cycle's short phases fail dwell. |
| Upgrade/recovery/cooldown | With hold 10 the cycle warns, upgrades once after ≥10 s critical and keeps the same ID. A warning notification's cooldown does not block critical; same-severity cooldown still applies. Fixed critical for >60 s never repeats just because cooldown expires. Adjust warn threshold around known synthetic usage to verify 3-point recovery hysteresis; require actual healthy recovery before another event. |
| Missing/gaps/clocks | Low RAM + missing PSI interrupts instead of proving pressure recovery. After restart/takeover, low measured RAM re-arms RAM: a later critical RAM breach must record/alert even with PSI still null. Pressure stays suppressed until measured recovery. PSI-only warning remains pressure-driven while PSI falls from 6 to 3 through dwell (RAM 50). Pending dwell resets across gaps. Unit clock tests cover rollback/long tick/suspend/reboot; live suspend is a separate permission gate below. |
| Modes/collection | Off records incidents, no toast. Critical skips warning toast, allows critical. Warning-and-critical allows both. Collection off keeps live gauge, collects nothing, no incidents/toasts and preserves saved history. |
| Persistence/session/clear | Off starts fresh session-only rings and never loads/saves history.json; cooldown/boot-only dispatch metadata is separate. Re-enable may adopt previous saved history without replay. Clear on demo erases only demo; Clear on test-owned live state erases file/rings without stopping status. With two private fallback probes and persist off, Clear through either removes pre-clear readings/active receipt in the other and invalidates its pending toast. Never clear production. |
| Context/no hidden scan | Close all Memory panels: every new receipt says not observed and no smaps/per-PID reads. Open Memory: receipt uses ≤5 synthetic apps with timestamp from an already active snapshot age ≤5 s. Start warning with observed context, close Memory for two minutes, then upgrade: the receipt keeps its original observation but the upgrade toast must not show it. A fresh subscribed upgrade snapshot may appear. History alone must not scan. For stale context, deliberately delay/drop apps in a **documented QA-only derivative**, retain subscription and run incident; store no context. Document that injected branch; automated test covers exact age 5 vs 5.01 s. |
| Receipt/read-only/navigation | Chart windows, nulls, gaps, start/threshold/peak/upgrade/interrupted duration, app age and not-caused wording accurate. History j/k/h/l/Enter/Esc works; x/f never signals. Memory synthetic rows protected; original guarded fake demo two-step TERM/KILL still works. History must not make a stale PID actionable. |
| Notification/ack/action | With safe synthetic live producer and alerts enabled, visible toast appears only after incident and dispatch checkpoints/reply. Unsaved event explicitly says not saved. Click opens correct incident on focused monitor; manual `omarchy-shell boundsj.raman openIncident ID` does the same. Start with target already on History/5m, and another visible History panel awaiting a reply: target must query 24h and inspect; the other panel must not consume the ID. Expired/cleared ID explains no longer retained. Respect DND; test both recorded DND states without treating suppression as delivery failure. |
| Write/helper failures | In QA state only, replace `history.json` with a directory after backing it up: checkpoint cannot replace it, live event survives with unsaved toast. Restore path and verify recovery. For dispatch.json failure, separately block that path: conservative restart suppression remains. For helper failure, use a separate QA shell/runtime or wrapper with controlled helper exit; status reports helper failure and IPC remains usable. Never change global PATH/system helper. |
| Restart/takeover | Restart during pending and active warning/critical. Restored receipts are interrupted, no retroactive toasts; new owner waits for measured recovery of the relevant dimension before fresh detection. Test saved and session-only modes. Reserve then drop ack (QA-only stub): no retry toast. Follower lease cannot reserve; owner exit/takeover changes epoch and invalidates old acknowledgements. |
| Two monitors | **Physical** monitors: one service/probe pair/dispatcher, one notification total per transition, click on focused monitor opens correct receipt. Two simultaneously visible Memory panels: closing one keeps detail for other; both closing stops it. Test reload/unplug/replug. If only virtual output available record exactly that, and leave physical/simultaneous-panel checks pending or obtain explicit Jesse-accepted exception. |

Round-two regression gates (record each against the exact reviewed head):

- Missing PSI: use a documented QA-only synthetic input sequence with RAM 80%
  for one sample, then 74% through dwell and an active extension. Expect one
  same-ID warning/extend, never an interruption at 74%. At 72% warning remains
  held; below 72% with PSI still unreadable, expect interrupted, not recovered.
  A pressure-only warning must not manufacture a RAM warning when PSI disappears.
- Durable remote Clear: two synthetic private probes sharing QA-only state,
  persistence on. Store an incident through the writer; Clear through its
  follower and refresh the writer. Before its next ordinary minute deadline,
  expect remote invalidation, no pre-clear receipts/readings in memory **and
  history.json**, matching clearToken and unchanged cooldowns. Repeat with
  persistence off. Force a QA-only history write failure, restore it, and verify
  erasure retries at the next tick rather than waiting a minute. `persisted`
  on a follower Clear acknowledges the durable erase marker, not a synchronous
  replacement by a different process; failed replacement remains visible in
  the writer's persistence status.
- Receipt privacy: run the injected-clock long-incident tests rather than
  manipulating GLHF's system clock or waiting 25 hours. Expect stable ongoing
  ID/timing through expiry, absent old names/footprints/thresholds, null old
  start/upgrade measurements, explicit expired labels, and working fresh
  upgrade/recovery. Inspect a QA-only fixture carrying these expired fields in
  the real History view and check read-only keys. Clearly identify fixture
  provenance; it proves presentation, not 25 hours of on-device observation.
- Notification markup: in a documented synthetic derivative use display names
  `<b>Synthetic warning</b> &amp; text`, quotes and a harmless literal image-tag
  string. With fresh active Memory context, the real toast must show the literal
  name, no formatting or image load. Verify receipt text remains literal too.
  Demo scenes never toast; use the isolated live synthetic producer and opt-in
  alert settings for notification verification/capture.
- Process discovery: the restart checker must find exactly the supported
  parent/worker pair, including `--defer-history` and the QA synthetic wrapper.
  Run the three documented live restart checks only after this discovery passes;
  local discovery tests do not prove a live shell restart.

Observe `omarchy-shell boundsj.raman status` and test-owned history/dispatch
files with private local logs. Never attribute actual apps to synthetic pressure.
Use `strace -f -e trace=openat` or an equivalent available tracing tool on the
**test-owned** probe to establish zero smaps/per-process reads while closed;
keep raw trace local and summarize/redact paths on the PR. History and alerts
must not enable detail. Kill verification uses fake demos or test-owned children
from the suite only, never actual desktop applications.

## Real-source lifecycle and performance (still isolated state)

After synthetic checks, regenerate the wrapper with
`RAMAN_QA_PRODUCER=real` (same snippet above), turn alerts off, history on/persist
on, `demo off`, and restart. The original reviewed `/proc` probe now runs with
only its state destination changed. Establish one pair; run **three separate**
checks and keep exit statuses:

```bash
XDG_STATE_HOME="$QA_ROOT/state" python3 scripts/check-restart-history.py \
  > "$QA_ROOT/logs/restart-1.txt" 2>&1
XDG_STATE_HOME="$QA_ROOT/state" python3 scripts/check-restart-history.py \
  > "$QA_ROOT/logs/restart-2.txt" 2>&1
XDG_STATE_HOME="$QA_ROOT/state" python3 scripts/check-restart-history.py \
  > "$QA_ROOT/logs/restart-3.txt" 2>&1
```

Each must print PASS, save readings immediately preceding its own restart and
show no old worker plus one shared pair afterward. The probe wrapper, not the
command's environment alone, makes the restarted probe use QA state.

Measure ≥60 s closed Memory/History and ≥60 s open Memory using test-owned
state. Capture summary arrival times, worker CPU/RSS, stdout sizes and
history.json mtime/replace counts. Expected cadence approximately 2 s; detail
2.5 s only with subscription; ordinary saves ≤one/minute, plus start/upgrade/end
checkpoints, final flush and explicit clear. Dispatch policy writes only on
notification reservation. Compare same-host merged H2 baseline if available,
with equal settings/workload and separate state. Report measurements and
sampling length, no guessed overhead or synthetic-source performance claims.
Use history off/on and alerts off/on cases; query errors/restarts must remain
responsive. Avoid significant load or memory exhaustion on GLHF.

Hardware suspend requires **explicit Jesse permission immediately before the
operation**, with preservation and recovery route confirmed. Without it mark
NOT RUN, not PASS. If approved, pending dwell must reset, active continuity end
interrupted, charts stay blank through suspend, no repeated sustained toast
until recovery, and sampling/IPC resume. VM/offscreen/injected clocks never
prove physical suspend.

## Capture plan and provenance

The current gallery is unchanged. Promote captures only after the live checks
pass at the reviewed head. Capture the **actual RAMen widget**, non-private
synthetic inputs, clean desktop, no private paths/windows; never competitor
screens, reconstructed charts, misleading composites, fake notifications or
AI-generated product screenshots.

Reusable full-resolution clean PNG masters are intended tracked deliverables
in `docs/media/masters/`, with a tracked provenance manifest alongside. README
cropped/resized derivatives belong separately in `docs/screenshots/`. Exploratory
captures/logs remain in `.agent-artifacts/`. Do not overwrite originals with
resize/compression; save lossless native-resolution master first, then derive.
A useful basename is `raman-h3-history-receipt-dark-160pct-synthetic.png`.
Provenance per file: full SHA, UTC capture time, exact source type/scene, Omarchy/
Hyprland/Quickshell/Qt revisions, physical/virtual output, native resolution,
logical geometry, scale, theme/font, motion/DND/settings, capture command,
master hash and derivative crop/resize recipe. Public provenance omits usernames
and private paths. README alt text names what the state actually shows.

Capture checklist:

- Green/yellow/red bars and Memory panels using `demo green|yellow|red`, plus
  protected rows and safe two-step fake-row confirmation where useful.
- History 5m/1h/24h, one cursor, gap/missing PSI, start/upgrade/interrupted
  receipt, fresh context and not observed. `demo history` gives stable,
  non-private History captures and no production writes.
- Alerts off/critical/warning-and-critical and collection/persistence controls;
  bounds, unsaved warning and safely induced query failure/empty/loading where
  useful. A transient loading state needs real capture timing, not fabrication.
- Real synthetic QA notification toast and click-open receipt from the temporary
  live producer. **Demo never toasts**: do not lower thresholds while demo is on
  expecting a toast. Use the isolated synthetic producer and real installed
  notification helper to verify/capture it; label as synthetic QA, not actual
  machine distress. IPC fallback is sufficient on unsupported helper versions,
  with that limitation recorded.
- Narrow/short card, dark/light desktop themes where safely supported, actual
  HiDPI/native scale and local animations off/reduced motion. Offscreen theme/
  1x/2x evidence is useful separately but cannot be promoted as live capture.
  Hardware/display/theme cases unavailable on GLHF stay explicit pending gates.

`grim` can capture clean whole frames to masters, or real widget geometry to
native PNGs; use RAMen `geometry`/Hyprland output metadata for bounds and verify
no nearby private content. `scripts/screenshots.sh` regenerates tracked README
images and switches workspace; run only intentionally after preserving them,
with its dependencies checked. It does not supply the whole History/marketing
capture matrix. Inspect every PNG at original resolution, ensure glyphs/focus/
text legibility, attach provenance, derive README images and update gallery
only when those actual masters are ready. Restoration still runs on failure.

## PASS/FAIL report and handoff

For each check keep:

```text
Check ID: H3-NOTIFY-01
Result: PASS / FAIL / NOT RUN
Exact HEAD: <full SHA>; PR head reverified: <full SHA>
Platform: <versions, physical/virtual outputs, scale/theme>
Settings/source: <full settings, real / synthetic QA / demo / offscreen>
Command/actions: <reproducible steps and exit status>
Expected: <specific outcome>
Observed: <specific outcome, times/counts/IDs>
Evidence: <local log/capture path and safe PR attachment/commit link>
Limitation/follow-up/owner: <including accepted exception if any>
Restoration: <plugin/settings/history/workspace checks>
```

Post consolidated QA_PASS only if required rows pass; otherwise QA_FAIL or
PARTIAL with failures/gates. Report on the same OPEN PR against exact SHA using
`gh pr comment "$PR_URL" --body-file "$QA_ROOT/report.md"`. Link that PR with
T3's `link_pull_request`. Keep RAMEN-1/2/11/14 In progress pending independent
review, applicable QA, Jesse's acceptance and verified merge. H1/H2 stay Done.
No permanent deployment, merge or ticket Done action is part of QA. If a fix
changes head, parent routes implementation/review and QA reruns affected checks.

## Copy-paste separate GLHF thread brief

```text
Act as GLHF RAMen QA/capture agent after the parent review loop. PR: <OPEN PR URL>.
Exact independently reviewed head: <FULL SHA>. Follow docs/qa/glhf-h3.md at that
head; verify PR head matches before/after. Read AGENTS.md and README.md. Preserve
installed plugin, byte-identical shell settings, private history, workspace,
pointer/theme/scale/outputs; prepare restoration before temporary installation.
Use test-owned RAMen state and the safe synthetic producer for incidents and real
notification tests, never real memory exhaustion or actual-app kills. Run all
Node/Python/Qt checks and the full live matrix, three restart-history checks,
measured cadence/write-rate/overhead, action/restart/takeover/no hidden scans and
two-monitor checks. Report physical versus virtual evidence honestly. Ask Jesse
explicitly before hardware suspend; record unsupported cases and accepted
exceptions. Capture original-resolution non-private real-widget PNG masters,
with exact-head environment/theme/scale/scene provenance, separately derived
README images, and promote only valid captures to tracked destinations. No
composites or invented screenshots; demo sends no toasts. Report findings on
this PR and working Notion issues; keep issues In progress, H1/H2 Done. Restore
original installation/settings and demo off/live state at exit. Do not merge,
permanently deploy, mark tickets Done, launch further threads or spawn reviewers.
Parent owns implementation fixes and independent review scheduling.
```
