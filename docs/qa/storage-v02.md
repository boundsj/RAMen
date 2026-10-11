# Storage 0.2 verification

RAMen 0.2 replaces the initial all-or-nothing Storage scan with explicit,
progressive inventories. This record supplements the accepted 0.1 hardware
QA; it does not reopen that ticket or convert its waived checks into passes.

## Automated checks and independent review

Runtime source for the native gallery: `a26b5cb34108e3ae83f1dba9d816d56397eb18d3`.
The captures use that runtime with the manifest prepared for 0.2.0. The final
release also updates documentation and screenshots.

- Eight Node suites passed: level, design, runtime, panel navigation, history,
  incidents, StorageModel and AppsModel.
- The Linux Python suite ran **282 tests: OK, 7 skipped**. The skip count is
  retained; root/container-dependent process, permission and descriptor checks
  are not claimed as native hardware coverage.
- Independent backend review at `ef10d99` ran all **82 Storage tests**, including
  **19 progressive job tests**, and additional mutation, retained-member and
  validated-action probes. It found no remaining actionable defects after two
  fix cycles.
- Qt 6.8.2 offscreen verification ran **215 checks: 0 failures** after the UI
  fixes. These use separately supplied Omarchy UI modules and local test doubles;
  they are not native screenshots. `tst_storage_progress.qml` and
  `tst_storage_recovery.qml` and `tst_storage_status.qml` retain the independently
  discovered race and recovery cases.
- The UI was independently reviewed with Astra at max reasoning and Fast
  service. Findings covered scope/checkpoint pairing, obsolete replies, visible
  first-start errors, Main storage legacy fallback, final refresh after errors,
  restoration, remount invalidation and retained-member pagination.

Relevant commands, from the repository root:

```sh
node tests/test_level.js && node tests/test_design.js && node tests/test_runtime.js && node tests/test_panel_nav.js && node tests/test_history_model.js && node tests/test_incidents.js && node tests/test_storage_model.js && node tests/test_apps_model.js
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
RAMAN_QML_SHELL=/path/to/omarchy/shell sh scripts/check-qml.sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage-jobs.py --files 60000
```

The Python and progressive fixture checks need Linux; the Qt recipe needs the
documented developer dependencies. Temporary data belongs in `.agent-artifacts`.
The job checker creates two test-owned inventories and removes its fixture on exit.

## Native Try Omarchy checks

The official [Try Omarchy](https://github.com/omacom/try-omarchy) 0.5.1 desktop
VM ran on Apple Silicon with four virtual CPUs, 4 GiB RAM and a 24 GiB virtual
disk. Guest: Linux `7.2.8-1-aarch64-ARCH`, ext4, Omarchy runtime `4.0.4-1`,
Quickshell `0.3.1`, Qt `6.11.2`. The plugin was copied onto the guest's local
ext4 filesystem, validated, enabled after the tray and loaded through a real
`omarchy restart shell`. The shared folder was used only to transfer source
and captures, not as the measured Storage fixture.

| Flow | Observed result |
| --- | --- |
| First opening / folder choice | Capacity and immediate folder names appear without recursion; path entry chooses a folder; Main storage names its included mounted roots. |
| Large folder | 100,000 empty files in 100 directories completed at **100,101 entries**, complete coverage, **3,690,496 allocated bytes**. |
| Pause | The restart check saved **30,001 entries**, 71 pending directories and the same known lower bound. The final gallery run paused at **35,001 entries**, 66 pending directories. |
| Shell restart | The inventory remained paused at **30,001 entries**. No automatic continuation occurred. `check-restart-history.py` also passed: newest history saved, old probes gone, exactly one new parent/worker pair. |
| Continue with popup closed | Explicit Continue advanced the same inventory to **100,101 entries** with no remaining frontier and complete coverage. Reopening loaded the saved view. |
| Main storage | The guest's primary ext4 scan finished at **242,169 entries**, **6,029,463,552 known allocated bytes**, partial coverage because protected/unreadable content and nested mounts were excluded. An earlier run also recorded a changing directory and finished other known folders. |
| Serving Board | The original `scripts/storage-demo.py` fixture completed at **119 entries**, complete coverage, **6,615,040 allocated bytes**. Sparse files, hardlinks, hidden entries and byte-escaped names remained visible. |
| Keyboard | Clicking a folder row then Enter descended into Noodle Workshop; `h` returned to the Serving Board root. |
| Copy | Native Copy placed the exact selected Noodle Workshop UTF-8 path on the Wayland clipboard, confirmed with `wl-paste --no-newline`. |
| Open | Native Open handed the validated Noodle Workshop directory to the portal; Nautilus opened that directory. No selected file was executed. |

The native [gallery and pixel provenance](../screenshots/storage-v02/README.md)
use Tokyo Night at 200% virtual-display scale. Full desktop captures stay in
ignored task artifacts; the published images are lossless panel crops only.
All fixture content is original synthetic data, and the account is disposable.

## Performance fixture

Two direct scanner runs used **1,000,000 empty files in 1,000 directories**
(1,001,001 measured nodes) on warm Linux 6.8 container overlay storage, Python
3.13.5, on the local Apple Silicon VM host:

| Run | Scan time | Committed slices | Largest slice | Inventory metadata | Root query |
| --- | ---: | ---: | ---: | ---: | ---: |
| First | 15.618 s | 201 | 0.525 s | 122,728,448 B (117.0 MiB) | 3.73 ms |
| Rescan | 14.424 s | 201 | 0.102 s | 122,728,448 B (117.0 MiB) | 3.22 ms |

Both inventories finished with complete coverage. These measurements call the
scanner directly: they exclude worker startup, UI response time and cold I/O.
Empty files and filesystem directory metadata are a controlled stress fixture,
not a claim about typical hardware, file sizes or physical occupancy.

The reusable checker uses the same construction and two-scan method:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check-storage-jobs.py --files 1000000
```

Its default 60,000-file run also passed after integration. No fixed time
threshold is asserted; entry count, complete coverage and root ledger count
are checked. Current safeguards are 5,000 entries/about 1.5 seconds per slice,
10 million entries, depth 128 and 240 MiB database metadata per inventory.
SQLite journals need additional temporary space. An individual slow syscall
can exceed the slice time target; saved progress remains available at a limit.

## Coverage limits

The VM verifies a real Omarchy/Hyprland/Quickshell integration and ext4, rather
than physical hardware. Btrfs filesystem/subvolume grouping, mounted snapshot
exclusion and alias deduplication have source-backed and mocked regression
coverage; this phase did not repeat them on a real Btrfs device. Cold-disk
performance, removable-device unplug, physical multi-monitor and suspend/resume
were not repeated for 0.2. GPU sampling was unchanged and no new GPU-hardware
coverage is claimed. The accepted [0.1 QA evidence](final-qa-evidence.md)
preserves its own passes, failures and waived/not-run checks.
