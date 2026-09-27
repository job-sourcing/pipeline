#!/usr/bin/env python3
"""Double-forked cron scheduler for the jobsearch pipeline (Sprint 3).

Drives `search-jobs` on a fixed cadence with the alert pass on, so new
high-fit postings reach Telegram without any interactive step. Modeled on
scripts/ats_prober_daemon.py (the container has no cron/systemd; the
double-fork survives across toolcalls by reparenting to PID 1).

Env knobs (all optional):
  JOBSEARCH_SCHED_QUERIES   semicolon-separated queries; each entry is
                            "keywords" or "keywords|location" (default:
                            "python developer|Remote")
  JOBSEARCH_SCHED_INTERVAL  minutes between cycles (default 240 = 4h;
                            minimum 30 to stay polite to the boards)
  JOBSEARCH_SCHED_NUM       --num per source per cycle (default 20)

Heartbeat: ingest/data/search_scheduler_heartbeat.txt
Log:       ingest/data/search_scheduler.log

Usage: python3 scripts/search_scheduler.py            (background)
       python3 scripts/search_scheduler.py --foreground   (one cycle, stdout)
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


def _parse_queries(raw: str) -> list[tuple[str, str]]:
    queries = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "|" in entry:
            keywords, _, location = entry.partition("|")
        else:
            keywords, location = entry, "Remote"
        queries.append((keywords.strip(), (location or "Remote").strip()))
    return queries or [("python developer", "Remote")]


def run_cycle(ingest: str, queries: list[tuple[str, str]], num: int,
              log) -> None:
    for keywords, location in queries:
        cmd = [sys.executable, "-m", "jobsearch.cli", keywords,
               "--location", location, "--num", str(num),
               "--alerts"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=280, cwd=ingest)
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            interesting = [l for l in tail
                           if "Telegram" in l or "Alerts" in l
                           or "degraded" in l]
            log.write(f"[{time.strftime('%H:%M:%S')}] '{keywords}' "
                      f"exit={r.returncode}"
                      + (f" | {'; '.join(interesting[-3:])}"
                         if interesting else "") + "\n")
            log.flush()
        except subprocess.TimeoutExpired:
            log.write(f"[{time.strftime('%H:%M:%S')}] '{keywords}' "
                      f"TIMED OUT (280s)\n")
            log.flush()


def main() -> None:
    foreground = "--foreground" in sys.argv
    if not foreground:
        daemonize()

    ingest = str(Path(__file__).resolve().parents[1] / "ingest")
    data = Path(ingest) / "data"
    data.mkdir(parents=True, exist_ok=True)
    heartbeat = data / "search_scheduler_heartbeat.txt"
    log_path = data / "search_scheduler.log"

    queries = _parse_queries(
        os.environ.get("JOBSEARCH_SCHED_QUERIES",
                       "python developer|Remote"))
    interval_min = max(30, int(os.environ.get("JOBSEARCH_SCHED_INTERVAL",
                                              "240") or 240))
    num = int(os.environ.get("JOBSEARCH_SCHED_NUM", "20") or 20)

    def beat(status: str) -> None:
        heartbeat.write_text(
            f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {status} "
            f"queries={len(queries)} interval={interval_min}m\n")

    beat("daemon=start")
    with open(log_path, "a") as log:
        log.write(f"[{time.strftime('%H:%M:%S')}] scheduler started: "
                  f"{len(queries)} query(ies), every {interval_min}m\n")
        cycles = 0
        while True:
            cycles += 1
            beat(f"cycle={cycles}")
            run_cycle(ingest, queries, num, log)
            if foreground:
                break
            # sleep in 30s slices so a stale heartbeat is never more than
            # 30s old while idle (operators check heartbeat freshness)
            for _ in range(interval_min * 2):
                time.sleep(30)
                beat(f"cycle={cycles} idle")


if __name__ == "__main__":
    main()
