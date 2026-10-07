# H3 on-device QA — 2026-10-04

**PARTIAL: exercised checks passed; remaining gates below are not waived.**
No confirmed product defect was found in the exercised paths. This is not a
merge recommendation or a claim that the entire runbook is complete.

PR: archived development PR #3. Reviewed and tested product head:
`4f934763883f4d876c5d7a8c5c12a0d9e7f07026`; GitHub head matched before testing
and after capture. This QA branch adds documentation and images only.

## Environment and isolation

Physical laptop display eDP-1, 2560×1600, 160% scale (1600×1000 logical).
Omarchy 4.0.4-1, Hyprland 0.56.2-2, Quickshell 0.3.1-1, Qt base 6.11.2-3 /
declarative 6.11.2-1, Python 3.14.7, Node 26.8.1. Tokyo Night theme,
JetBrainsMono Nerd Font. No theme, output or scale configuration was changed.

The original plugin symlink and byte-identical shell settings were preserved.
The shell was stopped before backing up production history. A guarded temporary
plugin copy used a wrapper that set RAMen's state destination beneath the
repository's ignored `.agent-artifacts/pr3-qa/`; other shell state was not
redirected. Source files stayed at the reviewed revision.

Synthetic testing used the supplied producer and a documented QA-only derivative:
`control.json` supplied numeric readings and protected fake app rows; an emit
wrapper logged protocol replies. It never scanned or signalled real apps.
The helper-failure case temporarily substituted an absolute test script exiting
17 for the notification helper in the copied runtime, then restored that file.
Real-source lifecycle checks used the original probe with only its state path
redirected. No production history was cleared, and no real memory pressure or
real-app termination was induced.

## Automated checks

| Check | Result |
| --- | --- |
| Level, Runtime, PanelNav, HistoryModel, incidents | Five Node suites pass |
| Python unittest discovery | 89 tests pass, no skips |
| Qt offscreen at 1× | 51 pass |
| Qt offscreen at 2× | First run: 47 pass, four screenshot-save timeouts; repeat: 51 pass |
| Whitespace | `git diff --check` passes |

The installed Omarchy package omits the adjacent LICENSE expected by the Qt
script. A local copy of the installed shell plus upstream v4.0.4's LICENSE
allowed the unchanged harness to run. Both Qt scales emit palette-name override
warnings under Qt 6.11. The first 2× run overlapped shell replacement; all four
failures were `saved …` assertions in the capture helper, not behavior assertions.
The repeat passed without changing product or test source. The timing cause is
not established. Both logs are retained.

## Live checks

| Check | Observed result |
| --- | --- |
| Healthy / brief spike / sustained | Healthy and a four-second warning under a ten-second hold produced no incident. Sustained warning produced one receipt and extensions. |
| Modes | Alerts off still records; critical-only skips warning notification and allows the critical upgrade; warning-and-critical produced both severities. |
| Upgrade and recovery | Warning upgraded to critical under the same receipt ID. Measured healthy recovery closed it; low RAM with missing PSI interrupted it. |
| No repeat | After an acknowledged, visible critical toast, another 63 seconds at critical produced no additional reservation or receipt ID despite a 60-second cooldown. |
| Missing PSI / hysteresis | RAM 80% → 74% with null PSI stayed qualifying through dwell. Exact 72% extended the same receipt; 71.9% interrupted. A PSI-only warning did not invent RAM evidence when PSI vanished. |
| Collection / bounds | Collection off left the gauge updating and produced no incidents. Imported hold=0/cooldown=5 clamped to 2/60. |
| Session / clear | Session-only incident acknowledgements were unsaved. Test-owned live Clear remained responsive. Demo Clear left the live clear token unchanged. |
| Context | Closed Memory produced `not-observed`; an active Memory subscription supplied a fresh protected synthetic app observation. History kept detail false. |
| Literal notification text | A name containing `<b>`, `&amp;`, quotes and a literal image-tag string appeared as text in the actual host notification and receipt. |
| Notification action | Installed discrete-argv helper delivered real warning and critical notifications. Host `notifications invokeLast` executed the same default-action callback as a click and opened the correct receipt in the 24-hour view. A physical pointer click was not exercised. |
| DND | With DND on, the normal warning was recorded and acknowledged while the captured desktop showed no warning toast. DND was returned to off. |
| Write failure | Replacing only the QA history path with a directory retained the live receipt, returned persisted=false/error, showed an unsaved toast and a visible save error. Restoring the path allowed a subsequent checkpoint. |
| Helper failure | Test helper exit 17 appeared in runtime status; status IPC stayed responsive. Original runtime file was restored. |
| Active restart | Restart while continuously critical created no new receipt or notification reservation; the new owner remained blocked until measured recovery. |
| Keyboard / safety | History windows, cursor, receipt inspection and Esc back worked. x/f/f in History generated no killed reply. Red demo showed arm, four-second disarm and confirmed fake-row removal. Protected rows remained marked. |
| Real-source restarts | Three separate `check-restart-history.py` runs PASS. Each saved a reading from its own restart boundary, removed all old processes and returned exactly one service probe pair. |
| Closed / History process access | All 54 recorded cycle status observations had detail=false. A separate ten-second Python open-audit of the real probe with five History queries observed zero `/proc/<pid>/…` opens. This was an audit hook, not a kernel syscall trace. |

The first boundary fixture rounded 72% down to 71.99999%; its first correction
briefly emitted that rounded reading before the exact value. Both attempts
correctly interrupted. The final fixture emitted only exact values using a
divisible total and passed. These were QA fixture errors, not product failures.
The original cycle recorder was interrupted by the next scenario's shell restart
after 63 seconds; warning, upgrade, interruption and action evidence had already
been collected. The separate completed no-repeat check supplies cooldown evidence.

## Real-probe measurements

Each case ran for approximately 62 seconds with its own state directory, on this
machine, sequentially. CPU is the worker's sampled user+system CPU, excluding
startup and final flush; RSS is its sampled peak. These are probe measurements,
not total shell/JavaScript overhead or a statistically significant benchmark.
Live UI QA continued during the measurements.

| Case | Summaries | Mean cadence | App messages | Worker CPU | Peak RSS | Observed file versions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| H3 closed, history on | 31 | 2.0022 s | 0 | 0.02 s / 0.032% core | 12,336 kB | 1 |
| H3 detail open | 31 | 2.0093 s | 25 | 5.91 s / 9.521% core | 13,912 kB | 1 |
| H3 collection off | 31 | 2.0018 s | 0 | 0.03 s / 0.048% core | 12,600 kB | 1 |
| H2 closed baseline `b47a5e5` | 31 | 2.0020 s | 0 | 0.02 s / 0.032% core | 14,136 kB | 1 |

Open-detail maximum summary interval was 2.2467 s. File versions count observed
inode/mtime pairs before EOF, not write syscalls; shutdown flush is excluded.
The collection-off process started normally before receiving its disable command,
so its one initial file version does not demonstrate a write while disabled.

## Captures

Eleven real native-resolution PNG masters cover three bars, three Memory panels,
5m/1h/24h History, a read-only receipt, and a real synthetic-input notification.
They were inspected at original resolution and copied byte-for-byte into
[`docs/media/masters`](../media/masters/). The
[provenance manifest](../media/masters/provenance.json) records SHA256, exact
source, UTC time, capture geometry, versions, scale, theme, settings and each
separate README derivative recipe. No composite or generated product imagery
was used. Exploratory full frames and failure-state captures stay ignored.

Spelling note (2026-10-06): the product name is RAMen. The notification
master and its README derivative show the title the widget sent at this
revision, "RAMan · Critical", which was later corrected. They are kept
unretouched with their recorded hashes; no other capture shows the name.

## Remaining gates

- Physical second-monitor routing, simultaneous visible panels and unplug/replug;
  only one physical display was present. User explicitly deferred these checks.
- Hardware suspend/resume: user explicitly deferred it after the automated work.
  No suspend was performed.
- Additional live-only cases from the runbook: session-only takeover, dropped-ack
  injection, dispatch-file failure, stale context after two minutes, expired
  receipt fixture rendering, delayed app delivery, and remote Clear with live
  multiple fallback runtimes. Related automated coverage passed; it is not
  substituted for these live checks.
- Alternate live themes, short/narrow physical output and a screen-reader pass.
  Offscreen theme/size tests are separate evidence.
- Full-shell alerts-on/off overhead comparison and physical pointer-click action.
- User acceptance and authorized merge. No PR comment, issue transition, merge
  or permanent deployment was performed by this QA pass.

## Restoration and local evidence

Restoration passed after the user deferred both hardware checks. The original
plugin symlink target and byte-identical shell settings match their backups.
Workspace 2, pointer coordinates, original DND=off, theme and 160% scale were
restored/preserved. The original service returned live readings with no detail
subscription. Production history was byte-identical immediately before restoring
the original plugin; its clear token remained unchanged afterward. No older
backup was copied over production history.

The restoration log, original backups, raw protocol/status logs, scratch scripts,
test attempts and environment details remain under ignored
`.agent-artifacts/pr3-qa/`. Private process data is not copied into this report
or the gallery. GitHub's PR head was rechecked after restoration and still
matched the tested revision. The subsequent QA evidence commit adds only this
report, screenshot masters/provenance, and README gallery updates; it does not
change the tested product implementation.
