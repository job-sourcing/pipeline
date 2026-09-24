#!/usr/bin/env python3
"""S17 probe: LinkedIn guest-search card-company strings for the three
new companies (minimax / shengshu / horizon) — the S15 exact-match
lesson: li_variants must be the EXACT card strings (lowercased),
probed live, never guessed."""
import sys

sys.path.insert(0, '/home/z/my-project/job-sourcing-research/ingest')
from jobsearch.config import Config          # noqa: E402
from jobsearch import corroborate            # noqa: E402


def probe(company: str) -> None:
    cfg = Config()
    prov = corroborate.LinkedInSignalProvider(cfg)
    variants = [company.strip().lower()]
    seen: set[str] = set()
    cards, _off, _ex, _exc = prov._paginate_query(
        company, "United States", variants, 2, 40, 0, seen)
    print(f"===== {company}: {len(cards)} cards =====")
    companies: dict[str, int] = {}
    for c in cards:
        cc = str(c.get("company") or "").strip()
        companies[cc] = companies.get(cc, 0) + 1
    for cc, n in sorted(companies.items(), key=lambda x: -x[1]):
        print(f"  {cc!r}: {n}")
    sample = [c.get("title") for c in cards[:3]]
    print("  sample titles:", sample)


if __name__ == "__main__":
    for co in (sys.argv[1:] or ["MiniMax", "Shengshu", "Horizon Robotics"]):
        try:
            probe(co)
        except Exception as e:
            print(f"{co}: ERR {type(e).__name__}: {e}")
