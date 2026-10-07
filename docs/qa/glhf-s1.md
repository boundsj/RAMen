# Storage S1 live Omarchy QA handoff

> **Current status (2026-10-06).** S1 was accepted and merged in PR #4. Its outstanding hardware, visual
> and Storage gallery items moved to the consolidated [final QA runbook](final-qa.md)
> at Jesse's direction. Read-only-source/unsupported-filesystem hardware QA and
> stalled/uninterruptible-I/O checks are excluded, not deferred. The text below
> is the historical procedure and evidence for its named revisions.

Run this in a separate authorized GLHF QA thread at the reviewed open PR's
exact head. S1 is backend-only: confirm the existing Memory/History widget and
probe lifecycle, run Linux fixtures/real pipes, and collect three live restart
receipts. The Storage page, its keyboard/action UX and final screenshots remain
S2/S3 work. Do not merge, close tickets or permanently deploy this PR.

## Checkout and baseline

Use Bash in an unlocked Omarchy session with Python 3, Node, git, pgrep/procps
and the current Omarchy shell. Substitute the reviewed SHA supplied in the PR
handoff. Save logs only below the repository `.agent-artifacts` directory.

```bash
cd /path/to/reviewed/RAMen
export RAMAN_S1_SHA=REVIEWED_FULL_SHA
test "$(git rev-parse HEAD)" = "$RAMAN_S1_SHA"
test -z "$(git status --porcelain)"
export QA_ROOT="$PWD/.agent-artifacts/s1-glhf-$RAMAN_S1_SHA"
mkdir -p "$QA_ROOT/backup" "$QA_ROOT/plugin" "$QA_ROOT/logs"
git show --no-patch --format='%H %s' HEAD > "$QA_ROOT/logs/head.txt"
uname -a > "$QA_ROOT/logs/platform.txt"
python3 --version >> "$QA_ROOT/logs/platform.txt"
node --version >> "$QA_ROOT/logs/platform.txt"
omarchy-shell boundsj.raman status > "$QA_ROOT/logs/baseline-status.json"
pgrep -af raman_probe.py > "$QA_ROOT/logs/baseline-probes.txt" || true
hyprctl monitors -j > "$QA_ROOT/logs/baseline-monitors.json"
```

Record Omarchy/Quickshell versions, active theme/scale, existing installation
and settings, and whether simultaneous visible panels/physical monitors are
available. No scanning of personal Home/Root, privileged mounts, hardware
removal or suspend is implied by this handoff. Use test-owned fixtures; hardware
coverage needs a separately accepted exception or appropriate authorization.

## Linux backend checks

Record `uname -r`; safe Storage identity requires Linux 6.8 unique mount IDs
and filesystem root birth time. Older kernels/filesystems must report
`identity-unavailable`; this is a conservative capability limit, not passing
scan coverage. These start test-owned probes with isolated state/cache. They never install a
widget, clear production history, issue desktop notifications or signal apps.

```bash
node tests/test_level.js > "$QA_ROOT/logs/node-level.txt"
node tests/test_runtime.js > "$QA_ROOT/logs/node-runtime.txt"
node tests/test_panel_nav.js > "$QA_ROOT/logs/node-nav.txt"
node tests/test_history_model.js > "$QA_ROOT/logs/node-history.txt"
node tests/test_incidents.js > "$QA_ROOT/logs/node-incidents.txt"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v \
  > "$QA_ROOT/logs/python.txt" 2>&1
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage.py \
  --output ".agent-artifacts/s1-glhf-$RAMAN_S1_SHA/backend-check" \
  > "$QA_ROOT/logs/backend-check.json" 2>&1
cat "$QA_ROOT/logs/backend-check.json"
```

Required outcomes: all suites PASS; correct sparse allocation/hardlink
attribution/no-follow symlinks; non-UTF8 ID navigation; bounded rows/Other/lines;
partial permissions/vanish/mount disclosure; root/remount/cache identity/schema
privacy; prior snapshots after cancellation/limit/crash; stale/queued/foreign
ownership checks; responsive refresh and summary cadence; page leave/EOF and
the actual waiting-parent → probe worker → storage child death chain.

Fault regressions must check the selected snapshot, rather than only old-file
existence: forced cancel/SIGKILL before catalog replacement retains prior data;
after replacement, reconciliation reports committed with the actual ID. Check
clock rollback and four-scope eviction/direct-ID rejection. Persistent sync/GC
faults must report uncertainty and block growth; recovery follows only the
catalog. Repeat cancel/leave while recovery is queued/running/reaping; latest
terminal results must preserve the actual committed ID. Advance generation
with a normal request; stale delivery must disappear while recovery deadlines
and reaping still release its lane. Cancel/leave acknowledgement is `cancelling`/`leaving` followed by a
separate terminal result.

The executable checker reports cached-page latency (target <100 ms), cancel
ack (<250 ms), EOF cleanup (<1 s for interruptible work), full scan elapsed,
progress intervals (>=0.5 s), maximum summary interval, actual wire line bytes,
reviewed SHA/source hashes, and parent-death cleanup. Report slow hardware
honestly; do not enlarge thresholds or invent evidence. If a copied container
tree cannot resolve Git, its SHA is null and source hashes identify the copy;
the real checked-out GLHF run must record the supplied exact SHA.
The checker still writes/prints the same JSON on a cached-page target miss
(`PASS_WITH_TARGET_MISS`), and now exits 1 so automation cannot treat it as a
passing acceptance gate. Only `PASS` exits 0.

## Temporary live widget and restoration

Stage the reviewed tracked source with a small QA wrapper isolating both RAMen
stores. Omarchy restarts use the compositor's environment, so setting XDG
variables only on the terminal restart command is insufficient.

```bash
export RAMAN_INSTALLED="$HOME/.config/omarchy/plugins/boundsj.raman"
export RAMAN_LIVE_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/raman"
export RAMAN_LIVE_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/raman/storage"
cp -a "$HOME/.config/omarchy/shell.json" "$QA_ROOT/backup/shell.json"
if test -e "$RAMAN_INSTALLED" || test -L "$RAMAN_INSTALLED"; then
  cp -a "$RAMAN_INSTALLED" "$QA_ROOT/backup/plugin-copy"
fi
test ! -e "$QA_ROOT/backup/plugin-held"
git archive HEAD | tar -x -C "$QA_ROOT/plugin"
mv "$QA_ROOT/plugin/raman_probe.py" "$QA_ROOT/plugin/raman_probe_real.py"
python3 - "$QA_ROOT" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
source = '''#!/usr/bin/env python3
import os, runpy
os.environ["XDG_STATE_HOME"] = %r
os.environ["XDG_CACHE_HOME"] = %r
runpy.run_path(os.path.join(os.path.dirname(__file__), "raman_probe_real.py"), run_name="__main__")
''' % (str(root / "live-state"), str(root / "live-cache"))
(root / "plugin" / "raman_probe.py").write_text(source)
PY
cat > "$QA_ROOT/restore.sh" <<'SH'
#!/bin/bash
set -eu
if test ! -e "$QA_ROOT/installation-started"; then exit 0; fi
omarchy-shell boundsj.raman demo off || true
omarchy-shell boundsj.raman close || true
while quickshell kill -p "$OMARCHY_PATH/shell" --any-display; do :; done
if test -L "$RAMAN_INSTALLED" && test "$(readlink "$RAMAN_INSTALLED")" = "$QA_ROOT/plugin"; then
  rm "$RAMAN_INSTALLED"
fi
if test -e "$QA_ROOT/backup/plugin-held" || test -L "$QA_ROOT/backup/plugin-held"; then
  if test -e "$RAMAN_INSTALLED" || test -L "$RAMAN_INSTALLED"; then
    echo 'Unexpected plugin exists; preserve it and restore manually'; exit 1
  fi
  mv "$QA_ROOT/backup/plugin-held" "$RAMAN_INSTALLED"
fi
cp -a "$QA_ROOT/backup/shell.json" "$HOME/.config/omarchy/shell.json"
cmp "$QA_ROOT/backup/shell.json" "$HOME/.config/omarchy/shell.json"
omarchy restart shell
omarchy-shell boundsj.raman demo off || true
omarchy-shell boundsj.raman status || true
SH
chmod 700 "$QA_ROOT/restore.sh"
if omarchy-hyprland-session-locked; then
  echo 'Unlock before replacing the shell'; exit 1
fi
trap 'bash "$QA_ROOT/restore.sh"' EXIT HUP INT TERM
touch "$QA_ROOT/installation-started"
while quickshell kill -p "$OMARCHY_PATH/shell" --any-display; do :; done
```

Wait for every original RAMen parent/worker to exit; `pgrep -af raman_probe.py`
shows the candidates. Never kill unrelated processes. Once they have exited,
copy any existing production state/cache as a baseline; the staged wrapper
does not load/write them. Keep private baseline copies in ignored artifacts.

```bash
if test -d "$RAMAN_LIVE_STATE"; then
  cp -a "$RAMAN_LIVE_STATE" "$QA_ROOT/backup/production-state"
fi
if test -d "$RAMAN_LIVE_CACHE"; then
  cp -a "$RAMAN_LIVE_CACHE" "$QA_ROOT/backup/production-cache"
fi
if test -e "$RAMAN_INSTALLED" || test -L "$RAMAN_INSTALLED"; then
  mv "$RAMAN_INSTALLED" "$QA_ROOT/backup/plugin-held"
fi
ln -s "$QA_ROOT/plugin" "$RAMAN_INSTALLED"
omarchy restart shell
omarchy-shell boundsj.raman demo off
omarchy-shell boundsj.raman status > "$QA_ROOT/logs/reviewed-widget-status.json"
pgrep -af raman_probe.py > "$QA_ROOT/logs/reviewed-widget-probes.txt"
```

If the original plugin was disabled, temporarily enable it with the README
install command and record the setting change; restoration restores shell.json.
Verify process environments contain the QA state/cache paths. No scan should
start: there is no Storage page/subscriber. The wrapper/staging difference is
a disclosed QA adapter; `raman_probe_real.py` and every implementation module
must match the reviewed tracked source. Do not call `clearHistory` against an
unverified production probe.

## Live checks and restart receipts

Open Memory and History; verify summary/gauge updates, page/focus/navigation,
read-only History, visible-only app detail and Memory's two-step kill arming
timeout using demo rows only. Run green/yellow/red scenes and restore demo off.
Confirm the Storage page remains unavailable and no `raman_storage.py --worker` exists
while panels are closed or browsing Memory/History. Inspect the backend checker
logs separately; S1's explicit leave hook has pipe evidence, while actual
Storage page-close wiring remains S2.

Run the existing restart checker three times in this temporary staged session:

```bash
XDG_STATE_HOME="$QA_ROOT/live-state" PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/check-restart-history.py > "$QA_ROOT/logs/restart-1.txt" 2>&1
XDG_STATE_HOME="$QA_ROOT/live-state" PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/check-restart-history.py > "$QA_ROOT/logs/restart-2.txt" 2>&1
XDG_STATE_HOME="$QA_ROOT/live-state" PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/check-restart-history.py > "$QA_ROOT/logs/restart-3.txt" 2>&1
```

Each must PASS with no old probe/storage child left. The checker restarts the
live shell; its XDG argument points to the wrapper's test-owned history. It
reads RAMen state and never clears production history. Verify one service probe
pair after restart. With available additional outputs, inspect the existing
shared owner; physical monitors/simultaneous panels and hardware suspend remain
explicit coverage limits if unavailable. No UI screenshots are required for S1.

Restore with `bash "$QA_ROOT/restore.sh"`, then `trap - EXIT HUP INT TERM` to
avoid running the guard twice. Confirm the original installation/settings and
healthy normal probe returned, demo off, no test-owned probes/children remain,
and the production stores match their stopped baselines. Do not automatically
overwrite legitimately newer production history. Record any theme/output/scale
changes and restore them separately.

## Report and separate thread brief

Post the exact SHA, source hashes, automated/backend result, measured targets,
three restart PASS logs, live Memory/History/no-hidden-worker result, restored
installation/settings/stores and specific remaining hardware gates on the PR.
Keep the PR open and issues In progress. Any actionable finding goes through
implementation and independent review at a new head before QA acceptance.

Copy this brief into the separate QA thread:

> Validate the open RAMen S1 PR at the supplied reviewed full SHA on GLHF
> Omarchy. Follow docs/qa/glhf-s1.md; preserve and restore plugin/settings and
> production state/cache using its isolated staged probe wrapper. Run all five
> Node suites, Linux Python suite, scripts/check-storage.py and three separate
> live check-restart-history.py runs. Verify existing Memory/History and demo
> kill safety, shared service/no hidden storage workers, actual scan/cancel/
> leave/EOF/waiting-parent death, private cached paging and measured response
> limits. Storage UI remains unavailable. Report exact-head evidence/limitations
> and restore everything. Do not merge, permanently deploy, close tickets,
> access private folders, mount/unplug hardware or suspend without separately
> applicable authorization.
