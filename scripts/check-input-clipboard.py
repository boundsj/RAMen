#!/usr/bin/env python3
"""Finite foreign-owner regression in a disposable, focused Wayland session.

Needs Qt 6 development headers/libs, C/C++, wayland-scanner, libwayland-client,
ext-data-control-v1.xml and wl-copy. Does not install packages or alter plugins.
It temporarily replaces the session clipboard; never run on a working desktop.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def stop(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=1)


def check(stage, name, source, ime=False):
    qml = stage / (name + '.qml')
    qml.write_text(source)
    gui = owner = None
    log = stage / (name + '.log')
    with log.open('w') as output, (stage / (name + '-owner.log')).open('w') as offers:
        try:
            gui = subprocess.Popen([str(stage / 'input-window'), str(qml), 'ime' if ime else 'plain'],
                                   stdout=output, stderr=output, start_new_session=True)
            end = time.monotonic() + 5
            while 'ACTIVE 1' not in log.read_text() and time.monotonic() < end:
                if gui.poll() is not None:
                    raise RuntimeError('native window failed: ' + log.read_text())
                time.sleep(.05)
            if 'PLATFORM wayland ACTIVE 1' not in log.read_text():
                raise RuntimeError('Focus the test window in a disposable Wayland session.')
            owner = subprocess.Popen([str(stage / 'clipboard-offer'), '3500', '1024'],
                                     stdout=offers, stderr=offers, start_new_session=True)
            time.sleep(5)
            if gui.poll() is not None:
                raise RuntimeError('native driver failed: ' + log.read_text())
        finally:
            stop(owner)
            stop(gui)
            subprocess.run(['wl-copy', '--clear'], timeout=2, check=False)
    text = log.read_text()
    beats = [int(n) for n in re.findall(r'BEAT (\d+)', text)]
    if len(beats) < 5 or 'CHANGED' not in text or ime and (
            'IME AND REJECTED-EDIT UNDO OK' not in text or 'MENU OK' not in text
            or 'PASTE UNDO/REDO OK' not in text):
        raise RuntimeError('native coverage incomplete: ' + text)
    return dict(maxGapMs=max(b - a for a, b in zip(beats, beats[1:])), ime=ime,
                platform=re.search(r'PLATFORM (.+)', text)[1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--disposable-session', action='store_true', required=True)
    parser.add_argument('--qt-include', default='/usr/include/qt6')
    parser.add_argument('--protocol', default='/usr/share/wayland-protocols/staging/ext-data-control/ext-data-control-v1.xml')
    args = parser.parse_args()
    artifacts = REPO / '.agent-artifacts' / 'input-clipboard'
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-', dir=artifacts) as temporary:
        stage = Path(temporary)
        for name in ['RamanTextField.qml', 'InputModel.js']:
            shutil.copy2(REPO / name, stage / name)
        subprocess.run(['wayland-scanner', 'client-header', args.protocol, str(stage / 'data-control.h')], check=True)
        subprocess.run(['wayland-scanner', 'private-code', args.protocol, str(stage / 'data-control.c')], check=True)
        subprocess.run(['cc', '-O2', '-I' + str(stage), str(REPO / 'tests/native/clipboard-offer.c'),
                        str(stage / 'data-control.c'), '-lwayland-client', '-o', str(stage / 'clipboard-offer')], check=True)
        include = Path(args.qt_include)
        subprocess.run(['c++', '-fPIC', str(REPO / 'tests/native/input-window.cpp'), '-I' + str(include)]
                       + ['-I' + str(include / part) for part in ['QtCore', 'QtGui', 'QtQuick', 'QtQml']]
                       + ['-lQt6Quick', '-lQt6Qml', '-lQt6Gui', '-lQt6Core', '-o', str(stage / 'input-window')], check=True)
        old = check(stage, 'native-textfield', '''import QtQuick
import QtQuick.Controls
Item { width: 420; height: 180
 TextField { objectName: "testedField"; focus: true; width: 380 }
 Item { visible: false; TextField {} TextField {} }
}
''')
        new = check(stage, 'bounded-field', '''import QtQuick
import QtQuick.Controls
Item { width: 420; height: 180
 QtObject {
  id: clipboard; objectName: "nativeClipboard"
  property int requests: 0
  signal result(var recipient, string text, string error)
  function read(actor, limit, primary) { requests++; return true }
  function cancel(actor) {}
 }
 RamanTextField { objectName: "testedField"; focus: true; width: 380; maximumLength: 8; clipboard: clipboard }
 Item { visible: false; RamanTextField {} RamanTextField {} }
}
''', ime=True)
        report = dict(negativeControl=old, boundedFields=new,
                      negativeControlReproduced=old['maxGapMs'] > 500)
        (artifacts / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        for name in ['native-textfield.log', 'bounded-field.log']:
            shutil.copy2(stage / name, artifacts / name)
        print(json.dumps(report, indent=2))
        if new['maxGapMs'] > 500:
            raise RuntimeError('bounded fields stalled on a foreign clipboard offer')
        if not report['negativeControlReproduced']:
            print('This Qt build did not reproduce the older automatic TextInput read.')


if __name__ == '__main__':
    main()
