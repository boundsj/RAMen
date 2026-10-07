"""Optional real desktop portal + GIO dispatch fixture (isolated D-Bus/XDG)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_storage import Pipe

base = Path(sys.argv[1])
for name in ('data/applications', 'config', 'cache', 'scope'):
    (base / name).mkdir(parents=True, exist_ok=True)
os.environ.update(XDG_DATA_HOME=str(base/'data'), XDG_CONFIG_HOME=str(base/'config'),
                  XDG_CACHE_HOME=str(base/'cache'), XDG_CURRENT_DESKTOP='GNOME')
report = base/'manager.json'
manager = base/'manager.py'
manager.write_text('import os,sys,json\nfrom urllib.parse import urlparse,unquote\np=unquote(urlparse(sys.argv[1]).path) if sys.argv[1].startswith("file:") else sys.argv[1]\nopen(%r,"w").write(json.dumps({"pid":os.getpid(),"path":p,"exists":os.path.isdir(p),"inode":os.stat(p).st_ino if os.path.isdir(p) else None}))\nimport time; time.sleep(15)\n' % str(report))
(base/'data/applications/raman-test.desktop').write_text('[Desktop Entry]\nType=Application\nName=RAMen Test Manager\nExec=%s %s %%u\nMimeType=inode/directory;\nNoDisplay=true\n' % (sys.executable, manager))
subprocess.run(['update-desktop-database', str(base/'data/applications')], check=True)
(base/'config/mimeapps.list').write_text('[Default Applications]\ninode/directory=raman-test.desktop\n')
# Activations inherit the isolated XDG environment. The GTK implementation is
# the normal system backend, not a success-only replacement for the portal.
subprocess.run(['dbus-update-activation-environment', 'XDG_DATA_HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_CURRENT_DESKTOP', 'DISPLAY'], check=True)
fd = os.open(base/'scope', os.O_RDONLY | os.O_DIRECTORY)
pipe = Pipe(str(base))
manager_pids = []
def send(command, rid, generation=1, **kw):
    pipe.send(dict(command='storage.'+command, requestId=rid, clientId='portal-test', generation=generation, **kw))
try:
    (base/'scope/never-execute').write_text('selected file')
    send('scan', 'scan', path=str(base/'scope'))
    scan = pipe.answer('scan', 'storage-result')
    send('children', 'children', snapshotId=scan['snapshotId'])
    listing = pipe.answer('children', 'storage-children')
    # First the selected file's parent; then the directory itself. Both must
    # reach ordinary GIO desktop dispatch as the scope directory, not its parent.
    for node in (listing['rows'][0]['nodeId'], listing['nodeId']):
        if report.exists(): report.unlink()
        send('action', 'open', snapshotId=scan['snapshotId'], nodeId=node, action='open')
        assert pipe.answer('open', 'storage-action')['status'] == 'opened'
        deadline = time.monotonic()+3
        while not report.exists() and time.monotonic()<deadline: time.sleep(.01)
        result = json.loads(report.read_text()); manager_pids.append(result['pid'])
        assert result['exists'], result
        assert result['inode'] == os.fstat(fd).st_ino, result
        assert not result['path'].startswith('/proc/self/fd/'), result
        print(json.dumps(result))
    send('leave', 'leave', generation=2)
    assert pipe.answer('leave', 'storage-result')['status'] == 'left'
    pipe.close()
    time.sleep(.3)
    for pid in manager_pids: os.kill(pid, 0)  # completed desktop handoffs survive
finally:
    pipe.close()
    for pid in manager_pids:
        try: os.kill(pid, 15)
        except ProcessLookupError: pass
    os.close(fd)
