"""Nonrecursive discovery remains usable before an inventory exists."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import raman_storage as storage


class BrowseTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[1] / '.agent-artifacts' / 'storage-browse-tests'
        artifacts.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temp.cleanup)
        self.path = os.fsencode(self.temp.name)
        info = os.stat(self.path)
        self.ident = dict(device=info.st_dev, inode=info.st_ino, mountId=1)
        self.mounts = [dict(id=1, point=b'/', type='ext4')]
        for target, value in [('read_mounts', self.mounts), ('identity', self.ident), ('fd_mount_id', 1)]:
            mocked = patch.object(storage, target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_names_are_not_sizes_and_links_are_not_traversed(self):
        os.mkdir(self.path + b'/Documents')
        os.mkdir(self.path + b'/.hidden')
        os.mkdir(self.path + b'/raw-\xff')
        os.symlink('Documents', self.path + b'/link')
        Path(self.temp.name, 'file').write_text('x')
        result = storage.browse(dict(path=self.temp.name))
        self.assertFalse(result['measured'])
        self.assertEqual({r['name'] for r in result['rows']}, {'Documents', '.hidden', 'raw-\\xff'})
        self.assertTrue(next(r for r in result['rows'] if r['name'] == '.hidden')['hidden'])
        for row in result['rows']:
            self.assertNotIn('knownAllocatedBytes', row)
            self.assertTrue(base64.b64decode(row['pathBytes']).startswith(self.path + b'/'))
        self.assertIsNone(result['nextOffset'])

    def test_paging_does_not_skip_directories(self):
        for i in range(19):
            os.mkdir(self.path + ('/folder-%d' % i).encode())
        found, offset = [], 0
        while offset is not None:
            result = storage.browse(dict(path=self.temp.name, offset=offset, limit=3))
            found += [r['name'] for r in result['rows']]
            offset = result['nextOffset']
        self.assertEqual(len(found), 19)
        self.assertEqual(len(set(found)), 19)

    def test_changed_root_refuses_labels(self):
        with patch.object(storage, 'identity', side_effect=[self.ident, dict(self.ident, inode=0)]):
            with self.assertRaises(storage.StorageError) as caught:
                storage.browse(dict(path=self.temp.name))
        self.assertEqual(caught.exception.code, 'root-changed')

    def test_invalid_offset(self):
        for offset in [-1, True, '0']:
            with self.assertRaises(storage.StorageError):
                storage.browse(dict(path=self.temp.name, offset=offset))
