"""Storage accounting/security fixtures and actual bounded probe transport."""

import base64
import contextlib
import errno
import importlib.util
import io
import json
import os
import select
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import raman_storage as storage

LINUX = sys.platform.startswith("linux")


class ParsingTests(unittest.TestCase):
    def test_mount_escapes_and_capacity(self):
        mounts = storage.parse_mountinfo(b"42 1 8:1 /sub\\040root /My\\040Disk\\134x rw shared:1 - ext4 /dev/a rw\n")
        self.assertEqual(mounts[0]["root"], b"/sub root")
        self.assertEqual(mounts[0]["point"], b"/My Disk\\x")
        values = storage.capacity_values(SimpleNamespace(f_frsize=4096, f_bsize=1024,
                                                        f_blocks=100, f_bfree=20, f_bavail=15, f_flag=1))
        self.assertEqual(values, dict(totalBytes=409600, freeBytes=81920, availableBytes=61440,
                                      usedBytes=327680, reservedBytes=20480, readOnly=True))
        fallback = storage.capacity_values(SimpleNamespace(f_frsize=0, f_bsize=512,
                                                          f_blocks=2, f_bfree=9, f_bavail=-1, f_flag=0))
        self.assertEqual(fallback["freeBytes"], 1024)
        self.assertEqual(fallback["reservedBytes"], 1024)

    def test_mount_selection_and_exclusions(self):
        mounts = storage.parse_mountinfo(b"1 0 8:1 / / rw - ext4 /dev/a rw\n2 1 8:1 /home /home rw - btrfs /dev/a rw\n")
        self.assertEqual(storage.mount_for(b"/home/a", mounts)["id"], 2)
        self.assertEqual(storage.mount_for(b"/homework", mounts)["id"], 1)
        self.assertEqual(storage.exclusion({"type": "tmpfs"}), "RAM-backed")
        self.assertEqual(storage.exclusion({"type": "nfs4"}), "network")
        self.assertEqual(storage.exclusion({"type": "proc"}), "pseudo")
        self.assertTrue(storage.exclusion({"type": "unknown"}))

    def test_display_and_wire(self):
        self.assertEqual(storage.display(b"a\n\xff"), "a\\x0a\\xff")
        self.assertEqual(storage.display("雪".encode()), "雪")
        self.assertLessEqual(len(storage.display(b"x" * 1000)), 256)
        self.assertRaises(storage.StorageError, storage.wire, {"data": "x" * storage.MAX_LINE_BYTES})
        self.assertRaises(ValueError, storage.wire, {"data": float("nan")})

    def test_capacity_sizes_real_unicode_envelope(self):
        mounts = [dict(id=n, point=b"/" + b"long-" * 800 + str(n).encode(), type="nfs", device="8:1") for n in range(50)]
        command = dict(requestId="😀" * 64, clientId="😀" * 64, generation=2 ** 53 - 1)
        with patch.object(storage, "read_mounts", return_value=mounts):
            result = storage.capacities(command)
        self.assertLess(len(result["mounts"]), 50)
        self.assertGreater(len(result["mounts"]), 0)
        self.assertLessEqual(len(storage.wire(dict(storage.envelope(command, "storage-capacity"), **result))), storage.MAX_LINE_BYTES)
        self.assertEqual(result["nextOffset"], len(result["mounts"]))

    def test_controller_validation_and_client_bound(self):
        controller = storage.Controller()
        self.addCleanup(controller.close)
        for value in (None, True, -1, 2 ** 53, "1"):
            reply = controller.handle(dict(command="storage.mounts", clientId="a", generation=value))
            self.assertEqual(reply["code"], "bad-request")
        controller.generations = {"c%d" % n: 1 for n in range(64)}
        reply = controller.handle(dict(command="storage.mounts", clientId="new", generation=1))
        self.assertEqual(reply["code"], "busy")
        self.assertEqual(len(controller.generations), 64)

    def test_nonfinite_payload_never_launches_or_leaks_a_worker(self):
        controller = storage.Controller()
        self.addCleanup(controller.close)
        with patch.object(storage.subprocess, "Popen") as launch:
            queued = controller.handle(dict(command="storage.mounts", requestId="nan", clientId="a", generation=1, offset=float("nan")))
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(controller.poll()[0]["code"], "bad-request")
            launch.assert_not_called()
        invalid = controller.handle(dict(command="storage.mounts", requestId="gen", clientId="a", generation=float("nan")))
        self.assertIsNone(invalid["generation"])
        storage.wire(invalid)

    def test_checker_target_miss_is_nonzero_and_keeps_json_evidence(self):
        root = os.path.dirname(os.path.dirname(__file__))
        path = os.path.join(root, "scripts", "check-storage.py")
        spec = importlib.util.spec_from_file_location("storage_checker", path)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        artifacts = os.path.join(root, ".agent-artifacts", "storage-checker-tests")
        os.makedirs(artifacts, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as output:
            for small, large in ((False, True), (True, False), (True, True)):
                measured = dict(cachedPageUnder100ms=small, largeCachedPagesUnder100ms=large)
                with patch.object(checker.sys, "platform", "linux"), \
                     patch.object(checker.sys, "argv", [path, "--output", output]), \
                     patch.object(checker, "run", return_value=measured), \
                     patch.object(checker.subprocess, "check_output", return_value="test-head\n"), \
                     contextlib.redirect_stdout(io.StringIO()):
                    status = checker.main()
                self.assertEqual(status, 0 if small and large else 1)
                with open(os.path.join(output, "result.json")) as handle:
                    evidence = json.load(handle)
                self.assertEqual(evidence["status"], "PASS" if small and large else "PASS_WITH_TARGET_MISS")
                self.assertEqual(evidence["cachedPageUnder100ms"], small)
                self.assertEqual(evidence["largeCachedPagesUnder100ms"], large)


@unittest.skipUnless(LINUX, "Linux mountinfo, fd identity and allocated accounting")
class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="raman-storage-")
        self.addCleanup(self.temp.cleanup)
        self.root = os.fsencode(os.path.join(self.temp.name, "scope"))
        os.mkdir(self.root)
        self.directory = os.path.join(self.temp.name, "cache", "raman", "storage")
        self.cache = storage.Cache(self.directory)
        self.addCleanup(self.cache.close)

    def make_file(self, name, size=8192):
        path = os.path.join(self.root, os.fsencode(name))
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def build(self, path=None, limits=None):
        scanner = storage.Scanner(path or self.root, self.cache, os.urandom(16).hex(), limits=limits)
        try:
            metadata = scanner.prepare()
            scanner.activate(metadata)
            return metadata
        finally:
            scanner.close()

    def query(self, metadata, **kwargs):
        return storage.children(dict(snapshotId=metadata["snapshotId"], **kwargs), self.cache)

    def test_nonrecycled_identity_collision_and_capability_fallback(self):
        previous = self.build()
        original = storage.filesystem_generation
        # All legacy mount/device/inode/source values remain exactly identical.
        for field, replacement in (("bootId", "0" * 36), ("filesystemId", "different-volume"),
                                   ("uniqueMountId", "999999999999"), ("rootBirthNanos", 999999999)):
            with self.subTest(field=field):
                def changed(fd):
                    return dict(original(fd), **{field: replacement})
                with patch.object(storage, "filesystem_generation", changed), self.assertRaises(storage.StorageError) as caught:
                    self.query(previous)
                self.assertEqual(caught.exception.code, "root-changed")
        with patch.object(storage.ctypes, "CDLL", return_value=object()), self.assertRaises(storage.StorageError) as caught:
            storage.identity(self.root)
        self.assertEqual(caught.exception.code, "identity-unavailable")

    def test_immutable_validation_token_reuses_full_check_and_detects_mutation(self):
        previous = self.build()
        with patch.object(storage, "validate_db", wraps=storage.validate_db) as validate:
            self.query(previous)
            self.query(previous)
        self.assertEqual([call.kwargs["full"] for call in validate.call_args_list], [False, False])
        self.cache.validated.clear()
        with patch.object(storage, "validate_db", wraps=storage.validate_db) as validate:
            self.query(previous)
            self.query(previous)
        self.assertEqual([call.kwargs["full"] for call in validate.call_args_list], [True, False])
        db = sqlite3.connect(self.cache.path(previous["snapshotId"] + ".sqlite"))
        db.execute("UPDATE totals SET known=known+1")
        db.commit()
        db.close()
        with self.assertRaises(storage.StorageError) as caught:
            self.query(previous)
        self.assertEqual(caught.exception.code, "invalid-snapshot")

    def test_activation_faults_preserve_previous_and_unpublish_candidate(self):
        previous = self.build()
        for stage in ("db-sync", "db-rename", "directory-sync-1", "receipt-sync", "receipt-rename", "directory-sync-2"):
            with self.subTest(stage=stage):
                scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
                new = scanner.prepare()
                real_sync, real_rename = os.fsync, os.rename
                directory_syncs = 0
                def sync(fd):
                    nonlocal directory_syncs
                    directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                    if directory:
                        directory_syncs += 1
                    link = os.readlink("/proc/self/fd/%d" % fd)
                    if (stage == "db-sync" and link.endswith(".sqlite")) or (stage == "receipt-sync" and link.endswith(".tmp")) \
                            or stage == "directory-sync-%d" % directory_syncs and directory:
                        raise OSError(errno.EIO, "injected sync failure")
                    return real_sync(fd)
                def rename(src, dst, **kwargs):
                    if (stage == "db-rename" and dst.endswith(".sqlite")) or (stage == "receipt-rename" and dst.endswith(".receipt")):
                        raise OSError(errno.EIO, "injected rename failure")
                    return real_rename(src, dst, **kwargs)
                with patch.object(storage.os, "fsync", sync), patch.object(storage.os, "rename", rename), self.assertRaises(OSError):
                    scanner.activate(new)
                scanner.close()
                self.assertTrue(os.path.exists(self.cache.path(previous["snapshotId"] + ".sqlite")))
                self.assertEqual(storage.cached({"path": os.fsdecode(self.root)}, self.cache)["snapshotId"], previous["snapshotId"])
                self.assertFalse(os.path.exists(self.cache.path(new["snapshotId"] + ".receipt")))
        # Even cleanup failure cannot unlink any prior completed snapshot.
        scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
        new = scanner.prepare()
        with patch.object(storage.os, "fsync", side_effect=OSError(errno.EIO, "fault")), self.assertRaises(OSError):
            scanner.activate(new)
        with patch.object(storage.os, "unlink", side_effect=OSError(errno.EIO, "cleanup fault")), self.assertRaises(OSError):
            scanner.close()
        self.assertEqual(self.query(previous)["snapshotId"], previous["snapshotId"])
        scanner.close()

    def test_catalog_clock_rollback_and_three_physical_queryable_scopes(self):
        old = self.build()
        self.make_file("new-entry")
        with patch.object(storage.time, "time", return_value=old["capturedAt"] - 5):
            new = self.build()
            selected = storage.cached({"path": os.fsdecode(self.root)}, self.cache)
        self.assertEqual(selected["snapshotId"], new["snapshotId"])
        self.assertEqual(selected["snapshot"]["entries"], 2)
        self.assertEqual(self.cache.catalog()["revision"], 2)
        self.assertFalse(os.path.exists(self.cache.path(old["snapshotId"] + ".sqlite")))
        snapshots = [new]
        for n in range(3):
            root = os.path.join(self.root, ("scope%d" % n).encode())
            os.mkdir(root)
            snapshots.append(self.build(root))
            self.assertLessEqual(len(self.cache.snapshots(all_files=True)), 3)
            self.assertLessEqual(len([n for n in os.listdir(self.cache.fd) if n.endswith(".receipt")]), 3)
        with self.assertRaises(storage.StorageError) as caught:
            self.query(new)
        self.assertEqual(caught.exception.code, "snapshot-not-active")
        self.assertEqual(storage.cached({"path": os.fsdecode(self.root)}, self.cache)["status"], "unscanned")
        self.assertEqual(self.cache.catalog()["revision"], 5)

    def test_post_catalog_sync_and_cleanup_failure_truthful_recovery(self):
        previous = self.build()
        self.make_file("new-entry")
        scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
        new = scanner.prepare()
        original_sync = os.fsync
        directory_syncs = 0
        def fail_commit_sync(fd):
            nonlocal directory_syncs
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                directory_syncs += 1
                if directory_syncs == 3:
                    raise OSError(errno.EIO, "commit sync fault")
            return original_sync(fd)
        with patch.object(storage.os, "fsync", fail_commit_sync), self.assertRaises(OSError):
            scanner.activate(new)
        scanner.close()
        selected = storage.cached({"path": os.fsdecode(self.root)}, self.cache)
        self.assertEqual(selected["snapshotId"], new["snapshotId"])
        self.assertEqual(selected["activationDurability"], "pending")
        self.assertTrue(os.path.exists(self.cache.path(previous["snapshotId"] + ".sqlite")))
        command = dict(path=os.fsdecode(self.root), candidateId=new["snapshotId"], previousSnapshotId=previous["snapshotId"], cause="cancel")
        with patch.object(storage.os, "fsync", side_effect=OSError(errno.EIO, "persistent sync fault")):
            outcome = storage.reconcile(command, self.cache)
        self.assertEqual(outcome["status"], "uncertain")
        self.assertEqual(outcome["snapshotId"], new["snapshotId"])
        self.assertFalse(outcome["previousSnapshotRetained"])
        self.assertTrue(outcome["cleanupPending"])
        outcome = storage.reconcile(command, self.cache)
        self.assertEqual(outcome["status"], "committed")
        self.assertEqual(len(self.cache.snapshots(all_files=True)), 1)
        self.assertFalse(os.path.exists(self.cache.path(previous["snapshotId"] + ".sqlite")))
        # Committed selection plus cleanup failure is explicit, never ready;
        # another candidate cannot grow the store until repair succeeds.
        next_scan = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
        next_metadata = next_scan.prepare()
        real_unlink = os.unlink
        def fail_old(name, **kwargs):
            if name == new["snapshotId"] + ".sqlite":
                raise OSError(errno.EIO, "cleanup fault")
            return real_unlink(name, **kwargs)
        with patch.object(storage.os, "unlink", fail_old):
            with self.assertRaises(storage.StorageError) as caught:
                next_scan.activate(next_metadata)
            self.assertEqual(caught.exception.code, "cleanup-failed")
            next_scan.close()
            blocked = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
            with self.assertRaises(storage.StorageError) as caught:
                blocked.prepare()
            self.assertEqual(caught.exception.code, "cleanup-failed")
            blocked.close()
            self.assertEqual(len(self.cache.snapshots(all_files=True)), 2)
            with self.assertRaises(storage.StorageError) as caught:
                self.query(new)
            self.assertEqual(caught.exception.code, "snapshot-not-active")
        self.cache.recover()
        self.assertEqual(len(self.cache.snapshots(all_files=True)), 1)
        self.assertEqual(storage.cached({"path": os.fsdecode(self.root)}, self.cache)["snapshotId"], next_metadata["snapshotId"])

    def test_sparse_hardlinks_symlinks_directory_own_and_byte_paths(self):
        first = self.make_file("a-hard")
        os.link(first, os.path.join(self.root, b"z-hard"))
        with open(os.path.join(self.root, b"sparse"), "wb") as handle:
            handle.seek(16 * 1024 * 1024)
            handle.write(b"a")
        os.mkdir(os.path.join(self.root, b"empty"))
        os.symlink(b".", os.path.join(self.root, b"cycle"))
        os.symlink(b"/proc", os.path.join(self.root, b"outside"))
        for name in (b".hidden", "雪".encode(), b"new\nline", b"invalid-\xff"):
            self.make_file(name, 1)
        metadata = self.build()
        result = self.query(metadata)
        rows = {r["name"]: r for r in result["rows"]}
        self.assertEqual(rows["a-hard"]["ownAllocatedBytes"], os.lstat(first).st_blocks * 512)
        self.assertTrue(rows["z-hard"]["sharedLink"])
        self.assertEqual(rows["z-hard"]["ownAllocatedBytes"], 0)
        self.assertLess(rows["sparse"]["ownAllocatedBytes"], rows["sparse"]["apparentBytes"])
        self.assertEqual(rows["cycle"]["kind"], "symlink")
        self.assertTrue(rows[".hidden"]["hidden"])
        self.assertIn("new\\x0aline", rows)
        self.assertIn("invalid-\\xff", rows)
        expected = os.lstat(self.root).st_blocks * 512
        seen = set()
        for entry in os.scandir(self.root):
            info = entry.stat(follow_symlinks=False)
            key = (info.st_dev, info.st_ino)
            if key not in seen:
                expected += info.st_blocks * 512
                seen.add(key)
        self.assertEqual(metadata["allocatedBytes"], expected)
        self.assertEqual(result["node"]["ownAllocatedBytes"] + result["childrenKnownAllocatedBytes"], expected)
        again = self.build()
        self.assertEqual({r["nodeId"] for r in self.query(again)["rows"]}, {r["nodeId"] for r in rows.values()})
        self.assertEqual(len(self.cache.snapshots()), 1)

    def test_non_utf8_directory_navigation(self):
        name = b"dir-\xff"
        os.mkdir(os.path.join(self.root, name))
        with open(os.path.join(self.root, name, b"inside"), "wb") as handle:
            handle.write(b"x")
        metadata = self.build()
        row = self.query(metadata)["rows"][0]
        result = self.query(metadata, nodeId=row["nodeId"])
        self.assertEqual(result["rows"][0]["name"], "inside")
        self.assertEqual(result["node"]["name"], "dir-\\xff")

    def test_paging_filter_other_and_wire_bounds(self):
        for n in range(170):
            self.make_file(("雪" * 80 + "%03d" % n).encode(), 1)
        metadata = self.build()
        ids = []
        offset = 0
        while offset is not None:
            result = self.query(metadata, offset=offset)
            self.assertLessEqual(len(result["rows"]), 50)
            self.assertLessEqual(len(result["segments"]) + bool(result["other"]), 60)
            self.assertLessEqual(len(storage.wire(storage.message("storage-children", "q", **result))), 128 * 1024)
            ids.extend(r["nodeId"] for r in result["rows"])
            offset = result["nextOffset"]
        self.assertEqual(len(ids), 170)
        self.assertEqual(len(set(ids)), 170)
        result = self.query(metadata, filter="003")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["filterScope"], "direct-children")
        result = self.query(metadata)
        self.assertEqual(result["other"]["count"], 111)
        self.assertEqual(sum(r["knownAllocatedBytes"] for r in result["segments"]) + result["other"]["knownAllocatedBytes"], result["childrenKnownAllocatedBytes"])

    def test_permissions_and_vanishing_entries_are_partial(self):
        os.mkdir(os.path.join(self.root, b"blocked"))
        self.make_file("vanish")
        original_stat, original_open = os.stat, os.open

        def changed_stat(path, *args, **kwargs):
            if path == b"vanish" and kwargs.get("dir_fd") is not None:
                raise FileNotFoundError(errno.ENOENT, "fixture disappeared")
            return original_stat(path, *args, **kwargs)

        def denied_open(path, *args, **kwargs):
            if path == b"blocked" and kwargs.get("dir_fd") is not None:
                raise PermissionError(errno.EACCES, "fixture denied")
            return original_open(path, *args, **kwargs)

        with patch.object(storage.os, "stat", changed_stat), patch.object(storage.os, "open", denied_open):
            metadata = self.build()
        self.assertEqual(metadata["coverage"], "partial")
        self.assertIsNone(metadata["allocatedBytes"])
        self.assertEqual(metadata["unreadable"], 1)
        self.assertEqual(metadata["changed"], 1)
        rows = {r["name"]: r for r in self.query(metadata)["rows"]}
        self.assertIsNone(rows["vanish"]["ownAllocatedBytes"])
        self.assertIsNone(rows["blocked"]["allocatedBytes"])
        self.assertEqual(rows["blocked"]["ownAllocatedBytes"], os.lstat(os.path.join(self.root, b"blocked")).st_blocks * 512)
        self.assertEqual(rows["blocked"]["knownAllocatedBytes"], rows["blocked"]["ownAllocatedBytes"])
        self.assertEqual(rows["vanish"]["reason"], "vanished")

    def test_unreadable_directory_keeps_own_overhead(self):
        os.mkdir(os.path.join(self.root, b"blocked"))
        original = os.scandir

        def denied(fd):
            if os.readlink("/proc/self/fd/%d" % fd).endswith("/blocked"):
                raise PermissionError(errno.EACCES, "fixture denied")
            return original(fd)

        with patch.object(storage.os, "scandir", denied):
            metadata = self.build()
        row = self.query(metadata)["rows"][0]
        self.assertEqual(row["ownAllocatedBytes"], os.lstat(os.path.join(self.root, b"blocked")).st_blocks * 512)
        self.assertIsNone(row["allocatedBytes"])
        self.assertEqual(metadata["unreadable"], 1)

    def test_directory_to_symlink_race_does_not_escape(self):
        os.mkdir(os.path.join(self.root, b"race"))
        original_open = os.open

        def swapped(path, *args, **kwargs):
            if path == b"race" and kwargs.get("dir_fd") is not None:
                os.rmdir(os.path.join(self.root, b"race"))
                os.symlink(b"/proc", os.path.join(self.root, b"race"))
            return original_open(path, *args, **kwargs)

        with patch.object(storage.os, "open", swapped):
            metadata = self.build()
        self.assertEqual(metadata["entries"], 2)
        self.assertEqual(metadata["coverage"], "partial")
        self.assertEqual(self.query(metadata)["rows"][0]["reason"], "changed-directory")

    def test_same_device_nested_mount_is_pruned(self):
        nested = os.path.join(self.root, b"nested")
        os.mkdir(nested)
        with open(os.path.join(nested, b"private"), "wb") as handle:
            handle.write(b"x")
        mounts = storage.read_mounts()
        own = storage.mount_for(self.root, mounts)
        mounts.append(dict(own, id=own["id"] + 100000, point=nested, root=b"/subvolume"))
        with patch.object(storage, "read_mounts", return_value=mounts):
            metadata = self.build()
        self.assertEqual(metadata["entries"], 2)
        self.assertEqual(metadata["excluded"], 1)
        self.assertIsNone(self.query(metadata)["rows"][0]["allocatedBytes"])

    def test_new_same_device_bind_mount_fd_is_pruned(self):
        os.mkdir(os.path.join(self.root, b"nested"))
        real_mount_id = storage.fd_mount_id

        def swapped(fd):
            if os.readlink("/proc/self/fd/%d" % fd).endswith("/nested"):
                return real_mount_id(fd) + 100000
            return real_mount_id(fd)

        with patch.object(storage, "fd_mount_id", swapped):
            metadata = self.build()
        self.assertEqual(metadata["excluded"], 1)
        self.assertEqual(metadata["entries"], 2)

    def test_limits_preserve_previous_snapshot(self):
        previous = self.build()
        self.make_file("one")
        self.make_file("two")
        for limits, code in (({"entries": 2}, "scan-entry-limit"), ({"depth": 0}, "scan-depth-limit"),
                             ({"seconds": 0}, "scan-time-limit")):
            with self.subTest(code=code), self.assertRaises(storage.StorageError) as caught:
                self.build(limits=limits)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(self.cache.snapshots()[0][0], previous["snapshotId"] + ".sqlite")
            self.assertFalse(any(n.startswith(".scan") for n in os.listdir(self.cache.fd)))
        self.assertEqual(self.query(previous)["total"], 0)

    def test_pending_fanout_counts_against_global_entry_budget(self):
        os.mkdir(os.path.join(self.root, b"a"))
        self.make_file("b")
        self.make_file("c")
        for name in (b"one", b"two", b"three"):
            with open(os.path.join(self.root, b"a", name), "wb") as handle:
                handle.write(b"x")
        scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex(), limits={"entries": 6})
        try:
            with self.assertRaises(storage.StorageError) as caught:
                scanner.prepare()
            self.assertEqual(caught.exception.code, "scan-entry-limit")
            self.assertEqual(scanner.count, 2)
        finally:
            scanner.close()

    def test_root_replaced_invalidates_cache_and_prepared_candidate(self):
        previous = self.build()
        scanner = storage.Scanner(self.root, self.cache, os.urandom(16).hex())
        try:
            candidate = scanner.prepare()
            os.rename(self.root, self.root + b"-old")
            os.mkdir(self.root)
            with self.assertRaises(storage.StorageError) as caught:
                scanner.activate(candidate)
            self.assertEqual(caught.exception.code, "root-changed")
            with self.assertRaises(storage.StorageError) as caught:
                self.query(previous)
            self.assertEqual(caught.exception.code, "root-changed")
            self.assertEqual(self.cache.snapshots()[0][0], previous["snapshotId"] + ".sqlite")
        finally:
            scanner.close()

    def test_mount_remount_identity_invalidates_cache(self):
        previous = self.build()
        mounts = storage.read_mounts()
        target = storage.mount_for(self.root, mounts)
        target["id"] += 100000
        with patch.object(storage, "read_mounts", return_value=mounts), self.assertRaises(storage.StorageError) as caught:
            self.query(previous)
        self.assertEqual(caught.exception.code, "root-changed")

    def test_cache_permissions_symlinks_version_and_writer_lease(self):
        metadata = self.build()
        self.assertEqual(stat.S_IMODE(os.fstat(self.cache.fd).st_mode), 0o700)
        name = metadata["snapshotId"] + ".sqlite"
        self.assertEqual(stat.S_IMODE(self.cache.safe_stat(name).st_mode), 0o600)
        other = storage.Cache(self.directory)
        try:
            with self.assertRaises(storage.StorageError) as caught:
                other.writer()
            self.assertEqual(caught.exception.code, "cache-busy")
        finally:
            other.close()
        # Release test's scan lock, then corrupt the version in its owned fixture.
        os.close(self.cache.lock_fd)
        self.cache.lock_fd = None
        db = sqlite3.connect(self.cache.path(name))
        db.execute("PRAGMA user_version=999")
        db.close()
        with self.assertRaises(storage.StorageError) as caught:
            self.query(metadata)
        self.assertEqual(caught.exception.code, "invalid-snapshot")
        replacement = self.build()
        self.assertEqual(len(self.cache.snapshots()), 1)
        self.assertEqual(self.query(replacement)["total"], 0)
        os.chmod(self.cache.path(replacement["snapshotId"] + ".sqlite"), 0o644)
        with self.assertRaises(storage.StorageError) as caught:
            self.query(replacement)
        self.assertEqual(caught.exception.code, "unsafe-cache")

    def test_cache_directory_symlink_rejected(self):
        os.close(self.cache.fd)
        self.cache.fd = os.open(self.temp.name, os.O_RDONLY | os.O_DIRECTORY)
        os.rmdir(self.directory)
        os.symlink(self.temp.name, self.directory)
        with self.assertRaises(OSError):
            storage.Cache(self.directory)

    def test_cache_scope_and_size_age_limits(self):
        # Each independent Scanner stands in for one short-lived real worker.
        for n in range(4):
            root = os.path.join(self.root, str(n).encode())
            os.mkdir(root)
            self.build(root)
            os.close(self.cache.lock_fd)
            self.cache.lock_fd = None
        self.assertEqual(len(self.cache.snapshots()), 3)
        name = self.cache.snapshots()[0][0]
        old = time.time() - storage.MAX_CACHE_AGE - 1
        os.utime(self.cache.path(name), (old, old))
        with self.assertRaises(storage.StorageError) as caught:
            self.cache.read(name[:-7])
        self.assertEqual(caught.exception.code, "stale-snapshot")
        self.cache.writer()
        # Expiry rejects reuse; GC cannot silently unselect a prior result
        # merely because a later scan starts and then fails.
        self.assertEqual(len(self.cache.snapshots()), 3)
        with patch.object(storage, "MAX_CACHE_BYTES", 1024), self.assertRaises(storage.StorageError) as caught:
            self.build()
        self.assertEqual(caught.exception.code, "cache-size-limit")

    def test_cached_lookup_ignores_unrelated_missing_root(self):
        previous = self.build()
        os.close(self.cache.lock_fd)
        self.cache.lock_fd = None
        other_root = os.path.join(self.temp.name, "other")
        os.mkdir(other_root)
        self.build(os.fsencode(other_root))
        os.rmdir(other_root)
        reply = storage.cached({"path": os.fsdecode(self.root)}, self.cache)
        self.assertEqual(reply["snapshotId"], previous["snapshotId"])


class Pipe:
    def __init__(self, temp, worker_source=None):
        root = os.path.dirname(os.path.dirname(__file__))
        command = [sys.executable, os.path.join(root, "raman_probe.py"), "--defer-history"]
        if worker_source is not None:
            source = ("import signal,sys,raman_probe as p,raman_storage as s; "
                      "C=s.Controller; s.Controller=lambda: C(%r); "
                      "sys.argv.append('--defer-history'); parent=p.detach_from_kill(); "
                      "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0)); p.main(parent)") % [sys.executable, "-c", worker_source]
            command = [sys.executable, "-c", source]
        self.p = subprocess.Popen(command,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=dict(os.environ, XDG_STATE_HOME=os.path.join(temp, "state"),
                                           XDG_CACHE_HOME=os.path.join(temp, "cache"), PYTHONDONTWRITEBYTECODE="1"), bufsize=0)
        self.buffer = b""
        self.messages = []
        self.line_sizes = []

    def send(self, value):
        raw = value.encode() + b"\n" if isinstance(value, str) else storage.wire(value)
        self.p.stdin.write(raw)

    def read(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                self.line_sizes.append(len(raw) + 1)
                message = json.loads(raw)
                self.messages.append(message)
                if predicate(message):
                    return message
            if not select.select([self.p.stdout], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            chunk = os.read(self.p.stdout.fileno(), 262144)
            if not chunk:
                break
            self.buffer += chunk
        raise AssertionError("no matching protocol response: %r" % [(m.get("type"), m.get("requestId"), m.get("code")) for m in self.messages[-15:]])

    def answer(self, request_id, kind=None):
        return self.read(lambda m: m.get("requestId") == request_id and (kind is None or m["type"] == kind))

    def close(self):
        if self.p.stdin and not self.p.stdin.closed:
            self.p.stdin.close()
        try:
            self.p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=3)
        self.p.stdout.close()
        self.p.stderr.close()


@unittest.skipUnless(LINUX, "Actual Linux probe/workers")
class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="raman-storage-ipc-")
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, XDG_CACHE_HOME=os.path.join(self.temp.name, "cache"),
                         XDG_STATE_HOME=os.path.join(self.temp.name, "state"))
        env.start()
        self.addCleanup(env.stop)
        self.root = os.path.join(self.temp.name, "scope")
        os.mkdir(self.root)
        self.pipe = Pipe(self.temp.name)
        self.addCleanup(self.pipe.close)
        self.pipe.read(lambda m: m["type"] == "summary")

    def test_completed_scan_then_cancel_command_error_and_authoritative_cache(self):
        # Actual controller/worker lifecycle: completion removes ownership before
        # a higher-generation Cancel arrives. The UI may discard that terminal.
        with open(os.path.join(self.root, "owned-file"), "wb") as stream:
            stream.write(b"x" * 4096)
        controller = storage.Controller()
        self.addCleanup(controller.close)
        queued = controller.handle(dict(command="storage.scan", requestId="completed", clientId="test", generation=1, path=self.root))
        output = self.pump_controller(controller, lambda messages: not controller.jobs and not controller.pending)
        completed = next(message for message in output if message["type"] == "storage-result")
        self.assertEqual(completed["status"], "ready")
        reply = controller.handle(dict(command="storage.cancel", requestId="late-cancel", clientId="test", generation=2, scanId=queued["scanId"]))
        self.assertEqual(reply["code"], "unknown-scan")
        self.assertEqual((reply["requestId"], reply["clientId"], reply["generation"]),
                         ("late-cancel", "test", 2))
        self.assertNotIn("scanId", reply)
        controller.handle(dict(command="storage.cached", requestId="selected", clientId="test", generation=2, path=self.root))
        output = self.pump_controller(controller, lambda messages: any(message["type"] == "storage-result" for message in messages))
        selected = next(message for message in output if message["type"] == "storage-result")
        self.assertEqual(selected["snapshot"]["snapshotId"], completed["snapshot"]["snapshotId"])
        self.assertEqual(selected["activationDurability"], "durable")

    def command(self, name, request, generation=1, **kwargs):
        self.pipe.send(dict(command="storage." + name, requestId=request, clientId="test", generation=generation, **kwargs))

    def scan(self, request="scan", generation=1):
        self.command("scan", request, generation=generation, path=self.root)
        queued = self.pipe.answer(request, "storage-progress")
        result = self.pipe.answer(request, "storage-result")
        self.assertEqual(queued["scanId"], result["scanId"])
        return result

    def busy_fixture(self, count=10000):
        # Large metadata fanout keeps the isolated job active long enough to cancel.
        for n in range(count):
            os.mkdir(os.path.join(self.root, "%05d" % n))

    def worker_pids(self):
        found = []
        for entry in os.scandir("/proc"):
            if entry.name.isdigit():
                try:
                    with open(entry.path + "/cmdline", "rb") as handle:
                        cmdline = handle.read()
                    with open(entry.path + "/environ", "rb") as handle:
                        env = handle.read()
                    if b"raman_storage.py" in cmdline and self.temp.name.encode() in env:
                        found.append(int(entry.name))
                except (OSError, PermissionError):
                    pass
        return found

    def test_capacity_scan_children_cache_and_history_separation(self):
        self.command("mounts", "mounts")
        capacity = self.pipe.answer("mounts", "storage-capacity")
        self.assertLessEqual(len(capacity["mounts"]), 50)
        self.assertTrue(any(m["excluded"] for m in capacity["mounts"]))
        with open(os.path.join(self.root, "file"), "wb") as handle:
            handle.write(b"test")
        result = self.scan()
        self.command("children", "children", snapshotId=result["snapshotId"])
        children = self.pipe.answer("children", "storage-children")
        self.assertEqual(children["rows"][0]["name"], "file")
        self.command("cached", "cached", path=self.root)
        cached = self.pipe.answer("cached", "storage-result")
        self.assertEqual(cached["snapshotId"], result["snapshotId"])
        self.pipe.send(dict(command="history.clear", requestId="clear"))
        self.pipe.answer("clear", "history-cleared")
        self.command("children", "after-clear", snapshotId=result["snapshotId"])
        self.pipe.answer("after-clear", "storage-children")
        self.assertTrue(all(size <= storage.MAX_LINE_BYTES for size in self.pipe.line_sizes))
        for m in self.pipe.messages:
            if m["type"].startswith("storage-"):
                self.assertEqual(m["schemaVersion"], 1)
                self.assertEqual(m["clientId"], "test")
                self.assertEqual(m["generation"], 1)
                self.assertIsInstance(m["time"], float)
        self.assertFalse(any(m["type"] == "apps" for m in self.pipe.messages))

    def test_cancel_responsive_preserves_snapshot_and_reaps(self):
        previous = self.scan()
        self.busy_fixture()
        self.command("scan", "rescan", generation=2, path=self.root)
        queued = self.pipe.answer("rescan", "storage-progress")
        time.sleep(0.1)
        pids = self.worker_pids()
        self.assertTrue(pids)
        start = time.monotonic()
        self.command("cancel", "cancel", generation=3, scanId=queued["scanId"])
        acknowledgement = self.pipe.answer("cancel", "storage-progress")
        self.assertEqual(acknowledgement["status"], "cancelling")
        self.assertLess(time.monotonic() - start, 0.25)
        result = self.pipe.answer("cancel", "storage-result")
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertTrue(result["previousSnapshotRetained"])
        deadline = start + 1.0
        while any(os.path.exists("/proc/%d" % pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(any(os.path.exists("/proc/%d" % pid) for pid in pids))
        self.pipe.send("refresh")
        self.pipe.read(lambda m: m["type"] == "summary")
        self.command("children", "previous", generation=3, snapshotId=previous["snapshotId"])
        self.pipe.answer("previous", "storage-children")
        self.assertFalse(any(m.get("requestId") == "rescan" and m["type"] == "storage-result" for m in self.pipe.messages))
        self.command("children", "stale", generation=2, snapshotId=previous["snapshotId"])
        self.assertEqual(self.pipe.answer("stale")["code"], "stale-generation")

    def test_leave_and_eof_cleanup(self):
        self.busy_fixture()
        self.command("scan", "scan", path=self.root)
        queued = self.pipe.answer("scan", "storage-progress")
        time.sleep(0.1)
        self.command("leave", "leave", generation=2)
        self.assertEqual(self.pipe.answer("leave", "storage-result")["status"], "left")
        self.command("scan", "again", generation=3, path=self.root)
        self.pipe.answer("again", "storage-progress")
        time.sleep(0.1)
        pids = self.worker_pids()
        self.assertTrue(pids)
        start = time.monotonic()
        self.pipe.p.stdin.close()
        self.pipe.p.wait(timeout=2)
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(any(os.path.exists("/proc/%d" % pid) for pid in pids))

    def test_waiting_parent_death_reaps_storage(self):
        self.busy_fixture()
        self.command("scan", "scan", path=self.root)
        self.pipe.answer("scan", "storage-progress")
        time.sleep(0.1)
        pids = self.worker_pids()
        self.assertTrue(pids)
        os.kill(self.pipe.p.pid, signal.SIGKILL)
        self.pipe.p.wait(timeout=2)
        deadline = time.monotonic() + 1.5
        while any(os.path.exists("/proc/%d" % pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(any(os.path.exists("/proc/%d" % pid) for pid in pids))

    def test_short_query_watchdog_exits_on_command_eof(self):
        # Keep the owner alive and the query blocked: EOF alone must revoke it,
        # even though short queries no longer create a scan-control queue.
        source = """import signal,sys,time,raman_storage as s
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
def blocked(*args):
 print('entered', flush=True)
 time.sleep(30)
s.children=blocked
s.worker_main()
"""
        process = subprocess.Popen([sys.executable, "-S", "-c", source],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), bufsize=0)

        def cleanup():
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()

        self.addCleanup(cleanup)
        process.stdin.write(storage.wire(dict(command="storage.children", requestId="blocked", clientId="test", generation=1)))
        self.assertTrue(select.select([process.stdout], [], [], 3)[0])
        self.assertEqual(process.stdout.readline(), b"entered\n")
        process.stdin.close()
        self.assertEqual(process.wait(timeout=1), 0)

    def test_summaries_and_progress_with_worker_active(self):
        self.busy_fixture(50000)
        self.command("scan", "scan", path=self.root)
        self.pipe.answer("scan", "storage-progress")
        self.pipe.send("refresh")
        start = time.monotonic()
        self.pipe.read(lambda m: m["type"] == "summary")
        self.assertLess(time.monotonic() - start, 0.25)
        result = self.pipe.read(lambda m: m.get("requestId") == "scan" and m["type"] == "storage-result", timeout=20)
        self.assertEqual(result["snapshot"]["entries"], 50001)
        progress = [m["time"] for m in self.pipe.messages if m["type"] == "storage-progress" and m.get("status") == "scanning"]
        self.assertTrue(progress)
        self.assertTrue(all(b - a >= 0.49 for a, b in zip(progress, progress[1:])))
        summaries = [m["monotonic"] for m in self.pipe.messages if m["type"] == "summary"]
        self.assertTrue(all(b - a < 2.25 for a, b in zip(summaries, summaries[1:])))

    def test_actual_protocol_limit_crash_and_timeout_preserve_snapshot(self):
        previous = self.scan()
        for n in range(10):
            os.mkdir(os.path.join(self.root, str(n)))
        self.pipe.close()
        self.pipe = Pipe(self.temp.name, "import raman_storage as s;s.MAX_ENTRIES=5;s.worker_main()")
        self.addCleanup(self.pipe.close)
        self.command("scan", "limited", generation=2, path=self.root)
        self.pipe.answer("limited", "storage-progress")
        self.assertEqual(self.pipe.answer("limited", "error")["code"], "scan-entry-limit")
        # A crash after private candidate creation has no activation permission.
        self.pipe.close()
        crash = ("import os,json,raman_storage as s;c=json.loads(input());cache=s.Cache();"
                 "scan=s.Scanner(s.unpack_path(c['path']),cache,c['scanId']);scan.prepare();os._exit(7)")
        self.pipe = Pipe(self.temp.name, crash)
        self.addCleanup(self.pipe.close)
        self.command("scan", "crash", generation=3, path=self.root)
        self.pipe.answer("crash", "storage-progress")
        self.assertEqual(self.pipe.answer("crash", "error")["code"], "worker-failed")
        cache_path = os.path.join(self.temp.name, "cache", "raman", "storage")
        names = os.listdir(cache_path)
        self.assertIn(previous["snapshotId"] + ".sqlite", names)
        self.assertFalse(any(name.startswith(".scan-") for name in names))
        self.pipe.close()
        # Directly inject a short parent deadline for a test-owned stuck worker.
        controller = storage.Controller([sys.executable, "-c", "import time;time.sleep(30)"])
        self.addCleanup(controller.close)
        controller.handle(dict(command="storage.scan", requestId="timeout", clientId="test", generation=4, path=self.root))
        job = next(iter(controller.jobs.values()))
        pid = job["process"].pid
        controller.poll()  # flush initial command without authorizing a reply
        job["deadline"] = time.monotonic() - 1
        output = controller.poll()
        deadline = time.monotonic() + 2
        while (controller.jobs or controller.pending) and time.monotonic() < deadline:
            output.extend(controller.poll())
            time.sleep(0.01)
        self.assertFalse(controller.jobs)
        self.assertTrue(any(m.get("code") == "worker-timeout" for m in output))
        self.assertFalse(os.path.exists("/proc/%d" % pid))
        self.assertIn(previous["snapshotId"] + ".sqlite", os.listdir(cache_path))
        # Restart and explicitly scan: abandoned private candidate is removed.
        self.pipe = Pipe(self.temp.name)
        self.addCleanup(self.pipe.close)
        self.scan("recovered", generation=5)
        self.assertFalse(any(name.startswith(".scan-") for name in os.listdir(cache_path)))

    def test_foreign_cancel_queued_cancel_spawn_failure_and_queue_bound(self):
        source = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)"
        controller = storage.Controller([sys.executable, "-c", source])
        self.addCleanup(controller.close)
        first = controller.handle(dict(command="storage.scan", requestId="one", clientId="one", generation=1, path=self.root))
        wrong = controller.handle(dict(command="storage.cancel", clientId="two", generation=1, scanId=first["scanId"]))
        self.assertEqual(wrong["code"], "unknown-scan")
        controller.handle(dict(command="storage.cancel", clientId="one", generation=2, scanId=first["scanId"]))
        queued = controller.handle(dict(command="storage.scan", requestId="queued", clientId="one", generation=3, path=self.root))
        self.assertTrue(controller.pending)
        cancelled = controller.handle(dict(command="storage.cancel", clientId="one", generation=4, scanId=queued["scanId"]))
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(controller.pending)
        # The short-query worker lane bounds pending requests independently of scans.
        for n in range(10):
            answer = controller.handle(dict(command="storage.mounts", requestId=str(n), clientId="query", generation=1))
        self.assertEqual(answer["code"], "busy")
        self.assertLessEqual(len(controller.pending), storage.MAX_QUEUE)
        self.assertLessEqual(len(controller.jobs), 2)
        failed = storage.Controller(["/missing/raman-test-worker"])
        self.addCleanup(failed.close)
        failed.handle(dict(command="storage.mounts", requestId="spawn", clientId="spawn", generation=1))
        self.assertEqual(failed.poll()[0]["code"], "worker-failed")

    def test_cancel_prepared_before_commit_and_no_inherited_owner_fds(self):
        previous = self.scan()
        self.pipe.close()
        # A prepared worker waits; the controller's stdin-first ordering revokes
        # authorization before a stale prepared message can replace the cache.
        controller = storage.Controller()
        self.addCleanup(controller.close)
        result = controller.handle(dict(command="storage.scan", requestId="prepared", clientId="owner", generation=1, path=self.root))
        job = next(iter(controller.jobs.values()))
        pid = job["process"].pid
        deadline = time.monotonic() + 3
        while not select.select([job["process"].stdout], [], [], 0.02)[0] and time.monotonic() < deadline:
            pass
        self.assertTrue(select.select([job["process"].stdout], [], [], 0)[0])
        controller.handle(dict(command="storage.cancel", requestId="cancel", clientId="owner", generation=2, scanId=result["scanId"]))
        for m in controller.poll():
            self.assertNotEqual(m["type"], "storage-result")
        controller.close()
        self.assertFalse(os.path.exists("/proc/%d" % pid))
        self.assertIn(previous["snapshotId"] + ".sqlite", os.listdir(os.path.join(self.temp.name, "cache", "raman", "storage")))
        # Probe history/dispatcher leases must not survive in a scan subprocess.
        self.pipe = Pipe(self.temp.name)
        self.addCleanup(self.pipe.close)
        self.pipe.send(dict(command="history.configure", requestId="live-state", enabled=True, persist=True))
        self.pipe.answer("live-state", "history-configured")
        self.pipe.send("refresh")
        self.pipe.read(lambda m: m["type"] == "summary")
        self.busy_fixture()
        self.command("scan", "fds", generation=3, path=self.root)
        self.pipe.answer("fds", "storage-progress")
        time.sleep(0.1)
        pids = self.worker_pids()
        self.assertTrue(pids)
        for worker in pids:
            links = []
            for fd in os.listdir("/proc/%d/fd" % worker):
                try:
                    links.append(os.readlink("/proc/%d/fd/%s" % (worker, fd)))
                except FileNotFoundError:
                    pass
            history_directory = os.path.join(self.temp.name, "state")
            self.assertFalse(any(link == history_directory or link.startswith(history_directory + os.sep)
                                 for link in links))
            self.assertFalse(any("dispatch" in link or "history.lock" in link for link in links))

    def test_large_literal_utf8_command_internal_expansion(self):
        path = self.root
        for n in range(10):
            path = os.path.join(path, "雪" * 80)
            os.mkdir(path)
        command = dict(command="storage.scan", requestId="utf8", clientId="test", generation=1, path=path)
        raw = json.dumps(command, ensure_ascii=False).encode("utf-8") + b"\n"
        self.assertLess(len(raw), 4096)
        self.assertGreater(len(storage.wire(command)), 4096)
        self.pipe.p.stdin.write(raw)
        result = self.pipe.answer("utf8", "storage-result")
        self.assertEqual(result["snapshot"]["entries"], 1)

    def test_actual_unicode_envelope_bound_with_partial_warning_metadata(self):
        for n in range(150):
            with open(os.path.join(self.root, "😀" * 62 + "%03d" % n), "wb") as handle:
                handle.write(b"x")
        nested = os.path.join(self.root, "😁" * 63, "😇" * 63)
        os.makedirs(nested)
        for n in range(24):
            os.mkdir(os.path.join(nested, "😭" * 62 + "%02d" % n))
        source = """import errno,os,raman_storage as s
real=s.os.open
def denied(path,*a,**k):
 if isinstance(path,bytes) and path.startswith('😭'.encode()) and k.get('dir_fd') is not None:
  raise PermissionError(errno.EACCES,'fixture')
 return real(path,*a,**k)
s.os.open=denied;s.worker_main()
"""
        pipe = Pipe(self.temp.name, source)
        self.addCleanup(pipe.close)
        rid = "😀" * 64
        pipe.send(dict(command="storage.scan", requestId="scan", clientId=rid, generation=1, path=self.root))
        ready = pipe.answer("scan", "storage-result")
        pipe.send(dict(command="storage.children", requestId=rid, clientId=rid, generation=1, snapshotId=ready["snapshotId"]))
        result = pipe.answer(rid, "storage-children")
        self.assertEqual(result["snapshot"]["coverage"], "partial")
        self.assertGreater(len(result["rows"]), 0)
        self.assertIsNotNone(result["nextOffset"])
        self.assertLessEqual(max(pipe.line_sizes), storage.MAX_LINE_BYTES)
        self.assertNotIn("_validated", result)
        pipe.send(dict(command="storage.mounts", requestId=rid, clientId=rid, generation=1))
        pipe.answer(rid, "storage-capacity")
        self.assertLessEqual(max(pipe.line_sizes), storage.MAX_LINE_BYTES)

    def test_activation_boundary_cancel_leave_eof_and_crash_preserve_previous(self):
        previous = self.scan()
        self.pipe.close()
        cache_directory = os.path.join(self.temp.name, "cache", "raman", "storage")
        for boundary in ("db-renamed", "receipt-renamed"):
            for action in ("cancel", "leave", "eof", "crash", "owner-death"):
                with self.subTest(boundary=boundary, action=action):
                    normal = Pipe(self.temp.name)
                    normal.send(dict(command="storage.scan", requestId="previous", clientId="normal", generation=1, path=self.root))
                    previous = normal.answer("previous", "storage-result")
                    normal.close()
                    marker = os.path.join(self.temp.name, "boundary")
                    if os.path.exists(marker):
                        os.unlink(marker)
                    source = """import os,signal,sys,time,raman_storage as s
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
real=s.os.rename
def boundary(src,dst,**k):
 real(src,dst,**k)
 if dst.endswith(%r):
  open(%r,'w').write(str(os.getpid()))
  time.sleep(30)
s.os.rename=boundary;s.worker_main()
""" % (".sqlite" if boundary == "db-renamed" else ".receipt", marker)
                    pipe = Pipe(self.temp.name, source)
                    self.addCleanup(pipe.close)
                    pipe.send(dict(command="storage.scan", requestId="rescan", clientId="boundary", generation=1, path=self.root))
                    queued = pipe.answer("rescan", "storage-progress")
                    deadline = time.monotonic() + 3
                    # Reading summaries drives actual prepared/commit transport.
                    while not os.path.exists(marker) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(os.path.exists(marker))
                    with open(marker) as handle:
                        worker = int(handle.read())
                    if action in ("cancel", "leave"):
                        pipe.send(dict(command="storage." + action, requestId="stop", clientId="boundary", generation=2, scanId=queued["scanId"]))
                        result = pipe.answer("stop", "storage-result")
                        self.assertTrue(result["previousSnapshotRetained"])
                        self.assertEqual(result["commitState"], "not-committed")
                    elif action == "crash":
                        os.kill(worker, signal.SIGKILL)
                        pipe.answer("rescan", "error")
                    elif action == "owner-death":
                        os.kill(pipe.p.pid, signal.SIGKILL)
                    pipe.close()
                    self.assertTrue(os.path.exists(os.path.join(cache_directory, previous["snapshotId"] + ".sqlite")))
                    self.assertTrue(os.path.exists(os.path.join(cache_directory, previous["snapshotId"] + ".receipt")))

    def test_authoritative_selection_forced_cancel_and_crash_at_commit(self):
        for boundary in ("receipt", "catalog"):
            for action in ("cancel", "leave", "crash"):
                with self.subTest(boundary=boundary, action=action):
                    normal = Pipe(self.temp.name)
                    normal.send(dict(command="storage.scan", requestId="prior", clientId="normal", generation=1, path=self.root))
                    prior = normal.answer("prior", "storage-result")
                    normal.close()
                    marker = os.path.join(self.temp.name, "commit-marker")
                    if os.path.exists(marker):
                        os.unlink(marker)
                    with open(os.path.join(self.root, "new-" + boundary + action), "w") as handle:
                        handle.write("new")
                    source = """import os,signal,time,raman_storage as s
signal.signal(signal.SIGTERM,signal.SIG_IGN)
real=s.os.rename
def pause(src,dst,**k):
 real(src,dst,**k)
 if dst.endswith('.receipt') if %r == 'receipt' else dst == 'catalog.json':
  open(%r,'w').write(str(os.getpid()));time.sleep(30)
s.os.rename=pause;s.worker_main()
""" % (boundary, marker)
                    pipe = Pipe(self.temp.name, source)
                    self.addCleanup(pipe.close)
                    pipe.send(dict(command="storage.scan", requestId="scan", clientId="owner", generation=1, path=self.root))
                    queued = pipe.answer("scan", "storage-progress")
                    deadline = time.monotonic() + 3
                    while not os.path.exists(marker) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(os.path.exists(marker))
                    cache = storage.Cache(os.path.join(self.temp.name, "cache", "raman", "storage"))
                    self.addCleanup(cache.close)
                    during = storage.cached({"path": self.root}, cache)
                    candidate = during["snapshotId"] if boundary == "catalog" else None
                    if boundary == "receipt":
                        self.assertEqual(during["snapshotId"], prior["snapshotId"])
                        unpublished = next(name[:-7] for name, _ in cache.snapshots(all_files=True) if name != prior["snapshotId"] + ".sqlite")
                        with self.assertRaises(storage.StorageError) as caught:
                            storage.children({"snapshotId": unpublished}, cache)
                        self.assertEqual(caught.exception.code, "snapshot-not-active")
                    else:
                        self.assertNotEqual(during["snapshotId"], prior["snapshotId"])
                        self.assertEqual(during["activationDurability"], "pending")
                    if action == "crash":
                        with open(marker) as handle:
                            os.kill(int(handle.read()), signal.SIGKILL)
                        result = pipe.answer("scan", "storage-result" if boundary == "catalog" else "error")
                    else:
                        start = time.monotonic()
                        pipe.send(dict(command="storage." + action, requestId="stop", clientId="owner", generation=2, scanId=queued["scanId"]))
                        ack = pipe.answer("stop", "storage-progress")
                        self.assertIn(ack["status"], ("cancelling", "leaving"))
                        self.assertLess(time.monotonic() - start, 0.25)
                        self.assertNotIn("previousSnapshotRetained", ack)
                        result = pipe.answer("stop", "storage-result")
                    after = storage.cached({"path": self.root}, cache)
                    if boundary == "receipt":
                        self.assertEqual(after["snapshotId"], prior["snapshotId"])
                        self.assertEqual(result["commitState"], "not-committed")
                        self.assertTrue(result["previousSnapshotRetained"])
                        expected = prior["snapshotId"]
                    else:
                        self.assertEqual(after["snapshotId"], candidate)
                        self.assertEqual(result["status"], "committed")
                        self.assertEqual(result["snapshotId"], candidate)
                        self.assertFalse(result["previousSnapshotRetained"])
                        self.assertEqual(after["activationDurability"], "durable")
                        expected = candidate
                    pipe.close()
                    self.assertEqual(len(cache.snapshots(all_files=True)), 1)
                    # A subsequent failed scan cannot elect any orphan receipt.
                    limited = Pipe(self.temp.name, "import raman_storage as s;s.MAX_ENTRIES=1;s.worker_main()")
                    self.addCleanup(limited.close)
                    limited.send(dict(command="storage.scan", requestId="limit", clientId="limit", generation=1, path=self.root))
                    self.assertEqual(limited.answer("limit", "error")["code"], "scan-entry-limit")
                    limited.close()
                    self.assertEqual(storage.cached({"path": self.root}, cache)["snapshotId"], expected)

    def pump_controller(self, controller, predicate, timeout=5):
        output = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            output.extend(controller.poll())
            if predicate(output):
                return output
            time.sleep(0.005)
        self.fail("controller fixture did not reach its bounded condition")

    def committed_controller_fixture(self):
        cache = storage.Cache()
        scanner = storage.Scanner(os.fsencode(self.root), cache, os.urandom(16).hex())
        try:
            prior = scanner.prepare()
            scanner.activate(prior)
        finally:
            scanner.close()
            cache.close()
        with open(os.path.join(self.root, os.urandom(8).hex()), "w") as handle:
            handle.write("new")
        marker = os.path.join(self.temp.name, os.urandom(8).hex())
        source = """import os,signal,time,raman_storage as s
signal.signal(signal.SIGTERM,signal.SIG_IGN)
real=s.os.rename
def pause(src,dst,**k):
 real(src,dst,**k)
 if dst == 'catalog.json':
  open(%r,'w').write(str(os.getpid()));time.sleep(30)
s.os.rename=pause;s.worker_main()
""" % marker
        controller = storage.Controller([sys.executable, "-S", "-c", source])
        self.addCleanup(controller.close)
        queued = controller.handle(dict(command="storage.scan", requestId="scan", clientId="owner", generation=1, path=self.root))
        self.pump_controller(controller, lambda _: os.path.exists(marker))
        cache = storage.Cache()
        try:
            candidate = storage.cached({"path": self.root}, cache)["snapshotId"]
        finally:
            cache.close()
        self.assertNotEqual(candidate, prior["snapshotId"])
        return controller, queued["scanId"], prior["snapshotId"], candidate

    def test_repeated_commands_preserve_queued_and_running_reconciliation(self):
        for phase in ("queued", "running", "terminal", "reaping"):
            for action in ("cancel", "leave"):
                with self.subTest(phase=phase, action=action):
                    controller, scan_id, prior, candidate = self.committed_controller_fixture()
                    gate = os.path.join(self.temp.name, os.urandom(8).hex())
                    entered = gate + "-entered"
                    source = """import os,time,raman_storage as s
real=s.Cache.writer
def hold(cache):
 open(%r,'w').write(str(os.getpid()))
 while not os.path.exists(%r):time.sleep(.005)
 return real(cache)
s.Cache.writer=hold;s.worker_main()
""" % (entered, gate)
                    if phase == "terminal":
                        source += "\ntime.sleep(30)"
                    controller.reconcile_worker_command = [sys.executable, "-S", "-c", source]
                    ack = controller.handle(dict(command="storage.cancel", requestId="first", clientId="owner", generation=2, scanId=scan_id))
                    self.assertEqual(ack["status"], "cancelling")
                    if phase == "queued":
                        with patch.object(controller, "launch", return_value=None):
                            self.pump_controller(controller, lambda _: any(c["command"] == "storage.reconcile" for c in controller.pending))
                            again = controller.handle(dict(command="storage." + action, requestId="again", clientId="owner", generation=3, scanId=scan_id))
                            pending = next(c for c in controller.pending if c["command"] == "storage.reconcile")
                            self.assertEqual(pending["candidateId"], candidate)
                            self.assertEqual(pending["previousSnapshotId"], prior)
                            self.assertEqual(pending["generation"], 3)
                    else:
                        self.pump_controller(controller, lambda _: os.path.exists(entered))
                        job = next(j for j in controller.jobs.values() if j["command"]["command"] == "storage.reconcile")
                        pid = job["process"].pid
                        if phase == "terminal":
                            with open(gate, "w") as handle:
                                handle.write("continue")
                            self.pump_controller(controller, lambda _: job["terminal"])
                        elif phase == "reaping":
                            job["deadline"] = time.monotonic() - 1
                            controller.poll()
                            self.assertTrue(job["stopped"])
                        again = controller.handle(dict(command="storage." + action, requestId="again", clientId="owner", generation=3, scanId=scan_id))
                        self.assertEqual(job["stopped"], phase in ("terminal", "reaping"))
                        self.assertEqual(job["process"].pid, pid)
                    self.assertEqual(again["type"], "storage-progress")
                    self.assertEqual(again["status"], "leaving" if action == "leave" else "cancelling")
                    self.assertNotIn("previousSnapshotRetained", again)
                    with open(gate, "w") as handle:
                        handle.write("continue")
                    output = self.pump_controller(controller, lambda out: any(m.get("requestId") == "again" and m["type"] == "storage-result" for m in out))
                    result = next(m for m in output if m.get("requestId") == "again" and m["type"] == "storage-result")
                    self.assertEqual(result["generation"], 3)
                    self.assertEqual(result["status"], "committed")
                    self.assertEqual(result["snapshotId"], candidate)
                    self.assertFalse(result["previousSnapshotRetained"])
                    self.assertFalse(any(m.get("generation") == 2 for m in output))
                    controller.close()
                    cache = storage.Cache()
                    try:
                        self.assertEqual(storage.cached({"path": self.root}, cache)["snapshotId"], candidate)
                        self.assertEqual(len(cache.snapshots(all_files=True)), 1)
                    finally:
                        cache.close()

    def test_generation_advance_cannot_disable_reconciliation_deadline(self):
        controller, scan_id, _, _ = self.committed_controller_fixture()
        controller.reconcile_worker_command = [sys.executable, "-S", "-c",
                                              "import sys,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);sys.stdin.readline();time.sleep(30)"]
        controller.handle(dict(command="storage.cancel", requestId="cancel", clientId="owner", generation=2, scanId=scan_id))
        controller.handle(dict(command="storage.cached", requestId="new", clientId="owner", generation=3, path=self.root))
        output = self.pump_controller(controller, lambda _: any(j["command"]["command"] == "storage.reconcile" for j in controller.jobs.values()))
        job = next(j for j in controller.jobs.values() if j["command"]["command"] == "storage.reconcile")
        self.assertEqual(job["generation"], 3)
        self.assertIsNone(job["delivery"])
        controller.handle(dict(command="storage.cached", requestId="newer", clientId="owner", generation=4, path=self.root))
        self.assertFalse(job["stopped"])
        self.assertNotEqual(job["generation"], controller.generations["owner"])
        job["deadline"] = time.monotonic() - 1
        output.extend(controller.poll())
        self.assertTrue(job["stopped"])
        pid = job["process"].pid
        output.extend(self.pump_controller(controller, lambda _: pid not in controller.jobs, timeout=1))
        self.assertFalse(os.path.exists("/proc/%d" % pid))
        self.assertFalse(any(m.get("generation") == 2 for m in output))
        # Recovery deadline/reap releases the scan lane; the next scanner repairs
        # the journal and completes rather than queueing indefinitely.
        controller.worker_command = [sys.executable, "-S", os.path.abspath(storage.__file__), "--worker"]
        controller.handle(dict(command="storage.scan", requestId="fresh", clientId="owner", generation=5, path=self.root))
        output = self.pump_controller(controller, lambda out: any(m.get("requestId") == "fresh" and m.get("status") == "ready" for m in out))
        self.assertTrue(any(m.get("requestId") == "fresh" and m.get("status") == "ready" for m in output))

    def test_commit_pipe_failure_and_late_cancel_are_typed(self):
        source = ("import json,sys,time,raman_storage as s;c=json.loads(input());"
                  "print(json.dumps(s.message('storage-prepared',c['requestId'],clientId=c['clientId'],generation=c['generation'],scanId=c['scanId'])),flush=True);"
                  "input();time.sleep(30)")
        controller = storage.Controller([sys.executable, "-c", source])
        self.addCleanup(controller.close)
        queued = controller.handle(dict(command="storage.scan", requestId="prepared", clientId="owner", generation=1, path=self.root))
        deadline = time.monotonic() + 2
        while not next(iter(controller.jobs.values()))["committing"] and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.01)
        self.assertTrue(next(iter(controller.jobs.values()))["committing"])
        cancel = controller.handle(dict(command="storage.cancel", requestId="late", clientId="owner", generation=2, scanId=queued["scanId"]))
        self.assertEqual(cancel["status"], "cancelling")
        deadline = time.monotonic() + 2
        output = []
        while controller.jobs and time.monotonic() < deadline:
            output.extend(controller.poll())
            time.sleep(0.01)
        result = next(m for m in output if m.get("requestId") == "late" and m["type"] == "storage-result")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["commitState"], "not-committed")
        self.assertTrue(result["previousSnapshotRetained"])
        controller.close()
        # Child writes prepared and closes its input before the parent commits.
        source = source.rsplit("input();time.sleep(30)", 1)[0] + "sys.stdin.close()"
        broken = storage.Controller([sys.executable, "-c", source])
        self.addCleanup(broken.close)
        broken.handle(dict(command="storage.scan", requestId="broken", clientId="owner", generation=1, path=self.root))
        deadline = time.monotonic() + 2
        output = []
        while broken.jobs and time.monotonic() < deadline:
            output.extend(broken.poll())
            time.sleep(0.01)
        self.assertTrue(any(m["type"] == "error" and m["code"] == "worker-failed" for m in output))


if __name__ == "__main__":
    unittest.main()
