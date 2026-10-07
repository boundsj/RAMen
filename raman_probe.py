#!/usr/bin/env python3
"""Memory probe for the RAMen bar widget.

Long-lived helper owned by BarWidget.qml. It streams one JSON object per line
on stdout and accepts line commands on stdin:

  detail 1|0          start/stop the per-app scan (only needed while the panel is open)
  gpu 1|0             start/stop GPU client sampling (a visible Apps page with gpuMetrics
                      auto); it runs only while detail is on too, every 5 s at most
  refresh             emit a summary (and apps, if detail is on) right away
  kill TERM|KILL <id> signal every process of an app group from the last scan
  demo <scene>|off    replay a canned scene from docs/demo-scenes.json (for
                      screenshots); kills in a scene only drop the fake row
  {"command": ...}    JSON-object commands (history/configure/incident/storage,
                      apps.query/details and the membership-checked apps.kill);
                      see docs/protocol.md

Output messages:
  {"type": "summary", ...}   every SUMMARY_INTERVAL seconds, numbered by "seq"
  {"type": "apps", ...}      every DETAIL_INTERVAL seconds while detail is on: the
                             top memory rows plus the new inventory snapshot's ID
  {"type": "killed", ...}    after a kill or apps.kill command
  {"type": "history" | "history-cleared" | "incident" | "error", ...}  replies to JSON commands

Each summary also feeds the bounded history in raman_history.py, which the
probe saves under XDG_STATE_HOME/raman at most once a minute.

The probe exits when stdin closes, so it never outlives the shell. It runs as
two processes (see detach_from_kill): Quickshell stops the one it started with
SIGKILL, and the worker still saves history before it exits.
"""

import errno
import json
import os
import select
import signal
import sys
import time

import raman_apps
import raman_gpu
import raman_history
import raman_storage

SUMMARY_INTERVAL = 2.0
DETAIL_INTERVAL = 2.5
MAX_APPS = 12
MAX_COMMAND_BYTES = 4096
PROTOCOL_VERSION = 1

TERMINALS = {
    "ghostty", "alacritty", "kitty", "foot", "wezterm-gui", "konsole",
    "gnome-terminal-", "xterm", "st", "urxvt",
}
SHELLS = {"bash", "zsh", "fish", "sh", "dash", "nu", "elvish", "xonsh"}
INTERPRETERS = {"node", "python", "python3", "bun", "deno", "ruby", "perl"}
# Killing any of these takes the desktop down with it.
PROTECTED = {
    "Hyprland", "hyprland", "Xwayland", "quickshell", "qs", "systemd",
    "dbus-broker", "dbus-broker-lau", "dbus-daemon", "pipewire",
    "pipewire-pulse", "wireplumber", "uwsm", "hypridle", "hyprlock",
    "xdg-desktop-por", "xdg-document-po", "xdg-permission-",
}


def emit(message):
    try:
        sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # The shell stopped reading: exit quietly, through main's final save.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(0)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


# ---------------------------------------------------------------- summary

def parse_meminfo(text):
    values = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            try:
                values[name.strip()] = int(parts[0])
            except ValueError:
                pass
    return values


def parse_psi(text):
    result = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        kind = parts[0]
        for field in parts[1:]:
            key, _, value = field.partition("=")
            if key in ("avg10", "avg60"):
                try:
                    result[kind + "_" + key] = float(value)
                except ValueError:
                    pass
    return result


def zram_used_kb():
    """RAM actually consumed by compressed zram pages (mm_stat mem_used_total).

    0 when there is no zram device; None when a device or /sys/block cannot be read.
    """
    total = 0
    try:
        names = os.listdir("/sys/block")
    except OSError:
        return None
    for name in names:
        if not name.startswith("zram"):
            continue
        fields = read_text("/sys/block/%s/mm_stat" % name).split()
        try:
            total += int(fields[2]) // 1024
        except (IndexError, ValueError):
            return None
    return total


def read_sample():
    """Current memory reading in kB / percent; None for anything unreadable."""
    mem = parse_meminfo(read_text("/proc/meminfo"))
    psi = parse_psi(read_text("/proc/pressure/memory"))
    total = mem.get("MemTotal")
    available = mem.get("MemAvailable", mem.get("MemFree"))
    swap_total = mem.get("SwapTotal")
    swap_free = mem.get("SwapFree")
    return {
        "total": total,
        "available": available,
        "used": max(0, total - available) if total is not None and available is not None else None,
        "cached": mem.get("Cached", 0) + mem.get("Buffers", 0),
        "swapTotal": swap_total,
        "swapUsed": max(0, swap_total - swap_free) if swap_total is not None and swap_free is not None else None,
        "zramRam": zram_used_kb(),
        "psiSome10": psi.get("some_avg10"),
        "psiSome60": psi.get("some_avg60"),
        "psiFull10": psi.get("full_avg10"),
    }


def summary(sample):
    """Live sources preserve nulls, including unavailable PSI."""
    return dict(sample, type="summary", time=time.time())


# ---------------------------------------------------------------- per-app scan

PF_KTHREAD = 0x00200000
CPU_ONLINE = "/sys/devices/system/cpu/online"


def parse_stat(text):
    """(comm, fields after comm) of /proc/PID/stat text, or None.

    comm may contain spaces and parentheses, so fields resume after the last
    ')'. fields[0] is the state (stat field 3); stat field n is fields[n - 3].
    """
    open_, close = text.find("("), text.rfind(")")
    if open_ < 0 or close < open_:
        return None
    return text[open_ + 1:close], text[close + 2:].split()


def read_cgroup(base):
    """The process's cgroup v2 path ("" when unreadable)."""
    lines = read_text(base + "cgroup").strip().splitlines()
    return lines[-1].split("::", 1)[-1] if lines else ""


def read_proc(pid, root="/proc"):
    """Snapshot one process, or None if it vanished, is a kernel thread or a zombie.

    Memory is read independently of CPU: when smaps_rollup is not readable
    (for example a non-dumpable process) pss and swap are None, and the
    process still counts for CPU and grouping.
    """
    base = "%s/%d/" % (root, pid)
    parsed = parse_stat(read_text(base + "stat"))
    read_at = time.monotonic()
    if parsed is None:
        return None
    comm, fields = parsed
    try:
        state = fields[0]
        ppid = int(fields[1])
        flags = int(fields[6])
        ticks = int(fields[11]) + int(fields[12])  # utime + stime; never cutime/cstime
        start = int(fields[19])
    except (IndexError, ValueError):
        return None
    if flags & PF_KTHREAD or state in ("Z", "X", "x"):
        return None
    pss = swap = None
    try:
        with open(base + "smaps_rollup", "r") as handle:
            for line in handle:
                if line.startswith("Pss:"):
                    pss = int(line.split()[1])
                elif line.startswith("SwapPss:"):
                    swap = int(line.split()[1])
        if pss is None:
            return None  # no address space left: the process is exiting
    except (FileNotFoundError, ProcessLookupError):
        return None  # exited
    except (OSError, ValueError):
        pss = None  # not permitted or unparsable: memory unavailable
    cgroup = read_cgroup(base)
    argv = read_text(base + "cmdline").split("\0")
    return {
        "pid": pid,
        "ppid": ppid,
        "start": start,
        "comm": comm,
        "argv": [a for a in argv if a],
        "pss": pss,
        "swap": (swap or 0) if pss is not None else None,
        "cgroup": cgroup,
        "state": state,
        "ticks": ticks,
        "readAt": read_at,
    }


def process_name(proc):
    """comm, untruncated from argv0 when the kernel cut it, or the script for interpreters."""
    comm = proc["comm"]
    argv = proc["argv"]
    # Chromium/Electron rewrite argv into one space-joined string.
    if len(argv) == 1 and " " in argv[0]:
        argv = argv[0].split(" ")
    if not argv:
        return comm
    if comm in INTERPRETERS or comm.startswith("python3."):
        args = argv[1:]
        for index, arg in enumerate(args):
            if arg in ("-c", "-e", "--eval"):
                return comm  # inline code: no useful name
            if arg == "-m" and index + 1 < len(args):
                return args[index + 1]
            if arg and not arg.startswith("-"):
                return os.path.basename(arg.rstrip("/")) or comm
        return comm
    base = os.path.basename(argv[0])
    if len(comm) == 15 and base.startswith(comm):
        return base
    return comm


def pretty(name):
    if name.islower() and name[:1].isalpha():
        return name[0].upper() + name[1:]
    return name


def footprint(item):
    """PSS + swap in kB, or None when memory is unavailable."""
    return None if item.get("pss") is None else item["pss"] + (item.get("swap") or 0)


def group_name(members):
    """The process name holding the most memory in a group, ignoring shells."""
    weights = {}
    for proc in members:
        name = process_name(proc)
        if proc["comm"] in SHELLS and len(members) > 1:
            continue
        weights[name] = weights.get(name, 0) + (footprint(proc) or 0) + 1
    if not weights:
        return pretty(process_name(members[0]))
    return pretty(max(weights.items(), key=lambda item: item[1])[0])


def scope_of(cgroup):
    """The app scope a process belongs to, or "" for services/session processes."""
    leaf = cgroup.rsplit("/", 1)[-1]
    if "/app.slice/" in cgroup and leaf.startswith("app-") and leaf.endswith(".scope"):
        return leaf
    return ""


def bucket_processes(procs):
    """{group ID: {id, kind, host, procs}}: which processes form which group.

    Grouping uses only pid, ppid, comm and cgroup, so the confirmation check
    in apps_kill can rebuild one group's current membership without the
    memory and CPU reads of a scan.
    """
    by_pid = {p["pid"]: p for p in procs}
    groups = {}

    def add(key, kind, proc, host=""):
        group = groups.get(key)
        if group is None:
            group = groups[key] = {"id": key, "kind": kind, "host": host, "procs": []}
        group["procs"].append(proc)

    by_scope = {}
    for proc in procs:
        scope = scope_of(proc["cgroup"])
        if scope:
            by_scope.setdefault(scope, []).append(proc)
        else:
            add("pid:%d" % proc["pid"], "process", proc)

    for scope, members in by_scope.items():
        terminals = {p["pid"] for p in members if p["comm"] in TERMINALS}
        if not terminals:
            for proc in members:
                add(scope, "app", proc)
            continue
        terminal = by_pid[min(terminals)]
        host = pretty(process_name(terminal))
        for proc in members:
            # Walk up to the direct child of the terminal: one shell session.
            node, root = proc, None
            seen = 0
            while node is not None and seen < 64:
                if node["ppid"] in terminals:
                    root = node
                    break
                node = by_pid.get(node["ppid"])
                seen += 1
            if root is None or proc["pid"] in terminals:
                add(scope, "app", proc)
            else:
                add("%s/%d" % (scope, root["pid"]), "session", proc, host)
    return groups


def group_processes(procs, self_pid, online=None):
    """Bucket processes into killable app groups. Pure; tested in tests/test_probe.py.

    Memory and CPU are aggregated separately. A group's pss/swap and
    cpuCorePercent are the subtotals of the members that have a reading
    (None when none has); memoryStatus/cpuStatus and the coverage counts say
    whether that subtotal is complete. self_pid is one PID or a set of PIDs
    (the probe's worker and waiting parent) that make their group protected.
    "pids" and "members" are internal: they are never published.
    """
    own = set(self_pid) if isinstance(self_pid, (set, frozenset, list, tuple)) else {self_pid}
    result = []
    for group in bucket_processes(procs).values():
        members = group["procs"]
        members.sort(key=lambda p: footprint(p) or 0, reverse=True)
        known = [p for p in members if p.get("pss") is not None]
        measured = [p for p in members if p.get("cpu") is not None]
        if len(measured) == len(members):
            cpu_status = "available"
        elif measured:
            cpu_status = "partial"
        elif any(p.get("cpuState") == "warming-up" for p in members):
            cpu_status = "warming-up"
        else:
            cpu_status = "unavailable"
        protected = any(p["comm"] in PROTECTED or p["pid"] in own for p in members)
        pids = [[p["pid"], p["start"]] for p in members]
        cpu = sum(p["cpu"] for p in measured) if measured else None
        result.append({
            "id": group["id"],
            "kind": group["kind"],
            "name": raman_apps.display_name(group_name(members)),
            "host": raman_apps.display_name(group["host"]),
            "count": len(members),
            "pss": sum(p["pss"] for p in known) if known else None,
            "swap": sum(p.get("swap") or 0 for p in known) if known else None,
            "memoryStatus": "available" if len(known) == len(members) else "partial" if known else "unavailable",
            "memoryCoverage": {"measured": len(known), "members": len(members)},
            "cpuCorePercent": cpu,
            "cpuMachinePercent": cpu / online if cpu is not None and online else None,
            "cpuStatus": cpu_status,
            "cpuCoverage": {"measured": len(measured), "members": len(members)},
            "generation": raman_apps.membership_generation(pids),
            "protected": protected,
            "pids": pids,
            # For on-demand details only; argv is not kept past the scan.
            "members": [{"pid": p["pid"], "start": p["start"], "comm": p["comm"], "name": process_name(p),
                         "state": p.get("state"), "pss": p.get("pss"), "swap": p.get("swap"),
                         "cpu": p.get("cpu"), "cpuState": p.get("cpuState")} for p in members],
        })
    result.sort(key=lambda g: (-(footprint(g) or 0), g["id"]))
    return result


class Unverified(Exception):
    """A process whose ownership, identity or cgroup could not be established."""


def read_entry(name, dir_fd=None):
    """Text of a /proc file (name relative to dir_fd when given), or None if the process has gone.

    Unlike read_text, any other failure (permission denied, I/O error) raises
    Unverified: an unreadable file says nothing about whose process it is.
    """
    try:
        fd = os.open(name, os.O_RDONLY, dir_fd=dir_fd)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise Unverified("%s: %s" % (name, exc.strerror or exc))
    try:
        with open(fd, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except ProcessLookupError:
        return None
    except OSError as exc:
        raise Unverified("%s: %s" % (name, exc.strerror or exc))


def ownership(root, entry, uid, dir_fd=None):
    """"owned" (this user's by real, effective and saved uid), "foreign" or "gone".

    The entry's owner decides without reading anything inside it, so another
    user's processes never need readable files. Only a root-owned entry is
    ambiguous for a non-root user: proc(5) gives a non-dumpable process's
    entry to root, so its status names the user. Raises Unverified when that
    cannot be read or parsed, or the entry cannot be examined at all. With
    dir_fd (an open /proc/PID directory) it judges that pinned process.
    """
    try:
        owner = (os.fstat(dir_fd) if dir_fd is not None else os.stat("%s/%s" % (root, entry))).st_uid
    except (FileNotFoundError, ProcessLookupError):
        return "gone"
    except OSError as exc:
        raise Unverified("%s: %s" % (entry, exc.strerror or exc))
    if owner == uid:
        return "owned"
    if uid == 0 or owner != 0:
        return "foreign"
    status = read_entry("status" if dir_fd is not None else "%s/%s/status" % (root, entry), dir_fd)
    if status is None:
        return "gone"
    for line in status.splitlines():
        if line.startswith("Uid:"):
            ids = line.split()[1:4]
            if len(ids) != 3:
                break
            return "owned" if all(value == str(uid) for value in ids) else "foreign"  # setuid stays foreign
    raise Unverified("%s: no readable Uid in status" % entry)


def owned(root, entry, uid):
    """True for a process of this user (real, effective and saved uid); False when that cannot be read."""
    try:
        return ownership(root, entry, uid) == "owned"
    except Unverified:
        return False


def read_processes(root="/proc", uid=None):
    """Owned processes (at most MAX_SCAN_PROCESSES) and inventory info."""
    uid = os.getuid() if uid is None else uid
    procs = []
    reason = None
    try:
        entries = os.listdir(root)
    except OSError:
        entries = []
        reason = "proc-unreadable"
    for entry in entries:
        if not entry.isdigit() or not owned(root, entry, uid):
            continue
        if len(procs) >= raman_apps.MAX_SCAN_PROCESSES:
            reason = "process-limit"
            break
        proc = read_proc(int(entry), root)
        if proc is not None:
            procs.append(proc)
    return procs, {"processes": len(procs), "incompleteReason": reason,
                   "memoryUnavailable": sum(1 for p in procs if p["pss"] is None)}


def online_cpus(path=CPU_ONLINE):
    """(topology key, online logical CPU count or None)."""
    text = read_text(path).strip()
    count = raman_apps.parse_cpu_list(text)
    if count is None:
        try:
            count = os.sysconf("SC_NPROCESSORS_ONLN")
        except (OSError, ValueError):
            count = None
        text = "sysconf:%s" % count
    return text, count if count and count > 0 else None


def scan(tracker, root="/proc", cpu_path=CPU_ONLINE):
    """One detail scan: (groups, inventory info, CPU batch metadata)."""
    topology, online = online_cpus(cpu_path)
    reset = tracker.begin(time.monotonic(), raman_history.boot_clock(), topology, online)
    procs, info = read_processes(root)
    for proc in procs:
        proc["cpu"], proc["cpuState"] = tracker.sample(proc["pid"], proc["start"], proc["ticks"], proc["readAt"])
    tracker.end()
    cpu = {"status": "unavailable" if tracker.clock_ticks is None else "warming-up" if reset else "available",
           "reset": reset, "onlineCpus": online, "clockTicks": tracker.clock_ticks,
           "anomalies": tracker.anomalies}
    return group_processes(procs, protected_pids(), online), info, cpu


def clock_ticks():
    try:
        return os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- kill

INTERNAL = ("pids", "members")  # group fields that never leave the probe


def public_group(group):
    return {k: v for k, v in group.items() if k not in INTERNAL}


def protected_pids():
    """The probe itself: this worker and the waiting parent that Quickshell started."""
    return {os.getpid(), os.getppid()}


def start_time(pid):
    stat = read_text("/proc/%d/stat" % pid)
    try:
        return int(stat[stat.rfind(")") + 2:].split()[19])
    except (IndexError, ValueError):
        return None


def read_identity(root, entry, uid):
    """What grouping needs of one of this user's processes (pid, ppid, start, comm, cgroup), or None.

    The /proc/PID directory stays open while it is read, so ownership, stat
    and cgroup all describe the same process even if the PID is reused
    meanwhile. None only for a process that is gone (including one exiting
    mid-read), not this user's, a kernel thread or a zombie, as a scan leaves
    them out. An unreadable or malformed stat, cgroup or status raises
    Unverified rather than counting as one of those.
    """
    try:
        fd = os.open("%s/%s" % (root, entry), os.O_RDONLY | os.O_DIRECTORY)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except OSError as exc:
        raise Unverified("%s: %s" % (entry, exc.strerror or exc))
    try:
        if ownership(root, entry, uid, fd) != "owned":
            return None
        text = read_entry("stat", fd)
        if text is None:
            return None
        parsed = parse_stat(text)
        if parsed is None:
            raise Unverified("%s: malformed stat" % entry)
        comm, fields = parsed
        try:
            state, ppid, flags, start = fields[0], int(fields[1]), int(fields[6]), int(fields[19])
        except (IndexError, ValueError):
            raise Unverified("%s: malformed stat" % entry) from None
        if flags & PF_KTHREAD or state in ("Z", "X", "x"):
            return None
        text = read_entry("cgroup", fd)
        if text is None:
            return None
        lines = text.strip().splitlines()
        if not lines:
            raise Unverified("%s: empty cgroup" % entry)
        return {"pid": int(entry), "ppid": ppid, "start": start, "comm": comm, "argv": [],
                "cgroup": lines[-1].split("::", 1)[-1]}
    finally:
        os.close(fd)


def live_members(group_id, root="/proc", uid=None):
    """The group's members as /proc has them now ([] when it has none), or None.

    One pass over this user's processes reading only stat and cgroup, grouped
    exactly as a scan groups them; it runs once per confirmed apps.kill, never
    per query. None when the membership is unverified: /proc unreadable, over
    the scan's process limit, or any process whose ownership, identity or
    cgroup could not be established, since it might belong to the group.
    Processes that are gone, another user's, kernel threads or zombies are
    simply not members.
    """
    uid = os.getuid() if uid is None else uid
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    procs = []
    try:
        for entry in entries:
            if not entry.isdigit() or ownership(root, entry, uid) != "owned":
                continue
            if len(procs) >= raman_apps.MAX_SCAN_PROCESSES:
                return None
            proc = read_identity(root, entry, uid)
            if proc is not None:
                procs.append(proc)
    except Unverified:
        return None
    group = bucket_processes(procs).get(group_id)
    return group["procs"] if group else []


def member_verdict(pid, start, uid, root="/proc"):
    """What one scanned (pid, start ticks) is right now.

    "ok" (same process, this user's, not protected), "gone" (exited, zombie or
    PID reused), "protected" (a desktop-critical name or the probe) or
    "foreign" (no longer owned by this user, for example after a setuid exec,
    or its ownership could not be read); only "ok" is ever signalled.
    """
    parsed = parse_stat(read_text("%s/%d/stat" % (root, pid)))
    if parsed is None:
        return "gone"
    comm, fields = parsed
    try:
        state, current = fields[0], int(fields[19])
    except (IndexError, ValueError):
        return "gone"
    if current != start or state in ("Z", "X", "x"):
        return "gone"
    if comm in PROTECTED or pid in protected_pids():
        return "protected"
    if not owned(root, str(pid), uid):
        return "foreign"
    return "ok"


def open_pidfd(pid):
    """A pidfd for pid (Linux 5.3+), "gone" if it has exited, or None if the kernel has no pidfd_open.

    Any other failure (descriptor or memory exhaustion, a sandbox refusing the
    call) raises OSError: the caller must not fall back to a numeric signal.
    """
    if not hasattr(os, "pidfd_open"):
        return None
    try:
        return os.pidfd_open(pid)
    except ProcessLookupError:
        return "gone"
    except OSError as exc:
        if exc.errno == errno.ENOSYS:
            return None
        raise


def signal_group(group, signame, uid=None):
    """Signal each member that is still the scanned process, re-checked right before its own signal.

    A preflight refuses the whole group, signalling nothing, when any member is
    protected now. Then, one member at a time: pin the PID with a pidfd, check
    its identity (start time, not a zombie, owner, protection) and signal it
    through that pidfd, so a PID reused after the check cannot receive the
    signal. Where the kernel has no pidfd_open the same check runs immediately
    before os.kill; only kill(2)'s own instant remains. Any other pidfd failure
    fails that member instead of falling back to the numeric signal. Exited or
    reused members are skipped and foreign ones count as failed; a member that
    became protected after the preflight stops the remaining signals.
    """
    uid = os.getuid() if uid is None else uid
    sig = signal.SIGKILL if signame == "KILL" else signal.SIGTERM
    if any(member_verdict(pid, start, uid) == "protected" for pid, start in group["pids"]):
        return {"sent": 0, "failed": 0, "skipped": 0, "error": "protected", "code": "protected"}
    sent = failed = skipped = 0
    error = ""
    for pid, start in group["pids"]:
        try:
            handle = open_pidfd(pid)
        except OSError as exc:
            failed += 1
            error = "could not pin the process (%s); not signalled" % (exc.strerror or exc)
            continue
        if handle == "gone":
            skipped += 1
            continue
        try:
            verdict = member_verdict(pid, start, uid)
            if verdict == "protected":
                error = "protected"
                break
            if verdict == "gone":
                skipped += 1
                continue
            if verdict == "foreign":
                failed += 1
                error = "not owned by this user"
                continue
            if handle is not None:
                signal.pidfd_send_signal(handle, sig)
            else:
                os.kill(pid, sig)
            sent += 1
        except ProcessLookupError:
            skipped += 1
        except OSError as exc:
            failed += 1
            error = exc.strerror or str(exc)
        finally:
            if handle is not None:
                os.close(handle)
    code = "sent" if sent else "protected" if error == "protected" else "failed" if failed else "gone"
    return {"sent": sent, "failed": failed, "skipped": skipped, "error": error, "code": code}


def kill_group(group, signame):
    """Legacy kill: signal each pid that is still the same process (start time unchanged)."""
    result = signal_group(group, signame)
    return {"type": "killed", "id": group["id"], "signal": signame, "sent": result["sent"],
            "failed": result["failed"], "error": result["error"]}


def apps_kill(command, state, request_id):
    """apps.kill: signal a group only if its membership is still the armed one."""
    try:
        group_id, membership, signame = raman_apps.parse_action(command, kill=True)
    except raman_apps.QueryError as exc:
        return dict(error_reply(request_id, exc.code, str(exc)), **exc.echo)
    generation = command.get("generation")

    def outcome(code, error, sent=0, failed=0, skipped=0, current=None):
        return reply("killed", request_id, generation=generation, id=group_id, signal=signame,
                     membership=membership, currentMembership=current, code=code, error=error,
                     sent=sent, failed=failed, skipped=skipped, demo=bool(state.demo))

    state.kill_followup = True
    if state.demo:
        # Demo kills are cosmetic: only a fake row whose fake membership matches is dropped.
        apps = state.demo["apps"]
        target = next((a for a in apps if a["id"] == group_id), None)
        if target is None:
            return outcome("gone", "no longer running")
        if target["generation"] != membership:
            return outcome("stale-membership", "membership changed since armed", current=target["generation"])
        if target["protected"]:
            return outcome("protected", "protected", current=target["generation"])
        state.demo["apps"] = [a for a in apps if a is not target]
        return outcome("demo", "", sent=target["count"], current=target["generation"])
    group = state.groups.get(group_id) if state.detail_active else None
    if group is None:
        return outcome("gone", "no longer running")
    if group["generation"] != membership:
        return outcome("stale-membership", "membership changed since armed", current=group["generation"])
    if group["protected"]:
        return outcome("protected", "protected", current=group["generation"])
    # The scan can be 2.5 s old: rebuild the group's membership from /proc now,
    # so a member that joined, exited, was reused or moved since refuses the kill.
    members = live_members(group_id, state.proc_root)
    if members is None:
        return outcome("unverified", "could not re-read every process; nothing was signalled")
    if not members:
        return outcome("gone", "no longer running")
    pids = [[p["pid"], p["start"]] for p in members]
    current = raman_apps.membership_generation(pids)
    if current != membership:
        return outcome("stale-membership", "membership changed since armed", current=current)
    own = protected_pids()
    if any(p["comm"] in PROTECTED or p["pid"] in own for p in members):
        return outcome("protected", "protected", current=current)
    result = signal_group({"id": group_id, "pids": pids}, signame)
    return outcome(result["code"], result["error"], result["sent"], result["failed"], result["skipped"], current)


def read_command(member, root="/proc"):
    """(command line, truncated, status) of one member, read now and only if it is still that process."""
    pid = member["pid"]
    try:
        with open("%s/%d/cmdline" % (root, pid), "rb") as handle:
            raw = handle.read(raman_apps.MAX_COMMAND * 4 + 1)
    except OSError:
        raw = None
    if start_time(pid) != member["start"]:
        return None, False, "exited"
    if not raw:
        return None, False, "unavailable"  # kernel-hidden, exited mid-read or empty
    argv = [a for a in raw.decode("utf-8", "replace").split("\0") if a]
    text, truncated = raman_apps.command_text(argv)
    return text, truncated or len(raw) > raman_apps.MAX_COMMAND * 4, "available"


def apps_details(command, state, request_id):
    """apps.details: one group's row and members, with command lines read on request; never stored."""
    try:
        group_id, _, _ = raman_apps.parse_action(command, kill=False)
    except raman_apps.QueryError as exc:
        return dict(error_reply(request_id, exc.code, str(exc)), **exc.echo)
    generation = command.get("generation")
    snapshot = state.inventory.latest() if state.detail_active else None
    base = reply("apps-details", request_id, generation=generation, id=group_id,
                 demo=bool(state.demo), snapshot=snapshot["id"] if snapshot else None,
                 sampledAt=snapshot["sampledAt"] if snapshot else None)
    if snapshot is None:
        return dict(error_reply(request_id, "inactive", "no app scan is running; open Memory or Apps"),
                    generation=generation, id=group_id)
    row = next((entry[0] for entry in snapshot["rows"] if entry[0]["id"] == group_id), None)
    if row is None:
        return dict(error_reply(request_id, "gone", "that app is no longer running"), generation=generation,
                    id=group_id)
    online = snapshot["cpu"].get("onlineCpus")
    if state.demo:
        source = next((a for a in state.demo["apps"] if a["id"] == group_id), {})
        members = [dict(m, start=0) for m in source.get("demoMembers", [])]
        return raman_apps.details_reply(base, row, members, online,
                                        lambda m: (m.get("command"), False, "demo"))
    group = state.groups.get(group_id)
    members = group["members"] if group else []
    return raman_apps.details_reply(base, row, members, online, read_command)


# ---------------------------------------------------------------- demo scenes

DEMO_SCENES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "demo-scenes.json")


def load_demo(name, path=DEMO_SCENES):
    """A canned {summary, apps} scene, or None. Never touches real processes."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            scene = json.load(handle).get(name)
    except (OSError, ValueError):
        return None
    if not isinstance(scene, dict):
        return None
    mem = dict(scene.get("summary", {}))
    mem["type"] = "summary"
    mem.setdefault("cached", 0)
    mem.setdefault("psiSome60", mem.get("psiSome10", 0.0))
    mem.setdefault("psiFull10", 0.0)
    mem["used"] = max(0, mem.get("total", 0) - mem.get("available", 0))
    apps = []
    for index, app in enumerate(scene.get("apps", [])):
        entry = {"id": "demo:%d" % index, "kind": "app", "host": "", "count": 1,
                 "swap": 0, "protected": False, "memoryStatus": "available",
                 "cpuCorePercent": None}
        entry.update(app)
        entry.setdefault("cpuStatus", "available" if entry["cpuCorePercent"] is not None else "unavailable")
        entry["generation"] = "demo-%d" % index
        entry.setdefault("memoryCoverage", {"measured": entry["count"] if entry["pss"] is not None else 0,
                                            "members": entry["count"]})
        entry.setdefault("cpuCoverage", {"measured": entry["count"] if entry["cpuCorePercent"] is not None else 0,
                                         "members": entry["count"]})
        apps.append(entry)
    timeline = scene.get("timeline") if isinstance(scene.get("timeline"), list) else []
    incidents = scene.get("incidents") if isinstance(scene.get("incidents"), list) else []
    cpu = dict(DEMO_CPU)
    if isinstance(scene.get("cpu"), dict):
        cpu.update(status="available", onlineCpus=scene["cpu"].get("onlineCpus"))
    gpu = None
    if isinstance(scene.get("gpu"), dict):
        gpu = raman_gpu.inactive_meta()
        gpu.update(scene["gpu"], status=scene["gpu"].get("status", "available"), reason="demo",
                   units=raman_gpu.UNITS)
    return {"summary": mem, "apps": apps, "timeline": timeline, "incidents": incidents, "cpu": cpu, "gpu": gpu}


def demo_gpu(demo, wanted):
    """Demo rows and GPU metadata: the scene's synthetic GPU readings only while
    GPU metrics are wanted; never a hardware read."""
    rows = [dict(a) for a in demo["apps"]]
    if not wanted:
        raman_gpu.strip_rows(rows)
        return rows, raman_gpu.inactive_meta()
    if demo.get("gpu") is None:
        raman_gpu.strip_rows(rows)
        meta = raman_gpu.inactive_meta()
        meta.update(status="unsupported", reason="demo-without-gpu")
        return rows, meta
    for row in rows:
        row.setdefault("gpu", [])
        row.setdefault("gpuMemoryKb", None)
        row.setdefault("gpuMemoryOverlap", False)
        row.setdefault("gpuStatus", "none")
        row.setdefault("gpuComplete", row["gpuStatus"] in ("available", "none"))
        row.setdefault("gpuCoverage", {"measured": row["count"], "members": row["count"]})
    return rows, dict(demo["gpu"], demo=True, synthetic=True)


def demo_preview(apps):
    """The demo Memory list, by the live rule: scene order, at least 1 MiB known, at most 12 rows."""
    return [demo_row(a) for a in apps if (footprint(a) or 0) >= 1024][:MAX_APPS]


def demo_row(app):
    return {k: v for k, v in app.items() if k != "demoMembers"}


def _lerp(a, b, f):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * f
    return b


def seed_demo_history(history, scene, now):
    """Fill a demo History from the scene's timeline; return the fake clock's now.

    The timeline replays 2 s readings on the fake clock so that it ends at now:
    phases ramp metrics `from` -> `to` (or `set` them, null meaning unreadable),
    and pauses leave a gap ("suspend" or "stall"). Incidents are synthetic and
    exist only in this demo History, which is never saved.
    """
    timeline = scene.get("timeline") or []
    if not timeline:
        return now
    def seconds(phase):
        return float(phase.get("pauseMinutes", phase.get("minutes", 0))) * 60
    t = now - sum(seconds(p) for p in timeline)
    boot = t
    start = t
    current = {name: scene["summary"].get(name) for name in raman_history.METRICS}
    samples = []
    for phase in timeline:
        if "pauseMinutes" in phase:
            pause = seconds(phase)
            t += pause
            boot += pause * (2 if phase.get("reason") == "suspend" else 1)  # boot clock runs on in suspend
            continue
        steps = max(1, int(round(seconds(phase) / SUMMARY_INTERVAL)))
        begin = dict(current, **phase.get("from", {}))
        end = dict(begin, **phase.get("to", {}))
        for step in range(steps):
            fraction = step / (steps - 1) if steps > 1 else 1.0
            values = {name: _lerp(begin.get(name), end.get(name), fraction) for name in raman_history.METRICS}
            values.update(phase.get("set", {}))
            total, available = values.get("total"), values.get("available")
            values["used"] = total - available if None not in (total, available) else None
            t += SUMMARY_INTERVAL
            boot += SUMMARY_INTERVAL
            history.ingest(values, t, t, boot)
            samples.append((t, values))
        current = end
    for index, spec in enumerate(scene.get("incidents") or []):
        at = start + float(spec.get("startMinute", 0)) * 60
        _, values = min(samples, key=lambda sample: abs(sample[0] - at))
        context = {"status": "not-observed"}
        if spec.get("observedApps"):
            top = sorted(scene["apps"], key=lambda a: a["pss"] + a["swap"], reverse=True)[:int(spec["observedApps"])]
            context = {"status": "observed", "at": at - 2,
                       "apps": [{"name": a["name"], "kb": a["pss"] + a["swap"]} for a in top]}
        end_minute = spec.get("endMinute")
        history.incidents.append({
            "id": "demo-%d" % (index + 1),
            "start": at,
            "end": None if end_minute is None else start + float(end_minute) * 60,
            "severity": spec.get("severity") if spec.get("severity") in raman_history.SEVERITIES else "warn",
            "reason": spec.get("reason") if spec.get("reason") in raman_history.INCIDENT_REASONS else "pressure",
            "summarySeq": None,
            "measurement": dict(values),
            "context": raman_history.sanitize_context(context),
        })
        receipt = history.incidents[-1]
        receipt["status"] = spec.get("status", "active" if end_minute is None else "recovered")
        receipt["lastAt"] = receipt["end"] or at
        if isinstance(spec.get("thresholds"), dict):
            receipt["thresholds"] = {k: raman_history._num(spec["thresholds"].get(k), 1, 100) for k in
                                     ("warnPercent", "criticalPercent", "warnPressure", "criticalPressure")}
        if spec.get("upgradeMinute") is not None:
            upgrade_at = start + float(spec["upgradeMinute"]) * 60
            _, upgrade_values = min(samples, key=lambda sample: abs(sample[0] - upgrade_at))
            receipt["upgrade"] = {"at": upgrade_at, "measurement": dict(upgrade_values)}
    del history.incidents[:max(0, len(history.incidents) - raman_history.MAX_INCIDENTS)]
    return t


DEMO_INVENTORY = {"processes": 0, "incompleteReason": None, "memoryUnavailable": 0}
DEMO_CPU = {"status": "unavailable", "reset": None, "onlineCpus": None, "clockTicks": None, "anomalies": 0}


# ---------------------------------------------------------------- JSON commands

def reply(kind, request_id, **fields):
    message = {"type": kind, "requestId": request_id, "schemaVersion": PROTOCOL_VERSION, "time": time.time()}
    message.update(fields)
    return message


def error_reply(request_id, code, message):
    return reply("error", request_id, code=code, message=message[:200])


def handle_json(text, state):
    """One JSON-object command -> one reply. Never scans processes; only
    apps.kill signals, after re-checking the armed group's membership."""
    try:
        command = json.loads(text)  # text starts with "{": an object or an error
    except (ValueError, RecursionError):
        return error_reply(None, "bad-json", "command is not valid JSON")
    request_id = command.get("requestId")
    valid_id = request_id is None or (isinstance(request_id, str) and len(request_id) <= 64) \
        or (isinstance(request_id, int) and not isinstance(request_id, bool) and abs(request_id) <= 2 ** 53)
    if not valid_id:
        return error_reply(None, "bad-request", "requestId must be a string of at most 64 characters or an integer")
    name = command.get("command")
    if isinstance(name, str) and name.startswith("storage."):
        return state.storage.handle(command)
    if name == "apps.query":
        return apps_query(command, state, request_id)
    if name == "apps.kill":
        return apps_kill(command, state, request_id)
    if name == "apps.details":
        return apps_details(command, state, request_id)
    history = state.demo_history if state.demo else state.history
    if name == "history.configure":
        enabled, persist = command.get("enabled"), command.get("persist")
        if type(enabled) is not bool or type(persist) is not bool:
            return error_reply(request_id, "bad-settings", "enabled and persist must be booleans")
        state.configure(enabled, persist)
        state.configuration_id = request_id
        return reply("history-configured", request_id, enabled=enabled, persist=persist)
    if name == "incident.record":
        return record_event(command, state, request_id)
    if name == "history.get":
        window = command.get("window")
        if not isinstance(window, str) or window not in raman_history.WINDOWS:
            return error_reply(request_id, "bad-window", "window must be one of 5m, 1h, 24h")
        persistence = raman_history.OFF_INFO if state.demo or not state.history_persist or not state.history_enabled else state.persistence.info()
        return reply("history", request_id, demo=bool(state.demo), enabled=state.history_enabled, persistence=persistence,
                     **history.query(window, state.demo_time if state.demo else time.time()))
    if name == "history.clear":
        if state.demo:
            history.clear()  # demo history only; production state is untouched
            return reply("history-cleared", request_id, demo=True, persisted=False, error="")
        persisted = state.persistence.clear(history, time.time(), time.monotonic())
        state.events = {}
        if not state.history_enabled or not state.history_persist:
            state.persistence.release()
        # No loading/collection side effect when disabled or session-only.

        return reply("history-cleared", request_id, demo=False, persisted=persisted,
                     error="" if persisted else state.persistence.error)
    return error_reply(request_id, "unknown-command", "unknown command")


def apps_query(command, state, request_id):
    """A page of the cached app inventory; never a scan (at most one is scheduled)."""
    try:
        query, sort, offset, limit, pinned, generation = raman_apps.parse_query(command)
        focus = raman_apps.parse_focus(command, generation)
    except raman_apps.QueryError as exc:
        # Echoes a valid generation so the client can accept it; an invalid one is omitted.
        return dict(error_reply(request_id, exc.code, str(exc)), **exc.echo)
    base = reply("apps-page", request_id, generation=generation)
    if not state.detail_active:
        return raman_apps.empty_reply(base, "inactive", query, sort, offset, limit)
    snapshot = state.inventory.latest() if pinned is None else state.inventory.get(pinned)
    if snapshot is None and pinned is not None:
        return dict(error_reply(request_id, "snapshot-expired", "snapshot is no longer retained; query from offset 0"),
                    generation=generation, snapshot=pinned)
    if snapshot is None:
        return raman_apps.empty_reply(base, "pending", query, sort, offset, limit)
    stale = time.monotonic() - snapshot["monotonic"] > raman_apps.STALE_SECONDS
    if stale and snapshot is state.inventory.latest():
        state.detail_refresh = True  # the main loop runs it; never a second, parallel scan
    return raman_apps.page_reply(dict(base, stale=stale), snapshot, query, sort, offset, limit, focus)


def record_event(command, state, request_id):
    """Trusted sample references and whitelisted context; never a new scan."""
    if state.demo:
        return error_reply(request_id, "demo-only", "demo receipts are read-only; no live producer")
    if not state.configured or not state.history_enabled:
        return error_reply(request_id, "history-disabled", "history collection is disabled")
    if not state.dispatch.owner or command.get("epoch") != state.dispatch.epoch:
        return error_reply(request_id, "not-owner", "incident producer no longer owns dispatch")
    action = command.get("action", "start")
    severity, reason = command.get("severity"), command.get("reason")
    if action not in ("start", "extend", "upgrade", "recover", "interrupt") or severity not in raman_history.SEVERITIES or reason not in raman_history.INCIDENT_REASONS:
        return error_reply(request_id, "bad-incident", "invalid incident action, severity or reason")
    seq = command.get("summarySeq")
    if type(seq) is not int or seq < 1:
        return error_reply(request_id, "bad-seq", "summarySeq must be a positive integer")
    sample = state.history.sample_by_seq(seq)
    key = command.get("key") or (state.session + ":" + str(seq))
    if raman_history._short_text(key) is None:
        return error_reply(request_id, "bad-incident", "key must be a nonempty string of at most 64 characters")
    incident = next((i for i in state.history.incidents if i["id"] == key), None)
    if sample is None:
        if action == "interrupt" and incident is not None and incident["end"] is None:
            # A long gap can evict the raw reference; lastAt is retained receipt evidence.
            sample = {"t": incident.get("lastAt", incident["start"]), "v": incident["measurement"]}
        else:
            return error_reply(request_id, "unknown-seq", "summary is no longer available in this producer")
    if action == "start" and incident is None:
        start_seq = command.get("startSeq", seq)
        start = state.history.sample_by_seq(start_seq) if type(start_seq) is int else None
        if start is None or start["t"] > sample["t"]:
            return error_reply(request_id, "unknown-seq", "startSeq is unavailable or later than summarySeq")
        incident = state.history.record_incident(start_seq, severity, reason, state.context(time.monotonic()))
        incident.update(id=key, lastAt=sample["t"], status="active")
        thresholds = command.get("thresholds", {})
        if isinstance(thresholds, dict):
            incident["thresholds"] = {k: raman_history._num(thresholds.get(k), 1, 100) for k in
                                      ("warnPercent", "criticalPercent", "warnPressure", "criticalPressure")}
    elif incident is None:
        return error_reply(request_id, "unknown-incident", "incident is no longer available")
    elif action != "start":
        if incident["end"] is not None:
            return error_reply(request_id, "closed-incident", "incident has already closed")
        try:
            state.history.update_incident(incident, sample, action, severity, reason)
        except ValueError as exc:
            return error_reply(request_id, str(exc), "incident cannot be upgraded again")
    # Deduplicate transitions and notification reservations even after lost replies.
    event = (key, action)
    notify = command.get("notify") is True and action in ("start", "upgrade") and event not in state.events
    policy_saved = state.dispatch.claim(severity) if notify else True
    state.events[event] = True
    # Bounded by receipts; no unbounded per-tick request map.
    live_ids = {i["id"] for i in state.history.incidents}
    state.events = {k: v for k, v in state.events.items() if k[0] in live_ids}
    persisted = False
    if state.history_persist and action != "extend":
        persisted = state.persistence.checkpoint(state.history, time.time(), time.monotonic())
    return reply("incident", request_id, incident=incident, measurement=dict(sample["v"]),
                 notificationContext=raman_history.sanitize_context(state.context(time.monotonic())),
                 action=action, demo=False,
                 epoch=state.dispatch.epoch, notify=notify, persisted=persisted,
                 policySaved=policy_saved, units=raman_history.UNITS,
                 error=state.persistence.error if state.history_persist else "")


class ProbeState:
    """History objects shared by the main loop and the JSON command handler."""

    def __init__(self, persistence):
        self.history = raman_history.History(read_text("/proc/sys/kernel/random/boot_id").strip()[:64])
        self.persistence = persistence
        self.history_enabled = True
        self.history_persist = True
        self.configured = True
        self.configuration_id = None
        self.session = os.urandom(8).hex()
        self.dispatch = raman_history.DispatchLease(persistence.directory, self.history.boot_id)
        self.detail_active = False
        self.detail_snapshot = None
        self.detail_refresh = False
        self.groups = {}  # id -> group of the newest live scan, for kills and details
        self.proc_root = "/proc"  # where apps.kill re-reads membership; tests use a fake tree
        self.kill_followup = False  # a kill was handled: rescan soon to show the freed memory
        self.cpu = raman_apps.CpuTracker(clock_ticks())
        # GPU sampling: only for a visible Apps page with gpuMetrics auto, never in demos.
        self.gpu_wanted = False
        self.gpu = raman_gpu.GpuSampler(boot=raman_history.boot_clock)
        self.inventory = raman_apps.Inventory()
        self.events = {}
        self.demo = None
        self.demo_history = None
        self.demo_time = None  # fake clock: advances exactly one interval per demo summary
        self.storage = raman_storage.Controller()

    def configure(self, enabled, persist):
        changed = not self.configured or enabled != self.history_enabled or persist != self.history_persist
        if changed:
            if self.configured and self.history_enabled and self.history_persist:
                self.persistence.flush(self.history, time.time(), time.monotonic())
            self.persistence.release()
            self.history = raman_history.History(self.history.boot_id)
            self.events = {}
            # Re-entering persisted mode must adopt current saved state.
            self.persistence = raman_history.Persistence(self.persistence.directory)
        self.history_enabled, self.history_persist, self.configured = enabled, persist, True

    def context(self, mono):
        snap = self.detail_snapshot
        if not self.detail_active or snap is None or not 0 <= mono - snap[0] <= 2 * DETAIL_INTERVAL:
            return {"status": "not-observed"}
        apps = []
        for app in snap[2][:5]:
            entry = {"name": app["name"], "kb": footprint(app)}
            if app.get("memoryStatus") == "partial":
                entry["partial"] = True  # kb is a known lower bound
            apps.append(entry)
        return {"status": "observed", "at": snap[1], "apps": apps}

    def sync_gpu(self):
        """GPU sampling follows the gpu and detail subscriptions; a demo never samples hardware."""
        self.gpu.set_active(self.gpu_wanted and self.detail_active and not self.demo)

    def reset_detail(self):
        """Drop CPU baselines and cached inventory (panel closed, demo switch)."""
        self.cpu.reset()
        self.inventory.clear()
        self.groups = {}
        self.detail_snapshot = None
        self.detail_refresh = False

    def start_demo(self, scene):
        # Demo never owns the live detector, and cannot resume its continuity.
        for incident in self.history.incidents:
            if incident["end"] is None:
                incident["end"] = incident.get("lastAt", incident["start"])
                incident["status"] = "interrupted"
        if self.history_enabled and self.history_persist:
            self.persistence.checkpoint(self.history, time.time(), time.monotonic())
        self.demo = scene
        self.demo_history = raman_history.History("demo")
        self.demo_time = seed_demo_history(self.demo_history, scene, time.time())

    def stop_demo(self):
        self.demo = self.demo_history = self.demo_time = None


# ---------------------------------------------------------------- main loop

def _forward(worker, signum):
    try:
        os.kill(worker, signum)
    except ProcessLookupError:
        pass


def detach_from_kill():
    """Fork; return the parent's pid in the worker child. The parent only waits.

    Quickshell stops a Process with QProcess::kill (SIGKILL) when the shell
    restarts or reloads, so no handler in the process it started can save
    history. That process therefore just waits: it passes SIGTERM/SIGHUP on
    (a terminal's SIGINT already reaches the worker) and exits with the worker's
    status, so BarWidget still restarts a probe that exits. When it is killed,
    the worker sees stdin close or its parent change, saves, and exits.
    """
    parent = os.getpid()
    worker = os.fork()
    if worker == 0:
        return parent
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda received, _: _forward(worker, received))
    os.close(sys.stdin.fileno())  # only the worker reads commands
    _, status = os.waitpid(worker, 0)
    code = os.waitstatus_to_exitcode(status)
    os._exit(code if code >= 0 else 128 - code)


def main(parent):
    state = ProbeState(raman_history.Persistence(raman_history.state_dir()))
    if "--defer-history" in sys.argv:
        state.configured = False
        state.history_enabled = state.history_persist = False
    try:
        serve(state, parent)
    finally:
        state.storage.close()
        # stdin closed, parent killed, SIGTERM or a crash: keep the last minute of history.
        if state.configured and state.history_enabled and state.history_persist:
            state.persistence.flush(state.history, time.time(), time.monotonic())
        state.persistence.release()
        state.dispatch.release()


def serve(state, parent):
    detail = False
    seq = 0
    next_summary = 0.0
    next_detail = 0.0
    buffer = b""
    discarding = False  # inside an over-long command line, skipping to its newline
    stdin = sys.stdin.fileno()

    while True:
        if os.getppid() != parent:
            return  # Quickshell killed our waiting parent; stdin may not close (yet)
        now = time.monotonic()
        if now >= next_summary:
            seq += 1
            wall = time.time()
            # Live history keeps sampling during a demo so it has no hole afterwards.
            sample = read_sample()
            previous_clear = state.history.clear_token
            state.persistence.follow_clears(state.history)
            if state.configured and state.history_enabled:
                if state.history_persist:
                    state.persistence.tick(state.history, wall, now)
                state.history.ingest(sample, wall, now, raman_history.boot_clock(), None if state.demo else seq)
            if state.history.clear_token != previous_clear:
                state.events = {}
                emit(reply("history-cleared", None, demo=False, persisted=True, remote=True, error=""))
            state.dispatch.tick(now)
            dispatch = state.dispatch.info(now)
            meta = dict(monotonic=now, session=state.session, demo=bool(state.demo),
                        historyConfigId=state.configuration_id,
                        dispatchOwner=dispatch["owner"], dispatch=dispatch,
                        breakBefore=bool(state.history.raw and state.history.raw[-1]["brk"]))
            if state.demo:
                state.demo_time += SUMMARY_INTERVAL
                state.demo_history.ingest(state.demo["summary"], state.demo_time, state.demo_time, None, seq)
                emit(dict(state.demo["summary"], time=wall, seq=seq, **meta))
            else:
                emit(dict(summary(sample), seq=seq, **meta))
            next_summary = now + SUMMARY_INTERVAL
        demo = state.demo
        if detail and now >= next_detail and demo:
            rows, gpu = demo_gpu(demo, state.gpu_wanted)
            snapshot = state.inventory.publish(rows, time.time(), time.monotonic(), DEMO_INVENTORY,
                                               demo["cpu"], demo=True, gpu=gpu)
            emit(dict({"type": "apps", "apps": demo_preview(rows)}, **raman_apps.meta(snapshot)))
            next_detail = now + DETAIL_INTERVAL
        elif detail and now >= next_detail:
            apps, info, cpu = scan(state.cpu)
            # GPU: adopt a finished sample, attribute it to this scan's groups, then start
            # the next one (a worker thread, at most one, every 5 s) only if it is due.
            state.gpu.poll()
            gpu = state.gpu.attribute(apps)
            state.gpu.maybe_start([(pid, start) for g in apps for pid, start in g["pids"]])
            state.groups = {g["id"]: g for g in apps}
            snapshot = state.inventory.publish(apps, time.time(), time.monotonic(), info, cpu, gpu=gpu)
            # The Memory preview only: Apps queries use the whole snapshot.
            visible = [g for g in apps if (footprint(g) or 0) >= 1024][:MAX_APPS]
            state.detail_snapshot = (time.monotonic(), time.time(), visible)
            emit(dict({"type": "apps", "apps": [public_group(g) for g in visible]}, **raman_apps.meta(snapshot)))
            next_detail = now + DETAIL_INTERVAL

        deadline = next_summary if not detail else min(next_summary, next_detail)
        # Storage work runs in owned subprocesses. Wake for replies/reaping, and
        # always consume ready stdin before authorizing a prepared snapshot.
        timeout = max(0.0, deadline - time.monotonic())
        if state.storage.jobs:
            timeout = min(timeout, 0.05)
        ready, _, _ = select.select([stdin] + state.storage.fds(), [], [], timeout)
        if stdin not in ready:
            for message in state.storage.poll():
                emit(message)
            continue
        if not ready:
            continue
        chunk = os.read(stdin, 4096)
        if not chunk:
            return  # shell went away
        buffer += chunk
        if b"\n" not in buffer and len(buffer) > MAX_COMMAND_BYTES:
            if not discarding:
                emit(error_reply(None, "too-large", "command longer than %d bytes" % MAX_COMMAND_BYTES))
            buffer, discarding = b"", True
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            if discarding:
                discarding = False
                continue
            if len(raw) > MAX_COMMAND_BYTES:
                emit(error_reply(None, "too-large", "command longer than %d bytes" % MAX_COMMAND_BYTES))
                continue
            text = raw.decode("utf-8", "replace").strip()
            if text.startswith("{"):
                try:
                    emit(handle_json(text, state))
                except Exception as exc:  # a history bug must not take live telemetry down
                    sys.stderr.write("raman_probe: %s: %s\n" % (type(exc).__name__, str(exc)[:200]))
                    emit(error_reply(None, "internal", "command failed; see stderr"))
                if state.detail_refresh and detail:
                    next_detail = 0.0
                state.detail_refresh = False
                if state.kill_followup:
                    # Show the freed memory quickly.
                    next_summary = next_detail = time.monotonic() + 0.6
                    state.kill_followup = False
                continue
            parts = text.split()
            if not parts:
                continue
            command = parts[0]
            if command == "detail" and len(parts) > 1:
                detail = parts[1] == "1"
                state.detail_active = detail
                if not detail:
                    state.reset_detail()  # reopening starts CPU from warm-up
                state.sync_gpu()
                next_detail = 0.0
            elif command == "gpu" and len(parts) > 1:
                state.gpu_wanted = parts[1] == "1"
                state.sync_gpu()
                if detail:
                    next_detail = 0.0
            elif command == "refresh":
                next_summary = next_detail = 0.0
            elif command == "demo" and len(parts) > 1:
                scene = None if parts[1] == "off" else load_demo(parts[1])
                if scene:
                    state.start_demo(scene)
                else:
                    state.stop_demo()
                state.reset_detail()
                state.sync_gpu()
                next_summary = next_detail = 0.0
            elif command == "kill" and len(parts) > 2 and state.demo:
                demo = state.demo
                # Demo kills are cosmetic: drop the fake row, signal nothing.
                target = [a for a in demo["apps"] if a["id"] == parts[2] and not a["protected"]]
                demo["apps"] = [a for a in demo["apps"] if a not in target]
                emit({"type": "killed", "id": parts[2], "signal": parts[1],
                      "sent": len(target), "failed": 0, "error": "" if target else "protected"})
                next_detail = time.monotonic() + 0.6
            elif command == "kill" and len(parts) > 2:
                group = state.groups.get(parts[2])
                if group is None:
                    emit({"type": "killed", "id": parts[2], "signal": parts[1],
                          "sent": 0, "failed": 0, "error": "no longer running"})
                elif group["protected"]:
                    emit({"type": "killed", "id": parts[2], "signal": parts[1],
                          "sent": 0, "failed": 0, "error": "protected"})
                else:
                    emit(kill_group(group, parts[1]))
                # Show the freed memory quickly.
                next_summary = next_detail = time.monotonic() + 0.6

        for message in state.storage.poll():
            emit(message)


if __name__ == "__main__":
    parent = detach_from_kill()
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # a closed stdout raises BrokenPipeError in emit
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # exit through main's final save
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    try:
        main(parent)
    except KeyboardInterrupt:
        pass
