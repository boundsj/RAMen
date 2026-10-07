"""A2: membership-checked apps.kill, on-demand apps.details and query focus.

Fixture and handler tests run on any platform and never signal: the
signalling functions are patched to fail the test if reached. Live tests
start the real probe on Linux, with XDG_STATE_HOME in a test-owned directory,
and only ever signal children this test started.
"""

import errno
import json
import os
import signal
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
from test_apps import CHAT, CPU, INFO, TERM, FixtureTest, busy_wait_for, row  # noqa: E402
from test_probe import answer, read_until, send, start_probe  # noqa: E402

LINUX = os.path.exists("/proc/self/smaps_rollup")
FORBIDDEN = AssertionError("this path must not signal")


def no_signals():
    """Patches that fail the test if anything would be signalled."""
    return [unittest.mock.patch.object(probe, "signal_group", side_effect=FORBIDDEN),
            unittest.mock.patch.object(probe.os, "kill", side_effect=FORBIDDEN),
            unittest.mock.patch.object(probe.signal, "pidfd_send_signal", side_effect=FORBIDDEN, create=True)]


class Handler(unittest.TestCase):
    def setUp(self):
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(raman_history.Persistence(os.path.join(state_home, "raman")))
        self.requests = 0

    def command(self, name, **fields):
        self.requests += 1
        return probe.handle_json(json.dumps(dict({"command": name, "requestId": "k:t:%d" % self.requests,
                                                  "generation": self.requests}, **fields)), self.state)

    def kill(self, **fields):
        return self.command("apps.kill", **dict({"signal": "TERM"}, **fields))


class ValidationTests(Handler):
    def test_kill_and_details_requests_are_validated_and_echo_the_generation(self):
        bad = [{"id": ""}, {"id": "x" * 513}, {"id": "a\nb"}, {"id": 5}, {"id": "a"},
               {"id": "a", "membership": ""}, {"id": "a", "membership": "g" * 65},
               {"id": "a", "membership": "g", "signal": "STOP"}, {"id": "a", "membership": "g", "signal": 9}]
        with contextlib_all(no_signals()):
            for fields in bad:
                reply = self.command("apps.kill", **fields)
                self.assertEqual((reply["type"], reply["code"], reply["generation"]), ("error", "bad-request", self.requests),
                                 fields)
            reply = self.command("apps.kill", id="a", membership="g", signal="TERM", generation="7")
            self.assertEqual(reply["code"], "bad-request")
            self.assertNotIn("generation", reply, "an invalid generation is never echoed")
            self.assertEqual(self.command("apps.details", id="")["code"], "bad-request")
        self.assertEqual(apps.parse_action({"id": "pid:1", "membership": "g", "signal": "KILL"}, kill=True),
                         ("pid:1", "g", "KILL"))
        self.assertEqual(apps.parse_action({"id": "pid:1"}, kill=False), ("pid:1", None, None))

    def test_focus_reports_presence_rank_and_membership(self):
        self.state.detail_active = True
        rows = [row("a", pss=30, cpu=1.0), row("b", pss=20, cpu=9.0), row("c", pss=10, cpu=5.0)]
        rows[1]["generation"] = "gen-b"
        self.state.inventory.publish(rows, time.time(), time.monotonic(), INFO, CPU)
        reply = self.command("apps.query", focus="b", sort="cpu", limit=1)
        self.assertEqual([r["id"] for r in reply["rows"]], ["b"])
        focus = reply["focus"]
        self.assertEqual((focus["id"], focus["present"], focus["generation"], focus["rank"]), ("b", True, "gen-b", 0))
        self.assertEqual(focus["row"]["id"], "b")
        moved = self.command("apps.query", focus="b", sort="memory", offset=0, limit=1)
        self.assertEqual((moved["rows"][0]["id"], moved["focus"]["rank"]), ("a", 1), "found on another page")
        filtered = self.command("apps.query", focus="b", query="zzz")
        self.assertEqual((filtered["focus"]["present"], filtered["focus"]["rank"]), (True, None))
        gone = self.command("apps.query", focus="nope")
        self.assertEqual((gone["focus"]["present"], gone["focus"]["generation"], gone["focus"]["row"]), (False, None, None))
        self.assertNotIn("focus", self.command("apps.query"), "focus is additive")
        bad = self.command("apps.query", focus="x" * 513)
        self.assertEqual((bad["code"], bad["generation"]), ("bad-request", self.requests))
        forbidden = AssertionError("a focus query touched processes")
        with contextlib_all(no_signals() + [
                unittest.mock.patch.object(probe, "scan", side_effect=forbidden),
                unittest.mock.patch.object(probe, "read_processes", side_effect=forbidden),
                unittest.mock.patch.object(probe, "read_proc", side_effect=forbidden),
                unittest.mock.patch.object(probe, "live_members", side_effect=forbidden),
                unittest.mock.patch.object(probe, "read_command", side_effect=forbidden)]):
            for generation in range(20):  # keystrokes with the armed row tracked
                self.command("apps.query", focus="b", query="b"[:generation % 2])
            self.assertEqual(self.command("apps.query", focus="b")["focus"]["generation"], "gen-b")


class MembershipFixtureTests(FixtureTest):
    """A fake /proc scan feeds the real handler; nothing is signalled."""

    def setUp(self):
        FixtureTest.setUp(self)
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(raman_history.Persistence(os.path.join(state_home, "raman")))
        self.state.detail_active = True
        self.state.proc_root = self.proc.root  # apps.kill re-reads membership from the fake tree

    def publish(self):
        groups, info, cpu = self.scan()
        self.state.groups = groups
        self.state.inventory.publish(list(groups.values()), time.time(), time.monotonic(), info, cpu)
        return groups

    def kill(self, group_id, membership, signame="TERM"):
        return probe.handle_json(json.dumps({"command": "apps.kill", "requestId": "k:f:1", "generation": 1,
                                             "id": group_id, "membership": membership, "signal": signame}), self.state)

    def test_membership_changed_between_arm_and_confirm_is_refused(self):
        session = "app-Hyprland-ghostty-1.scope/101"
        self.proc.add(100, comm="ghostty", cgroup=TERM, pss=30_000)
        self.proc.add(101, comm="bash", ppid=100, cgroup=TERM, pss=4_000)
        self.proc.add(102, comm="node", ppid=101, cgroup=TERM, pss=300_000)
        armed = self.publish()[session]["generation"]  # the user arms this row
        self.proc.add(103, comm="cargo", ppid=102, cgroup=TERM, pss=9_000)  # a child joins before confirm
        current = self.publish()[session]["generation"]
        self.assertNotEqual(current, armed)
        with contextlib_all(no_signals()):
            reply = self.kill(session, armed)
        self.assertEqual((reply["type"], reply["code"], reply["sent"]), ("killed", "stale-membership", 0))
        self.assertEqual((reply["membership"], reply["currentMembership"], reply["id"]), (armed, current, session))
        self.assertEqual(reply["requestId"], "k:f:1")
        self.assertTrue(self.state.kill_followup, "a refused kill still rescans soon")
        self.proc.remove(103)  # it exits again: same PIDs and start times as when armed
        self.assertEqual(self.publish()[session]["generation"], armed, "membership is (pid, start) pairs")
        with unittest.mock.patch.object(probe, "signal_group", return_value={
                "sent": 2, "failed": 0, "skipped": 0, "error": "", "code": "sent"}) as signalled:
            reply = self.kill(session, armed)
        self.assertEqual((reply["code"], reply["sent"]), ("sent", 2))
        group = signalled.call_args[0][0]
        self.assertEqual(sorted(p for p, _ in group["pids"]), [101, 102], "the session's members, not the terminal")

    def test_changes_after_the_last_scan_are_refused_at_confirmation(self):
        """No second scan: the confirmation itself re-reads the group from /proc."""
        session = "app-Hyprland-ghostty-1.scope/101"
        cases = {
            "joined": lambda: self.proc.add(103, comm="cargo", ppid=102, cgroup=TERM, pss=9_000),
            "exited": lambda: self.proc.remove(102),
            "reused": lambda: self.proc.add(102, comm="node", ppid=101, cgroup=TERM, start=55555),
            "moved": lambda: self.proc.add(102, comm="node", ppid=101, cgroup=CHAT),
            "re-parented": lambda: self.proc.add(102, comm="node", ppid=100, cgroup=TERM),
        }
        for name, change in cases.items():
            with self.subTest(name):
                for pid in os.listdir(self.proc.root):
                    if pid.isdigit():
                        self.proc.remove(int(pid))
                self.proc.add(100, comm="ghostty", cgroup=TERM, pss=30_000)
                self.proc.add(101, comm="bash", ppid=100, cgroup=TERM, pss=4_000)
                self.proc.add(102, comm="node", ppid=101, cgroup=TERM, pss=300_000)
                armed = self.publish()[session]["generation"]
                change()
                with contextlib_all(no_signals()):
                    reply = self.kill(session, armed)
                self.assertEqual((reply["code"], reply["sent"]), ("stale-membership", 0))
                self.assertNotEqual(reply["currentMembership"], armed)
                self.assertEqual(self.state.groups[session]["generation"], armed, "no scan ran in between")

    def test_confirmation_signals_the_members_it_just_verified(self):
        self.proc.add(100, comm="ghostty", cgroup=TERM, pss=30_000)
        self.proc.add(101, comm="bash", ppid=100, cgroup=TERM, pss=4_000)
        self.proc.add(102, comm="node", ppid=101, cgroup=TERM, pss="unreadable")
        session = "app-Hyprland-ghostty-1.scope/101"
        armed = self.publish()[session]["generation"]
        with unittest.mock.patch.object(probe, "signal_group", return_value={
                "sent": 2, "failed": 0, "skipped": 0, "error": "", "code": "sent"}) as signalled, \
                unittest.mock.patch.object(probe, "read_proc", side_effect=AssertionError("no memory/CPU scan")):
            reply = self.kill(session, armed, "KILL")
        self.assertEqual((reply["code"], reply["currentMembership"]), ("sent", armed))
        self.assertEqual(sorted(map(tuple, signalled.call_args[0][0]["pids"])), [(101, 1010), (102, 1020)])
        self.assertEqual(signalled.call_args[0][1], "KILL")

    def test_unverifiable_membership_and_late_protection_signal_nothing(self):
        self.proc.add(60, comm="worker")
        self.proc.add(61, comm="other")
        armed = self.publish()["pid:60"]["generation"]
        with contextlib_all(no_signals()):
            with unittest.mock.patch.object(apps, "MAX_SCAN_PROCESSES", 1):
                reply = self.kill("pid:60", armed)
            self.assertEqual((reply["code"], reply["sent"]), ("unverified", 0), "over the process limit")
            self.state.proc_root = os.path.join(self.proc.root, "missing")
            self.assertEqual(self.kill("pid:60", armed)["code"], "unverified", "/proc unreadable")
            self.state.proc_root = self.proc.root
            self.proc.add(60, comm="pipewire")  # same process (start time), now a protected name
            self.assertEqual(self.kill("pid:60", armed)["code"], "protected")
            with unittest.mock.patch.object(probe, "protected_pids", return_value={60}):
                self.proc.add(60, comm="worker")
                self.assertEqual(self.kill("pid:60", armed)["code"], "protected", "never the probe itself")

    SESSION = "app-Hyprland-ghostty-1.scope/101"

    def arm_session(self):
        """A terminal session (shell 101, node 102) armed from a scan; returns its generation."""
        for pid in os.listdir(self.proc.root):
            if pid.isdigit():
                for name in os.listdir(os.path.join(self.proc.root, pid)):
                    os.chmod(os.path.join(self.proc.root, pid, name), 0o600)
                self.proc.remove(int(pid))
        self.proc.add(100, comm="ghostty", cgroup=TERM, pss=30_000)
        self.proc.add(101, comm="bash", ppid=100, cgroup=TERM, pss=4_000)
        self.proc.add(102, comm="node", ppid=101, cgroup=TERM, pss=300_000)
        return self.publish()[self.SESSION]["generation"]

    def path(self, pid, name=None):
        return os.path.join(self.proc.root, str(pid), *([name] if name else []))

    def owners(self, uids):
        """Report these fake entries as owned by other uids, by path (os.stat) and pinned (os.fstat)."""
        real_stat, real_fstat = os.stat, os.fstat
        by_inode = {(lambda r: (r.st_dev, r.st_ino))(real_stat(self.path(pid))): uid for pid, uid in uids.items()}

        def owner(result):
            uid = by_inode.get((result.st_dev, result.st_ino))
            if uid is None:
                return result
            return os.stat_result((result.st_mode, result.st_ino, result.st_dev, result.st_nlink, uid,
                                   result.st_gid, result.st_size, 0, 0, 0))
        return [unittest.mock.patch.object(probe.os, "stat", side_effect=lambda p, *a, **k: owner(real_stat(p, *a, **k))),
                unittest.mock.patch.object(probe.os, "fstat", side_effect=lambda fd: owner(real_fstat(fd)))]

    def failing(self, rules):
        """os.open raises for (pid, name, exc) rules: name None is the /proc/PID directory, "*" any file in it.

        Matches path opens and opens relative to a pinned directory descriptor alike.
        """
        real_open, real_fstat, real_stat = os.open, os.fstat, os.stat
        targets = [((lambda r: (r.st_dev, r.st_ino))(real_stat(self.path(pid))), pid, name, exc)
                   for pid, name, exc in rules]

        def guarded(path, flags, *args, dir_fd=None, **kwargs):
            if dir_fd is not None:
                pinned = (lambda r: (r.st_dev, r.st_ino))(real_fstat(dir_fd))
            for inode, pid, name, exc in targets:
                if dir_fd is not None:
                    hit = name is not None and pinned == inode and name in ("*", path)
                elif name is None:
                    hit = str(path) == self.path(pid)
                else:
                    hit = os.path.dirname(str(path)) == self.path(pid) and name in ("*", os.path.basename(str(path)))
                if hit:
                    raise exc
            return real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)
        return unittest.mock.patch.object(probe.os, "open", side_effect=guarded)

    def stat_failing(self, pid, exc):
        """os.stat of the /proc/PID directory raises exc."""
        real_stat = os.stat

        def guarded(path, *args, **kwargs):
            if str(path) == self.path(pid):
                raise exc
            return real_stat(path, *args, **kwargs)
        return unittest.mock.patch.object(probe.os, "stat", side_effect=guarded)

    def write(self, pid, name, text):
        with open(self.path(pid, name), "w") as handle:
            handle.write(text)

    def test_a_new_member_that_cannot_be_established_refuses_the_kill(self):
        """No second scan: a child joins after arming and its ownership, identity or cgroup cannot be read.

        It might be a member, so the confirmation is unverified and nothing is signalled,
        never the old members alone.
        """
        denied, io_error = PermissionError(errno.EACCES, "Permission denied"), OSError(errno.EIO, "I/O error")
        mine = os.getuid()
        cases = {
            "stat denied": lambda: [self.failing([(103, "stat", denied)])],
            "stat I/O error": lambda: [self.failing([(103, "stat", io_error)])],
            "cgroup denied": lambda: [self.failing([(103, "cgroup", denied)])],
            "cgroup I/O error": lambda: [self.failing([(103, "cgroup", io_error)])],
            "directory denied": lambda: [self.failing([(103, None, PermissionError(errno.EPERM, "Not permitted"))])],
            "owner unreadable": lambda: [self.stat_failing(103, io_error)],
            "root-owned, status denied": lambda: self.owners({103: 0}) + [self.failing([(103, "status", denied)])],
            "root-owned, status without Uid": lambda: (self.write(103, "status", "Name:\tcargo\n"),
                                                       self.owners({103: 0}))[1],
            "root-owned, short Uid": lambda: (self.write(103, "status", "Uid:\t%d\t%d\n" % (mine, mine)),
                                              self.owners({103: 0}))[1],
            "stat without parentheses": lambda: (self.write(103, "stat", "103 cargo S 102\n"), [])[1],
            "truncated stat": lambda: (self.write(103, "stat", "103 (cargo) S 102 103 103\n"), [])[1],
            "empty stat": lambda: (self.write(103, "stat", ""), [])[1],
            "non-numeric start": lambda: (self.write(103, "stat", "103 (cargo) S 102" + " 0" * 17 + " x\n"), [])[1],
            "empty cgroup": lambda: (self.write(103, "cgroup", ""), [])[1],
        }
        for name, unreadable in cases.items():
            with self.subTest(name):
                if name.startswith("root-owned") and mine == 0:
                    continue  # for a root probe a root-owned entry is simply its own
                armed = self.arm_session()
                self.proc.add(103, comm="cargo", ppid=102, cgroup=TERM, pss=9_000)
                with contextlib_all(no_signals() + unreadable()):
                    reply = self.kill(self.SESSION, armed)
                self.assertEqual((reply["code"], reply["sent"], reply["currentMembership"]), ("unverified", 0, None))
                self.assertEqual(self.state.groups[self.SESSION]["generation"], armed, "no scan ran in between")
                if name == "root-owned, status denied":  # the scan path still counts it as not ours
                    with contextlib_all(self.owners({103: 0}) + [self.failing([(103, "status", denied)])]):
                        self.assertFalse(probe.owned(self.proc.root, "103", mine))
                        self.assertEqual(probe.member_verdict(103, 1030, mine, self.proc.root), "foreign",
                                         "an unverifiable owner is never signalled")

    def test_real_permission_denial_of_a_new_member_refuses_the_kill(self):
        """The reviewer's case with actual EACCES from the filesystem (needs a non-root user)."""
        if os.geteuid() == 0:
            self.skipTest("root reads mode-000 files; the injected-error test covers this path")
        for name in ("stat", "cgroup", "status"):
            with self.subTest(name):
                armed = self.arm_session()
                self.proc.add(103, comm="cargo", ppid=102, cgroup=TERM, pss=9_000)
                if name == "status":
                    self.write(103, "status", "Uid:\t%d\t%d\t%d\t%d\n" % ((os.getuid(),) * 4))
                os.chmod(self.path(103, name), 0)
                with self.assertRaises(PermissionError, msg="the denial is real"):
                    open(self.path(103, name)).close()
                patches = self.owners({103: 0}) if name == "status" else []  # a non-dumpable process's entry
                with contextlib_all(no_signals() + patches):
                    reply = self.kill(self.SESSION, armed)
                self.assertEqual((reply["code"], reply["sent"]), ("unverified", 0))
                os.chmod(self.path(103, name), 0o600)
                with unittest.mock.patch.object(probe, "signal_group", return_value={
                        "sent": 3, "failed": 0, "skipped": 0, "error": "", "code": "sent"}):
                    readable = self.kill(self.SESSION, armed)
                self.assertEqual(readable["code"], "stale-membership", "once readable, the join is detected")

    def test_gone_foreign_kernel_and_zombie_processes_do_not_block_the_kill(self):
        """Processes that are positively not members leave the armed membership verified."""
        armed = self.arm_session()
        mine = os.getuid()
        self.proc.add(103, comm="cargo", ppid=102, cgroup=TERM, state="Z")  # zombie child
        self.proc.add(104, comm="kworker/0:1", ppid=2, cgroup="/", flags=probe.PF_KTHREAD)
        self.proc.add(105, comm="other", ppid=102, cgroup=TERM)  # another user's: never read at all
        owners = {105: mine + 4242}
        if mine != 0:  # root-owned entries are another user's only for a non-root probe
            self.proc.add(106, comm="daemon", cgroup="/system.slice/daemon.service")
            self.write(106, "status", "Uid:\t0\t0\t0\t0\n")  # root's own
            self.proc.add(107, comm="sudo", ppid=102, cgroup=TERM)
            self.write(107, "status", "Uid:\t%d\t0\t0\t0\n" % mine)  # setuid in the session: not ours
            owners.update({106: 0, 107: 0})
        self.proc.add(108, comm="cargo", ppid=102, cgroup=TERM)  # exits between stat and cgroup
        self.proc.add(109, comm="cargo", ppid=102, cgroup=TERM)  # exits before its directory opens
        self.proc.add(110, comm="cargo", ppid=102, cgroup=TERM)  # exits before its stat is read
        gone = ProcessLookupError(errno.ESRCH, "No such process")
        patches = self.owners(owners) + [self.failing([
            (105, "*", AssertionError("another user's files are never read")),
            (105, None, AssertionError("another user's directory is never opened")),
            (108, "cgroup", gone), (109, None, FileNotFoundError(errno.ENOENT, "gone")),
            (110, "stat", gone)])]
        with unittest.mock.patch.object(probe, "signal_group", return_value={
                "sent": 2, "failed": 0, "skipped": 0, "error": "", "code": "sent"}) as signalled, \
                contextlib_all(patches):
            reply = self.kill(self.SESSION, armed)
        self.assertEqual((reply["code"], reply["currentMembership"]), ("sent", armed))
        self.assertEqual(sorted(map(tuple, signalled.call_args[0][0]["pids"])), [(101, 1010), (102, 1020)])
        if LINUX:  # real procfs, with real processes of other users (at least PID 1 when not root)
            me = probe.live_members("pid:%d" % os.getpid())
            self.assertIsNotNone(me, "an ordinary pass over real /proc is verified")

    def test_reused_pid_changes_membership(self):
        self.proc.add(60, comm="worker", pss=2_000)
        armed = self.publish()["pid:60"]["generation"]
        self.proc.add(60, comm="worker", pss=2_000, start=99999)  # same PID, a different process
        self.publish()
        with contextlib_all(no_signals()):
            reply = self.kill("pid:60", armed)
        self.assertEqual(reply["code"], "stale-membership")

    def test_vanished_protected_and_inactive_groups_are_refused(self):
        self.proc.add(70, comm="pipewire", pss=2_000)
        self.proc.add(71, comm="app", pss=2_000)
        groups = self.publish()
        self.assertTrue(groups["pid:70"]["protected"])
        with contextlib_all(no_signals()):
            self.assertEqual(self.kill("pid:70", groups["pid:70"]["generation"])["code"], "protected")
            self.assertEqual(self.kill("pid:404", "whatever")["code"], "gone")
            self.state.detail_active = False
            self.assertEqual(self.kill("pid:71", groups["pid:71"]["generation"])["code"], "gone",
                             "without a running scan there is nothing current to match")

    def test_member_verdict_rechecks_identity_protection_and_owner(self):
        self.proc.add(80, comm="app")
        self.proc.add(81, comm="Hyprland")
        self.proc.add(82, comm="zombie", state="Z")
        root, uid = self.proc.root, os.getuid()
        self.assertEqual(probe.member_verdict(80, 800, uid, root), "ok")
        self.assertEqual(probe.member_verdict(80, 801, uid, root), "gone", "a different start time is another process")
        self.assertEqual(probe.member_verdict(81, 810, uid, root), "protected")
        self.assertEqual(probe.member_verdict(82, 820, uid, root), "gone")
        self.assertEqual(probe.member_verdict(83, 830, uid, root), "gone")
        if uid != 0:
            self.assertEqual(probe.member_verdict(80, 800, uid + 1, root), "foreign")
        with unittest.mock.patch.object(probe, "protected_pids", return_value={80}):
            self.assertEqual(probe.member_verdict(80, 800, uid, root), "protected", "the probe never signals itself")

    def test_details_list_heaviest_members_and_read_commands_on_request(self):
        self.proc.add(100, comm="ghostty", cgroup=TERM, pss=30_000)
        self.proc.add(101, comm="bash", ppid=100, cgroup=TERM, pss=4_000, argv=["bash"])
        self.proc.add(102, comm="node", ppid=101, cgroup=TERM, pss=300_000, argv=["node", "/usr/bin/claude", "--x"])
        self.publish()
        session = "app-Hyprland-ghostty-1.scope/101"
        reads = []

        def command(member):
            reads.append(member["pid"])
            return "cmd %d" % member["pid"], False, "available"

        with contextlib_all(no_signals()), unittest.mock.patch.object(probe, "read_command", side_effect=command):
            reply = probe.handle_json(json.dumps({"command": "apps.details", "requestId": "d:t:3", "generation": 3,
                                                  "id": session}), self.state)
        self.assertEqual((reply["type"], reply["requestId"], reply["generation"], reply["id"]),
                         ("apps-details", "d:t:3", 3, session))
        self.assertEqual([m["pid"] for m in reply["members"]], [102, 101], "heaviest first")
        self.assertEqual(reply["members"][0]["command"], "cmd 102")
        self.assertEqual(reply["row"]["id"], session)
        self.assertEqual(reply["membersTotal"], 2)
        self.assertEqual(sorted(reads), [101, 102], "command lines are read only for the listed members, on request")
        self.assertNotIn("argv", json.dumps(self.state.groups[session]["members"]), "argv is not kept past the scan")
        gone = probe.handle_json(json.dumps({"command": "apps.details", "requestId": "d:t:4", "generation": 4,
                                             "id": "pid:404"}), self.state)
        self.assertEqual((gone["type"], gone["code"], gone["generation"]), ("error", "gone", 4))
        self.state.detail_active = False
        inactive = probe.handle_json(json.dumps({"command": "apps.details", "requestId": "d:t:5", "generation": 5,
                                                 "id": session}), self.state)
        self.assertEqual(inactive["code"], "inactive")

    def test_read_command_validates_identity_and_bounds_text(self):
        long = ["tool"] + ["--flag-%03d" % i for i in range(200)]
        self.proc.add(90, comm="tool", argv=long)
        with unittest.mock.patch.object(probe, "start_time", side_effect=lambda pid: 900):
            text, truncated, status = probe.read_command({"pid": 90, "start": 900}, self.proc.root)
        self.assertEqual(status, "available")
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), apps.MAX_COMMAND)
        self.assertTrue(text.startswith("tool --flag-000"))
        with unittest.mock.patch.object(probe, "start_time", side_effect=lambda pid: 12345):
            self.assertEqual(probe.read_command({"pid": 90, "start": 900}, self.proc.root), (None, False, "exited"),
                             "a reused PID's command line is never shown")
        self.assertEqual(apps.command_text(["a\tb", "c\x1bd"]), ("a b c d", False), "control characters never render")

    def test_details_reply_is_bounded(self):
        members = [{"pid": i, "start": 1, "comm": "w", "name": "中" * 256, "pss": 1000 - i, "swap": 0, "cpu": None,
                    "cpuState": "warming-up", "state": "S"} for i in range(200)]
        reply = apps.details_reply({"type": "apps-details"}, {"id": "x"}, members, 4,
                                   lambda m: ("中" * apps.MAX_COMMAND, False, "available"))
        self.assertLessEqual(len(json.dumps(reply, separators=(",", ":"))) + 1, apps.MAX_REPLY_BYTES)
        self.assertLessEqual(len(reply["members"]), apps.MAX_DETAIL_MEMBERS)
        self.assertEqual(reply["membersTotal"], 200)
        self.assertEqual(reply["members"][0]["cpuStatus"], "warming-up")


class DemoActionTests(Handler):
    def setUp(self):
        Handler.setUp(self)
        self.state.start_demo(probe.load_demo("apps"))
        self.state.detail_active = True
        demo = self.state.demo
        self.state.inventory.publish(demo["apps"], time.time(), time.monotonic(), probe.DEMO_INVENTORY, demo["cpu"],
                                     demo=True)

    def test_apps_scene_is_original_and_covers_the_states(self):
        scene = self.state.demo
        rows = {a["name"]: a for a in scene["apps"]}
        hog = rows["spin-the-noodle.sh"]
        self.assertNotIn(hog["id"], [a["id"] for a in probe.demo_preview(scene["apps"])],
                         "the low-memory CPU hog is outside the Memory preview")
        page = self.command("apps.query", sort="cpu")
        self.assertEqual(page["rows"][0]["name"], "Broth simulator (rolling boil)")
        self.assertEqual(page["rows"][1]["id"], hog["id"], "and near the top of the CPU lens")
        self.assertAlmostEqual(page["rows"][1]["cpuMachinePercent"], 99.6 / 8)
        statuses = {(r["memoryStatus"], r["cpuStatus"]) for r in page["rows"]}
        for expected in [("partial", "available"), ("unavailable", "available"), ("available", "warming-up"),
                         ("available", "partial")]:
            self.assertIn(expected, statuses)
        self.assertTrue(any(r["protected"] for r in page["rows"]))
        self.assertNotIn("demoMembers", json.dumps(probe.demo_preview(scene["apps"])))
        with unittest.mock.patch.object(probe, "read_command", side_effect=AssertionError("demo read /proc")):
            details = self.command("apps.details", id=rows["Broth simulator (rolling boil)"]["id"])
        self.assertEqual(details["members"][0]["commandStatus"], "demo")
        self.assertTrue(details["demo"])

    def test_demo_kill_only_drops_a_fake_row_with_the_armed_membership(self):
        hog = next(a for a in self.state.demo["apps"] if a["name"] == "spin-the-noodle.sh")
        protected = next(a for a in self.state.demo["apps"] if a["protected"])
        with contextlib_all(no_signals()):
            stale = self.kill(id=hog["id"], membership="demo-0")
            self.assertEqual((stale["code"], stale["demo"]), ("stale-membership", True))
            self.assertIn(hog, self.state.demo["apps"])
            self.assertEqual(self.kill(id=protected["id"], membership=protected["generation"])["code"], "protected")
            done = self.kill(id=hog["id"], membership=hog["generation"], signal="KILL")
            self.assertEqual((done["code"], done["sent"], done["error"]), ("demo", 1, ""))
            self.assertNotIn(hog, self.state.demo["apps"])
            self.assertEqual(self.kill(id=hog["id"], membership=hog["generation"])["code"], "gone")


class SignalOrderTests(unittest.TestCase):
    """signal_group with process primitives patched: identities change on cue, nothing is signalled."""

    def setUp(self):
        self.current = {}  # pid -> (start, comm verdict when it matches)
        self.events = []
        self.on_check = {}  # (pid, nth check) -> change to apply after that check

    def verdict(self, pid, start, uid, root="/proc"):
        now, verdict = self.current[pid]
        self.events.append(("check", pid, now))
        nth = sum(1 for e in self.events if e[:2] == ("check", pid))
        if (pid, nth) in self.on_check:
            self.on_check.pop((pid, nth))()
        return verdict if now == start else "gone"

    def kill(self, pid, sig):
        self.events.append(("SIGNAL", pid, self.current[pid][0]))

    def pidfd_signal(self, fd, sig):
        self.events.append(("SIGNAL", fd - 1000, self.pinned[fd]))

    def run_group(self, pids, pidfd=None):
        self.pinned = {}

        def opened(pid):
            if pidfd is not None:
                return pidfd(pid)
            self.pinned[1000 + pid] = self.current[pid][0]
            return 1000 + pid

        patches = [unittest.mock.patch.object(probe, "member_verdict", side_effect=self.verdict),
                   unittest.mock.patch.object(probe.os, "kill", side_effect=self.kill),
                   unittest.mock.patch.object(probe.os, "close"),
                   unittest.mock.patch.object(probe.signal, "pidfd_send_signal", side_effect=self.pidfd_signal,
                                              create=True),
                   unittest.mock.patch.object(probe, "open_pidfd", side_effect=opened)]
        with contextlib_all(patches):
            return probe.signal_group({"id": "x", "pids": pids}, "TERM")

    def signalled(self):
        return [e[1:] for e in self.events if e[0] == "SIGNAL"]

    def assert_checked_right_before_each_signal(self):
        for index, event in enumerate(self.events):
            if event[0] == "SIGNAL":
                self.assertEqual(self.events[index - 1], ("check",) + event[1:], self.events)

    def reuse(self, pid, start, verdict="ok"):
        return lambda: self.current.__setitem__(pid, (start, verdict))

    def test_fallback_rechecks_each_pid_right_before_its_signal(self):
        """Review repro: PID 10001 is reused while 10002 is checked; os.kill must not reach the new process."""
        self.current = {10001: (11, "ok"), 10002: (22, "ok")}
        self.on_check[(10002, 1)] = self.reuse(10001, 999)
        result = self.run_group([[10001, 11], [10002, 22]], pidfd=lambda pid: None)
        self.assertEqual(self.signalled(), [(10002, 22)], self.events)
        self.assertEqual((result["code"], result["sent"], result["skipped"]), ("sent", 1, 1))
        self.assert_checked_right_before_each_signal()

    def test_owner_and_protection_changes_after_the_preflight(self):
        self.current = {1: (10, "ok"), 2: (20, "ok"), 3: (30, "ok")}
        self.on_check[(3, 1)] = self.reuse(2, 20, "foreign")  # a setuid exec while 3 is preflighted
        result = self.run_group([[1, 10], [2, 20], [3, 30]], pidfd=lambda pid: None)
        self.assertEqual(self.signalled(), [(1, 10), (3, 30)])
        self.assertEqual((result["failed"], result["error"]), (1, "not owned by this user"))
        self.assert_checked_right_before_each_signal()
        self.setUp()
        self.current = {1: (10, "ok"), 2: (20, "ok"), 3: (30, "ok")}
        self.on_check[(1, 2)] = self.reuse(2, 20, "protected")  # becomes protected after the preflight
        result = self.run_group([[1, 10], [2, 20], [3, 30]], pidfd=lambda pid: None)
        self.assertEqual(self.signalled(), [(1, 10)], "the remaining members are not signalled")
        self.assertEqual((result["code"], result["sent"], result["error"]), ("sent", 1, "protected"))
        self.setUp()
        self.current = {1: (10, "ok"), 2: (20, "protected")}
        result = self.run_group([[1, 10], [2, 20]])
        self.assertEqual((self.signalled(), result["code"]), ([], "protected"), "the preflight refuses the group")

    def test_pidfd_pins_each_member_and_failures_never_fall_back(self):
        self.current = {1: (10, "ok"), 2: (20, "ok")}
        self.on_check[(2, 1)] = self.reuse(1, 99)
        result = self.run_group([[1, 10], [2, 20]])
        self.assertEqual(self.signalled(), [(2, 20)], "a member reused before its pidfd opened is skipped")
        self.assertEqual(result["skipped"], 1)
        self.setUp()
        self.current = {1: (10, "ok"), 2: (20, "ok")}

        def exhausted(pid):
            if pid == 1:
                raise OSError(errno.EMFILE, "Too many open files")
            self.pinned[1000 + pid] = self.current[pid][0]
            return 1000 + pid

        result = self.run_group([[1, 10], [2, 20]], pidfd=exhausted)
        self.assertEqual(self.signalled(), [(2, 20)], "no numeric os.kill when pidfd_open fails")
        self.assertEqual((result["failed"], result["sent"]), (1, 1))
        self.assertIn("Too many open files", result["error"])

    def test_open_pidfd_distinguishes_unsupported_from_failure(self):
        cases = [(OSError(errno.ENOSYS, "Function not implemented"), None),
                 (ProcessLookupError(errno.ESRCH, "No such process"), "gone")]
        for exc, expected in cases:
            with unittest.mock.patch.object(probe.os, "pidfd_open", side_effect=exc, create=True):
                self.assertEqual(probe.open_pidfd(5), expected)
        for exc in (OSError(errno.EMFILE, "Too many open files"), PermissionError(errno.EPERM, "Operation not permitted")):
            with unittest.mock.patch.object(probe.os, "pidfd_open", side_effect=exc, create=True):
                with self.assertRaises(OSError):
                    probe.open_pidfd(5)


@unittest.skipUnless(LINUX, "needs Linux procfs")
class SignalTests(unittest.TestCase):
    """signal_group against test-owned children only."""

    def child(self, argv, **kwargs):
        child = subprocess.Popen(argv, **kwargs)
        self.addCleanup(self.reap, child)
        return child

    def reap(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)

    def test_identity_is_rechecked_at_signal_time(self):
        child = self.child(["sleep", "30"])
        start = probe.start_time(child.pid)
        stale = probe.signal_group({"id": "x", "pids": [[child.pid, start + 1]]}, "TERM")
        self.assertEqual((stale["sent"], stale["skipped"], stale["code"]), (0, 1, "gone"), "a reused PID is skipped")
        self.assertIsNone(child.poll())
        result = probe.signal_group({"id": "x", "pids": [[child.pid, start]]}, "TERM")
        self.assertEqual((result["sent"], result["code"]), (1, "sent"))
        self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
        vanished = probe.signal_group({"id": "x", "pids": [[child.pid, start]]}, "KILL")
        self.assertEqual((vanished["sent"], vanished["code"]), (0, "gone"), "an exited member is skipped")

    def test_a_member_that_became_protected_refuses_the_whole_group(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        disguised = os.path.join(directory, "pipewire")  # comm comes from the executed file name
        os.symlink(subprocess.check_output(["sh", "-c", "command -v sleep"], text=True).strip(), disguised)
        protected = self.child([disguised, "30"])
        plain = self.child(["sleep", "30"])
        busy_wait_for(lambda: probe.parse_stat(probe.read_text("/proc/%d/stat" % protected.pid))[0] == "pipewire")
        group = {"id": "x", "pids": [[plain.pid, probe.start_time(plain.pid)],
                                     [protected.pid, probe.start_time(protected.pid)]]}
        result = probe.signal_group(group, "KILL")
        self.assertEqual((result["code"], result["sent"]), ("protected", 0))
        time.sleep(0.2)
        self.assertIsNone(plain.poll(), "nothing in the group was signalled")
        self.assertIsNone(protected.poll())

    def test_legacy_kill_group_reply_is_unchanged(self):
        child = self.child(["sleep", "30"])
        reply = probe.kill_group({"id": "pid:%d" % child.pid, "pids": [[child.pid, probe.start_time(child.pid)]]}, "KILL")
        self.assertEqual(set(reply), {"type", "id", "signal", "sent", "failed", "error"})
        self.assertEqual((reply["type"], reply["sent"]), ("killed", 1))
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)


CGROUP_ROOT = os.environ.get("RAMAN_TEST_CGROUP_ROOT", "")


@unittest.skipUnless(LINUX and CGROUP_ROOT and os.access(CGROUP_ROOT, os.W_OK),
                     "needs RAMAN_TEST_CGROUP_ROOT: a writable cgroup2 directory (for example a privileged container)")
class CgroupMembershipTests(unittest.TestCase):
    """A real app scope holding only test-owned children: membership changes that no scan has seen."""

    def setUp(self):
        self.scope = "app-raman-test-%d.scope" % os.getpid()
        self.path = os.path.join(CGROUP_ROOT, "app.slice", self.scope)
        os.makedirs(self.path)
        self.addCleanup(self.remove_scope)
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(raman_history.Persistence(os.path.join(state_home, "raman")))
        self.state.detail_active = True

    def remove_scope(self):
        with open(os.path.join(self.path, "cgroup.kill"), "w") as handle:
            handle.write("1")  # only this test's children are in the scope
        busy_wait_for(lambda: not probe.read_text(os.path.join(self.path, "cgroup.procs")).strip(), timeout=5)
        os.rmdir(self.path)
        try:
            os.rmdir(os.path.dirname(self.path))
        except OSError:
            pass

    def spawn(self):
        """A shell in the scope that forks a sleep into it when told to."""
        shell = subprocess.Popen(["sh", "-c", "read go; sleep 60 & echo $!; wait; read never"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (shell.poll() is None and shell.kill(), shell.wait(5), shell.stdin.close(),
                                 shell.stdout.close()))
        with open(os.path.join(self.path, "cgroup.procs"), "w") as handle:
            handle.write(str(shell.pid))
        self.assertIn(self.scope, probe.read_text("/proc/%d/cgroup" % shell.pid))
        return shell

    def fork_child(self, shell):
        shell.stdin.write("go\n")
        shell.stdin.flush()
        return int(shell.stdout.readline())

    def arm(self):
        """One real scan, as when the user arms the row."""
        groups, _, _ = probe.scan(apps.CpuTracker(probe.clock_ticks()))
        self.state.groups = {g["id"]: g for g in groups}
        return self.state.groups[self.scope]

    def kill(self, membership, signame="TERM"):
        return probe.handle_json(json.dumps({"command": "apps.kill", "requestId": "k:c:1", "generation": 1,
                                             "id": self.scope, "membership": membership, "signal": signame}),
                                 self.state)

    def test_a_child_forked_after_the_scan_refuses_the_kill(self):
        shell = self.spawn()
        armed = self.arm()
        self.assertEqual([p for p, _ in armed["pids"]], [shell.pid])
        child = self.fork_child(shell)  # joins the group after the scan, before the confirmation
        self.assertIn(self.scope, probe.read_text("/proc/%d/cgroup" % child))
        reply = self.kill(armed["generation"])
        self.assertEqual((reply["code"], reply["sent"]), ("stale-membership", 0))
        self.assertEqual(reply["currentMembership"], apps.membership_generation(
            [[shell.pid, probe.start_time(shell.pid)], [child, probe.start_time(child)]]))
        time.sleep(0.2)
        self.assertIsNone(shell.poll(), "nothing was signalled")
        self.assertEqual(probe.member_verdict(child, probe.start_time(child), os.getuid()), "ok")
        # Re-armed on a fresh scan, the same confirmation signals both members.
        armed = self.arm()
        reply = self.kill(armed["generation"], "KILL")
        self.assertEqual((reply["code"], reply["sent"]), ("sent", 2))
        self.assertEqual(shell.wait(timeout=5), -signal.SIGKILL)
        self.assertTrue(busy_wait_for(lambda: probe.member_verdict(child, 0, os.getuid()) == "gone", timeout=5))

    def test_a_member_that_exited_after_the_scan_refuses_the_kill(self):
        shell = self.spawn()
        child = self.fork_child(shell)
        armed = self.arm()
        self.assertEqual(len(armed["pids"]), 2)
        os.kill(child, signal.SIGKILL)  # a test-owned member leaves before the confirmation
        self.assertTrue(busy_wait_for(lambda: str(child) not in probe.read_text(
            os.path.join(self.path, "cgroup.procs")).split(), timeout=5))
        reply = self.kill(armed["generation"])
        self.assertEqual((reply["code"], reply["sent"]), ("stale-membership", 0))
        time.sleep(0.2)
        self.assertIsNone(shell.poll(), "the remaining member was not signalled")


@unittest.skipUnless(LINUX, "needs Linux procfs")
class LiveProbeActionTests(unittest.TestCase):
    """The real probe; it only ever signals children this test started."""

    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())
        self.p = start_probe(self.state)
        self.addCleanup(self.stop)
        self.requests = 0
        send(self.p, "detail 1")
        read_until(self.p, lambda m: m["type"] == "apps", timeout=10)

    def stop(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        self.p.stdout.close()

    def child(self, argv, **kwargs):
        child = subprocess.Popen(argv, **kwargs)
        self.addCleanup(self.reap, child)
        return child

    def reap(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()

    def command(self, name, **fields):
        self.requests += 1
        request_id = "%s:live:%d" % ("k" if name == "apps.kill" else "d" if name == "apps.details" else "a", self.requests)
        send(self.p, dict({"command": name, "requestId": request_id, "generation": self.requests}, **fields))
        return answer(self.p, request_id)[-1]

    def armed(self, pid):
        """The membership generation a user would arm, once the child is in a scan."""
        found = {}

        def scanned():
            read_until(self.p, lambda m: m["type"] == "apps", timeout=10)
            reply = self.command("apps.query", focus="pid:%d" % pid, query=str(pid))
            found.update(reply["focus"])
            return reply["focus"]["present"]

        self.assertTrue(busy_wait_for(scanned, timeout=15))
        return found["generation"]

    def test_confirm_signals_the_armed_child_and_escalates(self):
        stubborn = self.child(["sh", "-c", "trap '' TERM; echo ready; while :; do sleep 0.2; done"],
                              stdout=subprocess.PIPE, text=True)
        self.assertEqual(stubborn.stdout.readline().strip(), "ready")
        membership = self.armed(stubborn.pid)
        term = self.command("apps.kill", id="pid:%d" % stubborn.pid, membership=membership, signal="TERM")
        self.assertEqual((term["type"], term["code"], term["sent"], term["signal"]), ("killed", "sent", 1, "TERM"))
        time.sleep(0.5)
        self.assertIsNone(stubborn.poll(), "it ignores SIGTERM: the UI offers a force kill next")
        membership = self.armed(stubborn.pid)
        force = self.command("apps.kill", id="pid:%d" % stubborn.pid, membership=membership, signal="KILL")
        self.assertEqual((force["code"], force["sent"]), ("sent", 1))
        self.assertEqual(stubborn.wait(timeout=5), -signal.SIGKILL)

    def test_stale_membership_and_vanished_children_are_never_signalled(self):
        target = self.child(["sleep", "60"])
        membership = self.armed(target.pid)
        stale = self.command("apps.kill", id="pid:%d" % target.pid, membership="0" * 16, signal="KILL")
        self.assertEqual((stale["code"], stale["sent"], stale["currentMembership"]), ("stale-membership", 0, membership))
        time.sleep(0.3)
        self.assertIsNone(target.poll(), "a stale confirmation never signals")
        gone = self.child(["sleep", "60"])
        armed_gone = self.armed(gone.pid)
        gone.kill()
        gone.wait(timeout=5)
        reply = self.command("apps.kill", id="pid:%d" % gone.pid, membership=armed_gone, signal="TERM")
        self.assertEqual((reply["code"], reply["sent"]), ("gone", 0))
        self.assertIsNone(target.poll())

    def test_details_are_on_demand_and_never_saved(self):
        marker = "raman-a2-private-%d" % os.getpid()
        child = self.child(["sh", "-c", "sleep 60; : %s" % marker])
        self.armed(child.pid)
        details = self.command("apps.details", id="pid:%d" % child.pid)
        self.assertEqual(details["type"], "apps-details")
        self.assertEqual(len(details["members"]), 1)
        member = details["members"][0]
        self.assertEqual((member["pid"], member["commandStatus"]), (child.pid, "available"))
        self.assertIn(marker, member["command"])
        legacy = read_until(self.p, lambda m: m["type"] == "apps", timeout=10)[-1]
        for app in legacy["apps"]:
            self.assertFalse({"members", "pids"} & set(app), "scan internals stay inside the probe")
        # A busy desktop can push both probe processes out of the Memory
        # preview. Check our actual parent and worker in the full inventory.
        children = probe.read_text("/proc/%d/task/%d/children" % (self.p.pid, self.p.pid)).split()
        self.assertEqual(len(children), 1, "one worker belongs to the test-owned probe")
        for pid in [self.p.pid, int(children[0])]:
            own = self.command("apps.query", focus="pid:%d" % pid)["focus"]
            self.assertTrue(own["present"], "the test-owned probe is in the full inventory")
            self.assertTrue(own["row"]["protected"], "the probe's worker and parent are protected")
        self.stop()
        for directory, _, files in os.walk(self.state):
            for name in files:
                with open(os.path.join(directory, name), "rb") as handle:
                    self.assertNotIn(marker.encode(), handle.read(), "command lines are never persisted")


def contextlib_all(managers):
    import contextlib
    stack = contextlib.ExitStack()
    for manager in managers:
        stack.enter_context(manager)
    return stack


if __name__ == "__main__":
    unittest.main()
