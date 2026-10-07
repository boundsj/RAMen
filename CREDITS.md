# Credits

## Memory History (H1/H2)

- **Project / author:** Argus, Diego Peter.
- **Plugin ID:** `io.github.diegopluna.argus`.
- **Marketplace:** [Argus](https://plugins.omarchy.org/plugin.html?id=io.github.diegopluna.argus).
- **Repository:** [diegopluna/omarchy-argus](https://github.com/diegopluna/omarchy-argus).
- **Inspected revision:** `b830cf5688c43d55cd7b4dcf62fe073b4f54bb2d`,
  2026-10-03/04. Its LICENSE says MIT, copyright 2026 Diego Peter.
- **Influence:** retained resource history and contextual observations; its
  sampler ownership mechanism also prompted checking Omarchy's supported
  shared-owner APIs. RAMen uses Omarchy's service plugin kind instead.
- **Relationship:** design inspiration. No Argus code, assets, captions,
  screenshots or animations are incorporated. RAMen's implementation, tests,
  synthetic data, steam marker and bowl-rim curve were authored locally.
- **Local work:** `raman_history.py`, `HistoryModel.js`, `HistoryView.qml`,
  `RamanRuntime.qml`, `Runtime.js`, `Service.qml`, `PanelNav.js`, their integration,
  tests and documentation.
- **Original distinctions:** pressure episodes lead to receipts rather than
  equal-weight metric tiles; focus regions preserve Omarchy's bar-panel Tab
  behavior; missing observations, current thresholds and overlapping bucket
  extrema are labelled explicitly. See [design notes](docs/design/history.md).

Wait State and System Graphs appear in the roadmap's reference list, but their
material was not consulted or incorporated for this implementation. Apps
influences are credited below.

## Storage (S1 / S2 / S3)

- **Disk Lens / Maarten Tolhuijs.**
  - **Plugin ID:** `io.github.mtolhuys.disk-lens`.
  - **Marketplace:** [Disk Lens](https://plugins.omarchy.org/plugin.html?id=io.github.mtolhuys.disk-lens).
  - **Repository:** [mtolhuys/omarchy-disk-lens](https://github.com/mtolhuys/omarchy-disk-lens).
  - **Reviewed revision:** `7f0ea22aee80a4f7f23ade4dede500f61eedba75`,
    2026-10-03. Its LICENSE says MIT, copyright 2026 Maarten Tolhuijs.
  - **Influence:** user-triggered scans and linked map/list exploration.
- **Omadisk / Kennet Postigo.**
  - **Plugin ID:** `postman.omadisk`.
  - **Marketplace:** [Omadisk](https://plugins.omarchy.org/plugin.html?id=postman.omadisk).
  - **Repository:** [kennetpostigo/omadisk](https://github.com/kennetpostigo/omadisk).
  - **Reviewed revision:** `bcae90c75bf64b56f2919305fcbf5bd3f5b70935`,
    2026-10-03. Its LICENSE says MIT, copyright 2026 Kennet Postigo.
  - **Influence:** navigable hierarchy and synchronized selection.
- **Relationship:** design inspiration only. Both projects were studied for
  behaviour; no upstream code, assets, captions, screenshots or layouts are
  incorporated.
- **Local work:** `raman_storage.py`, `raman_portal.py`, the Storage parts of
  `raman_probe.py`, `StorageModel.js`, `StorageView.qml`, Storage routing in
  `PanelNav.js`, `scripts/storage-demo.py`, `scripts/check-storage.py`, their
  tests, synthetic fixtures (Serving Board, Empty Serving Board, Cancellation
  Kitchen) and documentation. All were authored for RAMen.
- **Original distinctions:** nested fixed-height horizontal bands in one frame,
  where only width encodes size, instead of a treemap mosaic or sunburst; a
  filesystem capacity strip kept separate from scoped allocation; explicit
  directory self-allocation, partial lower bounds and real-member Other; a
  read-only, no-delete action set with backend-validated paths; and ledger-first
  keyboard access. See the
  [design comparison](docs/design/storage.md#original-design-and-influences).
- **Not credited as influences:** OmaTree (`io.github.camstraps.omatree`) and
  Disks (`athesias.disks`) appear in the roadmap's reference list, but their
  materials were not consulted or incorporated for S1–S3.

## Apps (A1 backend, A2 page, A3 GPU attribution, A-DOC)

- **SysMon / Greg DeYoung.**
  - **Plugin ID:** `gdeyoung.sysmon`.
  - **Marketplace:** [SysMon](https://plugins.omarchy.org/plugin.html?id=gdeyoung.sysmon).
  - **Repository:** [gdeyoung/omarchy-sysmon](https://github.com/gdeyoung/omarchy-sysmon).
  - **Revision reviewed by the roadmap research:** `95743fdacd925bbd9b0af7b6a5ea960943b40a52`,
    2026-10-03 (README, `DetailPopup.qml`, `procprobe.sh`); its LICENSE says
    MIT, copyright 2026 Greg DeYoung.
  - **Influence:** its Processes tab switches one process list between CPU
    and memory ordering. That became the Apps page's Memory/CPU lens, applied
    to RAMen's existing app groups rather than to processes.
  - **Relationship:** inspiration, not adaptation. The A1 and A2 implementers
    did not consult SysMon's code, assets, layout, captions or screenshots;
    nothing from it is incorporated. RAMen's CPU is its own recent-interval
    rate (counter deltas keyed by PID and start time), not `ps`'s `%CPU`.
- **User Services / Gabriel da Silva.**
  - **Plugin ID:** `io.github.gabepsilva.user-services`.
  - **Marketplace:** [User Services](https://plugins.omarchy.org/plugin.html?id=io.github.gabepsilva.user-services).
  - **Repository:** [gabepsilva/omarchy-user-services](https://github.com/gabepsilva/omarchy-user-services).
  - **Revision reviewed by the roadmap research:** `cb925915f08e0049dc31b35219542dc6c5754fcf`,
    2026-10-03 (README, `vram.sh`, `Panel.qml`); its LICENSE says MIT,
    copyright 2026 Gabriel da Silva.
  - **Influence:** it shows the video memory each user service holds, so
    VRAM is attributed to a workload people recognise rather than to a PID.
    That became A3's per-app VRAM over RAMen's app scopes and terminal
    sessions.
  - **Relationship:** inspiration, not adaptation. The A3 implementer did not
    consult User Services' code, assets, layout, captions or screenshots;
    nothing from it is incorporated, and nothing from it is used by A1 or A2.
    RAMen's adapter makes its own choices: resident VRAM for its lens with
    allocated beside it, clients deduplicated by device and client ID across
    processes, cross-group clients kept on the device, shared-buffer sums
    marked as bounds, sleeping GPUs left unread, and no `nvidia-smi` or
    service controls.
- **A-DOC's own use of these sources:** to write the comparison in the
  [Apps design](docs/design/apps.md#influences-and-comparison), A-DOC read
  both READMEs and SysMon's `DetailPopup.qml`/`procprobe.sh` and User
  Services' `vram.sh` at those revisions (fetched 2026-10-06; byte-identical
  to the roadmap's saved copies), and rechecked both LICENSE headers. Nothing
  was copied, and no screenshot of either project is published.
- **Argus** (`io.github.diegopluna.argus`) was the roadmap's optional
  reference for capability-aware GPU process data. Its GPU materials were not
  consulted for Apps, so it is not credited as an Apps influence (it remains
  History's influence above). **Memory Usage** (`dev.egoist.memory-usage`) was
  listed as an optional later inspiration and was not used.
- **Linux kernel documentation and driver sources** (GPL-2.0 / MIT per file)
  are A3's technical sources, read at Linux `master`
  `69f80fef3153299d9c72c53d1d71eef6354b6926` (2026-10-06):
  `Documentation/gpu/drm-usage-stats.rst`, the i915 and xe usage-stats
  documentation, and `drm_file.c`, `amdgpu_fdinfo.c`, `amdgpu_vram_mgr.c`,
  `i915_drm_client.c`, `intel_memory_region.c`, `xe_drm_client.c`. RAMen
  implements the documented interface; no kernel text or code is copied, and
  the test fixtures use the documented key names with RAMen's own values.
  `proc(5)` and the kernel's procfs documentation are A1's sources for CPU and
  memory counters.
- **Local work:** `raman_apps.py`, `raman_gpu.py`, the Apps parts of `raman_probe.py`
  (CPU sampling, GPU sampling, inventory, `apps.query`, `apps.kill`, `apps.details`),
  `AppsModel.js`, `AppsView.qml`, Apps routing in `PanelNav.js`, the Apps
  helpers in `Runtime.js`/`RamanRuntime.qml`, the synthetic `apps` demo scene
  (its noodle-kitchen names, fake members and command lines, and its
  synthetic GPU devices and readings), their tests and documentation. All
  were authored for RAMen.
- **Original distinctions:** RAMen's app-scope/terminal-session groups instead
  of per-process or per-service rows; separate labelled footprint,
  resident-RAM and CPU measures with explicit coverage and no combined score;
  paired rails in RAMen's chopstick rhythm; a pointer-stable list and a
  two-step kill bound to the armed group's exact membership; per-device VRAM
  with explicit resident/allocated/system semantics, cross-group GPU clients
  reported once on the device, engine shares kept separate, and sleeping GPUs
  left asleep. See the [Apps design](docs/design/apps.md#original-design).
- **Screenshots:** the [native final-QA gallery](docs/screenshots/final-qa/README.md)
  includes the real Apps widget playing the synthetic `apps` scene, alongside
  Memory, History and an original Storage fixture. [Masters and provenance](docs/media/masters/final-qa/provenance.json)
  record the tested revision, capture method and hashes. The remaining [offscreen previews](docs/previews/README.md)
  retain that label. The native gallery is representative, not exhaustive.

## Visual identity and artwork (neon design)

- **Authorship:** the ramen-bowl mark and gauge, icon set, neon "RAMen"
  tube lettering, the ラーメン sign, the logo, banner and social preview, and
  `scripts/build-art.py` / `scripts/render-art.py` were authored for RAMen.
  Neon signage, rain, a perspective grid and a night food stall are generic
  motifs; no film, game, poster, logo or other artwork was traced, sampled,
  recoloured or used as a reference image, and no raster image generator was
  used. See [visual identity](docs/design/visual-identity.md) and
  [art provenance](docs/art/README.md).
- **Information-design references:** Edward Tufte's *The Visual Display of
  Quantitative Information* (the Lie Factor, data ink) and *Beautiful
  Evidence* (word-sized graphics), and Cleveland and McGill's
  graphical-perception results, informed the gauge and labelling choices;
  no text or figure from them is reproduced.
- **Font used for rendering only:** the taglines in the checked-in PNG
  artwork and the offscreen previews were rasterised with JetBrainsMono Nerd
  Font v3.4.0 (JetBrains Mono, SIL Open Font License 1.1). No font file is
  tracked; the widget uses whatever font the Omarchy shell is set to.

## Host platform and test dependencies

[Omarchy](https://github.com/basecamp/omarchy), led by David Heinemeier Hansson,
supplies RAMen's UI components, theme tokens and service/IPC and notification-helper APIs. Those APIs
were verified against v4.0.4 (`c668141e9c42b13c80c9ca4ea108e11708c5e8a5`).
The offscreen harness uses separately installed/fetched Omarchy UI files;
they are staged under `.agent-artifacts/`, with the upstream license, and
are not vendored in tracked source. Test doubles in `tests/qml/stubs/` are
locally authored substitutes. Qt and Quickshell supply the rendering/runtime.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency provenance
and [docs/art/README.md](docs/art/README.md) for artwork.
