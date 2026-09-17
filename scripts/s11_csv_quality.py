#!/usr/bin/env python3
"""S11 CSV quality sweep — characterize reqYear + sweep all 44 columns for
oddities the user may have spotted at a glance.

Read-only diagnostics; prints findings. Run from repo root or ingest/.
"""
import csv
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "ingest/data/workday/nvidia_us_fulltime.csv"

rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
print(f"rows={len(rows)} cols={len(rows[0])}")

# ── 1. reqYear vs startDate ────────────────────────────────────────────
pairs = []
for r in rows:
    try:
        sy = int(r["reqYear"]); actual = int(r["startDate"][:4])
        pairs.append((sy, actual))
    except (ValueError, KeyError):
        pass
same = sum(1 for sy, a in pairs if sy == a)
print(f"\n[reqYear] prefix==startDate.year: {same}/{len(pairs)} ({100*same/len(pairs):.1f}%)")
# monotonic signal check: correlation between prefix and start year
prefixes = [p for p, _ in pairs]; actuals = [a for _, a in pairs]
if len(prefixes) > 2:
    try:
        corr = statistics.correlation(prefixes, actuals)
        print(f"[reqYear] pearson(prefix, startYear) = {corr:.3f}")
    except Exception as e:
        print("corr failed:", e)
# spread of prefixes among rows posted in 2026
recent = [p for p, a in pairs if a == 2026]
print(f"[reqYear] rows started 2026: {len(recent)}; their prefix spread: "
      f"{min(recent)}..{max(recent)} (stdev {statistics.pstdev(recent):.1f})")

# ── 2. postedOn vs postingAgeDays consistency ─────────────────────────
def postedon_days(text, snapshot):
    m = re.match(r"Posted (\d+)[+]? Days? Ago", text or "")
    if not m:
        if text == "Posted Yesterday":
            return 1
        if text == "Posted Today":
            return 0
        return None
    return int(m.group(1))

mismatch = missing = 0
for r in rows:
    want = postedon_days(r["postedOn"], None)
    got = r["postingAgeDays"]
    if want is None:
        missing += 1
        continue
    try:
        g = int(got)
    except ValueError:
        continue
    # "30+ Days Ago" bucket: age >= 30 acceptable
    if "30+" in r["postedOn"]:
        if g < 30:
            mismatch += 1
    elif abs(g - want) > 1:
        mismatch += 1
print(f"\n[postedOn] unparsable={missing} age-mismatch(>1d)={mismatch}")

# ── 3. negative / odd derived numbers ──────────────────────────────────
for col in ("postingAgeDays", "daysOnMarket", "daysLeftToApply", "repostCount",
            "numApplicants", "similarJobsCount", "nLocations", "descriptionLength"):
    vals = []
    weird = Counter()
    for r in rows:
        v = r[col]
        if v == "":
            weird["empty"] += 1
            continue
        try:
            iv = int(v)
        except ValueError:
            weird[f"nonint"] += 1
            continue
        vals.append(iv)
        if iv < 0:
            weird["negative"] += 1
    line = f"[{col}] n={len(vals)}"
    if vals:
        line += f" min={min(vals)} max={max(vals)}"
    if weird:
        line += f" odd={dict(weird)}"
    print(line)

# ── 4. date sanity ─────────────────────────────────────────────────────
today = date(2026, 9, 17)
checks = {
    "startDate>today": 0, "endDate<past": 0, "firstSeen<startDate": 0,
    "applicationDeadline<past": 0, "corroboratedOn>today": 0,
    "earliestEvidence<2020": 0, "dumpDate!=const": 0,
}
dump_dates = Counter()
for r in rows:
    sd = r["startDate"]; ed = r["endDate"]; fs = r["firstSeenDate"]
    dl = r["applicationDeadline"]; co = r["corroboratedOn"]; ee = r["earliestEvidenceDate"]
    dump_dates[r["dumpDate"]] += 1
    if sd and sd > "2026-09-17":
        checks["startDate>today"] += 1
    if ed and ed < "2026-08-01":
        checks["endDate<past"] += 1
    if fs and sd and fs < sd:
        checks["firstSeen<startDate"] += 1
    if dl and dl < "2026-08-01":
        checks["applicationDeadline<past"] += 1
    if co and co > "2026-09-17":
        checks["corroboratedOn>today"] += 1
    if ee and ee < "2020-01-01":
        checks["earliestEvidence<2020"] += 1
print(f"\n[date sanity] {checks}")
print(f"[dumpDate] {dict(dump_dates)}")

# ── 5. enum/categorical distributions ──────────────────────────────────
for col in ("timeType", "remoteFlag", "country", "corroborationStatus",
            "matchMethod", "censored", "applicantCensored", "workerSubType",
            "daysOnMarketBasis", "applicantLabel"):
    c = Counter(r[col] for r in rows)
    top = ", ".join(f"{k!r}={v}" for k, v in c.most_common(8))
    print(f"[{col}] {len(c)} distinct: {top}")

# ── 6. structural checks ───────────────────────────────────────────────
q_filled = sum(1 for r in rows if r["questionnaire"].strip())
print(f"\n[questionnaire] non-empty: {q_filled}/{len(rows)}")
qvals = Counter(r["questionnaire"] for r in rows if r["questionnaire"].strip())
for k, v in qvals.most_common(6):
    print(f"  {k[:80]!r} x{v}")

statecodes_bad = sum(1 for r in rows if r["stateCodes"] and not re.fullmatch(
    r"[A-Z]{2}(,[A-Z]{2})*", r["stateCodes"]))
print(f"[stateCodes] non-2-letter-format: {statecodes_bad}")
urls_bad = sum(1 for r in rows if r["url"] and "nvidia.com" not in r["url"])
print(f"[url] non-nvidia: {urls_bad}")
li_bad = sum(1 for r in rows if r["linkedinUrl"] and "linkedin.com/jobs/view" not in r["linkedinUrl"])
print(f"[linkedinUrl] odd: {li_bad}")
desc_empty = sum(1 for r in rows if not r["description"].strip())
print(f"[description] empty: {desc_empty}")
hlen = sum(1 for r in rows if r["descriptionLength"] and int(r["descriptionLength"]) != len(r["description"]))
print(f"[descriptionLength] != len(description): {hlen}")

# hiringOrg vs company
ho = Counter(r["hiringOrg"] for r in rows)
print(f"[hiringOrg] {dict(list(ho.items())[:5])}")

# firstSeenDate coverage
fs_missing = sum(1 for r in rows if not r["firstSeenDate"].strip())
print(f"[firstSeenDate] missing: {fs_missing}")
