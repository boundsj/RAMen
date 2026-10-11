#!/usr/bin/env python3
"""Measure two progressive scans on a disposable Linux fixture, then remove it.

Everything is created below .agent-artifacts. This measures direct scanner
calls on empty files; it is not a cold-drive or end-to-end UI benchmark.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import raman_storage as storage
import raman_storage_jobs as jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=60_000)
    args = parser.parse_args()
    if not sys.platform.startswith('linux') or not 1 <= args.files <= 1_000_000:
        parser.error('use Linux and between 1 and 1,000,000 files')
    artifacts = ROOT / '.agent-artifacts' / 'storage-job-check'
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=artifacts) as temporary:
        base = Path(temporary)
        fixture = base / 'fixture'
        fixture.mkdir()
        groups = (args.files + 999) // 1000
        for batch in range(groups):
            folder = fixture / ('group-%04d' % batch)
            folder.mkdir()
            for index in range(min(1000, args.files - batch * 1000)):
                (folder / ('file-%04d' % index)).touch()
        cache = storage.Cache(str(base / 'raman' / 'storage'))
        try:
            results = []
            for attempt in range(2):
                started = time.monotonic()
                snapshot = jobs.start(dict(path=str(fixture)), cache)['snapshot']
                slices = []
                while snapshot['status'] != 'ready':
                    began = time.monotonic()
                    snapshot = jobs.step(dict(jobId=snapshot['snapshotId']), cache)['snapshot']
                    slices.append(time.monotonic() - began)
                    if snapshot['pauseReason']:
                        raise RuntimeError('fixture scan paused: ' + snapshot['pauseReason'])
                seconds = time.monotonic() - started
                if snapshot['coverage'] != 'complete' or snapshot['entries'] != args.files + groups + 1:
                    raise RuntimeError('fixture inventory is incomplete')
                began = time.monotonic()
                ledger = jobs.children(dict(jobId=snapshot['snapshotId']), cache)
                query_seconds = time.monotonic() - began
                if ledger['total'] != groups:
                    raise RuntimeError('fixture root ledger is incomplete')
                results.append(dict(attempt=attempt + 1, entries=snapshot['entries'],
                                    seconds=seconds, slices=len(slices),
                                    maxSliceSeconds=max(slices), rootQuerySeconds=query_seconds,
                                    bytes=cache.safe_stat('job-' + snapshot['snapshotId'] + '.sqlite').st_size,
                                    known=snapshot['knownAllocatedBytes'], coverage=snapshot['coverage']))
            print(json.dumps(results, indent=2))
        finally:
            cache.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
