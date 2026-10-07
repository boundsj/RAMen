"""GPU memory and engine attribution for RAMen's Apps page (A3).

Source: the kernel's DRM client usage stats (Documentation/gpu/drm-usage-stats.rst,
https://docs.kernel.org/gpu/drm-usage-stats.html). Every open DRM file of a
supporting driver prints `drm-*` keys in /proc/PID/fdinfo/FD. This module
reads them for this user's processes, deduplicates clients, and attributes
them to RAMen's app groups. No vendor tool is run and nothing is installed.

Identity. A client is one `struct drm_file`: `drm-client-id`, unique globally
or per device. The key is (device, client id), where the device comes from
the descriptor's character device number (major 226) mapped through
/sys/class/drm, so the same id on two devices is two clients. A client reached
through several descriptors or processes (dup, fork, fd passing) counts once.
A descriptor without `drm-client-id` cannot be deduplicated and is never
counted. A client whose descriptors sit in more than one RAMen group is
reported once, per device, as shared across groups, never in full for each.

Memory. Regions are driver names: `vram*` (amdgpu, xe) and `local*` (i915)
are device-local memory (VRAM; on an integrated GPU a firmware carve-out);
`memory` (reserved by the spec), `system*`, `gtt` and `cpu` are system RAM
used by GPU buffers, already part of the machine's RAM and never added to an
app's footprint. Everything else (stolen, gds, doorbell, ...) is listed but
never summed. Resident (`drm-resident-*`, or amdgpu's deprecated alias
`drm-memory-*`, never both) is the authoritative reading; allocated
(`drm-total-*`, may not be instantiated) is reported separately. A buffer
shared between DRM files (`drm-shared-*`) is counted by every client holding
it, and fdinfo gives no buffer identities, so a sum over two or more clients
that hold shared buffers may count one buffer several times. Such sums are
kept as client-reference totals and marked `possible` overlap, never
presented as unique occupancy; device residuals say so too.

Engines. Per engine and client: `drm-engine-*` busy nanoseconds over the
monotonic time between two reads (a read slower than READ_SLACK has no
usable time and is an error, not a rate), or `drm-cycles-*` over `drm-total-cycles-*`,
or over `drm-maxfreq-*` x time; each divided by `drm-engine-capacity-*`
(1 when absent). A counter that goes backwards keeps the larger previous value
until it catches up (the spec's rule): that reading is `stale`. Engines are
never summed or clamped into one GPU percentage.

Power. Reading fdinfo can resume a runtime-suspended device (xe takes a
runtime-PM reference in show_fdinfo). The device's power/runtime_status is
read before each client read; a suspended or suspending device is `asleep`
and none of its clients are read. Identity and capability data come from
sysfs and /proc symlinks, which never wake a device.

Lifecycle. GpuSampler samples only while active (the probe activates it for a
visible Apps page with gpuMetrics auto, never for Memory alone), at most every
GPU_INTERVAL seconds between sample starts (also across deactivation and
reopening), in one worker thread at a time. Results carry the sampler
generation; deactivation, reopening and demos bump it so late results are
dropped, and a stuck worker is reported, never duplicated. Freshness is the
time since the sample began reading, not since it was handed over: a sample
that ran past SAMPLE_TIMEOUT stays `stale` (`sample-timeout`) until a timely
one replaces it.
"""

import errno
import os
import re
import stat as stat_module
import threading
import time

DRM_MAJOR = 226
GPU_INTERVAL = 5.0  # seconds between sample starts, at the fastest
STALE_SECONDS = 15.0  # an older sample is shown, labelled stale
SAMPLE_TIMEOUT = 10.0  # a worker running longer is reported as an error
MAX_GAP = 3 * GPU_INTERVAL  # longer between samples: engine baselines reset
SUSPEND_SLACK = 1.0
READ_SLACK = 0.1  # an fdinfo read slower than this has no usable time for busy-time engines
ANOMALY_FACTOR = 1.1  # an engine above 110% of its capacity is a counter anomaly

MAX_DEVICES = 16
MAX_FDS_PER_PROCESS = 4096
MAX_FD_ENTRIES = 65536
MAX_DRM_FDS = 1024  # fdinfo reads per sample
MAX_FDINFO_BYTES = 16384
MAX_ENGINES = 16
MAX_REGIONS = 16
MAX_SYSFS_BYTES = 256

SOURCE = "drm-fdinfo"
STATUSES = ("available", "warming-up", "unsupported", "permission-denied", "asleep", "stale", "error")

UNITS = {
    "vramKb": "kB, device-local memory resident (drm-resident-<vram*|local*>), summed over this app's clients "
              "on the device; with vramOverlap possible a buffer shared by several of them counts once per client",
    "vramSharedKb": "kB, size of the summed clients' buffers shared with another DRM file (drm-shared-<vram*|local*>)",
    "vramAllocatedKb": "kB, device-local memory requested (drm-total-<vram*|local*>), not necessarily resident",
    "systemKb": "kB, system RAM resident in GPU buffers (drm-resident-<memory|system*|gtt|cpu>); part of RAM, not added",
    "systemAllocatedKb": "kB, system-memory GPU buffers requested (drm-total-...)",
    "enginePercent": "% of one engine class's capacity (busy time or cycles / capacity)",
    "gpuMemoryKb": "kB, sum of vramKb over devices (same resident semantics); sort key of the gpu-memory lens; "
                   "a client-reference total, not unique occupancy, when gpuMemoryOverlap is true",
}

_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,48}$")
_PDEV = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")
_NODE = re.compile(r"^(card|renderD)[0-9]{1,5}$")
_DEDICATED = re.compile(r"^(vram[0-9]*|local[0-9]+)$")
_SYSTEM = re.compile(r"^(memory|system[0-9]*|gtt|cpu)$")
_SIZE_UNITS = {"": 1, "KiB": 1024, "MiB": 1024 * 1024}
_FREQ_UNITS = {"Hz": 1, "KHz": 1000, "kHz": 1000, "MHz": 1000000}
_MEMORY_STATS = ("total", "shared", "resident", "purgeable", "active")
_TIMED_BASES = ("busy-time", "cycles-at-max-frequency")  # measured against the monotonic read time


# ---------------------------------------------------------------- parsing

def _uint(text):
    return int(text) if text.isascii() and text.isdigit() else None


def parse_size(value):
    """Bytes of a `<uint> [KiB|MiB]` memory value (bytes by default), or None."""
    number, _, unit = value.strip().partition(" ")
    amount = _uint(number)
    factor = _SIZE_UNITS.get(unit.strip())
    return None if amount is None or factor is None else amount * factor


def parse_fdinfo(text):
    """The drm-* keys of one fdinfo text, or None when it is not a DRM client with usage stats.

    Returns {"driver", "pdev", "clientId", "regions": {name: {stat: bytes}},
    "engines": {name: {...}}, "invalid": count}. Unknown keys, driver-specific
    keys and drm-client-name (set by userspace) are ignored. Values that do not
    parse are counted in "invalid" and left out, never read as zero.
    """
    info = {"driver": None, "pdev": None, "clientId": None, "regions": {}, "engines": {}, "invalid": 0}
    engines = {}

    def engine(name):
        if name not in engines:
            if len(engines) >= MAX_ENGINES or not _KEY.match(name):
                info["invalid"] += 1
                return None
            engines[name] = {}
        return engines[name]

    for line in text.splitlines():
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key.startswith("drm-"):
            continue
        if key == "drm-driver":
            info["driver"] = "".join(c for c in value if c.isprintable())[:32] or None
        elif key == "drm-pdev":
            info["pdev"] = value.lower() if _PDEV.match(value) else None
        elif key == "drm-client-id":
            info["clientId"] = _uint(value)
        elif key.startswith("drm-engine-capacity-"):
            slot = engine(key[len("drm-engine-capacity-"):])
            if slot is not None:
                slot["capacity"] = _uint(value)  # None or 0: invalid, reported as an error
        elif key.startswith("drm-engine-"):
            number, _, unit = value.partition(" ")
            slot = engine(key[len("drm-engine-"):])
            if slot is not None:
                slot["ns"] = _uint(number) if unit.strip() == "ns" else None
                if slot["ns"] is None:
                    info["invalid"] += 1
        elif key.startswith("drm-total-cycles-"):
            slot = engine(key[len("drm-total-cycles-"):])
            if slot is not None:
                slot["totalCycles"] = _uint(value)
        elif key.startswith("drm-cycles-"):
            slot = engine(key[len("drm-cycles-"):])
            if slot is not None:
                slot["cycles"] = _uint(value)
        elif key.startswith("drm-maxfreq-"):
            number, _, unit = value.partition(" ")
            slot = engine(key[len("drm-maxfreq-"):])
            factor = _FREQ_UNITS.get(unit.strip() or "Hz")
            amount = _uint(number)
            if slot is not None:
                slot["maxfreqHz"] = amount * factor if amount and factor else None
        else:
            stat_name, _, region = key[len("drm-"):].partition("-")
            name = {"memory": "residentAlias"}.get(stat_name, stat_name)
            if name not in _MEMORY_STATS + ("residentAlias",) or not region:
                continue
            if region not in info["regions"]:
                if len(info["regions"]) >= MAX_REGIONS or not _KEY.match(region):
                    info["invalid"] += 1
                    continue
                info["regions"][region] = {}
            size = parse_size(value)
            if size is None:
                info["invalid"] += 1
            else:
                info["regions"][region][name] = size
    if info["driver"] is None:
        return None
    info["engines"] = engines
    return info


def classify_region(name):
    """"dedicated" (device-local), "system" (system RAM) or "other"."""
    if _DEDICATED.match(name):
        return "dedicated"
    if _SYSTEM.match(name):
        return "system"
    return "other"


def resident_of(values):
    """Resident bytes of one region: drm-resident, else amdgpu's deprecated alias; never both added."""
    if "resident" in values:
        return values["resident"]
    return values.get("residentAlias")


def memory_summary(info):
    """{class: {"resident", "allocated", "shared", "regions"}} for one client.

    A class's resident (or allocated) sum is None unless every region of that
    class reports it, so a missing key is never treated as zero.
    """
    out = {}
    for region, values in sorted(info["regions"].items()):
        kind = classify_region(region)
        entry = out.setdefault(kind, {"resident": 0, "allocated": 0, "shared": 0, "regions": []})
        entry["regions"].append(region)
        for field, value in (("resident", resident_of(values)), ("allocated", values.get("total")),
                             ("shared", values.get("shared"))):
            entry[field] = None if value is None or entry[field] is None else entry[field] + value
    return out


def overlap(parts):
    """"possible" when two or more of these client memory parts hold resident
    memory and report shared buffers (or no drm-shared key): one buffer shared
    by them is then in each client's resident sum, and fdinfo has no buffer
    identities to deduplicate it. Otherwise "none": the sum counts each buffer
    once (a buffer in two clients' sums is shared, so both report it)."""
    holders = sum(1 for p in parts if p is not None and p["resident"] and (p["shared"] is None or p["shared"] > 0))
    return "possible" if holders > 1 else "none"


def engine_rate(engine, base, mono, read_seconds=0.0):
    """(reading, new baseline) for one engine of one client.

    reading is {"percent", "status", "basis", "capacity"}; percent is None
    unless status is "available". base is the previous baseline or None.
    mono is when the read began; read_seconds how long it took. A time-based
    reading from a read slower than READ_SLACK is an error ("slow-read") and
    leaves no baseline: the counter was taken at an unknown moment in it.
    """
    capacity = engine.get("capacity", 1)
    if engine.get("ns") is not None:
        basis, value, ref, freq = "busy-time", engine["ns"], mono, None
    elif engine.get("cycles") is not None and engine.get("totalCycles") is not None:
        basis, value, ref, freq = "gpu-cycles", engine["cycles"], engine["totalCycles"], None
    elif engine.get("cycles") is not None and engine.get("maxfreqHz"):
        basis, value, ref, freq = "cycles-at-max-frequency", engine["cycles"], mono, engine["maxfreqHz"]
    else:
        return {"percent": None, "status": "unsupported", "basis": None, "capacity": capacity}, None
    reading = {"percent": None, "status": "warming-up", "basis": basis, "capacity": capacity}
    if not capacity:
        reading.update(status="error", reason="capacity")  # zero or unparsable: the spec forbids it
        return reading, None
    if basis in _TIMED_BASES and read_seconds > READ_SLACK:
        reading.update(status="error", reason="slow-read")
        return reading, None
    current = {"basis": basis, "value": value, "ref": ref, "capacity": capacity}
    if base is None or base["basis"] != basis or base["capacity"] != capacity:
        return reading, current
    if value < base["value"] or ref <= base["ref"]:
        # Behind the previous larger value (or no time passed): keep that value until it catches up.
        reading["status"] = "stale"
        return reading, base
    span = ref - base["ref"]
    delta = value - base["value"]
    if basis == "busy-time":
        percent = delta / (span * 1e9) / capacity * 100.0
    elif basis == "gpu-cycles":
        percent = delta / span / capacity * 100.0
    else:
        percent = delta / (freq * span) / capacity * 100.0
    if percent > 100.0 * ANOMALY_FACTOR:
        reading.update(status="error", reason="anomaly")  # rebaseline; never shown as a spike
        return reading, current
    reading.update(percent=percent, status="available")
    return reading, current


# ---------------------------------------------------------------- sysfs devices

def read_small(path, limit=MAX_SYSFS_BYTES):
    """Stripped text of a small sysfs/proc file, or None."""
    try:
        with open(path, "rb") as handle:
            return handle.read(limit).decode("ascii", "replace").strip()
    except OSError:
        return None


def enumerate_devices(sys_root="/sys", cache=None):
    """({"major:minor": device key}, {device key: info}, incomplete reason or None).

    Only sysfs attributes and symlinks are read; none of them touches the
    hardware. cache keeps each device's static identity by its sysfs path.
    """
    cache = {} if cache is None else cache
    base = sys_root + "/class/drm"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return {}, {}, None  # no DRM class: no devices
    rdevs, devices, reason = {}, {}, None
    for name in names:
        if not _NODE.match(name):
            continue
        number = read_small("%s/%s/dev" % (base, name))
        try:
            path = os.path.realpath("%s/%s/device" % (base, name))
        except OSError:
            continue
        if not number or not os.path.isdir(path):
            continue
        major, _, minor = number.partition(":")
        if major != str(DRM_MAJOR) or not minor.isdigit():
            continue
        if path not in devices:
            if len(devices) >= MAX_DEVICES:
                reason = "device-limit"
                continue
            info = cache.get(path)
            if info is None:
                subsystem = os.path.basename(os.path.realpath(path + "/subsystem")) or "device"
                driver_link = path + "/driver"
                driver = os.path.basename(os.path.realpath(driver_link)) if os.path.islink(driver_link) else None
                leaf = os.path.basename(path)
                vendor, device = read_small(path + "/vendor"), read_small(path + "/device")
                total = _uint(read_small(path + "/mem_info_vram_total") or "")
                info = cache[path] = {
                    "key": path,
                    "id": "".join(c for c in "%s:%s" % (subsystem, leaf) if c.isprintable())[:80],
                    "subsystem": subsystem,
                    "pdev": leaf.lower() if subsystem == "pci" and _PDEV.match(leaf) else None,
                    "driver": "".join(c for c in (driver or "") if c.isprintable())[:32] or None,
                    "pciId": "%s:%s" % (vendor[2:], device[2:]) if vendor and device
                    and vendor.startswith("0x") and device.startswith("0x") else None,
                    "nodes": [],
                    "vramTotalBytes": total,
                }
            devices[path] = dict(info, nodes=[])
        devices[path]["nodes"].append(name)
        rdevs[number] = path
    return rdevs, devices, reason


def runtime_status(device_path):
    """The device's runtime-PM status: "active", "suspended", ..., "no-runtime-pm" or "unreadable"."""
    path = device_path + "/power/runtime_status"
    try:
        with open(path, "rb") as handle:
            text = handle.read(64).decode("ascii", "replace").strip()
    except FileNotFoundError:
        return "no-runtime-pm"  # always powered while bound: reading cannot resume it
    except OSError:
        return "unreadable"
    return text if text in ("active", "suspended", "suspending", "resuming", "unsupported", "error") else "unreadable"


SLEEPING = ("suspended", "suspending", "resuming")


def is_awake(status):
    """Whether a client read is allowed: only a device that is known to be powered.

    "unsupported" here is the kernel's runtime-PM state for a device that
    never runtime-suspends. A sleeping or unreadable status is never read."""
    return status in ("active", "no-runtime-pm", "unsupported")


def device_number(dir_fd, name):
    """"major:minor" of a /proc/PID/fd entry that is a character device, else None."""
    try:
        st = os.stat(name, dir_fd=dir_fd)
    except OSError:
        return None
    if not stat_module.S_ISCHR(st.st_mode):
        return None
    return "%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev))


# ---------------------------------------------------------------- collection (worker)

def _open_dir(name, dir_fd=None):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY, dir_fd=dir_fd)


def _start_ticks(dir_fd):
    try:
        fd = os.open("stat", os.O_RDONLY, dir_fd=dir_fd)
        with open(fd, "rb") as handle:
            text = handle.read(4096).decode("utf-8", "replace")
        return int(text[text.rfind(")") + 2:].split()[19])
    except (OSError, IndexError, ValueError):
        return None


def _read_fdinfo(dir_fd, name):
    """Parsed fdinfo of one descriptor (bounded read), "gone", or "error"."""
    try:
        fd = os.open("fdinfo/" + name, os.O_RDONLY, dir_fd=dir_fd)
        with open(fd, "rb") as handle:
            raw = handle.read(MAX_FDINFO_BYTES)
    except (FileNotFoundError, ProcessLookupError):
        return "gone"
    except OSError:
        return "error"
    return parse_fdinfo(raw.decode("utf-8", "replace"))


def collect(targets, proc_root="/proc", sys_root="/sys", cache=None, device_of=device_number,
            power_of=runtime_status, clock=time.monotonic):
    """One raw sample for targets, a list of (pid, start ticks) from the newest scan.

    Per process: its DRM descriptors, and for each, the client read from
    fdinfo (devices awake only). The /proc/PID directory stays open while it
    is read, and its start time must match the scan's, so a reused PID is
    never attributed. Every count is bounded; hitting a bound sets
    "incomplete".
    """
    started = clock()
    rdevs, devices, reason = enumerate_devices(sys_root, cache)
    sample = {"startedAt": started, "endedAt": started, "devices": devices, "processes": {}, "clients": {},
              "incomplete": reason, "counts": {"processes": 0, "fdEntries": 0, "drmFds": 0, "fdinfoReads": 0,
                                                "unknownDevices": 0, "mismatches": 0}}
    if not devices:
        return sample
    counts = sample["counts"]
    asleep = {}

    def awake(key):
        if asleep.get(key):
            return False
        status = power_of(key)
        devices[key]["runtime"] = status
        if not is_awake(status):
            asleep[key] = True
        return not asleep.get(key)

    for key in devices:
        awake(key)
    for pid, start in targets:
        record = {"status": "ok", "fds": []}
        sample["processes"][(pid, start)] = record
        try:
            dir_fd = _open_dir("%s/%d" % (proc_root, pid))
        except (FileNotFoundError, ProcessLookupError):
            record["status"] = "gone"
            continue
        except OSError as exc:
            record["status"] = "permission-denied" if exc.errno in (errno.EACCES, errno.EPERM) else "error"
            continue
        try:
            if _start_ticks(dir_fd) != start:
                record["status"] = "gone"
                continue
            counts["processes"] += 1
            try:
                fd_dir = _open_dir("fd", dir_fd)
            except (FileNotFoundError, ProcessLookupError):
                record["status"] = "gone"
                continue
            except OSError as exc:
                record["status"] = "permission-denied" if exc.errno in (errno.EACCES, errno.EPERM) else "error"
                continue
            try:
                try:
                    names = os.listdir(fd_dir)
                except (FileNotFoundError, ProcessLookupError):
                    record["status"] = "gone"  # exited while being read
                    continue
                except OSError:
                    record["status"] = "error"
                    continue
                if len(names) > MAX_FDS_PER_PROCESS:
                    record["status"] = "limit"
                    sample["incomplete"] = sample["incomplete"] or "fd-limit"
                    names = names[:MAX_FDS_PER_PROCESS]
                for name in names:
                    if counts["fdEntries"] >= MAX_FD_ENTRIES:
                        record["status"] = "limit"
                        sample["incomplete"] = sample["incomplete"] or "fd-limit"
                        break
                    counts["fdEntries"] += 1
                    try:
                        target = os.readlink(name, dir_fd=fd_dir)
                    except OSError:
                        continue  # closed meanwhile
                    if not target.startswith("/dev/dri/"):
                        continue
                    number = device_of(fd_dir, name)
                    key = rdevs.get(number) if number else None
                    if key is None:
                        counts["unknownDevices"] += int(number is not None)
                        continue
                    counts["drmFds"] += 1
                    if not awake(key):
                        state = "asleep" if devices[key]["runtime"] in SLEEPING else "error"
                        record["fds"].append((key, None, state))
                        continue
                    if counts["fdinfoReads"] >= MAX_DRM_FDS:
                        record["fds"].append((key, None, "unread"))
                        record["status"] = "limit"
                        sample["incomplete"] = sample["incomplete"] or "drm-fd-limit"
                        continue
                    counts["fdinfoReads"] += 1
                    read_at = clock()
                    info = _read_fdinfo(dir_fd, name)
                    read_seconds = clock() - read_at
                    if info == "gone":
                        continue
                    if info == "error":
                        record["fds"].append((key, None, "error"))
                        continue
                    if info is None:
                        record["fds"].append((key, None, "no-stats"))
                        continue
                    device = devices[key]
                    if info["pdev"] and device["pdev"] and info["pdev"] != device["pdev"]:
                        counts["mismatches"] += 1  # the descriptor changed between stat and read
                        record["fds"].append((key, None, "error"))
                        continue
                    if info["clientId"] is None:
                        record["fds"].append((key, None, "no-client-id"))
                        continue
                    client = (key, info["clientId"])
                    record["fds"].append((key, client, "read"))
                    if client not in sample["clients"]:
                        sample["clients"][client] = {"readAt": read_at, "readSeconds": read_seconds, "info": info}
            finally:
                os.close(fd_dir)
        finally:
            os.close(dir_fd)
    for key, device in devices.items():
        if device.get("vramTotalBytes") is not None and awake(key):
            device["vramUsedBytes"] = _uint(read_small(key + "/mem_info_vram_used") or "")
    sample["endedAt"] = clock()
    return sample


# ---------------------------------------------------------------- sampler (main thread)

def _kb(value):
    return None if value is None else value // 1024


def _add(total, value):
    return None if total is None or value is None else total + value


class GpuSampler:
    """Owns GPU sampling state for the probe's main loop.

    set_active(on) follows the probe's gpu subscription; maybe_start(targets)
    starts one worker when due; poll() adopts a finished worker's sample;
    attribute(groups) annotates scan groups and returns the snapshot metadata.
    Only the main thread calls these. The worker only runs collect().
    """

    def __init__(self, proc_root="/proc", sys_root="/sys", collect_fn=None, clock=time.monotonic,
                 wall=time.time, boot=None, threaded=True):
        self.proc_root, self.sys_root = proc_root, sys_root
        self.cache = {}  # static device identities, by sysfs path; touched by one worker at a time
        self.collect = collect_fn or (lambda targets: collect(targets, self.proc_root, self.sys_root, self.cache))
        self.clock, self.wall, self.boot = clock, wall, boot
        self.threaded = threaded
        self.lock = threading.Lock()
        self.active = False
        self.generation = 0
        self.worker = None  # {"thread", "generation", "started", "timedOut"}: never more than one
        self.finished = None  # (generation, sample or exception) handed over by the worker
        self.capabilities = {}  # device key -> {"dedicated": bool, "system": bool, "engines": [...]}
        self.runs = 0
        self.last_start = None  # physical cadence: kept across generations, so starts stay GPU_INTERVAL apart
        self._reset()

    def _reset(self):
        self.baselines = {}
        self.latest = None
        self.last_boot = None
        self.error = None
        self.reset_reason = "start"
        self.anomalies = 0

    def set_active(self, on):
        """Start or stop sampling. Any change drops baselines and the latest sample, but not the
        time of the last start: closing and reopening never samples sooner than GPU_INTERVAL."""
        on = bool(on)
        if on == self.active:
            return
        self.active = on
        self.generation += 1
        self._reset()

    def busy(self):
        return self.worker is not None

    def due(self, now):
        return self.active and self.worker is None and (self.last_start is None or now - self.last_start >= GPU_INTERVAL)

    def maybe_start(self, targets):
        """Start a sample of these (pid, start) targets if one is due. Returns True if started."""
        now = self.clock()
        if not self.due(now):
            return False
        self.last_start = now
        self.runs += 1
        generation = self.generation
        targets = list(targets)
        if not self.threaded:
            self._finish(generation, now, self._run_collect(targets))
            self.poll()
            return True
        thread = threading.Thread(target=lambda: self._finish(generation, now, self._run_collect(targets)),
                                  name="raman-gpu", daemon=True)
        self.worker = {"thread": thread, "generation": generation, "started": now}
        thread.start()
        return True

    def _run_collect(self, targets):
        try:
            return self.collect(targets)
        except Exception as exc:  # reported as an error state; never takes the probe down
            return exc

    def _finish(self, generation, started, result):
        with self.lock:
            self.finished = (generation, started, result)

    def poll(self):
        """Adopt a finished sample of the current generation; note a stuck worker.

        A sample that ran longer than SAMPLE_TIMEOUT (noticed while it ran, or
        by its own start and end) is adopted as stale data: its error stays
        until a timely sample replaces it."""
        with self.lock:
            finished, self.finished = self.finished, None
        if finished is not None:
            timed_out = bool(self.worker and self.worker.get("timedOut"))
            self.worker = None
            generation, started, result = finished
            if generation == self.generation and self.active:
                if isinstance(result, Exception):
                    self.error = "sample-failed"
                else:
                    late = timed_out or result["endedAt"] - result["startedAt"] > SAMPLE_TIMEOUT
                    self.error = "sample-timeout" if late else None
                    self._adopt(result, started)
        elif self.worker is not None and self.clock() - self.worker["started"] > SAMPLE_TIMEOUT:
            self.worker["timedOut"] = True
            self.error = "sample-timeout"  # no second worker starts until this one returns

    def age(self):
        """Seconds since the latest sample began reading (its oldest reading), or None."""
        return None if self.latest is None else self.clock() - self.latest["measuredAt"]

    def _adopt(self, raw, started):
        boot = self.boot() if self.boot else None
        reset = None
        if self.latest is not None:
            gap = raw["startedAt"] - self.latest["startedAt"]
            if gap > MAX_GAP or gap < 0:
                reset = "gap"
            elif boot is not None and self.last_boot is not None and (boot - self.last_boot) - gap > SUSPEND_SLACK:
                reset = "suspend"
        elif self.reset_reason:
            reset = self.reset_reason
        if reset and reset != "start":
            self.baselines = {}
        self.reset_reason = reset
        self.last_boot = boot
        clients, baselines, anomalies = {}, {}, 0
        for key, client in raw["clients"].items():
            info = client["info"]
            base = self.baselines.get(key, {})
            engines, next_base = {}, {}
            for name, engine in info["engines"].items():
                reading, kept = engine_rate(engine, base.get(name), client["readAt"], client.get("readSeconds", 0.0))
                engines[name] = reading
                anomalies += reading.get("reason") == "anomaly"
                if kept is not None:
                    next_base[name] = kept
            baselines[key] = next_base
            clients[key] = {"memory": memory_summary(info), "engines": engines}
            caps = self.capabilities.setdefault(key[0], {"dedicated": False, "system": False, "engines": set()})
            memory = clients[key]["memory"]
            caps["dedicated"] |= memory.get("dedicated", {}).get("resident") is not None
            caps["system"] |= memory.get("system", {}).get("resident") is not None
            caps["engines"] |= set(info["engines"])
        self.baselines = baselines  # clients not read this time lose their baseline
        self.anomalies = anomalies
        # Measurement time, not hand-over time: started is when this sample began reading (sampler clock).
        self.latest = dict(raw, clients=clients, measuredAt=started,
                           sampledAt=self.wall() - max(0.0, self.clock() - started),
                           collectSeconds=max(0.0, raw["endedAt"] - raw["startedAt"]))
        for key in list(self.capabilities):
            if key not in raw["devices"]:
                del self.capabilities[key]  # device gone (unbound): forget its capabilities

    # ------------------------------------------------------------ attribution

    def status(self):
        if not self.active:
            return "inactive", None
        if self.latest is None:
            return ("error", self.error) if self.error else ("warming-up", None)
        if not self.latest["devices"]:
            return "unsupported", "no-drm-devices"
        if self.error or self.age() > STALE_SECONDS:
            return "stale", self.error or "old-sample"
        return "available", None

    def attribute(self, groups):
        """Set gpu, gpuMemoryKb, gpuStatus and gpuCoverage on each group; return snapshot metadata."""
        status, reason = self.status()
        meta = {"status": status, "reason": reason, "source": SOURCE, "intervalSeconds": GPU_INTERVAL,
                "sampledAt": None, "ageSeconds": None, "collectSeconds": None, "reset": None, "incomplete": None,
                "memoryLens": False, "devices": [], "anomalies": 0, "counts": None, "units": UNITS}
        latest = self.latest
        if status == "inactive":
            meta["units"] = None  # Memory-only and closed views: keep the message small
        if latest is None or status in ("inactive", "unsupported"):
            row_status = status
            for group in groups:
                group.update(gpu=[], gpuMemoryKb=None, gpuMemoryOverlap=False, gpuComplete=None, gpuStatus=row_status,
                             gpuCoverage=None)
            if latest is not None:
                meta.update(sampledAt=latest["sampledAt"], ageSeconds=self.age(),
                            collectSeconds=latest["collectSeconds"])
            return meta
        meta.update(sampledAt=latest["sampledAt"], ageSeconds=self.age(), collectSeconds=latest["collectSeconds"],
                    reset=self.reset_reason, incomplete=latest["incomplete"],
                    anomalies=self.anomalies, counts=dict(latest["counts"]))
        stale = status == "stale"
        processes, clients, devices = latest["processes"], latest["clients"], latest["devices"]
        owners = {}
        for group in groups:
            for pid, start in group.get("pids", []):
                record = processes.get((pid, start))
                for _, client, _ in (record["fds"] if record else []):
                    if client is not None:
                        owners.setdefault(client, set()).add(group["id"])
        for group in groups:
            self._attribute_group(group, processes, clients, devices, owners, stale)
        meta["devices"] = [self._device_meta(key, devices[key], clients, owners) for key in sorted(devices)]
        meta["memoryLens"] = any(d["memory"]["dedicated"]["status"] in ("available", "asleep", "stale")
                                 and self.capabilities.get(d["key"], {}).get("dedicated") for d in meta["devices"])
        for device in meta["devices"]:
            del device["key"]
        return meta

    def _attribute_group(self, group, processes, clients, devices, owners, stale):
        pids = group.get("pids", [])
        read = denied = 0
        per_device = {}
        for pid, start in pids:
            record = processes.get((pid, start))
            if record is None or record["status"] == "gone":
                continue  # joined after the sample: not measured yet
            if record["status"] in ("permission-denied", "error"):
                denied += record["status"] == "permission-denied"
                continue
            read += record["status"] == "ok"
            for key, client, state in record["fds"]:
                entry = per_device.setdefault(key, {"own": set(), "shared": set(), "states": {}})
                if client is None:
                    entry["states"][state] = entry["states"].get(state, 0) + 1
                elif len(owners.get(client, ())) > 1:
                    entry["shared"].add(client)
                else:
                    entry["own"].add(client)
        entries = [self._group_entry(devices[key], per_device[key], clients)
                   for key in sorted(per_device)[:MAX_DEVICES]]
        known = [e["vramKb"] for e in entries if e["vramKb"] is not None]
        members = len(pids)
        complete = read == members
        # Every member read and every device entry counted all of its clients: judged before
        # staleness relabels them, so a stale sum keeps its lower-bound meaning.
        counted_all = complete and all(e["status"] == "available" for e in entries)
        if stale:
            for e in entries:
                if e["status"] in ("available", "partial"):  # the entries that hold readings
                    e["status"] = "stale"
                    for slot in e["engines"].values():
                        slot["status"] = "stale"
            row_status = "stale"
        elif not entries:
            row_status = "none" if complete else "permission-denied" if denied and not read \
                else "warming-up" if not read else "partial"
        elif any(e["status"] == "available" for e in entries):
            row_status = "available" if complete and all(e["status"] == "available" for e in entries) else "partial"
        else:
            order = ("asleep", "shared", "unsupported", "permission-denied", "error")
            row_status = next((s for s in order if any(e["status"] == s for e in entries)), entries[0]["status"])
        overlapping = any(e["vramOverlap"] == "possible" for e in entries if e["vramKb"] is not None)
        group.update(gpu=entries, gpuMemoryKb=sum(known) if known else None, gpuMemoryOverlap=overlapping,
                     gpuComplete=counted_all, gpuStatus=row_status, gpuCoverage={"measured": read, "members": members})

    def _group_entry(self, device, entry, clients):
        own = [clients[c] for c in sorted(entry["own"]) if c in clients]
        states = entry["states"]
        result = {"deviceId": device["id"], "driver": device["driver"], "source": SOURCE, "status": "available",
                  "vramKb": None, "vramAllocatedKb": None, "vramSharedKb": None, "vramOverlap": "none",
                  "vramStatus": "unsupported", "vramSemantics": "resident",
                  "systemKb": None, "systemAllocatedKb": None, "systemSharedKb": None, "systemOverlap": "none",
                  "systemStatus": "unsupported",
                  "engines": {}, "clients": len(own), "sharedClients": len(entry["shared"]),
                  "unidentified": states.get("no-client-id", 0), "unread": states.get("unread", 0)}
        if not own:
            if entry["shared"]:
                result["status"] = "shared"
            elif states.get("asleep"):
                result["status"] = "asleep"
            elif states.get("error"):
                result["status"] = "error"
            else:
                result["status"] = "unsupported"  # no usage stats or no client id: cannot attribute
            result["vramStatus"] = result["systemStatus"] = result["status"]
            return result
        for kind, cls in (("vram", "dedicated"), ("system", "system")):
            parts = [c["memory"].get(cls) for c in own]
            if all(p is None for p in parts):
                result[kind + "Status"] = "unsupported"  # this device reports no such region for these clients
                continue
            resident = allocated = shared = 0
            for part in parts:
                part = part or {"resident": 0, "allocated": 0, "shared": 0}
                resident = _add(resident, part["resident"])
                allocated = _add(allocated, part["allocated"])
                shared = _add(shared, part["shared"])
            result[kind + "Kb"] = _kb(resident)
            result[kind + "AllocatedKb"] = _kb(allocated)
            result[kind + "SharedKb"] = _kb(shared)
            result[kind + "Overlap"] = overlap(parts)  # summed client references; no buffer IDs to deduplicate
            result[kind + "Status"] = "available" if resident is not None else "unsupported"
        engines = {}
        for client in own:
            for name, reading in client["engines"].items():
                slot = engines.setdefault(name, {"percent": None, "status": None, "basis": reading["basis"],
                                                 "capacity": reading["capacity"], "readings": []})
                slot["readings"].append(reading)
        for name in sorted(engines)[:MAX_ENGINES]:
            slot = engines[name]
            readings = slot.pop("readings")
            good = [r["percent"] for r in readings if r["status"] == "available"]
            if good:
                slot["percent"] = sum(good)
                slot["status"] = "available" if len(good) == len(readings) else "partial"
            else:
                order = ("warming-up", "stale", "error", "unsupported")
                slot["status"] = next(s for s in order if any(r["status"] == s for r in readings))
            result["engines"][name] = slot
        if states.get("asleep") or states.get("unread") or states.get("error") or entry["shared"]:
            result["status"] = "partial"
        return result

    def _device_meta(self, key, device, clients, owners):
        visible = [(c, clients[c]) for c in clients if c[0] == key]
        caps = self.capabilities.get(key, {"dedicated": False, "system": False, "engines": set()})
        runtime = device.get("runtime")
        asleep = runtime in SLEEPING
        unknown = runtime is not None and not asleep and not is_awake(runtime)

        def metric(cls):
            parts = [c["memory"].get(cls) for _, c in visible]
            regions = sorted({r for p in parts if p for r in p["regions"]})
            if asleep:
                status = "asleep"
            elif unknown:
                status = "error"  # power state unreadable: clients were not read
            elif not visible:
                status = "no-clients"
            elif not regions:
                status = "unsupported"
            elif all(p is None or p["resident"] is not None for p in parts):
                status = "available"
            else:
                status = "unsupported"
            return {"status": status, "semantics": "resident", "regions": regions,
                    "seen": bool(caps["dedicated" if cls == "dedicated" else "system"])}

        dedicated = metric("dedicated")
        resident = shared = 0
        cross_clients = 0
        cross = 0
        for client, data in visible:
            part = data["memory"].get("dedicated") or {"resident": 0, "shared": 0}
            resident = _add(resident, part["resident"])
            shared = _add(shared, part["shared"])
            if len(owners.get(client, ())) > 1:
                cross_clients += 1
                cross = _add(cross, part["resident"])
        used = device.get("vramUsedBytes")
        unattributed = None
        if used is not None and resident is not None and dedicated["status"] == "available":
            difference = used - resident
            unattributed = _kb(difference) if difference >= 0 else None
        summed = visible and dedicated["status"] == "available"
        overlapping = overlap([c["memory"].get("dedicated") for _, c in visible]) if summed else None
        engine_names = sorted({n for _, c in visible for n in c["engines"]})[:MAX_ENGINES]
        return {
            "key": key,
            "id": device["id"],
            "driver": device["driver"],
            "pdev": device["pdev"],
            "pciId": device["pciId"],
            "nodes": device["nodes"][:4],
            "runtimeStatus": runtime,
            "status": "asleep" if asleep else "error" if unknown else "available" if visible else "no-clients",
            "clients": len(visible),
            "memory": {"dedicated": dedicated, "system": metric("system")},
            "engines": {"status": "asleep" if asleep else "error" if unknown else "available" if engine_names
                        else "no-clients" if not visible else "unsupported",
                        "names": engine_names, "seen": sorted(caps["engines"])[:MAX_ENGINES]},
            "totals": {
                "vramResidentKb": _kb(resident) if summed else None,
                "vramSharedKb": _kb(shared) if summed else None,
                "vramOverlap": overlapping,
                "crossGroupClients": cross_clients,
                "crossGroupVramKb": _kb(cross) if cross_clients and dedicated["status"] == "available" else None,
                "deviceVramUsedKb": _kb(used),
                "deviceVramTotalKb": _kb(device.get("vramTotalBytes")),
                "unattributedVramKb": unattributed,
                # A lower bound when the visible sum may count a buffer twice: known shared bytes, or
                # several resident clients without drm-shared keys (unknown sharing is not uniqueness).
                "unattributedLowerBound": bool(unattributed is not None and (shared or overlapping == "possible")),
            },
        }


def strip_rows(groups):
    """GPU fields for an inactive sampler (demo without a GPU subscription, for example)."""
    for group in groups:
        group.update(gpu=[], gpuMemoryKb=None, gpuMemoryOverlap=False, gpuComplete=None, gpuStatus="inactive",
                     gpuCoverage=None)


def inactive_meta():
    return {"status": "inactive", "reason": None, "source": SOURCE, "intervalSeconds": GPU_INTERVAL,
            "sampledAt": None, "ageSeconds": None, "collectSeconds": None, "reset": None, "incomplete": None,
            "memoryLens": False, "devices": [], "anomalies": 0, "counts": None, "units": None}
