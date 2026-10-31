#!/usr/bin/env python3
"""s51_spike_launch.py — launch the JD-spike shard daemons + the git
checkpoint supervisor.

  --up        spawn the shard processes (detached, double-fork) + supervisor
  --status    poll: per-shard progress from the .log tails + cache state
  --merge     one merge pass NOW (shards → jd_cache.jsonl) — no git
  --stop      touch the stop-flag file; shards exit at the next row
              boundary (the append-durable design loses nothing)

The supervisor (also detached) every CHECKPOINT_S seconds: merges all
shard files into ingest/data/roles/jd_cache.jsonl (union-merge by
(source,url), newest fetched_at wins — the F6 both-sided semantic),
commits ONLY those state files, pushes. On push rejection (the main
session moved main), the commit is soft-reset and retried next cycle —
never force, never rebase a dirty tree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SPIKE = REPO / "scripts/s51_jd_spike.py"
SHARD_DIR = REPO / "ingest/data/roles/jd_spike"
CACHE = REPO / "ingest/data/roles/jd_cache.jsonl"
FLAG = SHARD_DIR / "STOP"
CHECKPOINT_S = 600

# family → shard count (load-shaped: gen is 2/3 of the work; the
# todo files are PRE-SHARDED so each shard process is tiny — the 68MB
# gen bundle parses ONCE in --prepare, never per shard)
LAYOUT = {"pm": 2, "dmm": 5, "gen": 11}


def prepare() -> None:
    """One-shot: per family, select the fc_relay todo (careerjet-class
    excluded, cache-resolved filtered) and split into per-shard files."""
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "ingest"))
    import jd_refetch as jr  # noqa: PLC0415
    from s51_jd_spike import EXCLUDE_SOURCES  # noqa: PLC0415
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    for fam, shards in LAYOUT.items():
        jr._set_family(fam)
        jobs = jr._load_bundle()
        targets = jr._select_targets(jobs, "fc_relay")
        cache = jr._load_cache_map()

        def _resolved(key: str) -> bool:
            ent = cache.get(key, {})
            st = ent.get("status", "")
            if st == "ok":
                return True
            same = ent.get("transport", "direct") == "fc_relay"
            if st.startswith("q:"):
                return same or ent.get("transport", "") == ""
            if st.startswith("http-error:4"):
                return same
            return False

        todo = [t for t in targets
                if t["source"] not in EXCLUDE_SOURCES
                and not _resolved(f"{t['source']}|{t['url']}")]
        for i in range(shards):
            part = todo[i::shards]
            p = SHARD_DIR / f"todo_{fam}_{i}.jsonl"
            with p.open("w") as f:
                for t in part:
                    f.write(json.dumps(
                        {"source": t["source"], "url": t["url"],
                         "id": t.get("id") or ""},
                        ensure_ascii=False) + "\n")
            print(f"{fam}/{i}: {len(part)} rows")
        print(f"{fam}: todo={len(todo)} "
              f"(targets={len(targets)}, excluded-class filtered)")


def _daemonize(argv: list[str], log: Path) -> None:
    """Double-fork detach (survives the toolcall)."""
    pid = os.fork()
    if pid == 0:
        os.setsid()
        pid2 = os.fork()
        if pid2 == 0:
            os.chdir(REPO)
            fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                         0o644)
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            os.close(fd)
            os.execv(sys.executable,
                     [sys.executable, *argv])
        os._exit(0)
    os.waitpid(pid, 0)


def up() -> None:
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    if FLAG.exists():
        FLAG.unlink()
    n = 0
    for fam, shards in LAYOUT.items():
        for i in range(shards):
            todo = SHARD_DIR / f"todo_{fam}_{i}.jsonl"
            if not todo.exists():
                print(f"MISSING {todo} — run --prepare first",
                      file=sys.stderr)
                return
            log = SHARD_DIR / f"{fam}_{i}.out"
            _daemonize([str(SPIKE), "--family", fam,
                        "--shard", str(i), "--shards", str(shards),
                        "--todo-file", str(todo)], log)
            n += 1
            time.sleep(0.5)
    # the supervisor
    _daemonize([str(Path(__file__).resolve()), "--supervise"],
               SHARD_DIR / "supervisor.out")
    print(f"launched {n} shard daemons + supervisor; "
          f"checkpoints every {CHECKPOINT_S}s")


def merge_once() -> int:
    """Shards → jd_cache.jsonl union-merge. Returns rows merged."""
    if not SHARD_DIR.exists():
        return 0
    # load the live cache (the F6 union-merge loader)
    sys.path.insert(0, str(REPO / "scripts"))
    sys.path.insert(0, str(REPO / "ingest"))
    import jd_refetch as jr  # noqa: PLC0415
    base = jr._load_cache_map()
    added = 0
    for f in sorted(SHARD_DIR.glob("*.jsonl")):
        # ONLY the result shards — never the todo_* inputs (they carry
        # {source,url,id} without status/fetched_at and would clobber
        # real cache entries in the union-merge)
        if f.name.startswith("todo_"):
            continue
        for line in f.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = f"{d.get('source', '')}|{d.get('url', '')}"
            prev = base.get(k)
            if (not prev
                    or d.get("fetched_at", "")
                    >= prev.get("fetched_at", "")):
                base[k] = d
                added += 1
    jr._write_cache(base)
    return added


def supervise() -> None:
    while True:
        time.sleep(CHECKPOINT_S)
        if FLAG.exists():
            print("stop flag — supervisor exiting", flush=True)
            return
        try:
            added = merge_once()
        except Exception as e:                  # noqa: BLE001
            print(f"merge failed: {e!r}", flush=True)
            continue
        r = subprocess.run(
            ["git", "add",
             "ingest/data/roles/jd_cache.jsonl",
             "ingest/data/roles/jd_spike/"],
            cwd=REPO, capture_output=True, text=True)
        r = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO, capture_output=True)
        if r.returncode != 0:
            c = subprocess.run(
                ["git", "commit", "-q", "-m",
                 f"jd-spike checkpoint (+{added} rows) [skip ci]"],
                cwd=REPO, capture_output=True, text=True)
            p = subprocess.run(["git", "push", "-q", "origin", "main"],
                               cwd=REPO, capture_output=True, text=True)
            if p.returncode != 0:
                # main moved under us: soft-reset, retry next cycle
                subprocess.run(
                    ["git", "reset", "--soft", "HEAD~1"], cwd=REPO,
                    capture_output=True)
                print(f"push rejected (will retry): "
                      f"{p.stderr[:120]}", flush=True)
            else:
                print(f"checkpoint pushed (+{added})", flush=True)


def status() -> None:
    print(f"STOP flag: {FLAG.exists()}")
    if not SHARD_DIR.exists():
        print("no spike dir")
        return
    for f in sorted(SHARD_DIR.glob("*.log")):
        lines = f.read_text().splitlines()
        tail = lines[-1] if lines else "-"
        print(f"{f.stem:10} {tail[:150]}")
    # live progress from the .out files (the running daemons)
    for f in sorted(SHARD_DIR.glob("*.out")):
        lines = [l for l in f.read_text().splitlines() if l.strip()]
        tail = lines[-1] if lines else "-"
        print(f"out:{f.stem:9} {tail[:150]}")
    # cache stats
    import collections
    n = 0
    st = collections.Counter()
    if CACHE.exists():
        with CACHE.open() as f:
            for line in f:
                n += 1
                try:
                    st[json.loads(line).get("status", "?").split(":")[0]] += 1
                except Exception:               # noqa: BLE001
                    pass
    print(f"jd_cache.jsonl: {n} rows | {dict(st)}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    if mode == "--up":
        up()
    elif mode == "--prepare":
        prepare()
    elif mode == "--supervise":
        supervise()
    elif mode == "--merge":
        print("merged rows:", merge_once())
    elif mode == "--status":
        status()
    elif mode == "--stop":
        FLAG.write_text("stop\n")
        print("stop flag written")
    else:
        print(__doc__)
