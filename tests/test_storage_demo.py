"""Original synthetic Storage fixtures; no source-data or desktop access."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("storage_demo", REPO / "scripts/storage-demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux byte-safe Storage fixtures")
class StorageDemoTests(unittest.TestCase):
    def test_synthetic_scopes_and_byte_safe_names(self):
        artifacts = REPO / ".agent-artifacts/s3/demo-tests"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as directory:
            result = demo.create(directory, 12)
            scopes = result["scopes"]
            board = Path(scopes["board"])
            self.assertTrue(result["synthetic"])
            self.assertEqual(len(list(Path(scopes["cancellation"]).iterdir())), 12)
            self.assertEqual(list(Path(scopes["empty"]).iterdir()), [])
            self.assertEqual(len(list((board / "Spice jars").iterdir())), 100)
            self.assertEqual(os.lstat(board / "Shared greens.bin").st_ino,
                             os.lstat(board / "Market Basket/Greens.bin").st_ino)
            self.assertTrue((board / "Loop label").is_symlink())
            self.assertTrue(os.path.isdir(os.fsencode(board) + b"/byte-\xff"))
            self.assertTrue((board / "two\nlines").is_file())
            sparse = (board / "Sparse pantry.bin").stat()
            self.assertLess(sparse.st_blocks * 512, sparse.st_size)
            self.assertEqual(json.loads((board.parent / "provenance.json").read_text()), result)
            again = demo.create(directory, 0)
            self.assertNotEqual(again["scopes"]["board"], scopes["board"])
            self.assertTrue((board / "Market Basket/Greens.bin").is_file())

    def test_rejects_output_outside_artifacts(self):
        with self.assertRaisesRegex(ValueError, "artifacts"):
            demo.create(REPO / "docs", 0)
