"""Actual FD transfer and normal portal/GIO dispatch, when optional tools exist."""
import os
import ctypes.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from raman_portal import PortalError, open_directory


class PortalTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux desktop portal')
    def test_file_descriptor_is_never_launched(self):
        with tempfile.TemporaryFile() as handle:
            with self.assertRaises(PortalError): open_directory(handle.fileno())

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('dbus-run-session')
                         and shutil.which('xvfb-run') and shutil.which('update-desktop-database')
                         and os.path.exists('/usr/libexec/xdg-desktop-portal-gtk'), 'optional real desktop portal tools')
    def test_real_portal_and_gio_directory_dispatch(self):
        artifact = Path('.agent-artifacts/s2/fixes-1'); artifact.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifact) as directory:
            result = subprocess.run(['xvfb-run', '-a', 'dbus-run-session', '--', sys.executable,
                                     str(Path(__file__).with_name('portal_dispatch_fixture.py').resolve()),
                                     str(Path(directory).resolve())], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print('Real portal/GIO dispatch:', result.stdout.strip())

    @unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('dbus-run-session')
                         and ctypes.util.find_library('gio-2.0'), 'optional system D-Bus/GIO tools')
    def test_receipt_is_not_success_and_unfinished_requests_close(self):
        for mode in ('reject', 'timeout', 'cancel'):
            with self.subTest(mode=mode):
                result = subprocess.run(['dbus-run-session', '--', sys.executable,
                                         str(Path(__file__).with_name('portal_response_fixture.py').resolve()), mode],
                                        capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
