# Release notes

## 0.2.1 — Bounded clipboard input

- Apps search and both Storage fields no longer read clipboard contents when
  another application changes the clipboard, including while forms are hidden.
- Explicit paste runs outside the UI thread with a byte cap, a total deadline
  and the field's length limit. Invalid, oversized or slow offers leave the
  field unchanged; moving, editing, hiding or closing the field cancels a read.
- Native Unicode/input-method editing, selection and undo/redo remain available;
  each paste has its own undo step, separate from earlier and later typing.
  Long paths scroll to keep the caret visible. Paste requires the optional
  `wl-paste` integration; no new settings or package installation is required.
- Added backend, Qt and native Wayland regression checks. See
  [clipboard verification and scope](docs/qa/clipboard-v021.md).

Update through Omarchy's plugin manager, then restart the shell to replace
previously loaded components. The plugin ID and retained Storage data are
unchanged. This patch fixes RAMen's fields; other plugins' clipboard readers
are outside its scope.

## 0.2.0 — Friendly Storage exploration

- Open Storage at **My files**, see free space immediately, and browse folders
  before measuring them. Choose a folder when you want to look elsewhere.
- **Main storage** explores mounted folders on the filesystem containing Home,
  including supported Btrfs Home/System subvolumes. Other drives and mounted
  snapshots stay separate; capacity is counted once.
- Scans show useful sizes as they go. Close the popup, pause and keep results,
  or continue later. Shell restarts restore saved progress and wait for your
  explicit Continue. Busy folders and unreadable content are labelled partial.
- A compact, indexed inventory replaces the earlier all-or-nothing scan limits.
  One million empty files took about 15 seconds per scan in the warm Linux
  fixture; see [verification and limits](docs/qa/storage-v02.md).
- Updated native Storage gallery, simpler README and regression coverage for
  checkpoint recovery, cache budgets, validated actions and reply races.

Update through Omarchy's plugin manager, then restart the shell if it is
still using the earlier component. No new runtime dependencies or settings
are required. The plugin ID remains `boundsj.raman`. Existing 0.1 snapshots
remain readable; a fresh scan uses the new inventory format. Memory, History,
Apps and guarded process termination keep their existing behavior.

## 0.1.0 — Initial marketplace release

RAMen's memory bowl, pressure history, read-only Storage explorer and grouped
CPU/RAM diagnostics, with kernel-dependent GPU readings and guarded process
termination. Original MIT-licensed artwork and native QA gallery.
