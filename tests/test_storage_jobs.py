"""Progressive inventory transactions, frontier recovery and filesystem safety."""
import base64
from pathlib import Path
import os
import signal
import sys
import tempfile
import time
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
        changed = self.step()
        self.assertEqual(changed['status'], 'ready')
        self.assertEqual(changed['coverage'], 'partial')
        self.assertEqual(changed['changed'], 1)
        saved = J.cached(dict(jobId=self.job_id), self.cache)['snapshot']
        self.assertEqual(saved['entries'], first['entries'])

    def test_changed_directory_does_not_block_other_known_folders(self):
        first = self.scope / 'a'; first.mkdir()
        second = self.scope / 'b'; second.mkdir()
        (second / 'file').write_text('x')
        self.start(); self.step(2)
        (first / 'new').touch()
        result = self.ready()
        self.assertEqual(result['coverage'], 'partial')
        rows = J.children(dict(jobId=self.job_id), self.cache)['rows']
        self.assertEqual(next(r for r in rows if r['name'] == 'a')['reason'], 'changed-directory')
        self.assertEqual(next(r for r in rows if r['name'] == 'b')['coverage'], 'complete')

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

    def test_cache_budget_applies_to_every_worker_and_preserves_checkpoint(self):
        for i in range(500):
            (self.scope / ('f%04d-' % i + 'x' * 180)).touch()
        with patch.object(J, 'MAX_JOB_BYTES', 65536):
            self.start()
            saved = self.step(2)
            paused = self.step(500)
            self.assertEqual(paused['pauseReason'], 'cache-budget')
            self.assertEqual(paused['entries'], saved['entries'])
            self.assertLessEqual(self.cache.safe_stat('job-' + self.job_id + '.sqlite').st_size, 65536)
            self.assertEqual(J.cached(dict(jobId=self.job_id), self.cache)['snapshot']['entries'], saved['entries'])
            self.assertEqual(J.children(dict(jobId=self.job_id), self.cache)['total'], 2)

    def test_interrupted_eviction_is_recovered_by_next_writer(self):
        first = self.start()['snapshotId']
        self.start()
        unlink = os.unlink
        def interrupt(name, **kwargs):
            if name == 'job-' + first + '.sqlite':
                raise OSError('injected eviction interruption')
            return unlink(name, **kwargs)
        with patch.object(J.os, 'unlink', side_effect=interrupt):
            with self.assertRaises(OSError):
                self.start()
        self.assertTrue((self.base / 'raman/storage' / ('job-' + first + '.sqlite')).exists())
        self.start()
        files = list((self.base / 'raman/storage').glob('job-*.sqlite'))
        self.assertEqual(len(files), 2)
        self.assertFalse(any(first in f.name for f in files))

    def test_unicode_payload_shrinks_without_losing_pagination(self):
        parent = self.scope
        for i in range(24):
            parent = parent / ('%02d' % i + '\U0001f35c' * 28)
            parent.mkdir()
        for i in range(65):
            (parent / ('%02d' % i + '\U0001f35c' * 60)).touch()
        self.start(); self.ready()
        node = J.nid(1)
        for _ in range(24):
            node = J.children(dict(jobId=self.job_id, nodeId=node), self.cache)['rows'][0]['nodeId']
        command = dict(jobId=self.job_id, nodeId=node, requestId='\U0001f35c'*64, clientId='\U0001f35c'*64, generation=2**53-1)
        with patch.object(S, 'MAX_LINE_BYTES', 65536):
            result = J.children(command, self.cache)
            self.assertLessEqual(len(S.wire(dict(S.envelope(command, 'storage-children'), **result))), S.MAX_LINE_BYTES)
        self.assertEqual(result['nextOffset'], len(result['rows']))
        self.assertLess(len(result['segments']), S.MAX_SEGMENTS - 1)
        next_page = J.children(dict(command, offset=result['nextOffset']), self.cache)
        self.assertFalse({r['nodeId'] for r in result['rows']} & {r['nodeId'] for r in next_page['rows']})
        self.assertEqual(len(result['rows']) + len(next_page['rows']), 65)

    def test_scoped_cache_searches_both_retained_jobs(self):
        first = self.start()['snapshotId']
        other = self.base / 'other'; other.mkdir()
        J.start(dict(path=str(other)), self.cache)
        self.assertEqual(J.cached(dict(path=str(self.scope)), self.cache)['snapshot']['snapshotId'], first)
        other.rmdir()
        self.assertEqual(J.cached(dict(path=str(self.scope)), self.cache)['snapshot']['snapshotId'], first)

    def test_clipboard_capability_includes_byte_path_ancestors(self):
        raw = os.fsencode(self.scope) + b'/raw-\xff'
        os.mkdir(raw)
        fd = os.open(raw + b'/ascii', os.O_WRONLY | os.O_CREAT, 0o600)
        os.close(fd)
        self.start(); self.ready()
        directory = J.children(dict(jobId=self.job_id), self.cache)['rows'][0]
        self.assertFalse(directory['copyRepresentable'])
        child = J.children(dict(jobId=self.job_id, nodeId=directory['nodeId']), self.cache)['rows'][0]
        self.assertFalse(child['copyRepresentable'])

    def test_populated_sibling_completion_uses_indexed_pending_lookup(self):
        for i in range(100):
            folder = self.scope / str(i); folder.mkdir()
            (folder / 'allocated').write_bytes(b'x'*4096)
        self.start(); complete = self.ready()
        self.assertEqual(complete['coverage'], 'complete')
        with J.inventory(self.cache, self.job_id) as (db, _):
            plan = db.execute('EXPLAIN QUERY PLAN SELECT 1 FROM nodes WHERE parent=1 AND complete=0').fetchall()
        self.assertTrue(any('unfinished' in str(tuple(row)) for row in plan))

    def test_retained_member_queries_use_current_sizes_and_parent_scope(self):
        for i in range(65):
            (self.scope / str(i)).touch()
        (self.scope / 'subfolder').mkdir()
        (self.scope / 'subfolder' / 'private-member').touch()
        self.start(); self.ready()
        first = J.children(dict(jobId=self.job_id), self.cache)
        tail = J.children(dict(jobId=self.job_id, offset=60), self.cache)['rows']
        chosen = tail[0]['nodeId']
        result = J.children(dict(jobId=self.job_id, memberIds=[chosen]), self.cache)
        self.assertEqual(result['members'][0]['nodeId'], chosen)
        folder = next(r for r in first['rows'] if r['name'] == 'subfolder')
        nested = J.children(dict(jobId=self.job_id, nodeId=folder['nodeId']), self.cache)['rows'][0]
        self.assertEqual(J.children(dict(jobId=self.job_id, memberIds=[nested['nodeId']]), self.cache)['members'], [])
        with self.assertRaises(S.StorageError):
            J.children(dict(jobId=self.job_id, memberIds=[chosen]*51), self.cache)

    def test_cache_symlink_sidecar_is_rejected(self):
        self.start()
        os.symlink(self.scope / 'unrelated', self.cache.path('job-' + self.job_id + '.sqlite-wal'))
        with self.assertRaises(S.StorageError) as caught:
            J.cached(dict(jobId=self.job_id), self.cache)
        self.assertEqual(caught.exception.code, 'unsafe-cache')

    def pump(self, controller, predicate, seconds=5):
        replies = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            replies.extend(controller.poll())
            if predicate(replies):
                return replies
            time.sleep(0.005)
        self.fail('owned worker did not reach expected condition')

    def test_failed_job_action_reaps_helper_descendants(self):
        self.start()
        self.ready()
        marker = self.base / 'helper-pid'
        helper = self.base / 'wl-copy'
        helper.write_text('#!/usr/bin/env python3\nimport os,time\n'
                          'if os.fork()==0:\n open(%r,"w").write(str(os.getpid()));time.sleep(30)\n'
                          'time.sleep(0.1)\nraise SystemExit(7)\n' % str(marker))
        helper.chmod(0o700)
        with patch.dict(os.environ, XDG_CACHE_HOME=str(self.base), PATH=str(self.base) + ':' + os.environ['PATH']):
            controller = S.Controller()
            self.addCleanup(controller.close)
            controller.handle(dict(command='storage.job.action', jobId=self.job_id, nodeId=J.nid(1),
                                   action='copy', requestId='action', clientId='test', generation=1))
            replies = self.pump(controller, lambda r: any(m.get('type') == 'error' for m in r) and not controller.jobs)
        self.assertTrue(any(m.get('code') == 'action-unavailable' for m in replies))
        child = int(marker.read_text())
        def stopped():
            try:
                return Path('/proc/%d/stat' % child).read_text().split(') ')[1].startswith('Z')
            except FileNotFoundError:
                return True
        deadline = time.monotonic() + 2
        while not stopped() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not stopped():
            os.kill(child, signal.SIGKILL)
            self.fail('failed clipboard helper left a running descendant')

    def test_owner_close_rolls_back_inflight_slice_and_keeps_checkpoint(self):
        for i in range(20):
            (self.scope / str(i)).touch()
        self.start(); saved = self.step()
        os.close(self.cache.lock_fd); self.cache.lock_fd = None
        marker = self.base / 'worker-started'
        source = '''import contextlib,time,raman_storage as s,raman_storage_jobs as j
original=j.directory_entries
@contextlib.contextmanager
def blocked(fd,cookie):
 with original(fd,cookie) as entries:
  def slow():
   for i,entry in enumerate(entries):
    if i==2:
     open(%r,'w').write('started');time.sleep(30)
    yield entry
  yield slow()
j.directory_entries=blocked
s.worker_main()
''' % str(marker)
        with patch.dict(os.environ, XDG_CACHE_HOME=str(self.base)):
            controller = S.Controller([sys.executable, '-S', '-c', source])
            controller.handle(dict(command='storage.step', jobId=self.job_id, requestId='step', clientId='test', generation=1))
            self.pump(controller, lambda _: marker.exists())
            processes = [job['process'] for job in controller.jobs.values()]
            controller.close()
        self.assertTrue(all(p.poll() is not None for p in processes))
        checkpoint = J.cached(dict(jobId=self.job_id), self.cache)['snapshot']
        self.assertEqual(checkpoint['entries'], saved['entries'])
        self.assertEqual(self.ready()['entries'], 21)


class MainStorageTests(unittest.TestCase):
    def plan(self, records, primary):
        mounts = [dict(id=i, point=os.fsencode(path), root=base64.b64decode(record['mountRoot']),
                       type='btrfs', source=b'/dev/test', device='0:1')
                  for i, (path, record) in enumerate(records.items())]
        def record(path, _):
            return primary if path == b'/home/test' else records[os.fsdecode(path)]
        with patch.object(S, 'read_mounts', return_value=mounts), patch.object(J, 'root_record', side_effect=record), patch.object(S, 'capacities', return_value=dict(totalBytes=123)) as capacity:
            result = J.location_plan('/home/test')
            capacity.assert_called_once()
            return result

    def record(self, path, subvolume, root='/', snapshot=False, pool='primary'):
        return dict(path=path, pathBytes=J.encoded(os.fsencode(path)), identity=dict(filesystemId=subvolume),
                    pool=[pool], domain=[pool, subvolume], snapshot=snapshot, mountRoot=J.encoded(os.fsencode(root)))

    def test_primary_btrfs_subvolumes_share_capacity_and_skip_aliases_snapshots_other_drives(self):
        records = {'/': self.record('/', 1), '/home': self.record('/home', 2),
                   '/alias': self.record('/alias', 2, '/test'),
                   '/snapshot': self.record('/snapshot', 3, snapshot=True),
                   '/other': self.record('/other', 4, pool='other')}
        result = self.plan(records, records['/home'])
        self.assertEqual({r['path'] for r in result['roots']}, {'/', '/home'})
        self.assertEqual(result['skipped'], [dict(path='/snapshot', reason='snapshot')])
        self.assertEqual(result['capacity']['totalBytes'], 123)

    def test_snapshot_only_fallback_is_refused(self):
        record = self.record('/snapshot', 1, snapshot=True)
        with self.assertRaises(S.StorageError) as caught:
            self.plan({'/snapshot': record}, record)
        self.assertEqual(caught.exception.code, 'no-eligible-roots')
