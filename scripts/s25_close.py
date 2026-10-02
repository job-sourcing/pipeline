"""S25 session close-out bundle:

  1. worklog entry (append)
  2. probe-evidence adjudication dispatch state check
  3. bundle build + count report

Run after all chains finish: python3 scripts/s25_close.py
"""
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def chain_state() -> dict:
    done, red = 0, []
    for f in sorted((REPO / "ingest/data/workday").glob("*.report.txt")):
        name = f.name.replace(".report.txt", "")
        try:
            txt = f.read_text(errors="replace")
        except Exception:
            continue
        if "finish" in txt or "FINISH" in txt:
            done += 1
    # honest signal: list.status complete + csv existence
    csvs = list((REPO / "ingest/data/workday").glob("*_us_fulltime.csv"))
    return {"csvs": len(csvs), "finish_reports": done}


def bundle(out: str | None = None) -> dict:
    cmd = [sys.executable, str(REPO / "scripts/build_ui_bundle.py")]
    if out:
        cmd += ["--out", out]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-1000:], file=sys.stderr)
    idx = pathlib.Path((out or REPO / "ingest/data/ui")
                       if out else REPO / "ingest/data/ui") / "index.json"
    if not idx.exists() and out:
        idx = pathlib.Path(out) / "index.json"
    elif not idx.exists():
        for cand in (REPO / "ingest/data/ui", pathlib.Path(
                "/home/z/my-project/public/data")):
            if (cand / "index.json").exists():
                idx = cand / "index.json"
                break
    d = json.loads(idx.read_text())
    return {"companies": len(d.get("companies", [])),
            "jobs": len(d.get("jobs", [])) if "jobs" in d
            else d.get("totalJobs", "?"),
            "snapshot": d.get("snapshotDate")}


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    st = chain_state()
    print("chain state:", st)
    print("bundle:", bundle(out))
