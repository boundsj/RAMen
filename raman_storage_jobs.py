"""Progressive, explicitly requested storage inventories, in bounded workers.

The mutable inventory is a private SQLite transaction log and directory frontier.
Each step commits useful results and a Linux directory cookie together. Nothing
walks without an explicit start/continue; the QML runtime owns continuation.
"""
import base64
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
import uuid

import raman_storage as S

JOB_SCHEMA = 1
MAX_JOB_BYTES = 240 * 1024 * 1024
MAX_JOB_ENTRIES = 10_000_000
MAX_ROOTS = 24
STEP_ENTRIES = 5000
STEP_SECONDS = 1.5
KINDS = ('unknown', 'directory', 'file', 'symlink', 'special')


def encoded(path):
    return base64.b64encode(path).decode('ascii')


def btrfs_info(path):
    """Linux UAPI FS_INFO + GET_SUBVOL_INFO, no btrfs command or privileges."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fs = bytearray(1024)
        sub = bytearray(504)
        S.fcntl.ioctl(fd, 0x8400941f, fs, True)
        S.fcntl.ioctl(fd, 0x81f8943c, sub, True)
        return fs[16:32].hex(), int.from_bytes(sub[:8], sys.byteorder), any(sub[312:328])
    finally:
        os.close(fd)


def root_record(path, mounts):
    ident = S.identity(path, mounts)
    mount = next(m for m in mounts if m['id'] == ident['mountId'])
    if ident['filesystem'] == 'btrfs':
        fs_uuid, sub_id, snapshot = btrfs_info(path)
        # Do not confuse clones with the same on-disk UUID. Mount source is a
        # conservative additional guard; ordinary Home/System mounts agree.
        pool = ['btrfs', fs_uuid, encoded(os.path.realpath(mount['source']))]
        domain = pool + [sub_id]
    else:
        pool = [ident['filesystem'], ident['filesystemId'], mount['device']]
        domain, snapshot = pool, False
    return dict(pathBytes=encoded(path), path=S.display(path), identity=ident,
                pool=pool, domain=domain, snapshot=snapshot, mountRoot=encoded(mount['root']))


def location_plan(home=None):
    home = S.unpack_path(home or os.path.expanduser('~'))
    mounts = S.read_mounts()
    primary = root_record(home, mounts)
    candidates, skipped = [], []
    for mount in sorted(mounts, key=lambda m: (len(m['root']), len(m['point']), m['id'])):
        if S.exclusion(mount):
            continue
        try:
            record = root_record(mount['point'], mounts)
        except (OSError, S.StorageError):
            continue
        if record['pool'] != primary['pool']:
            continue
        if record['snapshot']:
            skipped.append(dict(path=record['path'], reason='snapshot'))
            continue
        # Bind aliases and nested views of the same subvolume/filesystem are
        # covered by their widest mounted root. Separate subvolumes are kept.
        root = base64.b64decode(record['mountRoot'])
        if any(r['domain'] == record['domain'] and S.contains(base64.b64decode(r['mountRoot']), root) for r in candidates):
            continue
        if len(candidates) >= MAX_ROOTS:
            raise S.StorageError('mount-limit', 'Main storage has too many mounted roots; choose a folder')
        candidates.append(record)
    if not candidates:
        candidates = [primary]
    return dict(label='Main storage', roots=candidates, skipped=skipped[:S.MAX_WARNINGS],
                capacityPathBytes=encoded(home), capacity=S.capacities(dict(path=os.fsdecode(home))),
                note='Mounted folders on the filesystem containing Home. Other drives, network mounts, snapshots and unreadable content are not included.')


def nid(value):
    return '%032x' % value if value is not None else None


def number(value):
    if not isinstance(value, str) or not S.ID_RE.fullmatch(value):
        raise S.StorageError('bad-request', 'nodeId must be a stored ID')
    result = int(value, 16)
    if not 1 <= result <= MAX_JOB_ENTRIES:
        raise S.StorageError('bad-request', 'invalid node ID')
    return result


@contextlib.contextmanager
def inventory(cache, job_id=None, write=False):
    catalog = cache.read_json('jobs.json')
    ids = catalog.get('ids') if isinstance(catalog, dict) else None
    if not isinstance(ids, list) or not 1 <= len(ids) <= 2 or any(not isinstance(i, str) or not S.ID_RE.fullmatch(i) for i in ids):
        raise S.StorageError('invalid-snapshot', 'invalid Storage job catalog')
    job_id = ids[0] if job_id is None else job_id
    if job_id not in ids:
        raise S.StorageError('snapshot-not-active', 'this Storage job was replaced')
    name = 'job-' + job_id + '.sqlite'
    info = cache.safe_stat(name)
    if info.st_size > MAX_JOB_BYTES:
        raise S.StorageError('invalid-snapshot', 'Storage job exceeds its cache budget')
    if write:
        cache.writer()
    db = sqlite3.connect('file:' + cache.path(name) + ('?mode=rw' if write else '?mode=ro'), uri=True, timeout=0.1)
    db.row_factory = sqlite3.Row
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] != JOB_SCHEMA:
            raise S.StorageError('invalid-snapshot', 'unsupported Storage job schema')
        if not write:
            db.execute('BEGIN')
        meta = json.loads(db.execute("SELECT value FROM metadata WHERE key='job'").fetchone()[0])
        if meta['snapshotId'] != job_id or not isinstance(meta['roots'], list) or not 1 <= len(meta['roots']) <= MAX_ROOTS:
            raise S.StorageError('invalid-snapshot', 'invalid Storage job identity')
        for root in meta['roots']:
            if S.identity(base64.b64decode(root['pathBytes'], validate=True)) != root['identity']:
                raise S.StorageError('root-changed', 'a scanned folder changed or was remounted; start a new scan')
        yield db, meta
    finally:
        db.close()


def metadata(db, meta):
    root = db.execute('SELECT known,complete FROM nodes WHERE id=1').fetchone()
    count = db.execute('SELECT max(id) FROM nodes').fetchone()[0]
    pending = db.execute('SELECT count(*) FROM frontier').fetchone()[0]
    return dict(meta, entries=count, pendingFolders=pending, knownAllocatedBytes=root['known'],
                allocatedBytes=root['known'] if root['complete'] else None,
                coverage='complete' if root['complete'] else 'partial',
                rootNodeId=nid(1), job=True, units='bytes')


def save_metadata(db, meta):
    db.execute("UPDATE metadata SET value=? WHERE key='job'", (json.dumps(meta),))


def start(command, cache):
    cache.writer()
    path = S.unpack_path(command.get('path'))
    if command.get('kind') == 'main':
        plan = location_plan(os.fsdecode(path))
        roots, label = plan['roots'], 'Main storage'
    else:
        roots, label = [root_record(path, S.read_mounts())], S.display(path)
        plan = dict(skipped=[], note='This folder only; mounted filesystems and symbolic links are not followed.')
    job_id = uuid.uuid4().hex
    name = 'job-' + job_id + '.sqlite'
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=cache.fd)
    os.close(fd)
    db = sqlite3.connect(cache.path(name))
    db.row_factory = sqlite3.Row
    try:
        db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
            PRAGMA cache_size=-2048; PRAGMA temp_store=MEMORY;
            CREATE TABLE nodes(id INTEGER PRIMARY KEY, parent INTEGER, root INTEGER NOT NULL,
                name BLOB NOT NULL, kind INTEGER NOT NULL, own INTEGER, apparent INTEGER,
                known INTEGER NOT NULL, complete INTEGER NOT NULL, done INTEGER NOT NULL,
                reason TEXT NOT NULL, shared INTEGER NOT NULL, label TEXT NOT NULL,
                device INTEGER, inode INTEGER, ctime INTEGER);
            CREATE UNIQUE INDEX names ON nodes(parent,name);
            CREATE INDEX children ON nodes(parent,known DESC,name,id);
            CREATE TABLE frontier(id INTEGER PRIMARY KEY,cookie INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE links(domain TEXT,device INTEGER,inode INTEGER,PRIMARY KEY(domain,device,inode)) WITHOUT ROWID;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        db.execute('PRAGMA user_version=%d' % JOB_SCHEMA)
        db.execute('PRAGMA max_page_count=%d' % (MAX_JOB_BYTES // 4096))
        virtual = len(roots) > 1
        if virtual:
            db.execute('INSERT INTO nodes VALUES(1,NULL,0,?,1,0,0,0,0,1,\'\',0,?,NULL,NULL,NULL)', (label.encode(), label.casefold()))
        for index, root in enumerate(roots):
            node_id = index + 2 if virtual else 1
            root['node'] = node_id
            raw = base64.b64decode(root['pathBytes'])
            info = os.stat(raw, follow_symlinks=False)
            own = max(0, info.st_blocks * 512)
            title = root['path'] if virtual else os.path.basename(raw) or b'/'
            title = os.fsencode(title)
            db.execute('INSERT INTO nodes VALUES(?,?,?,?,1,?,?,?,0,0,\'\',0,?,?,?,?)',
                       (node_id, 1 if virtual else None, node_id, title, own, info.st_size, own,
                        S.display(title).casefold(), info.st_dev, info.st_ino, info.st_ctime_ns))
            db.execute('INSERT INTO frontier(id) VALUES(?)', (node_id,))
            if virtual:
                db.execute('UPDATE nodes SET known=known+? WHERE id=1', (own,))
        meta = dict(snapshotId=job_id, scope=label, scopeBytes=encoded(path), kind=command.get('kind', 'folder'),
                    identity=roots[0]['identity'] if not virtual else None, roots=roots,
                    capturedAt=time.time(), updatedAt=time.time(), status='paused',
                    allocationMethod='st_blocks*512', skipped=plan['skipped'], scopeNote=plan['note'],
                    elapsedSeconds=0, unreadable=0, excluded=0, changed=0, pauseReason='', currentFolder='')
        db.execute("INSERT INTO metadata VALUES('job',?)", (json.dumps(meta),))
        db.commit()
        result = metadata(db, meta)
    except BaseException:
        db.close()
        os.unlink(name, dir_fd=cache.fd)
        raise
    else:
        db.close()
    try:
        prior = cache.read_json('jobs.json')['ids']
    except FileNotFoundError:
        prior = []
    ids = [job_id] + prior[:1]
    temp = '.jobs-' + job_id + '.tmp'
    cache.write_json(temp, dict(ids=ids))
    os.replace(temp, 'jobs.json', src_dir_fd=cache.fd, dst_dir_fd=cache.fd)
    os.fsync(cache.fd)
    for old in prior[1:]:
        if isinstance(old, str) and S.ID_RE.fullmatch(old):
            os.unlink('job-' + old + '.sqlite', dir_fd=cache.fd)
    return dict(status='paused', snapshot=result, snapshotId=job_id)


def chain(db, node_id):
    rows, seen = [], set()
    while node_id:
        if node_id in seen or len(rows) > S.MAX_DEPTH + 2:
            raise S.StorageError('invalid-snapshot', 'invalid Storage parent chain')
        seen.add(node_id)
        row = db.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        if row is None:
            raise S.StorageError('unknown-node', 'entry is absent from this inventory')
        rows.append(row)
        node_id = row['parent']
    return list(reversed(rows))


def open_node(db, meta, node_id, ancestors=False):
    rows = chain(db, node_id)
    target = rows[-1]
    root = next((r for r in meta['roots'] if r['node'] == target['root']), None)
    if root is None:
        raise S.StorageError('action-unavailable', 'select a real folder within Main storage')
    path = base64.b64decode(root['pathBytes'], validate=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        active = False
        for row in rows:
            if row['id'] == root['node']:
                active = True
                info = os.fstat(fd)
            elif active:
                name = row['name']
                if not isinstance(name, bytes) or not name or name in (b'.', b'..') or b'/' in name or b'\0' in name:
                    raise S.StorageError('invalid-snapshot', 'unsafe stored filename')
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                path = os.path.join(path, name)
                if row['kind'] == 1:
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    os.close(fd)
                    fd = child
                    info = os.fstat(fd)
            else:
                continue
            if (info.st_dev, info.st_ino) != (row['device'], row['inode']) or fd_mount_id(fd) != root['identity']['mountId']:
                raise S.StorageError('node-changed', 'folder or mount changed; start a new scan')
            if (ancestors or row['id'] == node_id) and info.st_ctime_ns != row['ctime']:
                raise S.StorageError('node-changed', 'entry changed since it was measured; start a new scan')
        return fd, path, target, root
    except BaseException:
        os.close(fd)
        raise


# Kept as a local name for fixture injection without changing legacy actions.
fd_mount_id = S.fd_mount_id


class Dirent(ctypes.Structure):
    _fields_ = [('inode', ctypes.c_ulong), ('cookie', ctypes.c_long),
                ('length', ctypes.c_ushort), ('kind', ctypes.c_ubyte), ('name', ctypes.c_char * 256)]


@contextlib.contextmanager
def directory_entries(fd, cookie):
    """Linux 64-bit readdir cookies avoid restarting million-entry directories."""
    if not sys.platform.startswith('linux') or ctypes.sizeof(ctypes.c_long) != 8:
        raise S.StorageError('unsupported-platform', 'progressive scans require 64-bit Linux')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.fdopendir.argtypes, libc.fdopendir.restype = [ctypes.c_int], ctypes.c_void_p
    libc.readdir.argtypes, libc.readdir.restype = [ctypes.c_void_p], ctypes.POINTER(Dirent)
    libc.seekdir.argtypes = [ctypes.c_void_p, ctypes.c_long]
    libc.closedir.argtypes = [ctypes.c_void_p]
    duplicate = os.dup(fd)
    stream = libc.fdopendir(duplicate)
    if not stream:
        os.close(duplicate)
        raise OSError(ctypes.get_errno(), 'cannot read directory')
    try:
        if cookie:
            libc.seekdir(stream, cookie)
        def entries():
            while True:
                ctypes.set_errno(0)
                pointer = libc.readdir(stream)
                if not pointer:
                    if ctypes.get_errno():
                        raise OSError(ctypes.get_errno(), 'directory enumeration failed')
                    return
                entry = pointer.contents
                name = bytes(entry.name)
                if name not in (b'.', b'..'):
                    yield name, entry.cookie
        yield entries()
    finally:
        libc.closedir(stream)


def propagate(db, node_id, delta):
    for row in reversed(chain(db, node_id)):
        db.execute('UPDATE nodes SET known=known+? WHERE id=?', (delta, row['id']))


def finish(db, node_id, reason=''):
    db.execute('DELETE FROM frontier WHERE id=?', (node_id,))
    db.execute('UPDATE nodes SET done=1,reason=? WHERE id=?', (reason, node_id))
    for row in reversed(chain(db, node_id)):
        db.execute('''UPDATE nodes SET complete=(done=1 AND reason='' AND NOT EXISTS
            (SELECT 1 FROM nodes child WHERE child.parent=nodes.id AND child.complete=0)) WHERE id=?''', (row['id'],))


def step(command, cache, limits=None):
    limits = dict(entries=STEP_ENTRIES, seconds=STEP_SECONDS) | (limits or {})
    started = time.monotonic()
    with inventory(cache, command.get('jobId'), write=True) as (db, meta):
        if meta['status'] == 'ready':
            return dict(status='ready', snapshot=metadata(db, meta))
        mounts = S.read_mounts()
        boundaries = {m['point'] for m in mounts}
        cache_path = os.fsencode(os.readlink('/proc/self/fd/%d' % cache.fd))
        count = db.execute('SELECT max(id) FROM nodes').fetchone()[0]
        processed, pause_reason = 0, ''
        try:
            db.execute('BEGIN IMMEDIATE')
            while processed < limits['entries'] and time.monotonic() - started < limits['seconds']:
                pending = db.execute('SELECT id,cookie FROM frontier ORDER BY id LIMIT 1').fetchone()
                if pending is None:
                    break
                node_id, cookie = pending
                try:
                    fd, path, parent, root = open_node(db, meta, node_id)
                except OSError as exc:
                    meta['unreadable'] += exc.errno == errno.EACCES
                    meta['changed'] += exc.errno != errno.EACCES
                    finish(db, node_id, 'unreadable-directory' if exc.errno == errno.EACCES else 'changed-directory')
                    continue
                meta['currentFolder'] = S.display(path)
                delta = 0
                try:
                    with directory_entries(fd, cookie) as entries:
                        ended = True
                        for name, next_cookie in entries:
                            if count >= MAX_JOB_ENTRIES:
                                pause_reason = 'entry-budget'; ended = False; break
                            absolute = os.path.join(path, name)
                            reason, info = '', None
                            try:
                                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                            except OSError as exc:
                                reason = 'vanished' if exc.errno == errno.ENOENT else 'unreadable-entry'
                                meta['changed'] += reason == 'vanished'
                                meta['unreadable'] += reason != 'vanished'
                            kind = 0 if info is None else 1 if stat.S_ISDIR(info.st_mode) else 2 if stat.S_ISREG(info.st_mode) else 3 if stat.S_ISLNK(info.st_mode) else 4
                            if info is not None and (absolute == cache_path or absolute in boundaries or info.st_dev != root['identity']['device']):
                                reason = 'raman-cache' if absolute == cache_path else 'mount-boundary'
                                meta['excluded'] += 1
                            if kind == 1 and len(chain(db, node_id)) > S.MAX_DEPTH:
                                reason = 'depth-boundary'; meta['excluded'] += 1
                            own = max(0, info.st_blocks * 512) if info is not None and not reason else None
                            shared = False
                            if info is not None and info.st_nlink > 1 and kind != 1 and not reason:
                                inserted = db.execute('INSERT OR IGNORE INTO links VALUES(?,?,?)',
                                                      (json.dumps(root['domain']), info.st_dev, info.st_ino)).rowcount
                                shared = not inserted
                                if shared:
                                    own = 0
                            count += 1
                            db.execute('INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                       (count, node_id, root['node'], name, kind, own, info.st_size if info else None,
                                        own or 0, int(kind != 1 and not reason), int(kind != 1 or bool(reason)), reason,
                                        int(shared), S.display(name).casefold(), info.st_dev if info else None,
                                        info.st_ino if info else None, info.st_ctime_ns if info else None))
                            if kind == 1 and not reason:
                                db.execute('INSERT INTO frontier(id) VALUES(?)', (count,))
                            delta += own or 0
                            processed += 1
                            cookie = next_cookie
                            if processed >= limits['entries'] or time.monotonic() - started >= limits['seconds']:
                                ended = False; break
                        if ended:
                            finish(db, node_id)
                        else:
                            db.execute('UPDATE frontier SET cookie=? WHERE id=?', (cookie, node_id))
                    if os.fstat(fd).st_ctime_ns != parent['ctime']:
                        raise S.StorageError('node-changed', 'folder contents changed while scanning; start a new scan')
                except OSError as exc:
                    meta['unreadable'] += 1
                    finish(db, node_id, 'unreadable-directory')
                finally:
                    os.close(fd)
                propagate(db, node_id, delta)
                if pause_reason:
                    break
            meta.update(updatedAt=time.time(), elapsedSeconds=meta['elapsedSeconds'] + time.monotonic() - started,
                        status='paused' if pause_reason else 'scanning' if db.execute('SELECT 1 FROM frontier LIMIT 1').fetchone() else 'ready',
                        pauseReason=pause_reason)
            save_metadata(db, meta)
            db.commit()
        except (S.StorageError, sqlite3.Error):
            db.rollback()
            raise
        return dict(status=meta['status'], snapshot=metadata(db, meta))


def cached(command, cache):
    try:
        with inventory(cache, command.get('jobId')) as (db, meta):
            scope = S.unpack_path(command.get('path')) if command.get('path') else None
            if scope and (base64.b64decode(meta['scopeBytes']) != scope or meta['kind'] != command.get('kind', 'folder')):
                return dict(status='unscanned', snapshot=None)
            return dict(status=meta['status'], snapshot=metadata(db, meta))
    except FileNotFoundError:
        return dict(status='unscanned', snapshot=None)


def row(db, node, meta):
    return dict(nodeId=nid(node['id']), parentId=nid(node['parent']), name=S.display(node['name']),
                kind=KINDS[node['kind']], ownAllocatedBytes=node['own'], apparentBytes=node['apparent'],
                knownAllocatedBytes=node['known'], allocatedBytes=node['known'] if node['complete'] else None,
                coverage='complete' if node['complete'] else 'partial', pending=not node['done'],
                reason=node['reason'], sharedLink=bool(node['shared']), hidden=node['name'].startswith(b'.'),
                actionIdentity=bool(node['root']) and not node['reason'], copyRepresentable=True)


def children(command, cache):
    with inventory(cache, command.get('jobId')) as (db, meta):
        node_id = number(command.get('nodeId', nid(1)))
        node = db.execute('SELECT * FROM nodes WHERE id=?', (node_id,)).fetchone()
        if node is None:
            raise S.StorageError('unknown-node', 'entry is absent from this inventory')
        offset = S.integer(command, 'offset', 0, 0, MAX_JOB_ENTRIES)
        limit = S.integer(command, 'limit', S.MAX_ROWS, 1, S.MAX_ROWS)
        text = command.get('filter', '')
        if not isinstance(text, str) or len(text) > 128 or not all(c.isprintable() for c in text):
            raise S.StorageError('bad-request', 'invalid folder filter')
        where = 'parent=?' + (' AND instr(label,?)>0' if text else '')
        args = (node_id, text.casefold()) if text else (node_id,)
        count, total, incomplete = db.execute('SELECT count(*),coalesce(sum(known),0),coalesce(sum(1-complete),0) FROM nodes WHERE ' + where, args).fetchone()
        order = ' ORDER BY known DESC,name,id LIMIT ? OFFSET ?'
        rows = [row(db, r, meta) for r in db.execute('SELECT * FROM nodes WHERE ' + where + order, args + (limit, offset))]
        segments = [row(db, r, meta) for r in db.execute('SELECT * FROM nodes WHERE ' + where + order, args + (S.MAX_SEGMENTS - 1, 0))]
        result = dict(snapshotId=meta['snapshotId'], nodeId=nid(node_id), node=row(db, node, meta),
                      snapshot=metadata(db, meta), rows=rows, segments=segments, units='bytes',
                      offset=offset, total=count, nextOffset=offset + len(rows) if offset + len(rows) < count else None,
                      filter=text, childrenKnownAllocatedBytes=total, activationDurability='durable',
                      breadcrumbs=[dict(nodeId=nid(r['id']), name=S.display(r['name'])) for r in chain(db, node_id)])
        result['other'] = dict(kind='other', count=count-len(segments), offset=len(segments), filter=text,
                               knownAllocatedBytes=total-sum(r['knownAllocatedBytes'] for r in segments),
                               coverage='partial' if incomplete else 'complete') if count > len(segments) else None
        return result


def action(command, cache):
    operation = command.get('action')
    if operation not in ('open', 'copy'):
        raise S.StorageError('bad-request', 'action must be open or copy')
    with inventory(cache, command.get('jobId')) as (db, meta):
        fd, path, node, root = open_node(db, meta, number(command.get('nodeId')), ancestors=True)
        try:
            if node['reason']:
                raise S.StorageError('action-unavailable', 'entry was not safely measured')
            if operation == 'copy':
                try:
                    path.decode('utf-8', 'strict')
                except UnicodeError:
                    raise S.StorageError('action-unavailable', 'clipboard cannot represent this byte path')
                import subprocess
                try:
                    result = subprocess.run(['wl-copy', '--type', 'text/plain;charset=utf-8'], input=path,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    raise S.StorageError('action-unavailable', 'clipboard helper unavailable')
                if result.returncode:
                    raise S.StorageError('action-unavailable', 'clipboard rejected this path')
            else:
                from raman_portal import open_directory, PortalError
                try:
                    open_directory(fd)
                except PortalError as exc:
                    raise S.StorageError('action-unavailable', str(exc)) from exc
            return dict(snapshotId=meta['snapshotId'], nodeId=command['nodeId'], action=operation,
                        status='copied' if operation == 'copy' else 'opened')
        finally:
            os.close(fd)


def dispatch(command, cache):
    name = command['command']
    handlers = {'storage.locations': lambda c, _: location_plan(c.get('path')),
                'storage.start': start, 'storage.step': step, 'storage.job': cached,
                'storage.job.children': children, 'storage.job.action': action}
    return handlers[name](command, cache)
