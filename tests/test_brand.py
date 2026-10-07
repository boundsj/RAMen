"""Brand: the product is RAMen, in the drawn wordmark and in the text.

The neon wordmark is tube paths from scripts/build-art.py, so a wrong letter
there survives any search of the SVG metadata. These checks read the drawn
letters back from the tracked SVGs, compare them with the manifest name, and
keep the earlier spelling out of the repository except in notes that quote a
historical capture. Standard library only; runs anywhere (no /proc needed).
"""

import importlib.util
import json
import os
import re
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ART = os.path.join(ROOT, "docs", "art")

_spec = importlib.util.spec_from_file_location("build_art", os.path.join(ROOT, "scripts", "build-art.py"))
art = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(art)

with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as _handle:
    MANIFEST = json.load(_handle)
NAME = MANIFEST["barWidget"]["displayName"]

MISSPELLING = "RAM" + "an"  # built so this file does not match itself
# Notes that quote what an unretouched historical capture or upload shows, with
# exact counts so a new occurrence anywhere fails.
HISTORICAL_NOTES = {
    "AGENTS.md": 1,
    "README.md": 2,
    "docs/art/README.md": 3,
    "docs/qa/h3-omarchy-evidence.md": 1,
}
SKIP_DIRS = {".git", ".agent-artifacts", "__pycache__", "node_modules"}
# A wordmark letter: translate(x y) scale(s) translate(advance 0), then its tube path.
LETTER = re.compile(r'<g transform="translate\([^)]*\) scale\([^)]*\) translate\(([\d.]+) 0\)"><path d="([^"]+)"')


def read(name):
    with open(os.path.join(ART, name), encoding="utf-8") as handle:
        return handle.read()


def git(*args):
    return subprocess.run(["git"] + list(args), cwd=ROOT, capture_output=True, check=True).stdout.decode("utf-8")


def repository_files():
    """Tracked and new unignored files when ROOT is a git checkout; otherwise
    (a copy without .git) every file outside SKIP_DIRS."""
    try:
        if os.path.realpath(git("rev-parse", "--show-toplevel").strip()) == os.path.realpath(ROOT):
            listed = git("ls-files", "-z", "-co", "--exclude-standard").split("\0")
            return [name for name in listed if name and os.path.isfile(os.path.join(ROOT, name))]
    except (OSError, subprocess.CalledProcessError):
        pass
    names = []
    for directory, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        names.extend(os.path.relpath(os.path.join(directory, f), ROOT) for f in files)
    return names


class WordmarkTests(unittest.TestCase):
    def test_drawn_wordmark_spells_the_manifest_name(self):
        self.assertEqual(MANIFEST["name"], NAME)
        glyphs = {path: letter for letter, (_, path) in art.LETTERS.items()}
        for name in ("banner.svg", "social-preview.svg"):
            letters = sorted((float(x), d) for x, d in LETTER.findall(read(name)))
            unknown = [d for _, d in letters if d not in glyphs]
            self.assertEqual(unknown, [], "%s draws letters the generator does not have; rebuild it" % name)
            self.assertEqual("".join(glyphs[d] for _, d in letters), NAME, name)

    def test_titles_name_the_product(self):
        for name in ("logo.svg", "banner.svg", "social-preview.svg", "icons.svg"):
            title = re.search(r"<title>([^<]*)</title>", read(name)).group(1)
            self.assertTrue(title.startswith(NAME), "%s title %r" % (name, title))

    def test_tracked_svgs_are_the_generator_output(self):
        built = {"logo.svg": art.logo(), "banner.svg": art.banner(),
                 "social-preview.svg": art.social(), "icons.svg": art.icon_sheet()}
        for name, text in built.items():
            self.assertEqual(read(name), text, "%s is stale: run scripts/build-art.py and scripts/render-art.py" % name)


class SpellingTests(unittest.TestCase):
    def test_earlier_spelling_appears_only_in_historical_notes(self):
        found = {}
        for name in repository_files():
            path = os.path.join(ROOT, name)
            if path == os.path.abspath(__file__):
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            if b"\0" in data:
                continue  # binary (images)
            count = data.decode("utf-8", "replace").count(MISSPELLING)
            if count:
                found[name.replace(os.sep, "/")] = count
        self.assertEqual(found, HISTORICAL_NOTES, "spell the product %s; compatibility names stay lowercase raman" % NAME)


if __name__ == "__main__":
    unittest.main()
