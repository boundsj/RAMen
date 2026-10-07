"""Incident commands use stored samples and existing snapshots, never processes."""
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

import raman_history as history
import raman_probe as probe


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(history.Persistence(os.path.join(self.tmp, "raman")))
        self.addCleanup(self.state.dispatch.release)
        self.addCleanup(lambda: self.state.persistence.release())
        self.wall, self.mono = time.time(), time.monotonic()
        self.state.persistence.tick(self.state.history, self.wall, self.mono)
        self.state.dispatch.tick(self.mono)
        for seq in range(1, 8):
            self.state.history.ingest({"total": 100, "used": 80, "available": 20, "psiSome10": None},
                                      self.wall + seq * 2, self.mono + seq * 2, seq=seq)

    def command(self, **changes):
        command = dict(command="incident.record", requestId="r1", summarySeq=6, startSeq=1,
                       key="event-1", action="start", severity="warn", reason="used", notify=True,
                       epoch=self.state.dispatch.epoch,
                       thresholds={"warnPercent": 75, "criticalPercent": 90, "warnPressure": 5, "criticalPressure": 20})
        command.update(changes)
        return probe.handle_json(json.dumps(command), self.state)

    def test_acknowledgement_persisted_before_reply_and_deduplication(self):
        ack = self.command()
        self.assertTrue(ack["persisted"])
        saved, status = history.read_state(self.state.persistence.path)
        self.assertEqual(saved.incidents[0]["id"], ack["incident"]["id"])
        self.assertEqual(saved.incidents[0]["status"], "interrupted", "restored active is never live")
        self.assertIsNone(ack["incident"]["measurement"]["psiSome10"])
        self.assertFalse(self.command()["notify"], "lost/duplicate request cannot reserve twice")
        self.assertEqual(len(self.state.history.incidents), 1)

    def test_extend_upgrade_recover_same_receipt_and_round_trip(self):
        self.command()
        with patch.object(self.state.persistence, "checkpoint", side_effect=AssertionError("per-tick write")):
            self.assertFalse(self.command(action="extend", summarySeq=7, notify=False)["persisted"])
        upgrade = self.command(action="upgrade", severity="critical", summarySeq=7)
        self.assertTrue(upgrade["notify"])
        self.assertEqual(upgrade["incident"]["upgrade"]["at"], self.wall + 14)
        self.assertEqual(self.command(action="upgrade", severity="critical")["code"], "bad-upgrade")
        end = self.command(action="recover", summarySeq=7, notify=False)
        self.assertEqual(end["incident"]["status"], "recovered")
        self.assertEqual(end["incident"]["end"], self.wall + 14)
        self.assertEqual(len(self.state.history.incidents), 1)
        saved, _ = history.read_state(self.state.persistence.path)
        self.assertEqual(saved.incidents[0]["thresholds"]["warnPercent"], 75)
        self.assertIn("upgrade", saved.incidents[0])

    def test_interrupt_after_raw_reference_expired_uses_last_observation(self):
        self.command()
        self.state.history.raw = []
        ack = self.command(action="interrupt", notify=False)
        self.assertEqual(ack["incident"]["status"], "interrupted")
        self.assertEqual(ack["incident"]["end"], self.wall + 12)
        self.assertTrue(ack["persisted"])

    def test_unsaved_ack_survives_write_failure(self):
        with patch.object(self.state.persistence, "_save", side_effect=OSError("test denied")):
            ack = self.command()
        self.assertTrue(ack["notify"])
        self.assertFalse(ack["persisted"])
        self.assertEqual(ack["error"], "test denied")
        self.assertEqual(len(self.state.history.incidents), 1)

    def test_fresh_stale_absent_context_and_no_hidden_scan_or_signal(self):
        apps = [{"name": "Synthetic Browser", "pss": 5, "swap": 3, "pids": [999], "argv": "private"}]
        self.state.detail_snapshot = (self.mono, self.wall, apps)
        forbidden = AssertionError("incident touched processes")
        with patch.object(probe, "scan", side_effect=forbidden), patch.object(probe, "kill_group", side_effect=forbidden), patch.object(probe.os, "kill", side_effect=forbidden):
            self.assertEqual(self.command()["incident"]["context"], {"status": "not-observed"})
            self.state.detail_active = True
            with patch.object(probe.time, "monotonic", return_value=self.mono + 5):
                ctx = self.command(key="fresh")["incident"]["context"]
            self.assertEqual(ctx, {"status": "observed", "at": self.wall, "apps": [{"name": "Synthetic Browser", "kb": 8}]})
            with patch.object(probe.time, "monotonic", return_value=self.mono + 5.01):
                self.assertEqual(self.command(key="stale")["incident"]["context"], {"status": "not-observed"})

    def test_session_only_disabled_and_explicit_erase(self):
        self.command()
        self.state.configure(True, False)
        before = Path(self.state.persistence.path).read_bytes()
        self.assertEqual(self.state.history.incidents, [])
        with patch.object(history, "read_state", side_effect=AssertionError("loaded production")):
            self.state.history.ingest({"total": 100, "used": 90}, self.wall, self.mono, seq=1)
            self.assertFalse(self.command(summarySeq=1)["persisted"])
        self.assertEqual(Path(self.state.persistence.path).read_bytes(), before)
        self.state.configure(False, False)
        self.assertEqual(self.command()["code"], "history-disabled")
        reply = probe.handle_json('{"command":"history.clear"}', self.state)
        self.assertTrue(reply["persisted"])
        saved, _ = history.read_state(self.state.persistence.path)
        self.assertEqual(saved.incidents, [])
        self.assertIsNone(saved.newest())

    def test_demo_and_invalid_epoch_seq_enums(self):
        for changes, code in [({"summarySeq": True}, "bad-seq"), ({"summarySeq": 999}, "unknown-seq"),
                              ({"epoch": "old"}, "not-owner"), ({"severity": "ok"}, "bad-incident"),
                              ({"key": "a" * 65}, "bad-incident")]:
            self.assertEqual(self.command(**changes)["code"], code)
        self.state.start_demo(probe.load_demo("history"))
        self.assertEqual(self.command()["code"], "demo-only")
        self.assertTrue(self.state.demo_history.incidents, "only the isolated canned receipts are present")

    def test_dispatch_owner_takeover_and_cooldown_without_history_persistence(self):
        follower = history.DispatchLease(self.state.persistence.directory, self.state.history.boot_id)
        self.addCleanup(follower.release)
        follower.tick(self.mono)
        self.assertFalse(follower.owner)
        self.assertTrue(self.state.dispatch.claim("critical"))
        self.state.dispatch.release()
        follower.tick(self.mono + 3)
        self.assertTrue(follower.owner)
        self.assertTrue(follower.blocked, "takeover waits for recovery, never restored toast")
        self.assertIsNotNone(follower.info(self.mono)["noticeElapsed"]["critical"])
        self.assertNotEqual(follower.epoch, self.state.dispatch.epoch)
        self.assertEqual(os.stat(self.state.persistence.directory).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(follower.path).st_mode & 0o777, 0o600)

    def test_upgrade_notification_context_is_fresh_and_receipt_context_stays_historical(self):
        self.state.detail_active = True
        self.state.detail_snapshot = (self.mono, self.wall, [{"name": "Old Browser", "pss": 1024, "swap": 0}])
        with patch.object(probe.time, "monotonic", return_value=self.mono):
            started = self.command()
        self.assertEqual(started["notificationContext"]["apps"][0]["name"], "Old Browser")
        for t in range(16, 136, 2):
            self.state.history.ingest({"total": 100, "used": 95, "psiSome10": 25},
                                      self.wall + t, self.mono + t, seq=t)
            self.command(action="extend", summarySeq=t, notify=False)
        self.state.detail_active = False
        self.state.detail_snapshot = None
        with patch.object(probe.time, "monotonic", return_value=self.mono + 134), \
             patch.object(probe, "scan", side_effect=AssertionError("hidden detail scan")):
            upgraded = self.command(action="upgrade", severity="critical", reason="pressure", summarySeq=134)
        self.assertEqual(upgraded["incident"]["context"]["apps"][0]["name"], "Old Browser")
        self.assertEqual(upgraded["notificationContext"], {"status": "not-observed"})
        # Another warning/upgrade while a subscribed fresh snapshot is available.
        self.state.detail_active = True
        self.state.detail_snapshot = (self.mono + 134, self.wall + 134,
                                      [{"name": "Fresh Editor", "pss": 2048, "swap": 0}])
        self.command(key="fresh-upgrade", summarySeq=134, startSeq=134, notify=False)
        with patch.object(probe.time, "monotonic", return_value=self.mono + 139):
            fresh = self.command(key="fresh-upgrade", action="upgrade", severity="critical", summarySeq=134)
        self.assertEqual(fresh["notificationContext"]["apps"][0]["name"], "Fresh Editor")
        self.state.detail_snapshot = (self.mono, self.wall, [{"name": "Stale", "pss": 1, "swap": 0}])
        self.command(key="stale-upgrade", summarySeq=134, startSeq=134, notify=False)
        with patch.object(probe.time, "monotonic", return_value=self.mono + 139):
            stale = self.command(key="stale-upgrade", action="upgrade", severity="critical", summarySeq=134)
        self.assertEqual(stale["notificationContext"], {"status": "not-observed"})

    def test_active_receipt_overlaps_every_window_and_survives_retention_and_compaction(self):
        h = history.History("test")
        values = {"total": 100, "used": 80, "psiSome10": 6}
        h.ingest(values, 1000, 0, seq=1)
        inc = h.record_incident(1, "warn", "pressure")
        inc.update(status="active", lastAt=1000)
        for t in range(2, history.RETENTION + 4, 2):
            h.ingest(values, 1000 + t, t, seq=t + 1)
            h.update_incident(inc, h.sample_by_seq(t + 1), "extend", "warn", "pressure")
            if t in (320, 3602, history.RETENTION + 2):
                for window in history.WINDOWS:
                    self.assertEqual([i["id"] for i in h.query(window, 1000 + t)["incidents"]], [inc["id"]])
        for n in range(history.MAX_INCIDENTS + 5):
            closed = h.record_incident(history.RETENTION + 3, "warn", "used")
            h.update_incident(closed, h.raw[-1], "recover", "warn", "used")
        self.assertEqual(len(h.incidents), history.MAX_INCIDENTS)
        self.assertIn(inc, h.incidents, "closed receipts are evicted before the live lifecycle")
        while h.compact():
            self.assertIn(inc, h.incidents)
        self.assertEqual(h.incidents, [inc])
        h.update_incident(inc, h.raw[-1], "upgrade", "critical", "pressure")
        h.update_incident(inc, h.raw[-1], "recover", "critical", "pressure")
        h._prune(inc["end"] + history.RETENTION + 1)
        self.assertEqual(h.incidents, [], "closed receipt expires normally")

    def test_long_incident_expires_payload_but_preserves_identity_and_transitions(self):
        h = history.History("test")
        values = {"total": 100, "used": 80, "available": 20, "psiSome10": 6}
        h.ingest(values, 1000, 0, seq=1)
        inc = h.record_incident(1, "warn", "pressure", {"status": "observed", "at": 998,
                                "apps": [{"name": "Private old app", "kb": 1024}]})
        inc.update(status="active", lastAt=1000, thresholds={"warnPercent": 75})
        identity = inc["id"]
        # Actual continuous 25-hour incident, with independently timed upgrade.
        for t in range(2, 90002, 2):
            h.ingest(values, 1000 + t, t, seq=t + 1)
            h.update_incident(inc, h.raw[-1], "upgrade" if t == 3600 else "extend",
                              "critical" if t >= 3600 else "warn", "pressure")
            if t == history.RETENTION - 2:
                self.assertEqual(inc["context"], {"status": "expired"}, "context expires by observation time")
                self.assertEqual(inc["measurement"]["used"], 80, "start evidence still within horizon")
        encoded = history.encode_state(h, 91000)
        self.assertNotIn(b"Private old app", encoded)
        self.assertEqual(inc["id"], identity)
        self.assertEqual(inc["start"], 1000)
        self.assertEqual(inc["measurementStatus"], "expired")
        self.assertTrue(all(v is None for v in inc["measurement"].values()))
        self.assertNotIn("thresholds", inc)
        self.assertIsNone(inc["summarySeq"])
        self.assertEqual(inc["upgrade"]["measurementStatus"], "expired")
        for window in history.WINDOWS:
            self.assertEqual([i["id"] for i in h.query(window, 91000)["incidents"]], [identity])
        restored = history.parse_state(json.loads(encoded)).incidents[0]
        self.assertEqual(restored["status"], "interrupted")
        self.assertEqual(restored["context"], {"status": "expired"})
        self.assertEqual(restored["measurementStatus"], "expired")
        h.update_incident(inc, h.raw[-1], "recover", "critical", "pressure")
        self.assertEqual(inc["status"], "recovered")
        h._prune(inc["end"] + history.RETENTION)
        self.assertEqual(h.incidents, [])

    def test_expiry_on_query_save_and_adoption_without_new_samples(self):
        for boundary in ("query", "save", "adopt"):
            with self.subTest(boundary=boundary):
                h = history.History("test")
                h.ingest({"used": 80, "total": 100}, 1000, 0, seq=1)
                inc = h.record_incident(1, "warn", "used", {"status": "observed", "at": 1000,
                                        "apps": [{"name": "Expired secret", "kb": 1}]})
                h.update_incident(inc, h.raw[-1], "recover", "warn", "used")
                inc["end"] = 1002  # end remains inside horizon as start expires
                now = 1000 + history.RETENTION
                if boundary == "query":
                    data = h.query("24h", now)["incidents"][0]
                elif boundary == "save":
                    data = json.loads(history.encode_state(h, now))["incidents"][0]
                else:
                    live = history.History("test")
                    live.adopt(h, now)
                    data = live.incidents[0]
                self.assertEqual(data["context"], {"status": "expired"})
                self.assertEqual(data["measurementStatus"], "expired")
                self.assertTrue(all(v is None for v in data["measurement"].values()))

    def test_expired_start_accepts_fresh_upgrade_and_closure_through_protocol(self):
        h = history.History("test")
        h.ingest({"total": 100, "used": 80}, 1000, 0, seq=1)
        inc = h.record_incident(1, "warn", "used")
        inc.update(id="event-1", status="active", lastAt=1000)
        h.ingest({"total": 100, "used": 95}, 91000, 90000, seq=2)
        self.state.history = h
        with patch.object(probe.time, "time", return_value=91000):
            upgraded = self.command(action="upgrade", severity="critical", summarySeq=2, notify=False)
            self.assertEqual(upgraded["type"], "incident")
            self.assertEqual(upgraded["incident"]["measurementStatus"], "expired")
            self.assertEqual(upgraded["incident"]["upgrade"]["measurement"]["used"], 95)
            self.assertTrue(upgraded["persisted"])
            ended = self.command(action="recover", severity="critical", summarySeq=2, notify=False)
        self.assertEqual(ended["incident"]["id"], "event-1")
        self.assertEqual(ended["incident"]["status"], "recovered")
        saved, _ = history.read_state(self.state.persistence.path)
        self.assertEqual(saved.incidents[0]["measurementStatus"], "expired")
        self.assertEqual(saved.incidents[0]["upgrade"]["measurement"]["used"], 95)

    def test_dispatch_lock_tracks_boot_instead_of_path_existence(self):
        directory = os.path.join(self.tmp, "boot-test")
        a = history.DispatchLease(directory, "boot-a")
        b = history.DispatchLease(directory, "boot-a")
        c = history.DispatchLease(directory, "boot-b")
        for lease in (a, b, c):
            self.addCleanup(lease.release)
        a.tick(0)
        self.assertFalse(a.blocked)
        a.release()
        b.tick(2)
        self.assertTrue(b.blocked, "same-boot continuity is suppressed even without a toast")
        b.release()
        c.tick(4)
        self.assertFalse(c.blocked, "prior boot lock is not an ongoing episode")

    def test_fixed_qa_scenes_recover_before_entering_elevated_state(self):
        scenes = runpy.run_path(str(Path(probe.__file__).parent / "scripts/qa-synthetic-probe.py"))
        at = scenes["scene_at"]
        for scene in ("warning", "critical", "missing-psi"):
            self.assertEqual(at(scene, 0, 12), "healthy")
            self.assertEqual(at(scene, 10, 12), "healthy")
            self.assertEqual(at(scene, 12, 12), scene)
            self.assertEqual(at(scene, 1000, 12), scene)


class SyntheticProtocolTests(unittest.TestCase):
    """Real pipe ordering/erase-marker tests, usable on macOS as well as Linux."""

    def setUp(self):
        self.root = Path(probe.__file__).parent
        artifacts = self.root / ".agent-artifacts" / "fix-h3-r2" / "protocol-tests"
        artifacts.mkdir(parents=True, exist_ok=True)
        self.home = self.enterContext(tempfile.TemporaryDirectory(dir=artifacts))

    def start(self):
        from test_probe import read_until
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_STATE_HOME=self.home,
                   RAMAN_QA_STATE_HOME=self.home, RAMAN_QA_SCENE="healthy")
        child = subprocess.Popen([sys.executable, str(self.root / "scripts/qa-synthetic-probe.py"), "--defer-history"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        def cleanup():
            child.stdin.close()
            self.assertEqual(child.wait(timeout=5), 0)
            child.stdout.close()
        self.addCleanup(cleanup)
        pre = read_until(child, lambda m: m["type"] == "summary")[-1]
        self.assertIsNone(pre["historyConfigId"])
        return child, pre

    def configure(self, child, request, persist=False):
        from test_probe import send, answer, read_until
        send(child, dict(command="history.configure", requestId=request, enabled=True, persist=persist), "refresh")
        self.assertEqual(answer(child, request)[-1]["type"], "history-configured")
        return read_until(child, lambda m: m["type"] == "summary" and m["historyConfigId"] == request)[-1]

    def test_deferred_samples_and_settings_transitions_have_matching_generation(self):
        from test_probe import send, answer
        child, pre = self.start()
        first = self.configure(child, "config:1")
        send(child, dict(command="history.get", requestId="h1", window="5m"))
        raw = answer(child, "h1")[-1]
        self.assertNotIn(pre["seq"], raw["seq"], "buffered deferred summary never entered history")
        self.assertIn(first["seq"], raw["seq"])
        second = self.configure(child, "config:2", persist=True)
        send(child, dict(command="history.get", requestId="h2", window="5m"))
        raw = answer(child, "h2")[-1]
        self.assertNotIn(first["seq"], raw["seq"], "new storage session resets raw references")
        self.assertIn(second["seq"], raw["seq"])

    def test_session_only_follower_observes_remote_clear_and_drops_live_receipt(self):
        from test_probe import send, answer, read_until
        a, _ = self.start()
        b, _ = self.start()
        self.configure(a, "a")
        sample = self.configure(b, "b")
        owner = b if sample["dispatchOwner"] else a
        other = a if owner is b else b
        if owner is a:
            send(a, "refresh")
            sample = read_until(a, lambda m: m["type"] == "summary")[-1]
        send(owner, dict(command="incident.record", requestId="start", action="start", key="before-clear",
                         summarySeq=sample["seq"], severity="warn", reason="used",
                         epoch=sample["dispatch"]["epoch"], notify=False))
        self.assertEqual(answer(owner, "start")[-1]["type"], "incident")
        send(other, dict(command="history.clear", requestId="clear"))
        cleared = answer(other, "clear")[-1]
        self.assertTrue(cleared["persisted"])
        send(owner, "refresh")
        messages = read_until(owner, lambda m: m["type"] == "summary" and m["time"] > cleared["time"])
        self.assertTrue(any(m.get("remote") is True and m["type"] == "history-cleared" for m in messages))
        send(owner, dict(command="history.get", requestId="after", window="5m"))
        data = answer(owner, "after")[-1]
        self.assertTrue(all(t >= cleared["time"] for t in data["t"]))
        self.assertEqual(data["incidents"], [])
        self.assertFalse((Path(self.home) / "raman/history.json").read_text().find("before-clear") >= 0)

    def test_persisted_writer_erases_remote_clear_on_next_tick_not_minute_deadline(self):
        from test_probe import send, answer, read_until
        writer, _ = self.start()
        sample = self.configure(writer, "writer", persist=True)
        follower, _ = self.start()
        self.configure(follower, "follower", persist=True)
        send(writer, dict(command="incident.record", requestId="record", action="start", key="pre-clear-private",
                         summarySeq=sample["seq"], severity="warn", reason="used", notify=False,
                         epoch=sample["dispatch"]["epoch"]))
        self.assertTrue(answer(writer, "record")[-1]["persisted"])
        path = Path(self.home) / "raman/history.json"
        before = path.read_bytes()
        self.assertIn(b"pre-clear-private", before)
        send(follower, dict(command="history.clear", requestId="erase"))
        cleared = answer(follower, "erase")[-1]
        self.assertTrue(cleared["persisted"])
        send(writer, "refresh")
        messages = read_until(writer, lambda m: m["type"] == "summary" and m["time"] > cleared["time"])
        self.assertTrue(any(m.get("remote") for m in messages))
        saved = json.loads(path.read_bytes())
        self.assertNotEqual(path.read_bytes(), before)
        self.assertEqual(saved["incidents"], [], "durable erase must precede normal minute save deadline")
        self.assertEqual(saved["clearToken"], json.loads((path.parent / "cleared.json").read_text())["token"])
        self.assertTrue(all(b[1] >= cleared["time"] for ring in saved["rings"].values() for b in ring))

    @unittest.skipUnless(shutil.which("pgrep"), "optional restart discovery requires pgrep (procps on Linux)")
    def test_restart_checker_discovers_actual_supported_entrypoints(self):
        from test_probe import read_until
        checker = runpy.run_path(str(self.root / "scripts/check-restart-history.py"))
        wrapper, _ = self.start()
        self.assertIn(str(wrapper.pid), checker["probes"](), "QA wrapper with --defer-history")
        for flags in ([], ["--defer-history"]):
            child = subprocess.Popen([sys.executable, str(self.root / "raman_probe.py"), *flags],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_STATE_HOME=self.home))
            try:
                read_until(child, lambda m: m["type"] == "summary")
                self.assertIn(str(child.pid), checker["probes"](), "actual legacy/deferred production argv")
            finally:
                child.stdin.close()
                self.assertEqual(child.wait(timeout=5), 0)
                child.stdout.close()


if __name__ == "__main__":
    unittest.main()
