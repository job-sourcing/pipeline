"""S32 splice: licompany adapter -> site_boards.py + driver patches.

Anchor-based (unique-match asserts, the SKILL display-glitch protocol):
  1. site_boards.py: insert the fragment before `_ADAPTERS = {`
     (i.e. after the last adapter class), register "licompany" in the
     registry dict, extend the grammar-doc comment.
  2. scripts/gha_board_watch.py: (a) variant guard claims
     LICOMPANY_CONTRACTS names/variants (P0-1 both directions);
     (b) skip_li_corroboration flag gates BOTH corroborate_new calls
     (P1-1 — the source IS LinkedIn; self-corroboration is waste).
"""
import re, sys
from pathlib import Path

ROOT = Path("/home/z/research")
SB = ROOT / "ingest/jobsearch/sources/site_boards.py"
WD = ROOT / "scripts/gha_board_watch.py"
FRAG = (ROOT / "scripts/s32_licompany_fragment.py").read_text()

# extract the FRAGMENT payload (between the markers)
m = re.search(r"FRAGMENT = r'''(.*?)'''\n", FRAG, re.DOTALL)
assert m, "fragment payload not found"
payload = m.group(1)

sb = SB.read_text()
assert "LICOMPANY_CONTRACTS" not in sb, "already spliced (site_boards)"

# 1. insert the fragment right before the registry line
anchor = "_ADAPTERS = {\"greenhouse\": GreenhouseAdapter,"
assert sb.count(anchor) == 1, f"registry anchor x{sb.count(anchor)}"
sb = sb.replace(anchor, payload.lstrip("\n") + "\n\n" + anchor)

# 2. register the kind
reg_anchor = ('             "tribepad": TribepadAdapter, '
              '"cwsjobs": CWSJobsAdapter}')
assert sb.count(reg_anchor) == 1, "registry tail anchor"
sb = sb.replace(reg_anchor, reg_anchor[:-1] + ",\n"
                '             "licompany": LinkedInCompanyAdapter}')

SB.write_text(sb)
print("site_boards.py: fragment inserted + licompany registered")
compile(sb, str(SB), "exec")

# 3. driver: variant guard claims the contracts
wd = WD.read_text()
assert "LICOMPANY_CONTRACTS" not in wd, "already spliced (driver)"
g_anchor = """    for w in watches:
        base = str(w.get("company") or "").strip().lower()
        if base:
            _claim(base, w["label"])
        for v in (w.get("li_variants") or []):
            v = str(v).strip().lower()
            if v:
                _claim(v, w["label"])
"""
assert wd.count(g_anchor) == 1, "guard anchor"
g_new = g_anchor + """
    # S32 (review P0-1): licompany contracts live in code, not config —
    # claim their display names + li_variants HERE so a future contract
    # colliding with a watch's company/variants fails at config load in
    # BOTH directions (the EcoFlow/MINISO/CelerData class of overlap).
    try:
        from jobsearch.sources.site_boards import LICOMPANY_CONTRACTS
        for key, c in LICOMPANY_CONTRACTS.items():
            nm = str(c.get("company") or "").strip().lower()
            if nm:
                _claim(nm, f"licompany:{key}")
            for v in (c.get("li_variants") or []):
                v = str(v).strip().lower()
                if v:
                    _claim(v, f"licompany:{key}")
    except ImportError:
        pass  # library-only contexts (tests without the package)
"""
wd = wd.replace(g_anchor, g_new)

# 4. driver: skip_li_corroboration gates both corroborate_new calls
c1_anchor = """    signals = corroborate_new((new_rows + backlog_rows) if country_client
                              else enrich_input, w["company"], cfg,
                              deadline, title_search_budget=[TITLE_SEARCH_MAX])
"""
assert wd.count(c1_anchor) == 1, f"corroborate-1 anchor x{wd.count(c1_anchor)}"
c1_new = """    # S32 (review P1-1): licompany boards ARE LinkedIn — the
    # corroboration lane would index LinkedIn against itself (pure
    # request waste; the adapter's own detail fetch already carries the
    # signals). Opt-out per watch entry.
    signals = ({} if w.get("skip_li_corroboration")
               else corroborate_new(
                   (new_rows + backlog_rows) if country_client
                   else enrich_input, w["company"], cfg,
                   deadline,
                   title_search_budget=[TITLE_SEARCH_MAX]))
"""
wd = wd.replace(c1_anchor, c1_new)

c2_anchor = """                sigs2 = corroborate_new(list(recent.values()),
                                        w["company"], cfg, deadline)
"""
assert wd.count(c2_anchor) == 1, f"corroborate-2 anchor x{wd.count(c2_anchor)}"
c2_new = """                sigs2 = ({} if w.get("skip_li_corroboration")
                         else corroborate_new(list(recent.values()),
                                              w["company"], cfg, deadline))
"""
wd = wd.replace(c2_anchor, c2_new)
WD.write_text(wd)
print("gha_board_watch.py: guard extension + corroboration skip")
compile(wd, str(WD), "exec")
print("both files compile OK")
