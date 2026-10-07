#!/usr/bin/env python3
"""Explicit, read-only storage jobs. All filesystem/SQLite work runs off-probe.

The probe owns subprocesses and commit authorization. Workers never inherit its
history/dispatch descriptors, scan file contents, follow directory symlinks or
signal only their owned session groups when unfinished work loses its owner.
"""

import base64
import collections
import ctypes
import fcntl
import hashlib
import json
import math
import os
import re
import select
import signal
import sqlite3
import stat
import sys
import threading
import time

SCHEMA_VERSION = 1
MAX_ENTRIES = 200_000
MAX_DEPTH = 128
SCAN_SECONDS = 120.0
QUERY_SECONDS = 10.0
MAX_SCOPES = 3
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHE_AGE = 7 * 86400
MAX_LINE_BYTES = 128 * 1024
MAX_ROWS = 50
MAX_SEGMENTS = 60
MAX_WARNINGS = 32
MAX_NAME = 256
MAX_PATH = 4096
MAX_NODE_PATH = MAX_DEPTH * 256
MAX_QUEUE = 8
MAX_WORKER_COMMAND_BYTES = 16 * 1024  # UTF-8 input may expand in ASCII JSON
PROGRESS_INTERVAL = 0.5
ID_RE = re.compile(r"^[0-9a-f]{32}$")
EXCLUDED = {
    "proc": "pseudo", "sysfs": "pseudo", "devtmpfs": "RAM-backed",
    "tmpfs": "RAM-backed", "ramfs": "RAM-backed", "cgroup": "pseudo",
    "cgroup2": "pseudo", "devpts": "pseudo", "securityfs": "pseudo",
    "debugfs": "pseudo", "tracefs": "pseudo", "configfs": "pseudo",
    "pstore": "pseudo", "mqueue": "pseudo", "hugetlbfs": "RAM-backed",
    "fusectl": "pseudo", "autofs": "automount", "rpc_pipefs": "pseudo",
    "nfs": "network", "nfs4": "network", "cifs": "network", "smb3": "network",
    "9p": "network", "ceph": "network", "afs": "network", "fuse.sshfs": "network",
    "fuse.rclone": "network", "fuse.s3fs": "network", "bpf": "pseudo",
    "efivarfs": "pseudo", "binfmt_misc": "pseudo", "nsfs": "pseudo",
}
# Unknown filesystem types are labelled unsupported rather than assumed local.
LOCAL = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "zfs", "bcachefs",
         "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "iso9660", "udf",
         "squashfs", "overlay", "jfs", "reiserfs", "hfs", "hfsplus"}


class StorageError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def message(kind, request_id=None, **fields):
    return dict(type=kind, requestId=request_id, schemaVersion=SCHEMA_VERSION,
                time=time.time(), **fields)


def error(request_id, code, text, **fields):
    return message("error", request_id, code=code, message=str(text)[:200], **fields)


def wire(value):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(data) + 1 > MAX_LINE_BYTES:
        raise StorageError("too-large", "storage reply exceeds 128 KiB")
    return data + b"\n"


def display(path):
    """Plain printable label only. Navigation uses node IDs and stored bytes."""
    text = os.fsdecode(path)
    return "".join(c if c.isprintable() and not 0xD800 <= ord(c) <= 0xDFFF
                   else "\\x%02x" % (ord(c) - 0xDC00 if 0xDC80 <= ord(c) <= 0xDCFF else ord(c))
                   for c in text)[:MAX_NAME]


def unpack_path(value):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise StorageError("invalid-path", "path must be a nonempty absolute path")
    try:
        path = os.fsencode(value)
    except UnicodeError:
        raise StorageError("invalid-path", "path contains an invalid filesystem character")
    if not os.path.isabs(path) or len(path) > MAX_PATH:
        raise StorageError("invalid-path", "path must be absolute and at most 4096 bytes")
    return os.path.realpath(path)


def mount_unescape(value):
    return re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m[1], 8)]), value)


def parse_mountinfo(data):
    mounts = []
    for line in data.splitlines():
        fields = line.split()
        try:
            split = fields.index(b"-")
            if split < 6 or len(fields) < split + 4:
                continue
            mounts.append({"id": int(fields[0]), "device": fields[2].decode("ascii"),
                           "root": mount_unescape(fields[3]), "point": mount_unescape(fields[4]),
                           "options": fields[5].decode("ascii"), "type": fields[split + 1].decode("ascii"),
                           "source": mount_unescape(fields[split + 2])})
        except (ValueError, UnicodeError):
            continue
    return mounts


def read_mounts():
    with open("/proc/self/mountinfo", "rb") as handle:
        data = handle.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise StorageError("mount-limit", "mount metadata exceeds 4 MiB")
    mounts = parse_mountinfo(data)
    if not mounts:
        raise StorageError("unsupported-platform", "Linux mountinfo is unavailable")
    return mounts


def contains(parent, child):
    return child == parent or child.startswith(parent.rstrip(b"/") + b"/")


def mount_for(path, mounts):
    matches = [m for m in mounts if contains(m["point"], path)]
    if not matches:
        raise StorageError("missing-volume", "no filesystem contains this scope")
    return max(matches, key=lambda m: (len(m["point"]), m["id"]))


def exclusion(mount):
    return EXCLUDED.get(mount["type"], "" if mount["type"] in LOCAL else "unsupported filesystem")


def identity(path, mounts=None):
    mounts = read_mounts() if mounts is None else mounts
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise StorageError("missing-root", "scope disappeared; scan explicitly after it returns")
    if not stat.S_ISDIR(info.st_mode):
        raise StorageError("invalid-path", "scan scope must be a directory")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise StorageError("root-changed", "root changed while opening")
        actual_mount_id = fd_mount_id(fd)
        generation = filesystem_generation(fd)
    finally:
        os.close(fd)
    matching = [m for m in mounts if m["id"] == actual_mount_id and contains(m["point"], path)]
    if not matching:
        raise StorageError("root-changed", "root mount changed while reading its identity")
    mount = matching[0]
    reason = exclusion(mount)
    if reason:
        raise StorageError("unsupported-filesystem", reason + ": " + mount["type"])
    return {"device": info.st_dev, "inode": info.st_ino, "mountId": mount["id"],
            "mountDevice": mount["device"], "mountRoot": base64.b64encode(mount["root"]).decode("ascii"),
            "mountPoint": base64.b64encode(mount["point"]).decode("ascii"),
            "filesystem": mount["type"], "source": base64.b64encode(mount["source"]).decode("ascii"),
            **generation}


def filesystem_generation(fd):
    """Non-recycled mount + boot + filesystem ID + root birth generation.

    Linux UAPI statx is 256 bytes; mount ID is at 144, btime at 80. Request
    UNIQUE (Linux 6.8) rather than the recyclable /proc mount ID. Fail closed
    when identity cannot distinguish a replaced directory/volume.
    """
    try:
        call = ctypes.CDLL(None, use_errno=True).statx
        call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
        call.restype = ctypes.c_int
        result = ctypes.create_string_buffer(256)
        if call(fd, b"", 0x1000, 0x4800, result) != 0:  # AT_EMPTY_PATH, MNT_ID_UNIQUE | BTIME
            raise ValueError()
        raw = result.raw
        mask = int.from_bytes(raw[:4], sys.byteorder)
        if mask & 0x4800 != 0x4800:
            raise ValueError()
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="ascii") as handle:
            boot = handle.read(64).strip()
        if not re.fullmatch(r"[0-9a-f-]{36}", boot):
            raise ValueError()
        return {"bootId": boot, "filesystemId": str(os.fstatvfs(fd).f_fsid),
                "uniqueMountId": str(int.from_bytes(raw[144:152], sys.byteorder)),
                "rootBirthSeconds": int.from_bytes(raw[80:88], sys.byteorder, signed=True),
                "rootBirthNanos": int.from_bytes(raw[88:92], sys.byteorder)}
    except (AttributeError, OSError, ValueError):
        raise StorageError("identity-unavailable", "safe storage identity requires Linux 6.8 unique mount IDs and root birth time")


def envelope(command, kind):
    fields = {key: command[key] for key in ("clientId", "generation", "scanId") if key in command}
    return message(kind, command.get("requestId"), **fields)


def fd_mount_id(fd):
    # Open-directory identity catches a bind/subvolume swap even on st_dev match.
    with open("/proc/self/fdinfo/%d" % fd, "rb") as handle:
        for line in handle:
            if line.startswith(b"mnt_id:"):
                return int(line.split()[1])
    raise StorageError("storage-unavailable", "directory mount identity is unavailable")


def capacity_values(info):
    unit = info.f_frsize or info.f_bsize
    total = max(0, info.f_blocks * unit)
    free = min(total, max(0, info.f_bfree * unit))
    available = min(free, max(0, info.f_bavail * unit))
    return {"totalBytes": total, "freeBytes": free, "availableBytes": available,
            "usedBytes": total - free, "reservedBytes": free - available,
            "readOnly": bool(info.f_flag & getattr(os, "ST_RDONLY", 1))}


def capacities(command, base=None):
    base = envelope(command, "storage-capacity") if base is None else base
    mounts = read_mounts()
    selected = command.get("path")
    if selected is not None:
        path = unpack_path(selected)
        ident = identity(path, mounts)
        values = capacity_values(os.statvfs(path))
        if identity(path) != ident:
            raise StorageError("root-changed", "scope changed while reading capacity")
        return dict(scope=display(path), identity=ident, units="bytes", **values)
    offset = integer(command, "offset", 0, 0, 1_000_000)
    limit = integer(command, "limit", MAX_ROWS, 1, MAX_ROWS)
    # Multiple mounts may describe one filesystem: never aggregate capacities.
    mounts.sort(key=lambda m: (m["point"], m["id"]))
    rows = []
    for m in mounts[offset:offset + limit]:
        row = {"mountId": m["id"], "path": display(m["point"]), "pathBytes": base64.b64encode(m["point"]).decode("ascii"),
               "filesystem": m["type"], "device": m["device"], "excluded": exclusion(m), "capacity": None}
        if not row["excluded"]:
            try:
                ident = identity(m["point"], mounts)
                if ident["mountId"] != m["id"]:
                    row["error"] = "overmounted"
                    rows.append(row)
                    continue
                values = capacity_values(os.statvfs(m["point"]))
                if identity(m["point"]) != ident:
                    raise StorageError("root-changed", "mount changed")
                row["capacity"] = values
            except (OSError, StorageError):
                row["error"] = "unavailable"
        rows.append(row)
    while True:
        result = dict(units="bytes", mounts=rows, offset=offset, total=len(mounts),
                      nextOffset=offset + len(rows) if offset + len(rows) < len(mounts) else None)
        try:
            wire(dict(base, **result))
            return result
        except StorageError:
            if len(rows) > 1:
                rows.pop()
            else:
                raise


def integer(command, name, default, low, high):
    value = command.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise StorageError("bad-request", "%s must be an integer in %d..%d" % (name, low, high))
    return value


def cache_dir():
    root = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    if not os.path.isabs(root):
        root = os.path.expanduser("~/.cache")
    return os.path.join(root, "raman", "storage")


def file_fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink]


class Cache:
    """Anchor all inventories to a no-follow directory FD; reject unsafe stores."""
    def __init__(self, directory=None, validated=None):
        directory = cache_dir() if directory is None else directory
        # The XDG base is user-selected; RAMen's own components may not be links.
        base, leaf = os.path.split(directory)
        xdg, parent = os.path.split(base)
        os.makedirs(xdg, mode=0o700, exist_ok=True)
        fd = os.open(xdg, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for name in (parent, leaf):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                info = os.fstat(next_fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    os.close(next_fd)
                    raise StorageError("unsafe-cache", "RAMen cache directories must be owned by you and mode 0700")
                os.close(fd)
                fd = next_fd
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise
        self.lock_fd = None
        self.validated = dict(validated or {})

    def path(self, name):
        # Linux procfs keeps SQLite's path anchored to the already-open directory.
        return "/proc/self/fd/%d/%s" % (self.fd, name)

    def close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
        os.close(self.fd)

    def safe_stat(self, name):
        info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
            raise StorageError("unsafe-cache", "snapshot must be a private regular file")
        return info

    def writer(self):
        if self.lock_fd is not None:
            self.recover()
            self.prune()
            return
        self.lock_fd = os.open("writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
        self.safe_stat("writer.lock")
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.lock_fd)
            self.lock_fd = None
            raise StorageError("cache-busy", "another RAMen runtime is scanning this cache")
        # Recovery/GC must succeed before another candidate can consume space.
        self.recover()
        self.prune()

    def read_json(self, name, limit=16384):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() \
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise StorageError("unsafe-cache", "catalog must be a private regular file")
            if info.st_size > limit:
                raise StorageError("invalid-snapshot", "oversized private catalog")
            raw = os.read(fd, limit + 1)
            if file_fingerprint(os.fstat(fd)) != file_fingerprint(info):
                raise StorageError("unsafe-cache", "private catalog changed during read")
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise StorageError("invalid-snapshot", "invalid private catalog JSON")
        finally:
            os.close(fd)

    def write_json(self, name, value):
        data = wire(value)
        if len(data) > 16384:
            raise StorageError("invalid-snapshot", "oversized private catalog")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
            os.fsync(fd)
        finally:
            os.close(fd)

    def catalog(self):
        try:
            value = self.read_json("catalog.json")
        except FileNotFoundError:
            return dict(schemaVersion=SCHEMA_VERSION, revision=0, entries=[])
        return self.validate_catalog(value)

    @staticmethod
    def validate_catalog(value):
        try:
            if not isinstance(value, dict) or value["schemaVersion"] != SCHEMA_VERSION \
                    or type(value["revision"]) is not int or not 0 <= value["revision"] < 2 ** 63 \
                    or not isinstance(value["entries"], list) or len(value["entries"]) > MAX_SCOPES:
                raise ValueError()
            scopes, ids = set(), set()
            for entry in value["entries"]:
                if not isinstance(entry, dict) or not isinstance(entry["snapshotId"], str) or not ID_RE.fullmatch(entry["snapshotId"]) \
                        or not isinstance(entry["scanId"], str) or not ID_RE.fullmatch(entry["scanId"]) \
                        or not isinstance(entry["scopeKey"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["scopeKey"]) \
                        or type(entry["capturedAt"]) not in (int, float) or not math.isfinite(entry["capturedAt"]) \
                        or not isinstance(entry["file"], list) or len(entry["file"]) != 8 \
                        or any(type(n) is not int for n in entry["file"]) \
                        or not isinstance(entry["metadataHash"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["metadataHash"]) \
                        or entry["scopeKey"] in scopes or entry["snapshotId"] in ids:
                    raise ValueError()
                scopes.add(entry["scopeKey"])
                ids.add(entry["snapshotId"])
            return value
        except (KeyError, TypeError, ValueError):
            raise StorageError("invalid-snapshot", "invalid activation catalog")

    def transaction(self):
        try:
            value = self.read_json("activation.json")
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or not isinstance(value.get("scanId"), str) or not ID_RE.fullmatch(value["scanId"]):
            raise StorageError("invalid-snapshot", "invalid activation journal")
        self.validate_catalog(value.get("before"))
        self.validate_catalog(value.get("after"))
        if value["after"]["revision"] != value["before"]["revision"] + 1:
            raise StorageError("invalid-snapshot", "invalid activation revision")
        return value

    def recover(self):
        transaction = self.transaction()
        if transaction is None:
            return
        current = self.catalog()
        if current not in (transaction["before"], transaction["after"]):
            raise StorageError("commit-uncertain", "catalog does not match its activation journal; no new scan permitted")
        # This sync resolves a crash after atomic replacement but before durable
        # completion. Until it succeeds, keep all rollback data and the journal.
        try:
            os.fsync(self.fd)
        except OSError:
            raise StorageError("commit-uncertain", "catalog durability is unresolved; rollback files retained")
        self.finish_activation()

    def finish_activation(self):
        try:
            self.prune()
            os.unlink("activation.json", dir_fd=self.fd)
            os.fsync(self.fd)
        except OSError:
            raise StorageError("cleanup-failed", "selected snapshot committed but cache cleanup failed; no new scan permitted")

    def durability(self, catalog=None):
        transaction = self.transaction()
        return "pending" if transaction is not None and (catalog or self.catalog()) == transaction["after"] else "durable"

    def receipt(self, snapshot_id):
        name = snapshot_id + ".receipt"
        info = self.safe_stat(name)
        if info.st_size > 4096:
            raise StorageError("invalid-snapshot", "oversized activation receipt")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            if file_fingerprint(os.fstat(fd)) != file_fingerprint(info):
                raise StorageError("unsafe-cache", "activation receipt changed while opening")
            value = json.loads(os.read(fd, 4097))
        finally:
            os.close(fd)
        if not isinstance(value, dict) or value.get("snapshotId") != snapshot_id \
                or not re.fullmatch(r"[0-9a-f]{64}", value.get("scopeKey", "")) \
                or type(value.get("capturedAt")) not in (int, float) \
                or not math.isfinite(value["capturedAt"]) or not isinstance(value.get("file"), list) \
                or len(value["file"]) != 8 or any(type(n) is not int for n in value["file"]) \
                or not re.fullmatch(r"[0-9a-f]{64}", value.get("metadataHash", "")):
            raise StorageError("invalid-snapshot", "invalid activation receipt")
        return value

    def snapshots(self, all_files=False):
        if all_files:
            return [(name, self.safe_stat(name)) for name in os.listdir(self.fd)
                    if re.fullmatch(r"[0-9a-f]{32}\.sqlite", name)]
        # Explicit activation order, never wall-clock capture/file timestamps.
        return [(entry["snapshotId"] + ".sqlite", self.safe_stat(entry["snapshotId"] + ".sqlite"))
                for entry in self.catalog()["entries"]]

    def prune(self):
        selected = {entry["snapshotId"] for entry in self.catalog()["entries"]}
        for name in os.listdir(self.fd):
            remove = re.fullmatch(r"\.scan-[0-9a-f]{32}\.sqlite", name) \
                or re.fullmatch(r"\.(receipt|catalog|activation)-[0-9a-f]{32}\.tmp", name) \
                or (re.fullmatch(r"[0-9a-f]{32}\.sqlite", name) and name[:-7] not in selected) \
                or (re.fullmatch(r"[0-9a-f]{32}\.receipt", name) and name[:-8] not in selected)
            if remove:
                self.safe_stat(name)
                try:
                    os.unlink(name, dir_fd=self.fd)
                except OSError:
                    raise StorageError("cleanup-failed", "cache cleanup failed; no new scan permitted")

    def read(self, snapshot_id, validate_root=True):
        if not isinstance(snapshot_id, str) or not ID_RE.fullmatch(snapshot_id):
            raise StorageError("bad-request", "snapshotId must be a stored 32-character ID")
        catalog = self.catalog()
        selected = next((e for e in catalog["entries"] if e["snapshotId"] == snapshot_id), None)
        if selected is None:
            raise StorageError("snapshot-not-active", "snapshot is not selected by the activation catalog")
        name = snapshot_id + ".sqlite"
        info = self.safe_stat(name)
        receipt = self.receipt(snapshot_id)
        if info.st_size > MAX_CACHE_BYTES or time.time() - info.st_mtime > MAX_CACHE_AGE:
            raise StorageError("stale-snapshot", "snapshot expired or exceeds cache limits; scan explicitly")
        # Open no-follow before SQLite and bind the read to that inode.
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            if file_fingerprint(os.fstat(fd)) != file_fingerprint(info):
                raise StorageError("unsafe-cache", "snapshot changed while opening")
            db = sqlite3.connect("file:/proc/self/fd/%d?mode=ro&immutable=1" % fd, uri=True)
            try:
                token = self.validated.get(snapshot_id)
                trusted = isinstance(token, dict) and token.get("file") == file_fingerprint(info)
                metadata = validate_db(db, full=not trusted)
                raw = db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0]
                metadata_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                if selected["file"] != file_fingerprint(info) or selected["metadataHash"] != metadata_hash \
                        or receipt["file"] != file_fingerprint(info) or receipt["metadataHash"] != metadata_hash \
                        or receipt["scopeKey"] != metadata["scopeKey"] or receipt["capturedAt"] != metadata["capturedAt"]:
                    raise StorageError("invalid-snapshot", "committed snapshot was modified")
                if trusted and token.get("metadataHash") != metadata_hash:
                    metadata = validate_db(db)
                self.validated[snapshot_id] = {"file": file_fingerprint(info), "metadataHash": metadata_hash}
                if metadata["snapshotId"] != snapshot_id:
                    raise StorageError("invalid-snapshot", "snapshot ID does not match its private file")
                path = base64.b64decode(metadata["scopeBytes"], validate=True)
                if validate_root and identity(path) != metadata["identity"]:
                    raise StorageError("root-changed", "filesystem/root identity changed; scan explicitly")
                if file_fingerprint(os.fstat(fd)) != file_fingerprint(info):
                    raise StorageError("unsafe-cache", "snapshot changed during validation")
                if not any(e["snapshotId"] == snapshot_id for e in self.catalog()["entries"]):
                    raise StorageError("snapshot-not-active", "snapshot was superseded during validation")
                return db, fd, metadata
            except BaseException:
                db.close()
                raise
        except BaseException:
            os.close(fd)
            raise


NODE_SQL = """CREATE TABLE nodes (
 id TEXT PRIMARY KEY, parent TEXT, name BLOB NOT NULL, path BLOB NOT NULL,
 kind TEXT NOT NULL, own INTEGER, apparent INTEGER, known INTEGER NOT NULL,
 complete INTEGER NOT NULL, reason TEXT NOT NULL, shared INTEGER NOT NULL,
 label TEXT NOT NULL)
"""


def validate_db(db, full=True):
    if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
        raise StorageError("invalid-snapshot", "unsupported storage schema")
    if full and db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise StorageError("invalid-snapshot", "snapshot failed integrity validation")
    columns = [r[1] for r in db.execute("PRAGMA table_info(nodes)")]
    if columns != ["id", "parent", "name", "path", "kind", "own", "apparent", "known", "complete", "reason", "shared", "label"]:
        raise StorageError("invalid-snapshot", "invalid storage node schema")
    raw = db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
    if raw is None or len(raw[0]) > 65536:
        raise StorageError("invalid-snapshot", "missing or oversized snapshot metadata")
    metadata = json.loads(raw[0])
    if not isinstance(metadata, dict):
        raise StorageError("invalid-snapshot", "invalid snapshot metadata")
    if metadata.get("schemaVersion") != SCHEMA_VERSION or metadata.get("allocationMethod") != "st_blocks*512" \
            or not isinstance(metadata.get("identity"), dict) or not ID_RE.fullmatch(metadata.get("snapshotId", "")) \
            or metadata.get("coverage") not in ("complete", "partial") \
            or not isinstance(metadata.get("warnings"), list) or len(metadata["warnings"]) > MAX_WARNINGS:
        raise StorageError("invalid-snapshot", "invalid snapshot metadata")
    try:
        scope = base64.b64decode(metadata["scopeBytes"], validate=True)
        captured = metadata["capturedAt"]
        if not os.path.isabs(scope) or b"\0" in scope or len(scope) > MAX_PATH \
                or metadata["scopeKey"] != hashlib.sha256(scope).hexdigest() \
                or type(captured) not in (int, float) or not math.isfinite(captured) \
                or captured < 0 or captured > time.time() + 60 \
                or time.time() - captured > MAX_CACHE_AGE \
                or not ID_RE.fullmatch(metadata["rootNodeId"]) \
                or any(not isinstance(w, dict) or not isinstance(w.get("name"), str)
                       or len(w["name"]) > MAX_NAME or not isinstance(w.get("reason"), str)
                       or len(w["reason"]) > 64 for w in metadata["warnings"]):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise StorageError("invalid-snapshot", "invalid or expired snapshot identity metadata")
    ident = metadata["identity"]
    if not all(key in ident for key in ("bootId", "filesystemId", "uniqueMountId", "rootBirthSeconds", "rootBirthNanos")):
        raise StorageError("invalid-snapshot", "snapshot lacks non-recycled filesystem/root identity")
    if not isinstance(ident["bootId"], str) or not re.fullmatch(r"[0-9a-f-]{36}", ident["bootId"]) \
            or not isinstance(ident["filesystemId"], str) or len(ident["filesystemId"]) > 64 \
            or not isinstance(ident["uniqueMountId"], str) or not re.fullmatch(r"[1-9][0-9]{0,19}", ident["uniqueMountId"]) \
            or type(ident["rootBirthSeconds"]) is not int or type(ident["rootBirthNanos"]) is not int \
            or not 0 <= ident["rootBirthNanos"] < 1_000_000_000:
        raise StorageError("invalid-snapshot", "invalid filesystem generation")
    if not full:
        return metadata
    count = db.execute("SELECT count(*) FROM nodes").fetchone()[0]
    if not 1 <= count <= MAX_ENTRIES:
        raise StorageError("invalid-snapshot", "invalid entry count")
    invalid = db.execute("""SELECT 1 FROM nodes WHERE
        typeof(id)!='text' OR length(id)!=32 OR id GLOB '*[^0-9a-f]*'
        OR (parent IS NOT NULL AND (typeof(parent)!='text' OR length(parent)!=32 OR parent GLOB '*[^0-9a-f]*'))
        OR typeof(name)!='blob' OR length(name)>255 OR typeof(path)!='blob' OR length(path)>32768
        OR kind NOT IN ('directory','symlink','file','special','unknown')
        OR typeof(known)!='integer' OR known<0
        OR (own IS NOT NULL AND (typeof(own)!='integer' OR own<0))
        OR (apparent IS NOT NULL AND (typeof(apparent)!='integer' OR apparent<0))
        OR complete NOT IN (0,1) OR shared NOT IN (0,1)
        OR typeof(reason)!='text' OR length(reason)>64 OR typeof(label)!='text' OR length(label)>768 LIMIT 1""").fetchone()
    if invalid or db.execute("SELECT count(*) FROM nodes WHERE parent IS NULL").fetchone()[0] != 1:
        raise StorageError("invalid-snapshot", "invalid storage accounting")
    if db.execute("SELECT id FROM nodes WHERE parent IS NULL").fetchone()[0] != metadata["rootNodeId"] \
            or db.execute("SELECT 1 FROM nodes n LEFT JOIN nodes p ON n.parent=p.id WHERE n.parent IS NOT NULL AND p.id IS NULL LIMIT 1").fetchone():
        raise StorageError("invalid-snapshot", "invalid root or parent nodes")
    if [r[1] for r in db.execute("PRAGMA table_info(totals)")] != ["id", "count", "known", "incomplete"] \
            or db.execute("SELECT 1 FROM nodes n LEFT JOIN totals t ON t.id=n.id WHERE t.id IS NULL LIMIT 1").fetchone() \
            or db.execute("SELECT 1 FROM totals WHERE typeof(count)!='integer' OR typeof(known)!='integer' OR typeof(incomplete)!='integer' OR count<0 OR known<0 OR incomplete<0 OR incomplete>count LIMIT 1").fetchone() \
            or db.execute("""SELECT 1 FROM totals t LEFT JOIN
                (SELECT parent, count(*) n, sum(known) k, sum(1-complete) i FROM nodes WHERE parent IS NOT NULL GROUP BY parent) a
                ON t.id=a.parent WHERE t.count!=coalesce(a.n,0) OR t.known!=coalesce(a.k,0) OR t.incomplete!=coalesce(a.i,0) LIMIT 1""").fetchone():
        raise StorageError("invalid-snapshot", "invalid child totals")
    return metadata


class Scanner:
    def __init__(self, path, cache, scan_id, progress=None, limits=None):
        self.path, self.cache, self.scan_id = path, cache, scan_id
        self.progress = progress or (lambda _: None)
        self.limits = {"entries": MAX_ENTRIES, "depth": MAX_DEPTH, "seconds": SCAN_SECONDS}
        if limits:
            self.limits.update(limits)  # tests only; IPC cannot raise ceilings
        self.started = time.monotonic()
        self.last_progress = self.started
        self.count = self.unreadable = self.excluded = self.changed = 0
        self.discovered = 1  # includes queued byte names across all open frames
        self.warnings = []
        self.seen = set()
        self.mounts = read_mounts()
        self.ident = identity(path, self.mounts)
        self.boundaries = {m["point"] for m in self.mounts if m["point"] != path and contains(path, m["point"])}
        self.cache_path = os.fsencode(os.readlink("/proc/self/fd/%d" % cache.fd))
        self.scope_key = hashlib.sha256(path).hexdigest()
        self.temp = ".scan-" + scan_id + ".sqlite"
        self.db = None
        self.final_name = None
        self.completed = False
        self.receipt_temp = ".receipt-" + scan_id + ".tmp"
        self.catalog_temp = ".catalog-" + scan_id + ".tmp"
        self.activation_temp = ".activation-" + scan_id + ".tmp"
        self.before = None

    def check(self):
        now = time.monotonic()
        if now - self.started >= self.limits["seconds"]:
            raise StorageError("scan-time-limit", "scan exceeded 120 seconds; select a narrower folder")
        if now - self.last_progress >= PROGRESS_INTERVAL:
            self.progress(dict(entries=self.count, unreadable=self.unreadable, excluded=self.excluded,
                               changed=self.changed, elapsedSeconds=now - self.started))
            self.last_progress = now

    def warn(self, path, reason):
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append({"name": display(path), "reason": reason})

    def node_id(self, rel):
        prefix = json.dumps(self.ident, sort_keys=True).encode("ascii") + b"\0" + self.path + b"\0"
        return hashlib.sha256(prefix + rel).hexdigest()[:32]

    def add(self, rel, parent, info=None, reason="", observed=False):
        self.check()
        self.count += 1
        if self.count > self.limits["entries"]:
            raise StorageError("scan-entry-limit", "scan exceeded 200000 entries; select a narrower folder")
        node_id = self.node_id(rel)
        own = apparent = None
        kind, shared, complete = "unknown", False, not bool(reason)
        if info is not None:
            kind = "directory" if stat.S_ISDIR(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "special"
            if not reason or observed:
                apparent = max(0, info.st_size)
                own = max(0, info.st_blocks * 512)
                key = (info.st_dev, info.st_ino)
                if info.st_nlink > 1 and not stat.S_ISDIR(info.st_mode):
                    shared = key in self.seen
                    self.seen.add(key)
                    if shared:
                        own = 0
        if reason:
            self.warn(rel, reason)
        known = own or 0
        name = os.path.basename(rel) if rel else os.path.basename(self.path) or b"/"
        self.db.execute("INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (node_id, parent, name, rel, kind, own, apparent, known, int(complete),
                         reason, int(shared), display(name).casefold()))
        self.db.execute("INSERT INTO totals VALUES (?,?,?,?)", (node_id, 0, 0, 0))
        if info is not None and not reason:
            self.db.execute("INSERT INTO action_identity VALUES (?,?,?,?)",
                            (node_id, info.st_dev, info.st_ino, info.st_ctime_ns))
        return node_id, known, complete

    def walk(self, fd, rel, parent, info, depth):
        node_id, known, complete = self.add(rel, parent, info)
        # Collect only a bounded fanout before sorting byte names for stable links.
        names = []
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    self.check()
                    self.discovered += 1
                    if self.discovered > self.limits["entries"]:
                        raise StorageError("scan-entry-limit", "directory fanout exceeds entry limit; select a narrower folder")
                    names.append(os.fsencode(entry.name))
        except OSError:
            self.unreadable += 1
            self.warn(rel, "unreadable-directory")
            self.db.execute("UPDATE nodes SET complete=0, reason='unreadable-directory' WHERE id=?", (node_id,))
            return node_id, known, False
        child_sum = incomplete = 0
        for name in sorted(names):
            self.check()
            child_rel = os.path.join(rel, name)
            absolute = os.path.join(self.path, child_rel)
            if depth + 1 > self.limits["depth"]:
                raise StorageError("scan-depth-limit", "scan exceeded depth 128; select a narrower folder")
            try:
                child_info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as exc:
                reason = "vanished" if exc.errno == 2 else "unreadable-entry"
                self.changed += reason == "vanished"
                self.unreadable += reason != "vanished"
                _, child_known, child_complete = self.add(child_rel, node_id, reason=reason)
            else:
                if absolute == self.cache_path:
                    self.excluded += 1
                    _, child_known, child_complete = self.add(child_rel, node_id, child_info, "raman-cache")
                elif absolute in self.boundaries or child_info.st_dev != self.ident["device"]:
                    self.excluded += 1
                    _, child_known, child_complete = self.add(child_rel, node_id, child_info, "mount-boundary")
                elif stat.S_ISDIR(child_info.st_mode):
                    child_fd = None
                    try:
                        child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (child_info.st_dev, child_info.st_ino):
                            raise OSError("directory changed")
                        if fd_mount_id(child_fd) != self.ident["mountId"]:
                            self.excluded += 1
                            _, child_known, child_complete = self.add(child_rel, node_id, child_info, "mount-boundary")
                        else:
                            _, child_known, child_complete = self.walk(child_fd, child_rel, node_id, child_info, depth + 1)
                    except OSError as exc:
                        if exc.errno == 13:
                            self.unreadable += 1
                        else:
                            self.changed += 1
                        reason = "unreadable-directory" if exc.errno == 13 else "changed-directory"
                        _, child_known, child_complete = self.add(child_rel, node_id, child_info, reason, observed=exc.errno == 13)
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
                else:
                    _, child_known, child_complete = self.add(child_rel, node_id, child_info)
            known += child_known
            child_sum += child_known
            incomplete += not child_complete
            complete = complete and child_complete
        self.db.execute("UPDATE nodes SET known=?, complete=? WHERE id=?", (known, int(complete), node_id))
        self.db.execute("UPDATE totals SET count=?,known=?,incomplete=? WHERE id=?", (len(names), child_sum, incomplete, node_id))
        return node_id, known, complete

    def prepare(self):
        self.cache.writer()
        self.before = self.cache.catalog()
        existing = sum(info.st_size for _, info in self.cache.snapshots(all_files=True))
        available = MAX_CACHE_BYTES - existing - ((MAX_SCOPES + 2) * 4096 + 32768)
        if available < 16384:
            raise StorageError("cache-size-limit", "cache budget cannot hold a new snapshot; remove old storage cache")
        fd = os.open(self.temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=self.cache.fd)
        os.close(fd)
        self.db = sqlite3.connect(self.cache.path(self.temp))
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA cache_size=-2048")
        self.db.execute("PRAGMA max_page_count=%d" % (available // 4096))
        self.db.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
        self.db.execute(NODE_SQL)
        self.db.execute("CREATE TABLE action_identity (id TEXT PRIMARY KEY, device INTEGER, inode INTEGER, ctime INTEGER)")
        self.db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.execute("CREATE TABLE totals (id TEXT PRIMARY KEY, count INTEGER NOT NULL, known INTEGER NOT NULL, incomplete INTEGER NOT NULL)")
        root_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            info = os.fstat(root_fd)
            if (info.st_dev, info.st_ino) != (self.ident["device"], self.ident["inode"]):
                raise StorageError("root-changed", "root changed before traversal")
            if fd_mount_id(root_fd) != self.ident["mountId"]:
                raise StorageError("root-changed", "root mount changed before traversal")
            root_id, known, complete = self.walk(root_fd, b"", None, info, 0)
        finally:
            os.close(root_fd)
        self.check()
        if identity(self.path) != self.ident:
            raise StorageError("root-changed", "root filesystem changed during scan")
        self.db.execute("CREATE INDEX children ON nodes(parent, known DESC, name, id)")
        self.db.execute("CREATE INDEX parents ON nodes(parent)")
        self.db.execute("CREATE INDEX action_paths ON nodes(path)")
        import uuid
        metadata = dict(schemaVersion=SCHEMA_VERSION, snapshotId=uuid.uuid4().hex,
                        scopeKey=self.scope_key, scopeBytes=base64.b64encode(self.path).decode("ascii"),
                        scope=display(self.path), identity=self.ident, capturedAt=time.time(),
                        allocationMethod="st_blocks*512", rootNodeId=root_id,
                        coverage="complete" if complete else "partial", entries=self.count,
                        unreadable=self.unreadable, excluded=self.excluded, changed=self.changed,
                        warnings=self.warnings, knownAllocatedBytes=known,
                        allocatedBytes=known if complete else None,
                        limits=dict(entries=MAX_ENTRIES, depth=MAX_DEPTH, seconds=SCAN_SECONDS))
        self.metadata_raw = json.dumps(metadata)
        self.db.execute("INSERT INTO metadata VALUES ('snapshot',?)", (self.metadata_raw,))
        self.db.commit()
        validate_db(self.db)
        self.db.close()
        self.db = None
        if self.cache.safe_stat(self.temp).st_size + existing + (MAX_SCOPES + 2) * 4096 + 32768 > MAX_CACHE_BYTES:
            raise StorageError("cache-size-limit", "new snapshot exceeds cache budget")
        return metadata

    def previous_id(self):
        return next((e["snapshotId"] for e in (self.before or {"entries": []})["entries"] if e["scopeKey"] == self.scope_key), None)

    def activate(self, metadata):
        self.check()
        if identity(self.path) != self.ident:
            raise StorageError("root-changed", "root changed before snapshot activation")
        fd = os.open(self.temp, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.cache.fd)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        self.final_name = metadata["snapshotId"] + ".sqlite"
        os.rename(self.temp, self.final_name, src_dir_fd=self.cache.fd, dst_dir_fd=self.cache.fd)
        token = {"file": file_fingerprint(self.cache.safe_stat(self.final_name)),
                 "metadataHash": hashlib.sha256(self.metadata_raw.encode("utf-8")).hexdigest()}
        receipt = dict(snapshotId=metadata["snapshotId"], scopeKey=self.scope_key,
                       capturedAt=metadata["capturedAt"], **token)
        self.cache.write_json(self.receipt_temp, receipt)
        os.rename(self.receipt_temp, metadata["snapshotId"] + ".receipt", src_dir_fd=self.cache.fd, dst_dir_fd=self.cache.fd)
        os.fsync(self.cache.fd)
        entry = dict(receipt, scanId=self.scan_id)
        # Ordering is explicit. Clock rollback cannot make an older scan win.
        after = dict(schemaVersion=SCHEMA_VERSION, revision=self.before["revision"] + 1,
                     entries=[entry] + [e for e in self.before["entries"] if e["scopeKey"] != self.scope_key
                                       and time.time() - e["capturedAt"] <= MAX_CACHE_AGE][:MAX_SCOPES - 1])
        self.cache.write_json(self.catalog_temp, after)
        self.cache.write_json(self.activation_temp, dict(scanId=self.scan_id, before=self.before, after=after))
        os.rename(self.activation_temp, "activation.json", src_dir_fd=self.cache.fd, dst_dir_fd=self.cache.fd)
        os.fsync(self.cache.fd)  # candidate/catalog contents and journal durable before commit
        self.check()
        if identity(self.path) != self.ident:
            raise StorageError("root-changed", "root changed before activation catalog replacement")
        # The sole commit linearization point. Receipt publication never selects.
        os.rename(self.catalog_temp, "catalog.json", src_dir_fd=self.cache.fd, dst_dir_fd=self.cache.fd)
        os.fsync(self.cache.fd)
        self.completed = True
        self.cache.validated[metadata["snapshotId"]] = token
        self.cache.finish_activation()  # successful completion means <=3 physical stores

    def close(self):
        if self.db is not None:
            self.db.close()
        # Never unlink a catalog-selected DB, including TERM inside catalog rename.
        selected = {e["snapshotId"] for e in self.cache.catalog()["entries"]}
        names = [self.temp, self.receipt_temp, self.catalog_temp, self.activation_temp]
        if self.final_name is not None and self.final_name[:-7] not in selected:
            names += [self.final_name[:-7] + ".receipt", self.final_name]
        for name in names:
            try:
                os.unlink(name, dir_fd=self.cache.fd)
            except FileNotFoundError:
                pass


def row_value(row, scope=b"", actions=False):
    node_id, parent, name, path, kind, own, apparent, known, complete, reason, shared = row[:11]
    try:
        os.path.join(scope, path).decode("utf-8", "strict")
        copy_representable = True
    except UnicodeError:
        copy_representable = False
    return dict(nodeId=node_id, parentId=parent, name=display(name), kind=kind,
                actionIdentity=actions, copyRepresentable=copy_representable,
                ownAllocatedBytes=own, apparentBytes=apparent, knownAllocatedBytes=known,
                allocatedBytes=known if complete else None, coverage="complete" if complete else "partial",
                reason=reason, sharedLink=bool(shared), hidden=name.startswith(b"."))


def children(command, cache, base=None):
    base = envelope(command, "storage-children") if base is None else base
    db, fd, metadata = cache.read(command.get("snapshotId"))
    try:
        node_id = command.get("nodeId", metadata["rootNodeId"])
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
            raise StorageError("bad-request", "nodeId must be a stored 32-character ID")
        parent = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if parent is None:
            raise StorageError("unknown-node", "node is absent from this snapshot")
        offset = integer(command, "offset", 0, 0, MAX_ENTRIES)
        limit = integer(command, "limit", MAX_ROWS, 1, MAX_ROWS)
        filter_text = command.get("filter", "")
        if not isinstance(filter_text, str) or len(filter_text) > 128 or not all(c.isprintable() for c in filter_text):
            raise StorageError("bad-request", "filter must be at most 128 printable characters")
        # Literal display substring, scoped to direct children; no SQL wildcard semantics.
        where = "parent=?" + (" AND instr(label,?)>0" if filter_text else "")
        args = (node_id, filter_text.casefold()) if filter_text else (node_id,)
        if filter_text:
            count, total, incomplete = db.execute("SELECT count(*), coalesce(sum(known),0), coalesce(sum(1-complete),0) FROM nodes WHERE " + where, args).fetchone()
        else:
            count, total, incomplete = db.execute("SELECT count,known,incomplete FROM totals WHERE id=?", (node_id,)).fetchone()
        rows = db.execute("SELECT * FROM nodes WHERE " + where + " ORDER BY known DESC,name,id LIMIT ? OFFSET ?", args + (limit, offset)).fetchall()
        # Map segments represent the entire current filtered directory, separately from page rows.
        segments = db.execute("SELECT * FROM nodes WHERE " + where + " ORDER BY known DESC,name,id LIMIT ?", args + (MAX_SEGMENTS - 1,)).fetchall()
        ancestors = []
        ancestor = parent
        while ancestor is not None:
            ancestors.append(dict(nodeId=ancestor[0], name=display(ancestor[2])))
            ancestor = db.execute("SELECT * FROM nodes WHERE id=?", (ancestor[1],)).fetchone() if ancestor[1] else None
        action_identity = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_identity'").fetchone())
        scope_bytes = base64.b64decode(metadata["scopeBytes"], validate=True)
        result = dict(breadcrumbs=list(reversed(ancestors)), snapshotId=metadata["snapshotId"], nodeId=node_id, node=row_value(parent, scope_bytes, action_identity),
                      snapshot=metadata, units="bytes", rows=[row_value(r, scope_bytes, action_identity) for r in rows],
                      segments=[row_value(r, scope_bytes, action_identity) for r in segments], other=None, offset=offset, total=count,
                      nextOffset=offset + len(rows) if offset + len(rows) < count else None,
                      filter=filter_text, filterScope="direct-children", childrenKnownAllocatedBytes=total, activationDurability=cache.durability())
        def update_other():
            selected = result["segments"]
            other_known = total - sum(r["knownAllocatedBytes"] for r in selected)
            partial = incomplete - sum(r["coverage"] == "partial" for r in selected)
            result["other"] = dict(kind="other", count=count - len(selected), knownAllocatedBytes=other_known,
                                   allocatedBytes=None if partial else other_known,
                                   coverage="partial" if partial else "complete", filter=filter_text,
                                   offset=len(selected)) if count > len(selected) else None

        # Escaped labels and bounded warnings still vary in byte size. Reduce the
        # page/map until the actual serialized line fits, preserving next offsets.
        while True:
            update_other()
            result["nextOffset"] = offset + len(result["rows"]) if offset + len(result["rows"]) < count else None
            try:
                wire(dict(base, **result))
                if not any(e["snapshotId"] == metadata["snapshotId"] for e in cache.catalog()["entries"]):
                    raise StorageError("snapshot-not-active", "snapshot was superseded during query")
                if file_fingerprint(os.fstat(fd)) != cache.validated[metadata["snapshotId"]]["file"]:
                    raise StorageError("unsafe-cache", "snapshot changed during query")
                return result
            except StorageError as exc:
                if exc.code != "too-large":
                    raise
                if len(result["segments"]) > 1:
                    result["segments"].pop()
                elif len(result["rows"]) > 1:
                    result["rows"].pop()
                else:
                    raise
    finally:
        db.close()
        os.close(fd)


def action(command, cache):
    """Validate stored byte identity, then hand a pinned directory to the helper.

    Old S1 inventories remain browsable; they lack action identity and must be
    explicitly rescanned before live actions. No selected file is executed.
    """
    operation = command.get("action")
    if operation not in ("open", "copy"):
        raise StorageError("bad-request", "action must be open or copy")
    db, inventory_fd, metadata = cache.read(command.get("snapshotId"))
    opened = []
    try:
        node_id = command.get("nodeId")
        if not isinstance(node_id, str) or not ID_RE.fullmatch(node_id):
            raise StorageError("bad-request", "nodeId must be a stored 32-character ID")
        node = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if node is None:
            raise StorageError("unknown-node", "node is absent from this snapshot")
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='action_identity'").fetchone():
            raise StorageError("action-unavailable", "rescan this S1 snapshot to enable validated path actions")
        scope = base64.b64decode(metadata["scopeBytes"], validate=True)
        fd = os.open(scope, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        opened.append(fd)
        if identity(scope) != metadata["identity"] or (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (metadata["identity"]["device"], metadata["identity"]["inode"]):
            raise StorageError("root-changed", "scope identity changed")
        rel = node[3]
        components = rel.split(b"/") if rel else []
        if any(part in (b"", b".", b"..") for part in components):
            raise StorageError("invalid-snapshot", "unsafe stored relative path")
        # Validate every ancestor from the already opened root; no symlink
        # resolution, path concatenation lookup, or selected-file execution.
        chain = [db.execute("SELECT * FROM nodes WHERE path=?", (b"/".join(components[:i]),)).fetchone()
                 for i in range(len(components) + 1)]
        for i, stored in enumerate(chain):
            if stored is None:
                raise StorageError("invalid-snapshot", "missing action ancestor")
            if i:
                info = os.stat(components[i-1], dir_fd=fd, follow_symlinks=False)
            else:
                info = os.fstat(fd)
            expected = db.execute("SELECT device,inode,ctime FROM action_identity WHERE id=?", (stored[0],)).fetchone()
            if expected != (info.st_dev, info.st_ino, info.st_ctime_ns):
                raise StorageError("node-changed", "entry changed since scan; rescan explicitly")
            if i and stored[4] == "directory":
                child = os.open(components[i-1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                opened.append(child)
                current = os.fstat(child)
                if (current.st_dev, current.st_ino, current.st_ctime_ns) != expected or fd_mount_id(child) != metadata["identity"]["mountId"]:
                    raise StorageError("node-changed", "entry or mount changed during action validation")
                fd = child
            elif stored[4] != "directory" and i < len(components):
                raise StorageError("node-changed", "ancestor is no longer a directory")
        path = os.path.join(scope, rel) if rel else scope
        if identity(scope) != metadata["identity"]:
            raise StorageError("root-changed", "scope changed during action validation")
        result = dict(snapshotId=metadata["snapshotId"], nodeId=node[0], action=operation,
                      pathBytes=base64.b64encode(path).decode("ascii"), status="validated")
        if operation == "copy":
            try:
                path.decode("utf-8", "strict")
            except UnicodeError:
                raise StorageError("action-unavailable", "path is not UTF-8; clipboard text cannot represent its bytes")
            import subprocess
            try:
                copied = subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=path,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                raise StorageError("action-unavailable", "Wayland clipboard helper unavailable")
            if copied.returncode:
                raise StorageError("action-unavailable", "Wayland clipboard rejected the path")
            result["status"] = "copied"
        else:
            from raman_portal import open_directory, PortalError
            try:
                open_directory(fd)
            except PortalError as exc:
                raise StorageError("action-unavailable", str(exc)) from exc
            result["status"] = "opened"
        return result
    except OSError:
        raise StorageError("node-changed", "entry is missing, unreadable or changed; rescan explicitly")
    finally:
        for fd in reversed(opened):
            os.close(fd)
        db.close()
        os.close(inventory_fd)


def cached(command, cache):
    path = unpack_path(command.get("path"))
    scope_key = hashlib.sha256(path).hexdigest()
    for name, _ in cache.snapshots():
        try:
            db, fd, metadata = cache.read(name[:-7], validate_root=False)
        except (StorageError, ValueError, sqlite3.Error):
            continue
        try:
            if metadata["scopeKey"] == scope_key:
                if identity(path) != metadata["identity"]:
                    raise StorageError("root-changed", "filesystem/root identity changed; scan explicitly")
                return dict(status="ready", snapshot=metadata, snapshotId=metadata["snapshotId"], units="bytes", activationDurability=cache.durability())
        finally:
            db.close()
            os.close(fd)
    return dict(status="unscanned", snapshotId=None, units="bytes")


def reconcile(command, cache):
    scope_key = hashlib.sha256(unpack_path(command["path"])).hexdigest()
    cause = command.get("cause", "cancel")
    candidate = command.get("candidateId")
    previous = command.get("previousSnapshotId")
    try:
        cache.writer()  # nonblocking lease, durable catalog resolution, then GC
    except StorageError as exc:
        current = next((e for e in cache.catalog()["entries"] if e["scopeKey"] == scope_key), None)
        return dict(status="uncertain", code=exc.code, message=str(exc)[:200], commitState="unknown",
                    snapshotId=current["snapshotId"] if current else None,
                    previousSnapshotRetained=(current["snapshotId"] if current else None) == previous,
                    cleanupPending=True)
    current = next((e for e in cache.catalog()["entries"] if e["scopeKey"] == scope_key), None)
    selected = current["snapshotId"] if current else None
    if candidate is not None and selected == candidate:
        return dict(status="committed", commitState="committed", snapshotId=selected,
                    previousSnapshotRetained=False, cleanupPending=False, units="bytes")
    if candidate is not None and selected != previous:
        return dict(status="uncertain", code="commit-uncertain", commitState="unknown", snapshotId=selected,
                    previousSnapshotRetained=False, cleanupPending=False)
    return dict(status="left" if cause == "leave" else "cancelled" if cause == "cancel" else "failed",
                commitState="not-committed", snapshotId=selected, previousSnapshotRetained=True,
                cleanupPending=False, **({"code": cause} if cause not in ("leave", "cancel") else {}))


def worker_main():
    raw = sys.stdin.buffer.readline(MAX_WORKER_COMMAND_BYTES + 1)
    if len(raw) > MAX_WORKER_COMMAND_BYTES:
        raise StorageError("too-large", "internal worker command exceeds limit")
    command = json.loads(raw)
    owner = os.getppid()
    # Only scans wait for commit authorization. Short queries still watch EOF
    # and owner death, but need neither a command queue nor its startup imports.
    controls = None
    if command["command"] == "storage.scan":
        import queue
        controls = queue.Queue(maxsize=1)

    action_complete = False

    def revoke_owner():
        # Controller workers are session leaders. A directly invoked test worker
        # may share its caller's group; it may only terminate itself in that case.
        if not action_complete and os.getpgrp() == os.getpid():
            os.killpg(os.getpid(), signal.SIGKILL)
        else:
            os.kill(os.getpid(), signal.SIGTERM)

    def watch_owner():
        while True:
            if os.getppid() != owner:
                revoke_owner()
                return
            ready, _, _ = select.select([sys.stdin.fileno()], [], [], 0.1)
            if ready:
                chunk = os.read(sys.stdin.fileno(), 128)
                if not chunk:
                    revoke_owner()
                    return
                if controls is not None and chunk == b"commit\n":
                    try:
                        controls.put_nowait(True)
                    except queue.Full:
                        pass

    threading.Thread(target=watch_owner, daemon=True).start()
    cache = scanner = None
    request_id = command.get("requestId")
    fields = {key: command[key] for key in ("clientId", "generation", "scanId") if key in command}

    def send(kind, **data):
        internal = {"_validated": cache.validated} if cache is not None else {}
        sys.stdout.buffer.write(wire(message(kind, request_id, **fields, **internal, **data)))
        sys.stdout.buffer.flush()

    try:
        name = command["command"]
        if name == "storage.mounts":
            base = envelope(command, "storage-capacity")
            sys.stdout.buffer.write(wire(dict(base, **capacities(command, base))))
            sys.stdout.buffer.flush()
        else:
            cache = Cache(validated=command.get("_validated"))
            if name == "storage.scan":
                scanner = Scanner(unpack_path(command.get("path")), cache, command["scanId"],
                                  lambda data: send("storage-progress", status="scanning", **data))
                metadata = scanner.prepare()
                send("storage-prepared", snapshot=metadata, previousSnapshotId=scanner.previous_id())
                try:
                    controls.get(timeout=2.0)
                except queue.Empty:
                    raise StorageError("owner-timeout", "probe did not authorize snapshot activation")
                scanner.activate(metadata)
                send("storage-result", status="ready", snapshotId=metadata["snapshotId"], snapshot=metadata, units="bytes", commitState="committed", activationDurability="durable")
            elif name == "storage.children":
                base = envelope(command, "storage-children")
                base["_validated"] = cache.validated
                sys.stdout.buffer.write(wire(dict(base, **children(command, cache, base))))
                sys.stdout.buffer.flush()
            elif name == "storage.action":
                result = action(command, cache)
                action_complete = True
                send("storage-action", **result)
            elif name == "storage.cached":
                send("storage-result", **cached(command, cache))
            elif name == "storage.reconcile":
                result = reconcile(command, cache)
                if result["status"] == "failed":
                    send("error", message="storage scan failed before catalog commit", **result)
                else:
                    send("storage-result", **result)
    except (StorageError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, StorageError) else "cache-size-limit" if isinstance(exc, sqlite3.Error) and "full" in str(exc) else "storage-unavailable"
        text = str(exc) if isinstance(exc, StorageError) else "storage metadata is unavailable"
        if scanner is not None and scanner.final_name is not None:
            # Resolve off-probe after this worker releases its lease. Never
            # classify an interrupted atomic catalog replacement as cancelled.
            send("storage-recovery-needed", code=code, message=text[:200])
        else:
            outcome = {"previousSnapshotRetained": True, "commitState": "not-committed"} if scanner is not None else {}
            sys.stdout.buffer.write(wire(error(request_id, code, text, **fields, **outcome)))
            sys.stdout.buffer.flush()
    finally:
        if scanner is not None:
            scanner.close()
        if cache is not None:
            cache.close()
        if command["command"] == "storage.action" and not action_complete and os.getpgrp() == os.getpid():
            # Failed/timed-out launcher leaders can leave children behind. The
            # error is flushed first; no successful desktop handoff is revoked.
            os.killpg(os.getpid(), signal.SIGKILL)


class Controller:
    """Bounded subprocess ownership and generation checks; no storage I/O here."""
    def __init__(self, worker_command=None):
        # Workers execute this module too; process creation and scan IDs belong
        # to the controller, so avoid importing them for every cached page.
        global subprocess, uuid
        import subprocess
        import uuid
        self.jobs = {}
        self.pending = collections.deque()
        self.generations = {}
        self.closed = False
        self.reconcile_worker_command = [sys.executable, "-S", os.path.abspath(__file__), "--worker"]
        self.worker_command = worker_command or self.reconcile_worker_command
        self.notifications = collections.deque()
        self.validated = {}

    def handle(self, command):
        request_id = command.get("requestId")
        name = command.get("command")
        allowed = {"storage.mounts", "storage.scan", "storage.cancel", "storage.leave", "storage.children", "storage.cached", "storage.action"}
        if name not in allowed:
            return error(request_id, "unknown-command", "unknown storage command")
        client = command.get("clientId")
        try:
            if not isinstance(client, str) or not 1 <= len(client) <= 64 or not all(c.isprintable() for c in client):
                raise StorageError("bad-request", "storage clientId must be 1..64 printable characters")
            generation = integer(command, "generation", None, 0, 2 ** 53 - 1)
            if client not in self.generations and len(self.generations) >= 64:
                raise StorageError("busy", "at most 64 storage clients per probe")
            prior = self.generations.get(client, -1)
            if generation < prior:
                raise StorageError("stale-generation", "storage client generation is obsolete")
            if name in ("storage.cancel", "storage.leave"):
                if generation <= prior:
                    raise StorageError("stale-generation", "cancel/leave must advance the client generation")
                scan_id = command.get("scanId")
                if name == "storage.cancel" and (not isinstance(scan_id, str) or not ID_RE.fullmatch(scan_id)):
                    raise StorageError("bad-request", "cancel requires the owned scanId")
                matching = [j for j in self.jobs.values() if j["client"] == client and
                            (name == "storage.leave" or j["command"].get("scanId") == scan_id)]
                queued = [c for c in self.pending if c["clientId"] == client and
                          (name == "storage.leave" or c.get("scanId") == scan_id)]
                if name == "storage.cancel" and not matching and not queued:
                    raise StorageError("unknown-scan", "scan is not active for this client")
                self.generations[client] = generation
                cause = "leave" if name == "storage.leave" else "cancel"
                recovering = any(j["command"]["command"] in ("storage.scan", "storage.reconcile") for j in matching) \
                    or any(c["command"] == "storage.reconcile" for c in queued)
                # Mandatory recovery survives page/client generations. Update
                # its delivery identity without discarding candidate/prior data.
                retained = collections.deque()
                for pending in self.pending:
                    if pending["clientId"] != client:
                        retained.append(pending)
                    elif pending["command"] == "storage.reconcile":
                        selected = pending in queued
                        pending.update(generation=generation, requestId=request_id if selected else None,
                                       cause=cause if selected else pending["cause"], _silent=not selected)
                        retained.append(pending)
                self.pending = retained
                for job in self.jobs.values():
                    if job["client"] == client:
                        kind = job["command"]["command"]
                        if kind == "storage.scan":
                            job["resolution"] = dict(command="storage.reconcile", requestId=request_id if job in matching else None,
                                                      _silent=job not in matching, clientId=client, generation=generation,
                                                      scanId=job["command"]["scanId"], path=job["command"]["path"], cause=cause)
                            self.stop(job)
                        elif kind == "storage.reconcile":
                            job["delivery"] = dict(requestId=request_id, generation=generation, cause=cause) if job in matching else None
                            if job in matching and (job["stopped"] or job["terminal"]):
                                job["resolution"] = dict(job["command"], requestId=request_id, generation=generation,
                                                         cause=cause, _silent=False)
                                self.stop(job)
                        else:
                            self.stop(job)
                if recovering:
                    self.launch()
                    return message("storage-progress", request_id, clientId=client, generation=generation,
                                   scanId=scan_id, status="leaving" if name == "storage.leave" else "cancelling", entries=0)
                return message("storage-result", request_id, clientId=client, generation=generation,
                               scanId=scan_id, status="left" if name == "storage.leave" else "cancelled",
                               commitState="not-committed", previousSnapshotRetained=True)
            if generation > prior:
                self.generations[client] = generation
                retained = collections.deque()
                for pending in self.pending:
                    if pending["clientId"] != client:
                        retained.append(pending)
                    elif pending["command"] == "storage.reconcile":
                        pending.update(generation=generation, requestId=None, _silent=True)
                        retained.append(pending)
                self.pending = retained
                for job in list(self.jobs.values()):
                    if job["client"] == client:
                        if job["command"]["command"] == "storage.reconcile":
                            job["delivery"] = None
                        else:
                            self.stop(job)
            # Bound client state as well as live workers and waiting requests.
            self.generations[client] = generation
            command = dict(command)
            if "pathBytes" in command:
                try:
                    raw_path = base64.b64decode(command.pop("pathBytes"), validate=True)
                    if not raw_path or b"\0" in raw_path or not os.path.isabs(raw_path) or len(raw_path) > MAX_PATH:
                        raise ValueError()
                    command["path"] = os.fsdecode(raw_path)
                except (ValueError, TypeError):
                    raise StorageError("invalid-path", "invalid byte-safe absolute path")
            command.pop("_validated", None)  # only trusted worker receipts may supply these
            if name == "storage.scan":
                if any(j["command"]["command"] == name and not j["stopped"] for j in self.jobs.values()) \
                        or any(c["command"] == name for c in self.pending):
                    raise StorageError("busy", "one storage scan may run per probe")
                command["scanId"] = uuid.uuid4().hex
                if not isinstance(command.get("path"), str):
                    raise StorageError("invalid-path", "scan requires an absolute directory path")
            if len(self.pending) >= MAX_QUEUE:
                raise StorageError("busy", "storage request queue is full")
            self.pending.append(command)
            self.launch()
            return message("storage-progress", request_id, clientId=client, generation=generation,
                           scanId=command.get("scanId"), status="queued", entries=0)
        except StorageError as exc:
            supplied_generation = command.get("generation")
            valid_client = client if isinstance(client, str) and len(client) <= 64 else None
            valid_generation = supplied_generation if type(supplied_generation) is int and 0 <= supplied_generation < 2 ** 53 else None
            return error(request_id, exc.code, str(exc), clientId=valid_client, generation=valid_generation)

    def launch(self):
        for command in list(self.pending):
            scan = command["command"] in ("storage.scan", "storage.reconcile")
            active = [j for j in self.jobs.values() if (j["command"]["command"] in ("storage.scan", "storage.reconcile")) == scan]
            if active:
                continue  # one scanner + one short query worker, including reaping
            self.pending.remove(command)
            if command["command"] in ("storage.children", "storage.cached", "storage.action", "storage.reconcile"):
                command["_validated"] = dict(self.validated)
            try:
                payload = wire(command)
                if len(payload) > MAX_WORKER_COMMAND_BYTES:
                    raise ValueError()
            except (StorageError, ValueError):
                self.notifications.append(error(command.get("requestId"), "bad-request", "storage command must contain finite bounded JSON values",
                                                clientId=command["clientId"], generation=command["generation"], scanId=command.get("scanId")))
                continue
            try:
                process = subprocess.Popen(self.reconcile_worker_command if command["command"] == "storage.reconcile" else self.worker_command,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           close_fds=True, start_new_session=True, bufsize=0)
            except OSError:
                self.notifications.append(error(command.get("requestId"), "worker-failed", "storage worker could not start",
                                                clientId=command["clientId"], generation=command["generation"], scanId=command.get("scanId")))
                continue
            os.set_blocking(process.stdout.fileno(), False)
            os.set_blocking(process.stdin.fileno(), False)
            now = time.monotonic()
            self.jobs[process.pid] = dict(process=process, command=command, client=command["clientId"],
                                         generation=command["generation"], buffer=b"", stopped=False,
                                         killAt=None, deadline=now + (SCAN_SECONDS if command["command"] == "storage.scan" else QUERY_SECONDS),
                                         lastProgress=now, terminal=False, committing=False, input=payload,
                                         delivery=None if command.get("_silent") else dict(requestId=command.get("requestId"),
                                                                                          generation=command["generation"], cause=command.get("cause")))
            self.flush_input(self.jobs[process.pid])

    def schedule_resolution(self, job, cause="worker-failed"):
        if self.closed:
            return
        command = job.get("resolution")
        if command is None:
            original = job["command"]
            command = dict(command="storage.reconcile", requestId=original.get("requestId"),
                           clientId=job["client"], generation=self.generations[job["client"]],
                           scanId=original.get("scanId"), path=original["path"], cause=cause,
                           _silent=job["stopped"] and job.get("resolution") is None)
        command = dict(command)
        current_generation = self.generations[job["client"]]
        if command["generation"] != current_generation:
            command.update(generation=current_generation, requestId=None, _silent=True)
        command["candidateId"] = job.get("candidateId", command.get("candidateId"))
        command["previousSnapshotId"] = job.get("previousSnapshotId", command.get("previousSnapshotId"))
        # Reconciliation precedes a queued scanner. Its bounded slot belongs to
        # the one scanner lifecycle, rather than adding unbounded jobs/queues.
        if len(self.pending) >= MAX_QUEUE:
            displaced = self.pending.pop()
            self.notifications.append(error(displaced.get("requestId"), "busy", "request displaced by required scan reconciliation",
                                            clientId=displaced["clientId"], generation=displaced["generation"], scanId=displaced.get("scanId")))
        self.pending.appendleft(command)

    def deliver(self, job, value):
        if job["command"]["command"] != "storage.reconcile":
            return value
        delivery = job["delivery"]
        if delivery is None or delivery["generation"] != self.generations.get(job["client"]):
            return None
        value.update(requestId=delivery["requestId"], generation=delivery["generation"])
        if value.get("commitState") == "not-committed" and delivery["cause"] in ("cancel", "leave"):
            value.update(type="storage-result", status="left" if delivery["cause"] == "leave" else "cancelled")
        return value

    def flush_input(self, job):
        if job["stopped"] or not job["input"]:
            return
        try:
            sent = os.write(job["process"].stdin.fileno(), job["input"])
            job["input"] = job["input"][sent:]
        except BlockingIOError:
            pass  # a partial large command resumes without blocking the probe
        except (OSError, ValueError):
            self.stop(job)
            reply = self.deliver(job, error(job["command"].get("requestId"), "worker-failed", "storage worker input closed",
                                            clientId=job["client"], generation=job["generation"], scanId=job["command"].get("scanId")))
            if reply is not None:
                self.notifications.append(reply)

    def stop(self, job):
        if job["stopped"]:
            return
        job["stopped"] = True
        job["buffer"] = b""
        try:
            if job.get("actionComplete"):
                job["process"].terminate()
            else:
                os.killpg(job["process"].pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        job["killAt"] = time.monotonic() + 0.2

    def fds(self):
        return [j["process"].stdout.fileno() for j in self.jobs.values()]

    def poll(self):
        now = time.monotonic()
        output = list(self.notifications)
        self.notifications.clear()
        def emit_for(job, value):
            reply = self.deliver(job, value)
            if reply is not None:
                output.append(reply)
        for pid, job in list(self.jobs.items()):
            process, command = job["process"], job["command"]
            current = not job["stopped"] and (command["command"] == "storage.reconcile" or
                                               self.generations.get(job["client"]) == job["generation"])
            # Resource lifetime is independent of permission to deliver a reply.
            if not job["stopped"] and now >= job["deadline"]:
                if command["command"] == "storage.scan":
                    job["resolution"] = dict(command="storage.reconcile", requestId=command.get("requestId"),
                                              clientId=job["client"], generation=job["generation"],
                                              scanId=command.get("scanId"), path=command["path"], cause="worker-timeout")
                elif current:
                    reply = self.deliver(job, error(command.get("requestId"), "worker-timeout", "storage worker exceeded its deadline",
                                                   clientId=job["client"], generation=job["generation"], scanId=command.get("scanId"),
                                                   commitState="unknown" if command["command"] == "storage.reconcile" else "not-committed"))
                    if reply is not None:
                        output.append(reply)
                self.stop(job)
                current = False
            if job["stopped"] and job["killAt"] is not None and now >= job["killAt"]:
                try:
                    if job.get("actionComplete"):
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                job["killAt"] = None  # signal this anchored group only once
            if current and job["input"]:
                self.flush_input(job)
                current = not job["stopped"]
            chunks, eof = [], False
            for _ in range(8):
                try:
                    chunk = os.read(process.stdout.fileno(), MAX_LINE_BYTES + 1)
                except BlockingIOError:
                    break
                if not chunk:
                    eof = True
                    break
                chunks.append(chunk)
            chunk = b"".join(chunks)
            if current:
                job["buffer"] += chunk
                if len(job["buffer"]) > MAX_LINE_BYTES and b"\n" not in job["buffer"]:
                    self.stop(job)
                    emit_for(job, error(command.get("requestId"), "too-large", "storage worker reply exceeds limit"))
                    job["terminal"] = True
                while b"\n" in job["buffer"] and not job["stopped"]:
                    raw, job["buffer"] = job["buffer"].split(b"\n", 1)
                    if len(raw) + 1 > MAX_LINE_BYTES:
                        self.stop(job)
                        emit_for(job, error(command.get("requestId"), "too-large", "storage worker reply exceeds limit",
                                            clientId=job["client"], generation=job["generation"], scanId=command.get("scanId")))
                        job["terminal"] = True
                        break
                    try:
                        value = json.loads(raw)
                    except ValueError:
                        self.stop(job)
                        emit_for(job, error(command.get("requestId"), "worker-failed", "storage worker reply was invalid",
                                            clientId=job["client"], generation=job["generation"], scanId=command.get("scanId")))
                        job["terminal"] = True
                        break
                    if value.get("clientId") != job["client"] or value.get("generation") != job["generation"] \
                            or value.get("requestId") != command.get("requestId"):
                        self.stop(job)
                        emit_for(job, error(command.get("requestId"), "worker-failed", "storage worker reply identity was invalid",
                                            clientId=job["client"], generation=job["generation"], scanId=command.get("scanId")))
                        job["terminal"] = True
                        break
                    kind = value.get("type")
                    tokens = value.pop("_validated", {})
                    if isinstance(tokens, dict):
                        for key, token in tokens.items():
                            if ID_RE.fullmatch(key) and isinstance(token, dict):
                                self.validated[key] = token
                        while len(self.validated) > MAX_SCOPES:
                            self.validated.pop(next(iter(self.validated)))
                    if kind == "storage-prepared":
                        job["candidateId"] = value.get("snapshot", {}).get("snapshotId")
                        job["previousSnapshotId"] = value.get("previousSnapshotId")
                        # Catalog replacement is commit; prepared files are invisible.
                        try:
                            os.write(process.stdin.fileno(), b"commit\n")
                            job["committing"] = True
                        except (OSError, ValueError):
                            self.stop(job)
                            emit_for(job, error(command.get("requestId"), "worker-failed", "storage worker closed before commit authorization",
                                                clientId=job["client"], generation=job["generation"], scanId=command.get("scanId")))
                            job["terminal"] = True
                    elif kind == "storage-recovery-needed":
                        job["recoveryCause"] = value.get("code", "worker-failed")
                        job["terminal"] = True
                    elif kind == "storage-progress":
                        if now - job["lastProgress"] >= PROGRESS_INTERVAL:
                            emit_for(job, value)
                            job["lastProgress"] = now
                    else:
                        if value.get("type") == "storage-action":
                            job["actionComplete"] = True
                        reply = self.deliver(job, value)
                        if reply is not None:
                            output.append(reply)
                        job["terminal"] = True
            # Keep the group leader unreaped until escalation, anchoring the
            # owned PGID even when an unfinished helper outlives its parent.
            if job["stopped"] and job["killAt"] is not None and now < job["killAt"]:
                continue
            if eof and process.poll() is not None:
                # poll() uses waitpid(WNOHANG), so every exited child is reaped.
                if command["command"] == "storage.scan" and (job["stopped"] or not job["terminal"] or job.get("recoveryCause")):
                    self.schedule_resolution(job, job.get("recoveryCause", "worker-failed"))
                elif command["command"] == "storage.reconcile" and job.get("resolution"):
                    self.schedule_resolution(job)
                elif current and not job["terminal"]:
                    reply = self.deliver(job, error(command.get("requestId"), "worker-failed", "storage worker exited",
                                                   clientId=job["client"], generation=job["generation"], scanId=command.get("scanId"),
                                                   commitState="unknown" if command["command"] == "storage.reconcile" else "not-committed"))
                    if reply is not None:
                        output.append(reply)
                process.stdin.close()
                process.stdout.close()
                del self.jobs[pid]
        if not self.closed:
            self.launch()
        output.extend(self.notifications)
        self.notifications.clear()
        return output

    def close(self):
        self.closed = True
        self.pending.clear()
        for job in self.jobs.values():
            self.stop(job)
        deadline = time.monotonic() + 0.9
        while self.jobs and time.monotonic() < deadline:
            self.poll()
            if self.jobs:
                select.select(self.fds(), [], [], 0.02)
        # No unbounded wait on uninterruptible kernel I/O. Closed pipes and the
        # watchdog also revoke ownership; SIGKILL stays pending until I/O returns.
        for job in self.jobs.values():
            try:
                if job["killAt"] is not None:
                    if job.get("actionComplete"):
                        job["process"].kill()
                    else:
                        os.killpg(job["process"].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            job["process"].stdin.close()
            job["process"].stdout.close()


if __name__ == "__main__" and "--worker" in sys.argv:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    worker_main()
