#!/usr/bin/env python3
"""Live check: RAMen history survives `omarchy restart shell`.

Quickshell stops the probe it started with SIGKILL (QProcess::kill), so the
probe's worker process must still save before it exits. Run this inside an
Omarchy session with RAMen enabled. It restarts the shell once and only reads
RAMen's state: it never clears or edits history.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import raman_history  # noqa: E402

PATH = os.path.join(raman_history.state_dir(), "history.json")


def probes():
    # Both parent and worker inherit argv. Match supported production/QA entry
    # points, with either the legacy or deferred configuration invocation.
    found = subprocess.run(["pgrep", "-f", r"[Pp]ython[0-9.]* ([^ ]*/)?(raman_probe|qa-synthetic-probe)\.py( --defer-history)?$"], capture_output=True, text=True)
    return set(found.stdout.split())


def runtime_status():
    """RAMen's `status` IPC reply (read-only), or None if it is not reachable."""
    try:
        found = subprocess.run(["omarchy-shell", "boundsj.raman", "status"], capture_output=True, text=True, timeout=5)
        return json.loads(found.stdout).get("runtime")
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return None


def newest_saved():
    """Wall time of the newest saved 10 s bucket sample, or None."""
    try:
        with open(PATH) as handle:
            buckets = json.load(handle)["rings"]["10"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return max((bucket[2] for bucket in buckets), default=None)


def wait_for(condition, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.25)
    return False


def main():
    old = probes()
    if not old:
        print("FAIL: no RAMen probe is running")
        return 1
    print("probe processes before restart: %d (a waiting parent and a worker per runtime)" % len(old))
    time.sleep(5)  # readings newer than any periodic save
    before = newest_saved()
    restart = time.time()
    subprocess.run(["omarchy", "restart", "shell"], check=True)

    saved = wait_for(lambda: (newest_saved() or 0) >= restart - 4 and newest_saved() != before, 15)
    newest = newest_saved()
    print("newest saved sample: %s (restart at %.1f)" % (
        "none" if newest is None else "%.1f, %.1f s before restart" % (newest, restart - newest), restart))
    gone = wait_for(lambda: not (old & probes()), 15)
    # `omarchy restart shell` can return before the new shell has loaded RAMen.
    wait_for(lambda: len(probes() - old) >= len(old), 30)
    new = probes()
    print("old probe processes gone: %s; probe processes now: %d" % (gone, len(new)))
    # With Omarchy's service kind, one runtime serves every monitor: exactly one pair.
    runtime = runtime_status()
    shared = bool(runtime) and runtime.get("mode") == "service"
    print("runtime: %s" % (json.dumps(runtime) if runtime else "status unavailable"))

    problems = [text for failed, text in (
        (not saved, "readings from just before the restart were not saved"),
        (not gone, "old probe processes are still running"),
        (len(new) != len(old), "probe process count changed from %d to %d" % (len(old), len(new))),
        (shared and len(new) != 2, "shared runtime should run one probe pair, found %d processes" % len(new)),
    ) if failed]
    print("FAIL: " + "; ".join(problems) if problems else "PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
