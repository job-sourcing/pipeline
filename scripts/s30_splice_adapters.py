#!/usr/bin/env python3
"""S30: splice the 5 new adapters into site_boards.py + update the
4 registration seams (_SPEC_RE, parse_site error, _ADAPTER_GEO_SCOPE,
_ADAPTER_ACCEPTS_REMOTE, _ADAPTERS registry)."""
import pathlib

SB = pathlib.Path("/home/z/research/ingest/jobsearch/sources/site_boards.py")
src = SB.read_text(encoding="utf-8")
frag = pathlib.Path(
    "/home/z/research/scripts/s30_adapters_fragment.py").read_text(
    encoding="utf-8")

# ── 1. insert the adapter classes before the registry ──────────────
ANCHOR = '_ADAPTERS = {"greenhouse": GreenhouseAdapter, "ashby": AshbyAdapter,'
assert ANCHOR in src, "registry anchor missing"
assert "class SmartRecruitersAdapter" not in src, "already spliced"
src = src.replace(ANCHOR, frag + "\n\n" + ANCHOR, 1)

# ── 2. _SPEC_RE: add the four new ats kinds ─────────────────────────
NL = chr(10)
old_re = ('    r"teamtailor|radancy|rippling|breezy|jobvite|oraclehcm|'
          'bamboohr|j2w|"' + NL +
          '    r"ultipro|talentadore|workstream|ttiproxy|sanity|'
          'wpjobboard|wuxibio|"')
new_re = (old_re + NL +
          '    r"smartrecruiters|trakstar|icims|paycom|"')
assert old_re in src, "_SPEC_RE anchor missing"
src = src.replace(old_re, new_re, 1)

# ── 3. parse_site error message: add the kinds ──────────────────────
old_err = ('        f"workstream|ttiproxy|sanity|wpjobboard|wuxibio|'
           'antintl|"')
new_err = ('        f"workstream|ttiproxy|sanity|wpjobboard|wuxibio|'
           'antintl|"\n'
           '        f"smartrecruiters|trakstar|icims|paycom|"')
assert old_err in src, "parse_site error anchor missing"
src = src.replace(old_err, new_err, 1)

# ── 4. _ADAPTER_GEO_SCOPE: add the four kinds ───────────────────────
old_geo = ('_ADAPTER_GEO_SCOPE = {"greenhouse", "ashby", "lever", "workable",\n'
           '                      "feishuhire", "adp", "paylocity"}')
new_geo = ('_ADAPTER_GEO_SCOPE = {"greenhouse", "ashby", "lever", "workable",\n'
           '                      "feishuhire", "adp", "paylocity",\n'
           '                      # S30 RE wave: all four carry country\n'
           '                      # evidence (SR ISO code; trakstar\n'
           '                      # country span; icims loc-code prefix;\n'
           '                      # paycom plain-loc + remoteType)\n'
           '                      "smartrecruiters", "trakstar", "icims",\n'
           '                      "paycom"}')
assert old_geo in src, "geo scope anchor missing"
src = src.replace(old_geo, new_geo, 1)

# ── 5. _ADAPTER_ACCEPTS_REMOTE: add the four kinds ──────────────────
old_rem = ('_ADAPTER_ACCEPTS_REMOTE = {"greenhouse": True, "ashby": True,\n'
           '                           "lever": True, "workable": True}')
new_rem = ('_ADAPTER_ACCEPTS_REMOTE = {"greenhouse": True, "ashby": True,\n'
           '                           "lever": True, "workable": True,\n'
           '                           # S30: SR location.remote flag;\n'
           '                           # trakstar/icims/paycom remote\n'
           '                           # tokens in location text\n'
           '                           "smartrecruiters": True,\n'
           '                           "trakstar": True, "icims": True,\n'
           '                           "paycom": True}')
assert old_rem in src, "accepts_remote anchor missing"
src = src.replace(old_rem, new_rem, 1)

# ── 6. registry: add the five classes ───────────────────────────────
old_reg = '             "dify": DifyAdapter}'
new_reg = ('             "dify": DifyAdapter,\n'
           '             # S30 RE wave (contracts in '
           'audit/s30_fetchkit_probes.md)\n'
           '             "smartrecruiters": SmartRecruitersAdapter,\n'
           '             "trakstar": TrakstarAdapter,\n'
           '             "icims": ICIMSAdapter,\n'
           '             "paycom": PaycomAdapter,\n'
           '             "a123": A123Adapter}')
assert old_reg in src, "registry tail anchor missing"
src = src.replace(old_reg, new_reg, 1)

SB.write_text(src, encoding="utf-8")
print("spliced OK; new size:", len(src.splitlines()), "lines")
