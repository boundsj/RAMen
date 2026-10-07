#!/usr/bin/env python3
"""Create original, synthetic RAMen Storage scopes; never touch source data.

Choose the printed scopes in the real widget and explicitly Scan. This is a
fixture generator, not a screenshot, fake snapshot, or live-shell QA result.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]


def create(output, fanout=2000):
    output = Path(output).resolve()
    artifacts = REPO / ".agent-artifacts"
    if not output.is_relative_to(artifacts.resolve()):
        raise ValueError("output must stay beneath repository .agent-artifacts")
    output.mkdir(parents=True, exist_ok=True)
    # A fresh owned directory avoids overwriting previous captures or fixtures.
    root = Path(tempfile.mkdtemp(prefix="synthetic-", dir=output))
    board = root / "Serving Board"
    board.mkdir()
    for directory, files in {
        "Noodle Workshop": {"Broth batch.bin": 3 * 1024 * 1024, "Fresh noodles.bin": 1024 * 1024},
        "Market Basket": {"Mushrooms.bin": 512 * 1024, "Greens.bin": 128 * 1024},
        "Recipe Archive/Seasonal": {"Autumn broth.bin": 256 * 1024},
    }.items():
        folder = board / directory
        folder.mkdir(parents=True)
        for name, count in files.items():
            (folder / name).write_bytes(b"R" * count)
    (board / "Empty bowl").mkdir()
    (board / ".hidden recipe").write_bytes(b"synthetic hidden metadata\n")
    (board / "雪 noodles; $(echo harmless)").write_bytes(b"synthetic name\n")
    (board / "two\nlines").write_bytes(b"synthetic newline name\n")
    with (board / "Sparse pantry.bin").open("wb") as handle:
        handle.seek(64 * 1024 * 1024)
        handle.write(b"R")
    os.link(board / "Market Basket/Greens.bin", board / "Shared greens.bin")
    os.symlink(".", board / "Loop label")
    os.mkdir(os.fsencode(board) + b"/byte-\xff")
    small = board / "Spice jars"
    small.mkdir()
    for index in range(99):
        (small / ("Jar %03d.bin" % index)).write_bytes(b"R" * (index + 1))
    (small / "Bulk spice.bin").write_bytes(b"R" * 1024 * 1024)
    empty = root / "Empty Serving Board"
    empty.mkdir()
    wide = root / "Cancellation Kitchen"
    wide.mkdir()
    for index in range(fanout):
        (wide / ("Station %06d" % index)).mkdir()
    result = dict(synthetic=True, source="scripts/storage-demo.py", scopes={
        "board": str(board), "empty": str(empty), "cancellation": str(wide)},
        fanout=fanout, notes=["Allocation depends on the actual filesystem; no invented totals.",
                             "All content and names are original synthetic fixtures.",
                             "No real scans, desktop actions, screenshots or acceptance performed.",
                             "Partial/remount/permission failures require separate injected or native QA."])
    (root / "provenance.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=".agent-artifacts/s3/demo")
    parser.add_argument("--fanout", type=int, default=2000)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Linux is required for byte-safe fixture names and Storage scans")
    if not 0 <= args.fanout <= 150000:
        parser.error("fanout must be 0..150000")
    try:
        result = create(REPO / args.output, args.fanout)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
