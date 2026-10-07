# Native final-QA captures

These are genuine captures of the installed RAMen widget at runtime candidate
`6ebe9f05712def5964024f138ad5d2552567c606`, on physical eDP-1,
2560 × 1600 at 160%, Tokyo Night, JetBrainsMono Nerd Font. Captured on an empty
workspace using `grim`; panel crops are lossless and untouched. No live app
list, personal path, reconstructed UI or offscreen rendering is published here.

| Display copy | Scene | Native master |
| --- | --- | --- |
| [Memory](memory-red.png) | Built-in red scene; all readings and app names synthetic | [616 × 928](../../media/masters/final-qa/raman-memory-red-tokyo-night-160pct.png) |
| [History](history.png) | Built-in History scene on its fake clock, including a synthetic gap | [616 × 1004](../../media/masters/final-qa/raman-history-tokyo-night-160pct.png) |
| [Storage](storage-ready.png) | Original Serving Board fixture at a neutral path; real Btrfs allocation/capacity | [616 × 1360](../../media/masters/final-qa/raman-storage-ready-tokyo-night-160pct.png) |
| [Apps](apps-cpu.png) | Built-in Apps CPU scene; fake rows and synthetic GPU readings, labelled Demo data | [616 × 1300](../../media/masters/final-qa/raman-apps-cpu-tokyo-night-160pct.png) |

[Provenance](../../media/masters/final-qa/provenance.json) records the capture
method, time, crop, appearance, settings, candidate, and SHA-256 of every
master and losslessly re-encoded display copy. README scales only the display
size. Masters were inspected at native resolution for readability and privacy.

These four representative captures do not cover every theme, scale or state.
The [verification summary](../../qa/final-qa-evidence.md) records measured
coverage and limits. Historical H3 captures and Qt offscreen previews retain
their original labels and revision provenance.
