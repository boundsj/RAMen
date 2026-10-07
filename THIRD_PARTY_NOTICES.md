# Third-party material and dependencies

No third-party source or artwork has been incorporated into RAMen's tracked
History (H1/H2/H3), Storage (S1/S2/S3) or Apps (A1/A2/A3/A-DOC)
implementation, fixtures, demo scenes or documentation, so no third-party
notice is required for them. Argus, Disk Lens, Omadisk, SysMon and User
Services are design influences, described in [CREDITS.md](CREDITS.md); their
MIT licenses do not imply that their code was reused. The existing Jesse
Bounds MIT grant in [LICENSE](LICENSE) remains intact.

## Apps: reviewed, not reused

| Source | Revision | What was read | Reused |
| --- | --- | --- | --- |
| SysMon, Greg DeYoung (MIT) | `95743fdacd925bbd9b0af7b6a5ea960943b40a52` | `README.md`, `DetailPopup.qml`, `procprobe.sh`, `LICENSE` | Nothing |
| User Services, Gabriel da Silva (MIT) | `cb925915f08e0049dc31b35219542dc6c5754fcf` | `README.md`, `vram.sh`, `LICENSE` (the roadmap also read `Panel.qml`) | Nothing |
| Linux kernel (GPL-2.0 / MIT per file) | `master` `69f80fef3153299d9c72c53d1d71eef6354b6926` | `Documentation/gpu/drm-usage-stats.rst`, i915/xe usage-stats docs, `drm_file.c`, `amdgpu_fdinfo.c`, `amdgpu_vram_mgr.c`, `i915_drm_client.c`, `intel_memory_region.c`, `xe_drm_client.c` | Nothing; RAMen implements the documented `fdinfo` interface |

The DRM fixtures in `tests/test_gpu.py` use the documented key names with
RAMen's own values, and the synthetic GPU devices, names, members and command
lines in the `apps` scene of `docs/demo-scenes.json` were written for RAMen.
No upstream screenshot or image is tracked; Apps adds no image files.

## Artwork rendering (not bundled)

The neon artwork in `docs/art/` and its generator scripts are RAMen's own
(see [CREDITS.md](CREDITS.md#visual-identity-and-artwork-neon-design)).
Rendering tools are not tracked: headless Chrome/Chromium rasterises the SVGs,
and the PNG taglines and the offscreen previews in `docs/previews/` were
rasterised with JetBrainsMono Nerd Font v3.4.0 (SIL Open Font License 1.1,
which permits embedding glyph images in documents). No font file is
distributed here.

## Runtime dependencies (not bundled)

These are used from the host system at run time; no copy is tracked here.

- Omarchy shell, Quickshell and Qt: host UI, theme, service, IPC and
  notification APIs.
- Python 3 standard library, including its `sqlite3` module for Storage
  snapshots.
- Linux kernel DRM drivers that publish per-client usage stats in
  `/proc/PID/fdinfo` (for the Apps GPU readings); no GPU vendor tool is used.
- Storage path actions: system GIO (loaded through `ctypes`) and the
  freedesktop desktop portal for Open; `wl-copy` from wl-clipboard for Copy.
  Missing helpers are reported as unavailable actions.

## Omarchy host files used by the optional QML harness

- Upstream: [basecamp/omarchy](https://github.com/basecamp/omarchy), v4.0.4,
  revision `c668141e9c42b13c80c9ca4ea108e11708c5e8a5`.
- Local location: generated `.agent-artifacts/**/qml-check.*/stubs/qs/Ui/` and
  `stubs/qs/Commons/`; these are external dependencies, not tracked files.
- Upstream paths: `shell/Ui/{BarWidget,Panel,PanelController,PanelKeyCatcher,
  PanelHero,PanelSeparator,PanelSectionHeader,PanelActionButton,PanelToolTip,
  BorderSurface,CursorSurface,WidgetButton,BorderOverlay}.qml` and
  `shell/Commons/{Border.qml,BorderGeometry.js}` and
  `shell/plugins/notifications/NotificationLogic.js` (StyledText test dependency).
- Modifications: none. Process, IPC, theme and KeyboardPanel test doubles are
  locally authored separate files. `scripts/check-qml.sh` copies the dependency's
  LICENSE into each stage as `OMARCHY-LICENSE`.
- License evidence: upstream `LICENSE`, read at that revision. Notice follows.

```text
Copyright (c) David Heinemeier Hansson

Permission is hereby granted, free of charge, to any person obtaining
a copy of this software and associated documentation files (the
"Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to
the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```
