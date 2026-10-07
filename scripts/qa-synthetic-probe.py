#!/usr/bin/env python3
"""Opt-in, isolated QA producer. Never scans or signals real app processes.

Use only with a temporary QA plugin wrapper, not the installed live probe.
RAMAN_QA_STATE_HOME and XDG_STATE_HOME must name the same ignored artifact dir.
The real detector/commands/history run unchanged, with synthetic input sources.
"""
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import raman_probe as probe  # noqa: E402


def scene_at(scene, elapsed, warmup):
    """Fixed cases transition within one dispatcher lifetime after warmup."""
    if scene != "cycle":
        return "healthy" if elapsed < warmup else scene
    seconds = elapsed % 80
    return ("healthy" if seconds < 8 or seconds >= 64
            else "warning" if seconds < 26 else "critical" if seconds < 48
            else "missing-psi" if seconds < 56 else "healthy")


def main():
    qa = os.environ.get("RAMAN_QA_STATE_HOME", "")
    state = Path(qa).resolve() if qa else None
    artifacts = (ROOT / ".agent-artifacts").resolve()
    if state is None or artifacts not in state.parents or Path(os.environ.get("XDG_STATE_HOME", "")).resolve() != state:
        sys.exit("QA requires matching RAMAN_QA_STATE_HOME/XDG_STATE_HOME beneath this checkout's .agent-artifacts")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    # Fresh agents can select fixed states for context/failure/capture checks.
    scene = os.environ.get("RAMAN_QA_SCENE", "cycle")
    if scene not in ("cycle", "healthy", "warning", "critical", "missing-psi"):
        sys.exit("RAMAN_QA_SCENE must be cycle, healthy, warning, critical or missing-psi")
    try:
        warmup = int(os.environ.get("RAMAN_QA_WARMUP_SECONDS", "12"))
        if not 8 <= warmup <= 120:
            raise ValueError()
    except ValueError:
        sys.exit("RAMAN_QA_WARMUP_SECONDS must be an integer from 8 to 120")

    def sample():
        name = scene_at(scene, time.monotonic() - started, warmup)
        used, psi = {"healthy": (50, 0), "warning": (80, 6), "critical": (95, 25), "missing-psi": (50, None)}[name]
        total = 8 * 1024 * 1024
        return dict(total=total, used=total * used // 100, available=total * (100 - used) // 100,
                    cached=512 * 1024, swapUsed=256 * 1024, swapTotal=2 * 1024 * 1024,
                    zramRam=128 * 1024, psiSome10=psi, psiSome60=psi, psiFull10=None if psi is None else psi / 2)

    def apps():
        return [dict(id="qa-synthetic", name="Synthetic Browser", kind="app", host="", pss=512 * 1024,
                     swap=128 * 1024, count=3, protected=True, pids=[])]

    probe.read_sample = sample
    probe.scan = apps
    probe.kill_group = lambda group, sig: dict(type="killed", id=group["id"], signal=sig, sent=0,
                                             failed=0, error="QA producer never signals")
    parent = probe.detach_from_kill()
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    try:
        probe.main(parent)
    except (BrokenPipeError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
