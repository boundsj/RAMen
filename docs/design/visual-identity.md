# Visual identity and information design

RAMen's identity is a ramen bowl seen from the side: rim, deep rounded body,
small foot, and two chopsticks resting on the rim. The same geometry is the
bar gauge, the Memory page hero, the logo, the banner and the social preview,
so the mark a user sees in the bar is the mark on GitHub.

The artwork sets that bowl as a night food stall's neon sign: cyan tube
outlines, magenta chopsticks with small gold "contacts" (the RAM-stick pun of
the earlier logo), warm broth light, steam, rain and a perspective grid floor,
with a vertical neon sign reading ラーメン ("ramen"). That mood stays in the
artwork. Inside the widget, colour belongs to the active Omarchy theme and to
meaning; the panel gets one restrained accent line, not a neon skin.

## One geometry, everywhere

- [`Icons.js`](../../Icons.js) holds the bowl (an 18 x 16 box) and the icon set
  (a 16-unit box, 1.5-unit round stroke) as SVG path data.
  [`RamanBowl.qml`](../../RamanBowl.qml) and [`RamanIcon.qml`](../../RamanIcon.qml)
  draw them with Qt Quick Shapes; [`scripts/build-art.py`](../../scripts/build-art.py)
  reads the same file to write the SVG artwork and
  [`docs/art/icons.svg`](../art/icons.svg).
- Icons: close (kill, SIGTERM), a stop-sign octagon around the cross (force
  kill, SIGKILL), lock (protected), info (details), back/next, search, and the
  steam marker for recorded incidents. Kill and force kill differ in shape, not
  only colour. Earlier builds drew these with Nerd Font glyphs, which render as
  empty boxes when the font is missing; no meaning now depends on an icon font
  (`tests/qml/tst_design.qml` checks every page for private-use glyphs).
- Row actions are 24-pixel targets (the host's own action buttons are 22).

## The bowl as a gauge

The broth level is the gauge: a linear position from the inner base (0%) to
the rim (100% used), with the exact percentage beside it. Position along a
common scale is the most accurately read of the common encodings (Cleveland
and McGill's graphical-perception experiments), so the level, not the area,
carries the reading.

Area still matters to the eye, and a bowl narrows toward its base. Measured on
the actual curve (`tests/test_design.js`), the filled area divided by the level
is 0.73 at 25%, 0.88 at 50%, 0.95 at 75% and 0.98 at 90% used. That ratio is
Tufte's Lie Factor (*The Visual Display of Quantitative Information*, p. 57:
the size of an effect shown in the graphic over its size in the data) for the
filled area. The body was made steeper until it stayed within 5% from 75% up,
where the warning and critical thresholds sit; a rounder bowl under-drew the
middle by 20%. Low readings still look a little emptier than they are; the
label is always there to read the number.

Steam is a second, shape-only cue: none when healthy, one wisp when tight, two
when critical. Tight and critical can come from memory pressure while used
memory is moderate, so the level and the steam can disagree, and both are
true. Nothing animates except the level and colour transitions the user's
`animations` setting and the shell's reduced-motion preference allow.

## Colour: the theme's meaning, made legible

- Green, yellow and red are still chosen by `Level.palette` with its hue
  checks. [`Design.js`](../../Design.js) never changes that choice or its hue:
  it only darkens (on a light surface) or lightens (on a dark one) a colour
  that would be too faint, to 4.5:1 for text and 3:1 for marks (WCAG 2.x).
  On dark themes the colours are unchanged; on light themes a yellow label that
  measured 1.4:1 on the bar becomes a readable amber.
- The check is made against the surface actually behind the colour, tints
  composited: the panel's colours hold on the card, the host's row cursor,
  selected rows, meter tracks and History's range band. Text that sits on a
  semantic tint (`Kill?`/`Force?` at rest and under the pointer, the Memory
  percentage badge) gets its own colour derived against that tint. Before
  this, the light theme's `Kill?` measured 3.3:1 under the pointer.
- The bar uses the bar's own background; a transparent bar keeps the theme
  colour because its background is unknown.
- The theme accent is the one "neon" line in the panel: under the selected
  page, window or lens, under the History title, the History cursor, the CPU
  rails and the Storage bands. Measured data keeps the foreground colour;
  warning and critical colours mean only warning and critical.
- No glow, gradient, 3D or shadow touches a measured length in the widget.

## Hierarchy and labels (Tufte-informed)

- Direct labels instead of legends: the warn/critical thresholds are labelled
  under their ticks on the Memory meter (packed side by side inside the meter
  when close, even at 99% and 100%); the size
  column is labelled "PSS + SWAP" above the numbers; the Storage capacity strip
  has swatch-keyed labels under it and the ledger has column headers; History
  keeps its labelled per-track scales and dashed, labelled current thresholds.
- Redundant ink removed: Memory's legend sentence moved into the column label;
  Storage no longer states "partial" three times and its capacity sentence
  became labels keyed to the strip; key hints wrap instead of being cut
  mid-word.
- Numbers right-aligned in a monospace face, units attached, with each
  column as wide as its longest value needs (measured in the current font), so
  app names keep the rest of the row.
- Unknown is never drawn as zero: an empty outlined rail, `–`, `≥` and `≤`
  keep their meanings from the Apps design.
- Apps details read as labelled facts (Footprint, RAM, CPU, GPU, Members) with
  their own meters, instead of a run of similar sentences.
- Storage bands keep width as the only size encoding; tone now shows depth
  (strongest at the top level) and alternates between neighbours, with a
  one-pixel seam, and labels hide when a band is too narrow to hold one.
- Selection and keyboard focus are separate: selected is filled, bold and lit;
  focused adds a 2-pixel outline.

## Before and after, briefly

| Area | Before | After |
| --- | --- | --- |
| Bar | Battery outline, colour only, light-theme yellow at 1.4:1 | Bowl gauge, level + steam, contrast-safe label |
| Memory | Chip glyph hero, unlabelled threshold ticks, legend cut off | Live bowl hero, labelled thresholds, "PSS + SWAP" column label |
| History | Already close to the target; grey title rim | Accent rim and cursor, readable thresholds on light themes |
| Storage | Long run-on status lines, two capacity statements, wrapped button grid, fixed `#444` track | Sections, swatch-labelled capacity strip, column headers, depth-toned bands, badge + status |
| Apps | Names cut at ~20 characters, details as sentences, struck-through unavailable lens | Measured columns, labelled details facts, dimmed lens with reason |
| Art | Tokyo Night card with RAM-stick bowl | Neon yatai: tube wordmark, bowl mark, ラーメン sign, bar strip |

## Limits

- The widget captures in this repository are Qt offscreen renders
  ([previews](../previews/README.md)); they are not native Omarchy
  screenshots. Native visual checks are listed in
  [final QA](../qa/final-qa.md#6-neon-design-native-visual-checks).
- Theme-specific results depend on each theme's colours; the offscreen
  renders use the test stubs' dark and light colours and RAMen's fallback
  palette.
- GitHub's social preview image is a repository setting and is not changed by
  a commit; `docs/art/social-preview.png` is ready to upload.
- Qt's software scene graph (used by the offscreen renders, and by Quickshell
  under `QT_QUICK_BACKEND=software`) paints a Shape that lies wholly outside
  its clip with no clip at all. Icons and History's chart Shapes therefore
  hide while scrolled wholly out of their list or page
  (`Design.inViewport`); GPU renderers clip them either way.
