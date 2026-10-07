"""Recent CPU accounting, app inventory snapshots and apps.query for RAMen.

raman_probe.py reads /proc and groups processes; this module turns those
readings into recent CPU rates and a bounded, cached inventory that clients
page through with the JSON `apps.query` command. It does no /proc access of
its own, so it imports and tests on any platform (tests/test_apps.py).

CPU: each process's utime + stime (its own threads only, never the children's
cumulative counters) is compared with the previous reading of the same
(pid, start ticks) identity, using SC_CLK_TCK and the monotonic time of each
read. cpuCorePercent is 100 for one fully busy logical CPU; cpuMachinePercent
divides by the online logical CPU count. A first reading, panel reopen, long
gap, suspend or CPU topology change has no baseline: the value is null and
the status says it is warming up. Counter rollback or impossible jumps
rebaseline that process instead of producing a spike.

Inventory: every scan publishes one immutable snapshot of all groups (within
the process/group ceilings below). Queries filter, sort and page a retained
snapshot and never walk /proc; paging can pin a snapshot by its ID.

GPU (A3): raman_gpu.py attributes DRM client memory and engine readings to
the scan's groups while the Apps page asks for them. Rows carry a per-device
`gpu` list and the `gpu-memory` lens sorts by `gpuMemoryKb` (device-local
memory resident); without readings it is null and sorts last. With
`gpuMemoryOverlap` it sums several clients' references, so a buffer they share
counts once per client: a labelled reference total, not unique occupancy.

Actions (A2): `apps.kill` and `apps.details` name one group by ID. A kill also
carries the membership generation the client armed; the probe rejects it when
the newest scan's membership differs. Validation lives here; raman_probe.py
re-checks each process and signals. Details are built on request from the
scan's member list plus a fresh, bounded command-line read, and never kept.
"""

import hashlib
import json

PROTOCOL_VERSION = 1

MIN_CPU_INTERVAL = 1.0  # seconds; a faster rescan reuses the last full interval
MAX_CPU_GAP = 7.5  # seconds between scans (3 x the 2.5 s detail cadence)
SUSPEND_SLACK = 1.0  # boot clock ahead of monotonic by more than this: suspended
ANOMALY_FACTOR = 1.1  # a process above this many online CPUs is a counter anomaly

MAX_SCAN_PROCESSES = 8192  # owned processes read per scan
MAX_GROUPS = 4096  # groups kept in a snapshot
SNAPSHOTS_KEPT = 4  # about 10 s of pinned paging at the 2.5 s cadence
STALE_SECONDS = 5.0  # a query older than this schedules one refresh
PAGE_LIMIT = 50
MAX_QUERY = 256
MAX_NAME = 256
MAX_REPLY_BYTES = 128 * 1024
SORTS = ("memory", "cpu", "gpu-memory")
MAX_ID = 512  # characters of a group ID in a focus, kill or details request
MAX_GENERATION = 64
SIGNALS = ("TERM", "KILL")
MAX_DETAIL_MEMBERS = 40  # members listed in one details reply
MAX_COMMAND = 512  # characters of one member's command line in details

UNITS = {
    "pss": "kB",
    "swap": "kB",
    "cpuCorePercent": "% of one logical CPU",
    "cpuMachinePercent": "% of online logical CPUs",
    "sampledAt": "Unix seconds",
    "gpuMemoryKb": "kB, device-local GPU memory resident, summed over devices and this app's clients; "
                   "with gpuMemoryOverlap a buffer shared by several clients counts once per client",
    "gpu": "per-device entries; see the snapshot's gpu.units",
}


def parse_cpu_list(text):
    """Number of CPUs in a sysfs list such as "0-3,6", or None if unparsable."""
    text = (text or "").strip()
    if not text:
        return None
    count = 0
    for part in text.split(","):
        first, _, last = part.partition("-")
        try:
            low, high = int(first), int(last or first)
        except ValueError:
            return None
        if high < low:
            return None
        count += high - low + 1
    return count


def display_name(text):
    """Printable text of at most MAX_NAME characters, for untrusted names."""
    return "".join(c for c in str(text or "") if c.isprintable())[:MAX_NAME]


def membership_generation(pids):
    """Opaque version of a group's (pid, start ticks) membership."""
    text = ",".join("%d:%d" % (pid, start) for pid, start in sorted(pids))
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


class CpuTracker:
    """Recent CPU rates keyed by (pid, start ticks); see the module docstring.

    One scan is begin(), sample() for each process, then end(). Processes that
    were not sampled in a scan (exited, reused PID) lose their baseline.
    """

    def __init__(self, clock_ticks):
        self.clock_ticks = clock_ticks if clock_ticks and clock_ticks > 0 else None
        self.reset()

    def reset(self):
        self.baselines = {}  # (pid, start) -> (ticks, monotonic, last rate or None)
        self.next = {}
        self.last_mono = self.last_boot = None
        self.topology = None
        self.online = None
        self.anomalies = 0

    def begin(self, mono, boot, topology, online):
        """Start a scan. Returns why baselines were reset, or None."""
        reason = None
        if self.last_mono is None:
            reason = "start"
        elif topology != self.topology:
            reason = "topology"
        elif mono - self.last_mono > MAX_CPU_GAP or mono < self.last_mono:
            reason = "gap"
        elif boot is not None and self.last_boot is not None \
                and (boot - self.last_boot) - (mono - self.last_mono) > SUSPEND_SLACK:
            reason = "suspend"
        if reason:
            self.baselines = {}
        self.next = {}
        self.anomalies = 0
        self.last_mono, self.last_boot = mono, boot
        self.topology, self.online = topology, online
        return reason

    def sample(self, pid, start, ticks, mono):
        """(cpuCorePercent or None, "available" | "warming-up") for one process."""
        key = (pid, start)
        if ticks is None or self.clock_ticks is None:
            return None, "unavailable"
        base = self.baselines.get(key)
        if base is None:
            self.next[key] = (ticks, mono, None)
            return None, "warming-up"
        base_ticks, base_mono, last = base
        elapsed = mono - base_mono
        if ticks < base_ticks or elapsed < 0:
            self.anomalies += 1  # counter rollback: rebaseline, never a negative rate
            self.next[key] = (ticks, mono, None)
            return None, "warming-up"
        if elapsed < MIN_CPU_INTERVAL:
            self.next[key] = base
            return last, "available" if last is not None else "warming-up"
        rate = (ticks - base_ticks) * 100.0 / self.clock_ticks / elapsed
        quantum = 100.0 / self.clock_ticks / elapsed  # one tick of rounding
        if self.online and rate > self.online * 100.0 * ANOMALY_FACTOR + 2 * quantum:
            self.anomalies += 1
            self.next[key] = (ticks, mono, None)
            return None, "warming-up"
        self.next[key] = (ticks, mono, rate)
        return rate, "available"

    def end(self):
        self.baselines, self.next = self.next, {}


# ---------------------------------------------------------------- snapshots

def public_row(group, online, sampled_at):
    """The protocol row for one group (no PIDs)."""
    core = group.get("cpuCorePercent")
    return {
        "id": group["id"],
        "generation": group.get("generation"),
        "kind": group["kind"],
        "name": group["name"],
        "host": group["host"],
        "count": group["count"],
        "protected": group["protected"],
        "pss": group["pss"],
        "swap": group["swap"],
        "memoryStatus": group.get("memoryStatus", "available"),
        "memoryCoverage": group.get("memoryCoverage"),
        "cpuCorePercent": core,
        "cpuMachinePercent": core / online if core is not None and online else None,
        "cpuStatus": group.get("cpuStatus", "unavailable"),
        "cpuCoverage": group.get("cpuCoverage"),
        "gpu": group.get("gpu") or [],
        "gpuMemoryKb": group.get("gpuMemoryKb"),
        "gpuMemoryOverlap": bool(group.get("gpuMemoryOverlap")),
        "gpuComplete": group.get("gpuComplete"),
        "gpuStatus": group.get("gpuStatus", "inactive"),
        "gpuCoverage": group.get("gpuCoverage"),
        "sampledAt": sampled_at,
    }


def _footprint(row):
    return None if row["pss"] is None else row["pss"] + (row["swap"] or 0)


def _limit_groups(groups, limit):
    """At most limit groups, alternating the memory, CPU and GPU memory rankings."""
    if len(groups) <= limit:
        return groups, False
    rankings = [sorted(groups, key=lambda g, sort=sort: _sort_key(g, sort)) for sort in SORTS]
    kept, ids = [], set()
    for pair in zip(*rankings):
        for group in pair:
            if group["id"] not in ids and len(kept) < limit:
                ids.add(group["id"])
                kept.append(group)
        if len(kept) >= limit:
            break
    return kept, True


def _sort_key(row, sort):
    value = row.get("cpuCorePercent") if sort == "cpu" else row.get("gpuMemoryKb") if sort == "gpu-memory" \
        else _footprint(row)
    return (value is None, -(value or 0), row["id"])


class Inventory:
    """The last few published snapshots, for queries and pinned paging."""

    def __init__(self):
        self.snapshots = []
        self.next_id = 0

    def clear(self):
        self.snapshots = []

    def publish(self, groups, sampled_at, mono, info, cpu, demo=False, gpu=None):
        """Store one scan's groups (dicts from group_processes) as a snapshot."""
        groups, capped = _limit_groups(groups, MAX_GROUPS)
        online = cpu.get("onlineCpus")
        rows = []
        for group in groups:
            row = public_row(group, online, sampled_at)
            pids = [pid for pid, _ in group.get("pids", [])]
            rows.append((row, (row["name"] + "\n" + row["host"]).casefold(), frozenset(pids)))
        info = dict(info)
        if capped and not info.get("incompleteReason"):
            info["incompleteReason"] = "group-limit"
        info["groups"] = len(rows)
        info["complete"] = not info.get("incompleteReason")
        info["processLimit"] = MAX_SCAN_PROCESSES
        info["groupLimit"] = MAX_GROUPS
        self.next_id += 1
        snapshot = {"id": self.next_id, "sampledAt": sampled_at, "monotonic": mono,
                    "rows": rows, "inventory": info, "cpu": dict(cpu), "demo": bool(demo), "gpu": gpu,
                    "coverage": {
                        "memoryPartial": sum(1 for r, _, _ in rows if r["memoryStatus"] == "partial"),
                        "memoryUnavailable": sum(1 for r, _, _ in rows if r["memoryStatus"] == "unavailable"),
                        "cpuPartial": sum(1 for r, _, _ in rows if r["cpuStatus"] == "partial"),
                        "cpuUnavailable": sum(1 for r, _, _ in rows
                                              if r["cpuStatus"] in ("warming-up", "unavailable")),
                        "gpuReadings": sum(1 for r, _, _ in rows if r["gpuStatus"] in ("available", "partial")),
                        "gpuPartial": sum(1 for r, _, _ in rows if r["gpuStatus"] == "partial"),
                        "gpuOverlap": sum(1 for r, _, _ in rows if r["gpuMemoryOverlap"]
                                          and r["gpuMemoryKb"] is not None),
                    }}
        self.snapshots = (self.snapshots + [snapshot])[-SNAPSHOTS_KEPT:]
        return snapshot

    def latest(self):
        return self.snapshots[-1] if self.snapshots else None

    def get(self, snapshot_id):
        return next((s for s in self.snapshots if s["id"] == snapshot_id), None)


def meta(snapshot):
    """Snapshot metadata added to the legacy `apps` message."""
    return {"snapshot": snapshot["id"], "sampledAt": snapshot["sampledAt"],
            "inventory": snapshot["inventory"], "cpu": snapshot["cpu"],
            "coverage": snapshot["coverage"], "gpu": snapshot.get("gpu")}


class QueryError(Exception):
    def __init__(self, code, message, **echo):
        Exception.__init__(self, message)
        self.code = code
        self.echo = echo  # validated request fields the error reply repeats (the generation)


def _integer(value, low, high, default=None):
    if value is None and default is not None:
        return default
    if type(value) is not int or not low <= value <= high:
        return None
    return value


def parse_query(command):
    """Validated (query, sort, offset, limit, snapshot, generation) or QueryError.

    The generation is checked first so every later error can echo it; clients
    accept an apps reply only for their pending request ID and generation."""
    generation = command.get("generation")
    if generation is not None and _integer(generation, 0, 2 ** 53 - 1) is None:
        raise QueryError("bad-request", "generation must be an integer 0..2^53-1")
    query = command.get("query", "")
    if not isinstance(query, str) or len(query) > MAX_QUERY:
        raise QueryError("bad-query", "query must be a string of at most %d characters" % MAX_QUERY,
                         generation=generation)
    sort = command.get("sort", "memory")
    if sort not in SORTS:
        raise QueryError("bad-sort", "sort must be memory, cpu or gpu-memory", generation=generation)
    offset = _integer(command.get("offset"), 0, MAX_GROUPS, 0)
    limit = _integer(command.get("limit"), 1, PAGE_LIMIT, PAGE_LIMIT)
    if offset is None or limit is None:
        raise QueryError("bad-page", "offset must be 0..%d and limit 1..%d" % (MAX_GROUPS, PAGE_LIMIT),
                         generation=generation)
    snapshot = command.get("snapshot")
    if snapshot is not None and _integer(snapshot, 1, 2 ** 53 - 1) is None:
        raise QueryError("bad-request", "snapshot must be a positive integer", generation=generation)
    return query, sort, offset, limit, snapshot, generation


def _group_id(value):
    return isinstance(value, str) and 0 < len(value) <= MAX_ID and value.isprintable()


def parse_focus(command, generation):
    """The optional `focus` group ID of an apps.query, or QueryError."""
    focus = command.get("focus")
    if focus is not None and not _group_id(focus):
        raise QueryError("bad-request", "focus must be a group ID of at most %d printable characters" % MAX_ID,
                         generation=generation)
    return focus


def parse_action(command, kill):
    """Validated (id, membership generation, signal) of apps.kill / (id, None, None) of apps.details."""
    generation = command.get("generation")
    if generation is not None and _integer(generation, 0, 2 ** 53 - 1) is None:
        raise QueryError("bad-request", "generation must be an integer 0..2^53-1")
    group = command.get("id")
    if not _group_id(group):
        raise QueryError("bad-request", "id must be a group ID of at most %d printable characters" % MAX_ID,
                         generation=generation)
    if not kill:
        return group, None, None
    membership = command.get("membership")
    if not isinstance(membership, str) or not 0 < len(membership) <= MAX_GENERATION:
        raise QueryError("bad-request", "membership must be the armed row's generation", generation=generation)
    signame = command.get("signal")
    if signame not in SIGNALS:
        raise QueryError("bad-request", "signal must be TERM or KILL", generation=generation)
    return group, membership, signame


def matches(entry, needle, pid):
    row, text, pids = entry
    return not needle or needle in text or (pid is not None and pid in pids)


def _matching(snapshot, query, sort):
    needle = query.strip().casefold()
    # ASCII digits only: "²", "②" or "٣" pass str.isdigit() but are literal name text.
    pid = int(needle) if needle.isascii() and needle.isdigit() and len(needle) <= 10 else None
    found = [entry[0] for entry in snapshot["rows"] if matches(entry, needle, pid)]
    found.sort(key=lambda row: _sort_key(row, sort))
    return found


def run_query(snapshot, query, sort, offset, limit):
    """Filtered, sorted page of a snapshot: (total matches, rows)."""
    found = _matching(snapshot, query, sort)
    return len(found), found[offset:offset + limit]


def focus_info(snapshot, found, focus):
    """Where one group stands in a snapshot, whichever page is shown.

    present: in the snapshot at all; rank: its index among the query's
    matches (None when filtered out); generation: its current membership."""
    row = next((entry[0] for entry in snapshot["rows"] if entry[0]["id"] == focus), None)
    rank = next((index for index, match in enumerate(found) if match["id"] == focus), None)
    return {"id": focus, "present": row is not None, "generation": row["generation"] if row else None,
            "rank": rank, "row": row}


def page_reply(base, snapshot, query, sort, offset, limit, focus=None):
    """apps-page fields for a snapshot, trimmed to MAX_REPLY_BYTES."""
    found = _matching(snapshot, query, sort)
    total, rows = len(found), found[offset:offset + limit]
    reply = dict(base, status="ready", demo=snapshot["demo"], snapshot=snapshot["id"],
                 sampledAt=snapshot["sampledAt"], query=query, sort=sort, offset=offset, limit=limit,
                 total=total, inventory=snapshot["inventory"], cpu=snapshot["cpu"],
                 coverage=snapshot["coverage"], gpu=snapshot.get("gpu"), units=UNITS, rows=rows)
    if focus is not None:
        reply["focus"] = focus_info(snapshot, found, focus)
    while True:
        reply["nextOffset"] = offset + len(rows) if offset + len(rows) < total else None
        if len(json.dumps(reply, separators=(",", ":"))) + 1 <= MAX_REPLY_BYTES or not rows:
            return reply
        rows = rows[:-1]
        reply["rows"] = rows


def empty_reply(base, status, query, sort, offset, limit):
    return dict(base, status=status, demo=False, snapshot=None, sampledAt=None, query=query, sort=sort,
                offset=offset, limit=limit, total=0, nextOffset=None, inventory=None, cpu=None,
                coverage=None, gpu=None, units=UNITS, rows=[])


# ---------------------------------------------------------------- details

def command_text(argv):
    """(printable command line of at most MAX_COMMAND characters, truncated)."""
    text = "".join(c if c.isprintable() else " " for c in " ".join(argv))
    return text[:MAX_COMMAND], len(text) > MAX_COMMAND


def member_row(member, online, command):
    """One process in a details reply; command is (text, truncated, status)."""
    core = member.get("cpu")
    text, truncated, status = command
    return {"pid": member["pid"], "name": display_name(member.get("name") or member.get("comm")),
            "state": member.get("state"), "pss": member.get("pss"), "swap": member.get("swap"),
            "memoryStatus": "available" if member.get("pss") is not None else "unavailable",
            "cpuCorePercent": core, "cpuMachinePercent": core / online if core is not None and online else None,
            "cpuStatus": "available" if core is not None else member.get("cpuState") or "unavailable",
            "command": text, "commandTruncated": truncated, "commandStatus": status}


def details_reply(base, row, members, online, read_command):
    """apps-details fields: the group row and its heaviest members, trimmed to MAX_REPLY_BYTES.

    read_command(member) -> (text or None, truncated, status) is called only
    for the members listed, at request time; nothing is cached."""
    ranked = sorted(members, key=lambda m: (-((m.get("pss") or 0) + (m.get("swap") or 0)),
                                            -(m.get("cpu") or 0), m["pid"]))
    shown = [member_row(m, online, read_command(m)) for m in ranked[:MAX_DETAIL_MEMBERS]]
    reply = dict(base, row=row, membersTotal=len(members), members=shown, commandLimit=MAX_COMMAND, units=UNITS)
    while len(json.dumps(reply, separators=(",", ":"))) + 1 > MAX_REPLY_BYTES and shown:
        shown = shown[:-1]
        reply["members"] = shown
    return reply
