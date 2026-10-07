"""A3: DRM fdinfo GPU attribution (raman_gpu.py) and its probe/query wiring.

Fixture tests build a fake sysfs (/sys/class/drm nodes, device links, runtime
PM status, amdgpu VRAM files) and a fake /proc (stat, fd symlinks, fdinfo
texts) in a temporary directory, so they run on any platform. The fdinfo
texts follow the kernel's documented formats (drm-usage-stats.rst, i915 and xe
examples, amdgpu_fdinfo.c). Live tests start the real probe on Linux with
test-owned children only; no DRM hardware is assumed, and none is touched.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import raman_apps as apps  # noqa: E402
import raman_gpu as gpu  # noqa: E402
import raman_history  # noqa: E402
import raman_probe as probe  # noqa: E402
from test_probe import answer, read_until, send, start_probe  # noqa: E402

AMD_PDEV, INTEL_PDEV, NV_PDEV = "0000:03:00.0", "0000:00:02.0", "0000:01:00.0"

# Key formats from the kernel's documentation and drivers; values are RAMen's own.
I915_SHAPE = """pos:    0
flags:  0100002
mnt_id: 31
drm-driver: i915
drm-pdev:   0000:00:02.0
drm-client-id:      5
drm-engine-render:  7340012345 ns
drm-engine-copy:    1048576000 ns
drm-engine-video:   0 ns
drm-engine-capacity-video:   2
drm-engine-video-enhance:   0 ns
"""

XE_SHAPE = """pos:    0
flags:  0100002
mnt_id: 33
ino:    904
drm-driver:     xe
drm-client-id:  4
drm-pdev:       0000:03:00.0
drm-total-system:       0
drm-shared-system:      0
drm-active-system:      0
drm-resident-system:    0
drm-purgeable-system:   0
drm-total-gtt:  384 KiB
drm-shared-gtt: 0
drm-active-gtt: 0
drm-resident-gtt:       384 KiB
drm-total-vram0:        51200 KiB
drm-shared-vram0:       8 MiB
drm-active-vram0:       0
drm-resident-vram0:     51200 KiB
drm-total-stolen:       0
drm-shared-stolen:      0
drm-active-stolen:      0
drm-resident-stolen:    0
drm-cycles-rcs: 12345678
drm-total-cycles-rcs:   9000000000
drm-cycles-bcs: 0
drm-total-cycles-bcs:   9000000000
drm-cycles-vcs: 0
drm-total-cycles-vcs:   9000000000
drm-engine-capacity-vcs:        2
drm-cycles-vecs:        0
drm-total-cycles-vecs:  9000000000
drm-engine-capacity-vecs:       2
drm-cycles-ccs: 0
drm-total-cycles-ccs:   9000000000
drm-engine-capacity-ccs:        4
"""


def amdgpu(client, vram_resident_kib, vram_total_kib=None, gtt_kib=0, vram_shared_kib=0, gfx_ns=None,
           pdev=AMD_PDEV, name_line=""):
    """amdgpu_fdinfo.c output: drm_print_memory_stats per placement, the deprecated
    drm-memory-* aliases, amd-* keys and drm-engine-* only for used IPs."""
    total = vram_resident_kib if vram_total_kib is None else vram_total_kib
    lines = ["pos:\t0", "flags:\t02100002", "mnt_id:\t24", "ino:\t1062", "drm-driver:\tamdgpu",
             "drm-client-id:\t%d" % client, "drm-pdev:\t%s" % pdev, name_line, "pasid:\t32771"]
    for region, resident, alloc, shared in (("vram", vram_resident_kib, total, vram_shared_kib),
                                            ("gtt", gtt_kib, gtt_kib, 0), ("cpu", 0, 0, 0)):
        lines += ["drm-total-%s:\t%d KiB" % (region, alloc), "drm-shared-%s:\t%d KiB" % (region, shared),
                  "drm-resident-%s:\t%d KiB" % (region, resident), "drm-purgeable-%s:\t0" % region]
    lines += ["drm-memory-vram:\t%d KiB" % vram_resident_kib, "drm-memory-gtt: \t%d KiB" % gtt_kib,
              "drm-memory-cpu: \t0 KiB", "amd-evicted-vram:\t0 KiB", "amd-requested-vram:\t%d KiB" % total,
              "amd-requested-gtt:\t%d KiB" % gtt_kib]
    if gfx_ns is not None:
        lines.append("drm-engine-gfx:\t%d ns" % gfx_ns)
    return "\n".join(line for line in lines if line) + "\n"


def legacy_amdgpu(client, vram_kib, pdev=AMD_PDEV):
    """Linux v6.8 amdgpu_show_fdinfo after drm_show_fdinfo: drm-memory-* only, no
    drm-resident-*/drm-shared-* keys, so whether clients share buffers is unknown."""
    return ("drm-driver:\tamdgpu\ndrm-client-id:\t%d\ndrm-pdev:\t%s\npasid:\t32770\n"
            "drm-memory-vram:\t%d KiB\ndrm-memory-gtt: \t0 KiB\ndrm-memory-cpu: \t0 KiB\n"
            "amd-memory-visible-vram:\t0 KiB\namd-evicted-vram:\t0 KiB\namd-evicted-visible-vram:\t0 KiB\n"
            "amd-requested-vram:\t%d KiB\namd-requested-visible-vram:\t0 KiB\namd-requested-gtt:\t0 KiB\n"
            % (client, pdev, vram_kib, vram_kib))


class FakeGpuSystem:
    """A fake sysfs + procfs pair. Devices map DRM nodes to a bus device; processes hold fd links."""

    def __init__(self, root):
        self.sys = os.path.join(root, "sys")
        self.proc = os.path.join(root, "proc")
        os.makedirs(os.path.join(self.sys, "class", "drm"))
        os.makedirs(self.proc)
        self.numbers = {}  # node name -> "226:minor"
        self.fdinfo_reads = []

    def device(self, pdev, driver, nodes, runtime="active", vram_total=None, vram_used=None, subsystem="pci",
               vendor="0x1002", device_id="0x73bf"):
        path = os.path.join(self.sys, "devices", "pci0000:00", pdev)
        os.makedirs(os.path.join(path, "power"), exist_ok=True)
        os.makedirs(os.path.join(self.sys, "bus", subsystem, "drivers", driver), exist_ok=True)
        if not os.path.islink(os.path.join(path, "subsystem")):
            os.symlink(os.path.join(self.sys, "bus", subsystem), os.path.join(path, "subsystem"))
            os.symlink(os.path.join(self.sys, "bus", subsystem, "drivers", driver), os.path.join(path, "driver"))
        self.write(os.path.join(path, "vendor"), vendor)
        self.write(os.path.join(path, "device"), device_id)
        if runtime is not None:
            self.set_runtime(pdev, runtime)
        if vram_total is not None:
            self.write(os.path.join(path, "mem_info_vram_total"), str(vram_total))
            self.write(os.path.join(path, "mem_info_vram_used"), str(vram_used or 0))
        for name, minor in nodes:
            node = os.path.join(self.sys, "class", "drm", name)
            os.makedirs(node)
            self.write(os.path.join(node, "dev"), "226:%d" % minor)
            os.symlink(path, os.path.join(node, "device"))
            self.numbers[name] = "226:%d" % minor
        return path

    def set_runtime(self, pdev, status):
        self.write(os.path.join(self.sys, "devices", "pci0000:00", pdev, "power", "runtime_status"), status)

    def process(self, pid, start=None, fds=None):
        """fds: {fd number: (node name or other target, fdinfo text or None)}."""
        directory = os.path.join(self.proc, str(pid))
        os.makedirs(os.path.join(directory, "fd"), exist_ok=True)
        os.makedirs(os.path.join(directory, "fdinfo"), exist_ok=True)
        start = pid * 10 if start is None else start
        rest = ["S", 1, pid, pid, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 1, 0, start, 0, 0]
        self.write(os.path.join(directory, "stat"), "%d (proc) %s" % (pid, " ".join(str(v) for v in rest)))
        for fd, (target, text) in (fds or {}).items():
            link = os.path.join(directory, "fd", str(fd))
            os.symlink(target if target.startswith("/") else "/dev/dri/" + target, link)
            if text is not None:
                self.write(os.path.join(directory, "fdinfo", str(fd)), text)
        return (pid, start)

    @staticmethod
    def write(path, text):
        with open(path, "w") as handle:
            handle.write(text + ("" if text.endswith("\n") else "\n"))

    def device_of(self, dir_fd, name):
        target = os.readlink(name, dir_fd=dir_fd)
        return self.numbers.get(os.path.basename(target))

    def collect(self, targets, **kwargs):
        original = gpu._read_fdinfo

        def spy(dir_fd, name):
            self.fdinfo_reads.append(name)
            return original(dir_fd, name)

        with unittest.mock.patch.object(gpu, "_read_fdinfo", spy):
            return gpu.collect(targets, self.proc, self.sys, {}, device_of=self.device_of, **kwargs)


class Clock:
    def __init__(self):
        self.mono = 1000.0
        self.boot = 5000.0

    def __call__(self):
        return self.mono

    def advance(self, seconds, suspended=0.0):
        self.mono += seconds
        self.boot += seconds + suspended


def group(gid, *targets):
    return {"id": gid, "pids": [list(t) for t in targets]}


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGpuSystem(self.enterContext(tempfile.TemporaryDirectory()))
        self.clock = Clock()
        self.sampler = gpu.GpuSampler(collect_fn=self.collect, clock=self.clock, wall=lambda: 1791063600.0,
                                      boot=lambda: self.clock.boot, threaded=False)
        self.sampler.set_active(True)

    def collect(self, targets):
        return self.fake.collect(targets, clock=self.clock)

    def sample(self, groups, advance=gpu.GPU_INTERVAL):
        """Run one sample over the groups' processes and attribute it; returns ({id: group}, meta)."""
        self.clock.advance(advance)
        self.sampler.maybe_start([tuple(p) for g in groups for p in g["pids"]])
        self.sampler.poll()
        meta = self.sampler.attribute(groups)
        return {g["id"]: g for g in groups}, meta

    def amd(self, **kwargs):
        return self.fake.device(AMD_PDEV, "amdgpu", [("card1", 1), ("renderD129", 129)], **kwargs)


class ParsingTests(unittest.TestCase):
    def test_documented_i915_and_xe_formats(self):
        i915 = gpu.parse_fdinfo(I915_SHAPE)
        self.assertEqual((i915["driver"], i915["pdev"], i915["clientId"]), ("i915", INTEL_PDEV, 5))
        self.assertEqual(i915["engines"]["render"], {"ns": 7340012345})
        self.assertEqual(i915["engines"]["video"], {"ns": 0, "capacity": 2})
        self.assertEqual(i915["regions"], {})
        xe = gpu.parse_fdinfo(XE_SHAPE)
        self.assertEqual((xe["driver"], xe["clientId"]), ("xe", 4))
        self.assertEqual(xe["regions"]["vram0"]["shared"], 8 * 1024 * 1024)
        self.assertEqual(xe["regions"]["vram0"]["resident"], 51200 * 1024)
        self.assertEqual(xe["engines"]["ccs"], {"cycles": 0, "totalCycles": 9000000000, "capacity": 4})
        self.assertNotIn("cycles-rcs", xe["regions"], "drm-total-cycles-* is not a memory region")
        memory = gpu.memory_summary(xe)
        self.assertEqual(memory["dedicated"]["resident"], 51200 * 1024)
        self.assertEqual(memory["system"]["resident"], 384 * 1024, "system + gtt")
        self.assertEqual(memory["other"]["regions"], ["stolen"], "stolen is listed, never summed")

    def test_amdgpu_deprecated_alias_is_never_added_to_resident(self):
        info = gpu.parse_fdinfo(amdgpu(5, 4096, 8192, gtt_kib=512, vram_shared_kib=1024, gfx_ns=12))
        vram = info["regions"]["vram"]
        self.assertEqual((vram["resident"], vram["residentAlias"], vram["total"]), (4096 * 1024,) * 2 + (8192 * 1024,))
        memory = gpu.memory_summary(info)
        self.assertEqual(memory["dedicated"]["resident"], 4096 * 1024, "drm-resident, not resident + drm-memory")
        self.assertEqual(memory["dedicated"]["allocated"], 8192 * 1024)
        self.assertEqual(memory["system"]["resident"], 512 * 1024, "gtt + cpu are system RAM")
        # An older amdgpu that prints only the alias still reads as resident.
        old = gpu.parse_fdinfo("drm-driver: amdgpu\ndrm-client-id: 1\ndrm-memory-vram: 100 KiB\n")
        self.assertEqual(gpu.memory_summary(old)["dedicated"]["resident"], 100 * 1024)

    def test_non_drm_unsupported_and_malformed_inputs(self):
        self.assertIsNone(gpu.parse_fdinfo("pos:\t0\nflags:\t02\nmnt_id:\t14\nino:\t1\n"), "not a DRM client")
        bare = gpu.parse_fdinfo("drm-driver:\tnvidia-drm\ndrm-client-id:\t4\ndrm-pdev:\t0000:01:00.0\n")
        self.assertEqual((bare["regions"], bare["engines"]), ({}, {}), "a driver without usage stats")
        bad = gpu.parse_fdinfo("drm-driver: x\ndrm-client-id: seven\ndrm-total-vram: 12 GiB\n"
                               "drm-resident-vram: -1\ndrm-engine-gfx: 12 ms\ndrm-pdev: ../../etc\n"
                               "drm-engine-a b: 1 ns\ndrm-client-name: secret argv\n")
        self.assertIsNone(bad["clientId"])
        self.assertIsNone(bad["pdev"])
        self.assertEqual(bad["regions"]["vram"], {}, "unknown units and negatives are not zero")
        self.assertGreaterEqual(bad["invalid"], 3)
        self.assertNotIn("secret", json.dumps(bad), "drm-client-name is userspace text and is never kept")
        many = "drm-driver: x\n" + "".join("drm-engine-e%d: 1 ns\ndrm-total-r%d: 1\n" % (i, i) for i in range(64))
        parsed = gpu.parse_fdinfo(many)
        self.assertEqual((len(parsed["engines"]), len(parsed["regions"])), (gpu.MAX_ENGINES, gpu.MAX_REGIONS))
        self.assertEqual(gpu.parse_size("3 MiB"), 3 << 20)
        self.assertEqual(gpu.parse_size("17"), 17)
        self.assertIsNone(gpu.parse_size("1.5 KiB"))

    def test_region_classes(self):
        for name, kind in (("vram", "dedicated"), ("vram0", "dedicated"), ("local0", "dedicated"),
                           ("memory", "system"), ("system", "system"), ("system0", "system"), ("gtt", "system"),
                           ("cpu", "system"), ("stolen", "other"), ("stolen-local0", "other"),
                           ("stolen-system0", "other"), ("gds", "other"), ("doorbell", "other")):
            self.assertEqual(gpu.classify_region(name), kind, name)


class EngineTests(unittest.TestCase):
    def test_busy_time_capacity_and_warmup(self):
        first, base = gpu.engine_rate({"ns": 1_000_000_000, "capacity": 2}, None, 10.0)
        self.assertEqual((first["status"], first["percent"]), ("warming-up", None))
        reading, base = gpu.engine_rate({"ns": 2_000_000_000, "capacity": 2}, base, 12.0)
        self.assertEqual(reading["status"], "available")
        self.assertAlmostEqual(reading["percent"], 25.0, msg="1 s busy over 2 s on two engines")
        self.assertEqual((reading["basis"], reading["capacity"]), ("busy-time", 2))
        idle, _ = gpu.engine_rate({"ns": 2_000_000_000, "capacity": 2}, base, 14.0)
        self.assertEqual((idle["status"], idle["percent"]), ("available", 0.0), "a measured idle engine is 0")

    def test_gpu_cycles_and_max_frequency(self):
        _, base = gpu.engine_rate({"cycles": 100, "totalCycles": 1000, "capacity": 4}, None, 1.0)
        reading, _ = gpu.engine_rate({"cycles": 300, "totalCycles": 2000, "capacity": 4}, base, 99.0)
        self.assertAlmostEqual(reading["percent"], 5.0, msg="GPU clock domain: CPU time is irrelevant")
        self.assertEqual(reading["basis"], "gpu-cycles")
        _, base = gpu.engine_rate({"cycles": 0, "maxfreqHz": 1000}, None, 1.0)
        reading, _ = gpu.engine_rate({"cycles": 500, "maxfreqHz": 1000}, base, 2.0)
        self.assertAlmostEqual(reading["percent"], 50.0)
        # GPU cycles carry their own reference: a slow read does not blur them.
        _, base = gpu.engine_rate({"cycles": 100, "totalCycles": 1000}, None, 1.0)
        late, _ = gpu.engine_rate({"cycles": 300, "totalCycles": 2000}, base, 70.0, read_seconds=60.0)
        self.assertAlmostEqual(late["percent"], 20.0)
        _, base = gpu.engine_rate({"ns": 0}, None, 1.0)
        blurred, kept = gpu.engine_rate({"ns": 10}, base, 6.0, read_seconds=gpu.READ_SLACK * 2)
        self.assertEqual((blurred["status"], blurred["reason"], kept), ("error", "slow-read", None))
        only, kept = gpu.engine_rate({"cycles": 5}, None, 1.0)
        self.assertEqual((only["status"], kept), ("unsupported", None), "cycles cannot be normalized alone")

    def test_counter_behind_keeps_the_larger_value_until_it_catches_up(self):
        _, base = gpu.engine_rate({"ns": 5_000_000_000}, None, 10.0)
        behind, kept = gpu.engine_rate({"ns": 4_000_000_000}, base, 15.0)
        self.assertEqual((behind["status"], behind["percent"]), ("stale", None))
        self.assertIs(kept, base, "the spec: stay with the larger previous value")
        caught, _ = gpu.engine_rate({"ns": 6_000_000_000}, kept, 20.0)
        self.assertAlmostEqual(caught["percent"], 10.0, msg="1 s over the 10 s since the kept value")

    def test_capacity_change_zero_capacity_and_anomaly(self):
        _, base = gpu.engine_rate({"ns": 0}, None, 0.0)
        changed, rebased = gpu.engine_rate({"ns": 10, "capacity": 2}, base, 5.0)
        self.assertEqual(changed["status"], "warming-up")
        self.assertEqual(rebased["capacity"], 2)
        zero, kept = gpu.engine_rate({"ns": 10, "capacity": 0}, base, 5.0)
        self.assertEqual((zero["status"], zero["reason"], kept), ("error", "capacity", None))
        spike, rebased = gpu.engine_rate({"ns": 50_000_000_000}, base, 5.0)
        self.assertEqual((spike["status"], spike["reason"], spike["percent"]), ("error", "anomaly", None))
        self.assertEqual(rebased["value"], 50_000_000_000, "rebaselined, never a spike")


class AttributionTests(FixtureTest):
    def test_shared_descriptors_count_each_client_once(self):
        self.amd()
        text = amdgpu(9, 2048, gtt_kib=256)
        parent = self.fake.process(100, fds={5: ("renderD129", text), 6: ("card1", text), 7: ("/dev/null", None)})
        child = self.fake.process(101, fds={5: ("renderD129", text)})  # inherited across fork
        groups, meta = self.sample([group("app", parent, child)])
        entry = groups["app"]["gpu"][0]
        self.assertEqual((entry["clients"], entry["vramKb"], entry["systemKb"]), (1, 2048, 256))
        self.assertEqual(groups["app"]["gpuMemoryKb"], 2048, "VRAM only; GTT is not added")
        self.assertEqual(meta["devices"][0]["clients"], 1)
        self.assertEqual(meta["devices"][0]["totals"]["vramResidentKb"], 2048)
        self.assertEqual(meta["counts"]["drmFds"], 3)

    def test_cross_group_client_is_shared_never_assigned_in_full_to_each(self):
        self.amd()
        shared = amdgpu(11, 1000)
        compositor = self.fake.process(200, fds={4: ("renderD129", shared), 5: ("renderD129", amdgpu(12, 300))})
        app = self.fake.process(300, fds={9: ("renderD129", shared), 10: ("renderD129", amdgpu(13, 50))})
        lonely = self.fake.process(400, fds={3: ("renderD129", shared)})
        groups, meta = self.sample([group("compositor", compositor), group("app", app), group("lonely", lonely)])
        self.assertEqual(groups["compositor"]["gpuMemoryKb"], 300)
        self.assertEqual(groups["app"]["gpuMemoryKb"], 50)
        self.assertEqual(groups["compositor"]["gpu"][0]["sharedClients"], 1)
        self.assertEqual(groups["compositor"]["gpu"][0]["status"], "partial")
        self.assertEqual(groups["lonely"]["gpu"][0]["status"], "shared")
        self.assertIsNone(groups["lonely"]["gpuMemoryKb"], "only a shared client: not attributed")
        self.assertEqual(groups["lonely"]["gpuStatus"], "shared")
        totals = meta["devices"][0]["totals"]
        self.assertEqual((totals["crossGroupClients"], totals["crossGroupVramKb"]), (1, 1000))
        self.assertEqual(totals["vramResidentKb"], 1350, "each client once in the device total")
        attributed = sum(g["gpuMemoryKb"] or 0 for g in groups.values())
        self.assertEqual(attributed + totals["crossGroupVramKb"], totals["vramResidentKb"], "no double counting")

    def test_same_client_id_on_two_devices_is_two_clients(self):
        self.amd()
        self.fake.device(INTEL_PDEV, "i915", [("card0", 0), ("renderD128", 128)])
        intel = I915_SHAPE.replace("drm-client-id:      5", "drm-client-id:      9")
        app = self.fake.process(500, fds={3: ("renderD129", amdgpu(9, 700)), 4: ("renderD128", intel)})
        groups, meta = self.sample([group("app", app)])
        entries = {e["driver"]: e for e in groups["app"]["gpu"]}
        self.assertEqual(set(entries), {"amdgpu", "i915"})
        self.assertEqual((entries["amdgpu"]["clients"], entries["i915"]["clients"]), (1, 1))
        self.assertEqual(entries["i915"]["vramStatus"], "unsupported", "integrated: no device-local region")
        self.assertIsNone(entries["i915"]["vramKb"])
        self.assertEqual(groups["app"]["gpuMemoryKb"], 700, "a per-device list; the lens sums only VRAM")
        self.assertEqual([d["id"] for d in meta["devices"]], ["pci:" + INTEL_PDEV, "pci:" + AMD_PDEV])

    def test_missing_client_id_and_driver_without_stats_are_never_counted(self):
        self.amd()
        self.fake.device(NV_PDEV, "nvidia", [("card2", 2), ("renderD130", 130)], vendor="0x10de", device_id="0x2684")
        no_id = amdgpu(3, 900).replace("drm-client-id:\t3\n", "")
        bare = "drm-driver:\tnvidia-drm\ndrm-client-id:\t4\ndrm-pdev:\t%s\n" % NV_PDEV
        app = self.fake.process(600, fds={3: ("renderD129", no_id), 4: ("renderD130", bare)})
        groups, meta = self.sample([group("app", app)])
        entries = {e["driver"]: e for e in groups["app"]["gpu"]}
        self.assertEqual((entries["amdgpu"]["status"], entries["amdgpu"]["unidentified"]), ("unsupported", 1))
        self.assertIsNone(entries["amdgpu"]["vramKb"])
        nvidia = entries["nvidia"]
        self.assertEqual((nvidia["status"], nvidia["vramStatus"], nvidia["vramKb"]), ("available", "unsupported", None))
        self.assertIsNone(groups["app"]["gpuMemoryKb"], "unknown is not zero")
        nv_meta = next(d for d in meta["devices"] if d["driver"] == "nvidia")
        self.assertEqual((nv_meta["memory"]["dedicated"]["status"], nv_meta["engines"]["status"]),
                         ("unsupported", "unsupported"))
        self.assertEqual(nv_meta["pciId"], "10de:2684")
        self.assertFalse(meta["memoryLens"], "no device has given a VRAM reading")

    def test_successful_zero_versus_no_clients_and_sorting(self):
        self.amd()
        zero = self.fake.process(700, fds={3: ("renderD129", amdgpu(1, 0))})
        none = self.fake.process(701, fds={3: ("/dev/null", None)})
        big = self.fake.process(702, fds={3: ("renderD129", amdgpu(2, 4096))})
        groups, meta = self.sample([group("zero", zero), group("none", none), group("big", big)])
        self.assertEqual((groups["zero"]["gpuMemoryKb"], groups["zero"]["gpuStatus"]), (0, "available"))
        self.assertEqual((groups["none"]["gpuMemoryKb"], groups["none"]["gpuStatus"], groups["none"]["gpu"]),
                         (None, "none", []))
        self.assertTrue(meta["memoryLens"])
        for g in groups.values():
            g.update(kind="app", name=g["id"], host="", count=1, protected=False, pss=1, swap=0,
                     generation="g", cpuCorePercent=None)
        inventory = apps.Inventory()
        snapshot = inventory.publish(list(groups.values()), 1.0, 1.0, {"incompleteReason": None}, {}, gpu=meta)
        _, rows = apps.run_query(snapshot, "", "gpu-memory", 0, 50)
        self.assertEqual([r["id"] for r in rows], ["big", "zero", "none"], "zero before unknown")
        self.assertEqual(snapshot["coverage"]["gpuReadings"], 2)

    def test_partial_and_permission_denied_coverage(self):
        self.amd()
        readable = self.fake.process(800, fds={3: ("renderD129", amdgpu(1, 100))})
        hidden = self.fake.process(801)
        alone = self.fake.process(802)
        real_open = os.open
        targets = {801, 802}

        def deny_fd_dir(path, flags, *args, dir_fd=None, **kwargs):
            if path == "fd" and dir_fd in opened:
                raise PermissionError(13, "Permission denied")
            fd = real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)
            if isinstance(path, str) and path.rsplit("/", 1)[-1].isdigit() and int(path.rsplit("/", 1)[-1]) in targets:
                opened.add(fd)
            return fd

        opened = set()
        with unittest.mock.patch.object(gpu.os, "open", deny_fd_dir):
            groups, _ = self.sample([group("mixed", readable, hidden), group("alone", alone)])
        mixed = groups["mixed"]
        self.assertEqual((mixed["gpuStatus"], mixed["gpuMemoryKb"]), ("partial", 100))
        self.assertEqual(mixed["gpuCoverage"], {"measured": 1, "members": 2})
        self.assertEqual((groups["alone"]["gpuStatus"], groups["alone"]["gpuMemoryKb"]), ("permission-denied", None))

    def test_asleep_device_is_never_read_or_woken(self):
        self.amd(runtime="suspended", vram_total=8 << 30, vram_used=1 << 30)
        app = self.fake.process(900, fds={3: ("renderD129", amdgpu(1, 500))})
        with unittest.mock.patch.object(gpu, "read_small", wraps=gpu.read_small) as small:
            groups, meta = self.sample([group("app", app)])
        self.assertEqual(self.fake.fdinfo_reads, [], "no client of a suspended device is read")
        self.assertNotIn("mem_info_vram_used", " ".join(str(c) for c in small.call_args_list))
        entry = groups["app"]["gpu"][0]
        self.assertEqual((entry["status"], entry["vramKb"]), ("asleep", None))
        self.assertEqual(groups["app"]["gpuStatus"], "asleep")
        device = meta["devices"][0]
        self.assertEqual((device["status"], device["runtimeStatus"], device["memory"]["dedicated"]["status"]),
                         ("asleep", "suspended", "asleep"))
        self.assertIsNone(device["totals"]["deviceVramUsedKb"])
        self.assertEqual(device["totals"]["deviceVramTotalKb"], 8 << 20, "static identity is cached sysfs")

    def test_power_is_checked_before_every_client_read(self):
        self.amd()
        first = self.fake.process(1000, fds={3: ("renderD129", amdgpu(1, 10))})
        second = self.fake.process(1001, fds={3: ("renderD129", amdgpu(2, 20))})
        calls = []

        def power(path):
            calls.append(path)
            return "active" if len(self.fake.fdinfo_reads) == 0 else "suspending"

        self.clock.advance(gpu.GPU_INTERVAL)
        raw = self.fake.collect([first, second], power_of=power, clock=self.clock)
        self.assertEqual(len(self.fake.fdinfo_reads), 1, "the device went to sleep: the next client is not read")
        self.assertEqual(raw["processes"][second]["fds"], [(raw["processes"][first]["fds"][0][0], None, "asleep")])
        self.assertGreaterEqual(len(calls), 3)

    def test_unreadable_power_state_is_not_read(self):
        self.amd(runtime="garbage")
        app = self.fake.process(1100, fds={3: ("renderD129", amdgpu(1, 10))})
        groups, meta = self.sample([group("app", app)])
        self.assertEqual(self.fake.fdinfo_reads, [])
        self.assertEqual(groups["app"]["gpu"][0]["status"], "error")
        self.assertEqual(meta["devices"][0]["status"], "error")

    def test_no_runtime_pm_is_readable(self):
        path = self.amd(runtime=None)
        self.assertFalse(os.path.exists(path + "/power/runtime_status"))
        app = self.fake.process(1200, fds={3: ("renderD129", amdgpu(1, 10))})
        groups, meta = self.sample([group("app", app)])
        self.assertEqual(groups["app"]["gpuMemoryKb"], 10)
        self.assertEqual(meta["devices"][0]["runtimeStatus"], "no-runtime-pm")

    def test_reused_pid_and_pdev_mismatch_are_not_attributed(self):
        self.amd()
        self.fake.process(1300, start=111, fds={3: ("renderD129", amdgpu(1, 10))})
        mismatch = self.fake.process(1301, fds={3: ("renderD129", amdgpu(2, 20, pdev="0000:09:00.0"))})
        groups, meta = self.sample([group("reused", (1300, 999)), group("mismatch", mismatch)])
        self.assertEqual((groups["reused"]["gpu"], groups["reused"]["gpuStatus"]), ([], "warming-up"),
                         "a different process under the same PID is not this member")
        self.assertEqual(groups["mismatch"]["gpu"][0]["status"], "error")
        self.assertEqual(meta["counts"]["mismatches"], 1)

    def test_vram_residual_and_overlap(self):
        self.amd(vram_total=16 << 30, vram_used=10 << 20)
        app = self.fake.process(1400, fds={3: ("renderD129", amdgpu(1, 4096))})
        _, meta = self.sample([group("app", app)])
        totals = meta["devices"][0]["totals"]
        self.assertEqual((totals["deviceVramUsedKb"], totals["deviceVramTotalKb"]), (10240, 16 << 20))
        self.assertEqual((totals["unattributedVramKb"], totals["unattributedLowerBound"]), (6144, False))
        shared = self.fake.process(1401, fds={3: ("renderD129", amdgpu(2, 8192, vram_shared_kib=4096))})
        _, meta = self.sample([group("app", app), group("shared", shared)])
        totals = meta["devices"][0]["totals"]
        self.assertIsNone(totals["unattributedVramKb"], "clients' shared buffers exceed the device total")
        os.unlink(os.path.join(self.fake.proc, "1401", "fd", "3"))
        self.fake.process(1402, fds={3: ("renderD129", amdgpu(3, 1024, vram_shared_kib=512))})
        _, meta = self.sample([group("app", app), group("b", (1402, 14020))])
        totals = meta["devices"][0]["totals"]
        self.assertEqual((totals["unattributedVramKb"], totals["unattributedLowerBound"]), (5120, True))

    def test_residual_is_a_lower_bound_whenever_the_visible_sum_may_repeat_a_buffer(self):
        # Two 4 MiB clients beside 10 MiB used: 2 MiB unattributed only if they share nothing.
        self.amd(vram_total=16 << 20, vram_used=10 << 20)
        cases = {
            "known-zero": (amdgpu(1, 4096), amdgpu(2, 4096), "none", False),
            # Legacy amdgpu (Linux v6.8) prints no drm-shared key: both could reference one
            # allocation, leaving 6 MiB unattributed. Unknown sharing is not uniqueness.
            "unknown": (legacy_amdgpu(1, 4096), legacy_amdgpu(2, 4096), "possible", True),
            "known-positive": (amdgpu(1, 4096, vram_shared_kib=1024), amdgpu(2, 4096, vram_shared_kib=1024),
                               "possible", True),
        }
        for pid, (name, (first, second, overlapping, lower)) in enumerate(cases.items(), 1450):
            with self.subTest(name):
                app = self.fake.process(pid, fds={3: ("renderD129", first), 4: ("renderD129", second)})
                groups, meta = self.sample([group("app", app)])
                totals = meta["devices"][0]["totals"]
                self.assertEqual((totals["vramResidentKb"], totals["deviceVramUsedKb"]), (8192, 10240))
                self.assertEqual((totals["vramOverlap"], groups["app"]["gpu"][0]["vramOverlap"]),
                                 (overlapping, overlapping))
                self.assertEqual((totals["unattributedVramKb"], totals["unattributedLowerBound"]), (2048, lower))
        # One client alone with an unknown drm-shared key is summed once: its residual is exact.
        app = self.fake.process(1460, fds={3: ("renderD129", legacy_amdgpu(1, 4096))})
        _, meta = self.sample([group("app", app)])
        totals = meta["devices"][0]["totals"]
        self.assertEqual((totals["vramOverlap"], totals["unattributedVramKb"], totals["unattributedLowerBound"]),
                         ("none", 6144, False))

    def test_shared_buffers_of_distinct_clients_are_a_labelled_reference_sum(self):
        # The kernel counts a buffer shared between DRM files (drm-shared-*) in
        # every client holding it and prints no buffer identity. Two clients of
        # one app each referencing the same 4 GiB buffer on an 8 GiB device.
        self.amd(vram_total=8 << 30, vram_used=4 << 30)
        a = self.fake.process(100, fds={3: ("renderD129", amdgpu(1, 4 << 20, vram_shared_kib=4 << 20))})
        b = self.fake.process(101, fds={3: ("renderD129", amdgpu(2, 4 << 20, vram_shared_kib=4 << 20))})
        groups, meta = self.sample([group("app", a, b)])
        row, entry = groups["app"], groups["app"]["gpu"][0]
        self.assertEqual((entry["clients"], entry["vramKb"], entry["vramSharedKb"]), (2, 8 << 20, 8 << 20),
                         "the client-reference total is kept, not invented away")
        self.assertEqual(entry["vramOverlap"], "possible")
        self.assertEqual((row["gpuMemoryKb"], row["gpuMemoryOverlap"]), (8 << 20, True), "never an unlabelled exact sum")
        totals = meta["devices"][0]["totals"]
        self.assertEqual((totals["vramOverlap"], totals["deviceVramUsedKb"]), ("possible", 4 << 20))
        self.assertIsNone(totals["unattributedVramKb"], "the overlap exceeds the device's physical use")
        # The app-query surface carries the label and counts the rows ranked by such a sum.
        row.update(kind="app", name="app", host="", count=2, protected=False, pss=1, swap=0, generation="g",
                   cpuCorePercent=None)
        snapshot = apps.Inventory().publish([row], 1.0, 1.0, {"incompleteReason": None}, {}, gpu=meta)
        reply = apps.page_reply({"type": "apps-page"}, snapshot, "", "gpu-memory", 0, 50)
        self.assertEqual((reply["rows"][0]["gpuMemoryOverlap"], reply["coverage"]["gpuOverlap"]), (True, 1))
        self.assertIs(reply["rows"][0]["gpuComplete"], True, "query rows carry completeness")
        self.assertIn("once per client", reply["units"]["gpuMemoryKb"])
        self.assertIn("vramSharedKb", meta["units"])

    def test_overlap_needs_two_resident_clients_with_shared_buffers(self):
        self.amd()
        private = [self.fake.process(110 + n, fds={3: ("renderD129", amdgpu(10 + n, 1024))}) for n in range(2)]
        one_shared = self.fake.process(120, fds={3: ("renderD129", amdgpu(20, 2048, vram_shared_kib=512)),
                                                 4: ("renderD129", amdgpu(21, 100))})
        idle_shared = self.fake.process(130, fds={3: ("renderD129", amdgpu(30, 0, 64, vram_shared_kib=64)),
                                                  4: ("renderD129", amdgpu(31, 300, vram_shared_kib=300))})
        no_key = "drm-driver: amdgpu\ndrm-client-id: %d\ndrm-resident-vram: 10 KiB\n"
        unknown = self.fake.process(140, fds={3: ("renderD129", no_key % 40), 4: ("renderD129", no_key % 41)})
        groups, _ = self.sample([group("private", *private), group("one", one_shared), group("idle", idle_shared),
                                 group("unknown", unknown)])
        self.assertEqual(groups["private"]["gpu"][0]["vramOverlap"], "none", "no shared buffers: exact")
        self.assertEqual(groups["one"]["gpu"][0]["vramOverlap"], "none", "only one client shares: counted once here")
        self.assertEqual(groups["one"]["gpu"][0]["vramSharedKb"], 512, "its shared buffers are still reported")
        self.assertEqual(groups["idle"]["gpu"][0]["vramOverlap"], "none", "a client with nothing resident adds nothing")
        self.assertEqual(groups["unknown"]["gpu"][0]["vramOverlap"], "possible", "no drm-shared key: unknown, not exact")
        self.assertIsNone(groups["unknown"]["gpu"][0]["vramSharedKb"])
        self.assertEqual([groups[g]["gpuMemoryOverlap"] for g in ("private", "one", "idle", "unknown")],
                         [False, False, False, True])

    def test_overlap_is_per_device_and_across_groups_only_on_the_device(self):
        self.amd()
        self.fake.device(INTEL_PDEV, "xe", [("renderD128", 128)])
        xe = XE_SHAPE.replace("drm-shared-vram0:       8 MiB", "drm-shared-vram0:       0").replace(AMD_PDEV, INTEL_PDEV)
        app = self.fake.process(150, fds={3: ("renderD129", amdgpu(1, 1000, vram_shared_kib=1000)),
                                          4: ("renderD129", amdgpu(2, 1000, vram_shared_kib=1000)),
                                          5: ("renderD128", xe)})
        # Two apps, one client each, both holding a shared buffer: each row counts it once.
        left = self.fake.process(160, fds={3: ("renderD129", amdgpu(3, 700, vram_shared_kib=700))})
        right = self.fake.process(161, fds={3: ("renderD129", amdgpu(4, 700, vram_shared_kib=700))})
        groups, meta = self.sample([group("app", app), group("left", left), group("right", right)])
        entries = {e["driver"]: e for e in groups["app"]["gpu"]}
        self.assertEqual((entries["amdgpu"]["vramOverlap"], entries["xe"]["vramOverlap"]), ("possible", "none"))
        self.assertEqual(groups["app"]["gpuMemoryKb"], 2000 + 51200)
        self.assertTrue(groups["app"]["gpuMemoryOverlap"], "one overlapping device makes the row's sum a reference total")
        self.assertEqual([groups[g]["gpuMemoryOverlap"] for g in ("left", "right")], [False, False])
        devices = {d["driver"]: d for d in meta["devices"]}
        self.assertEqual(devices["amdgpu"]["totals"]["vramOverlap"], "possible", "the device sum spans both apps")
        self.assertEqual(devices["xe"]["totals"]["vramOverlap"], "none")

    def test_bounds_mark_the_sample_incomplete(self):
        self.amd()
        many = self.fake.process(1500, fds={n: ("renderD129", amdgpu(n, 1)) for n in range(3, 9)})
        with unittest.mock.patch.object(gpu, "MAX_DRM_FDS", 2):
            groups, meta = self.sample([group("many", many)])
        self.assertEqual(meta["incomplete"], "drm-fd-limit")
        self.assertEqual(groups["many"]["gpu"][0]["unread"], 4)
        self.assertEqual(groups["many"]["gpuStatus"], "partial")
        with unittest.mock.patch.object(gpu, "MAX_FDS_PER_PROCESS", 3):
            _, meta = self.sample([group("many", many)])
        self.assertEqual(meta["incomplete"], "fd-limit")
        huge = self.fake.process(1501, fds={3: ("renderD129", "x" * (gpu.MAX_FDINFO_BYTES + 10) + "\n"
                                                + amdgpu(1, 99))})
        groups, _ = self.sample([group("huge", huge)])
        self.assertEqual(groups["huge"]["gpu"][0]["status"], "unsupported", "keys beyond the read bound are unseen")

    def test_no_drm_devices_skips_the_process_walk(self):
        app = self.fake.process(1600, fds={3: ("renderD129", amdgpu(1, 10))})
        with unittest.mock.patch.object(gpu.os, "listdir", wraps=os.listdir) as listdir:
            groups, meta = self.sample([group("app", app)])
        self.assertEqual((meta["status"], meta["reason"]), ("unsupported", "no-drm-devices"))
        self.assertEqual(groups["app"]["gpuStatus"], "unsupported")
        self.assertEqual(len(listdir.call_args_list), 1, "only /sys/class/drm was listed")

    def stalling_read(self, stalls):
        """_read_fdinfo that advances the clock by stalls[fd name] inside the read, the way xe's
        show_fdinfo can sleep on a buffer's reservation lock or a pending exec-queue removal."""
        original = gpu._read_fdinfo

        def read(dir_fd, name):
            self.clock.advance(stalls.get(name, 0.0))
            return original(dir_fd, name)
        return unittest.mock.patch.object(gpu, "_read_fdinfo", read)

    def test_slow_client_read_has_no_engine_rate_and_a_slow_sample_is_stale(self):
        self.amd()
        quick = self.fake.process(1800, fds={3: ("renderD129", amdgpu(1, 10, gfx_ns=0))})
        slow = self.fake.process(1801, fds={4: ("renderD129", amdgpu(2, 20, gfx_ns=0))})
        self.sample([group("quick", quick), group("slow", slow)])
        for pid, fd in ((1800, 3), (1801, 4)):
            self.fake.write(os.path.join(self.fake.proc, str(pid), "fdinfo", str(fd)),
                            amdgpu(pid - 1799, 10 * (pid - 1799), gfx_ns=1_000_000_000))
        with self.stalling_read({"4": 0.5}):
            groups, meta = self.sample([group("quick", quick), group("slow", slow)])
        self.assertEqual(groups["quick"]["gpu"][0]["engines"]["gfx"]["status"], "available")
        slow_gfx = groups["slow"]["gpu"][0]["engines"]["gfx"]
        self.assertEqual((slow_gfx["status"], slow_gfx["percent"]), ("error", None),
                         "busy ns taken at an unknown moment of a 0.5 s read: no rate")
        self.assertEqual(meta["status"], "available", "a short stall leaves the sample fresh")
        groups, _ = self.sample([group("quick", quick), group("slow", slow)])
        self.assertEqual(groups["slow"]["gpu"][0]["engines"]["gfx"]["status"], "warming-up", "no baseline was kept")
        # A read that sleeps past the timeout: the sample is retained but stale until a timely one.
        with self.stalling_read({"4": gpu.SAMPLE_TIMEOUT + 2}):
            groups, meta = self.sample([group("quick", quick), group("slow", slow)])
        self.assertEqual((meta["status"], meta["reason"]), ("stale", "sample-timeout"))
        self.assertGreater(meta["collectSeconds"], gpu.SAMPLE_TIMEOUT)
        self.assertEqual({g["gpuStatus"] for g in groups.values()}, {"stale"})
        self.assertEqual(groups["quick"]["gpu"][0]["engines"]["gfx"]["status"], "stale")
        groups, meta = self.sample([group("quick", quick), group("slow", slow)])
        self.assertEqual((meta["status"], meta["reason"]), ("available", None), "the next timely sample clears it")

    def test_engines_per_device_never_summed_across_engines(self):
        self.amd()
        app = self.fake.process(1700, fds={3: ("renderD129", amdgpu(1, 10, gfx_ns=0))})
        groups, _ = self.sample([group("app", app)])
        self.assertEqual(groups["app"]["gpu"][0]["engines"]["gfx"]["status"], "warming-up")
        self.fake.write(os.path.join(self.fake.proc, "1700", "fdinfo", "3"), amdgpu(1, 10, gfx_ns=2_500_000_000))
        groups, _ = self.sample([group("app", app)])
        gfx = groups["app"]["gpu"][0]["engines"]["gfx"]
        self.assertEqual(gfx["status"], "available")
        self.assertAlmostEqual(gfx["percent"], 50.0, places=3)
        self.assertNotIn("percent", groups["app"]["gpu"][0], "no device-wide GPU percentage")
        # A gap longer than three intervals resets the baselines.
        groups, _ = self.sample([group("app", app)], advance=gpu.MAX_GAP + 1)
        self.assertEqual(groups["app"]["gpu"][0]["engines"]["gfx"]["status"], "warming-up")
        self.assertEqual(self.sampler.reset_reason, "gap")


class LifecycleTests(unittest.TestCase):
    def make(self, collect_fn, threaded=True):
        self.clock = Clock()
        return gpu.GpuSampler(collect_fn=collect_fn, clock=self.clock, wall=lambda: 1.0, threaded=threaded)

    @staticmethod
    def raw(clock, clients=None):
        return {"startedAt": clock(), "endedAt": clock(), "devices": {"/d": {"id": "pci:x", "driver": "x",
                "pdev": None, "pciId": None, "nodes": [], "runtime": "active"}},
                "processes": {(1, 10): {"status": "ok", "fds": [("/d", ("/d", 1), "read")]}},
                "clients": clients if clients is not None else {("/d", 1): {"readAt": clock(), "info": gpu.parse_fdinfo(
                    "drm-driver: x\ndrm-client-id: 1\ndrm-resident-vram: 1 KiB\n")}},
                "incomplete": None, "counts": {}}

    def test_inactive_does_no_work_and_reports_inactive(self):
        calls = []
        sampler = self.make(lambda targets: calls.append(targets), threaded=False)
        self.assertFalse(sampler.maybe_start([(1, 10)]))
        groups = [group("g", (1, 10))]
        self.assertEqual(sampler.attribute(groups)["status"], "inactive")
        self.assertEqual((groups[0]["gpuStatus"], groups[0]["gpu"]), ("inactive", []))
        self.assertEqual(calls, [])

    def test_cadence_is_at_least_five_seconds(self):
        sampler = self.make(lambda targets: self.raw(self.clock), threaded=False)
        sampler.set_active(True)
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        for step in (2.5, 2.4):
            self.clock.advance(step)
            self.assertFalse(sampler.maybe_start([(1, 10)]), "the 2.5 s detail scan does not resample")
        self.clock.advance(0.1)
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        self.assertEqual(sampler.runs, 2)

    def test_cadence_survives_deactivation_reopen_and_settings(self):
        starts = []
        sampler = self.make(lambda targets: (starts.append(self.clock()), self.raw(self.clock))[1], threaded=False)
        sampler.set_active(True)
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        self.clock.advance(0.25)
        sampler.set_active(False)  # panel closed or gpuMetrics off ...
        sampler.set_active(True)  # ... and back 0.25 s later
        self.assertIsNone(sampler.latest, "the old sample and baselines are still dropped")
        self.assertFalse(sampler.maybe_start([(1, 10)]), "a reopen never starts a sample sooner than 5 s")
        self.clock.advance(gpu.GPU_INTERVAL - 0.25 - 0.01)
        self.assertFalse(sampler.maybe_start([(1, 10)]))
        self.clock.advance(0.01)
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        self.assertEqual(starts, [1000.0, 1005.0])
        self.assertEqual(sampler.status()[0], "available", "the new generation's own sample")

    def test_freshness_is_measurement_time_not_hand_over_time(self):
        # A sample whose client read happens at once, then stalls 60 s before it is handed over.
        self.clock = Clock()

        def stalls(targets):
            raw = self.raw(self.clock)
            self.clock.advance(60)
            raw["endedAt"] = self.clock()
            return raw

        sampler = gpu.GpuSampler(collect_fn=stalls, clock=self.clock, wall=lambda: 1791063600.0 + self.clock(),
                                 threaded=False)
        sampler.set_active(True)
        sampler.maybe_start([(1, 10)])
        groups = [group("g", (1, 10))]
        meta = sampler.attribute(groups)
        self.assertEqual((meta["status"], meta["reason"]), ("stale", "sample-timeout"), "never fresh")
        self.assertEqual(meta["sampledAt"], 1791063600.0 + 1000.0, "when it was read, not when it was handed over")
        self.assertEqual((meta["ageSeconds"], meta["collectSeconds"]), (60.0, 60.0))
        self.assertEqual((groups[0]["gpuStatus"], groups[0]["gpu"][0]["status"], groups[0]["gpuMemoryKb"]),
                         ("stale", "stale", 1), "retained and labelled")

    def test_old_sample_adopted_late_is_stale(self):
        # A timely sample whose result the main loop picks up 20 s later is already old.
        release = threading.Event()
        sampler = self.make(lambda targets: (release.wait(10), self.raw(self.clock))[1])
        sampler.set_active(True)
        sampler.maybe_start([(1, 10)])
        release.set()
        sampler.worker["thread"].join(5)
        self.clock.advance(gpu.STALE_SECONDS + 5)
        sampler.poll()
        self.assertEqual(sampler.status(), ("stale", "old-sample"))

    def test_one_worker_at_a_time_timeout_and_late_result(self):
        release = threading.Event()
        started = []

        def slow(targets):
            started.append(targets)
            release.wait(10)
            return self.raw(self.clock)

        sampler = self.make(slow)
        sampler.set_active(True)
        began = time.perf_counter()
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        sampler.poll()
        self.assertLess(time.perf_counter() - began, 0.5, "the main loop never waits for a sample")
        self.clock.advance(gpu.GPU_INTERVAL * 3)
        self.assertFalse(sampler.maybe_start([(1, 10)]), "never a second worker")
        self.clock.advance(gpu.SAMPLE_TIMEOUT)
        sampler.poll()
        self.assertEqual(sampler.status(), ("error", "sample-timeout"))
        groups = [group("g", (1, 10))]
        sampler.attribute(groups)
        self.assertEqual(groups[0]["gpuStatus"], "error")
        release.set()
        sampler.worker["thread"].join(5)
        sampler.poll()
        self.assertEqual(sampler.status(), ("stale", "sample-timeout"),
                         "the same generation's late result is kept, labelled stale, never fresh")
        meta = sampler.attribute(groups)
        self.assertEqual((meta["status"], groups[0]["gpuStatus"]), ("stale", "stale"))
        self.assertGreater(meta["ageSeconds"], gpu.SAMPLE_TIMEOUT)
        self.assertEqual(len(started), 1)
        # The next sample starts at once (its 5 s are long past) and, being timely, clears the error.
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        sampler.worker["thread"].join(5)
        sampler.poll()
        self.assertEqual(sampler.status(), ("available", None))
        self.assertEqual(len(started), 2)

    def test_deactivate_and_reopen_drop_an_old_generation(self):
        release = threading.Event()
        sampler = self.make(lambda targets: (release.wait(10), self.raw(self.clock))[1])
        sampler.set_active(True)
        sampler.maybe_start([(1, 10)])
        old = sampler.worker["thread"]
        sampler.set_active(False)
        sampler.set_active(True)  # reopen while the old worker is still running
        self.clock.advance(gpu.GPU_INTERVAL)
        self.assertFalse(sampler.maybe_start([(1, 10)]), "the stuck worker is not duplicated")
        release.set()
        old.join(5)
        sampler.poll()
        self.assertIsNone(sampler.latest, "a result from before the reopen is discarded")
        self.assertTrue(sampler.maybe_start([(1, 10)]))
        sampler.worker["thread"].join(5)
        sampler.poll()
        self.assertIsNotNone(sampler.latest)

    def test_failures_and_staleness(self):
        sampler = self.make(lambda targets: (_ for _ in ()).throw(OSError("boom")), threaded=False)
        sampler.set_active(True)
        sampler.maybe_start([(1, 10)])
        self.assertEqual(sampler.status(), ("error", "sample-failed"))
        good = self.make(lambda targets: self.raw(self.clock), threaded=False)
        good.set_active(True)
        good.maybe_start([(1, 10)])
        groups = [group("g", (1, 10))]
        good.attribute(groups)
        self.assertEqual((groups[0]["gpuStatus"], groups[0]["gpuMemoryKb"]), ("available", 1))
        self.clock.advance(gpu.STALE_SECONDS + 1)
        good.attribute(groups)
        self.assertEqual((groups[0]["gpuStatus"], groups[0]["gpuMemoryKb"]), ("stale", 1), "labelled, retained")
        self.assertEqual(groups[0]["gpu"][0]["status"], "stale")

    def test_stale_sums_keep_their_completeness(self):
        # Completeness is judged before staleness relabels a reading: a sum that missed
        # members or clients stays a lower bound when it turns stale ("~" with overlap,
        # "≥" without), never an upper bound or an exact figure.
        text = "drm-driver: x\ndrm-client-id: %d\ndrm-resident-vram: 4 MiB\ndrm-shared-vram: 4 MiB\ndrm-engine-gfx: 5 ns\n"
        ids = {(1, 10): 1, (2, 20): 2, (3, 30): 3, (4, 40): 4, (5, 50): 5, (6, 60): 6, (7, 70): 7, (8, 80): 8,
               (9, 90): 9}

        def collect(targets):
            raw = self.raw(self.clock)
            raw["clients"] = {("/d", n): {"readAt": self.clock(), "info": gpu.parse_fdinfo(text % n)}
                              for n in ids.values()}
            raw["processes"] = {key: {"status": "ok", "fds": [("/d", ("/d", n), "read")]} for key, n in ids.items()}
            raw["processes"][(33, 330)] = {"status": "permission-denied", "fds": []}
            raw["processes"][(6, 60)]["fds"].append(("/d", None, "unread"))  # past the per-sample limit
            return raw

        sampler = self.make(collect, threaded=False)
        sampler.set_active(True)
        sampler.maybe_start([])
        rows = [group("new", (1, 10), (2, 20), (99, 990)),  # (99, 990) joined after the sample
                group("denied", (3, 30), (4, 40), (33, 330)),  # unreadable member
                group("unread", (5, 50), (6, 60)),  # a descriptor not read
                group("whole", (7, 70), (8, 80)),
                group("single", (9, 90), (99, 991))]
        sampler.attribute(rows)
        fresh = {r["id"]: (r["gpuStatus"], r["gpuComplete"], r["gpuMemoryOverlap"]) for r in rows}
        self.assertEqual(fresh, {"new": ("partial", False, True), "denied": ("partial", False, True),
                                 "unread": ("partial", False, True), "whole": ("available", True, True),
                                 "single": ("partial", False, False)})
        self.clock.advance(gpu.STALE_SECONDS + 1)
        sampler.attribute(rows)
        stale = {r["id"]: (r["gpuStatus"], r["gpuComplete"], r["gpuMemoryOverlap"]) for r in rows}
        self.assertEqual(stale, {k: ("stale",) + v[1:] for k, v in fresh.items()}, "completeness survives staleness")
        self.assertEqual({r["id"]: r["gpuCoverage"]["measured"] for r in rows},
                         {"new": 2, "denied": 2, "unread": 2, "whole": 2, "single": 1})
        for row in rows:
            self.assertEqual(row["gpuMemoryKb"], 4096 * len([p for p in row["pids"] if tuple(p) in ids]))
            self.assertEqual([(e["status"], e["engines"]["gfx"]["status"]) for e in row["gpu"]], [("stale", "stale")])
        self.assertEqual(rows[2]["gpu"][0]["unread"], 1, "details still say which descriptors were missed")
        inactive = [group("g", (1, 10))]
        sampler.set_active(False)
        sampler.attribute(inactive)
        self.assertIsNone(inactive[0]["gpuComplete"], "no sample: completeness is unknown, not true")

    def test_suspend_resets_engine_baselines(self):
        clock = Clock()
        sampler = gpu.GpuSampler(collect_fn=lambda t: self.raw(clock), clock=clock, wall=lambda: 1.0,
                                 boot=lambda: clock.boot, threaded=False)
        self.clock = clock
        sampler.set_active(True)
        sampler.maybe_start([(1, 10)])
        clock.advance(gpu.GPU_INTERVAL, suspended=60)
        sampler.maybe_start([(1, 10)])
        self.assertEqual(sampler.reset_reason, "suspend")


class ProbeWiringTests(unittest.TestCase):
    def setUp(self):
        state_home = self.enterContext(tempfile.TemporaryDirectory())
        self.state = probe.ProbeState(raman_history.Persistence(os.path.join(state_home, "raman")))

    def test_gpu_follows_apps_visibility_detail_and_demo(self):
        for wanted, detail, demo, active in ((False, True, None, False), (True, False, None, False),
                                             (True, True, None, True), (True, True, {"apps": []}, False)):
            self.state.gpu_wanted, self.state.detail_active, self.state.demo = wanted, detail, demo
            self.state.sync_gpu()
            self.assertEqual(self.state.gpu.active, active, (wanted, detail, demo))

    def test_setting_and_demo_toggles_keep_the_five_second_cadence(self):
        clock = Clock()
        starts = []
        self.state.gpu = gpu.GpuSampler(collect_fn=lambda t: (starts.append(clock()), LifecycleTests.raw(clock))[1],
                                        clock=clock, wall=lambda: 1.0, threaded=False)
        self.state.gpu_wanted, self.state.detail_active = True, True
        self.state.sync_gpu()
        self.assertTrue(self.state.gpu.maybe_start([(1, 10)]))
        for flip in ("gpu_wanted", "detail_active", "demo"):
            clock.advance(0.25)
            old = getattr(self.state, flip)
            setattr(self.state, flip, {"apps": []} if flip == "demo" else False)
            self.state.sync_gpu()
            self.assertFalse(self.state.gpu.active)
            setattr(self.state, flip, old)
            self.state.sync_gpu()
            self.assertTrue(self.state.gpu.active)
            self.assertFalse(self.state.gpu.maybe_start([(1, 10)]), flip)
        clock.advance(gpu.GPU_INTERVAL - 0.75)
        self.assertTrue(self.state.gpu.maybe_start([(1, 10)]))
        self.assertEqual(starts, [1000.0, 1005.0])

    def test_demo_gpu_is_synthetic_and_only_when_wanted(self):
        scene = probe.load_demo("apps")
        rows, meta = probe.demo_gpu(scene, True)
        self.assertEqual((meta["demo"], meta["synthetic"], meta["status"]), (True, True, "available"))
        broth = next(r for r in rows if r["name"].startswith("Broth"))
        self.assertGreater(broth["gpuMemoryKb"], 6 << 20)
        self.assertEqual({r["gpuStatus"] for r in rows} >= {"available", "partial", "shared", "none",
                                                            "permission-denied", "warming-up", "asleep"}, True)
        self.assertTrue(any(r["gpuMemoryKb"] == 0 for r in rows), "a measured zero")
        # A sleeping device is shown as live ones are: no client read, no reading, no invented total.
        asleep = [d for d in meta["devices"] if d["status"] == "asleep"]
        self.assertEqual([(d["runtimeStatus"], d["clients"], d["totals"]["deviceVramUsedKb"]) for d in asleep],
                         [("suspended", 0, None)])
        sleeper = next(r for r in rows if r["gpuStatus"] == "asleep")
        self.assertEqual((sleeper["gpuMemoryKb"], sleeper["gpuComplete"], [e["deviceId"] for e in sleeper["gpu"]]),
                         (None, False, [asleep[0]["id"]]))
        self.assertEqual({r["gpuComplete"] for r in rows if r["gpuStatus"] in ("partial", "shared", "warming-up")},
                         {False}, "demo rows state completeness like live ones")
        off_rows, off = probe.demo_gpu(scene, False)
        self.assertEqual(off["status"], "inactive")
        self.assertEqual({r["gpuStatus"] for r in off_rows}, {"inactive"})
        self.assertEqual({(r["gpuMemoryKb"], r["gpuStatus"]) for r in probe.demo_preview(off_rows)},
                         {(None, "inactive")}, "no synthetic GPU reading unless GPU metrics are wanted")
        red_rows, red = probe.demo_gpu(probe.load_demo("red"), True)
        self.assertEqual((red["status"], red["reason"]), ("unsupported", "demo-without-gpu"))
        # The preview Memory rows are unchanged by GPU fields.
        self.assertEqual([r["id"] for r in probe.demo_preview(rows)], [r["id"] for r in probe.demo_preview(off_rows)])

    def test_query_sorts_by_gpu_memory_and_reports_the_lens(self):
        scene = probe.load_demo("apps")
        rows, meta = probe.demo_gpu(scene, True)
        snapshot = apps.Inventory().publish(rows, 1.0, 1.0, probe.DEMO_INVENTORY, scene["cpu"], demo=True, gpu=meta)
        reply = apps.page_reply({"type": "apps-page"}, snapshot, "", "gpu-memory", 0, 50)
        ordered = [r["gpuMemoryKb"] for r in reply["rows"]]
        known = [v for v in ordered if v is not None]
        self.assertEqual(known, sorted(known, reverse=True))
        self.assertEqual(ordered[:len(known)], known, "unknown sorts last")
        self.assertTrue(reply["gpu"]["memoryLens"])
        self.assertLessEqual(len(json.dumps(reply)), apps.MAX_REPLY_BYTES)

    def test_many_devices_and_engines_stay_within_the_reply_bound(self):
        engines = {"engine%d" % e: {"percent": 1.5, "status": "available", "basis": "busy-time", "capacity": 1}
                   for e in range(gpu.MAX_ENGINES)}
        entry = {"deviceId": "pci:0000:%02x:00.0" % 1, "driver": "amdgpu", "source": gpu.SOURCE, "status": "available",
                 "vramKb": 1, "vramAllocatedKb": 1, "vramStatus": "available", "vramSemantics": "resident",
                 "systemKb": 1, "systemAllocatedKb": 1, "systemStatus": "available", "engines": engines,
                 "clients": 1, "sharedClients": 0, "unidentified": 0, "unread": 0}
        groups = [{"id": "g%03d" % i, "kind": "app", "name": "n" * 256, "host": "", "count": 1, "protected": False,
                   "pss": 1, "swap": 0, "generation": "x", "cpuCorePercent": None, "pids": [[i + 1, 1]],
                   "gpu": [dict(entry, deviceId="pci:0000:%02x:00.0" % d) for d in range(gpu.MAX_DEVICES)],
                   "gpuMemoryKb": gpu.MAX_DEVICES, "gpuStatus": "available", "gpuCoverage": {"measured": 1, "members": 1}}
                  for i in range(60)]
        snapshot = apps.Inventory().publish(groups, 1.0, 1.0, {"incompleteReason": None}, {}, gpu=gpu.inactive_meta())
        reply = apps.page_reply({"type": "apps-page"}, snapshot, "", "gpu-memory", 0, 50)
        self.assertLessEqual(len(json.dumps(reply, separators=(",", ":"))) + 1, apps.MAX_REPLY_BYTES)
        self.assertLess(len(reply["rows"]), 50, "trimmed to the bound")
        self.assertEqual(reply["nextOffset"], len(reply["rows"]), "paging continues where the rows stop")


def busy_wait(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(os.path.exists("/proc/self/fdinfo"), "needs Linux procfs")
class LinuxProcTests(unittest.TestCase):
    """The real /proc walk with a fixture sysfs device: test-owned children, no DRM hardware."""

    def setUp(self):
        self.fake = FakeGpuSystem(self.enterContext(tempfile.TemporaryDirectory()))
        self.fake.device(AMD_PDEV, "amdgpu", [("renderD129", 129)])
        self.children = []

    def tearDown(self):
        for child in self.children:
            child.kill()
            child.wait(5)
            if child.stdout:
                child.stdout.close()

    def child(self, code):
        child = subprocess.Popen([sys.executable, "-c", code + "; print('ready', flush=True); import time; "
                                  "time.sleep(60)"], stdout=subprocess.PIPE, text=True)
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        return child

    def start_of(self, pid):
        with open("/proc/%d/stat" % pid) as handle:
            text = handle.read()
        return int(text[text.rfind(")") + 2:].split()[19])

    def test_real_processes_without_drm_descriptors(self):
        child = self.child("f = open('/dev/null')")
        target = (child.pid, self.start_of(child.pid))
        raw = gpu.collect([target, (child.pid, target[1] + 1)], "/proc", self.fake.sys, {})
        self.assertEqual(raw["processes"][target], {"status": "ok", "fds": []})
        self.assertEqual(raw["processes"][(child.pid, target[1] + 1)]["status"], "gone", "start time is checked")
        self.assertGreater(raw["counts"]["fdEntries"], 2)
        self.assertEqual(raw["counts"]["drmFds"], 0)
        # A non-DRM fdinfo parses as "not a client".
        with open("/proc/%d/fdinfo/0" % os.getpid()) as handle:
            self.assertIsNone(gpu.parse_fdinfo(handle.read()))

    def test_non_dumpable_child_is_permission_denied_not_zero(self):
        child = self.child("import ctypes; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)")
        try:
            os.listdir("/proc/%d/fd" % child.pid)
            self.skipTest("this environment can read a non-dumpable process's descriptors")
        except PermissionError:
            pass
        target = (child.pid, self.start_of(child.pid))
        raw = gpu.collect([target], "/proc", self.fake.sys, {})
        self.assertEqual(raw["processes"][target]["status"], "permission-denied")


@unittest.skipUnless(os.path.exists("/proc/self/smaps_rollup"), "needs Linux procfs")
class LiveProbeGpuTests(unittest.TestCase):
    """The real probe: GPU sampling only for detail + gpu, and reported truthfully without hardware."""

    def setUp(self):
        self.state = self.enterContext(tempfile.TemporaryDirectory())
        self.p = start_probe(self.state)
        self.requests = 0

    def tearDown(self):
        if self.p.poll() is None:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        self.p.stdout.close()

    def next_apps(self, predicate=lambda m: True, timeout=10):
        return read_until(self.p, lambda m: m["type"] == "apps" and predicate(m), timeout=timeout)[-1]

    def test_memory_only_never_samples_and_apps_with_gpu_does(self):
        send(self.p, "detail 1")
        first = self.next_apps()
        self.assertEqual(first["gpu"]["status"], "inactive", "Memory alone: no GPU work")
        self.assertIsNone(first["gpu"]["counts"])
        send(self.p, "gpu 1")
        seen = self.next_apps(lambda m: m["gpu"]["status"] not in ("inactive", "warming-up"), timeout=15)
        has_drm = os.path.isdir("/sys/class/drm") and any(n.startswith(("card", "renderD"))
                                                          for n in os.listdir("/sys/class/drm"))
        if not has_drm:
            self.assertEqual((seen["gpu"]["status"], seen["gpu"]["reason"]), ("unsupported", "no-drm-devices"))
        self.assertIn(seen["gpu"]["status"], ("unsupported", "available", "stale"))
        self.requests += 1
        send(self.p, {"command": "apps.query", "requestId": "a:gpu:1", "generation": 1, "sort": "gpu-memory"})
        reply = answer(self.p, "a:gpu:1")[-1]
        self.assertEqual((reply["type"], reply["sort"]), ("apps-page", "gpu-memory"))
        self.assertIn("gpuStatus", reply["rows"][0])
        send(self.p, "gpu 0")
        self.next_apps(lambda m: m["gpu"]["status"] == "inactive")
        send(self.p, "detail 0", "gpu 1")
        send(self.p, {"command": "apps.query", "requestId": "a:gpu:2", "generation": 2})
        self.assertEqual(answer(self.p, "a:gpu:2")[-1]["status"], "inactive", "gpu without detail: no scan")

    def test_demo_never_samples_hardware(self):
        send(self.p, "gpu 1", "detail 1", "demo apps")
        message = self.next_apps(lambda m: m["gpu"].get("demo") is True)
        self.assertTrue(message["gpu"]["synthetic"])
        send(self.p, "demo off")
        self.next_apps(lambda m: not m["gpu"].get("demo"))


if __name__ == "__main__":
    unittest.main()
