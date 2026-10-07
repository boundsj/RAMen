#!/usr/bin/env python3
"""Test-owned Linux S1 transport/lifecycle/performance check; no shell changes.

All fixtures, XDG state/cache and evidence stay in .agent-artifacts. This script
does not install RAMen, scan personal folders or signal any unrelated process.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import raman_storage as storage


class Client:
    def __init__(self, directory):
        self.process = subprocess.Popen([sys.executable, str(REPO / "raman_probe.py"), "--defer-history"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                                 XDG_STATE_HOME=str(directory / "state"),
                                                 XDG_CACHE_HOME=str(directory / "cache")), bufsize=0)
        self.buffer = b""
        self.records = []
        self.max_line = 0
        self.read(lambda m: m["type"] == "summary")

    def send(self, command):
        data = command.encode() + b"\n" if isinstance(command, str) else storage.wire(command)
        self.process.stdin.write(data)

    def command(self, name, request, generation=1, **fields):
        self.send(dict(command="storage." + name, requestId=request, clientId="s1-qa", generation=generation, **fields))

    def read(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                self.max_line = max(self.max_line, len(raw) + 1)
                assert self.max_line <= storage.MAX_LINE_BYTES, "oversized wire reply"
                value = json.loads(raw)
                self.records.append(value)
                if predicate(value):
                    return value
            ready = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))[0]
            if not ready:
                break
            chunk = os.read(self.process.stdout.fileno(), 262144)
            if not chunk:
                break
            self.buffer += chunk
        raise AssertionError("response timed out or probe exited")

    def reply(self, request, kind):
        return self.read(lambda m: m.get("requestId") == request and m["type"] == kind)

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=2)
            self.process.stdout.close()
            self.process.stderr.close()


def descendants(pid):
    try:
        text = Path("/proc/%d/task/%d/children" % (pid, pid)).read_text()
    except FileNotFoundError:
        return []
    result = []
    for value in text.split():
        child = int(value)
        result.append(child)
        result.extend(descendants(child))
    return result


def await_gone(pids, seconds=1.0):
    deadline = time.monotonic() + seconds
    while any(Path("/proc/%d" % pid).exists() for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.01)
    return not any(Path("/proc/%d" % pid).exists() for pid in pids)


def storage_workers(pid):
    owned = []
    for child in descendants(pid):
        try:
            command = Path("/proc/%d/cmdline" % child).read_bytes()
        except FileNotFoundError:
            continue  # A completed descendant can be reaped after enumeration.
        if b"raman_storage.py" in command:
            owned.append(child)
    return owned


def run(directory, fanout):
    scope = directory / "scope"
    scope.mkdir()
    for index in range(170):
        (scope / ("synthetic-%03d" % index)).write_bytes(b"x" * (index + 1))
    with (scope / "sparse").open("wb") as handle:
        handle.seek(16 * 1024 * 1024)
        handle.write(b"x")
    os.link(scope / "synthetic-000", scope / "shared-link")
    os.symlink(".", scope / "cycle")
    os.mkdir(os.fsencode(scope) + b"/byte-\xff")
    client = Client(directory)
    metrics = {}
    try:
        client.command("mounts", "capacity", path=str(scope))
        capacity = client.reply("capacity", "storage-capacity")
        assert capacity["totalBytes"] == capacity["usedBytes"] + capacity["freeBytes"]
        assert capacity["freeBytes"] == capacity["availableBytes"] + capacity["reservedBytes"]
        client.command("scan", "baseline", path=str(scope))
        baseline = client.reply("baseline", "storage-result")
        snapshot = baseline["snapshotId"]
        start = time.monotonic()
        client.command("children", "page", snapshotId=snapshot)
        page = client.reply("page", "storage-children")
        metrics["cachedPageMs"] = round((time.monotonic() - start) * 1000, 2)
        assert len(page["rows"]) <= 50
        assert len(page["segments"]) + bool(page["other"]) <= 60
        assert page["other"]["count"] > 0
        metrics["cachedPageUnder100ms"] = metrics["cachedPageMs"] < 100
        # Test-owned wide directories keep actual metadata scans active.
        for index in range(fanout):
            (scope / ("wide-%06d" % index)).mkdir()
        client.command("scan", "cancelled", generation=2, path=str(scope))
        queued = client.reply("cancelled", "storage-progress")
        time.sleep(0.05)
        scan_workers = storage_workers(client.process.pid)
        assert scan_workers, "owned scan worker expected"
        start = time.monotonic()
        client.command("cancel", "cancel", generation=3, scanId=queued["scanId"])
        ack = client.reply("cancel", "storage-progress")
        assert ack["status"] == "cancelling"
        metrics["cancelAckMs"] = round((time.monotonic() - start) * 1000, 2)
        result = client.reply("cancel", "storage-result")
        metrics["cancelResolvedMs"] = round((time.monotonic() - start) * 1000, 2)
        assert result["status"] == "cancelled" and result["previousSnapshotRetained"] is True
        assert metrics["cancelAckMs"] < 250
        assert await_gone(scan_workers, max(0, 1.0 - (time.monotonic() - start)))
        metrics["cancelWorkerCleanupMs"] = round((time.monotonic() - start) * 1000, 2)
        assert metrics["cancelWorkerCleanupMs"] < 1000
        client.command("children", "prior", generation=3, snapshotId=snapshot)
        client.reply("prior", "storage-children")
        client.command("scan", "full", generation=4, path=str(scope))
        scan_start = time.monotonic()
        client.reply("full", "storage-progress")
        start = time.monotonic()
        client.send("refresh")
        client.read(lambda m: m["type"] == "summary")
        metrics["activeRefreshMs"] = round((time.monotonic() - start) * 1000, 2)
        full = client.read(lambda m: m.get("requestId") == "full" and m["type"] == "storage-result", timeout=125)
        assert full["snapshot"]["entries"] == fanout + 175
        metrics["scanElapsedSeconds"] = round(time.monotonic() - scan_start, 4)
        metrics["largeSnapshotEntries"] = full["snapshot"]["entries"]
        metrics["largeCachedPageMs"] = []
        for index in range(5):
            start = time.monotonic()
            client.command("children", "large-page-%d" % index, generation=4, snapshotId=full["snapshotId"])
            client.reply("large-page-%d" % index, "storage-children")
            metrics["largeCachedPageMs"].append(round((time.monotonic() - start) * 1000, 2))
        metrics["largeCachedPagesUnder100ms"] = all(value < 100 for value in metrics["largeCachedPageMs"])
        progress = [m["time"] for m in client.records if m.get("requestId") == "full" and m.get("status") == "scanning"]
        intervals = [b - a for a, b in zip(progress, progress[1:])]
        assert all(interval >= 0.49 for interval in intervals)
        metrics["scanProgressMessages"] = len(progress)
        metrics["minimumProgressIntervalSeconds"] = round(min(intervals), 4) if intervals else None
        summaries = [m["monotonic"] for m in client.records if m["type"] == "summary"]
        metrics["maximumSummaryIntervalSeconds"] = round(max(b - a for a, b in zip(summaries, summaries[1:])), 4)
        assert metrics["maximumSummaryIntervalSeconds"] < 2.25
        assert not any(m["type"] == "apps" for m in client.records)
        metrics["maxStorageLineBytes"] = client.max_line
        client.command("scan", "departed", generation=5, path=str(scope))
        client.reply("departed", "storage-progress")
        time.sleep(0.05)
        client.command("leave", "leave", generation=6)
        assert client.reply("leave", "storage-progress")["status"] == "leaving"
        left = client.reply("leave", "storage-result")
        assert left["status"] == "left" and left["previousSnapshotRetained"] is True
        client.command("children", "after-leave", generation=7, snapshotId=full["snapshotId"])
        client.reply("after-leave", "storage-children")
        client.command("scan", "eof", generation=8, path=str(scope))
        client.reply("eof", "storage-progress")
        time.sleep(0.05)
        owned = descendants(client.process.pid)
        assert len(owned) >= 2, "active probe and scanner expected"
        start = time.monotonic()
        client.process.stdin.close()
        client.process.wait(timeout=2)
        assert await_gone(owned)
        metrics["eofReapMs"] = round((time.monotonic() - start) * 1000, 2)
        assert metrics["eofReapMs"] < 1000
    finally:
        client.close()
    # Killing only the waiting parent models Quickshell's restart behavior.
    client = Client(directory)
    try:
        client.command("scan", "parent-death", generation=1, path=str(scope))
        client.reply("parent-death", "storage-progress")
        time.sleep(0.05)
        owned = descendants(client.process.pid)
        assert len(owned) >= 2
        start = time.monotonic()
        os.kill(client.process.pid, signal.SIGKILL)
        client.process.wait(timeout=2)
        assert await_gone(owned)
        metrics["waitingParentDeathCleanupMs"] = round((time.monotonic() - start) * 1000, 2)
        assert metrics["waitingParentDeathCleanupMs"] < 1000
    finally:
        client.close()
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fanout", type=int, default=50000)
    parser.add_argument("--output", default=".agent-artifacts/s1-check")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Linux is required; this does not establish Omarchy UI evidence")
    if not 1000 <= args.fanout <= 150000:
        parser.error("fanout must be 1000..150000")
    output = (REPO / args.output).resolve()
    if not output.is_relative_to(REPO / ".agent-artifacts"):
        parser.error("output must stay beneath repository .agent-artifacts")
    output.mkdir(parents=True, exist_ok=True)
    # /tmp is deliberately not used: the runbook and AGENTS own all task artifacts.
    with tempfile.TemporaryDirectory(prefix="fixture-", dir=output) as temp:
        result = run(Path(temp), args.fanout)
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        sha = None  # A copied container fixture is not a verified Git checkout.
    result.update(platform=sys.platform, python=sys.version.split()[0], fanout=args.fanout,
                  sha=sha, sourceHashes={name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
                                        for name in ("raman_probe.py", "raman_storage.py", "scripts/check-storage.py")},
                  liveOmarchy=False, status="PASS" if result["cachedPageUnder100ms"] and result["largeCachedPagesUnder100ms"] else "PASS_WITH_TARGET_MISS")
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
