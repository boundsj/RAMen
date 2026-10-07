# History: design and verification

The user opens RAMen after a hitch, when the current reading has recovered.
History should show what was measured then, where observation stopped, and
what context was actually saved. H2 displays H1's store; H3 adds the
[live incident producer](incidents.md) without making receipts actionable.

## Composition and interaction

The design working name is **Simmer history**; the product page is **History**.
Final narrow composition, within the existing 420 logical-pixel panel:

```text
Memory  [History]
History                 [5 min] [1 hour] [24 hours]
  bowl rim (decoration)
  recorded pressure episodes (static steam markers)
MEMORY PRESSURE                            scale %
  mean / range     blank gap               | cursor
RAM USED                                scale GiB
SWAP USED                               scale GiB
             one aligned local-time axis
cursor time, bucket/readings count, values and ranges
INCIDENTS
  read-only receipts -> inline inspect -> Back / Esc
```

A wider 600-pixel screen-fitted proposal keeps the same hierarchy rather than
adding unrelated tiles or another scale:

```text
             pressure / RAM / swap + cursor
             incident list / inline receipt
```

H2 retains the established 420-pixel content width, fitted by the host to the
screen. At narrower widths, facts wrap, app names elide with a full-name hover
hint and accessible name, and taller content scrolls. Inspection stays inline
so it works without a second column. State notes remain visible before charts.

Page buttons use h/l only while their region has focus. Time-window controls
use h/l while focused; charts use h/l to scrub, H/L to move twelve points.
j/k visits regions or incident rows. Enter inspects; Esc returns, then closes.
Tab/Shift+Tab remains the host's bar-panel navigation. History has no signal
action; PanelNav's exhaustive safety test and the panel guard preserve that
boundary. Memory keeps its protected-process checks and two-step timeout.

## Data truth and failure states

- Pressure, RAM and swap have separate labelled scales. Pressure is PSI some
  avg10 (%); RAM and swap are kB formatted with binary K/M/G units.
- The 5m view draws readings; 1h and 24h draw time-weighted means with min/max
  envelopes. A shared cursor reads the same stored point across all tracks.
- Nulls break a metric's line. Continuity breaks and absent time split runs.
  Recorded gaps are blank with dashed edges and named reasons; isolated points
  stay visible. No curve smoothing or gap interpolation manufactures values.
  Aggregate centers inside a gap are omitted, including cursor data markers;
  joins through explicit gaps are split even when the gap is inside a bucket.
  Cursor readouts still expose that bucket's stored aggregate and explain why
  its plotted center was omitted.
- Pressure references are **current settings**; no historical threshold is
  inferred. Receipts explicitly say when a threshold was not stored.
- Receipt extrema come from stored readings in the current window. Aggregated
  buckets can overlap an incident's boundaries and include readings outside it;
  the UI says so. Missing PSI/app observations stay unavailable, not healthy.
- App observation times are shown relative to the start. Observed apps are
  context, never a causal explanation or a process identity usable for killing.
- Loading, empty, partial coverage, unavailable metrics, persistence warnings,
  query errors and a five-second reply timeout are distinct. Late generations
  and duplicate replies are rejected. Windows are independent between panels.
  Migrating from a private runtime to the shared service invalidates old
  requests, reserves a new client ID and immediately queries the view's window.
- H3 records live sustained incidents; `demo history` supplies synthetic
  receipts only in its isolated in-memory store and never sends notifications.

## Ownership, accessibility and original work

Omarchy v4.0.4's documented service kind is a headless singleton mounted by
`shell.qml`'s `ensureService`, destroyed by `unloadPluginServices` during
reload. Widgets reach it through `bar.shell.serviceFor(ownId)`. Service IPC
uses the scoped host's summon/hide/toggle, which routes to the focused monitor.
RAMen retains the public hostWidget facade and private-runtime fallback.
Each visible panel owns a subscription key; the runtime combines requests.
The unchanged Python lock and waiting-parent/worker lifecycle remain the
persistence backstop. H3's notification dispatcher uses this shared runtime.

Theme foreground/background and Level.palette supply colors; warning/critical
also use outlined/solid episode shapes and named severities. Focus has an
outline. Labels and names use plain text. History never animates data or
markers; the animations setting disables existing RAMen transitions, and the
shell's reduced-motion token takes precedence when available. The v4.0.4
Style source has no such token, hence the local off option.

Argus's retained history and contextual observations informed the problem.
The implementation was independently authored. Compared with the reference:

1. Pressure episodes lead to inline receipts, rather than equal-weight metric
   tiles or Argus's multi-tab arrangement.
2. Focus regions coexist with Omarchy bar-panel Tab navigation; historical
   evidence cannot inherit live process actions.
3. Explicit nulls, downtime, current-only thresholds and overlapping-bucket
   limits expose observation boundaries instead of suggesting a complete record.
4. Locally authored bowl/steam paths connect the view to RAMen's own artwork;
   no upstream captions, screenshots, icons or animations are reused.

The reference was inspected at `b830cf5688c43d55cd7b4dcf62fe073b4f54bb2d`.
The host API was inspected at Omarchy v4.0.4
`c668141e9c42b13c80c9ca4ea108e11708c5e8a5`. See [credits](../../CREDITS.md)
and [notices](../../THIRD_PARTY_NOTICES.md).

## Actual verification and remaining gates

Implementation host: macOS Mini. Linux `/proc` checks run in Docker; QML tests
run in Qt 6 with test doubles for processes, IPC and the layer-shell window.
This harness exercises the real PanelKeyCatcher and RAMen components, using
separately fetched Omarchy UI dependencies. It never changes the installed
plugin, production settings/history, live shell or desktop.

Four JavaScript suites and 67 Python tests pass. The 36 offscreen QML checks cover
shared ownership, multiple panel subscriptions, window generations, close and
reopen, clear/error, real key events, read-only inspection, window independence,
runtime migration, short-screen chart focus, a 30-row receipt list, and local
animation off. Captures are generated at 420 and 340 logical pixels,
dark/light, long names, and 1x/2x device scale. Artifacts stay ignored.

Closed-panel probe measurements used two Linux containers on the Mini, the
same idle workload, separate test-owned state directories and 30-second runs.
CPU includes process startup and final save/exit; RSS is the worker's highest
observed resident memory. These small samples establish a local baseline, not
an on-device performance guarantee or a statistically significant CPU change.

| Measure | Merged H1 (`3342a5e`) | H2 |
| --- | ---: | ---: |
| CPU seconds | 0.0479 | 0.0493 |
| CPU share of one core | 0.160% | 0.164% |
| Worker peak RSS (kB) | 13,624 | 14,020 |
| Summaries / 30 seconds | 15 | 15 |
| Mean summary cadence | 2.0032 s | 2.0031 s |
| Stdout bytes | 3,139 | 3,143 |
| App messages while closed | 0 | 0 |

The probe lifecycle/persistence code is unchanged by H2. The history-command
regression explicitly forbids process scans and signals; real on-device
cadence, ownership and restart tests remain required.

H2 subsequently passed live Omarchy QA
at `6245c9bea7fc1a2d8af7039543e0dcb1b54adf1f` and merged as `b47a5e5`.
That QA covered live navigation/focus/history/demo isolation, all three separate
restart-history checks and virtual-output widget lifecycle 1→2→1. It restored
the original plugin/settings/workspace/scale and live readings. Versions:
Omarchy 4.0.4-1, Hyprland 0.56.2, Quickshell 0.3.1, Qt 6.11.2, native 1.6x.
Qt 6.11 emitted nonblocking palette-property naming warnings; earlier warning-free
Qt 6.8 results do not extend to it. Local H2 captures were not promoted to the
tracked gallery.

Physical monitors/unplug, simultaneous visible Memory panels, hardware suspend,
real screen reader and a desktop theme switch remained untested. Virtual outputs
do not prove physical checks. H3 notification ownership/actions/incident writes
are not established by H2 QA or demo receipts. H3 independent review found no remaining actionable product findings.
The [GLHF report](../qa/h3-omarchy-evidence.md) records completed live checks,
measurements and original captures, plus explicit remaining verification limits.
The [executable runbook](../qa/glhf-h3.md) retains the full QA procedure. Do not suspend hardware without Jesse's
permission. Preserve and restore original installation/settings/history; local
fixtures/containers are never reported as live Omarchy evidence.
