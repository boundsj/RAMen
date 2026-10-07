# S3 implementation-host evidence

> **Current status (2026-10-06).** S3 was accepted and merged in PR #6 as `9ca8b4d` after the correction retest. Its outstanding hardware, visual
> and Storage gallery items moved to the consolidated [final QA runbook](final-qa.md)
> at Jesse's direction. Read-only-source/unsupported-filesystem hardware QA and
> stalled/uninterruptible-I/O checks are excluded, not deferred. The text below
> is the historical procedure and evidence for its named revisions.

Implementation baseline is accepted S2 merge `8192c593baefaaf43153842dba70a32748627d56`.
The commit containing this report is the implementation handoff; parent must pin
its full SHA for independent review and the separate [S3 native QA](glhf-s3.md).
This report does not close S3 or claim live/hardware acceptance. Final gallery
and feature-wide documentation reconciliation remain S-DOC.

## Actual results

Host: macOS, Node 26. Linux checks: task-owned Docker `raman-s2-fixes:local`,
Debian 13 arm64, Python 3.13.5, Qt 6.8.2. Python/performance copies used the
container's filesystem; Qt mounted the source and separately supplied Omarchy
v4.0.4 UI with tracked process/IPC/window doubles. Linux containers use `--init`
for owned-orphan adoption/reaping. No remote GLHF, installed widget, production
History/cache, personal scan or real desktop notification was accessed.

- All six Node suites pass: Level, Runtime, PanelNav, HistoryModel, Incidents,
  StorageModel (new identity/failure classification checks).
- Python discovery: 147 tests pass, no skips, 47.774 s. Includes existing
  actual test-owned portal/GIO dispatch to directory/file-parent and new original
  fixture safety/content checks. A test desktop handler is not Nautilus/Omarchy.
- Offscreen Qt after the round-one correction: 113 pass, zero failed/skipped at
  1× (9.880 s) and 2× (10.040 s),
  including failure classes, transient/changed capacity during scans, partial
  readiness, cancelled/unknown outcome without cache, helper restart/recovery,
  same-runtime panel isolation, and all earlier keyboard/map/Other/action tests.
  No QML warning; locale fallback notice only. Narrow Storage capture inspected.
- `git diff --check` passes. Source/protocol/settings identities are retained;
  no settings, accounting, backend ownership or source-action contract changed.

Final serial default-worker responsiveness checks on Docker's Linux filesystem:

| Metric | 50,000 fanout | 60,000 fanout |
| --- | ---: | ---: |
| Initial cached page | 27.85 ms | 48.72 ms |
| Five large cached pages | 28.31–29.32 ms | 44.50–53.96 ms |
| Cancel ACK from send | 0.17 ms | 0.23 ms |
| Terminal reconciliation from send | 236.66 ms | 229.30 ms |
| Owned scan worker gone from send | 236.74 ms | 229.41 ms |
| Active manual refresh | 0.25 ms | 0.37 ms |
| EOF cleanup | 220.60 ms | 268.77 ms |
| Waiting-parent loss cleanup | 271.52 ms | 264.19 ms |
| Maximum summary spacing | 2.0072 s | 2.0019 s |
| Minimum progress spacing | 0.5022 s | 0.5000 s |
| Maximum storage wire line | 38,689 bytes | 38,682 bytes |

Both checker runs exit 0 with `status:PASS`; no apps messages while detail is
closed. Copies have `sha:null`, honestly distinguished from a verified checkout;
these source hashes bind the measured backend/checker to this implementation:

```text
raman_probe.py      1c817284c3f43fd443206a922e966a3f5b13b2f828a191122c583dedbbfb9243
raman_storage.py    46c78749e522a1fca65d41f9d0b189006bad7409e15cf498d27582d472e5e539
check-storage.py   33c860ca3525a7b5909c1892b2e3e3606d1fda5f6a7465e2193841e8288daecb
```

Logs, generated fixture manifests, original offscreen captures, copied source,
initial failures and controlled reproductions remain ignored beneath
`.agent-artifacts/s3/`. They are task evidence, not published screenshots.

## Preserved failures and corrections

The first Python run on a macOS bind mount ran 145 tests and failed one actual
Unicode-envelope scan with `unsupported-filesystem` (Docker's virtiofs). The
copied Linux filesystem run exercised that same fixture successfully. This does
not establish that virtiofs is supported, nor replace native filesystem QA.

An initial no-init container failed waiting-parent cleanup because `/proc` still
contained adopted workers. A separate owned orphan reproduction produced
`State: Z (zombie), PPid: 1` without a reaping init and no `/proc` entry with
`--init`. Final checks use init and enforce true absence; the checker does not
relabel zombies as cleaned up. Real Linux init adoption remains native QA.

The first copied-volume full suite overlapped a checker and failed the old
terminal-cancel assertion at 257.98 ms. S3's <250 ms target is the acknowledgement;
interruptible cleanup has a separate <1 s target. The regression now explicitly
reads the `cancelling` ACK and measures it from send against 250 ms, then checks
terminal response and actual owned-worker absence from the same send against
1 s. No acceptance target was raised. The initial terminal timing remains a
recorded observation; it is not claimed to meet 250 ms. Final suites/workloads
ran serially. Prior S2 81–157 ms Docker cached-query misses remain historical,
unexplained evidence; current passes do not erase them or prove their cause.

## Remaining acceptance

Independent exact-head review and Linux/native Omarchy runbook execution remain
open. No installed-bar/service/multi-monitor live proof, real file-manager and
clipboard use, native latency workloads, three live restart-history receipts,
real-widget Serving Board scenes or physical pointer/theme/settings/HiDPI cases
were performed here. Original fixture trees are ready for that authorized QA.
Physical removable media, privileged mount changes/read-only filesystems,
physical monitors/unplug, suspend and uninterruptible kernel I/O remain untested.
Injected metadata/permission faults and offscreen shared-owner checks do not
prove them. Required missing coverage must be completed or explicitly accepted
by Jesse with its scope stated; no waiver or issue completion is inferred.

## Independent review round-one correction

Astra High found one P2 at original handoff `09deac9e4cb790d40777772043ed395a0fdb2a6c`:
Scan/keyboard r during helper downtime (including Reload then r) created a
pending scan without a live pipe. Its presentation phase prevented startup
recovery, leaving no scan ID and unusable controls. The finding was accepted.
Original reproduction is preserved in `.agent-artifacts/s3/review-1/repro.log`;
the original handoff's 108 passing fixture checks had not covered these inputs.

The correction exposes transport availability independently of presentation,
disables scan/actions/reload while unavailable, and records pending requests only
after a successful send. Startup recovery always retires obsolete work and
reloads authoritative capacity/cache for the still-current runtime, after QML
transport bindings settle. It never initiates a scan. Scope choices during
initial startup/downtime are retained for that discovery; runtime migration
cannot install another owner's recovery callback.

Five new Qt cases cover direct Scan, keyboard scan action, Reload, Reload then
r, and initial-startup/scope/runtime changes. They assert no unavailable writes,
no phantom pending requests/scanning, and resumed cached discovery independently
of a deliberately changed presentation phase. All six Node suites and all 113
Qt checks pass at each scale; whitespace checks pass. Backend/checker/Python
files are unchanged from the original validated handoff, so the 147 Python and
two serial responsiveness results above are retained evidence, not claimed as
new correction-head runs. Native/live acceptance remains pending. The reviewer
discarded a separate suspected error-envelope issue after actual controller
reproduction; no product change was made for that suspicion.

## Published native QA and response

PR #6 native QA findings
are pinned to `64684ba59d8834cd385f64f40f2378388055000d`, against base
`8192c593baefaaf43153842dba70a32748627d56`. The report found two P2 gaps:
selected-scope directory-to-file replacement did not invalidate displayed data,
and a clicked/focused ledger delegate consumed arrow navigation. The arrow
integration defect also reproduced on base; it is not a new PR regression.
Both findings are accepted and corrected by the commit containing this section.

Before-fix local evidence is retained in
`.agent-artifacts/s3/qa-fixes-1/before-1x.log`: actual mouse click gave a visible
ledger delegate active focus, Up left its region at ledger, while K/H passed.
The offscreen fixture explicitly requests StrongFocus to match native Qt click
focus. The other regression renames an owned directory, creates a regular file
at its old path, runs actual capacity validation on Linux, and supplies the
resulting correlated error to the real Storage page. Before the correction,
that page retained its ready snapshot. This is real fixture filesystem mutation
plus offscreen UI transport, not a physical remount or live-shell check.

Invalid-path scope validation now retires pending work and drops obsolete
inventory/selection/actions; unrelated volume-list errors remain independent.
Pointer selection restores the existing host key dispatcher, including after
non-editor Storage button clicks. No broad key forwarding was added. Editors
keep active focus and editing arrows/letters; deferred button-focus restoration
checks editor ownership before acting. Regression coverage includes actual
mouse clicks and key events, K/H parity, a clicked button followed by arrows,
path/filter ownership, late action rejection and volume-list isolation.

Correction verification: all six Node suites pass; Qt 6.8.2 offscreen passes
120/120 at 1× (10.762 s) and 2× (10.720 s), no QML warnings, locale fallback only;
whitespace checks pass. Backend/checker code is unchanged. Python/native latency
and live-shell checks were not rerun by this implementation agent; prior-head
results below remain tied to their measured revision.

The published old-head QA report records native Linux 7.2.5/Btrfs, Omarchy
4.0.4, Quickshell 0.3.1 at 160%: six Node suites; 147 Python tests (146 pass,
one optional desktop-portal skip); 113 Qt checks at each scale with palette
warnings also on base. Serial native 50k/60k workloads passed all twelve queries
at 38.29–44.42 ms, ACK 0.26 ms, owned cleanup 237.14/240.90 ms, EOF/parent-loss
under 265 ms. Maximum summary spacing was 0.9167/2.0001 s; the shorter scan
cannot establish sustained cadence. Three isolated live restart-history checks
passed. Installed-service explicit/cache/cancel/close/restart/Other/partial/
empty/settings checks, real Nautilus directory/file-parent opens, byte-exact
clipboard and non-UTF8 refusal, Memory demo safety and read-only History passed.
The QA owner restored plugin/settings/clipboard and production History/cache;
this implementation agent performed no remote access or live installation.

That evidence is for the old head, not the correction head. Focused exact-head
native retest of both failures and neighboring input/action behavior remains
required, along with independent review and Jesse's acceptance/merge. Suspend
and monitor-dependent checks are deferred at the user's request in the QA
report. Removable/remount/read-only/privileged filesystems, uninterruptible I/O,
alternate themes/card sizes/physical scales and exhaustive live fault injection
remain untested; no deferred or injected case is relabelled as passed. The
published findings are not full S3 acceptance or merge approval.
