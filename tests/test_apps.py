"""A1: recent CPU accounting, complete app inventory and apps.query.

Fixture tests build a fake /proc tree in a temporary directory and run on any
platform. LiveProbeAppsTests start the real probe on Linux with test-owned
children only, and point XDG_STATE_HOME at a test-owned directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raman_apps as apps  # noqa: E402
import raman_history  # noqa: E402
import raman_probe as probe  # noqa: E402
from test_probe import answer, read_until, send, start_probe  # noqa: E402

UID = os.getuid()
APP = "/user.slice/user-1000.slice/user@1000.service/app.slice/"
TERM = APP + "app-graphical.slice/app-Hyprland-ghostty-1.scope"
CHAT = APP + "app-Hyprland-chat-2.scope"
SERVICE = "/user.slice/user-1000.slice/user@1000.service/session.slice/example.service"
HZ = 100


class FakeProc:
    """A fake procfs tree: stat, smaps_rollup, cgroup, cmdline and status."""

    def __init__(self, root):
        self.root = root
        self.cpu_online = os.path.join(root, "cpu-online")
        self.set_online("0-3")

    def set_online(self, text):
        with open(self.cpu_online, "w") as handle:
            handle.write(text + "\n")

    def add(self, pid, comm="app", ticks=0, ppid=1, start=None, pss=1000, swap=0, cgroup=SERVICE,
            argv=None, state="S", flags=0, utime=None):
        directory = os.path.join(self.root, str(pid))
        os.makedirs(directory, exist_ok=True)
        start = pid * 10 if start is None else start
        utime = ticks if utime is None else utime
        # Children's cumulative counters are deliberately huge: they must never count.
        rest = [state, ppid, pid, pid, 0, -1, flags, 0, 0, 0, 0, utime, ticks - utime, 999999, 999999,
                20, 0, 1, 0, start, 0, 0]
        with open(os.path.join(directory, "stat"), "w") as handle:
            handle.write("%d (%s) %s\n" % (pid, comm, " ".join(str(v) for v in rest)))
        smaps = os.path.join(directory, "smaps_rollup")
        if os.path.isdir(smaps):
            os.rmdir(smaps)
        elif os.path.exists(smaps):
            os.unlink(smaps)
        if pss == "unreadable":
            os.mkdir(smaps)  # opening a directory fails: memory unavailable
        elif pss == "empty":
            open(smaps, "w").close()
        elif pss is not None:
            with open(smaps, "w") as handle:
                handle.write("Rss: %d kB\nPss: %d kB\nSwapPss: %d kB\n" % (pss * 2, pss, swap))
        with open(os.path.join(directory, "cgroup"), "w") as handle:
            handle.write("0::%s\n" % cgroup)
        with open(os.path.join(directory, "cmdline"), "w") as handle:
            handle.write("\0".join(argv if argv is not None else [comm]))

    def remove(self, pid):
        directory = os.path.join(self.root, str(pid))
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            os.rmdir(path) if os.path.isdir(path) else os.unlink(path)
        os.rmdir(directory)


class Clock:
    """Deterministic monotonic/boot clocks for the scanner."""

    def __init__(self):
        self.mono = 1000.0
        self.boot = 5000.0

    def advance(self, seconds, suspended=0.0):
        self.mono += seconds
        self.boot += seconds + suspended


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.proc = FakeProc(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock()
        self.tracker = apps.CpuTracker(HZ)

    def scan(self, advance=2.5, suspended=0.0):
        """One scan of the fake tree; returns ({id: group}, info, cpu)."""
        if advance:
            self.clock.advance(advance, suspended)
        with unittest.mock.patch.object(probe.time, "monotonic", return_value=self.clock.mono), \
                unittest.mock.patch.object(probe.raman_history, "boot_clock", return_value=self.clock.boot):
            groups, info, cpu = probe.scan(self.tracker, self.proc.root, self.proc.cpu_online)
        return {g["id"]: g for g in groups}, info, cpu


class CpuTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = apps.CpuTracker(HZ)

    def scan(self, mono, readings, boot=None, topology="0-3", online=4):
        reason = self.tracker.begin(mono, mono if boot is None else boot, topology, online)
        result = {pid: self.tracker.sample(pid, start, ticks, mono) for pid, start, ticks in readings}
        self.tracker.end()
        return reason, result

    def test_one_busy_core_multithread_and_idle_zero(self):
        reason, first = self.scan(10.0, [(1, 100, 0), (2, 200, 0), (3, 300, 50)])
        self.assertEqual(reason, "start")
        self.assertEqual(first[1], (None, "warming-up"), "a first reading has no baseline")
        _, second = self.scan(12.5, [(1, 100, 250), (2, 200, 500), (3, 300, 50)])
        self.assertAlmostEqual(second[1][0], 100.0, msg="250 ticks in 2.5 s at 100 Hz is one core")
        self.assertAlmostEqual(second[2][0], 200.0, msg="two busy threads are two cores")
        self.assertEqual(second[3], (0.0, "available"), "an idle process is a measured zero, not null")

    def test_reused_pid_rollback_and_anomaly_never_spike(self):
        self.scan(10.0, [(1, 100, 1000), (2, 200, 1000), (3, 300, 0)])
        _, result = self.scan(12.5, [(1, 999, 900000), (2, 200, 10), (3, 300, 900000)])
        self.assertEqual(result[1], (None, "warming-up"), "same PID, new start time: a new process")
        self.assertEqual(result[2], (None, "warming-up"), "counter rollback rebaselines")
        self.assertEqual(result[3], (None, "warming-up"), "more than the machine can run is an anomaly")
        self.assertEqual(self.tracker.anomalies, 2)
        _, after = self.scan(15.0, [(1, 999, 900250), (2, 200, 10), (3, 300, 900000)])
        self.assertAlmostEqual(after[1][0], 100.0)
        self.assertEqual(after[2], (0.0, "available"))

    def test_exited_process_loses_its_baseline(self):
        self.scan(10.0, [(1, 100, 0)])
        self.scan(12.5, [])
        _, back = self.scan(15.0, [(1, 100, 500)])
        self.assertEqual(back[1], (None, "warming-up"))

    def test_resets_on_gap_suspend_topology_and_reopen(self):
        self.scan(10.0, [(1, 100, 0)])
        self.assertEqual(self.scan(20.0, [(1, 100, 100)])[0], "gap")
        self.scan(22.5, [(1, 100, 100)])
        self.assertEqual(self.scan(25.0, [(1, 100, 100)], boot=85.0)[0], "suspend")
        self.scan(27.5, [(1, 100, 100)], boot=87.5)
        reason, result = self.scan(30.0, [(1, 100, 350)], boot=90.0, topology="0-1", online=2)
        self.assertEqual((reason, result[1]), ("topology", (None, "warming-up")))
        self.tracker.reset()  # panel closed
        self.assertEqual(self.scan(32.5, [(1, 100, 600)], boot=92.5, topology="0-1", online=2)[0], "start")

    def test_fast_rescan_reuses_the_last_full_interval(self):
        self.scan(10.0, [(1, 100, 0)])
        self.scan(12.5, [(1, 100, 250)])
        _, quick = self.scan(12.8, [(1, 100, 290)])
        self.assertAlmostEqual(quick[1][0], 100.0, msg="0.3 s of ticks would be too coarse")
        _, later = self.scan(15.0, [(1, 100, 375)])
        self.assertAlmostEqual(later[1][0], 50.0, msg="the baseline stayed at 12.5 s")

    def test_cpu_list(self):
        self.assertEqual(apps.parse_cpu_list("0-3,6,8-9\n"), 7)
        self.assertEqual(apps.parse_cpu_list("0"), 1)
        self.assertIsNone(apps.parse_cpu_list(""))
        self.assertIsNone(apps.parse_cpu_list("3-1"))
        self.assertIsNone(apps.parse_cpu_list("a-b"))


class ScanFixtureTests(FixtureTest):
    def test_stat_parser_uses_last_parenthesis_and_own_counters(self):
        self.proc.add(10, comm="my (weird) app) x", ticks=0, utime=0, pss=4000)
        self.scan()
        self.proc.add(10, comm="my (weird) app) x", ticks=500, utime=300, pss=4000)
        groups, _, cpu = self.scan()
        group = groups["pid:10"]
        self.assertEqual(group["name"], "My (weird) app) x")
        self.assertAlmostEqual(group["cpuCorePercent"], 200.0, msg="utime + stime, not cutime/cstime")
        self.assertAlmostEqual(group["cpuMachinePercent"], 50.0, msg="four online CPUs")
        self.assertEqual((cpu["onlineCpus"], cpu["clockTicks"], cpu["status"]), (4, HZ, "available"))

    def test_kernel_threads_zombies_and_exited_processes_are_skipped(self):
        self.proc.add(10, comm="kworker", flags=probe.PF_KTHREAD, pss="empty")
        self.proc.add(11, comm="dead", state="Z", pss="empty")
        self.proc.add(12, comm="exiting", pss=None)  # smaps_rollup vanished
        self.proc.add(13, comm="noaddr", pss="empty")  # address space already gone
        self.proc.add(14, comm="alive")
        groups, info, _ = self.scan()
        self.assertEqual(list(groups), ["pid:14"])
        self.assertEqual(info["processes"], 1)

    def test_missing_pss_keeps_cpu_and_reports_partial_memory(self):
        self.proc.add(20, comm="chat", ticks=0, pss=50_000, swap=1000, cgroup=CHAT)
        self.proc.add(21, comm="helper", ticks=0, pss="unreadable", cgroup=CHAT, ppid=20)
        self.proc.add(30, comm="agent", ticks=0, pss="unreadable")
        self.scan()
        self.proc.add(20, comm="chat", ticks=25, pss=50_000, swap=1000, cgroup=CHAT)
        self.proc.add(21, comm="helper", ticks=250, pss="unreadable", cgroup=CHAT, ppid=20)
        self.proc.add(30, comm="agent", ticks=125, pss="unreadable")
        groups, info, _ = self.scan()
        chat = groups["app-Hyprland-chat-2.scope"]
        self.assertEqual(chat["count"], 2, "the unreadable member still belongs to its app")
        self.assertEqual((chat["pss"], chat["swap"]), (50_000, 1000), "known subtotal only")
        self.assertEqual(chat["memoryStatus"], "partial")
        self.assertEqual(chat["memoryCoverage"], {"measured": 1, "members": 2})
        self.assertAlmostEqual(chat["cpuCorePercent"], 110.0)
        self.assertEqual(chat["cpuStatus"], "available")
        agent = groups["pid:30"]
        self.assertEqual((agent["pss"], agent["swap"], agent["memoryStatus"]), (None, None, "unavailable"),
                         "missing memory is null, never zero")
        self.assertAlmostEqual(agent["cpuCorePercent"], 50.0)
        self.assertEqual(info["memoryUnavailable"], 2)

    def test_permission_denied_memory_is_unavailable(self):
        self.proc.add(40, comm="private", ticks=10)
        real_open = open

        def guarded(path, *args, **kwargs):
            if str(path).endswith("/40/smaps_rollup"):
                raise PermissionError(13, "Permission denied")
            return real_open(path, *args, **kwargs)
        with unittest.mock.patch("builtins.open", side_effect=guarded):
            groups, _, _ = self.scan()
        self.assertEqual(groups["pid:40"]["memoryStatus"], "unavailable")
        self.assertEqual(groups["pid:40"]["cpuStatus"], "warming-up")

    def test_non_dumpable_owned_process_is_kept_and_setuid_is_not(self):
        self.proc.add(50, comm="ssh")
        self.proc.add(51, comm="sudo")
        for pid, uids in ((50, "1000\t1000\t1000\t1000"), (51, "1000\t0\t0\t0")):
            with open(os.path.join(self.proc.root, str(pid), "status"), "w") as handle:
                handle.write("Name:\tx\nUid:\t%s\n" % uids)
        real_stat = os.stat

        def root_owned(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if os.path.basename(str(path)) in ("50", "51"):
                return os.stat_result((result.st_mode, 0, 0, 0, 0, 0, 0, 0, 0, 0))
            return result
        with unittest.mock.patch.object(probe.os, "stat", side_effect=root_owned):
            procs, _ = probe.read_processes(self.proc.root, uid=1000)
        self.assertEqual([p["pid"] for p in procs], [50],
                         "a non-dumpable process of the user is CPU-visible; a setuid program is not ours")

    def test_joins_exits_and_reuse_in_a_terminal_session(self):
        self.proc.add(100, comm="ghostty", ticks=0, cgroup=TERM, pss=30_000)
        self.proc.add(101, comm="bash", ticks=0, ppid=100, cgroup=TERM, pss=4_000)
        self.proc.add(102, comm="node", ticks=0, ppid=101, cgroup=TERM, pss=300_000, argv=["node", "/usr/bin/claude"])
        self.scan()
        self.proc.add(102, comm="node", ticks=250, ppid=101, cgroup=TERM, pss=300_000, argv=["node", "/usr/bin/claude"])
        self.proc.add(103, comm="cargo", ticks=40000, ppid=102, cgroup=TERM, pss=9_000)  # joins mid-interval
        groups, _, _ = self.scan()
        session = groups["app-Hyprland-ghostty-1.scope/101"]
        self.assertEqual((session["name"], session["host"], session["kind"]), ("Claude", "Ghostty", "session"))
        self.assertEqual(session["cpuStatus"], "partial", "the new child is still warming up")
        self.assertAlmostEqual(session["cpuCorePercent"], 100.0, msg="no spike from the joining child's lifetime ticks")
        self.assertEqual(session["cpuCoverage"], {"measured": 2, "members": 3})
        joined = session["generation"]
        self.proc.add(102, comm="node", ticks=500, ppid=101, cgroup=TERM, pss=300_000, argv=["node", "/usr/bin/claude"])
        self.proc.add(103, comm="cargo", ticks=40250, ppid=102, cgroup=TERM, pss=9_000)
        groups, _, _ = self.scan()
        self.assertAlmostEqual(groups["app-Hyprland-ghostty-1.scope/101"]["cpuCorePercent"], 200.0)
        self.assertEqual(groups["app-Hyprland-ghostty-1.scope/101"]["generation"], joined, "same membership")
        self.proc.remove(103)  # exits
        self.proc.add(102, comm="node", ticks=750, ppid=101, cgroup=TERM, pss=300_000, argv=["node", "/usr/bin/claude"])
        groups, _, _ = self.scan()
        session = groups["app-Hyprland-ghostty-1.scope/101"]
        self.assertEqual((session["count"], session["cpuStatus"]), (2, "available"))
        self.assertAlmostEqual(session["cpuCorePercent"], 100.0)
        self.assertNotEqual(session["generation"], joined, "membership changed")
        self.proc.add(103, comm="cargo", ticks=90000, ppid=102, cgroup=TERM, pss=9_000, start=77777)  # PID reused
        groups, _, _ = self.scan()
        session = groups["app-Hyprland-ghostty-1.scope/101"]
        self.assertEqual(session["cpuStatus"], "partial")
        self.assertEqual(session["cpuCorePercent"], 0.0, "a reused PID never inherits the old baseline")
        terminal = groups["app-Hyprland-ghostty-1.scope"]
        self.assertEqual([p for p, _ in terminal["pids"]], [100], "grouping is unchanged")

    def test_counter_rollback_and_topology_reset_in_a_scan(self):
        self.proc.add(60, ticks=1000)
        self.scan()
        self.proc.add(60, ticks=900)
        groups, _, cpu = self.scan()
        self.assertEqual((groups["pid:60"]["cpuCorePercent"], groups["pid:60"]["cpuStatus"]), (None, "warming-up"))
        self.assertEqual(cpu["anomalies"], 1)
        self.proc.add(60, ticks=1150)
        groups, _, _ = self.scan()
        self.assertAlmostEqual(groups["pid:60"]["cpuCorePercent"], 100.0)
        self.proc.set_online("0-7")
        groups, _, cpu = self.scan()
        self.assertEqual((cpu["status"], cpu["reset"], cpu["onlineCpus"]), ("warming-up", "topology", 8))
        self.assertIsNone(groups["pid:60"]["cpuCorePercent"])
        groups, _, cpu = self.scan(advance=2.5, suspended=30)
        self.assertEqual(cpu["reset"], "suspend")
        groups, _, cpu = self.scan(advance=12)
        self.assertEqual(cpu["reset"], "gap")

    def test_process_ceiling_is_disclosed(self):
        for pid in range(200, 205):
            self.proc.add(pid)
        with unittest.mock.patch.object(apps, "MAX_SCAN_PROCESSES", 3):
            groups, info, cpu = self.scan()
            snapshot = apps.Inventory().publish(list(groups.values()), 1.0, 1.0, info, cpu)
        self.assertEqual((info["processes"], info["incompleteReason"]), (3, "process-limit"))
        self.assertFalse(snapshot["inventory"]["complete"], "never advertised as a complete inventory")

    def test_low_memory_cpu_hog_is_outside_the_memory_preview_but_found_by_query(self):
        for index in range(14):
            self.proc.add(300 + index, comm="big%d" % index, pss=100_000 + index, ticks=0)
        self.proc.add(400, comm="spin", pss=300, ticks=0)
        self.scan()
        self.proc.add(400, comm="spin", pss=300, ticks=250)
        groups, info, cpu = self.scan()
        ordered = sorted(groups.values(), key=lambda g: (-(probe.footprint(g) or 0), g["id"]))
        preview = [g for g in ordered if (probe.footprint(g) or 0) >= 1024][:probe.MAX_APPS]
        self.assertNotIn("pid:400", [g["id"] for g in preview], "old top-12 / 1 MiB preview hides it")
        snapshot = apps.Inventory().publish(list(groups.values()), 1.0, 1.0, info, cpu)
        total, rows = apps.run_query(snapshot, "", "cpu", 0, 50)
        self.assertEqual((total, rows[0]["id"]), (15, "pid:400"))
        self.assertAlmostEqual(rows[0]["cpuCorePercent"], 100.0)
        self.assertEqual(apps.run_query(snapshot, "SPIN", "memory", 0, 50)[1][0]["id"], "pid:400")
        self.assertEqual(apps.run_query(snapshot, "400", "memory", 0, 50)[1][0]["id"], "pid:400", "PID search")

    def test_untrusted_names_are_printable_and_bounded(self):
        self.proc.add(70, comm="a\x07b", argv=["a\x07b"])
        self.proc.add(71, comm="x" * 15, argv=["/bin/" + "x" * 400])
        groups, _, _ = self.scan()
        self.assertEqual(groups["pid:70"]["name"], "Ab")
        self.assertEqual(len(groups["pid:71"]["name"]), apps.MAX_NAME)


def row(ident, pss=None, cpu=None, name=None, pids=()):
    return {"id": ident, "kind": "process", "name": name or ident, "host": "", "count": 1, "protected": False,
            "pss": pss, "swap": 0 if pss is not None else None,
            "memoryStatus": "available" if pss is not None else "unavailable",
            "memoryCoverage": {"measured": int(pss is not None), "members": 1},
            "cpuCorePercent": cpu, "cpuStatus": "available" if cpu is not None else "warming-up",
            "cpuCoverage": {"measured": int(cpu is not None), "members": 1}, "generation": "g",
            "pids": [[pid, 1] for pid in pids]}


INFO = {"processes": 1, "incompleteReason": None, "memoryUnavailable": 0}
CPU = {"status": "available", "reset": None, "onlineCpus": 4, "clockTicks": HZ, "anomalies": 0}


class QueryTests(unittest.TestCase):
    def test_pages_ties_and_nulls(self):
        rows = [row("g%03d" % i, pss=1000, cpu=5.0) for i in range(120)] + [row("z-null")]
        snapshot = apps.Inventory().publish(list(reversed(rows)), 10.0, 1.0, INFO, CPU)
        total, page = apps.run_query(snapshot, "", "memory", 0, 50)
        self.assertEqual(total, 121)
        self.assertEqual([r["id"] for r in page][:3], ["g000", "g001", "g002"], "ties break by stable ID")
        _, last = apps.run_query(snapshot, "", "cpu", 100, 50)
        self.assertEqual(last[-1]["id"], "z-null", "unavailable readings sort last, never as zero")
        self.assertIsNone(last[-1]["cpuMachinePercent"])
        seen = []
        for offset in range(0, 121, 50):
            seen += [r["id"] for r in apps.run_query(snapshot, "", "cpu", offset, 50)[1]]
        self.assertEqual(len(seen), len(set(seen)), "paging one snapshot never duplicates")
        self.assertEqual(len(seen), 121, "or skips")
        self.assertAlmostEqual(page[0]["cpuMachinePercent"], 1.25)
        self.assertEqual(page[0]["sampledAt"], 10.0)

    def test_validation(self):
        bad = [({"query": "q" * 257}, "bad-query"), ({"query": 5}, "bad-query"), ({"sort": "vram"}, "bad-sort"),
               ({"limit": 51}, "bad-page"), ({"limit": 0}, "bad-page"), ({"offset": -1}, "bad-page"),
               ({"offset": True}, "bad-page"), ({"limit": 2.5}, "bad-page"), ({"snapshot": 0}, "bad-request"),
               ({"generation": -1}, "bad-request")]
        for fields, code in bad:
            with self.assertRaises(apps.QueryError, msg=fields) as caught:
                apps.parse_query(dict({"command": "apps.query"}, **fields))
            self.assertEqual(caught.exception.code, code, fields)
        self.assertEqual(apps.parse_query({"query": "q" * 256}), ("q" * 256, "memory", 0, 50, None, None))
        with self.assertRaises(apps.QueryError) as caught:
            apps.parse_query({"generation": 7, "offset": 4097})
        self.assertEqual((caught.exception.code, caught.exception.echo), ("bad-page", {"generation": 7}))
        with self.assertRaises(apps.QueryError) as caught:
            apps.parse_query({"generation": "7", "offset": 4097})
        self.assertEqual((caught.exception.code, caught.exception.echo), ("bad-request", {}),
                         "an invalid generation is reported first and never echoed")

    def test_unicode_digits_are_literal_name_search(self):
        rows = [row("sup", pss=10, name="Build²", pids=[2]), row("circled", pss=10, name="step②", pids=[2]),
                row("arabic", pss=10, name="app٣", pids=[3]), row("wide", pss=10, name="tab１２", pids=[12]),
                row("pid2", pss=10, name="plain", pids=[2])]
        snapshot = apps.Inventory().publish(rows, 1.0, 1.0, INFO, CPU)
        for needle, expected in (("²", ["sup"]), ("②", ["circled"]), ("٣", ["arabic"]), ("１２", ["wide"])):
            total, found = apps.run_query(snapshot, needle, "memory", 0, 50)
            self.assertEqual((total, [r["id"] for r in found]), (len(expected), expected),
                             "%r is name text, never a PID" % needle)
        self.assertEqual([r["id"] for r in apps.run_query(snapshot, "2", "memory", 0, 50)[1]],
                         ["circled", "pid2", "sup"], "ASCII digits still match member PIDs exactly")
        self.assertEqual(apps.run_query(snapshot, "1" * 11, "memory", 0, 50)[0], 0, "over 10 digits is never a PID")

    def test_group_ceiling_keeps_both_rankings(self):
        rows = [row("m%d" % i, pss=10_000 - i, cpu=0.0) for i in range(6)]
        rows += [row("c%d" % i, pss=1, cpu=90.0 - i) for i in range(6)]
        with unittest.mock.patch.object(apps, "MAX_GROUPS", 4):
            snapshot = apps.Inventory().publish(rows, 1.0, 1.0, INFO, CPU)
        kept = sorted(r["id"] for r, _, _ in snapshot["rows"])
        self.assertEqual(kept, ["c0", "c1", "m0", "m1"])
        self.assertEqual((snapshot["inventory"]["complete"], snapshot["inventory"]["incompleteReason"]),
                         (False, "group-limit"))

    def test_reply_line_is_bounded(self):
        wide = "中" * apps.MAX_NAME  # six JSON bytes per character
        rows = [dict(row("r%02d" % i, pss=1000, name=wide), host=wide) for i in range(50)]
        snapshot = apps.Inventory().publish(rows, 1.0, 1.0, INFO, CPU)
        reply = apps.page_reply({"type": "apps-page"}, snapshot, "", "memory", 0, 50)
        self.assertLessEqual(len(json.dumps(reply, separators=(",", ":"))) + 1, apps.MAX_REPLY_BYTES)
        self.assertLess(len(reply["rows"]), 50)
        self.assertEqual(reply["nextOffset"], len(reply["rows"]), "the next page starts after the rows supplied")


class HandlerTests(unittest.TestCase):
    def setUp(self):
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(raman_history.Persistence(os.path.join(state_home, "raman")))

    def query(self, **fields):
        return probe.handle_json(json.dumps(dict({"command": "apps.query", "requestId": "a:t:1"}, **fields)), self.state)

    def test_queries_never_walk_proc_or_signal(self):
        forbidden = AssertionError("a query touched processes")
        with unittest.mock.patch.object(probe, "scan", side_effect=forbidden), \
                unittest.mock.patch.object(probe, "read_processes", side_effect=forbidden), \
                unittest.mock.patch.object(probe, "read_proc", side_effect=forbidden), \
                unittest.mock.patch.object(probe, "kill_group", side_effect=forbidden), \
                unittest.mock.patch.object(probe.os, "kill", side_effect=forbidden):
            inactive = self.query()
            self.assertEqual((inactive["type"], inactive["status"], inactive["rows"]), ("apps-page", "inactive", []),
                             "no closed-panel scan")
            self.state.detail_active = True
            self.assertEqual(self.query()["status"], "pending")
            self.state.inventory.publish([row("a", pss=10, pids=[7]), row("b", pss=20)], time.time(),
                                         time.monotonic(), INFO, CPU)
            for generation in range(30):  # a burst of keystrokes
                reply = self.query(query="a", generation=generation)
                self.assertEqual((reply["generation"], reply["total"], reply["requestId"]), (generation, 1, "a:t:1"))
        self.assertFalse(self.state.detail_refresh, "a fresh snapshot needs no refresh")
        self.assertEqual(reply["units"]["cpuCorePercent"], "% of one logical CPU")
        self.assertEqual(reply["schemaVersion"], 1)

    def test_pinned_paging_expiry_and_stale_refresh(self):
        self.state.detail_active = True
        first = self.state.inventory.publish([row("a", pss=10)], 1.0, time.monotonic() - 60, INFO, CPU)
        stale = self.query()
        self.assertTrue(stale["stale"])
        self.assertTrue(self.state.detail_refresh, "an old snapshot schedules one refresh in the main loop")
        for _ in range(apps.SNAPSHOTS_KEPT - 1):
            self.state.inventory.publish([row("b", pss=10)], 2.0, time.monotonic(), INFO, CPU)
        pinned = self.query(snapshot=first["id"])
        self.assertEqual((pinned["snapshot"], pinned["rows"][0]["id"]), (first["id"], "a"),
                         "a pinned page reads its own snapshot, not the newest")
        self.state.inventory.publish([row("c", pss=10)], 3.0, time.monotonic(), INFO, CPU)
        expired = self.query(snapshot=first["id"], generation=4)
        self.assertEqual((expired["type"], expired["code"], expired["generation"]), ("error", "snapshot-expired", 4))
        self.assertEqual(self.query(sort="vram")["code"], "bad-sort")

    def test_unicode_digit_query_replies_to_its_request(self):
        self.state.detail_active = True
        self.state.inventory.publish([row("pid:2", name="²", pss=10, pids=[2])], time.time(), time.monotonic(),
                                     INFO, CPU)
        reply = self.query(generation=7, query="²")
        self.assertEqual((reply["type"], reply["requestId"], reply["generation"], reply["total"]),
                         ("apps-page", "a:t:1", 7, 1), "a literal search, not an uncorrelated internal error")

    def test_validation_errors_echo_a_valid_generation(self):
        bad = [({"offset": 4097}, "bad-page"), ({"limit": 0}, "bad-page"), ({"query": 5}, "bad-query"),
               ({"sort": "vram"}, "bad-sort"), ({"snapshot": 0}, "bad-request")]
        for fields, code in bad:
            error = self.query(generation=7, **fields)
            self.assertEqual((error["type"], error["code"], error["requestId"], error["generation"]),
                             ("error", code, "a:t:1", 7), fields)
            self.assertIsNone(self.query(**fields)["generation"], "an omitted generation echoes null, like a page")
        for generation in (-1, True, "7", 2.5, 2 ** 53):
            error = self.query(generation=generation, offset=4097)
            self.assertEqual(error["code"], "bad-request", generation)
            self.assertNotIn("generation", error, "an invalid generation is never echoed")

    @unittest.skipUnless(shutil.which("node"), "needs Node.js for the client helper")
    def test_error_envelopes_meet_the_client_acceptance_helper(self):
        script = ("const R = require(%s).load('Runtime.js');"
                  "const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
                  "console.log(JSON.stringify(cases.map(c => R.acceptsAppsReply(c[0], c[1], c[2]))));"
                  % json.dumps(os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_js.js")))
        cases = [["a:t:1", 7, self.query(generation=7, offset=4097)],
                 ["a:t:1", 7, self.query(generation=7, sort="vram")],
                 ["a:t:1", 7, self.query(generation=7)],
                 ["a:t:1", 8, self.query(generation=7, offset=4097)],
                 ["a:t:1", 7, self.query(generation="7", offset=4097)]]
        result = subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True, text=True,
                                timeout=30, check=True)
        self.assertEqual(json.loads(result.stdout), [True, True, True, False, False],
                         "valid-generation errors are accepted; stale or invalid generations are not")

    def test_context_marks_partial_subtotals(self):
        self.state.detail_active = True
        partial = row("p", pss=2048)
        partial["memoryStatus"] = "partial"
        self.state.detail_snapshot = (time.monotonic(), 5.0, [partial, row("q", pss=4096)])
        context = raman_history.sanitize_context(self.state.context(time.monotonic()))
        self.assertEqual(context["apps"], [{"name": "p", "kb": 2048, "partial": True}, {"name": "q", "kb": 4096}])
        self.assertNotIn("partial", raman_history.sanitize_context(
            {"status": "observed", "at": 1, "apps": [{"name": "x", "kb": 1, "partial": "yes"}]})["apps"][0])

    def test_demo_rows_are_a_demo_snapshot(self):
        scene = probe.load_demo("red")
        self.state.detail_active = True
        self.state.inventory.publish(scene["apps"], 1.0, time.monotonic(), probe.DEMO_INVENTORY, probe.DEMO_CPU,
                                     demo=True)
        reply = self.query(sort="cpu")
        self.assertTrue(reply["demo"])
        self.assertEqual(reply["total"], len(scene["apps"]))
        self.assertTrue(all(r["cpuCorePercent"] is None and r["cpuStatus"] == "unavailable" for r in reply["rows"]))


def busy_wait_for(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(os.path.exists("/proc/self/smaps_rollup"), "needs Linux procfs")
class LiveProbeAppsTests(unittest.TestCase):
    """The real probe against test-owned children only. Nothing else is signalled."""

    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())
        self.children = []
        self.p = start_probe(self.state)
        self.requests = 0

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            if child.stdout:
                child.stdout.close()
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        self.p.stdout.close()

    def child(self, argv, **kwargs):
        child = subprocess.Popen(argv, **kwargs)
        self.children.append(child)
        return child

    def query(self, **fields):
        self.requests += 1
        request_id = "a:live:%d" % self.requests
        send(self.p, dict({"command": "apps.query", "requestId": request_id, "generation": self.requests}, **fields))
        return answer(self.p, request_id)[-1]

    def next_apps(self):
        return read_until(self.p, lambda m: m["type"] == "apps", timeout=10)[-1]

    def hog_row(self, hog):
        reply = self.query(query=str(hog.pid), sort="cpu")
        return reply, next((r for r in reply["rows"] if r["id"] == "pid:%d" % hog.pid), None)

    def test_low_memory_cpu_hog_outside_preview_and_reopen_warmup(self):
        holder = "import sys, time; data = b'x' * (16 << 20); print('ready', flush=True); time.sleep(120)"
        holders = [self.child([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True) for _ in range(13)]
        for child in holders:
            self.assertEqual(child.stdout.readline().strip(), "ready")
        hog = self.child(["sh", "-c", "while :; do :; done"])
        send(self.p, "detail 1")
        first = self.next_apps()
        self.assertIn("snapshot", first)
        self.assertEqual(first["cpu"]["reset"], "start")
        legacy = self.next_apps()
        self.assertEqual(len(legacy["apps"]), probe.MAX_APPS)
        self.assertNotIn("pid:%d" % hog.pid, [a["id"] for a in legacy["apps"]], "outside the memory top twelve")
        reply, hog_row = self.hog_row(hog)
        self.assertEqual(reply["status"], "ready")
        self.assertTrue(reply["inventory"]["complete"])
        self.assertIsNotNone(hog_row, "found by PID search in the full inventory")
        self.assertEqual(hog_row["cpuStatus"], "available")
        self.assertGreater(hog_row["cpuCorePercent"], 40.0)
        online = reply["cpu"]["onlineCpus"]
        self.assertAlmostEqual(hog_row["cpuMachinePercent"], hog_row["cpuCorePercent"] / online)
        self.assertEqual(hog_row["memoryStatus"], "available")
        top = self.query(sort="cpu")["rows"][:3]
        self.assertIn(hog_row["id"], [r["id"] for r in top], "the CPU lens ranks the hog first")
        # Closing and reopening the panel starts CPU from warm-up again.
        send(self.p, "detail 0", "detail 1")
        reopened = read_until(self.p, lambda m: m["type"] == "apps" and m["cpu"]["reset"] == "start", timeout=10)[-1]
        self.assertEqual((reopened["cpu"]["reset"], reopened["cpu"]["status"]), ("start", "warming-up"))
        _, warming = self.hog_row(hog)
        self.assertEqual((warming["cpuCorePercent"], warming["cpuStatus"]), (None, "warming-up"))

    def test_query_burst_reuses_snapshots_without_scanning(self):
        send(self.p, "detail 1")
        self.next_apps()
        for index in range(30):
            send(self.p, {"command": "apps.query", "requestId": "a:burst:%d" % index, "generation": index,
                          "query": "python"[:1 + index % 6], "sort": ("memory", "cpu")[index % 2]})
        messages = read_until(self.p, lambda m: m.get("requestId") == "a:burst:29")
        replies = [m for m in messages if m["type"] == "apps-page"]
        self.assertEqual([m["generation"] for m in replies], list(range(30)))
        self.assertLessEqual(len([m for m in messages if m["type"] == "apps"]), 1,
                             "thirty queries cause no extra scans; at most the regular cadence")
        self.assertLessEqual(len({m["snapshot"] for m in replies}), 2)

    def test_non_dumpable_child_keeps_cpu_with_memory_unavailable(self):
        code = ("import ctypes, time; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0); "
                "print('ready', flush=True); time.sleep(120)")
        child = self.child([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        try:
            with open("/proc/%d/smaps_rollup" % child.pid) as handle:
                handle.read()
            self.skipTest("this environment can read a non-dumpable process's memory (CAP_SYS_PTRACE)")
        except PermissionError:
            pass
        send(self.p, "detail 1")
        self.next_apps()
        self.next_apps()
        reply = self.query(query=str(child.pid))
        found = [r for r in reply["rows"] if r["id"] == "pid:%d" % child.pid]
        self.assertEqual(len(found), 1, "kept in the inventory despite unreadable memory")
        self.assertEqual((found[0]["pss"], found[0]["swap"], found[0]["memoryStatus"]), (None, None, "unavailable"))
        self.assertEqual(found[0]["cpuStatus"], "available")
        self.assertGreaterEqual(reply["inventory"]["memoryUnavailable"], 1)


if __name__ == "__main__":
    unittest.main()
