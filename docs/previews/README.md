# Offscreen previews (synthetic, not native screenshots)

These PNGs show the current widget design. They are **Qt offscreen renders**,
made by the optional QML harness, not screenshots of a running Omarchy shell.
Four representative [native final-QA captures](../screenshots/final-qa/README.md)
now show this design. These offscreen files retain their original provenance;
the older H3 native captures also remain tied to their historical revision.

How they were made ([provenance.json](provenance.json) lists every file with
its gallery state and SHA-256):

- **Code:** the commit that adds or last changes each file. Each image is a
  named state of `tests/qml/tst_gallery.qml`, which builds the real
  `BarWidget`, `Panel`, page views and `RamanRuntime`; only the probe process,
  IPC and the layer-shell window are test doubles.
- **Data:** the probe's own demo-scene output (`tests/make_qml_fixtures.py`
  runs `raman_probe.load_demo` and the Apps inventory/query code), the History
  demo timeline, and a synthetic "Serving Board" Storage listing written in the
  test. Nothing was scanned, read from a real system or signalled.
- **Renderer:** Qt 6.8.2, `QT_QPA_PLATFORM=offscreen`, `QT_SCALE_FACTOR=2`
  (2x device pixels, shown at half size in the README), animations off so
  each image is the settled state, in the
  `raman-s2-fixes:local` Debian 13 ARM64 container, with Omarchy v4.0.4's UI
  modules (`c668141e`) and the repository's test stubs.
- **Theme:** the stubs' dark (`#101315` / `#cacccc`) and light (`#e6e7ed` /
  `#343b58`) colours with accent `#7aa2f7`, and RAMen's fallback green /
  yellow / red; a real theme supplies its own colours.
- **Font:** JetBrainsMono Nerd Font v3.4.0 through a fontconfig `monospace`
  alias, as `omarchy-font-set` does. No icon in the design depends on its
  Nerd glyphs; the same tests pass with DejaVu Sans Mono only.
- **Width:** the panel at 420 logical pixels (340 for the narrow image).

Reproduce (with Omarchy's `shell/` directory and Qt 6.8+):

```bash
QT_SCALE_FACTOR=2 RAMAN_QML_SHELL=/path/to/omarchy/shell scripts/check-qml.sh \
  Gallery::test_bar Gallery::test_memory Gallery::test_history Gallery::test_storage Gallery::test_apps
# images: <stage>/shots/gallery/*.png
```

| File | State |
| --- | --- |
| `bar-green-dark.png`, `bar-yellow-dark.png`, `bar-red-dark.png` | Bar item, `demo green/yellow/red` summaries, dark bar |
| `bar-yellow-light.png` | Bar item, `demo yellow`, light bar (contrast-adjusted amber) |
| `memory-red-armed-420-dark.png` | Memory, `demo red`, Chrome armed (`Kill?` shown, never confirmed) |
| `memory-yellow-420-light.png` | Memory, `demo yellow`, light theme |
| `history-1h-420-dark.png` | History, demo timeline, 1 hour |
| `history-receipt-420-light.png` | History, critical receipt inspected, light theme |
| `storage-ready-420-dark.png` | Storage, synthetic Serving Board listing with bands, `broth-stock` selected |
| `apps-memory-armed-420-dark.png` | Apps, `demo apps`, Memory lens, Broth simulator armed |
| `apps-gpu-420-dark.png` | Apps, `demo apps`, GPU memory lens (synthetic GPU readings) |
| `apps-details-420-dark.png` | Apps details for Broth simulator (synthetic members and command lines) |
| `apps-memory-protected-340-dark.png` | Apps at 340 px, protected Hyprland row selected |
