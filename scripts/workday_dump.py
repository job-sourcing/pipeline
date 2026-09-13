#!/usr/bin/env python3
"""THIN WRAPPER — deprecated entry point (design D3 finally honored).

This was the v1 NVIDIA dumper (S5-2 pilot, 2026-09-08): a full parallel
implementation of list/details/finish with the v1 10-column CSV. The
generic board-dump v2 (scripts/board_dump.py) superseded it on 2026-09-09
(30→31-column CSV, corroboration signals, B1/B2/B5 contracts, per-row
checkpointing) — and S7-B1 SA-2 flagged the two implementations sharing
one output path as a footgun: running the v1 script against a v2 dataset
would silently write the old 10-column CSV over the new deliverable.

All invocations now forward to board_dump.py. The phased CLI is
compatible (list/details/corroborate/finish); v1's --phase all becomes
list (see board_dump --help for the full contract).

Usage (unchanged from v1, now forwarded):
  python3 scripts/workday_dump.py --phase list
  python3 scripts/workday_dump.py --phase details   (resumable)
  python3 scripts/workday_dump.py --phase finish
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    # forward argv verbatim; board_dump's argparse handles the contract
    sys.argv[0] = str(HERE / "board_dump.py")
    runpy.run_path(str(HERE / "board_dump.py"), run_name="__main__")
