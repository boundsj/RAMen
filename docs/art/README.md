# Artwork and image provenance

## Brand artwork (neon yatai)

| File | What it is | Size |
| --- | --- | --- |
| `logo.svg` → `logo.png` | App mark: the neon bowl on a rain-lit grid in a rounded tile | 512 × 512 |
| `banner.svg` → `banner.png` | README banner: bowl, "RAMen" tube wordmark, taglines, bar items, ラーメン sign | 2560 × 800 |
| `social-preview.svg` → `social-preview.png` | GitHub social preview at GitHub's recommended size | 1280 × 640 |
| `icons.svg` | Reference sheet of the in-app vector icons and the bowl gauge | vector |

All four SVGs are written by [`scripts/build-art.py`](../../scripts/build-art.py)
(Python standard library, deterministic) and are RAMen's own artwork, authored
for this repository under the existing Jesse Bounds MIT license. Edit the
script, not the generated SVGs. The bowl, chopsticks, steam and icons are read
from [`Icons.js`](../../Icons.js), the same geometry the widget draws, so the
art cannot drift from the bar gauge. The wordmark "RAMen" and the vertical
sign ラーメン ("ramen", with the long-vowel mark vertical as in vertical
writing) are hand-drawn monoline tube paths, not text set in a font; the
katakana strokes were checked against a system Japanese font. No logo,
lettering, photograph or still from any film, game or other project was
traced, sampled or recoloured; the neon, rain and grid are generic motifs
drawn from scratch. No raster image generator was used.

Render the PNGs with [`scripts/render-art.py`](../../scripts/render-art.py),
which inlines each SVG in a page and screenshots it with headless
Chrome/Chromium:

```bash
python3 scripts/build-art.py
python3 scripts/render-art.py --font /path/to/JetBrainsMonoNerdFont-Regular.ttf
```

The checked-in PNGs were rendered with Google Chrome 154.0.8037.98 headless on macOS
with JetBrainsMono Nerd Font v3.4.0 (SIL OFL 1.1, used only to rasterise the
taglines; no font file is tracked). Without `--font` the taglines fall back to
the browser's monospace.

The GitHub **social preview** is a repository setting (Settings → General →
Social preview) and is not changed by committing `social-preview.png`; upload
it there. A copy uploaded before the spelling fix below shows "RAMan" and
needs replacing.

The previous Tokyo Night-coloured logo and banner (hand-written SVG, RAM-stick
chopsticks over a meter on the bowl front) are in git history before this
change; their RAM-stick chopsticks survive as the gold contacts on the new
chopsticks.

The first neon banner and social preview (commit `8a330ae`, PR #12)
misspelled the wordmark as "RAMan". The lowercase `a` tube was replaced by an
`e` on the same 30-unit bowl, with a full-width crossbar and an aperture open
36° below it, and both PNGs were re-rendered with the toolchain above. The
logo has no lettering and its PNG is unchanged. `tests/test_brand.py` reads the
drawn letters back from the SVGs and checks them against the manifest name.

## In-widget drawing

The widget adds no image files. Its bowl gauge (`RamanBowl.qml`), icons
(`RamanIcon.qml`), chips, meters and buttons are Qt Quick Shapes and
rectangles in the active Omarchy theme's colours; see the
[visual identity notes](../design/visual-identity.md). History's steam marker
and bowl-rim underline, Storage's folder bands and Apps' paired rails are
drawn at run time as before. The `apps` demo scene's names, members, command
lines and synthetic GPU devices and the Storage fixtures are original
synthetic material. No Argus, Disk Lens, Omadisk, SysMon or User Services
artwork, layout or screenshot was reused.

## Widget images

- **Current previews:** [`docs/previews/`](../previews/README.md) holds Qt
  6.8 offscreen renders of the current QML, made by `tests/qml/tst_gallery.qml`
  from the probe's demo scenes, with [provenance](../previews/provenance.json).
  They are synthetic renders, **not** native Omarchy screenshots.
- **Historical native captures:** `docs/screenshots/` and
  `docs/media/masters/` hold H3's direct captures of the real widget on
  Omarchy at 160% scale (the earlier design, before the Storage and Apps page
  buttons). Every master, derivative and recipe is listed in
  [provenance.json](../media/masters/provenance.json); see the
  [on-device QA report](../qa/h3-omarchy-evidence.md). They are kept as
  evidence of that revision, unretouched: the notification capture
  (`critical-toast.png` and its master) shows the title the widget sent then,
  with the earlier spelling "RAMan · Critical".
- **Native captures:** four representative Memory, History, Storage and Apps
  captures are in the [native gallery](../screenshots/final-qa/README.md), with
  [lossless masters and provenance](../media/masters/final-qa/provenance.json).
  They show the installed widget on a physical display using synthetic scenes
  and a test-owned Storage fixture. See [coverage and limits](../qa/final-qa-evidence.md).

See [credits](../../CREDITS.md), [visual identity](../design/visual-identity.md),
[History design](../design/history.md),
[Storage design](../design/storage.md#original-design-and-influences) and
[Apps design](../design/apps.md#original-design).

## Marketplace preview

The repository-root [preview.png](../../preview.png) is a byte-for-byte copy of
`social-preview.png`: original RAMen branding generated from
`scripts/build-art.py` and rendered by `scripts/render-art.py`. It is an
illustration, not a native widget screenshot. The README's native gallery
retains its separate capture provenance. After regenerating the artwork, copy
`docs/art/social-preview.png` to `preview.png` as well.
