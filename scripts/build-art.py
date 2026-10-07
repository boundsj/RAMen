#!/usr/bin/env python3
"""Write RAMen's SVG artwork masters into docs/art/.

    python3 scripts/build-art.py        # logo.svg, banner.svg, social-preview.svg, icons.svg

Standard library only and deterministic (seeded rain). The bowl mark and the
icon set are read from Icons.js, so the bar gauge, the panel icons and the
artwork share one geometry. The wordmark "RAMen" and the katakana sign
ラーメン ("ramen") are drawn as monoline neon-tube paths, not set in a font;
only the taglines use a font (JetBrains Mono, falling back to any monospace).
Rasterise with scripts/render-art.py. See docs/art/README.md.
"""
import json
import os
import random
import re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "docs", "art")

# --- geometry from Icons.js ----------------------------------------------------------
ICONS_JS = open(os.path.join(ROOT, "Icons.js"), encoding="utf-8").read()


def _js_string(key, text):
    return re.search(r"\b%s:\s*\"([^\"]+)\"" % key, text).group(1)


BOWL_SRC = ICONS_JS[ICONS_JS.index("var BOWL"):]
BOWL = {
    "left": json.loads(re.search(r"left:\s*(\[\[.*?\]\])", BOWL_SRC).group(1)),
    "width": float(re.search(r"width:\s*([\d.]+)", BOWL_SRC).group(1)),
    "rimY": float(re.search(r"rimY:\s*([\d.]+)", BOWL_SRC).group(1)),
    "baseY": float(re.search(r"baseY:\s*([\d.]+)", BOWL_SRC).group(1)),
    "rim": _js_string("rim", BOWL_SRC),
    "foot": _js_string("foot", BOWL_SRC),
    "chopsticks": _js_string("chopsticks", BOWL_SRC),
    "steam": re.findall(r"\"(M[^\"]+)\"", re.search(r"steam:\s*\[([^\]]+)\]", BOWL_SRC).group(1)),
}
ICON_SRC = ICONS_JS[ICONS_JS.index("var ICONS"):ICONS_JS.index("function icon(")]
ICONS = {}
for name, body in re.findall(r"\n  (\w+): \{ stroke: ((?:\"[^\"]*\"\s*\+?\s*)+)\}", ICON_SRC):
    ICONS[name] = "".join(re.findall(r"\"([^\"]*)\"", body))


def bowl_body():
    left, w = BOWL["left"], BOWL["width"]
    def p(pt): return "%g %g" % tuple(pt)
    def m(pt): return "%g %g" % (w - pt[0], pt[1])
    return "M%s C%s %s %s C%s %s %s" % (p(left[0]), p(left[1]), p(left[2]), p(left[3]), m(left[2]), m(left[1]), m(left[0]))


# --- palette (artwork only; the widget uses the active Omarchy theme) ------------------
NIGHT0, NIGHT1, NIGHT2 = "#07060e", "#0d0b1c", "#18123a"
CYAN, CYAN_CORE = "#2de2e6", "#d6fdff"
MAGENTA, MAGENTA_CORE = "#ff3ea5", "#ffe0f0"
AMBER, AMBER_CORE = "#ffb547", "#fff1cf"
MIST = "#b9b4d6"
# The bar mock uses Omarchy's default (Tokyo Night) greens, yellows and reds.
OK, WARN, CRIT, BAR_BG, BAR_FG = "#9ece6a", "#e0af68", "#f7768e", "#1a1b26", "#a9b1d6"
MONO = "JetBrains Mono, JetBrainsMono Nerd Font, DejaVu Sans Mono, monospace"

# --- lettering: monoline tubes on a 100-unit cap height --------------------------------
LETTERS = {  # (advance, path)
    "R": (70, "M0 100 V0 H36 A26 26 0 0 1 36 52 H0 M30 52 L70 100"),
    "A": (72, "M0 100 V36 A36 36 0 0 1 72 36 V100 M0 62 H72"),
    "M": (80, "M0 100 V0 L40 58 L80 0 V100"),
    # Crossbar, then the bowl anticlockwise to a terminal 36° below the bar,
    # leaving the aperture open at small sizes.
    "e": (60, "M0 70 H60 A30 30 0 1 0 54.27 87.63"),
    "n": (58, "M0 100 V40 M0 68 A29 28 0 0 1 58 68 V100"),
}
# ラーメン, top to bottom as on a vertical sign: in vertical writing the long
# vowel mark ー is a vertical stroke. 100-unit em per character.
KANA = [
    ["M30 14 H70", "M18 38 H80 C80 64 62 86 28 95"],               # ラ
    ["M50 10 V90"],                                                # ー (vertical)
    ["M76 10 C70 48 50 76 16 93", "M28 38 C44 50 58 62 74 80"],    # メ
    ["M18 28 C26 30 32 34 38 40", "M20 90 C50 84 72 62 84 22"],    # ン
]


def word_paths(text, gap=26):
    x, out = 0, []
    for ch in text:
        adv, d = LETTERS[ch]
        out.append((x, d))
        x += adv + gap
    return out, x - gap


# --- SVG helpers -----------------------------------------------------------------------
# Gaussian blurs are defined in the user space of the element that uses them,
# so each (blur in pixels, drawing scale, region) gets its own filter.
FILTERS = {}


def blur(px, scale=1.0, region=(0, 0, 1280, 640)):
    key = (round(px / scale, 4), region)
    if key not in FILTERS:
        FILTERS[key] = "b%d" % len(FILTERS)
    return "url(#%s)" % FILTERS[key]


def filter_defs():
    return "".join('<filter id="%s" filterUnits="userSpaceOnUse" x="%g" y="%g" width="%g" height="%g" '
                   'color-interpolation-filters="sRGB"><feGaussianBlur stdDeviation="%g"/></filter>'
                   % (fid, r[0], r[1], r[2], r[3], sd) for (sd, r), fid in FILTERS.items())


def neon(d, color, core, width, transform="", glow=1.0, scale=1.0, region=(0, 0, 1280, 640)):
    """A neon tube: wide halo, coloured glass, bright core. Decoration only.
    `width` is in the transformed user space; `scale` is that space's size
    in pixels, so the halo blurs by the same number of pixels at any scale."""
    t = ' transform="%s"' % transform if transform else ""
    common = 'fill="none" stroke-linecap="round" stroke-linejoin="round"'
    return (
        '<g%s>'
        '<path d="%s" %s stroke="%s" stroke-width="%g" opacity="%g" filter="%s"/>'
        '<path d="%s" %s stroke="%s" stroke-width="%g" opacity="%g" filter="%s"/>'
        '<path d="%s" %s stroke="%s" stroke-width="%g"/>'
        '<path d="%s" %s stroke="%s" stroke-width="%g" opacity="0.9"/>'
        '</g>' % (t, d, common, color, width * 2.6, 0.55 * glow, blur(9, scale, region),
                  d, common, color, width * 1.4, 0.8 * glow, blur(3.2, scale, region),
                  d, common, color, width, d, common, core, width * 0.42))


def rain(rng, w, h, n, alpha=0.13):
    out = []
    for _ in range(n):
        x, y = rng.uniform(-40, w), rng.uniform(-40, h)
        length = rng.uniform(14, 46)
        out.append('<path d="M%.1f %.1f l%.1f %.1f" stroke="#8fa2ff" stroke-opacity="%.3f" stroke-width="%.2f"/>'
                   % (x, y, length * 0.26, length, alpha * rng.uniform(0.35, 1.0), rng.uniform(0.6, 1.3)))
    return '<g stroke-linecap="round">%s</g>' % "".join(out)


def grid_floor(w, h, horizon, vx, color, alpha=0.32, spread=2.4, verticals=17, rows=9):
    """A perspective grid from the horizon to the bottom edge, fading into the distance."""
    lines = []
    for i in range(verticals):
        f = i / (verticals - 1)
        x_bottom = vx + (f - 0.5) * w * spread
        lines.append("M%.1f %.1f L%.1f %.1f" % (vx + (x_bottom - vx) * 0.04, horizon, x_bottom, h))
    for k in range(1, rows + 1):
        y = horizon + (h - horizon) * (k / rows) ** 2.1
        lines.append("M0 %.1f H%d" % (y, w))
    return ('<linearGradient id="floorFade" x1="0" y1="%g" x2="0" y2="%g" gradientUnits="userSpaceOnUse">'
            '<stop offset="0" stop-color="%s" stop-opacity="0"/><stop offset="0.35" stop-color="%s" stop-opacity="%g"/>'
            '<stop offset="1" stop-color="%s" stop-opacity="%g"/></linearGradient>'
            '<path d="%s" stroke="url(#floorFade)" stroke-width="1.2" fill="none" filter="%s"/>'
            '<path d="%s" stroke="url(#floorFade)" stroke-width="0.8" fill="none"/>'
            % (horizon, h, color, color, alpha, color, alpha * 1.4, " ".join(lines), blur(1.2), " ".join(lines)))


def noodle_wave(x0, x1, y, amp, period):
    d, x, up = "M%.1f %.1f" % (x0, y), x0, True
    while x < x1:
        nx = min(x1, x + period / 2)
        d += " Q%.1f %.1f %.1f %.1f" % ((x + nx) / 2, y - amp if up else y + amp, nx, y)
        x, up = nx, not up
    return d


BOWL_REGION = (-6, -6, 30, 28)
LETTER_REGION = (-60, -60, 200, 220)


def neon_bowl(uid, x, y, scale, level=0.74):
    """The bowl mark (Icons.js geometry) as a neon sign: dark body, warm broth,
    noodles, magenta chopsticks with gold RAM contacts, cyan rim and body."""
    s, body = scale, bowl_body()
    tr = "translate(%g %g) scale(%g)" % (x, y, s)
    broth_y = BOWL["baseY"] - level * (BOWL["baseY"] - BOWL["rimY"])
    stroke = 1.0 / s  # one user unit
    out = ['<g>']
    out.append('<radialGradient id="%sBroth" cx="0.5" cy="0.15" r="0.9"><stop offset="0" stop-color="%s"/>'
               '<stop offset="0.55" stop-color="%s"/><stop offset="1" stop-color="#7a3a12"/></radialGradient>' % (uid, AMBER_CORE, AMBER))
    out.append('<linearGradient id="%sBody" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1d1640"/>'
               '<stop offset="1" stop-color="#0a0818"/></linearGradient>' % uid)
    out.append('<clipPath id="%sLevel"><rect x="0" y="%g" width="%g" height="20"/></clipPath>' % (uid, broth_y, BOWL["width"]))
    # Warm light spilling from the bowl.
    out.append('<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="%s" opacity="0.30" filter="%s"/>'
               % (x + 9 * s, y + 5.5 * s, 9.5 * s, 3.2 * s, AMBER, blur(22)))
    # Chopsticks behind the rim, with gold contact fingers near their tips.
    chop = BOWL["chopsticks"]
    out.append(neon(chop, MAGENTA, MAGENTA_CORE, 0.85, tr, glow=0.9, scale=s, region=BOWL_REGION))
    for (x0, y0, x1, y1) in re.findall(r"M([\d.]+) ([\d.]+) L([\d.]+) ([\d.]+)", chop):
        x0, y0, x1, y1 = map(float, (x0, y0, x1, y1))
        dx, dy = x1 - x0, y1 - y0
        ln = (dx * dx + dy * dy) ** 0.5
        nx, ny = -dy / ln * 0.55, dx / ln * 0.55
        ticks = []
        for f in (0.72, 0.8, 0.88):
            px, py = x0 + dx * f, y0 + dy * f
            ticks.append("M%.2f %.2f L%.2f %.2f" % (px - nx, py - ny, px + nx, py + ny))
        out.append('<path transform="%s" d="%s" stroke="%s" stroke-width="%g" stroke-linecap="round" opacity="0.95"/>'
                   % (tr, " ".join(ticks), AMBER, 0.32))
    # Body (occludes the floor), broth to the level, noodles on the surface.
    out.append('<path transform="%s" d="%s Z" fill="url(#%sBody)"/>' % (tr, body, uid))
    out.append('<path transform="%s" d="%s Z" fill="url(#%sBroth)" clip-path="url(#%sLevel)" opacity="0.95"/>' % (tr, body, uid, uid))
    noodles = " ".join(noodle_wave(3.0 + i * 0.4, 15.0 - i * 0.3, broth_y + 0.7 + i * 1.1, 0.45, 1.6 + i * 0.25) for i in range(3))
    out.append('<path transform="%s" d="%s" fill="none" stroke="%s" stroke-width="0.32" stroke-linecap="round" opacity="0.85"/>'
               % (tr, noodles, AMBER_CORE))
    out.append(neon(BOWL["rim"] + " " + body + " " + BOWL["foot"], CYAN, CYAN_CORE, 0.85, tr, scale=s, region=BOWL_REGION))
    out.append('</g>')
    return "".join(out)


def steam_wisps(x, y, scale, color=CYAN_CORE):
    d = ("M0 60 C-10 44 10 34 0 16 C-6 6 2 0 0 -6 "
         "M26 64 C14 46 38 34 26 12 C20 0 28 -8 26 -16 "
         "M52 60 C42 44 62 34 52 16 C46 6 54 0 52 -6")
    return ('<g transform="translate(%g %g) scale(%g)" fill="none" stroke-linecap="round">'
            '<path d="%s" stroke="%s" stroke-width="7" opacity="0.18" filter="%s"/>'
            '<path d="%s" stroke="%s" stroke-width="2.4" opacity="0.55"/></g>'
            % (x, y, scale, d, color, blur(3.2, scale, (-60, -60, 180, 180)), d, color))


def wordmark(x, y, h):
    s = h / 100.0
    parts, width = word_paths("RAMen")  # RAM + ramen: "RAM" in cyan, "en" in magenta
    out = []
    for i, (dx, d) in enumerate(parts):
        color, core = (CYAN, CYAN_CORE) if i < 3 else (MAGENTA, MAGENTA_CORE)
        out.append(neon(d, color, core, 9.0, "translate(%g %g) scale(%g) translate(%g 0)" % (x, y, s, dx), scale=s, region=LETTER_REGION))
    return "".join(out), width * s


def kana_sign(x, y, char_h, pad=None):
    s = char_h / 100.0
    pad = pad if pad is not None else char_h * 0.28
    w, h = char_h + pad * 2, char_h * 4 + pad * 2 + char_h * 0.12 * 3
    out = ['<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="#0b0918" stroke="%s" stroke-opacity="0.55" stroke-width="2"/>'
           % (x, y, w, h, pad * 0.6, CYAN),
           '<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="none" stroke="%s" stroke-opacity="0.35" stroke-width="6" filter="%s"/>'
           % (x, y, w, h, pad * 0.6, CYAN, blur(3.2))]
    for i, strokes in enumerate(KANA):
        cy = y + pad + i * char_h * 1.12
        out.append(neon(" ".join(strokes), MAGENTA, MAGENTA_CORE, 9.0, "translate(%g %g) scale(%g)" % (x + pad, cy, s), scale=s, region=LETTER_REGION))
    return "".join(out), w, h


def bar_mock(x, y, scale=1.0):
    """Three bar items exactly as the widget draws them: bowl gauge + label."""
    out = []
    items = [(0.37, OK, "37%", 0), (0.82, WARN, "82%", 1), (0.96, CRIT, "96%", 2)]
    h = 26 * scale
    unit = 16 * scale / 16.0
    item_w = (18 * unit) + 4 * scale + 30 * scale
    total = len(items) * item_w + (len(items) - 1) * 14 * scale + 24 * scale
    out.append('<rect x="%g" y="%g" width="%g" height="%g" rx="%g" fill="%s" stroke="#2d2f45" stroke-width="1"/>'
               % (x, y, total, h + 16 * scale, (h + 16 * scale) / 2, BAR_BG))
    cx = x + 12 * scale
    body = bowl_body()
    for i, (frac, color, label, steam) in enumerate(items):
        top = y + 8 * scale + (h - 16 * unit) / 2
        tr = "translate(%g %g) scale(%g)" % (cx, top, unit)
        level = BOWL["baseY"] - frac * (BOWL["baseY"] - BOWL["rimY"])
        cid = "barLevel%d" % i
        out.append('<clipPath id="%s"><rect x="0" y="%g" width="18" height="20"/></clipPath>' % (cid, level))
        out.append('<path transform="%s" d="%s Z" fill="%s" clip-path="url(#%s)"/>' % (tr, body, color, cid))
        out.append('<path transform="%s" d="%s %s %s %s" fill="none" stroke="%s" stroke-opacity="0.72" stroke-width="1" '
                   'stroke-linecap="round" stroke-linejoin="round"/>' % (tr, BOWL["rim"], body, BOWL["foot"], BOWL["chopsticks"], BAR_FG))
        if steam:
            out.append('<path transform="%s" d="%s" fill="none" stroke="%s" stroke-width="1" stroke-linecap="round"/>'
                       % (tr, " ".join(BOWL["steam"][:steam]), color))
        out.append('<text x="%g" y="%g" font-family="%s" font-size="%g" fill="%s">%s</text>'
                   % (cx + 18 * unit + 4 * scale, y + 8 * scale + h / 2 + 4.6 * scale, MONO, 13 * scale, color, label))
        cx += item_w + 14 * scale
    return "".join(out), total, h + 16 * scale


def background(w, h, rng, horizon, vx, rain_n):
    return "".join([
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s"/>'
        '<stop offset="0.62" stop-color="%s"/><stop offset="1" stop-color="%s"/></linearGradient>' % (NIGHT0, NIGHT2, NIGHT1),
        '<rect width="%d" height="%d" fill="url(#sky)"/>' % (w, h),
        # City haze at the horizon: magenta to the right, cyan to the left.
        '<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="%s" opacity="0.22" filter="%s"/>' % (w * 0.82, horizon, w * 0.3, h * 0.16, MAGENTA, blur(22)),
        '<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="%s" opacity="0.14" filter="%s"/>' % (w * 0.18, horizon, w * 0.26, h * 0.12, CYAN, blur(22)),
        rain(rng, w, h, rain_n),
        grid_floor(w, h, horizon, vx, CYAN),
        '<rect y="%g" width="%d" height="1.2" fill="%s" opacity="0.55"/>' % (horizon, w, MAGENTA),
    ])


def svg(w, h, title, desc, body):
    defs = filter_defs()
    FILTERS.clear()
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img">'
            '<title>%s</title><desc>%s</desc><defs>%s</defs>%s</svg>\n' % (w, h, w, h, title, desc, defs, body))


# --- compositions ------------------------------------------------------------------------
def logo():
    w = h = 256
    rng = random.Random(7)
    body = ['<clipPath id="tile"><rect x="4" y="4" width="248" height="248" rx="56"/></clipPath>',
            '<g clip-path="url(#tile)">', background(w, h, rng, 196, 128, 28),
            steam_wisps(78, 44, 0.95), neon_bowl("logo", 47, 64, 9.0), '</g>',
            '<rect x="4.5" y="4.5" width="247" height="247" rx="55.5" fill="none" stroke="#3b2f74" stroke-width="2"/>']
    return svg(w, h, "RAMen", "A neon ramen bowl with RAM-stick chopsticks, steam and broth light on a rain-lit grid.", "".join(body))


def banner():
    w, h = 1280, 400
    rng = random.Random(11)
    horizon = 304
    words, ww = wordmark(396, 92, 112)
    sign, sw, sh = kana_sign(1112, 48, 58)
    bar, bw, bh = bar_mock(400, 314, 1.15)
    body = [background(w, h, rng, horizon, 640, 150),
            # The bowl's reflection on the wet street.
            '<g transform="translate(0 %g) scale(1 -1)" opacity="0.22" filter="%s">%s</g>' % (2 * horizon + 6, blur(3.2), neon_bowl("refl", 70, 82, 12.2)),
            steam_wisps(122, 32, 1.15), neon_bowl("main", 70, 82, 12.2),
            words,
            '<text x="400" y="256" font-family="%s" font-size="25" fill="%s">Memory at a glance for the Omarchy bar.</text>' % (MONO, MIST),
            '<text x="400" y="292" font-family="%s" font-size="25" fill="%s">Find the hog. Kill it. Stop hitching.</text>' % (MONO, AMBER),
            bar, sign]
    return svg(w, h, "RAMen: memory at a glance for the Omarchy bar",
               "Neon RAMen wordmark beside a neon ramen bowl, a vertical sign reading ラーメン (ramen), and three bar items at 37, 82 and 96 percent.",
               "".join(body))


def social():
    w, h = 1280, 640
    rng = random.Random(23)
    horizon = 470
    words, ww = wordmark(448, 168, 124)
    sign, sw, sh = kana_sign(1112, 84, 80)
    bar, bw, bh = bar_mock(452, 452, 1.35)
    body = [background(w, h, rng, horizon, 640, 230),
            '<g transform="translate(0 %g) scale(1 -1)" opacity="0.22" filter="%s">%s</g>' % (2 * horizon + 8, blur(3.2), neon_bowl("refl", 70, 196, 15.0)),
            steam_wisps(134, 122, 1.4), neon_bowl("main", 70, 196, 15.0),
            words,
            '<text x="452" y="352" font-family="%s" font-size="25" fill="%s">Memory at a glance for the Omarchy bar.</text>' % (MONO, MIST),
            '<text x="452" y="392" font-family="%s" font-size="25" fill="%s">Find the hog. Kill it. Stop hitching.</text>' % (MONO, AMBER),
            bar, sign]
    return svg(w, h, "RAMen: memory at a glance for the Omarchy bar",
               "Social preview: neon RAMen wordmark, ramen bowl, ラーメン sign and three bar items.", "".join(body))


def icon_sheet():
    """Reference sheet of the in-app vector icons and the bar mark (from Icons.js)."""
    names = list(ICONS)
    cell, pad = 96, 24
    w = pad * 2 + cell * (len(names) + 1)
    h = 150
    out = ['<rect width="%d" height="%d" fill="#101315"/>' % (w, h)]
    for i, name in enumerate(names):
        x = pad + i * cell
        out.append('<g transform="translate(%g 24) scale(3)"><path d="%s" fill="none" stroke="#cacccc" stroke-width="1.5" '
                   'stroke-linecap="round" stroke-linejoin="round"/></g>' % (x + 24, ICONS[name]))
        out.append('<text x="%g" y="120" text-anchor="middle" font-family="%s" font-size="12" fill="#8c90a0">%s</text>' % (x + 48, MONO, name))
    x = pad + len(names) * cell
    body = bowl_body()
    out.append('<g transform="translate(%g 30) scale(3)"><clipPath id="sheetLevel"><rect x="0" y="%g" width="18" height="20"/></clipPath>'
               '<path d="%s Z" fill="%s" clip-path="url(#sheetLevel)"/><path d="%s %s %s %s" fill="none" stroke="#cacccc" stroke-width="1" '
               'stroke-linecap="round" stroke-linejoin="round"/><path d="%s" fill="none" stroke="%s" stroke-width="1" stroke-linecap="round"/></g>'
               % (x + 21, BOWL["baseY"] - 0.82 * (BOWL["baseY"] - BOWL["rimY"]), body, WARN, BOWL["rim"], body, BOWL["foot"], BOWL["chopsticks"], BOWL["steam"][0], WARN))
    out.append('<text x="%g" y="120" text-anchor="middle" font-family="%s" font-size="12" fill="#8c90a0">bowl gauge</text>' % (x + 48, MONO))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img"><title>RAMen icons</title>%s</svg>\n'
            % (w, h, w, h, "".join(out)))


def main():
    files = {"logo.svg": logo(), "banner.svg": banner(), "social-preview.svg": social(), "icons.svg": icon_sheet()}
    for name, text in files.items():
        with open(os.path.join(ART, name), "w", encoding="utf-8") as handle:
            handle.write(text)
        print("wrote docs/art/%s (%d bytes)" % (name, len(text)))


if __name__ == "__main__":
    main()
