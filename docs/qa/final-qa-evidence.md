# Native verification summary

Runtime tested: `6ebe9f05712def5964024f138ad5d2552567c606`.
Test-harness correction: `6f641978c9626b91ca4f00e9b40e577ddb6f3426`.
The correction queries the actual probe parent and worker in the complete Apps
inventory instead of assuming they rank in Memory's top 12. Runtime code was
unchanged. Later spelling corrections do not extend these results to new behavior.

Environment: Linux 7.2.5, Omarchy 4.0.4, Quickshell 0.3.1 and Qt 6.11.2;
Btrfs; Tokyo Night; native 160% scale, with a second physical display at 150%.

## Executed checks

- All eight Node suites passed.
- Corrected Linux Python suite: 254 tests, zero failures, three skips; both
  delegated-cgroup membership cases also passed separately on the native host.
- Qt offscreen: 183 tests passed at each of 1× and 2×. Offscreen results are
  not native rendering evidence.
- Three isolated restart-history checks passed, including old-probe cleanup.
- Observed installed four-page operation, closed-panel inactivity, one shared
  probe across physical displays and focused routing while the popup was closed.
- Monitor removal during Apps use and Storage scanning cleaned up correctly.
- Test-owned USB cached/active-scan removal, same-drive remount invalidation and
  rescan passed. Storage-idle suspend resumed with fresh capacity and a History gap.
- Four [native captures](../screenshots/final-qa/README.md) show synthetic
  Memory, History and Apps scenes and a test-owned Storage fixture.

## Findings and coverage limits

One initial Storage cached query took 102.18 ms against the unchanged 100 ms
criterion. The wide workload and three default-size retests passed; default
retests measured 38.35–46.34 ms. The original miss remains part of the result;
these timings are machine-specific, not a performance guarantee.

External-display cable reconnect failed with the plugin both enabled and
disabled. Suspend restored the link; cable-only recovery was not sustained.
The underlying display/driver/cable cause was not established.

Omarchy 4.0.4 coordinates one popup globally and reuses an already-open copy.
Simultaneous independent panels were not available on this host. Closed-panel
IPC selects the focused monitor; an existing popup may stay on its current output.

Coverage does not include the full theme/scale/layout/settings/interaction
matrix, other page or active-scan suspend cases, changed-filesystem substitution,
extended Apps performance tests, or complete real-GPU attribution/no-wake/cadence
verification. Intel xe was detected and some live readings observed; synthetic
GPU captures do not establish hardware coverage. The four captures are a
representative gallery, not exhaustive state coverage.

The [runbook](final-qa.md) contains repeatable procedures for these areas.
Detailed session records are kept privately; this summary contains the public
results and limits needed to interpret the implementation.
