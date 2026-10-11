"""Progressive inventory transactions, frontier recovery and filesystem safety."""
import base64
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import raman_storage as S
import raman_storage_jobs as J


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux mount identity and directory cookies')
class JobTests(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[1] / '.agent-artifacts' / 'storage-job-tests'
        artifacts.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.scope = self.base / 'fixture'
        self.scope.mkdir()
        self.cache = S.Cache(str(self.base / 'raman' / 'storage'))
        self.addCleanup(self.cache.close)

    def start(self):
        result = J.start(dict(path=str(self.scope)), self.cache)
        self.job_id = result['snapshot']['snapshotId']
        return result['snapshot']

    def step(self, entries=3):
        return J.step(dict(jobId=self.job_id), self.cache, dict(entries=entries, seconds=5))['snapshot']

    def ready(self):
        for _ in range(100):
            result = self.step()
            if result['status'] == 'ready':
                return result
        self.fail('scan did not finish')

    def test_pause_resume_does_not_rescan_or_lose_rows(self):
        for i in range(19):
            (self.scope / ('file-%d' % i)).write_bytes(b'x' * (4096 * (i + 1)))
        self.start()
        first = self.step()
        self.assertEqual(first['entries'], 4)
        self.assertEqual(first['pendingFolders'], 1)
        # A new worker/restart sees the same partial inventory and cookie.
        checkpoint = J.cached(dict(jobId=self.job_id), self.cache)['snapshot']
        self.assertEqual(checkpoint['knownAllocatedBytes'], first['knownAllocatedBytes'])
        result = self.ready()
        self.assertEqual(result['entries'], 20)
        self.assertEqual(result['coverage'], 'complete')
        allocated = sum(os.lstat(self.scope / name).st_blocks * 512 for name in os.listdir(self.scope)) + os.lstat(self.scope).st_blocks * 512
        self.assertEqual(result['knownAllocatedBytes'], allocated)
        rows = J.children(dict(jobId=self.job_id), self.cache)
        self.assertEqual(rows['total'], 19)
        self.assertEqual(len({r['nodeId'] for r in rows['rows']}), 19)

    def test_completed_and_pending_folders_are_distinct(self):
        (self.scope / 'empty').mkdir()
        (self.scope / 'stuff').mkdir()
        (self.scope / 'stuff' / 'one').write_text('hello')
        self.start()
        first = self.step(2)
        self.assertEqual(first['coverage'], 'partial')
        self.ready()
        rows = J.children(dict(jobId=self.job_id), self.cache)['rows']
        self.assertTrue(all(not r['pending'] and r['coverage'] == 'complete' for r in rows))

    def test_symlinks_not_followed_and_hardlinks_count_once(self):
        (self.scope / 'original').write_bytes(b'x' * 4096)
        os.link(self.scope / 'original', self.scope / 'alias')
        os.symlink(self.base, self.scope / 'loop')
        self.start()
        result = self.ready()
        expected = os.lstat(self.scope).st_blocks * 512 + os.lstat(self.scope / 'original').st_blocks * 512 + os.lstat(self.scope / 'loop').st_blocks * 512
        self.assertEqual(result['knownAllocatedBytes'], expected)
        self.assertEqual(result['entries'], 4)
        rows = J.children(dict(jobId=self.job_id), self.cache)['rows']
        self.assertEqual(sum(r['sharedLink'] for r in rows), 1)

    def test_changed_frontier_refuses_old_cookie_and_preserves_checkpoint(self):
        for i in range(7):
            (self.scope / str(i)).write_text('x')
        self.start(); first = self.step()
        (self.scope / 'new').write_text('new')
        with self.assertRaises(S.StorageError) as caught:
            self.step()
        self.assertEqual(caught.exception.code, 'node-changed')
        saved = J.cached(dict(jobId=self.job_id), self.cache)['snapshot']
        self.assertEqual(saved['entries'], first['entries'])

    def test_rescan_retains_previous_job_and_replaces_only_third(self):
        first = self.start()['snapshotId']; self.ready()
        second = self.start()['snapshotId']; self.ready()
        self.assertEqual(J.cached(dict(jobId=first), self.cache)['snapshot']['snapshotId'], first)
        third = self.start()['snapshotId']
        self.assertEqual(self.cache.read_json('jobs.json')['ids'], [third, second])
        with self.assertRaises(S.StorageError):
            J.cached(dict(jobId=first), self.cache)

    def test_filter_is_literal_and_paging_is_bounded(self):
        for i in range(11):
            (self.scope / ('a_%%%d' % i)).write_text('x')
        self.start(); self.ready()
        result = J.children(dict(jobId=self.job_id, filter='_%', limit=3), self.cache)
        self.assertEqual(len(result['rows']), 3)
        self.assertEqual(result['total'], 11)
        self.assertEqual(result['nextOffset'], 3)
        self.assertLess(len(S.wire(result)), S.MAX_LINE_BYTES)
