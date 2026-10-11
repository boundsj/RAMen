"""Owned helper fixtures: byte/unit caps, total deadline and worker cleanup."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import raman_clipboard as clipboard
import raman_storage as storage


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux worker ownership and pipe selectors')
class ClipboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.helper = self.root / 'wl-paste'
        self.env = patch.dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'],
                              XDG_STATE_HOME=str(self.root / 'state'), XDG_CACHE_HOME=str(self.root / 'cache'))
        self.env.start()
        self.addCleanup(self.env.stop)

    def script(self, code):
        self.helper.write_text('#!' + sys.executable + '\nimport os,sys,time\n' + code + '\n')
        self.helper.chmod(0o700)

    def refused(self, code, limit=2048, seconds=0.15):
        with self.assertRaises(clipboard.ClipboardError) as caught:
            clipboard.read_text(limit, seconds=seconds)
        self.assertEqual(caught.exception.code, code)

    def test_unicode_and_explicit_primary(self):
        self.script("assert sys.argv[1:] == ['--no-newline','--type','text','--primary']; os.write(1,'雪 🍜'.encode())")
        self.assertEqual(clipboard.read_text(4, True), '雪 🍜')
        self.script("os.write(1,'雪 🍜'.encode())")
        self.refused('clipboard-too-large', limit=3)

    def test_byte_cap_and_whole_field_refusal(self):
        self.script("os.write(1,b'a'*8193); time.sleep(2)")
        self.refused('clipboard-too-large')
        self.script("os.write(1,b'a'*129)")
        self.refused('clipboard-too-large', limit=128)
        self.script("os.write(1,b'a'*2048)")
        self.assertEqual(len(clipboard.read_text(2048)), 2048)

    def test_deadline_is_total_even_when_owner_drips_bytes(self):
        self.script("\nfor n in range(100):\n os.write(1,b'a'); time.sleep(.02)")
        began = time.monotonic()
        self.refused('clipboard-timeout')
        self.assertLess(time.monotonic() - began, .6)
        self.script("time.sleep(2)")
        self.refused('clipboard-timeout')
        self.script("os.close(1); time.sleep(2)")
        self.refused('clipboard-timeout')

    def test_single_line_utf8_and_errors(self):
        for payload in [b'\xff', b'line\nline', b'\x00', b'\t', '\u2028'.encode(), '\u2029'.encode()]:
            self.script('os.write(1,' + repr(payload) + ')')
            self.refused('clipboard-invalid')
        self.script('sys.exit(1)')
        self.refused('clipboard-unavailable')
        with patch.object(clipboard.shutil, 'which', return_value=None):
            self.refused('clipboard-unavailable')
        for limit in [True, 0, -1, 2049, 1.5]:
            self.refused('bad-request', limit=limit)

    def test_worker_result_does_not_create_storage_cache(self):
        self.script("os.write(1,b'plain')")
        controller = storage.Controller()
        self.addCleanup(controller.close)
        command = dict(command='input.paste', requestId='p:test:1', clientId='paste', generation=1, limit=128)
        self.assertEqual(controller.handle(command)['status'], 'queued')
        replies = self.finish(controller)
        self.assertEqual(replies[0]['type'], 'input-result')
        self.assertEqual(replies[0]['text'], 'plain')
        self.assertEqual(replies[0]['requestId'], 'p:test:1')
        self.assertFalse((self.root / 'cache').exists())
        self.assertFalse((self.root / 'state').exists())

    def finish(self, controller):
        end = time.monotonic() + 4
        replies = []
        while time.monotonic() < end:
            replies.extend(controller.poll())
            if not controller.jobs and not controller.pending:
                return replies
            time.sleep(.01)
        self.fail('owned workers did not finish')

    def test_cancel_stops_helper_descendant_and_drops_late_reply(self):
        pid_path = self.root / 'helper-pid'
        self.script("open(" + repr(str(pid_path)) + ",'w').write(str(os.getpid())); time.sleep(30)")
        controller = storage.Controller()
        self.addCleanup(controller.close)
        controller.handle(dict(command='input.paste', requestId='p:test:1', clientId='paste', generation=1, limit=128))
        end = time.monotonic() + 2
        while not pid_path.exists() and time.monotonic() < end:
            controller.poll(); time.sleep(.01)
        self.assertTrue(pid_path.exists())
        pid = int(pid_path.read_text())
        reply = controller.handle(dict(command='input.cancel', requestId='p:test:2', clientId='paste', generation=2))
        self.assertEqual(reply['type'], 'input-result')
        self.assertFalse(any(r.get('requestId') == 'p:test:1' for r in self.finish(controller)))
        # A killed orphan may remain a zombie until init reaps it; it cannot run.
        stat = Path('/proc') / str(pid) / 'stat'
        self.assertTrue(not stat.exists() or stat.read_text().split(') ')[1][0] == 'Z')

    def test_invalid_request_never_launches(self):
        controller = storage.Controller()
        self.addCleanup(controller.close)
        for bad in [dict(limit=True), dict(limit=0), dict(limit=2049), dict(limit=3, primary=1)]:
            reply = controller.handle(dict(command='input.paste', requestId='p:t:1', clientId='paste', generation=1, **bad))
            self.assertEqual(reply['code'], 'bad-request')
        self.assertFalse(controller.jobs)
        self.assertFalse(controller.pending)
