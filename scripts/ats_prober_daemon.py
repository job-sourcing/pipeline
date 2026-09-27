#!/usr/bin/env python3
"""Double-forked background prober for the ATS directory.

Runs build_ats_directory.py batches in a loop until nothing is pending,
checkpointing to the DB after every batch. Survives across toolcalls
(reparents to PID 1). Heartbeat: ingest/data/ats_directory_heartbeat.txt
"""
import os
import subprocess
import sys
import time
from pathlib import Path


def daemonize():
    if os.fork():
        sys.exit(0)
    os.setsid()
    if os.fork():
        sys.exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open("/dev/null", os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)


def main():
    daemonize()
    # Derive paths from this script's location so any checkout works
    # (CodeRabbit round-1: a hardcoded /home/z path breaks other machines).
    ingest = str(Path(__file__).resolve().parents[1] / "ingest")
    heartbeat = f"{ingest}/data/ats_directory_heartbeat.txt"
    log = f"{ingest}/data/ats_directory_prober.log"
    Path(ingest + "/data").mkdir(parents=True, exist_ok=True)
    # Claim the directory immediately (a manual --batch run between our
    # batches must refuse to race us) and mark our children so THEY can
    # run while the heartbeat is fresh (build_ats_directory checks the
    # ATS_PROBER_DAEMON env marker).
    with open(heartbeat, "w") as hb:
        hb.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} daemon=start\n")
    child_env = dict(os.environ)
    child_env["ATS_PROBER_DAEMON"] = "1"
    batches = 0
    with open(log, "a") as lf:
        lf.write(f"[{time.strftime('%H:%M:%S')}] prober daemon started\n")
    while batches < 400:
        # one batch of 60 with 0.3s spacing ≈ 60-90s (worst case with slow
        # boards can exceed it — TimeoutExpired is caught, batch shrinks)
        r = None
        try:
            r = subprocess.run(
                [sys.executable,
                 f"{ingest}/scripts/build_ats_directory.py",
                 "--batch", "60", "--sleep", "0.3", "--retry-errors"],
                capture_output=True, text=True, timeout=170,
                cwd=ingest, env=child_env)
        except subprocess.TimeoutExpired:
            with open(log, "a") as lf:
                lf.write(f"[{time.strftime('%H:%M:%S')}] batch {batches + 1} "
                         f"TIMED OUT (slow boards) — retrying smaller\n")
            # retry smaller so slow batches can't kill the daemon
            try:
                r = subprocess.run(
                    [sys.executable,
                     f"{ingest}/scripts/build_ats_directory.py",
                     "--batch", "25", "--sleep", "0.3", "--retry-errors"],
                    capture_output=True, text=True, timeout=170,
                    cwd=ingest, env=child_env)
            except subprocess.TimeoutExpired:
                with open(log, "a") as lf:
                    lf.write(f"[{time.strftime('%H:%M:%S')}] batch {batches + 1} "
                             f"timed out twice — sleeping 30s\n")
                time.sleep(30)
                continue

        batches += 1
        if r is None or r.returncode != 0:
            # A failed child must not masquerade as progress in the heartbeat.
            err = (r.stderr if r else "no result").strip().splitlines()
            with open(log, "a") as lf:
                lf.write(f"[{time.strftime('%H:%M:%S')}] batch {batches} FAILED "
                         f"(rc={r.returncode if r else '?'}): "
                         f"{err[-1] if err else 'no stderr'}\n")
            time.sleep(10)
            continue

        with open(log, "a") as lf:
            tail = (r.stdout or "").strip().splitlines()
            lf.write(f"[{time.strftime('%H:%M:%S')}] batch {batches}: "
                     f"{tail[-1] if tail else 'no output'}\n")
        with open(heartbeat, "w") as hb:
            hb.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} batch={batches}\n")
        if "nothing pending" in (r.stdout or ""):
            with open(log, "a") as lf:
                lf.write(f"[{time.strftime('%H:%M:%S')}] all done\n")
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
