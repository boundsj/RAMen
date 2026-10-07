#!/usr/bin/env python3
"""Rasterise docs/art/*.svg to the checked-in PNGs with headless Chrome/Chromium.

    python3 scripts/render-art.py [--font PATH-TO-JetBrainsMono.ttf]

The SVG is inlined into a page so the taglines can use the given font (the
PNGs in the repository were rendered with JetBrains Mono); without --font the
browser's monospace is used. Set CHROME to the browser binary if it is not
found. Writes logo.png (512 px), banner.png (2560 px wide) and
social-preview.png (1280 x 640, GitHub's social preview size).
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "docs", "art")
# (svg, png, css width, css height, device scale)
JOBS = [("logo.svg", "logo.png", 256, 256, 2), ("banner.svg", "banner.png", 1280, 400, 2),
        ("social-preview.svg", "social-preview.png", 1280, 640, 1)]
CANDIDATES = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "google-chrome", "google-chrome-stable",
              "chromium", "chromium-browser"]


def browser():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for name in CANDIDATES:
        path = name if os.path.isabs(name) else shutil.which(name)
        if path and os.path.exists(path):
            return path
    sys.exit("headless Chrome/Chromium not found; set CHROME")


def main():
    font = None
    if "--font" in sys.argv:
        font = os.path.abspath(sys.argv[sys.argv.index("--font") + 1])
    chrome = browser()
    face = ('@font-face { font-family: "JetBrains Mono"; src: url("file://%s"); }' % font) if font else ""
    with tempfile.TemporaryDirectory() as tmp:
        for svg_name, png_name, w, h, scale in JOBS:
            svg = open(os.path.join(ART, svg_name), encoding="utf-8").read()
            page = os.path.join(tmp, svg_name + ".html")
            with open(page, "w", encoding="utf-8") as handle:
                handle.write('<!doctype html><html><head><meta charset="utf-8"><style>%s html,body{margin:0;background:transparent}'
                             'svg{display:block}</style></head><body>%s</body></html>' % (face, svg))
            out = os.path.join(ART, png_name)
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                            "--default-background-color=00000000", "--force-device-scale-factor=%d" % scale,
                            "--window-size=%d,%d" % (w, h), "--screenshot=" + out, "file://" + page],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("rendered docs/art/%s (%dx%d)" % (png_name, w * scale, h * scale))


if __name__ == "__main__":
    main()
