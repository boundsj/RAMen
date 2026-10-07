import fcntl
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import raman_history  # noqa: E402
import raman_probe as probe  # noqa: E402

APP = "/user.slice/user-1000.slice/user@1000.service/app.slice/"
TERM = APP + "app-graphical.slice/app-Hyprland-xdg\\x2dterminal\\x2dexec-1.scope"
CHROME = APP + "app-Hyprland-google\\x2dchrome-2.scope"
SESSION = "/user.slice/user-1000.slice/user@1000.service/session.slice/wayland-wm@hyprland.desktop.service"


def start_probe(state_home):
    """The real probe with its history in a test-owned XDG_STATE_HOME."""
    env = dict(os.environ, XDG_STATE_HOME=state_home, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, probe.__file__], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)


def send(p, *lines):
    """Write commands: strings as-is, anything else as a JSON object."""
    for line in lines:
        p.stdin.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
    p.stdin.flush()


def answer(p, request_id):
    """Messages read up to and including the reply to request_id."""
    return read_until(p, lambda m: m.get("requestId") == request_id)


def read_until(p, predicate, timeout=5):
    """Messages read until one satisfies predicate (returned last), or AssertionError."""
    seen = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        seen.append(json.loads(line))
        if predicate(seen[-1]):
            return seen
    raise AssertionError("no matching message; saw %r" % [m.get("type") for m in seen])


def proc(pid, ppid, comm, pss, cgroup, swap=0, argv=None):
    return {"pid": pid, "ppid": ppid, "start": pid * 10, "comm": comm,
            "argv": argv if argv is not None else [comm], "pss": pss, "swap": swap, "cgroup": cgroup}


class GroupingTests(unittest.TestCase):
    def setUp(self):
        self.procs = [
            proc(100, 1, "chrome", 400_000, CHROME, swap=100_000,
                 argv=["/opt/google/chrome/chrome --type=browser"]),
            proc(101, 100, "chrome", 300_000, CHROME),
            proc(200, 1, "ghostty", 20_000, TERM),
            proc(201, 200, "bash", 4_000, TERM),
            proc(202, 201, "node", 350_000, TERM, argv=["node", "/usr/bin/claude"]),
            proc(203, 200, "bash", 4_000, TERM),
            proc(300, 1, "Hyprland", 30_000, SESSION),
            proc(301, 1, "quickshell", 90_000, SESSION),
        ]
        self.groups = {g["id"]: g for g in probe.group_processes(self.procs, self_pid=999)}

    def test_app_scope_is_one_group(self):
        chrome = self.groups["app-Hyprland-google\\x2dchrome-2.scope"]
        self.assertEqual(chrome["name"], "Chrome")
        self.assertEqual(chrome["count"], 2)
        self.assertEqual(chrome["pss"], 700_000)
        self.assertEqual(chrome["swap"], 100_000)

    def test_terminal_scope_splits_into_sessions(self):
        session = self.groups["app-Hyprland-xdg\\x2dterminal\\x2dexec-1.scope/201"]
        self.assertEqual(session["kind"], "session")
        self.assertEqual(session["name"], "Claude", "interpreter scripts are named by script")
        self.assertEqual(session["host"], "Ghostty")
        self.assertEqual(sorted(p for p, _ in session["pids"]), [201, 202])
        idle = self.groups["app-Hyprland-xdg\\x2dterminal\\x2dexec-1.scope/203"]
        self.assertEqual(idle["name"], "Bash")
        terminal = self.groups["app-Hyprland-xdg\\x2dterminal\\x2dexec-1.scope"]
        self.assertEqual([p for p, _ in terminal["pids"]], [200])

    def test_desktop_processes_are_protected(self):
        self.assertTrue(self.groups["pid:300"]["protected"])
        self.assertTrue(self.groups["pid:301"]["protected"])
        self.assertFalse(self.groups["app-Hyprland-google\\x2dchrome-2.scope"]["protected"])

    def test_sorted_by_footprint(self):
        ordered = probe.group_processes(self.procs, self_pid=999)
        self.assertEqual(ordered[0]["name"], "Chrome")

    def test_truncated_comm_uses_argv(self):
        p = proc(1, 0, "xdg-desktop-por", 1, SESSION, argv=["/usr/lib/xdg-desktop-portal-hyprland"])
        self.assertEqual(probe.process_name(p), "xdg-desktop-portal-hyprland")


    def test_interpreter_inline_code_and_modules(self):
        self.assertEqual(probe.process_name(proc(1, 0, "python3", 1, SESSION, argv=["python3", "-c", "x=1"])), "python3")
        self.assertEqual(probe.process_name(proc(1, 0, "python3", 1, SESSION, argv=["python3", "-m", "http.server"])), "http.server")


class ParseTests(unittest.TestCase):
    def test_psi(self):
        psi = probe.parse_psi("some avg10=1.50 avg60=0.66 avg300=0.59 total=1\nfull avg10=0.12 avg60=0.62 avg300=0.57 total=2\n")
        self.assertEqual(psi["some_avg10"], 1.5)
        self.assertEqual(psi["full_avg10"], 0.12)

    def test_meminfo(self):
        mem = probe.parse_meminfo("MemTotal:  8000 kB\nMemAvailable:   2000 kB\n")
        self.assertEqual(mem, {"MemTotal": 8000, "MemAvailable": 2000})


class KillTests(unittest.TestCase):
    def test_kills_live_process_and_skips_reused_pid(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            start = probe.start_time(child.pid)
            stale = probe.kill_group({"id": "x", "pids": [[child.pid, start + 1]]}, "TERM")
            self.assertEqual(stale["sent"], 0, "a pid whose start time changed is never signalled")
            result = probe.kill_group({"id": "x", "pids": [[child.pid, start]]}, "TERM")
            self.assertEqual(result["sent"], 1)
            self.assertEqual(child.wait(timeout=5), -signal.SIGTERM)
        finally:
            if child.poll() is None:
                child.kill()

    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())

    def test_probe_streams_and_exits_with_stdin(self):
        p = start_probe(self.state)
        p.stdin.write("detail 1\n")
        p.stdin.flush()
        seen = set()
        deadline = time.time() + 5
        while time.time() < deadline and seen != {"summary", "apps"}:
            line = p.stdout.readline()
            if '"type":"summary"' in line:
                seen.add("summary")
            if '"type":"apps"' in line:
                seen.add("apps")
        self.assertEqual(seen, {"summary", "apps"})
        p.stdin.close()
        self.assertEqual(p.wait(timeout=5), 0)
        p.stdout.close()


class DemoTests(unittest.TestCase):
    def test_scenes_load(self):
        for name in ("green", "yellow", "red", "history"):
            scene = probe.load_demo(name)
            self.assertIsNotNone(scene, name)
            self.assertGreater(scene["summary"]["used"], 0)
            self.assertTrue(all(a["id"].startswith("demo:") for a in scene["apps"]))
        self.assertIsNone(probe.load_demo("nope"))

    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())

    def test_history_scene_seeds_a_synthetic_demo_history(self):
        scene = probe.load_demo("history")
        history = raman_history.History("demo")
        now = 1791063600.0
        self.assertEqual(probe.seed_demo_history(history, scene, now), now, "the fake clock ends at now")
        hour = history.query("1h", now)
        self.assertEqual([g["reason"] for g in hour["gaps"]], ["suspend"])
        self.assertIn(True, hour["breaks"])
        self.assertIn(None, hour["series"]["psiSome10"]["mean"], "unreadable PSI stays null")
        self.assertEqual(max(v for v in hour["series"]["psiSome10"]["max"] if v is not None), 31.0)
        incidents = hour["incidents"]
        self.assertEqual([i["id"] for i in incidents], ["demo-1", "demo-2", "demo-3"])
        self.assertEqual([i["severity"] for i in incidents], ["warn", "warn", "critical"])
        for incident in incidents:
            self.assertLess(incident["start"], incident["end"])
            self.assertGreaterEqual(incident["measurement"]["psiSome10"],
                                    5 if incident["severity"] == "warn" else 20,
                                    "each synthetic incident starts at a reading past its threshold")
        self.assertEqual(incidents[0]["context"], {"status": "not-observed"})
        observed = incidents[2]["context"]
        self.assertEqual(observed["status"], "observed")
        self.assertEqual(observed["at"], incidents[2]["start"] - 2)
        self.assertEqual(len(observed["apps"]), 5)
        self.assertEqual(set(observed["apps"][0]), {"name", "kb"}, "only whitelisted fields")
        self.assertEqual(history.query("5m", now)["t"][-1], now)
        # Live scenes without a timeline keep the old behavior.
        plain = raman_history.History("demo")
        self.assertEqual(probe.seed_demo_history(plain, probe.load_demo("red"), now), now)
        self.assertEqual(plain.raw, [])

    def test_history_scene_stays_in_demo_and_is_never_saved(self):
        p = start_probe(self.state)
        try:
            send(p, "demo history", {"command": "history.get", "requestId": "d1", "window": "1h"})
            demo = answer(p, "d1")[-1]
            self.assertTrue(demo["demo"])
            self.assertEqual(len(demo["incidents"]), 3)
            self.assertGreater(len(demo["t"]), 300, "the timeline fills the hour")
            send(p, "demo off", {"command": "history.get", "requestId": "l1", "window": "1h"})
            live = answer(p, "l1")[-1]
            self.assertFalse(live["demo"])
            self.assertEqual(live["incidents"], [], "synthetic incidents never reach live history")
            self.assertEqual(live["gaps"], [])
            self.assertLess(len(live["t"]), 10)
        finally:
            p.stdin.close()
            p.wait(timeout=5)
            p.stdout.close()
        with open(os.path.join(self.state, "raman", "history.json")) as handle:
            saved = json.load(handle)
        self.assertEqual(saved["incidents"], [], "demo incidents are never persisted")
        self.assertEqual(saved["gaps"], [])

    def test_demo_kill_only_drops_the_fake_row(self):
        p = start_probe(self.state)
        try:
            p.stdin.write("demo red\ndetail 1\nkill KILL demo:0\n")
            p.stdin.flush()
            killed = after = None
            deadline = time.time() + 5
            while time.time() < deadline and after is None:
                message = json.loads(p.stdout.readline())
                if message["type"] == "killed":
                    killed = message
                elif message["type"] == "apps" and killed:
                    after = [a["id"] for a in message["apps"]]
            self.assertEqual(killed["sent"], 1)
            self.assertNotIn("demo:0", after)
            self.assertIn("demo:1", after)
        finally:
            p.stdin.close()
            p.wait(timeout=5)
            p.stdout.close()

    def test_demo_history_is_separate_and_never_saved(self):
        p = start_probe(self.state)
        try:
            red = probe.load_demo("red")["summary"]
            send(p, "demo red")
            read_until(p, lambda m: m["type"] == "summary" and m["used"] == red["used"])
            send(p, {"command": "history.get", "requestId": "d1", "window": "5m"})
            demo = answer(p, "d1")[-1]
            self.assertTrue(demo["demo"])
            self.assertEqual(demo["series"]["used"]["last"][-1], red["used"])
            send(p, {"command": "history.clear", "requestId": "d2"}, "demo off",
                 {"command": "history.get", "requestId": "l1", "window": "5m"})
            cleared = answer(p, "d2")[-1]
            self.assertEqual((cleared["demo"], cleared["persisted"]), (True, False))
            live = answer(p, "l1")[-1]
            self.assertFalse(live["demo"])
            self.assertGreaterEqual(len(live["t"]), 2, "clearing the demo left live history alone")
            self.assertNotIn(red["used"], live["series"]["used"]["max"], "no demo readings in live history")
        finally:
            p.stdin.close()
            p.wait(timeout=5)
            p.stdout.close()
        with open(os.path.join(self.state, "raman", "history.json")) as handle:
            saved = json.load(handle)
        self.assertNotIn(red["used"], [b[6][0] and b[6][0][1] for b in saved["rings"]["10"]])
        self.assertFalse(os.path.exists(os.path.join(self.state, "raman", "cleared.json")),
                         "a demo clear never touches production state")


class HistoryProtocolTests(unittest.TestCase):
    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())
        self.p = start_probe(self.state)

    def tearDown(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        elif not self.p.stdin.closed:
            self.p.stdin.close()
        self.p.stdout.close()

    def send(self, *lines):
        send(self.p, *lines)

    def answer(self, request_id):
        return answer(self.p, request_id)

    def test_history_reply_envelope_and_bounds(self):
        for window in ("5m", "1h", "24h"):
            self.send({"command": "history.get", "requestId": window, "window": window})
            message = self.answer(window)[-1]
            self.assertEqual(message["type"], "history")
            self.assertEqual(message["schemaVersion"], 1)
            self.assertIsInstance(message["time"], float)
            self.assertEqual(message["window"], window)
            self.assertLessEqual(len(message["t"]), 360)
            self.assertEqual(message["units"]["used"], "kB")
            self.assertEqual(message["units"]["psiSome10"], "%")
            self.assertTrue(message["persistence"]["owner"])

    def test_typed_errors_echo_request_ids(self):
        self.send({"command": "history.get", "requestId": "a", "window": "2h"},
                  {"command": "history.nuke", "requestId": 7},
                  {"command": "history.get", "requestId": ["x"], "window": "1h"},
                  {"command": "history.get", "requestId": "w", "window": ["1h"]},
                  {"command": "history.get", "requestId": 10 ** 30, "window": "1h"},
                  "{not json")
        messages = read_until(self.p, lambda m: m.get("code") == "bad-json")
        errors = [(m["code"], m["requestId"]) for m in messages if m["type"] == "error"]
        self.assertEqual(errors, [("bad-window", "a"), ("unknown-command", 7), ("bad-request", None),
                                  ("bad-window", "w"), ("bad-request", None), ("bad-json", None)])

    def test_deeply_nested_command_does_not_stop_the_probe(self):
        # The deepest nesting that fits in 4 KiB; RecursionError is caught too, in case a
        # Python's JSON parser limits depth lower than 3.12/3.14 do.
        self.send('{"a":' + "[" * 2000 + "]" * 2000 + "}",
                  {"command": "history.get", "requestId": "after", "window": "5m"})
        self.assertEqual(self.answer("after")[-1]["type"], "history")
        self.assertIsNone(self.p.poll())

    def test_oversized_command_is_rejected_and_probe_keeps_working(self):
        self.send('{"command":"history.get","requestId":"big","pad":"' + "x" * 10000 + '"}')
        self.send({"command": "history.get", "requestId": "after", "window": "5m"})
        messages = self.answer("after")
        self.assertIn("too-large", [m.get("code") for m in messages])
        self.assertNotIn("big", [m.get("requestId") for m in messages])

    def test_history_commands_never_start_a_process_scan(self):
        # The panel is closed (no "detail 1"): history work must not scan /proc/<pid>.
        self.send({"command": "history.get", "requestId": "g", "window": "24h"},
                  {"command": "history.clear", "requestId": "c"},
                  "refresh")
        messages = self.answer("c")
        messages += read_until(self.p, lambda m: m["type"] == "summary")
        self.assertNotIn("apps", [m["type"] for m in messages])
        self.assertIsNone(self.p.poll(), "probe still running")

    def test_clear_reply_and_private_state_on_exit(self):
        self.send({"command": "history.clear", "requestId": "c1"})
        cleared = self.answer("c1")[-1]
        self.assertEqual((cleared["type"], cleared["persisted"], cleared["demo"]), ("history-cleared", True, False))
        started = time.time()
        self.p.stdin.close()
        self.assertEqual(self.p.wait(timeout=5), 0)
        self.assertLess(time.time() - started, 2, "exits promptly when stdin closes")
        directory = os.path.join(self.state, "raman")
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        for name in ("history.json", "cleared.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(directory, name)).st_mode), 0o600, name)

    def test_sigterm_still_saves(self):
        # Summary 2 is emitted after tick 1 made this probe the history writer.
        self.send("refresh")
        read_until(self.p, lambda m: m["type"] == "summary" and m["seq"] >= 2)
        self.p.terminate()
        self.assertEqual(self.p.wait(timeout=5), 0)
        self.assertTrue(os.path.exists(os.path.join(self.state, "raman", "history.json")))

    def writer_released(self, timeout=5):
        """True once nothing holds history.lock: the worker has saved and exited."""
        lock = os.path.join(self.state, "raman", "history.lock")
        deadline = time.time() + timeout
        while time.time() < deadline:
            fd = os.open(lock, os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                time.sleep(0.05)
            finally:
                os.close(fd)
        return False

    def collect(self, count):
        """Wait for count summaries, asking for each one (the probe is the writer after the first)."""
        for seq in range(2, count + 1):
            self.send("refresh")
            read_until(self.p, lambda m, seq=seq: m["type"] == "summary" and m["seq"] >= seq)

    def saved_samples(self):
        with open(os.path.join(self.state, "raman", "history.json")) as handle:
            return sum(bucket[3] for bucket in json.load(handle)["rings"]["10"])

    def test_quickshell_teardown_still_saves(self):
        # Quickshell 0.3.1's ~Process() calls QProcess::kill(): SIGKILL to the pid it started,
        # whose pipes close once that pid is reaped. Nothing in the killed process can run.
        self.collect(3)
        self.assertFalse(os.path.exists(os.path.join(self.state, "raman", "history.json")),
                         "no periodic save yet: only the shutdown save can keep these readings")
        self.p.kill()
        self.p.wait(timeout=5)
        self.p.stdin.close()
        self.p.stdout.close()
        self.assertTrue(self.writer_released(), "the worker saved and exited")
        self.assertGreaterEqual(self.saved_samples(), 3, "pre-restart readings were saved")

    def test_killed_parent_is_noticed_even_if_stdin_stays_open(self):
        self.collect(3)
        self.p.kill()
        self.p.wait(timeout=5)
        self.assertTrue(self.writer_released(), "the worker noticed its parent was gone")
        self.assertGreaterEqual(self.saved_samples(), 3)

    def test_summaries_are_numbered(self):
        self.send("refresh")
        messages = read_until(self.p, lambda m: m["type"] == "summary" and m.get("seq", 0) >= 2)
        seqs = [m["seq"] for m in messages if m["type"] == "summary"]
        self.assertEqual(seqs, sorted(seqs))
        self.assertIn("psiSome10", messages[-1], "legacy summary fields are unchanged")


class HandlerTests(unittest.TestCase):
    def test_history_commands_never_call_scan_or_kill(self):
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        state = probe.ProbeState(probe.raman_history.Persistence(os.path.join(state_home, "raman")))
        state.history.ingest({"used": 1}, time.time(), time.monotonic())
        commands = [{"command": "history.get", "requestId": w, "window": w} for w in ("5m", "1h", "24h")]
        commands.append({"command": "history.clear", "requestId": "c"})
        forbidden = AssertionError("closed-panel history work touched processes")
        with unittest.mock.patch.object(probe, "scan", side_effect=forbidden), \
                unittest.mock.patch.object(probe, "kill_group", side_effect=forbidden), \
                unittest.mock.patch.object(probe.os, "kill", side_effect=forbidden):
            replies = [probe.handle_json(json.dumps(c), state) for c in commands]
        self.assertEqual([r["type"] for r in replies], ["history"] * 3 + ["history-cleared"])


class IncidentProtocolTests(unittest.TestCase):
    def test_deferred_start_and_session_only_have_no_history_file_or_hidden_apps(self):
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        env = dict(os.environ, XDG_STATE_HOME=state_home, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.Popen([sys.executable, probe.__file__, "--defer-history"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True, env=env)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        try:
            first = read_until(p, lambda m: m["type"] == "summary")[-1]
            self.assertIn("dispatch", first)
            self.assertFalse(os.path.exists(os.path.join(state_home, "raman", "history.json")))
            send(p, {"command": "history.configure", "requestId": "c", "enabled": True, "persist": False}, "refresh")
            answer(p, "c")
            sample = read_until(p, lambda m: m["type"] == "summary")[-1]
            send(p, {"command": "incident.record", "requestId": "event", "summarySeq": sample["seq"],
                     "severity": "critical", "reason": "used", "epoch": sample["dispatch"]["epoch"], "notify": False})
            messages = answer(p, "event")
            ack = messages[-1]
            self.assertEqual(ack["type"], "incident")
            self.assertFalse(ack["persisted"])
            self.assertEqual(ack["incident"]["context"], {"status": "not-observed"})
            self.assertNotIn("apps", [m["type"] for m in messages])
            self.assertFalse(os.path.exists(os.path.join(state_home, "raman", "history.json")))
        finally:
            p.stdin.close()
            p.wait(timeout=5)
            p.stdout.close()
        self.assertEqual(p.returncode, 0)


class SampleTests(unittest.TestCase):
    def test_unreadable_zram_stays_null_in_history_and_summary(self):
        # PR review: an unreadable mm_stat was recorded as a measured 0.
        with unittest.mock.patch.object(probe.os, "listdir", return_value=["zram0", "sda"]), \
                unittest.mock.patch.object(probe, "read_text", return_value=""):
            self.assertIsNone(probe.zram_used_kb())
        with unittest.mock.patch.object(probe.os, "listdir", side_effect=OSError("no /sys")):
            self.assertIsNone(probe.zram_used_kb())
        with unittest.mock.patch.object(probe.os, "listdir", return_value=["sda"]):
            self.assertEqual(probe.zram_used_kb(), 0, "no zram device is a real zero")
        with unittest.mock.patch.object(probe.os, "listdir", return_value=["zram0", "zram1"]), \
                unittest.mock.patch.object(probe, "read_text", return_value="1 2 2048 4\n"):
            self.assertEqual(probe.zram_used_kb(), 4, "mem_used_total bytes summed as kB")
        sample = {k: 1 for k in ("total", "available", "used", "swapTotal", "swapUsed", "cached",
                                 "psiSome10", "psiSome60", "psiFull10")}
        sample["zramRam"] = None
        self.assertIsNone(probe.summary(sample)["zramRam"])
        history = probe.raman_history.History()
        history.ingest(sample, 1000.0, 1.0)
        self.assertIsNone(history.query("5m", 1000.0)["series"]["zramRam"]["last"][0])

    def test_summary_preserves_missing_sources(self):
        empty = {k: None for k in ("total", "available", "used", "swapTotal", "swapUsed",
                                   "psiSome10", "psiSome60", "psiFull10")}
        empty.update(cached=0, zramRam=0)
        message = probe.summary(empty)
        self.assertEqual((message["total"], message["used"], message["psiSome10"]), (None, None, None))


if __name__ == "__main__":
    unittest.main()
