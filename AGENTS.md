# Working on RAMen

RAMen is an Omarchy/Quickshell bar widget for memory usage and per-app process
management. Its plugin ID and IPC target are `boundsj.raman`. Read `README.md`
for user-facing behavior, installation, settings, and keyboard controls.

The name is RAMen (RAM + ramen) in prose, UI text and artwork. The lowercase
`raman` in the plugin ID, `raman_*` modules, `Raman*` QML types, `RAMAN_*`
variables and state/cache paths is a compatibility name; leave it as is.
`tests/test_brand.py` checks that the drawn wordmark spells the manifest name
and that no file reintroduces the earlier "RAMan" spelling.

## Agent workspace

Use the repository-root `.agent-artifacts/` directory for all temporary task
work: plans, notes, research, scratch scripts, prototypes, logs, test output,
screenshots, illustrations, diagrams, and other generated working files.
Create it as needed with `mkdir -p .agent-artifacts`, and use task-specific
subdirectories when useful. This directory is ignored by Git; do not force-add
its contents. Keep material needed to understand a committed change in the
appropriate source, test, or documentation file. Promote an artifact into
`docs/` or another tracked location only when it is an intended deliverable.

## Code map

- `manifest.json`: plugin metadata, entry points (`barWidget` and `service`),
  and bar settings schema.
- `Service.qml`: Omarchy `service` entry point. The shell mounts one per
  session, so every monitor's widget shares its `RamanRuntime`; panel IPC goes
  through the host's summon/hide/toggle (focused monitor).
- `RamanRuntime.qml`: the probe lifecycle, latest data snapshots, level, theme
  colors, kill bookkeeping, keyed data subscriptions, history and apps-query
  requests, and the IPC target. `Runtime.js` holds its pure subscription/request helpers.
- `BarWidget.qml`: the bowl gauge and label, settings, and panel wiring. It uses the
  service's runtime (or a private one when the bar offers no service) and
  keeps the `hostWidget` facade that `Panel.qml` reads.
- `Panel.qml`: page buttons (Memory / History / Storage / Apps), focus regions, per-panel data
  subscriptions, memory details, app rows, and the two-step kill
  interaction. It gets data and sends commands through `hostWidget`.
  `PanelNav.js` routes keys by page and focused region; only the Memory and
  Apps app lists can reach a kill path.
- `AppsView.qml` and `AppsModel.js`: the Apps page: grouped ledger with
  separate RAM/CPU rails, search/sort/paging over `apps.query`, on-demand
  details, ID-based selection, pointer/armed freeze with queued updates, and
  the two-step kill sent as `apps.kill` with the armed membership generation.
  `AppsModel.js` is pure (Node-tested); the view only renders and wires.
- `HistoryView.qml`: the read-only History page.
- `StorageView.qml` and `StorageModel.js`: explicit Storage scan/cache browsing,
  keyed generation/reply handling, original proportional bands and the ledger.
  Open/copy use the backend validated action contract, never display labels.
- `HistoryModel.js`: pure chart geometry, labelled scales, cursor readouts,
  coverage states/read-only receipts and the live incident dwell/cooldown state machine.
- `Level.js`: pure helpers for thresholds, hysteresis, formatting, and theme
  color selection, shared by both QML components.
- `Design.js` and `Icons.js`: pure presentation helpers. `Design.js` makes the
  theme's semantic colours legible on the actual surface, tints composited
  (4.5:1 text, 3:1 marks; hue unchanged), packs threshold labels, hides Shapes
  scrolled wholly out of a view, and maps a reading to the bowl's broth level;
  `Icons.js` is the single source of the bowl mark and the vector icon paths.
- `RamanBowl.qml`, `RamanIcon.qml`, `RamanIconButton.qml`, `RamanChip.qml`,
  `RamanMeter.qml`, `RamanButton.qml`: shared visual components (bowl gauge,
  Shapes icons, row action, choice chip, one-denominator meter, command
  button) used by every page. `docs/design/visual-identity.md` explains them.
- `raman_probe.py`: long-lived Python helper that reads Linux `/proc` and zram
  stats, groups processes, handles signals, and serves demo scenes.
- `raman_apps.py`: stdlib, no `/proc` access: recent CPU baselines keyed by
  (pid, start ticks), the retained app inventory snapshots and `apps.query`
  validation/paging, plus `apps.kill`/`apps.details` validation and bounded
  details replies. The probe's one detail scan feeds it; Memory and Apps
  share that scan. At an Apps kill confirmation the probe rebuilds the group's
  membership from `/proc` (stat and cgroup only) and refuses a changed or
  unverifiable group, then re-checks each PID (pidfd, start time, owner,
  protection) right before its own signal. `docs/design/apps.md` records A1/A2.
- `raman_history.py`: stdlib history rings, continuity gaps, single-writer
  persistence under `XDG_STATE_HOME/raman`, and clear; no `/proc` access, so
  it imports and tests on any platform. `docs/protocol.md` documents the
  probe's stdin/stdout protocol, including the JSON history commands.
- `raman_gpu.py`: GPU attribution from the kernel's DRM client usage stats
  (`/proc/PID/fdinfo` `drm-*` keys): device identity through
  `/sys/class/drm` and descriptor `st_rdev`, client dedupe on
  (device, `drm-client-id`), cross-group clients counted once on the device,
  VRAM vs system-RAM regions and resident vs allocated, per-engine deltas with
  capacity, client sums marked `possible` overlap when shared buffers may
  repeat (fdinfo has no buffer IDs), and `GpuSampler` (one worker thread,
  generation-guarded, starts at least 5 s apart even across reopen, freshness
  from when a sample began reading). It checks `power/runtime_status` before each client read and
  never reads a sleeping device. Sampling runs only while the probe has both
  `detail 1` and `gpu 1` (a visible Apps page with `gpuMetrics` `auto`), never
  in demos. No vendor tool; `docs/design/apps.md` records A3's sources.
- `raman_storage.py`: explicit Linux capacity discovery, no-follow allocated
  traversal, private validated SQLite snapshots and paged queries in owned
  workers. The probe's controller handles client generations/cancel/leave/EOF;
  the Storage page consumes its bounded queries and validated path actions.
- `tests/test_level.js`, `tests/test_design.js`, `tests/test_runtime.js`, `tests/test_panel_nav.js`,
  `tests/test_history_model.js`, `tests/test_incidents.js`, `tests/test_apps_model.js`,
  `tests/test_incidents.py`, `tests/test_probe.py`, `tests/test_apps.py`,
  `tests/test_apps_actions.py`, `tests/test_gpu.py` and `tests/test_history.py`: JavaScript helper tests (`tests/load_js.js` loads a
  `.pragma library` file under Node); Python parsing, grouping,
  process-safety, protocol, and demo tests; and clock-injected history tests.
- `docs/demo-scenes.json`: canned green/yellow/red/history scenes and the
  synthetic `apps` scene (CPU readings, coverage states, fake member details,
  synthetic GPU devices/readings shown only while GPU metrics are wanted).
  `docs/art/` holds the generated SVG artwork and rendered PNGs
  (`scripts/build-art.py`, `scripts/render-art.py`; edit the script, not the
  SVGs); `docs/previews/` holds Qt offscreen renders from
  `tests/qml/tst_gallery.qml` (never call them native screenshots);
  `docs/screenshots/` holds H3's native captures of the earlier design.
- `scripts/check-restart-history.py`: live Omarchy check that history from just
  before `omarchy restart shell` is saved and no old probe lingers.
- `tests/test_storage.py` and `scripts/check-storage.py`: Linux/test-owned
  allocation, cache, bounded protocol and owned-worker lifecycle checks. The
  executable checker puts all fixtures/logs under `.agent-artifacts`.
- `scripts/storage-demo.py` and `tests/test_storage_demo.py`: original
  synthetic Storage fixture trees (Linux, beneath `.agent-artifacts` only);
  Storage has no IPC demo scene.
- `docs/qa/`: per-milestone runbooks and evidence (historical), and
  `final-qa.md`, the consolidated pre-release hardware/visual/gallery pass
  (RAMEN-17) that later phases append to.
- `scripts/check-qml.sh` and `tests/qml/`: optional Qt 6.8+ offscreen checks with
  separately supplied Omarchy UI and locally authored process/IPC/window test
  doubles. Fixtures and captures are generated in `.agent-artifacts/`.
  `tst_design.qml` covers the visual layer's meaning (gauge, contrast on tinted
  surfaces at rest and under the pointer, no icon font glyphs, kill through the
  vector buttons, threshold labels, scrolled-out Shapes); `tst_gallery.qml`
  renders named states of every page into `shots/gallery/` (animations off)
  and fails on text that runs outside the panel, Shapes painting outside their
  clip, or semantic text under 4.5:1. Capture with `grabToImage` (true device
  pixels), not `grabImage`, which crops at 2x.
- `scripts/screenshots.sh`: regenerates documentation screenshots through the
  live widget; see the manual checks below before running it.

## Implementation guidance

- Follow the existing style: two-space indentation in QML/JavaScript and four
  spaces in Python. Runtime Python and the tests use only standard libraries;
  there is no package-manager install or build step for the current project.
- Keep `/proc` access and signalling in the Python probe, UI state and
  interaction in QML, and reusable calculations in `Level.js` (presentation
  colour and geometry in `Design.js`/`Icons.js`). Keep these JS files free of
  QML types so the Node tests can evaluate them after stripping
  `.pragma library`.
- Draw icons with `RamanIcon` from `Icons.js`; never rely on a Nerd Font
  glyph for meaning. Semantic text and marks use the panel's `ink`/`tones`
  (contrast-safe theme colours), never raw palette colours on the card; text
  on a semantic tint derives its colour against that tint over its surface
  (`confirmInk`, `badgeInk`). The theme accent is the only decorative colour;
  no glow, gradient or 3D effect on a measured length.
- Qt's software scene graph (the offscreen harness) paints a Shape that lies
  wholly outside its clip unclipped. A raw `Shape` inside a Flickable or
  ListView must hide while `Design.inViewport` is false (`RamanIcon` already
  does); layers are not a fix, they render wrongly there at 2x.
- The probe accepts newline-delimited commands on stdin and emits one JSON
  object per stdout line (`summary`, `apps`, `killed`, and replies to JSON
  commands). Keep diagnostics on stderr. Coordinate protocol changes with
  `BarWidget.qml`, `docs/protocol.md`, and relevant tests.
- Tests that start the probe must point `XDG_STATE_HOME` at a test-owned
  directory so they never touch real history.
- Memory fields are in kB. App footprints use PSS plus swap; preserve grouping
  by systemd app scope and the separate groups for terminal sessions.
- Keep detailed process scans conditional on the panel being open, and keep
  the probe exiting when stdin closes. Avoid adding work to the frequent
  summary loop that belongs in the more expensive detail scan.
- Preserve protected-process checks, PID start-time validation before signals,
  the two-step kill confirmation, and its timeout. Kill keys and buttons
  belong to the Memory and Apps app lists only (not Apps details); read-only
  pages must never reach them. Apps kills must carry the armed membership
  generation, and the probe must refuse a changed group. Demo kills must only
  remove fake rows. Use demo scenes or test-owned child processes to verify kills.
- Full command lines are shown only on an explicit `apps.details` request
  (re-read then, bounded) and never stored or persisted. The detail scan reads
  `cmdline` only to choose display names and must keep no argument list.
- GPU work stays off the summary/IPC path and off closed, Memory-only and
  `gpuMetrics: off` panels; never read `fdinfo` of a runtime-suspended device,
  never add GPU memory to RAM figures, never show a possibly overlapping
  client sum as exact or as physical occupancy, and never combine engines into one
  GPU percentage. Fixtures for GPU tests live in temporary sysfs/procfs trees;
  real GPU hardware checks belong to `docs/qa/final-qa.md`.
- Preserve threshold hysteresis and theme hue checks. Keep setting names and
  defaults aligned across `manifest.json`, `BarWidget.qml`, `Level.js` (or
  `AppsModel.js` for `appsDefaultSort` and `gpuMetrics`), and `README.md` when changing them.

## Validation

Run the relevant suites from the repository root; run both for changes that
span the probe and UI helpers:

```bash
node tests/test_level.js && node tests/test_design.js && node tests/test_runtime.js && node tests/test_panel_nav.js && node tests/test_history_model.js && node tests/test_incidents.js && node tests/test_storage_model.js && node tests/test_apps_model.js
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

The tests need Node.js and Python 3. The Python suite also needs Linux `/proc`
and the `sleep` command; it spawns its own child process for signal tests.
Add focused regression coverage for changed behavior. These suites do not
render QML, so UI changes also need a check in a running Omarchy shell.

## Manual UI and documentation checks

The QML imports `qs.Commons` and `qs.Ui` come from the Omarchy shell. Follow the
README's development setup when a live widget is needed. Useful IPC commands
for an installed widget are:

```bash
omarchy-shell boundsj.raman toggle
omarchy-shell boundsj.raman refresh
omarchy-shell boundsj.raman status
omarchy-shell boundsj.raman demo red
omarchy-shell boundsj.raman demo off
```

Use `green`, `yellow`, and `red` demo scenes to check colors and interactions;
restore live data with `demo off` afterward. If edits to `Panel.qml` seem to
have no effect, hot reload may be serving a cached component; the README's
remedy is `omarchy restart shell`.

The probe runs as a waiting parent plus a worker. Quickshell SIGKILLs the
process it started on restart, and the worker still saves history. After
changing probe lifecycle or persistence, run `scripts/check-restart-history.py`
in a live session. It restarts the shell once, only reads RAMen's state, and
prints PASS or FAIL.

`scripts/screenshots.sh` writes tracked images in `docs/screenshots/`, switches
to an empty workspace (9 by default, configurable with `RAMAN_SHOT_WORKSPACE`),
and restores the previous workspace and live data on exit. It needs a running
Hyprland/Omarchy session plus `grim`, ImageMagick (`magick`), `jq`, and `wtype`.
Run it when intentionally updating documentation screenshots. Keep exploratory
captures and illustration drafts under `.agent-artifacts/`.
