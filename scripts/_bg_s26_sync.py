#!/usr/bin/env python3
"""_bg final /home/sync rsync for S26 close (tree mirror, resumable)."""
import subprocess, sys
src = "/home/z/research/"
dst = "/home/sync/job-sourcing-research/"
# --delete off (S22 incident rule: never delete in the backup), -a
r = subprocess.run(["rsync", "-a", src, dst], capture_output=True, text=True)
print("rc:", r.returncode)
print(r.stderr[-2000:] if r.stderr else "clean")
