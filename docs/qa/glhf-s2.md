# S2 live Omarchy acceptance handoff

> **Current status (2026-10-06).** S2 was accepted and merged in PR #5. Its outstanding hardware, visual
> and Storage gallery items moved to the consolidated [final QA runbook](final-qa.md)
> at Jesse's direction. Read-only-source/unsupported-filesystem hardware QA and
> stalled/uninterruptible-I/O checks are excluded, not deferred. The text below
> is the historical procedure and evidence for its named revisions.

S2 code readiness is not issue acceptance. Run these checks at the reviewed PR
head in an authorized Linux 6.8+ Omarchy v4.0.4 session, using test-owned folders
and cache. This handoff does not authorize installation, remote GLHF access or
permanent deployment. Parent orchestrator owns independent review/PR creation;
Jesse owns acceptance/merge. S3 owns the exhaustive failure/race/performance and
hardware matrix; final gallery remains S-DOC.

Record exact SHA, kernel/filesystem, Omarchy/Quickshell versions, commands and
outcomes. Keep exploratory captures/logs under `.agent-artifacts/s2/`. Do not
publish personal paths or scan unrelated folders. Restore demo off afterward.

1. Run all six Node suites, full Python discovery, and `scripts/check-storage.py`
   with task-owned output. Use `RAMAN_QML_ARTIFACTS=.agent-artifacts/s2` and a
   separately supplied Omarchy shell for `scripts/check-qml.sh`. Do not call
   macOS skips Linux evidence. Repeat performance measurements on native GLHF
   disk: local Docker cached-page checks had an occasional >100 ms target miss.
2. Open Storage without a cache, reopen with a cache, and open Memory/History.
   Confirm no recursive scan starts. Capacity alone refreshes at 30 s only while
   Storage is visible. Memory detail remains visible-only, summary cadence and
   bar gauge/colors/hysteresis unchanged, History remains read-only.
3. Select Home, Root, a local volume and a typed test directory with spaces,
   Unicode/newlines, hidden/sparse/hardlinked files, empty/zero entries, symlinks,
   inaccessible/excluded folders and >100 siblings. Explicitly scan. Check
   capacity used/reserved/available separately from scoped known allocation,
   folder overhead, partial/lower-bound wording, age and no fabricated percent.
4. Compare band/ledger IDs, three stable heights/shared frame, self allocation,
   tiny list and Other's real members. Page through with exact returned offsets;
   filter `%_` literally within only the current directory. Confirm ≤50 rows and
   ≤60 segments, empty directory overhead and partial coverage remain truthful.
   Expand Other then select a large map file or submit a matching filter; check
   the visible highlight, row index and Open/Copy target agree. Select third-level
   Folder itself and confirm its directory appears in the ledger; Folder itself /
   All entries buttons provide the same transition. Submit a filter, then try an
   old row click, j/k, map/Other, self/tiny, paging and Open/Copy while pending:
   those controls must remain unavailable and the matching reply must still
   install the filter results. Later selections must work. New filters, scope
   and breadcrumbs may supersede queries; page buttons/Escape must still exit.
   Force a query failure and confirm the retained rows and filter agree.
5. Test j/k, Enter/l, h/Backspace, r/c/o/y and Escape. Type those letters plus
   arrows, Backspace, Return and Escape in both editors: editing must not scan,
   cancel, navigate, switch panels or signal. From ledger row zero, k/Up must
   return to page buttons so Left can switch to History. Use every equivalent button. No
   Storage key arms a kill; verify Memory's two-step kill/timeout only with demo
   rows or owned child processes.
6. Cancel queued/active scans and switch scopes/pages/close during them. ACKs
   must remain pending until cancelled/left/committed/uncertain reconciliation.
   Previous data stays labelled older; a won commit reloads the selected cache;
   uncertainty never claims prior retention. Inject late request/generation IDs.
   Open two panels: leaving one must not interrupt the other's work. Exercise
   runtime migration/helper restart and reopen without accumulating clients.
7. Open a directory and a file's parent through the desktop OpenURI portal;
   confirm the actual file manager opens the intended directory. Check system
   GIO/session bus/backend support and verify that missing/refusing support is
   explicit. The directory FD goes over D-Bus, not through a `/proc/self/fd` URI. It must never execute the file.
   Check copied path bytes via `wl-paste`, including newlines/shell-looking names.
   Changed/replaced/symlink ancestors must fail until rescan. Legacy S1 snapshot
   actions must explain rescan. Non-UTF8 copy must explain the representation
   limit while browsing remains possible. Missing/refusing helpers report errors.
   A successful portal response does not establish the file manager's later
   canonicalization/race behavior; record the desktop's observed support.
8. Verify map on/off and scope-memory on/off, shared session semantics, narrow
   and wide cards, scrolling at short height, light/dark, HiDPI, long breadcrumbs
   and tiny labels. Check ledger highlight and accessible button names visually.
   Restore the original settings and live data.

Remaining gates: independent exact-head review, authorized live Omarchy steps
above, native disk performance target, physical monitor/HiDPI/theme verification,
and Jesse acceptance/merge. No S2 acceptance/Done or full-feature closure is
claimed by local fixture checks.

## Review round 1 fix verification (2026-10-04)

All six findings have implementation regressions: real portal/GIO directory and
file-parent dispatch; unfinished action-child cleanup across leave/EOF/owner
loss/failure/timeout, preserving a completed manager and an unrelated process;
scan committed/uncertain recovery; nested Other's visible action identity;
snapshot navigation reset; and ledger-to-page keyboard focus. Portal rejection,
timeout and cancellation close unfinished requests and never report success.
The tests use stdlib Python, with optional system GIO/D-Bus/portal/Xvfb tools for
desktop fixtures. These are test-owned desktop handlers, not live Omarchy QA.

Linux Docker (Debian 13, Python 3.13.5, Qt 6.8.2, procps installed): all six Node
suites pass; 144 Python tests pass with no skips; 63 Qt checks pass at 1× and 2×.
The 50,000-fanout checker passes: cached page 29.94 ms; five large cached pages
32.29–34.88 ms; cancel ACK 0.21 ms; terminal reconciliation 227.73 ms; EOF cleanup
220.97 ms; waiting-parent-death cleanup 214.05 ms; maximum summary spacing 2.0001 s.

Controlled cached-query comparison used the same image, native Docker volume,
2,000-entry fixtures, 50-row protocol pages, and S1/head/head/S1 order without
concurrent verification. Each run discarded five warmup requests and measured
30 requests. S1 warm medians were 30.70/33.44 ms and maxima 79.40/44.76 ms;
S2 medians were 31.86/33.73 ms and maxima 50.20/64.48 ms. No warmed sample exceeded
100 ms. The overlapping measurements do not show a material S2 regression or
justify changing the implementation/threshold. They do **not** explain or erase
the original 81–157 ms failed target, which remains evidence requiring native
verification. No unmeasured claim about Docker overhead is made.

Logs, controlled benchmark scripts/results and Qt captures remain under
`.agent-artifacts/s2/fixes-1/`. Exact-head independent review, real Omarchy,
actual native desktop/clipboard, physical display and native timing are still
required. These checks do not establish S2 acceptance or issue completion.


## Review round 2 fix verification (2026-10-04)

Both map selection manifestations and filtering from Other now have tracked Qt
regressions. Adjacent cases cover empty/filtered directories, self selection and
return to children, tiny paging, directory descent, Open/Copy node identity and
superseded ledger replies after map/filter/keyboard choices. Paging tiny members
while a nested directory query is outstanding retains that necessary query and
selects from the latest returned slice.

All six Node suites pass. Qt 6.8.2 offscreen checks in the existing Debian 13
container pass at 1× and 2×: 73 passed per scale, no failures or skips. Logs and
captures are under `.agent-artifacts/s2/fixes-2/`. Python/backend code is unchanged;
this follow-up does not claim a fresh backend run. Independent review, authorized
live Omarchy/native manager/clipboard, physical-display checks and native timing
remain gates. The earlier 81–157 ms cached-query miss remains unexplained. No
S2 acceptance or Done status is established by these checks.


## Review round 3 fix verification (2026-10-04)

The actual old-ledger delegate-click reproduction failed before the fix: filter
`big` remained active while the unfiltered listing survived. The tracked Qt case
now drives that delegate and verifies its pending query survives and installs the
matching rows. One query-pending rule disables old ledger/map/Other/self/tiny,
paging, rescan and path actions until data settles. New filters, breadcrumbs,
scope changes and page exit remain available; later selection works normally.
Query errors restore the retained listing's filter. Adjacent tracked tests cover
these transitions and superseded replies; earlier local-choice tests now reflect
this rule rather than cancelling pending data.

Six Node suites and 89 Qt 6.8.2 offscreen checks at each of 1× and 2× pass, with
no failures, skips or QML warnings. Evidence is under
`.agent-artifacts/s2/fixes-3/`. Backend code is unchanged and backend suites were
not repeated. Parent independent review, authorized live Omarchy, native manager
and clipboard, physical display and native timing remain open. The earlier
81–157 ms miss is still unexplained; S2 acceptance is not established.


## User QA follow-up fixes (2026-10-04)

Review 5410027951
reports two reproduced P2 failures on `86264e8ed8e0f14ccdf6c36f471d2b58720e83a7`:
Cancel after completion
and mixed Other membership.
Both have tracked failing-before/passing-after regressions. Correlated command
errors without scanId now settle Cancel and reload authoritative cache without
claiming cancellation won or that the scan failed. Stale/unrelated replies and
explicit wrong scan IDs remain rejected. Browsing/actions wait for the reload;
selected cache and activation uncertainty remain truthful.

The clicked Other band's Previous / Next members controls now traverse explicit
members and backend pages, and return across their boundary. Regressions cover
99 members at root and nested levels, filtered membership, multiple backend
pages, reverse paging, exact distinct membership, selected action IDs, a filter
superseding a pending tail query, and shared-budget coalescing of directory self
allocation. Limits remain 50 ledger rows and 60 map segments.

Fresh local checks: all six Node suites pass; Qt 6.8.2 / Debian 13 ARM64 Docker
with supplied Omarchy UI and process/IPC/window doubles passes **96 checks at
both 1× and 2×**, with no failures, skips or QML warnings. Python discovery
runs **145 tests: 142 passed, 3 optional-tool skips** (restart discovery and two
portal fixtures). An actual Linux controller and owned scan worker reproduce
completion/reaping before Cancel, its scanId-free error, and authoritative cache
selection. Runtime Python is unchanged. Logs/reproduction receipts are under
`.agent-artifacts/s2/qa-fixes-1/`.

The linked review's native evidence belongs to the pre-fix SHA: Linux 7.2.5,
Omarchy 4.0.4, Quickshell 0.3.1, Qt 6.11.2, Python 3.14.7 and Btrfs; six Node
suites, 144 Python tests (one optional skip), and a passing 50,000-entry checker
with cached queries 33–51.8 ms. Its Qt History failures also reproduced on base.
Its isolated real UI/probe checks at 160% exercised scan/cache/filter/selection,
byte-exact clipboard and correct portal parent URI. These are user QA evidence,
not new checks of these fixes. The earlier 81–157 ms timing miss remains unexplained.

Parent independent review and live verification of these two fixes remain gates.
Full installed-bar/service integration, multi-monitor interactions, all live
settings/themes and the exhaustive hardware/failure matrix remain unverified.
No installed widget change, push, remote comment, merge or S2 acceptance is
implied by this fix receipt.
