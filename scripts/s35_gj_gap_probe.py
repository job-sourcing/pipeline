#!/usr/bin/env python3
"""s35_gj_gap_probe.py — probe gj for the 33 li-dormant gap companies.

The S35 directive #2 evidence pass: these companies have (a) no own
board, (b) zero/low LinkedIn-guest cards (li_dormant), (c) NO gj
catch-all contract. 1 firecrawl credit per company. Output:
audit/s35_aggregator/gj_gap_probe.md (per-company card table) + the
saved markdown evidence per company for contract pinning.

Verdicts per company: REAL (>=1 variant-matched card), NOISE-WALL
(cards but none match), or EMPTY (no cards). Tiering: REAL->monthly gj
lane; NOISE-WALL/EMPTY -> quarterly probe (or park).
"""
import json
import urllib.parse
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path("/home/z/repos/job-sourcing-research")
OUT = REPO / "audit/s35_aggregator"
OUT.mkdir(parents=True, exist_ok=True)

COMPANIES = [
    ("affine", "AFFiNE"), ("anycubic", "Anycubic"), ("aqara", "Aqara"),
    ("baseus", "Baseus"), ("bentoml", "BentoML"), ("bluetti", "Bluetti"),
    ("bougerv", "BougeRV"), ("centurygames", "Century Games"),
    ("databend", "Databend"), ("dreo", "Dreo"), ("elegoo", "Elegoo"),
    ("fotor", "Fotor"), ("hollyland", "Hollyland"),
    ("lianlian", "LianLian Global"), ("merach", "Merach"),
    ("moondrop", "Moondrop"), ("notta", "Notta"),
    ("petlibro", "Petlibro"), ("pixverse", "PixVerse"),
    ("polloai", "Pollo AI"), ("sengled", "Sengled"), ("sharge", "Sharge"),
    ("skyworth", "Skyworth"), ("switchbot", "SwitchBot"),
    ("tdengine", "TDengine"), ("timekettle", "Timekettle"),
    ("torras", "Torras"), ("vidnoz", "Vidnoz"), ("xgimi", "XGIMI"),
    ("xreal", "XREAL"), ("xtransfer", "XTransfer"), ("zendure", "Zendure"),
    ("zhiyun", "Zhiyun"),
]

_ANCHOR_RE = re.compile(
    r"^(?P<title>[^\n•·]{3,140})\n+"
    r"(?P<company>[^\n•·]{2,80})\n+"
    r"(?P<loc>[^\n•]{2,80}) • via (?P<via>[^\n]{2,60})\n",
    re.MULTILINE)
_ANCHOR_COMPACT_RE = re.compile(
    r"^(?P<title>[^\n•·]{3,140})\n+"
    r"(?P<company>[^\n•·]{2,80})[ \xa0]*·[ \xa0]*"
    r"(?P<loc>[^\n•·]{2,80})[ \xa0]*·[ \xa0]*via (?P<via>[^\n]{2,60})\n",
    re.MULTILINE)


def _key():
    env = (REPO / "ingest/.env").read_text()
    return re.search(r"FIRECRAWL_API_KEY=(\S+)", env).group(1)


def fc_scrape(url: str, wait_ms: int = 4000):
    import time
    last_err = None
    for attempt in range(3):        # S33: firecrawl 500s are transient
        try:
            req = urllib.request.Request(
                "https://api.firecrawl.dev/v2/scrape",
                data=json.dumps({"url": url, "formats": ["markdown"],
                                 "onlyMainContent": False,
                                 "waitFor": wait_ms}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {_key()}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=120) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            if not d.get("success"):
                return "", json.dumps(d)[:300]
            return d["data"].get("markdown", ""), None
        except Exception as e:
            last_err = str(e)[:200]
            time.sleep(20 * (attempt + 1))
    return "", f"retries-exhausted: {last_err}"


def parse_cards(md: str) -> list[dict]:
    anchors = [(m.start(), m.end(), m.groupdict())
               for rx in (_ANCHOR_RE, _ANCHOR_COMPACT_RE)
               for m in rx.finditer(md)]
    anchors.sort(key=lambda t: t[0])
    cards, seen = [], set()
    for i, (s, e, g) in enumerate(anchors):
        k = (g["title"].strip(), g["loc"].strip(), g["via"].strip())
        if k in seen:
            continue
        seen.add(k)
        cards.append({"title": g["title"].strip(),
                      "company": g["company"].strip(),
                      "loc": g["loc"].strip(), "via": g["via"].strip()})
    return cards


def match_variants(company: str, cards: list[dict]):
    """Which card company-strings plausibly denote the REAL entity."""
    base = re.sub(r"\s*\(.*?\)\s*$", "", company).strip().lower()
    # tokens of the company name (>=4 chars, alpha) for loose matching
    toks = {t for t in re.split(r"[^a-z0-9]+", base) if len(t) >= 4}
    hits, near = [], []
    for c in cards:
        cc = c["company"].strip().lower()
        cb = re.sub(r"\s*\(.*?\)\s*$", "", cc).strip()
        if cb == base or base == cc:
            hits.append((c, "exact"))
        elif toks and toks <= {t for t in re.split(r"[^a-z0-9]+", cb)}:
            hits.append((c, "all-tokens"))
        elif toks and toks & {t for t in re.split(r"[^a-z0-9]+", cb)}:
            near.append(c)
    return hits, near


def main():
    only = sys.argv[1:]
    results = []
    for key, company in COMPANIES:
        if only and key not in only:
            continue
        q = f"{company} jobs USA"
        url = (f"https://www.google.com/search?"
               f"q={urllib.parse.quote_plus(q)}&ibp=htl;jobs&hl=en&gl=us")
        md, err = fc_scrape(url)
        if err:
            print(f"{key}: SCRAPE ERR {err}", flush=True)
            results.append({"key": key, "company": company,
                            "verdict": "error", "error": err})
            continue
        (OUT / f"gj_probe_{key}.md").write_text(md, encoding="utf-8")
        cards = parse_cards(md)
        hits, near = match_variants(company, cards)
        verdict = ("REAL" if hits else
                   ("NOISE-WALL" if cards else "EMPTY"))
        results.append({"key": key, "company": company, "verdict": verdict,
                        "n_cards": len(cards),
                        "matched": [h[0] for h in hits],
                        "near": near[:6]})
        print(f"{key}: {verdict} ({len(cards)} cards, "
              f"{len(hits)} matched, {len(near)} near)", flush=True)
    # summary table
    lines = ["# S35 gj gap probe — 33 li-dormant companies",
             "", "| key | company | verdict | cards | matched |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['key']} | {r['company']} | {r['verdict']} | "
                     f"{r.get('n_cards', 0)} | "
                     f"{len(r.get('matched', []))} |")
    real = [r for r in results if r["verdict"] == "REAL"]
    lines.append(f"\n**REAL: {len(real)}** — " +
                 ", ".join(r["key"] for r in real))
    OUT.joinpath("gj_gap_probe.md").write_text("\n".join(lines))
    OUT.joinpath("gj_gap_probe.json").write_text(json.dumps(results, indent=1))
    print(f"\nREAL: {[r['key'] for r in real]}")


if __name__ == "__main__":
    main()
