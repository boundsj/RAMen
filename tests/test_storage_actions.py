"""Live path actions use byte identity, pinned directories and fixed argv."""
import base64
import json
import os
import subprocess
import sys
import sqlite3
import unittest
from unittest.mock import patch
from test_storage import FilesystemTests, ProtocolTests
import raman_storage as storage

# Reuse fixtures, without inheriting their full test collections.
@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux descriptor identity')
class ActionTests(unittest.TestCase):
    setUp = FilesystemTests.setUp
    make_file = FilesystemTests.make_file
    build = FilesystemTests.build
    query = FilesystemTests.query

    def command(self, metadata, node, operation):
        return dict(snapshotId=metadata['snapshotId'], nodeId=node, action=operation)

    def test_copy_bytes_and_file_parent_pinned_open(self):
        path = self.make_file('$(touch BAD); odd\n雪')
        meta = self.build()
        rows = self.query(meta)['rows']
        command = self.command(meta, rows[0]['nodeId'], 'copy')
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            result = storage.action(command, self.cache)
            self.assertEqual(result['status'], 'copied')
            self.assertEqual(run.call_args.args[0], ['wl-copy', '--type', 'text/plain;charset=utf-8'])
            self.assertEqual(run.call_args.kwargs['input'], path)
        def opened(fd):
            self.assertTrue(os.path.isdir('/proc/self/fd/%d' % fd))
            self.assertEqual(os.fstat(fd).st_ino, os.stat(self.root).st_ino)
        with patch('raman_portal.open_directory', side_effect=opened):
            self.assertEqual(storage.action(dict(command, action='open'), self.cache)['status'], 'opened')

    def test_portal_failure_is_not_open_success(self):
        from raman_portal import PortalError
        meta = self.build()
        with patch('raman_portal.open_directory', side_effect=PortalError('portal denied')):
            with self.assertRaises(storage.StorageError) as caught:
                storage.action(self.command(meta, meta['rootNodeId'], 'open'), self.cache)
            self.assertEqual(caught.exception.code, 'action-unavailable')

    def test_changed_node_and_symlink_ancestor_fail_closed(self):
        folder = os.path.join(self.root, b'folder'); os.mkdir(folder)
        self.make_file(b'folder/file')
        meta = self.build()
        directory = self.query(meta)['rows'][0]
        child = self.query(meta, nodeId=directory['nodeId'])['rows'][0]
        os.rename(folder, folder + b'-old')
        os.symlink(folder + b'-old', folder)
        with patch.object(subprocess, 'run') as run:
            with self.assertRaises(storage.StorageError) as caught:
                storage.action(self.command(meta, child['nodeId'], 'open'), self.cache)
            self.assertEqual(caught.exception.code, 'node-changed')
            run.assert_not_called()

    def test_non_utf8_clipboard_capability(self):
        self.make_file(b'raw-\xff')
        meta = self.build(); child = self.query(meta)['rows'][0]
        with patch.object(subprocess, 'run') as run:
            with self.assertRaises(storage.StorageError) as caught:
                storage.action(self.command(meta, child['nodeId'], 'copy'), self.cache)
            self.assertEqual(caught.exception.code, 'action-unavailable')
            run.assert_not_called()
        self.assertEqual(self.query(meta)['rows'][0]['nodeId'], child['nodeId'])

    def test_legacy_inventory_remains_browsable_without_actions(self):
        scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
        try:
            meta = scanner.prepare()
            with sqlite3.connect(self.cache.path(scanner.temp)) as db:
                db.execute("DROP TABLE action_identity")
            scanner.activate(meta)
        finally:
            scanner.close()
        rows = self.query(meta)
        self.assertFalse(rows['node']['actionIdentity'])
        with self.assertRaises(storage.StorageError) as caught:
            storage.action(self.command(meta, meta['rootNodeId'], 'open'), self.cache)
        self.assertEqual(caught.exception.code, 'action-unavailable')

    def test_unknown_action_and_snapshot_are_not_authority(self):
        meta = self.build()
        with self.assertRaises(storage.StorageError):
            storage.action(self.command(meta, 'f'*32, 'copy'), self.cache)
        with self.assertRaises(storage.StorageError):
            storage.action(self.command(meta, meta['rootNodeId'], 'delete'), self.cache)

@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux real probe pipe')
class ActionPipeTests(unittest.TestCase):
    setUp = ProtocolTests.setUp
    command = ProtocolTests.command
    scan = ProtocolTests.scan

    def test_pipe_actions_and_byte_scope(self):
        # A real test-owned helper records raw stdin; no desktop clipboard access.
        helpers = os.path.join(self.temp.name, 'helpers'); os.mkdir(helpers)
        log = os.path.join(self.temp.name, 'copied')
        helper = os.path.join(helpers, 'wl-copy')
        with open(helper, 'w') as handle:
            handle.write('#!/usr/bin/env python3\nimport sys\nopen(%r,"wb").write(sys.stdin.buffer.read())\n' % log)
        os.chmod(helper, 0o700)
        # Restart the owned probe so it inherits only the test helper PATH.
        from test_storage import Pipe
        self.pipe.close()
        with patch.dict(os.environ, PATH=helpers + os.pathsep + os.environ['PATH']):
            self.pipe = Pipe(self.temp.name)
        self.addCleanup(self.pipe.close)
        path = os.path.join(self.root, 'odd $(echo BAD)\nfile')
        with open(path, 'wb') as handle: handle.write(b'hello')
        ready = self.scan()
        self.command('children', 'children', snapshotId=ready['snapshotId'])
        children = self.pipe.answer('children', 'storage-children')
        node = children['rows'][0]['nodeId']
        self.command('action', 'copy', snapshotId=ready['snapshotId'], nodeId=node, action='copy')
        self.assertEqual(self.pipe.answer('copy', 'storage-action')['status'], 'copied')
        with open(log, 'rb') as handle: self.assertEqual(handle.read(), os.fsencode(path))
        self.command('cached', 'cached', pathBytes=base64.b64encode(os.fsencode(self.root)).decode('ascii'))
        self.assertEqual(self.pipe.answer('cached', 'storage-result')['snapshotId'], ready['snapshotId'])
        self.command('action', 'stale', generation=0, snapshotId=ready['snapshotId'], nodeId=node, action='copy')
        self.assertEqual(self.pipe.answer('stale', 'error')['code'], 'stale-generation')

del FilesystemTests, ProtocolTests

@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux owned process groups')
class ActionLifetimeTests(unittest.TestCase):
    setUp = ActionPipeTests.setUp
    command = ActionPipeTests.command
    scan = ActionPipeTests.scan

    def test_unfinished_helper_descendants_leave_eof_and_owner_death(self):
        import time
        import signal
        from test_storage import Pipe
        helpers = os.path.join(self.temp.name, 'helpers'); os.mkdir(helpers)
        pidfile = os.path.join(self.temp.name, 'child-pid')
        # The leader exits on TERM; its child deliberately ignores TERM so the
        # controller must retain the PGID anchor and escalate after 200 ms.
        script = ('import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                  'open(%r,"w").write(str(os.getpid())); time.sleep(30)' % pidfile)
        with open(os.path.join(helpers, 'wl-copy'), 'w') as handle:
            handle.write('#!%s\nimport subprocess,sys\nsubprocess.run([sys.executable,"-c",%r])\n' % (sys.executable, script))
        os.chmod(os.path.join(helpers, 'wl-copy'), 0o700)
        unrelated = subprocess.Popen(['sleep', '30'])
        self.addCleanup(lambda: unrelated.wait() if unrelated.poll() is not None else (unrelated.kill(), unrelated.wait()))
        for stop in ('leave', 'eof', 'owner-death', 'failure', 'timeout'):
            with self.subTest(stop=stop):
                self.pipe.close()
                if stop == 'failure':
                    with open(os.path.join(helpers, 'wl-copy'), 'w') as handle:
                        handle.write('#!%s\nimport subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",%r]); time.sleep(.05); sys.exit(1)\n' % (sys.executable, script))
                elif stop == 'timeout':
                    with open(os.path.join(helpers, 'wl-copy'), 'w') as handle:
                        handle.write('#!%s\nimport subprocess,sys\nsubprocess.run([sys.executable,"-c",%r])\n' % (sys.executable, script))
                if os.path.exists(pidfile): os.unlink(pidfile)
                with patch.dict(os.environ, PATH=helpers + os.pathsep + os.environ['PATH']):
                    self.pipe = Pipe(self.temp.name)
                self.addCleanup(self.pipe.close)
                ready = self.scan()
                self.command('action', 'copy', snapshotId=ready['snapshotId'], nodeId=ready['snapshot']['rootNodeId'], action='copy')
                deadline = time.monotonic() + 3
                while not os.path.exists(pidfile) and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(os.path.exists(pidfile))
                with open(pidfile) as handle: pid = int(handle.read())
                self.addCleanup(lambda pid=pid: os.kill(pid, signal.SIGKILL) if alive(pid) else None)
                started = time.monotonic()
                if stop == 'leave':
                    self.command('leave', 'leave', generation=2)
                    self.assertEqual(self.pipe.answer('leave', 'storage-result')['status'], 'left')
                elif stop == 'eof': self.pipe.close()
                elif stop in ('failure', 'timeout'):
                    self.assertEqual(self.pipe.answer('copy', 'error')['code'], 'action-unavailable')
                    started = time.monotonic()
                else:
                    # Kill the waiting probe parent and worker to test storage's
                    # owner watchdog, without relying on controller.close().
                    with open('/proc/%d/task/%d/children' % (self.pipe.p.pid, self.pipe.p.pid)) as handle:
                        children = handle.read().split()
                    self.pipe.p.kill()
                    for child in children: os.kill(int(child), signal.SIGKILL)
                while alive(pid) and time.monotonic() - started < 1: time.sleep(.01)
                self.assertFalse(alive(pid), 'owned action child survived ' + stop)
                self.assertIsNone(unrelated.poll(), 'cleanup affected unrelated process')


def alive(pid):
    try:
        with open('/proc/%d/stat' % pid) as handle: return handle.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except (FileNotFoundError, ProcessLookupError): return False  # reaped mid-read
