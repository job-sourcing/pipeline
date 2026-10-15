"""S38 pins — W6-a: companyKey duplicate-identity dedup at the bundle
boundary (audit P3-1, research-review-s37-d1.md).

The S35-b canonicalization left residue: rows whose per-row company
NAME column differs from the watch name (raw-cid fallbacks, collapsed
or rebranded spellings) slugged to a SECOND companyKey for the same
company — riotgames 65 + riot-games 18, alibaba 42 + alibaba-group 7,
plus ten more pairs measured in the 2026-10-15 census (8,083 rows /
392 keys / 177 index companies; every variant key rides exactly ONE
companyId — see the _COMPANY_KEY_ALIAS comment in
scripts/build_ui_bundle.py). The alias resolves the variant slug to
the canonical one, deterministically: exact-match alias first, then
the raw slug.

Row ids are companyId-prefixed (``id == f"{companyId}:{reqId}"``) and
are NEVER touched by the re-key — persisted marks/filters/verdict
jobIds key by id (canon's ai_verdicts.jsonl references
``riotgames:…`` ids that keep working; no verdict or decision id
carries a re-mapped key as its prefix).
"""
import importlib.util
import json
import pathlib
import sys
from collections import Counter, defaultdict

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_ui_bundle", REPO_ROOT / "scripts" / "build_ui_bundle.py")
bundle = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("build_ui_bundle", bundle)
_spec.loader.exec_module(bundle)

LIVE_BUNDLE = REPO_ROOT / "ingest" / "data" / "ui" / "jobs.json"

# the measured pairs, as (variant name string, canonical name string,
# canonical key) — the census evidence, pinned so a map edit that
# drops a pair fails HERE first.
_MEASURED = [
    ("riotgames", "Riot Games", "riot-games"),
    ("alibaba", "Alibaba Group", "alibaba-group"),
    ("beigene", "BeOne Medicines", "beone-medicines"),
    ("Haier", "GE Appliances", "ge-appliances"),
    ("LegendCareers", "Legend Biotech", "legend-biotech"),
    ("Bitdeer Technologies Group", "Bitdeer", "bitdeer"),
    ("Canadiansolar", "Canadian Solar", "canadian-solar"),
    ("faradayfuture", "Faraday Future", "faraday-future"),
    ("Popmart", "Pop Mart", "pop-mart"),
    ("Power Solutions International Inc",
     "Power Solutions International", "power-solutions-international"),
    ("bosonai", "Boson AI", "boson-ai"),
    ("moonshot", "Moonshot AI", "moonshot-ai"),
]

# corporate-identity suffix tokens for the live census — a CLOSED set
# (audit P3-1's list); department-name fragments ('southern', 'tool',
# 'ny'…) are deliberately NOT here: tti's company column carries
# department strings (222 junk keys, an upstream adapter bug tracked
# out of scope — never aliased, never merged by this test).
_CORP_SUFFIXES = {
    "games", "group", "corp", "inc", "technologies", "labs", "studios",
    "holdings", "co", "llc", "usa", "us", "company", "international",
    "industries", "ai",
}


class TestCompanyKeyAlias:
    def test_alias_resolves_every_measured_pair(self):
        f = bundle.company_key
        for variant_name, canonical_name, canonical in _MEASURED:
            assert f(variant_name) == canonical, variant_name
            # the canonical name string itself must slug to the same
            # key (rows from both name spellings now group together)
            assert f(canonical_name) == canonical, canonical_name

    def test_unaliased_companies_ship_as_themselves(self):
        f = bundle.company_key
        assert f("NVIDIA") == "nvidia"
        assert f("Jackery") == "jackery"
        assert f("MINISO (US)") == "miniso"

    def test_exact_match_only_no_fuzzy_matching(self):
        f = bundle.company_key
        # near-misses of alias keys must NOT resolve (no prefix/substring
        # guessing — determinism over cleverness)
        assert f("Riot Games Inc") == "riot-games-inc"
        assert f("riotgamess") == "riotgamess"
        assert f("Alibaba Cloud") == "alibaba-cloud"
        assert f("moonshot labs") == "moonshot-labs"

    def test_alias_composes_with_slug_rules(self):
        # trailing parenthetical is stripped BEFORE the alias lookup
        assert bundle.company_key("Alibaba (Group)") == "alibaba-group"
        assert bundle.company_key("  POPMART  ") == "pop-mart"

    def test_map_shape_no_chains_no_empties(self):
        m = bundle._COMPANY_KEY_ALIAS
        keys, targets = set(m), set(m.values())
        assert keys and targets
        assert all(isinstance(k, str) and k for m_ in (keys, targets)
                   for k in m_), "empty alias entries"
        assert keys.isdisjoint(targets), "alias chains are forbidden"
        assert all(k == k.strip("-") and k == k.lower() for k in keys | targets), \
            "entries must be pre-slugged"


@pytest.mark.skipif(not LIVE_BUNDLE.is_file(),
                    reason="live bundle mirror not present")
class TestLiveBundleCensus:
    """The shipped mirror must carry ZERO duplicate-identity keys.

    (a) every row's companyKey re-derives from its own company string
        (the mirror was built with the alias in force);
    (b) no two distinct keys are concatenation-equivalent;
    (c) no two distinct keys differ by one corporate-suffix token;
    (d) none of the 12 measured variant keys survives;
    (e) every measured board carries exactly ONE key;
    (f) ids stay companyId-prefixed — re-keying never touches ids.
    """

    def _jobs(self):
        return json.loads(
            LIVE_BUNDLE.read_text(encoding="utf-8"))["jobs"]

    def test_rows_rederive_through_the_alias(self):
        for j in self._jobs():
            assert bundle.company_key(j["company"]) == j["companyKey"], j["id"]

    def test_zero_concatenation_equivalent_keys(self):
        keys = Counter(j["companyKey"] for j in self._jobs())
        by_core = defaultdict(set)
        for k in keys:
            by_core[k.replace("-", "")].add(k)
        dupes = [sorted(ks) for ks in by_core.values() if len(ks) > 1]
        assert not dupes, f"concatenation-equivalent keys: {dupes}"

    def test_zero_corporate_suffix_pairs(self):
        keys = sorted({j["companyKey"] for j in self._jobs()})
        dupes = []
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                for suf in _CORP_SUFFIXES:
                    if b == f"{a}-{suf}" or a == f"{b}-{suf}":
                        dupes.append((a, b, suf))
        assert not dupes, f"corporate-suffix identity pairs: {dupes}"

    def test_measured_variant_keys_are_gone(self):
        keys = {j["companyKey"] for j in self._jobs()}
        survivors = keys & set(bundle._COMPANY_KEY_ALIAS)
        assert not survivors, f"variant keys still shipping: {survivors}"

    def test_measured_boards_carry_one_key(self):
        measured_cids = {
            "riotgames", "alibaba", "beone", "gea", "legendbiotech",
            "bitdeer", "canadiansolar", "faradayfuture", "popmart",
            "psi", "bosonai", "moonshot",
        }
        cid_keys = defaultdict(set)
        for j in self._jobs():
            if j["companyId"] in measured_cids:
                cid_keys[j["companyId"]].add(j["companyKey"])
        split = {cid: sorted(ks) for cid, ks in cid_keys.items()
                 if len(ks) != 1}
        assert not split, f"boards still split across keys: {split}"

    def test_ids_stay_company_prefixed(self):
        # the P3-2 invariant the audit asked to pin: re-keying
        # companyKey NEVER rewrites ids — persisted marks, filters and
        # canon's verdict jobIds all key by id
        for j in self._jobs():
            assert j["id"] == f"{j['companyId']}:{j['reqId']}", j["id"]
