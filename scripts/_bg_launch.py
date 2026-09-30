#!/usr/bin/env python3
"""Detached launcher for long-running node workers (container-safe).

Double-fork reparents to PID 1 — escapes the toolcall descendant-tree
reaping (plain `nohup ... &` / setsid die at bash-toolcall end).

Usage:  python3 scripts/_bg_launch.py <logfile> <cmd> [args...]
Then:   tail <logfile>  to watch progress.
"""
import os
import sys

if len(sys.argv) < 3:
    print('usage: _bg_launch.py <logfile> <cmd> [args...]')
    sys.exit(1)
LOG = sys.argv[1]
CMD = sys.argv[2:]


def daemonize():
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    sys.stdout.flush()
    sys.stderr.flush()
    logfd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    devnull = os.open('/dev/null', os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(logfd, 1)
    os.dup2(logfd, 2)


daemonize()
# S22 (4th recurrence of the baked-path class): chdir from the script's OWN
# realpath — the repo root is one dirname above scripts/. Never a literal.
_here = os.path.dirname(os.path.realpath(__file__))
_repo = os.path.dirname(_here)
os.chdir(_repo)
env = dict(os.environ)
os.execvp(CMD[0], CMD)
