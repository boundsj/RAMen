"""raman_history: aggregation, continuity, persistence and bounds.

Pure and clock-injected, so these run anywhere (no /proc needed).
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import raman_history as rh  # noqa: E402

GB = 1024 * 1024


def reading(used=4 * GB, psi=1.0, total=8 * GB, **extra):
    values = {"used": used, "available": None if used is None else total - used, "total": total,
              "swapUsed": 0, "swapTotal": 2 * GB, "zramRam": 0, "psiSome10": psi, "psiFull10": 0.0}
    values.update(extra)
    return values


class Clock:
    """Fake wall/monotonic/boot clocks advancing together unless told otherwise."""

    def __init__(self, wall=1_000_000.0):
        self.wall, self.mono, self.boot = wall, 100.0, 100.0

    def step(self, seconds, wall=None, boot=None):
        self.mono += seconds
        self.wall += seconds if wall is None else wall
        self.boot += seconds if boot is None else boot


def feed(history, clock, values, seq=None, boot=True):
    history.ingest(values, clock.wall, clock.mono, clock.boot if boot else None, seq)


def series(result, metric, stat_name):
    return result["series"][metric][stat_name]


class AggregationTests(unittest.TestCase):
    def test_hand_calculated_bucket(self):
        h = rh.History()
        c = Clock(wall=1000.0)
        # (seconds since previous sample, used kB); None = unreadable.
        steps = [(0, 100), (2, 200), (3, 50), (1, None), (2, 300)]
        for delta, used in steps:
            c.step(delta)
            feed(h, c, {"used": used})
        c.step(2)
        feed(h, c, {"used": 999})  # 1010: next bucket
        result = h.query("1h", c.wall)
        self.assertEqual(result["t"][:2], [1000, 1010])
        self.assertEqual(series(result, "used", "min")[0], 50)
        self.assertEqual(series(result, "used", "max")[0], 300)
        self.assertEqual(series(result, "used", "last")[0], 300)
        # Weights: 2 (first sample, nominal), 2, 3, (1 s unreadable), 2.
        # (100*2 + 200*2 + 50*3 + 300*2) / 9 = 1350 / 9 = 150
        self.assertEqual(series(result, "used", "mean")[0], 150)
        self.assertEqual(result["count"][0], 5)
        self.assertEqual(result["covered"][0], 10.0)

    def test_unreadable_metric_stays_null(self):
        h = rh.History()
        c = Clock(wall=2000.0)
        for _ in range(5):
            feed(h, c, reading(psi=None))
            c.step(2)
        result = h.query("5m", c.wall)
        self.assertEqual(series(result, "psiSome10", "max"), [None] * 5)
        self.assertEqual(series(result, "used", "max")[0], 4 * GB)
        hour = h.query("1h", c.wall)
        self.assertIsNone(series(hour, "psiSome10", "mean")[0], "never a zero-valued healthy interval")

    def test_24_hours_downsampled_to_360_points_exactly(self):
        h = rh.History()
        c = Clock(wall=240 * 10_000.0)
        # Four minutes at 100/200/300/400 kB, sampled every 2 s, then a long steady tail.
        for minute in range(4):
            for _ in range(30):
                feed(h, c, {"used": (minute + 1) * 100})
                c.step(2)
        result = h.query("24h", c.wall)
        self.assertEqual(result["resolution"], 240)
        self.assertEqual(series(result, "used", "min")[0], 100)
        self.assertEqual(series(result, "used", "max")[0], 400)
        self.assertEqual(series(result, "used", "mean")[0], 250, "equal weights: mean of the four minutes")
        self.assertEqual(series(result, "used", "last")[0], 400)
        self.assertEqual(result["count"][0], 120)
        # A full day at the slowest contiguous cadence never exceeds the page bound.
        while c.wall < 240 * 10_000.0 + 86_400 + 600:
            feed(h, c, {"used": 1})
            c.step(6)
        for window, expected in (("5m", 50), ("1h", 360), ("24h", 360)):
            points = h.query(window, c.wall)
            self.assertEqual(len(points["t"]), expected, window)
            self.assertEqual(len(points["t"]), len(series(points, "used", "mean")))
        self.assertLessEqual(len(h.rings[60].buckets), 1440)
        self.assertLessEqual(len(h.rings[10].buckets), 360)
        self.assertLessEqual(len(h.raw), 150)

    def test_raw_window_keeps_sequence_numbers(self):
        h = rh.History()
        c = Clock()
        for seq in range(1, 4):
            feed(h, c, reading(), seq=seq)
            c.step(2)
        self.assertEqual(h.query("5m", c.wall)["seq"], [1, 2, 3])
        self.assertEqual(h.sample_by_seq(2)["v"]["used"], 4 * GB)
        self.assertIsNone(h.sample_by_seq(99))


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.h = rh.History()
        self.c = Clock()
        for _ in range(3):
            feed(self.h, self.c, reading())
            self.c.step(2)

    def gap_reasons(self):
        return [g["reason"] for g in self.h.gaps]

    def test_steady_sampling_has_no_gaps(self):
        self.assertEqual(self.h.gaps, [])
        self.assertEqual(self.h.query("5m", self.c.wall)["breaks"], [False, False, False])

    def test_missed_ticks_break_the_line(self):
        self.c.step(20)
        feed(self.h, self.c, reading())
        self.assertEqual(self.gap_reasons(), ["stall"])
        self.assertEqual(self.h.query("5m", self.c.wall)["breaks"][-1], True)

    def test_suspend_uses_the_boot_clock(self):
        self.c.step(2, wall=602, boot=602)  # monotonic stood still for 10 minutes
        feed(self.h, self.c, reading())
        gap = self.h.gaps[-1]
        self.assertEqual(gap["reason"], "suspend")
        self.assertAlmostEqual(gap["end"] - gap["start"], 604, msg="from the previous sample")
        hour = self.h.query("1h", self.c.wall)
        self.assertTrue(hour["breaks"][-1])
        self.assertTrue(all(not (gap["start"] < t < gap["end"] - 10) for t in hour["t"]), "no invented points")

    def test_forward_wall_step_without_boot_clock(self):
        self.c.step(2, wall=3600)
        feed(self.h, self.c, reading(), boot=False)
        self.assertEqual(self.gap_reasons(), ["clock"])

    def test_clock_rollback_drops_future_data(self):
        newest = self.c.wall
        self.c.step(2, wall=-120)
        feed(self.h, self.c, reading(used=GB))
        self.assertEqual(self.gap_reasons(), ["clock"])
        self.assertTrue(all(s["t"] <= self.c.wall for s in self.h.raw))
        self.assertLess(self.h.newest(), newest)
        for ring in self.h.rings.values():
            starts = [b["t"] for b in ring.buckets]
            self.assertEqual(starts, sorted(set(starts)), "buckets stay strictly ordered")
        self.assertEqual(series(self.h.query("5m", self.c.wall), "used", "last")[-1], GB)

    def test_refresh_bursts_are_time_weighted(self):
        h = rh.History()
        c = Clock(wall=5000.0)
        feed(h, c, {"used": 0})
        c.step(0.5)  # manual refresh half a second later
        feed(h, c, {"used": 1000})
        c.step(2)
        feed(h, c, {"used": 1000})
        # (0*2 + 1000*0.5 + 1000*2) / 4.5
        self.assertEqual(series(h.query("1h", c.wall), "used", "mean")[0], round(2500 / 4.5))


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.h = rh.History()
        self.c = Clock()
        for seq in range(1, 61):
            feed(self.h, self.c, reading(), seq=seq)
            self.c.step(2)

    def test_unknown_sequence_and_enums_are_rejected(self):
        with self.assertRaises(ValueError):
            self.h.record_incident(9999, "critical", "pressure")
        with self.assertRaises(ValueError):
            self.h.record_incident(60, "panic", "pressure")

    def test_bounds_and_whitelist(self):
        apps = [{"name": "Chrome\x00\x1b[31m" + "x" * 400, "kb": 3 * GB, "argv": ["--token=secret"],
                 "env": {"HOME": "/home/me"}, "pids": [[1, 2]]} for _ in range(8)]
        incident = self.h.record_incident(60, "critical", "pressure",
                                          {"status": "observed", "at": self.c.wall, "apps": apps})
        context = incident["context"]
        self.assertEqual(len(context["apps"]), rh.MAX_INCIDENT_APPS)
        self.assertEqual(set(context["apps"][0]), {"name", "kb"}, "no argv, environment or pids")
        self.assertEqual(len(context["apps"][0]["name"]), rh.MAX_NAME)
        self.assertTrue(context["apps"][0]["name"].startswith("Chrome[31m"), "control characters stripped")
        self.assertEqual(incident["measurement"]["used"], 4 * GB, "measurement comes from the stored sample")
        self.assertEqual(self.h.record_incident(59, "warn", "used")["context"], {"status": "not-observed"})

    def test_at_most_fifty_incidents(self):
        for seq in range(1, 61):
            self.h.record_incident(seq, "warn", "used")
        self.assertEqual(len(self.h.incidents), rh.MAX_INCIDENTS)
        self.assertEqual(self.h.incidents[0]["summarySeq"], 11, "oldest dropped first")

    def test_incidents_expire_after_24_hours(self):
        self.h.record_incident(60, "warn", "used")
        self.c.step(2, wall=rh.RETENTION + 10)
        feed(self.h, self.c, reading())
        self.assertEqual(self.h.incidents, [])


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.dir = os.path.join(self.root, "raman")
        self.c = Clock()

    def probe(self, boot="boot-a", **kwargs):
        return rh.History(boot), rh.Persistence(self.dir, **kwargs)

    def run_ticks(self, history, store, count, values=None):
        for _ in range(count):
            feed(history, self.c, values or reading())
            store.tick(history, self.c.wall, self.c.mono)
            self.c.step(2)

    def saved(self):
        with open(os.path.join(self.dir, "history.json")) as handle:
            return json.load(handle)

    def test_private_atomic_round_trip(self):
        os.makedirs(self.dir, mode=0o755)
        os.chmod(self.dir, 0o755)
        h, store = self.probe()
        self.run_ticks(h, store, 35)  # 70 s: one periodic save
        self.assertTrue(store.owner)
        self.assertEqual(store.save_status, "saved")
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "history.json")).st_mode), 0o600)
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [], "no temp files left")
        store.flush(h, self.c.wall, self.c.mono)
        before = h.query("1h", self.c.wall)
        store.release()

        h2, store2 = self.probe()
        self.c.step(4)
        self.run_ticks(h2, store2, 1)
        self.assertEqual(store2.load_status, "ok")
        after = h2.query("1h", self.c.wall)
        self.assertEqual(after["t"][:len(before["t"])], before["t"])
        self.assertEqual(series(after, "used", "max")[:3], series(before, "used", "max")[:3])
        self.assertEqual(h2.gaps, [], "a restart within a few seconds is continuous")

    def test_saves_at_most_once_a_minute(self):
        h, store = self.probe()
        writes = []
        real = rh.write_private
        with mock.patch.object(rh, "write_private", side_effect=lambda *a: (writes.append(a[1]), real(*a))):
            self.run_ticks(h, store, 90)  # 180 s of samples
        history_writes = [p for p in writes if p.endswith("history.json")]
        self.assertEqual(len(history_writes), 2, "after 60 s and 120 s; nothing per tick")

    def test_restart_and_reboot_leave_gaps_not_lines(self):
        h, store = self.probe(boot="boot-a")
        self.run_ticks(h, store, 5)
        store.flush(h, self.c.wall, self.c.mono)
        store.release()

        self.c.step(300)
        h2, store2 = self.probe(boot="boot-a")
        self.run_ticks(h2, store2, 2)
        self.assertEqual([g["reason"] for g in h2.gaps], ["restart"])
        store2.flush(h2, self.c.wall, self.c.mono)
        store2.release()

        self.c.step(900)
        h3, store3 = self.probe(boot="boot-b")
        self.run_ticks(h3, store3, 2)
        self.assertEqual([g["reason"] for g in h3.gaps], ["restart", "reboot"])
        result = h3.query("1h", self.c.wall)
        self.assertEqual(sum(result["breaks"]), 2)

    def test_adopting_before_the_first_sample_still_marks_downtime(self):
        h, store = self.probe(boot="boot-a")
        self.run_ticks(h, store, 5)
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        for boot, reason in (("boot-a", "restart"), ("boot-b", "reboot")):
            state, status = rh.read_state(os.path.join(self.dir, "history.json"))  # adopt consumes it
            self.assertEqual(status, "ok")
            later = Clock(wall=self.c.wall + 120)  # a new process, two minutes on
            fresh = rh.History(boot)
            fresh.adopt(state, later.wall)
            feed(fresh, later, reading())
            self.assertEqual([g["reason"] for g in fresh.gaps], [reason])
            self.assertTrue(fresh.query("1h", later.wall)["breaks"][-1])

    def test_restored_history_is_not_replayed_as_new(self):
        h, store = self.probe()
        self.run_ticks(h, store, 3)
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        h2, store2 = self.probe()
        self.run_ticks(h2, store2, 1)
        self.assertEqual(len(h2.raw), 1, "only this process's samples are recent raw readings")

    def test_corrupt_oversized_and_incompatible_files_start_empty(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "history.json")
        cases = {
            "corrupt": b"{not json",
            "oversized": b" " * 5000,
            "incompatible": json.dumps({"format": rh.FILE_FORMAT, "schemaVersion": 99}).encode(),
        }
        for status, data in cases.items():
            with open(path, "wb") as handle:
                handle.write(data)
            h, store = self.probe(max_bytes=4096)
            self.run_ticks(h, store, 2)
            self.assertEqual(store.load_status, status)
            self.assertEqual(len(h.query("5m", self.c.wall)["t"]), 2, "live history still works")
            store.release()
        good = rh.History().to_state(1.0)
        good["rings"]["10"] = [[1, 2, 3]]
        with open(path, "w") as handle:
            json.dump(good, handle)
        self.assertEqual(rh.read_state(path)[1], "corrupt", "malformed buckets are rejected")

    def test_failed_writes_keep_live_history(self):
        h, store = self.probe()
        with mock.patch.object(rh, "write_private", side_effect=OSError(28, "No space left on device")):
            self.run_ticks(h, store, 35)
        self.assertEqual(store.save_status, "error")
        self.assertIn("No space", store.error)
        self.assertEqual(len(h.raw), 35)
        self.run_ticks(h, store, 31)  # next attempt one interval later succeeds
        self.assertEqual(store.save_status, "saved")

    def test_output_bounded_by_compacting_oldest(self):
        h, store = self.probe(max_bytes=20_000)
        for _ in range(2000):
            feed(h, self.c, reading())
            self.c.step(6)
        oldest = h.rings[60].buckets[0]["t"]
        store.tick(h, self.c.wall, self.c.mono)
        store.flush(h, self.c.wall, self.c.mono)
        self.assertLessEqual(os.path.getsize(os.path.join(self.dir, "history.json")), 20_000)
        self.assertGreater(self.saved()["rings"]["60"][0][0], oldest)

    def test_single_writer_and_takeover(self):
        h1, writer = self.probe()
        self.run_ticks(h1, writer, 60, reading(used=GB))  # two minutes alone
        h2, follower = self.probe()
        for _ in range(35):
            for h, store, used in ((h1, writer, GB), (h2, follower, 2 * GB)):
                feed(h, self.c, reading(used=used))
                store.tick(h, self.c.wall, self.c.mono)
            self.c.step(2)
        self.assertTrue(writer.owner)
        self.assertFalse(follower.owner)
        self.assertEqual(follower.save_status, "pending", "a follower never writes")
        store_before = h2.rings[60].buckets[0]["a"]  # the follower's first sample
        writer.flush(h1, self.c.wall, self.c.mono)
        writer.release()  # e.g. the old probe exits after a shell reload
        self.run_ticks(h2, follower, 1, reading(used=2 * GB))
        self.assertTrue(follower.owner)
        self.assertEqual(follower.load_status, "ok")
        self.assertEqual(h2.gaps, [], "both probes sampled the same span: no gap")
        hour = h2.query("1h", self.c.wall)
        maxima = series(hour, "used", "max")
        adopted = [m for t, m in zip(hour["t"], maxima) if t + 10 <= store_before]
        self.assertGreaterEqual(len(adopted), 10, "the writer's earlier minutes were adopted")
        self.assertEqual(set(adopted), {GB})
        self.assertEqual(maxima[-1], 2 * GB, "from the takeover on, the new writer's samples win")
        starts = hour["t"]
        self.assertEqual(starts, sorted(set(starts)))

    def test_clear_from_any_probe_clears_the_file(self):
        h1, writer = self.probe()
        h2, follower = self.probe()
        for _ in range(35):
            for h, store in ((h1, writer), (h2, follower)):
                feed(h, self.c, reading(used=GB))
                store.tick(h, self.c.wall, self.c.mono)
            self.c.step(2)
        self.assertTrue(self.saved()["rings"]["10"])
        self.c.step(1)
        self.assertTrue(follower.clear(h2, self.c.wall, self.c.mono))
        self.c.step(1)
        feed(h1, self.c, reading(used=2 * GB))
        writer.tick(h1, self.c.wall, self.c.mono)
        saved = self.saved()
        used_max = [bucket[6][0][1] for bucket in saved["rings"]["10"]]
        self.assertEqual(used_max, [2 * GB], "only data observed after the clear survives")
        self.assertEqual(h2.query("1h", self.c.wall)["t"], [])
        # Live sampling continues after a clear.
        feed(h2, self.c, reading())
        self.assertEqual(len(h2.query("5m", self.c.wall)["t"]), 1)

    def test_clear_reaches_a_file_the_old_writer_never_rewrote(self):
        h1, writer = self.probe()
        self.run_ticks(h1, writer, 35)
        h2, follower = self.probe()
        self.run_ticks(h2, follower, 1)
        follower.clear(h2, self.c.wall, self.c.mono)
        writer.release()  # exits before noticing the clear
        self.c.step(2)
        self.run_ticks(h2, follower, 1)
        self.assertTrue(follower.owner)
        self.assertEqual(len(h2.query("1h", self.c.wall)["t"]), 1)

    def test_consumed_remote_clear_retries_durable_erasure_after_failed_write(self):
        h1, writer = self.probe()
        self.run_ticks(h1, writer, 35)
        h2, follower = self.probe()
        self.run_ticks(h2, follower, 1)
        follower.clear(h2, self.c.wall, self.c.mono)
        before = self.saved()
        self.assertTrue(writer.follow_clears(h1), "probe consumes marker before tick")
        self.assertFalse(writer.follow_clears(h1), "duplicate observation is harmless")
        with mock.patch.object(rh, "write_private", side_effect=OSError("test denied")):
            writer.tick(h1, self.c.wall, self.c.mono)
        self.assertEqual(writer.save_status, "error")
        self.assertEqual(self.saved(), before, "failed disk replacement remains reported")
        self.c.step(2)
        writer.tick(h1, self.c.wall, self.c.mono)
        self.assertEqual(writer.save_status, "saved")
        self.assertEqual(self.saved()["rings"]["10"], [])
        self.assertFalse(writer._clear_pending)

    def test_local_clear_failed_save_retries_before_minute_deadline(self):
        h, writer = self.probe()
        self.run_ticks(h, writer, 35)
        real = rh.write_private
        def deny_history(directory, path, data):
            if path.endswith("history.json"):
                raise OSError("test denied")
            return real(directory, path, data)
        with mock.patch.object(rh, "write_private", side_effect=deny_history):
            self.assertFalse(writer.clear(h, self.c.wall, self.c.mono))
        self.assertTrue(writer._clear_pending)
        self.c.step(2)
        writer.tick(h, self.c.wall, self.c.mono)
        self.assertEqual(self.saved()["rings"]["10"], [])
        self.assertFalse(writer._clear_pending)

    def test_clear_survives_the_old_writers_final_save(self):
        h1, writer = self.probe()
        self.run_ticks(h1, writer, 35)
        h2, follower = self.probe()
        self.run_ticks(h2, follower, 1)
        follower.clear(h2, self.c.wall, self.c.mono)
        self.c.step(1)
        writer.flush(h1, self.c.wall, self.c.mono)  # stdin closed before its next tick
        writer.release()
        self.assertEqual(self.saved()["rings"]["10"], [], "the exit save applied the clear")
        self.c.step(1)
        self.run_ticks(h2, follower, 1)
        self.assertTrue(follower.owner)
        self.assertEqual(len(h2.query("1h", self.c.wall)["t"]), 1)

    def test_adopting_a_file_older_than_the_clear_keeps_its_boot_id(self):
        h1, writer = self.probe(boot="boot-a")
        self.run_ticks(h1, writer, 35)
        writer.flush(h1, self.c.wall, self.c.mono)
        self.c.step(5)
        cleared_at = self.c.wall
        h0, other = self.probe(boot="boot-b")
        other.clear(h0, cleared_at, self.c.mono)  # old writer holds lock: marker only
        writer.release()
        with open(os.path.join(self.dir, "history.json")) as handle:
            stale = json.load(handle)
        stale["rings"]["10"][-1][1] = cleared_at + 1  # one bucket first observed after the clear
        stale["rings"]["10"][-1][2] = cleared_at + 1
        with open(os.path.join(self.dir, "history.json"), "w") as handle:
            json.dump(stale, handle)
        self.c.step(600)
        h2, store2 = self.probe(boot="boot-b")
        self.run_ticks(h2, store2, 1)
        self.assertEqual([g["reason"] for g in h2.gaps], ["reboot"], "saved boot ID survives the filter")
        self.assertEqual(len(h2.rings[10].buckets), 2)

    def test_restart_inside_one_bucket_still_breaks_the_line(self):
        self.c = Clock(wall=60 * 30_000.0)
        h, store = self.probe()
        self.run_ticks(h, store, 3)  # samples at :00, :02, :04
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        self.c.step(30)  # back at :36, same 60 s slot
        h2, store2 = self.probe()
        self.run_ticks(h2, store2, 1)
        self.assertEqual([g["reason"] for g in h2.gaps], ["restart"])
        self.assertEqual(len(h2.rings[60].buckets), 1, "saved tail merged into the new bucket")
        self.assertTrue(h2.query("24h", self.c.wall)["breaks"][-1])
        self.assertTrue(h2.query("1h", self.c.wall)["breaks"][-1])

    def test_clock_behind_saved_history_after_restart(self):
        h, store = self.probe()
        self.run_ticks(h, store, 35)
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        self.c.step(5, wall=-1800)  # restarted with the wall clock 30 minutes earlier
        h2, store2 = self.probe()
        self.run_ticks(h2, store2, 1)
        self.assertEqual([g["reason"] for g in h2.gaps], ["clock"])
        self.assertTrue(all(b["z"] <= self.c.wall for b in h2.rings[10].buckets), "future data dropped")
        self.assertTrue(h2.query("1h", self.c.wall)["breaks"][-1])

    def test_takeover_mid_slot_while_the_writer_kept_sampling(self):
        self.c = Clock(wall=10_000.0)
        h1, writer = self.probe()
        self.run_ticks(h1, writer, 13)  # writer alone through 10_024; follower starts at 10_026
        h2, follower = self.probe()
        for _ in range(20):
            for h, store in ((h1, writer), (h2, follower)):
                feed(h, self.c, reading())
                store.tick(h, self.c.wall, self.c.mono)
            self.c.step(2)
        writer.flush(h1, self.c.wall, self.c.mono)
        writer.release()
        self.run_ticks(h2, follower, 1)
        self.assertTrue(follower.owner)
        self.assertEqual(h2.gaps, [], "the saved bucket spanning our start shows no downtime")
        hour = h2.query("1h", self.c.wall)
        self.assertFalse(any(hour["breaks"]))
        counts = dict(zip(hour["t"], hour["count"]))
        # The saved 10_020 bucket overlaps ours, so ours replaces it rather than double-counting.
        self.assertEqual(counts[10_020], 2, "only our 10_026 and 10_028 samples")
        self.assertEqual(counts[10_010], 5, "the writer's earlier bucket was adopted whole")

    def test_takeover_after_a_clock_rollback_keeps_buckets_unique(self):
        # PR review: both probes roll back from 1000 to 800; the saved [800..820] buckets
        # must not be prepended to the follower's identical ones.
        h1, writer = self.probe()
        h2, follower = self.probe()
        self.c = Clock(wall=1000.0)
        for back in (False, True):
            if back:
                self.c.step(2, wall=-240)
            for _ in range(10):
                for h, store in ((h1, writer), (h2, follower)):
                    feed(h, self.c, reading())
                    store.tick(h, self.c.wall, self.c.mono)
                self.c.step(2)
        writer.flush(h1, self.c.wall, self.c.mono)
        writer.release()
        self.run_ticks(h2, follower, 1)
        self.assertTrue(follower.owner)
        for ring in h2.rings.values():
            starts = [b["t"] for b in ring.buckets]
            self.assertEqual(starts, sorted(set(starts)), "unique, ordered buckets")
        follower.flush(h2, self.c.wall, self.c.mono)
        self.assertEqual(rh.read_state(follower.path)[1], "ok", "the saved file stays loadable")

    def test_takeover_boundary_uses_the_saved_data_that_was_kept(self):
        # PR review: the writer stalls 1010-1060 while the follower starts at 1030.
        self.c = Clock(wall=1000.0)
        h1, writer = self.probe()
        h2, follower = self.probe()
        for wall in range(1000, 1066, 2):
            self.c.wall = self.c.mono = self.c.boot = float(wall)
            if wall <= 1010 or wall >= 1060:
                feed(h1, self.c, reading())
                writer.tick(h1, self.c.wall, self.c.mono)
            if wall >= 1030:
                feed(h2, self.c, reading())
                follower.tick(h2, self.c.wall, self.c.mono)
        writer.flush(h1, 1064.0, 1064.0)
        writer.release()
        self.c.wall = self.c.mono = self.c.boot = 1066.0
        self.run_ticks(h2, follower, 1)
        self.assertEqual(h2.gaps, [{"start": 1010, "end": 1030, "reason": "stall"}],
                         "the writer's gap is clipped at the follower's first sample")
        hour = h2.query("1h", self.c.wall)
        self.assertEqual(dict(zip(hour["t"], hour["breaks"]))[1030], True)

    def test_unreadable_file_is_not_overwritten(self):
        h, store = self.probe()
        self.run_ticks(h, store, 35)
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        path = os.path.join(self.dir, "history.json")
        with open(path, "rb") as handle:
            original = handle.read()
        h2, store2 = self.probe()
        with mock.patch.object(rh, "read_state", return_value=(None, "unreadable")):
            self.run_ticks(h2, store2, 40)
        self.assertFalse(store2.owner)
        self.assertEqual(store2.load_status, "unreadable")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), original, "24 h of saved history kept")
        self.assertEqual(len(h2.raw), 40, "live history unaffected")
        self.run_ticks(h2, store2, 31)  # readable again: takes over a minute later
        self.assertTrue(store2.owner)
        self.assertEqual(store2.load_status, "ok")

    def test_hostile_state_and_marker_files_never_raise(self):
        os.makedirs(self.dir)
        path = os.path.join(self.dir, "history.json")
        with open(path, "w") as handle:
            handle.write("[" * 2_000_000)  # deeper than any JSON recursion limit, under 2 MiB
        with open(os.path.join(self.dir, "cleared.json"), "w") as handle:
            handle.write("[" * 3000)
        h, store = self.probe()
        self.run_ticks(h, store, 2)
        self.assertEqual(store.load_status, "corrupt")
        self.assertEqual(len(h.raw), 2)
        for mean, weight, status in ((1e200, 1e200, "corrupt"), (float("nan"), 1, "corrupt"), (5, 1, "corrupt"),
                                     (10 ** 400, 1, "corrupt")):
            state = rh.History().to_state(1.0)
            metrics = [None] * len(rh.METRICS)
            metrics[0] = [1, 2, 2, 1, mean, weight]  # mean 5 is outside [min 1, max 2]
            state["rings"]["10"] = [[10, 10, 10, 1, 2, 0, metrics]]
            with open(path, "w") as handle:
                handle.write(json.dumps(state))
            self.assertEqual(rh.read_state(path)[1], status, (mean, weight))

    def test_huge_integers_are_corruption_and_saving_resumes(self):
        # PR review: math.isfinite(10**400) raised OverflowError past read_state.
        os.makedirs(self.dir)
        state = rh.History().to_state(1.0)
        state["gaps"] = [[10 ** 400, 1, "stall"]]
        with open(os.path.join(self.dir, "history.json"), "w") as handle:
            json.dump(state, handle)
        h, store = self.probe()
        self.run_ticks(h, store, 35)
        self.assertEqual(store.load_status, "corrupt")
        self.assertTrue(store.owner)
        self.assertEqual(store.save_status, "saved", "the empty-state recovery saves new history")

    def test_owner_clear_rewrites_immediately(self):
        h, store = self.probe()
        self.run_ticks(h, store, 35)
        self.assertTrue(store.clear(h, self.c.wall, self.c.mono))
        self.assertEqual(self.saved()["rings"], {"10": [], "60": []})
        self.assertTrue(os.path.exists(os.path.join(self.dir, "cleared.json")))

    def test_incident_ids_survive_persistence(self):
        h, store = self.probe()
        for seq in range(1, 4):
            feed(h, self.c, reading(), seq=seq)
            store.tick(h, self.c.wall, self.c.mono)
            self.c.step(2)
        incident = h.record_incident(3, "critical", "pressure", {"status": "observed", "at": self.c.wall,
                                                                  "apps": [{"name": "Chrome", "kb": GB}]})
        store.flush(h, self.c.wall, self.c.mono)
        store.release()
        h2, store2 = self.probe()
        self.run_ticks(h2, store2, 1)
        self.assertEqual([i["id"] for i in h2.incidents], [incident["id"]])
        self.assertEqual(h2.incidents[0]["context"]["apps"], [{"name": "Chrome", "kb": GB}])

    def test_unwritable_state_dir_is_reported_not_raised(self):
        blocker = os.path.join(self.root, "file")
        with open(blocker, "w") as handle:
            handle.write("x")
        h, store = rh.History(), rh.Persistence(os.path.join(blocker, "raman"))
        self.run_ticks(h, store, 3)
        self.assertEqual(store.save_status, "error")
        self.assertEqual(len(h.raw), 3)


class EnvironmentTests(unittest.TestCase):
    def test_state_dir_follows_xdg(self):
        self.assertEqual(rh.state_dir({"XDG_STATE_HOME": "/x/state", "HOME": "/home/u"}), "/x/state/raman")
        self.assertEqual(rh.state_dir({"HOME": "/home/u"}), "/home/u/.local/state/raman")
        self.assertEqual(rh.state_dir({"XDG_STATE_HOME": "relative", "HOME": "/home/u"}),
                         "/home/u/.local/state/raman", "relative XDG paths are ignored")


if __name__ == "__main__":
    unittest.main()
