"""Bounded memory history for the RAMen probe.

Pure bookkeeping plus a small persistence layer; nothing here reads /proc, so
it imports and tests on any platform. raman_probe.py feeds one sample per
summary tick and answers history queries from it.

Time handling: monotonic seconds measure sample spacing and weights; wall
seconds label points and key buckets; a boot clock (CLOCK_BOOTTIME, when the
platform has one) tells suspend apart from a wall-clock step. Any break in
continuity is recorded as a gap and the next point is flagged so a chart never
draws a line across downtime.

Memory metrics are kB, pressure metrics are percent. A source that could not be
read is None and stays None in every aggregate.
"""

import fcntl
import json
import math
import os
import tempfile
import time

SCHEMA_VERSION = 1
FILE_FORMAT = "raman-history"

METRICS = ("used", "available", "total", "swapUsed", "swapTotal", "zramRam", "psiSome10", "psiFull10")
PERCENT_METRICS = ("psiSome10", "psiFull10")
UNITS = {name: ("%" if name in PERCENT_METRICS else "kB") for name in METRICS}

NOMINAL_INTERVAL = 2.0   # the probe's summary cadence; weight of a sample after a break
STALL_SECONDS = 6.0      # three missed ticks: the sampler was not running
CLOCK_TOLERANCE = 2.0    # wall vs monotonic disagreement that counts as a clock step
SUSPEND_TOLERANCE = 2.0  # boot clock ahead of monotonic by more than this: suspended

RAW_LIMIT = 150          # 5 minutes of 2 s samples, memory only
RAW_SPAN = 300
RINGS = ((10, 360), (60, 1440))  # (resolution s, bucket limit): 1 hour and 24 hours
WINDOWS = {"5m": (None, RAW_SPAN), "1h": (10, 3600), "24h": (60, 86400)}
MAX_POINTS = 360
RETENTION = 86400

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_INCIDENTS = 50
MAX_INCIDENT_APPS = 5
MAX_NAME = 256
MAX_ID = 64
MAX_GAPS = 512
SAVE_INTERVAL = 60.0
MAX_MARKER_BYTES = 4096

MAX_TIME = 1e11          # wall seconds (year 5138); anything beyond is corruption
MAX_VALUE = 1e15         # kB or percent
MAX_WEIGHT = 1e7         # seconds or samples in one bucket

GAP_REASONS = ("stall", "suspend", "clock", "restart", "reboot")
SEVERITIES = ("warn", "critical")
INCIDENT_REASONS = ("pressure", "used")

# Persistence status for a history that is never saved (demo mode).
OFF_INFO = {"owner": False, "load": "none", "save": "off", "error": "", "lastSavedAt": None}


def _num(value, low=-MAX_VALUE, high=MAX_VALUE):
    """value if it is an int/float within [low, high], else None.

    Only comparisons, so NaN, infinities and huge integers are rejected without
    raising (math.isfinite overflows on integers past float range).
    """
    if type(value) not in (int, float):
        return None
    return value if low <= value <= high else None


def _time(value):
    return _num(value, 0, MAX_TIME)


def _short_text(value):
    """A non-empty string of at most MAX_ID characters, or None."""
    return value if isinstance(value, str) and 0 < len(value) <= MAX_ID else None


def _incident_tail(incident):
    return incident["end"] if incident["end"] is not None else incident.get("lastAt", incident["start"])


def _round(metric, value):
    if value is None:
        return None
    return round(value, 2) if metric in PERCENT_METRICS else int(round(value))


# ------------------------------------------------------------ accumulators
# Per metric: [min, max, last, count, weighted sum, weight]. None = no reading.

def _acc_add(acc, value, weight):
    if value is None:
        return acc
    if acc is None:
        return [value, value, value, 1, value * weight, weight]
    acc[0] = min(acc[0], value)
    acc[1] = max(acc[1], value)
    acc[2] = value
    acc[3] += 1
    acc[4] += value * weight
    acc[5] += weight
    return acc


def _acc_merge(earlier, later):
    if earlier is None:
        return None if later is None else list(later)
    if later is None:
        return list(earlier)
    return [min(earlier[0], later[0]), max(earlier[1], later[1]), later[2],
            earlier[3] + later[3], earlier[4] + later[4], earlier[5] + later[5]]


def _acc_mean(acc):
    return acc[4] / acc[5] if acc[5] > 0 else acc[2]


def _new_bucket(start, wall):
    return {"t": start, "a": wall, "z": wall, "n": 0, "cov": 0.0, "brk": False,
            "m": {name: None for name in METRICS}}


def _fill_bucket(bucket, wall, weight, values, brk):
    bucket["z"] = wall
    bucket["n"] += 1
    bucket["cov"] += weight
    bucket["brk"] = bucket["brk"] or brk
    for name in METRICS:
        bucket["m"][name] = _acc_add(bucket["m"][name], values[name], weight)
    return bucket


def _merge_buckets(start, buckets):
    """One bucket from time-ordered buckets, keeping min/max/last/count/mean exact."""
    merged = _new_bucket(start, buckets[0]["a"])
    merged["brk"] = any(b["brk"] for b in buckets)
    for bucket in buckets:
        merged["z"] = max(merged["z"], bucket["z"])
        merged["n"] += bucket["n"]
        merged["cov"] += bucket["cov"]
        for name in METRICS:
            merged["m"][name] = _acc_merge(merged["m"][name], bucket["m"][name])
    return merged


def _drop_front(items, expired, limit):
    """Trim a time-ordered list in place: expired items from the front, then to limit."""
    drop = 0
    while drop < len(items) and expired(items[drop]):
        drop += 1
    drop = max(drop, len(items) - limit)
    if drop:
        del items[:drop]


class Ring:
    """Fixed-resolution buckets keyed by wall time, oldest first."""

    def __init__(self, resolution, limit):
        self.resolution = resolution
        self.limit = limit
        self.buckets = []

    def add(self, wall, weight, values, brk):
        start = math.floor(wall / self.resolution) * self.resolution
        if self.buckets and self.buckets[-1]["t"] > start:
            return  # older than what we hold; History truncates on clock rollback first
        if not self.buckets or self.buckets[-1]["t"] < start:
            self.buckets.append(_new_bucket(start, wall))
        _fill_bucket(self.buckets[-1], wall, weight, values, brk)

    def prune(self, now):
        horizon = now - self.resolution * self.limit
        _drop_front(self.buckets, lambda b: b["t"] + self.resolution <= horizon, self.limit)


class History:
    """In-memory history: raw samples, two bucket rings, gaps and incidents.

    Every method takes its clock readings as arguments, so tests drive it with
    a fake clock and the probe with the real one.
    """

    def __init__(self, boot_id=""):
        self.boot_id = boot_id
        self.clear_token = ""  # the latest clear (cleared.json token) this history reflects
        self._prev = None      # (wall, mono, boot) of the last sample this process took
        self._reset()

    def _reset(self):
        self.raw = []
        self.rings = {res: Ring(res, limit) for res, limit in RINGS}
        self.gaps = []
        self.incidents = []
        self._saved_boot = None  # boot ID of adopted saved history

    def newest(self):
        """Wall time of the newest point held, saved or live."""
        return max((r.buckets[-1]["z"] for r in self.rings.values() if r.buckets), default=None)

    # ---------------------------------------------------------- ingestion

    def ingest(self, values, wall, mono, boot=None, seq=None):
        """Add one summary reading. values maps metric -> number or None."""
        values = {name: _num(values.get(name)) for name in METRICS}
        weight, brk = NOMINAL_INTERVAL, False
        reason, gap_start = self._continuity(wall, mono, boot)
        if reason == "contiguous":
            weight = max(0.0, mono - self._prev[1])
        elif reason is not None:
            self.gaps.append({"start": gap_start, "end": wall, "reason": reason})
            brk = True
        self._prev = (wall, mono, boot)
        self.raw.append({"t": wall, "seq": seq, "w": weight, "brk": brk, "v": values})
        for ring in self.rings.values():
            ring.add(wall, weight, values, brk)
        self._prune(wall)

    def _continuity(self, wall, mono, boot):
        """("contiguous", _), (None, _) for a first point, or (gap reason, gap start)."""
        if self._prev is None:
            last = self.newest()  # saved history adopted before our first sample
            if last is None:
                return None, None
            if wall < last:
                self._truncate_after(wall)
                return "clock", wall
            if wall - last <= STALL_SECONDS:
                return None, None
            return self._resume_reason(), last
        prev_wall, prev_mono, prev_boot = self._prev
        dw, dm = wall - prev_wall, mono - prev_mono
        if dw < 0:
            self._truncate_after(wall)
            return "clock", wall
        if boot is not None and prev_boot is not None and (boot - prev_boot) - dm > SUSPEND_TOLERANCE:
            return "suspend", prev_wall
        if abs(dw - dm) > CLOCK_TOLERANCE:
            return "clock", prev_wall
        if dm > STALL_SECONDS:
            return "stall", prev_wall
        return "contiguous", None

    def _resume_reason(self):
        """Why saved history and this process's samples are apart."""
        rebooted = self._saved_boot and self.boot_id and self._saved_boot != self.boot_id
        return "reboot" if rebooted else "restart"

    def _keep(self, keep):
        """Retain what keep(first observed, last observed) accepts, in every store."""
        self.raw = [s for s in self.raw if keep(s["t"], s["t"])]
        for ring in self.rings.values():
            ring.buckets = [b for b in ring.buckets if keep(b["a"], b["z"])]
        self.gaps = [g for g in self.gaps if keep(g["start"], g["end"])]
        self.incidents = [i for i in self.incidents if keep(i["start"], _incident_tail(i))]

    def _truncate_after(self, wall):
        """Drop everything stamped after wall (the clock went backwards)."""
        self._keep(lambda first, last: last <= wall)

    def forget_before(self, wall):
        """Drop data first observed before wall (a clear made by another probe)."""
        self._keep(lambda first, last: first >= wall)

    def _prune(self, now):
        horizon = now - RETENTION
        _drop_front(self.raw, lambda s: s["t"] <= now - RAW_SPAN, RAW_LIMIT)
        for ring in self.rings.values():
            ring.prune(now)
        _drop_front(self.gaps, lambda g: g["end"] <= horizon, MAX_GAPS)
        self._prune_incidents(now)

    def _prune_incidents(self, now):
        horizon = now - RETENTION
        self.incidents = [i for i in self.incidents
                          if (i.get("status") == "active" and i["end"] is None) or _incident_tail(i) > horizon]
        for incident in self.incidents:
            # Keep lifecycle identity/timing, never an unlimited observation.
            if incident["start"] <= horizon:
                incident["measurement"] = {name: None for name in METRICS}
                incident["measurementStatus"] = "expired"
                incident["summarySeq"] = None
                incident.pop("thresholds", None)
                incident.pop("threshold", None)
            context = incident.get("context", {})
            if context.get("status") == "observed":
                at = _time(context.get("at"))
                if at is None or at <= horizon:
                    incident["context"] = {"status": "expired" if at is not None else "not-observed"}
            upgrade = incident.get("upgrade")
            if upgrade and upgrade["at"] <= horizon:
                upgrade["measurement"] = {name: None for name in METRICS}
                upgrade["measurementStatus"] = "expired"
        self._bound_incidents()

    def _bound_incidents(self):
        # Preserve the live lifecycle when removing old closed receipts.
        excess = len(self.incidents) - MAX_INCIDENTS
        if excess > 0:
            victims = sorted(self.incidents, key=lambda i: (i["end"] is None, _incident_tail(i)))[:excess]
            ids = {i["id"] for i in victims}
            self.incidents = [i for i in self.incidents if i["id"] not in ids]

    def sample_by_seq(self, seq):
        """The stored reading for a summary sequence number, if still in the raw ring."""
        for sample in reversed(self.raw):
            if sample["seq"] == seq:
                return sample
        return None

    def clear(self):
        """Forget all history. Live sampling continues with the next reading."""
        self._reset()

    # ---------------------------------------------------------- incidents

    def record_incident(self, seq, severity, reason, context=None):
        """Store a bounded incident built from the reading behind summary seq.

        Only whitelisted fields are kept: app display names (truncated) and
        footprints, never command lines or environment. Raises ValueError for
        an unknown seq or an invalid enum.
        """
        sample = self.sample_by_seq(seq)
        if sample is None:
            raise ValueError("unknown-seq")
        if severity not in SEVERITIES or reason not in INCIDENT_REASONS:
            raise ValueError("bad-enum")
        incident = {
            "id": os.urandom(8).hex(),
            "start": sample["t"],
            "end": None,
            "severity": severity,
            "reason": reason,
            "summarySeq": seq,
            "measurement": dict(sample["v"]),
            "context": sanitize_context(context),
        }
        self.incidents.append(incident)
        self._bound_incidents()
        return incident

    # ---------------------------------------------------------- queries

    def update_incident(self, incident, sample, action, severity, reason):
        """Extend/upgrade/close the same receipt; no process identities."""
        if action == "upgrade":
            if incident["severity"] != "warn" or severity != "critical":
                raise ValueError("bad-upgrade")
            incident["severity"], incident["reason"] = severity, reason
            incident["upgrade"] = {"at": sample["t"], "measurement": dict(sample["v"])}
        incident["lastAt"] = max(incident.get("lastAt", incident["start"]), sample["t"])
        if action in ("recover", "interrupt"):
            incident["end"] = max(incident["start"], sample["t"])
            incident["status"] = "recovered" if action == "recover" else "interrupted"
        return incident

    def query(self, window, now):
        """Columnar points for a window, at most MAX_POINTS of them."""
        self._prune_incidents(now)
        resolution, span = WINDOWS[window]
        start = now - span
        if resolution is None:
            points = [_sample_bucket(s) for s in self.raw if s["t"] >= start]
            resolution = NOMINAL_INTERVAL
        else:
            points = [b for b in self.rings[resolution].buckets if b["t"] + resolution > start]
            group = max(1, int(math.ceil(span / resolution / MAX_POINTS)))
            if group > 1:
                points, resolution = _downsample(points, resolution * group), resolution * group
        points = points[-MAX_POINTS:]
        result = {
            "window": window,
            "resolution": resolution,
            "from": start,
            "to": now,
            "metrics": list(METRICS),
            "units": dict(UNITS),
            "t": [p["t"] for p in points],
            "count": [p["n"] for p in points],
            "covered": [round(p["cov"], 3) for p in points],
            "breaks": [p["brk"] for p in points],
            "series": {},
            "gaps": [dict(g) for g in self.gaps if g["end"] >= start],
            "incidents": [dict(i) for i in self.incidents if i["start"] <= now and _incident_tail(i) >= start],
        }
        if window == "5m":
            result["seq"] = [p["seq"] for p in points]
        for name in METRICS:
            accs = [p["m"][name] for p in points]
            result["series"][name] = {
                "min": [_round(name, a[0]) if a else None for a in accs],
                "max": [_round(name, a[1]) if a else None for a in accs],
                "mean": [_round(name, _acc_mean(a)) if a else None for a in accs],
                "last": [_round(name, a[2]) if a else None for a in accs],
            }
        return result

    # ---------------------------------------------------------- persistence form

    def to_state(self, saved_at):
        self._prune_incidents(saved_at)
        return {
            "format": FILE_FORMAT,
            "schemaVersion": SCHEMA_VERSION,
            "savedAt": saved_at,
            "bootId": self.boot_id,
            "clearToken": self.clear_token,
            "metrics": list(METRICS),
            "rings": {str(res): [_bucket_to_list(b) for b in ring.buckets] for res, ring in self.rings.items()},
            "gaps": [[g["start"], g["end"], g["reason"]] for g in self.gaps],
            "incidents": [dict(i) for i in self.incidents],
        }

    def compact(self):
        """Drop the oldest quarter of the largest store. False when nothing is left."""
        closed = [i for i in self.incidents if i["end"] is not None]
        stores = [self.rings[60].buckets, self.rings[10].buckets, self.gaps, closed]
        largest = max(stores, key=len)
        if not largest:
            return False
        if largest is closed:
            ids = {i["id"] for i in closed[:max(1, len(closed) // 4)]}
            self.incidents = [i for i in self.incidents if i["id"] not in ids]
        else:
            del largest[:max(1, len(largest) // 4)]
        return True

    def adopt(self, saved, now):
        """Take over saved history (a History from read_state, consumed) as the new writer.

        In each ring, saved buckets strictly before this process's first bucket
        are kept, then this process's own buckets, so the result is ordered and
        unique by construction; a saved tail sharing the first slot without
        overlapping it is merged in. The boundary is then checked against the
        saved data that was actually kept.
        """
        self._saved_boot = saved.boot_id
        known = {i["id"] for i in self.incidents}
        self.incidents = sorted([i for i in saved.incidents if i["id"] not in known] + self.incidents,
                                key=lambda i: i["start"])
        self._bound_incidents()
        if not self.rings[60].buckets:
            # Nothing sampled yet: the next ingest checks continuity against this.
            self.rings, self.gaps = saved.rings, saved.gaps
            self._prune(now)
            return
        live_first = min(r.buckets[0]["a"] for r in self.rings.values())  # our first sample
        reason, gap_start = self._boundary(saved, live_first, now)
        for res, ring in self.rings.items():
            first = ring.buckets[0]
            kept = [b for b in saved.rings[res].buckets
                    if b["t"] < first["t"] or (b["t"] == first["t"] and b["z"] < first["a"])]
            if kept and kept[-1]["t"] == first["t"]:
                ring.buckets[0] = _merge_buckets(first["t"], [kept.pop(), first])
            ring.buckets = kept + ring.buckets
        # Saved gaps reaching our first sample are replaced by the boundary gap.
        self.gaps = [g for g in saved.gaps if g["end"] < live_first] + self.gaps
        if reason:
            self.gaps.append({"start": gap_start, "end": live_first, "reason": reason})
            for ring in self.rings.values():
                # The bucket holding our first sample, even when a saved tail was merged into it.
                for bucket in ring.buckets:
                    if bucket["t"] + ring.resolution > live_first:
                        bucket["brk"] = True
                        break
            if self.raw and self.raw[0]["t"] == live_first:
                self.raw[0]["brk"] = True
        self.gaps.sort(key=lambda g: g["start"])
        self._prune(now)

    def _boundary(self, saved, live_first, now):
        """(gap reason or None, gap start) between saved history and our first sample."""
        saved_buckets = [b for ring in saved.rings.values() for b in ring.buckets]
        before = [b["z"] for b in saved_buckets if b["z"] < live_first]
        tail = max(before) if before else None
        newest = saved.newest()
        if newest is not None and newest > now:
            return "clock", tail if tail is not None else live_first  # saved under a later wall clock
        for gap in saved.gaps:
            if gap["start"] < live_first <= gap["end"]:
                return gap["reason"], gap["start"]  # the old writer was not sampling then either
        if any(b["a"] <= live_first <= b["z"] for b in saved_buckets):
            return None, None  # the old writer was still sampling when we started
        if tail is not None and live_first - tail > STALL_SECONDS:
            return self._resume_reason(), tail
        return None, None


def _sample_bucket(sample):
    bucket = _fill_bucket(_new_bucket(sample["t"], sample["t"]), sample["t"], sample["w"], sample["v"],
                          sample["brk"])
    bucket["seq"] = sample["seq"]
    return bucket


def _downsample(buckets, resolution):
    groups = []
    for bucket in buckets:
        start = math.floor(bucket["t"] / resolution) * resolution
        if groups and groups[-1][0] == start:
            groups[-1][1].append(bucket)
        else:
            groups.append((start, [bucket]))
    return [_merge_buckets(start, members) for start, members in groups]


# ------------------------------------------------------------ incident bounds

def sanitize_name(value):
    text = "".join(ch for ch in str(value) if ch.isprintable())
    return text[:MAX_NAME]


def sanitize_context(context):
    """Whitelisted, bounded app context for an incident."""
    if isinstance(context, dict) and context.get("status") == "expired":
        return {"status": "expired"}
    if not isinstance(context, dict) or context.get("status") != "observed":
        return {"status": "not-observed"}
    apps = []
    for app in context.get("apps") or []:
        if not isinstance(app, dict):
            continue
        kb = _num(app.get("kb"))
        entry = {"name": sanitize_name(app.get("name", "")), "kb": int(kb) if kb is not None else None}
        if app.get("partial") is True:
            entry["partial"] = True  # kb is the known subtotal of a partially readable group
        apps.append(entry)
        if len(apps) == MAX_INCIDENT_APPS:
            break
    return {"status": "observed", "at": _time(context.get("at")), "apps": apps}


# ------------------------------------------------------------ state file format

def _bucket_to_list(bucket):
    metrics = []
    for name in METRICS:
        acc = bucket["m"][name]
        metrics.append(None if acc is None else
                       [acc[0], acc[1], acc[2], acc[3], round(_acc_mean(acc), 3), round(acc[5], 3)])
    return [bucket["t"], bucket["a"], bucket["z"], bucket["n"], round(bucket["cov"], 3),
            1 if bucket["brk"] else 0, metrics]


def _bucket_from_list(item):
    if not isinstance(item, list) or len(item) != 7 or not isinstance(item[6], list) or len(item[6]) != len(METRICS):
        raise ValueError("bucket shape")
    t, a, z = (_time(v) for v in item[:3])
    n, cov = (_num(v, 0, MAX_WEIGHT) for v in item[3:5])
    if None in (t, a, z, n, cov) or not a <= z:
        raise ValueError("bucket fields")
    bucket = {"t": t, "a": a, "z": z, "n": int(n), "cov": float(cov), "brk": bool(item[5]), "m": {}}
    for name, acc in zip(METRICS, item[6]):
        if acc is None:
            bucket["m"][name] = None
            continue
        if not isinstance(acc, list) or len(acc) != 6:
            raise ValueError("metric shape")
        lo, hi, last, mean = (_num(v) for v in acc[:3] + acc[4:5])
        count, weight = _num(acc[3], 1, MAX_WEIGHT), _num(acc[5], 0, MAX_WEIGHT)
        if None in (lo, hi, last, mean, count, weight) or not lo <= last <= hi or not lo - 0.01 <= mean <= hi + 0.01:
            raise ValueError("metric fields")
        bucket["m"][name] = [lo, hi, last, int(count), mean * weight, weight]
    return bucket


def _incident_from_json(item):
    if not isinstance(item, dict):
        raise ValueError("incident shape")
    start, end = _time(item.get("start")), _time(item.get("end"))
    if start is None or (item.get("end") is not None and (end is None or end < start)):
        raise ValueError("incident time")
    if item.get("severity") not in SEVERITIES or item.get("reason") not in INCIDENT_REASONS:
        raise ValueError("incident enum")
    if _short_text(item.get("id")) is None:
        raise ValueError("incident id")
    measurement = item.get("measurement") if isinstance(item.get("measurement"), dict) else {}
    result = {
        "id": item["id"],
        "start": start,
        "end": end,
        "severity": item["severity"],
        "reason": item["reason"],
        "summarySeq": _num(item.get("summarySeq")),
        "measurement": {name: _num(measurement.get(name)) for name in METRICS},
        "context": sanitize_context(item.get("context")),
    }

    result["lastAt"] = max(start, _time(item.get("lastAt")) or start)
    if item.get("measurementStatus") == "expired":
        result["measurementStatus"] = "expired"
        result["measurement"] = {name: None for name in METRICS}
    result["status"] = item.get("status") if item.get("status") in ("active", "recovered", "interrupted") else "active" if end is None else "recovered"
    threshold = item.get("thresholds")
    if isinstance(threshold, dict) and result.get("measurementStatus") != "expired":
        result["thresholds"] = {k: _num(threshold.get(k), 1, 100) for k in
                                ("warnPercent", "criticalPercent", "warnPressure", "criticalPressure")}
    upgrade = item.get("upgrade")
    if isinstance(upgrade, dict) and _time(upgrade.get("at")) is not None:
        m = upgrade.get("measurement") if isinstance(upgrade.get("measurement"), dict) else {}
        result["upgrade"] = {"at": upgrade["at"], "measurement": {k: _num(m.get(k)) for k in METRICS}}
        if upgrade.get("measurementStatus") == "expired":
            result["upgrade"]["measurementStatus"] = "expired"
            result["upgrade"]["measurement"] = {name: None for name in METRICS}
    return result


def parse_state(data):
    """A History from a decoded state file. Raises ValueError ("corrupt"/"incompatible")."""
    if not isinstance(data, dict) or data.get("format") != FILE_FORMAT:
        raise ValueError("corrupt")
    if data.get("schemaVersion") != SCHEMA_VERSION or data.get("metrics") != list(METRICS):
        raise ValueError("incompatible")
    try:
        history = History(_short_text(data.get("bootId")) or "")
        history.clear_token = _short_text(data.get("clearToken")) or ""
        for res, _ in RINGS:
            buckets = [_bucket_from_list(item) for item in data["rings"][str(res)]]
            if any(b["t"] >= c["t"] for b, c in zip(buckets, buckets[1:])):
                raise ValueError("bucket order")
            history.rings[res].buckets = buckets
        for item in data["gaps"][-MAX_GAPS:]:
            start, end = _time(item[0]), _time(item[1])
            if start is None or end is None or item[2] not in GAP_REASONS:
                raise ValueError("gap")
            history.gaps.append({"start": start, "end": end, "reason": item[2]})
        history.incidents = [_incident_from_json(item) for item in data["incidents"][-MAX_INCIDENTS:]]
        # An unfinished receipt belongs to the previous producer, never this one.
        for incident in history.incidents:
            if incident["end"] is None:
                incident["end"] = incident.get("lastAt", incident["start"])
                incident["status"] = "interrupted"
        return history
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError("corrupt") from exc


def encode_state(history, saved_at, max_bytes=MAX_FILE_BYTES):
    """Serialized state within max_bytes, compacting the oldest records if needed."""
    while True:
        data = json.dumps(history.to_state(saved_at), separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) <= max_bytes or not history.compact():
            return data


def read_state(path, max_bytes=MAX_FILE_BYTES):
    """(History or None, status). Status: ok, empty, oversized, corrupt, incompatible, unreadable."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return None, "empty"
    except OSError:
        return None, "unreadable"
    if len(data) > max_bytes:
        return None, "oversized"
    try:
        return parse_state(json.loads(data)), "ok"
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        return None, "incompatible" if str(exc) == "incompatible" else "corrupt"


def write_private(directory, path, data):
    """Atomically replace path with data (mode 0600) inside directory."""
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


# ------------------------------------------------------------ environment

def state_dir(env=None):
    """XDG_STATE_HOME/raman, normally ~/.local/state/raman."""
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME", "")
    if not os.path.isabs(base):
        base = os.path.join(env.get("HOME") or os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "raman")


def boot_clock():
    """Seconds since boot including suspend, or None where unsupported."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        return None
    try:
        return time.clock_gettime(clock)
    except OSError:
        return None


# ------------------------------------------------------------ single writer

class Persistence:
    """Owns history.json for one probe.

    Exactly one probe per user writes: the holder of an flock on history.lock.
    Other probes (one per monitor, or a new probe while the old one finishes
    after a shell reload) keep session-only history and retry the lock every
    tick; the winner adopts the saved file. A clear made by any probe is
    published through cleared.json; every probe applies it, and the file
    records which clear it reflects.

    tick, clear and flush never raise: failures are reported through info().
    """

    def __init__(self, directory, max_bytes=MAX_FILE_BYTES, save_interval=SAVE_INTERVAL):
        self.directory = directory
        self.path = os.path.join(directory, "history.json")
        self.lock_path = os.path.join(directory, "history.lock")
        self.marker_path = os.path.join(directory, "cleared.json")
        self.max_bytes = max_bytes
        self.save_interval = save_interval
        self.lock_fd = None
        self.load_status = "none"
        self.save_status = "pending"
        self.error = ""
        self.last_saved_at = None
        # Monotonic deadline: the next save when writing, the next takeover attempt otherwise.
        self._next = 0.0
        self._clear_pending = False  # durable marker observed, disk erasure still owed

    @property
    def owner(self):
        return self.lock_fd is not None

    def info(self):
        return {"owner": self.owner, "load": self.load_status, "save": self.save_status,
                "error": self.error, "lastSavedAt": self.last_saved_at}

    def _fail(self, exc):
        self.save_status = "error"
        self.error = (getattr(exc, "strerror", None) or str(exc) or type(exc).__name__)[:200]

    def _ensure_dir(self):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        st = os.stat(self.directory)
        if st.st_uid == os.getuid() and st.st_mode & 0o077:
            os.chmod(self.directory, 0o700)

    def _try_lock(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.lock_fd = fd
        return True

    def release(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def _marker(self):
        """(token, clearedAt) of the latest clear, or None."""
        try:
            with open(self.marker_path, "rb") as handle:
                data = json.loads(handle.read(MAX_MARKER_BYTES))
            token, cleared_at = _short_text(data["token"]), _time(data["clearedAt"])
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None
        return (token, cleared_at) if token and cleared_at is not None else None

    def _follow_clears(self, history):
        """Apply a clear published by any probe. True when one was applied."""
        marker = self._marker()
        if marker is None or marker[0] == history.clear_token:
            return False
        history.forget_before(marker[1])
        history.clear_token = marker[0]
        self._clear_pending = True
        return True

    def follow_clears(self, history):
        """Observe only erase markers, including when collection/saving is off."""
        try:
            return self._follow_clears(history)
        except Exception as exc:
            self._fail(exc)
            return False

    def tick(self, history, wall, mono):
        """Per summary tick: follow clears, take over if free, save at most once per interval."""
        try:
            self._follow_clears(history)
            if not self.owner and mono >= self._next:
                self._take_over(history, wall, mono)
            if self.owner and (self._clear_pending or mono >= self._next):
                self._save(history, wall, mono)
        except Exception as exc:  # persistence must never stop live telemetry
            self._fail(exc)

    def _take_over(self, history, wall, mono):
        self._ensure_dir()
        if not self._try_lock():
            return
        self._next = mono + self.save_interval
        try:
            saved, self.load_status = read_state(self.path, self.max_bytes)
            if self.load_status == "unreadable":
                raise OSError("cannot read " + self.path)
            if saved is not None:
                marker = self._marker()
                if marker is not None and saved.clear_token != marker[0]:
                    saved.forget_before(marker[1])  # the old writer exited before applying it
                history.adopt(saved, wall)
        except Exception:
            self.release()  # never save over a file we could not take in; retry next interval
            raise

    def _save(self, history, wall, mono):
        self._next = mono + self.save_interval
        self._follow_clears(history)  # a clear published since the last tick, e.g. before an exit save
        write_private(self.directory, self.path, encode_state(history, wall, self.max_bytes))
        self._clear_pending = False  # reset only after atomic replacement succeeds
        self.save_status, self.error, self.last_saved_at = "saved", "", wall

    def checkpoint(self, history, wall, mono):
        """Immediate event acknowledgement, independent of the minute deadline."""
        if not self.owner:
            return False
        try:
            self._save(history, wall, mono)
            return True
        except Exception as exc:
            self._fail(exc)
            return False

    def clear(self, history, wall, mono):
        """Clear history in every probe. True once the clear is recorded on disk."""
        history.clear()
        try:
            self._ensure_dir()
            token = os.urandom(8).hex()
            write_private(self.directory, self.marker_path,
                          json.dumps({"token": token, "clearedAt": wall}).encode("utf-8"))
            history.clear_token = token
            self._clear_pending = True
            if not self.owner:
                self._try_lock()  # clear an idle file immediately, without loading it
            if self.owner:
                self._save(history, wall, mono)
            return True  # otherwise the writer applies the marker on its next tick
        except Exception as exc:
            self._fail(exc)
            return False

    def flush(self, history, wall, mono):
        """Final save on exit, when this probe is the writer."""
        if self.owner:
            try:
                self._save(history, wall, mono)
            except Exception as exc:
                self._fail(exc)


class DispatchLease:
    """One toast producer, including session-only/fallback runtimes.

    A private policy file contains only cooldown timestamps and boot identity,
    never readings or app names. A same-boot lock stamp suppresses launch-time
    episodes; each measured dimension must recover before re-arming.
    File descriptors are kept until the worker exits.
    """

    def __init__(self, directory, boot_id):
        self.directory, self.boot_id = directory, boot_id
        self.path = os.path.join(directory, "dispatch.json")
        self.lock_path = os.path.join(directory, "dispatch.lock")
        self.fd = None
        self.epoch = ""
        self.blocked = False
        self.notices = {"warn": None, "critical": None}
        self.error = ""
        self._next = 0

    @property
    def owner(self):
        return self.fd is not None

    def tick(self, mono):
        if self.owner or mono < self._next:
            return
        self._next = mono + NOMINAL_INTERVAL
        try:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
            st = os.stat(self.directory)
            if st.st_uid == os.getuid() and st.st_mode & 0o077:
                os.chmod(self.directory, 0o700)
            existed = os.path.exists(self.lock_path)
            fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0), 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return
            # The lock inode must remain stable for flock. Stamp boot identity
            # in place; an empty/corrupt old stamp remains conservative.
            prior_boot = None
            try:
                prior_boot = _short_text(json.loads(os.read(fd, MAX_MARKER_BYTES + 1)).get("bootId"))
            except (ValueError, AttributeError, RecursionError):
                pass
            self.fd, self.epoch = fd, os.urandom(8).hex()
            self.blocked = existed and (not prior_boot or not self.boot_id or prior_boot == self.boot_id)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps({"bootId": self.boot_id}).encode("utf-8"))
            os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
            try:
                with open(self.path, "rb") as handle:
                    data = json.loads(handle.read(MAX_MARKER_BYTES + 1))
                if data.get("bootId") == self.boot_id:
                    self.notices = {k: _num(data.get(k), 0, MAX_TIME) for k in self.notices}
            except (OSError, ValueError, AttributeError, RecursionError):
                pass
            self.error = ""
        except OSError as exc:
            self.error = (exc.strerror or str(exc))[:200]

    def claim(self, severity):
        """Reserve before toast; at-most-once wins over retrying lost delivery."""
        now = boot_clock()
        self.notices[severity] = now if now is not None else time.monotonic()
        try:
            write_private(self.directory, self.path,
                          json.dumps(dict(self.notices, bootId=self.boot_id)).encode("utf-8"))
            self.error = ""
            return True
        except OSError as exc:
            self.error = (exc.strerror or str(exc))[:200]
            return False

    def info(self, mono):
        boot = boot_clock()
        if boot is None:
            boot = time.monotonic()
        elapsed = {k: None if v is None or boot is None or boot < v else boot - v for k, v in self.notices.items()}
        return {"owner": self.owner, "epoch": self.epoch, "blocked": self.blocked,
                "noticeElapsed": elapsed, "error": self.error}

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
