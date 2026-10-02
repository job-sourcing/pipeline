#!/usr/bin/env python3
"""S25 — scripted board-hunt for the biopharma census records the
timed-out agent left at ats=None (2-c timed out twice; the board-hunt
is bounded curl work — done directly).

Probes ashby/greenhouse/lever/workable/smartrecruiters with slug
variants derived from the company name; records live boards only
(HTTP 200 + parseable jobs payload). Writes findings to
s25_census/biopharma_boardhunt.json for the orchestrator to patch
into seg_biopharma.jsonl surgically.
"""
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent
SEG = HERE / "ingest/data/ats_seed/s25_census"
OUT = SEG / "biopharma_boardhunt.json"

TARGETS = [  # (company, slug variants in preference order)
    ("Mindray", ["mindray", "mindrayus", "mindray-north-america"]),
    ("MicroPort Scientific", ["microport", "microportscientific"]),
    ("Pharmaron", ["pharmaron", "pharmaron-us"]),
    ("Tigermed", ["tigermed", "tigermed-us", "tigermedicine"]),
    ("JOINN Laboratories", ["joinn", "joinnlab", "joinnlaboratories"]),
    ("Frontage Laboratories", ["frontage", "frontagelabs"]),
    ("Medicilon", ["medicilon"]),
    ("Adagene", ["adagene"]),
    ("Structure Therapeutics", ["structuretherapeutics", "structure"]),
    ("Harbour BioMed", ["harbourbiomed", "harbour"]),
    ("HUTCHMED", ["hutchmed"]),
    ("Junshi Biosciences", ["junshi", "junshibiosciences"]),
    ("Fosun Pharma", ["fosunpharma", "fosun"]),
    ("Innovent Biologics", ["innovent", "innoventbiologics"]),
    ("Asymchem", ["asymchem"]),
    ("Porton Pharma Solutions", ["porton", "portonpharma"]),
    ("WuXi XDC", ["wuxixdc"]),
    ("LaNova Medicines", ["lanova"]),
    ("Regor Therapeutics", ["regor", "regortherepeutics"]),
    ("OncoC4", ["oncoc4"]),
    ("Brii Biosciences", ["briibio", "brii"]),
    ("Phanes Therapeutics", ["phanes", "phanestherapeutics"]),
    ("Adcentrx Therapeutics", ["adcentrx"]),
]


def _curl(url: str, timeout: int = 12) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/tmp/bh.json", "-w", "%{http_code}",
             "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5)
        code = int(r.stdout.strip() or 0)
        body = pathlib.Path("/tmp/bh.json").read_text(errors="replace") \
            if pathlib.Path("/tmp/bh.json").exists() else ""
        return code, body
    except Exception:
        return 0, ""


def _count_ashby(body: str):
    try:
        d = json.loads(body)
        jobs = d.get("jobs", [])
        if not isinstance(jobs, list):
            return None
        return len(jobs), [j.get("location", "?") for j in jobs[:6]]
    except Exception:
        return None


def probe(name: str, slug: str) -> dict | None:
    # ashby
    code, body = _curl(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if code == 200:
        got = _count_ashby(body)
        if got is not None:
            return {"ats": f"ashby:{slug}", "jobs": got[0],
                    "locations": got[1], "probe": "ashby"}
    # greenhouse
    code, body = _curl(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if code == 200:
        try:
            n = len(json.loads(body).get("jobs", []))
            return {"ats": f"greenhouse:{slug}", "jobs": n,
                    "probe": "greenhouse"}
        except Exception:
            pass
    # lever
    code, body = _curl(
        f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if code == 200:
        try:
            d = json.loads(body)
            if isinstance(d, list):
                return {"ats": f"lever:{slug}", "jobs": len(d),
                        "probe": "lever"}
        except Exception:
            pass
    # workable
    code, body = _curl(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
        f"?details=true")
    if code == 200:
        try:
            d = json.loads(body)
            if d.get("name"):
                return {"ats": f"workable:{slug}",
                        "jobs": len(d.get("jobs", [])),
                        "probe": "workable"}
        except Exception:
            pass
    # smartrecruiters
    code, body = _curl(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/jobs"
        f"?limit=5")
    if code == 200:
        try:
            d = json.loads(body)
            total = d.get("total")
            if isinstance(total, int) and total > 0:
                return {"ats": f"smartrecruiters:{slug}", "jobs": total,
                        "probe": "smartrecruiters"}
        except Exception:
            pass
    return None


def main() -> int:
    found = {}
    for name, slugs in TARGETS:
        for slug in slugs:
            hit = probe(name, slug)
            if hit:
                found[name] = hit
                print(f"  HIT {name}: {hit['ats']} ({hit['jobs']} jobs)")
                break
        else:
            print(f"  miss {name}")
        time.sleep(0.4)
    OUT.write_text(json.dumps(found, indent=2, ensure_ascii=False) + "\n")
    print(f"\nwrote {OUT} — {len(found)} live boards found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
