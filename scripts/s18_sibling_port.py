#!/usr/bin/env python3
"""s18_sibling_port.py — absorb the jsr S17+S18 adapter delta (7cd5911→HEAD)
into canon facet-02's living port, per build/PROVENANCE.md drift policy.

Delta being absorbed (upstream strbtwc/job-sourcing-research):
  - FeishuHireAdapter (+ city map + _PORTAL_COMPANY + _post_json_urllib)
  - XiaohongshuAdapter
  - PaylocityAdapter (class #9)
  - the 4 hermetic test classes (feishuhire/xiaohongshu/s17-round2/paylocity)
  - census evidence payload as a fixture (unitedimaging_pageJobs.json)

Canon deviations re-applied (per ledger):
  #9  tests: _S18_CENSUS_DIR via tests/_paths.py FIXTURES (not jsr repo shape)
  grammar: canon's _SPEC_RE already carries smartrecruiters (canon-native);
  feishuhire|paylocity appended — the union grammar.
"""
import re
import shutil
import subprocess
from pathlib import Path

JSR = Path("/home/z/my-project/job-sourcing-research")
CANON = Path("/home/z/my-project/sibling/ai-job-search-experiments"
             "/facets/02_job_sourcing")

SRC_SB = JSR / "ingest/jobsearch/sources/site_boards.py"
DST_SB = CANON / "build/jobsearch/sources/site_boards.py"
SRC_TEST = JSR / "ingest/tests/test_site_boards.py"
DST_TEST = CANON / "tests/test_site_boards.py"


def extract(src: Path, start_marker: str, end_marker: str) -> str:
    lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
    s = e = None
    for i, l in enumerate(lines):
        if s is None and start_marker in l:
            s = i
        if end_marker in l:
            e = i
            break
    assert s is not None and e is not None, (start_marker, end_marker)
    return "".join(lines[s:e])


def sha16(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


# ── 1. site_boards.py surgery ──────────────────────────────────────────────
sb = DST_SB.read_text(encoding="utf-8")

# 1a. imports: json / cookiejar / urllib / unescape / fetch_text
old_imp = """import re
import sys
import time
from datetime import date, datetime, timezone
from typing import Optional

from ..config import Config
from . import workday
from .base import fetch_json"""
new_imp = """import http.cookiejar
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timezone
from html import unescape
from typing import Optional

from ..config import Config
from . import workday
from .base import fetch_json, fetch_text"""
assert old_imp in sb, "import block drifted"
sb = sb.replace(old_imp, new_imp, 1)

# 1b. grammar: + feishuhire|paylocity (canon already has smartrecruiters)
old_re = ('_SPEC_RE = re.compile(\n'
          '    r"^ats:(greenhouse|ashby|lever|workable|smartrecruiters):"\n'
          '    r"([A-Za-z0-9_.\\-]+)$")')
new_re = ('_SPEC_RE = re.compile(\n'
          '    r"^ats:(greenhouse|ashby|lever|workable|smartrecruiters|'
          'feishuhire|paylocity):"\n'
          '    r"([A-Za-z0-9_.\\-]+)$")')
assert old_re in sb, "spec regex drifted"
sb = sb.replace(old_re, new_re, 1)

# 1c. parse_site error message
old_err = ('f"greenhouse|ashby|lever|workable|smartrecruiters, or '
           "'custom:kind' \"")
new_err = ('f"greenhouse|ashby|lever|workable|smartrecruiters|feishuhire|"\n'
           "        f\"paylocity, or 'custom:kind' \"")
assert old_err in sb, "parse_site error drifted"
sb = sb.replace(old_err, new_err, 1)

# 1d. the adapter block: feishu section start → _ADAPTERS registry (excl.)
block = extract(SRC_SB, "# ── feishuhire (S17:", "_ADAPTERS = {")
# strip nothing else; verify self-containment markers
assert "class FeishuHireAdapter" in block
assert "class XiaohongshuAdapter" in block
assert "class PaylocityAdapter" in block
assert "def _post_json_urllib" in block

old_reg = "_ADAPTERS = {"
assert old_reg in sb
sb = sb.replace(
    old_reg,
    "\n" + block.rstrip("\n") + "\n\n\n" + old_reg, 1)

# 1e. registry entries
old_adapters = ('_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,\n'
                '             "lever": LeverAdapter,\n'
                '             "smartrecruiters": SmartRecruitersAdapter,\n'
                '             "workable": WorkableAdapter,\n'
                '             "bytedance": ByteDanceAdapter, "alibaba": AlibabaAdapter,\n'
                '             "tripcom": TripComAdapter}')
new_adapters = ('_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,\n'
                '             "lever": LeverAdapter,\n'
                '             "smartrecruiters": SmartRecruitersAdapter,\n'
                '             "workable": WorkableAdapter,\n'
                '             "feishuhire": FeishuHireAdapter,\n'
                '             "bytedance": ByteDanceAdapter, "alibaba": AlibabaAdapter,\n'
                '             "tripcom": TripComAdapter,\n'
                '             "xiaohongshu": XiaohongshuAdapter,\n'
                '             "paylocity": PaylocityAdapter}')
assert old_adapters in sb, "registry drifted"
sb = sb.replace(old_adapters, new_adapters, 1)

DST_SB.write_text(sb, encoding="utf-8")
print(f"site_boards.py ported: {len(sb.splitlines())} lines")

# ── 2. test_site_boards.py surgery ─────────────────────────────────────────
tt = DST_TEST.read_text(encoding="utf-8")

block_t = extract(SRC_TEST, "class TestFeishuHireAdapter:",
                  "_S18_CENSUS_DIR =")  # stops before the paylocity preamble
block_p = extract(SRC_TEST, "_S18_CENSUS_DIR =",
                  "def test_census_replay_unitedimaging_evidence") \
    if False else None
# the paylocity preamble + class: _S18_CENSUS_DIR .. EOF
lines = SRC_TEST.read_text(encoding="utf-8").splitlines(keepends=True)
s18_start = next(i for i, l in enumerate(lines) if l.startswith("_S18_CENSUS_DIR"))
paylo = "".join(lines[s18_start:])
# surgery #9: jsr repo-shape census dir → canon FIXTURES
old_dir = ('_S18_CENSUS_DIR = (Path(__file__).resolve().parents[1]\n'
           '                   / "data" / "ats_seed" / "s18_census")')
new_dir = ('_S18_CENSUS_DIR = FIXTURES / "s18_census"   # [S22-VG1] '
           'deviation-9 surgery: canon fixture path')
assert old_dir in paylo, "census dir preamble drifted"
paylo = paylo.replace(old_dir, new_dir, 1)

tt = tt.rstrip("\n") + "\n\n\n" + block_t.rstrip("\n") + "\n\n\n" + paylo
# header note
tt = tt.replace(
    "# [S21-VG2 upstream absorb] WorkableAdapter",
    "# [S22-VG1 upstream absorb] FeishuHire/Xiaohongshu/Paylocity adapters\n"
    "# (jsr S17+S18, pin 36b9f85) — see build/PROVENANCE.md ledger #22.\n"
    "# [S21-VG2 upstream absorb] WorkableAdapter", 1)
DST_TEST.write_text(tt, encoding="utf-8")
print(f"test_site_boards.py ported: {len(tt.splitlines())} lines")

# ── 3. fixtures: census evidence payloads ──────────────────────────────────
fx = CANON / "fixtures/s18_census"
fx.mkdir(parents=True, exist_ok=True)
shutil.copy2(JSR / "ingest/data/ats_seed/s18_census"
             "/unitedimaging_pageJobs.json", fx / "unitedimaging_pageJobs.json")
print("fixture copied:", fx / "unitedimaging_pageJobs.json")

# ── 4. report shas for the PROVENANCE update ──────────────────────────────
for p in (SRC_SB, DST_SB, SRC_TEST, DST_TEST):
    print(f"sha16 {p.name}: {sha16(p)}")
