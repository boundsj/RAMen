"""Explicit, bounded clipboard reads. No clipboard observation or persistence."""
import os
import selectors
import shutil
import subprocess
import time

MAX_UNITS = 2048
MAX_BYTES = MAX_UNITS * 4
READ_SECONDS = 1.0


class ClipboardError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read_text(limit, primary=False, seconds=READ_SECONDS):
    if type(limit) is not int or not 1 <= limit <= MAX_UNITS or type(primary) is not bool:
        raise ClipboardError('bad-request', 'invalid clipboard read limit')
    helper = shutil.which('wl-paste')
    if not helper:
        raise ClipboardError('clipboard-unavailable', 'Paste needs wl-paste (wl-clipboard).')
    deadline = time.monotonic() + seconds
    try:
        process = subprocess.Popen([helper, '--no-newline', '--type', 'text'] + (['--primary'] if primary else []),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   close_fds=True, bufsize=0)
    except OSError:
        raise ClipboardError('clipboard-unavailable', 'Clipboard helper could not start.') from None
    try:
        os.set_blocking(process.stdout.fileno(), False)
        data = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ClipboardError('clipboard-timeout', 'Clipboard took too long; type your text instead.')
                block = os.read(process.stdout.fileno(), min(4096, MAX_BYTES + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > MAX_BYTES:
                    raise ClipboardError('clipboard-too-large', 'Clipboard text is too large for this field.')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ClipboardError('clipboard-timeout', 'Clipboard took too long; type your text instead.')
        try:
            status = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ClipboardError('clipboard-timeout', 'Clipboard took too long; type your text instead.') from None
        if status:
            raise ClipboardError('clipboard-unavailable', 'Clipboard has no available text.')
        try:
            text = data.decode('utf-8', errors='strict')
        except UnicodeDecodeError:
            raise ClipboardError('clipboard-invalid', 'Clipboard text is not valid UTF-8.') from None
        if len(text.encode('utf-16-le')) // 2 > limit:
            raise ClipboardError('clipboard-too-large', 'Clipboard text is too long for this field.')
        if any(ord(char) < 32 or ord(char) == 127 or char in '\u2028\u2029' for char in text):
            raise ClipboardError('clipboard-invalid', 'Paste one line without control characters.')
        return text
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass  # The owning controller also reaps this entire worker group.
        process.stdout.close()
