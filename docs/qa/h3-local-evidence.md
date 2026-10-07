# H3 implementation-host evidence

This is **local implementation evidence**, not GLHF live QA or release acceptance.
The PR's final full head identifies this delivered tree. Independent Astra
xhigh round 2 reviewed `4f0420f2ae13db8c4dc2b85bcd5e69849c96ded6`: seven prior
threads were independently verified/resolved; active retention remained open
and five actionable findings required this correction. Implementation responses
do not resolve findings. Subsequent Astra xhigh round 3
verified all fixes at `4f934763883f4d876c5d7a8c5c12a0d9e7f07026`.
The later [GLHF report](h3-omarchy-evidence.md) records live checks and captures
with explicit remaining limits; the dated local receipts below remain historical.

## Fix round 2 — current correction receipt

Runtime/tests/docs/runbook commit: `b2f8259879241d3930a2d6d4a7a2e68d302d4930`.
The following evidence commit changes only this document. Exact final PR head
and author response links are in the open PR body. No finding is resolved by
the implementation agent. All five findings are accepted; no contract expansion
or implementation disagreement is requested.

- Five Node suites pass on macOS, Node 26.0.0.
- **89 Python tests pass in Linux Docker**, Python 3.12, no skips, test-owned
  state. The transient `python:3.12-slim` container installed procps for the
  actual restart-discovery regression. **19 focused incident/protocol tests
  pass on macOS**, Python 3.14. These do not require Linux process scans.
  Discovery skips only if the optional `pgrep` executable is absent; it was
  present in both final runs.
- **51 Qt 6.8.2 offscreen checks pass at each 1x/2x**, no QML warnings, only
  the locale fallback notice. Separately supplied pinned Omarchy
  `c668141e9c42b13c80c9ca4ea108e11708c5e8a5` UI/NotificationLogic is staged with
  its license; process/IPC/window/notifier effects remain doubles. StyledText
  geometry matches literal PlainText for tag/entity/quote/image-like names.
  Expired receipt labels and read-only keys are checked in the actual History
  component. No desktop notifications or live shell restarts were performed.
- Actual two-probe synthetic pipe reproduction now reports remote invalidation,
  empty disk receipts and changed file bytes on the writer's **next summary**,
  before the normal minute deadline. Write-failure tests prove a consumed marker
  and a failed local Clear both retry disk erasure on the next tick; prior
  takeover/final-save tests still pass.
- The review's pure detector triggers now produce warning start/extend for
  80%→74% RAM/null PSI. Regression cases include the 72% boundary, critical
  continuation, disappearance of pressure-only evidence and independently held
  RAM/PSI causes. Missing PSI still cannot prove total pressure recovery.
- A continuous injected-clock **25-hour** incident serializes without old app
  names/footprints, start/upgrade measurements or thresholds. Stable ID/start/
  lastAt and all window overlaps survive; null/expired evidence survives reload.
  Query/save/adoption expiry and fresh upgrade/closure through `incident.record`
  pass. Recent upgrade payload has its own age boundary.
- Actual legacy/deferred production and deferred QA-wrapper discovery passes
  without a desktop. The review reproduction finds one parent/worker pair for
  production `--defer-history`, rather than zero. Three live restart checks
  remain GLHF gates.
- `git diff --check` passes. Logs/source snapshots and adapted reproductions
  are ignored under `.agent-artifacts/fix-h3-r2/`. The old reproduction's direct
  access to expired context `at` was adapted to inspect the explicit expired
  marker; the original review artifacts were preserved.

The first broader run caught sample-query boundary pruning and missing procps
in the slim container. Query expiry was narrowed to receipt payload so existing
sample boundary semantics remain intact; the final full run includes procps.
The new Qt fixture initially selected a different, newer row; a single-receipt
fixture now verifies the intended expired labels. The final results above are
from corrected source/tests, not those earlier failing attempts.

Remaining gates are unchanged: independent Astra xhigh verification; separate
GLHF notifications/actions/DND/settings/context/failures/ownership and three
restart checks; on-device performance; physical monitors and permission-gated
suspend; accessibility/themes; original real-widget captures/gallery; acceptance
and authorized merge. Runbook adds executable round-two checks and `pgrep`.
No GLHF access, deployment, merge, gallery update or Done action occurred.

## Fix round 1 — historical correction receipt

Runtime/test/QA-source commit: `0423fa9745ac2d3fbbfdf9fac48ce55d10eaa30b`.
The subsequent documentation commit changes guidance/evidence only. Full final
head and eight response links are recorded on the open PR; no thread is
resolved by the implementation agent.

- Five Node suites pass on macOS (Node 26).
- **82 Python tests pass in Linux Docker**, Python 3.12, including the protected
  process/identity/EOF suites. **14 focused incident/protocol tests pass natively
  on macOS**, using isolated state and synthetic input instead of Linux procfs.
- **46 Qt 6.8.2 offscreen checks pass at both 1x and 2x**, with the same pinned
  Omarchy UI and local effects doubles described below; no QML warnings, only
  the locale fallback notice. No desktop notification helper was executed.
- An additional macOS real-pipe integration starts the fixed critical QA source
  with an existing same-boot dispatch lock. Healthy warmup re-arms detection in
  that same process lifetime, then one start is acknowledged saved/notify=true
  and one extend shares its ID; no protocol errors, normal EOF exit. It checks
  the documented transition, not real desktop delivery or >60 s suppression.
  Pure detector coverage verifies sustained no-repeat past the cooldown.
- `git diff --check` passes. Scratch/logs remain under
  `.agent-artifacts/fix-h3-r1/`; automatic Qt stages remain ignored.

The new regressions map directly to
Astra round 1:

| Finding | Correction and actual regression |
| --- | --- |
| Buffered deferred summary starts dwell | Matching configuration ack and summary configuration ID gate production; Qt exercises buffered and old-setting generations, real pipes verify only post-config sequences are ingested. |
| No-PSI restart permanently blocks RAM | Suppression re-arms each dimension after measured hysteretic recovery; Node verifies low RAM/null PSI → later critical RAM, launch-high suppression and no fabricated PSI recovery. Python verifies same/new boot lock stamps. |
| Upgrade reuses old app snapshot | Separate current notificationContext preserves historical receipt context; Python tests closed/fresh/stale upgrades without scan, Node/Qt also recheck subscription/age when a queued helper starts. |
| Existing/other-panel receipt routing | Host-opened panel owns the ID; a new 24h query generation consumes it. Both review Qt scenarios pass, including another panel reply and stale target reply. |
| Active receipt disappears/prunes | Query uses lastAt; explicitly active lifecycle survives the 24h horizon with stable ID. Injected 2-second clock tests span all windows, count eviction, compaction, upgrade/recovery and closed expiry. |
| Session-only remote Clear | Erase-marker observation is independent of history save/load. Two actual synthetic pipe producers remove pre-clear readings/active receipt and emit remote invalidation; Qt rejects pending toast and waits for re-arming. |
| PSI hysteresis reports healthy RAM cause | Pending qualifying pressure reason survives its hysteresis band. Node tests PSI 6→3 at RAM 50, plus RAM-driven dwell with PSI that never breached. |
| Fixed QA scenes remain launch-blocked | Fixed source now warms up healthy then transitions in one lifetime; scene clock tests and real existing-lock pipe integration pass. Runbook requires the first visible acknowledged toast before no-repeat/capture checks. |

No code disagreement remains from implementation. Independent review, GLHF
notification/action/settings/context/physical checks, lifecycle/performance and
release captures remain required. H1/H2 stay Done; F0/History/H3/H-DOC stay
In progress. No GLHF access, merge, deployment or gallery update was performed.

## Initial implementation checks (historical, before round 1 fixes)

Implementation host: macOS Mini. Node v26.0.0. Linux tests: Docker
`python:3.12-slim` (Python 3.12), test-owned state only. Qt:
`raman-qt6:local`, Debian 13 ARM64, Qt 6.8.2, with separately fetched Omarchy
v4.0.4 `c668141e9c42b13c80c9ca4ea108e11708c5e8a5` UI and local process/IPC/window
doubles. No installed plugin/settings/history or GLHF session was changed.

- Five Node suites pass: Level, Runtime, PanelNav, HistoryModel and incidents.
  New cases cover dwell, short spikes, same-row critical upgrade, hysteresis,
  same-severity cooldown, no sustained repeat, unknown PSI versus known RAM
  breach, gaps/rollback/session change, restart block/recovery, disabled/demo/
  follower behavior, bounds and acknowledged toast measurements.
- **76 Python tests pass on Linux**, including eight focused incident tests and
  a real probe deferred-start/session-only protocol regression. Existing tests
  cover protected processes, start-time identity, EOF, history clocks/rings,
  bounded/corrupt files, clear and writer adoption. New tests cover checkpoint
  acknowledgement/failure, deduplicated reservation, extend without disk write,
  upgrade/recovery/long-gap closure, fresh/stale/absent snapshot, no scan/signal,
  session-only/disabled/explicit erase and dispatcher takeover/privacy.
- **41 offscreen Qt checks pass at 1x and 2x**, with no QML warnings in this Qt
  6.8.2 run. The runner emits a locale fallback notice. Regressions cover ack
  before notifier, duplicate/invalidated/expired/error replies, demo/owner/
  settings/clear invalidation, restart healthy gate, read-only IPC selection and
  alert-policy changes preserving an active receipt. Existing navigation,
  migration, subscription, narrow/short/theme/motion cases continue passing.
- Offscreen 420/340 layouts include updated upgrade/threshold receipts and long
  names; representative full-size critical and narrow captures were inspected.
  These are generated artifacts only, not live shell screenshots or marketing
  masters. H2's documented Qt 6.11 palette warnings remain a host-version limit.
- A 36-second **macOS synthetic pipe integration** used the tracked opt-in QA
  producer plus the real pure JS detector/history command handler: one start,
  one upgrade sharing one ID, both persisted acknowledgements, 11 extends,
  not-observed context, normal EOF exit. No desktop notifications were sent.
- Whitespace validation passes. The existing signal suites require Linux `/proc`;
  an early full-suite attempt on macOS hit those platform cases and the former
  zero-default assertions. The assertions were updated for intentional nulls;
  the complete suite's passing result is Linux, not macOS. Eight focused pure
  incident tests also pass natively on macOS.

Generated logs/fixtures/scripts remain ignored under `.agent-artifacts/h3/`
and `.agent-artifacts/qml-check.*/`. No upstream source/assets were vendored.
Existing Argus/Diego Peter inspiration and Omarchy notices remain accurate.

## Small local performance samples

Closed-panel 30-second runs used separate Linux containers on the same Mini,
H2 merge source `b47a5e5` and the H3 development tree. They include probe
startup/final flush/exit; benchmark instrumentation is outside the probe.
The runs overlapped local automated checks. Boundary timing admitted 15 versus
16 readings, so this is a rough local baseline, not a statistically significant
CPU result or on-device guarantee. Later changes concern incident reply text,
settings invalidation, receipt demos and regressions, not summary work.

| Measure | H2 | H3 |
| --- | ---: | ---: |
| Duration | 30.02 s | 30.21 s |
| Child CPU seconds | 0.13255 | 0.13805 |
| Worker peak RSS | 14,096 kB | 14,476 kB |
| Summaries | 15 | 16 |
| Mean arrival cadence | 2.0032 s | 2.0042 s |
| Stdout bytes | 3,142 | 7,174 |
| Closed-panel apps messages | 0 | 0 |

Extra stdout is the small producer session/clock/lease metadata. A separate
65.10-second H3 Linux run produced 33 summaries, zero app messages, 2.0074 s mean
cadence (1.9994–2.0151 s), 0.12933 child CPU seconds and 14,780 kB peak RSS.
One ordinary history replacement was observed at 60.31 s; final exit also
flushes by contract. No incident/notification checkpoints occur without the
QML producer in this standalone probe measurement. The synthetic command tests
prove extends do not checkpoint; live event/write-rate/CPU checks remain GLHF.

## Gates and policy risks

[Executable GLHF QA/capture runbook](glhf-h3.md) is the required fresh-agent
handoff. Pending: independent review, installed notification/action behavior,
live settings and context/no-hidden-scan evidence, persistence/helper failures,
restart/takeover/three lifecycle runs, two physical monitors and simultaneous
Memory panels, hardware suspend with explicit permission, on-device performance,
real screen reader and final original PNG masters/README derivatives. H2's
prior QA applies to H2 only; virtual-output evidence never proves physical
notification routing. Gallery unchanged; no captures have been invented.

Reviewer attention: same-boot restart/takeover suppression re-arms dimensions
independently after measured recovery; unavailable PSI never re-arms pressure,
but later RAM breaches can qualify after observed low RAM. Long active receipts
retain minimal stable identity/lifecycle while measurements/thresholds/context
expire after 24 hours; expired evidence is explicit, and recent upgrades have
an independent payload age boundary.
Dispatch metadata persists cooldowns
independently of session-only history; at-most-once reservation can lose delivery
on a crash; history and policy checkpoints are distinct acknowledgements.
[Incident design](../design/incidents.md) explains these choices and primary
host APIs. All tickets remain In progress; H1/H2 remain Done. No merge,
deployment or GLHF access was performed.
