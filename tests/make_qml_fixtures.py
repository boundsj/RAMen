"""Build history replies from the real probe code for the offscreen QML harness."""
import json, os, sys, copy
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import raman_probe as probe, raman_history as H

NOW = 1791063600.0
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

def reply(history, window, demo, persistence=None):
    return probe.reply("history", "FIXTURE", demo=demo,
                       persistence=persistence or (H.OFF_INFO if demo else {"owner": True, "load": "ok", "save": "saved", "error": "", "lastSavedAt": NOW - 20}),
                       **history.query(window, NOW))

scene = probe.load_demo("history")
demo = H.History("demo")
probe.seed_demo_history(demo, scene, NOW)
for w in ("5m", "1h", "24h"):
    json.dump(reply(demo, w, True), open(os.path.join(out, "demo-%s.json" % w), "w"))

# Long names: the same history with a 256-character app name in an observed incident.
long = copy.deepcopy(demo)
long.incidents[-1]["context"] = H.sanitize_context({"status": "observed", "at": long.incidents[-1]["start"] - 2,
    "apps": [{"name": "Extremely long application name " * 12, "kb": 3 * 1024 * 1024}] + [{"name": "Slack", "kb": 600000}]})
json.dump(reply(long, "1h", True), open(os.path.join(out, "longnames-1h.json"), "w"))

# Live-like: 12 minutes of calm readings after a restart gap, no PSI (unreadable), no incidents.
live = H.History("boot")
t = NOW - 40 * 60
for i in range(300):
    live.ingest({"used": 6e6, "available": 1.7e6, "total": 7.7e6, "swapUsed": 0, "swapTotal": 0, "zramRam": 0,
                 "psiSome10": None, "psiFull10": None}, t, t)
    t += 2
t = NOW - 12 * 60
live._prev = None
for i in range(360):
    live.ingest({"used": 6.1e6, "available": 1.6e6, "total": 7.7e6, "swapUsed": 0, "swapTotal": 0, "zramRam": 0,
                 "psiSome10": None, "psiFull10": None}, t, t + 1e6)
    t += 2
json.dump(reply(live, "1h", False, {"owner": True, "load": "ok", "save": "saved", "error": "", "lastSavedAt": NOW - 30}),
          open(os.path.join(out, "live-nopsi-1h.json"), "w"))

empty = H.History("boot")
json.dump(reply(empty, "1h", False, {"owner": True, "load": "empty", "save": "pending", "error": "", "lastSavedAt": None}),
          open(os.path.join(out, "live-empty-1h.json"), "w"))
json.dump(reply(empty, "24h", False, {"owner": True, "load": "empty", "save": "pending", "error": "", "lastSavedAt": None}),
          open(os.path.join(out, "live-empty-24h.json"), "w"))
# Exercise the actual scope identity validator after replacing an owned
# directory with a regular file. This fails before platform/mount identity
# discovery, so no injected Linux filesystem identity is needed.
import raman_storage as storage
replacement = os.path.join(out, "directory-to-file")
os.mkdir(replacement)
os.rename(replacement, replacement + "-retained")
with open(replacement, "wb") as handle:
    handle.write(b"synthetic replacement")
try:
    if sys.platform.startswith("linux"):
        storage.capacities({"path": replacement})
    else:
        storage.identity(os.fsencode(replacement), mounts=[])
    raise AssertionError("directory-to-file replacement must fail identity")
except storage.StorageError as exc:
    assert exc.code == "invalid-path"
    with open(os.path.join(out, "storage-directory-to-file.json"), "w") as handle:
        json.dump(storage.error("FIXTURE", exc.code, str(exc)), handle)
# Gallery (tst_gallery.qml): the probe's own demo-scene messages, so offscreen
# previews show exactly what `omarchy-shell boundsj.raman demo <scene>` feeds
# the real widget. Synthetic data only; nothing is scanned or signalled.
import raman_apps
for name in ("green", "yellow", "red"):
    scene = probe.load_demo(name)
    json.dump(dict(scene["summary"], seq=1), open(os.path.join(out, "gallery-%s-summary.json" % name), "w"))
    json.dump({"type": "apps", "apps": probe.demo_preview(scene["apps"])},
              open(os.path.join(out, "gallery-%s-apps.json" % name), "w"))
apps_scene = probe.load_demo("apps")
json.dump(dict(apps_scene["summary"], seq=1), open(os.path.join(out, "gallery-apps-summary.json"), "w"))
for gpu_wanted in (False, True):
    inventory = raman_apps.Inventory()
    rows, gpu = probe.demo_gpu(apps_scene, gpu_wanted)
    snapshot = inventory.publish(rows, NOW, 0.0, probe.DEMO_INVENTORY, apps_scene["cpu"], demo=True, gpu=gpu)
    suffix = "-gpu" if gpu_wanted else ""
    for sort in (("memory", "cpu", "gpu-memory") if gpu_wanted else ("memory", "cpu")):
        page = raman_apps.page_reply(probe.reply("apps-page", "FIXTURE", generation=0), snapshot, "", sort, 0, 50)
        json.dump(page, open(os.path.join(out, "gallery-apps%s-%s.json" % (suffix, sort)), "w"))
    for row, _, _ in snapshot["rows"]:
        source = next(a for a in apps_scene["apps"] if a["id"] == row["id"])
        if not source.get("demoMembers") and not (gpu_wanted and row.get("gpu")):
            continue
        members = [dict(m, start=0) for m in source.get("demoMembers", [])]
        base = probe.reply("apps-details", "FIXTURE", generation=0, id=row["id"], demo=True,
                           snapshot=snapshot["id"], sampledAt=NOW)
        details = raman_apps.details_reply(base, row, members, 8, lambda m: (m.get("command"), False, "demo"))
        json.dump(details, open(os.path.join(out, "gallery-apps%s-details-%s.json" % (suffix, row["id"].replace(":", "-"))), "w"))
print("fixtures:", sorted(os.listdir(out)))
