#!/usr/bin/env python3
"""S28 v2: split .agents/SKILL.md (143k → ≤30k).

Moves:
  1. §14–§30 per-session distillations → kb/distillations-{s11-s18,
     s19-s23, s24-s27}.md (verbatim)
  2. Deep domain-reference sections → kb/domain-{landscape,ats-apis,
     site-id-discovery,cloudflare,autofill,tools,dedup}.md (verbatim)
SKILL.md keeps: Part A meta-process + the decision/strategy sections
(§2, §4, §10, §12) + compact kb/ index + §13's doc map folded in.
"""
import pathlib
import re

SKILL = pathlib.Path(".agents/SKILL.md")
KB = pathlib.Path(".agents/kb")
KB.mkdir(exist_ok=True)

src = SKILL.read_text(encoding="utf-8")
lines = src.split("\n")

# ── parse top-level sections ─────────────────────────────────────────
marks = []
for i, l in enumerate(lines):
    if re.match(r"^## \d+\. |^# §\d+ |^# PART|^# SKILL", l):
        marks.append((i, l.strip()))

def section_num(title):
    m = re.match(r"^## (\d+)\. ", title)
    if m:
        return int(m.group(1))
    m = re.match(r"^# §(\d+) ", title)
    return int(m.group(1)) if m else None

# boundaries: section start line → next mark line
secs = {}          # num -> (start, end, title)
part_bounds = []
for j, (ln, title) in enumerate(marks):
    end = marks[j + 1][0] if j + 1 < len(marks) else len(lines)
    n = section_num(title)
    if n is not None:
        secs[n] = (ln, end, title)
    elif title.startswith("# PART"):
        part_bounds.append((ln, end, title))

# ── 1. distillation groups (§14–§30) ─────────────────────────────────
DIST_GROUPS = [
    ("distillations-s11-s18.md", [14, 15, 16, 17, 18]),
    ("distillations-s19-s23.md", [19, 20, 21, 22, 24, 25, 26]),
    ("distillations-s24-s27.md", [27, 28, 29, 30]),
]
DIST_HEADERS = {
    "distillations-s11-s18.md":
        "Per-session distillations S11–S18 (moved verbatim from "
        "SKILL.md §14–§18). Historic engineering lessons — read on "
        "demand.",
    "distillations-s19-s23.md":
        "Per-session distillations S19–S23 (moved verbatim from "
        "SKILL.md §19–§22 + §26/S23). Includes the sector-expansion "
        "SOP.",
    "distillations-s24-s27.md":
        "Per-session distillations S24–S27 (moved verbatim from "
        "SKILL.md §27–§30). Origin policy, the non_cn scope, removal "
        "mechanics, GHA daily-chain lessons.",
}

# ── 2. domain-reference moves ────────────────────────────────────────
DOMAIN_MOVES = [
    (1, "domain-source-landscape.md", "The 4-tier source landscape"),
    (3, "domain-ats-apis.md", "ATS public API pagination/scale quirks"),
    (5, "domain-workday-site-id.md",
     "Workday Job_Posting_Site_ID discovery"),
    (6, "domain-cloudflare-bypass.md",
     "Cloudflare bypass (Indeed/Glassdoor/ZipRecruiter)"),
    (7, "domain-workday-autofill.md", "Workday application autofill"),
    (8, "domain-personal-tools.md", "Personal tools landscape verdict"),
    (9, "domain-dedup-at-scale.md", "Deduplication strategy at scale"),
    (11, "domain-environment-notes.md",
     "Environment notes (S8-era; verify freshness)"),
]
DOMAIN_INTRO = ("Deep domain reference (moved verbatim from SKILL.md "
                "§%d — read on demand).")

moved_ranges = []      # (start, end) line ranges to cut
for fname, nums in DIST_GROUPS:
    body = []
    for n in nums:
        s, e, t = secs[n]
        body.append("\n".join(lines[s:e]).rstrip())
        moved_ranges.append((s, e))
    out = (f"# {DIST_HEADERS[fname]}\n\n"
           + "\n\n".join(body) + "\n")
    (KB / fname).write_text(out, encoding="utf-8")
    print(f"wrote kb/{fname}: {len(out)} chars")

for n, fname, desc in DOMAIN_MOVES:
    s, e, t = secs[n]
    body = "\n".join(lines[s:e]).rstrip()
    out = (f"# {DOMAIN_INTRO % n} — {desc}\n\n{body}\n")
    (KB / fname).write_text(out, encoding="utf-8")
    moved_ranges.append((s, e))
    print(f"wrote kb/{fname}: {len(out)} chars")

# ── 3. rebuild SKILL.md ──────────────────────────────────────────────
cut = set()
for s, e in moved_ranges:
    cut.update(range(s, e))
kept = [l for i, l in enumerate(lines) if i not in cut]

index = """
## Distillation + domain-reference archive (kb/ — the 30k split)

Everything below moved VERBATIM to `.agents/kb/` (nothing rewritten —
the kb files are the source of truth for the detail):

| kb file | was | covers |
|---|---|---|
| `kb/distillations-s11-s18.md` | §14–§18 | hidden API surfaces, classification authority, adapter seams, Unicode traps, ATS dialect boards, evidence ladders |
| `kb/distillations-s19-s23.md` | §19–§22, §24–§26 | root-path WAFs, detail-title drift, wave-2 adapter grind, Netlify fleet, sub-agent adjudication, **§26 sector-expansion SOP** |
| `kb/distillations-s24-s27.md` | §27–§30 | sanctions policy, **D-S27-1 origin criteria-as-code**, non_cn scope (D-S27-2), removal mechanics, homonym traps, GHA chain lessons |
| `kb/domain-source-landscape.md` | §1 | the 4-tier job-data landscape |
| `kb/domain-ats-apis.md` | §3 | per-ATS pagination + scale behavior |
| `kb/domain-workday-site-id.md` | §5 | Job_Posting_Site_ID discovery |
| `kb/domain-cloudflare-bypass.md` | §6 | CF bypass stack + allowlist quirks |
| `kb/domain-workday-autofill.md` | §7 | the Workday autofill problem |
| `kb/domain-personal-tools.md` | §8 | personal tools honest verdict |
| `kb/domain-dedup-at-scale.md` | §9 | dedup strategy at scale |
| `kb/domain-environment-notes.md` | §11 | environment notes (S8-era) |

**Always-current rules (the short list that travels):** git-is-disk
push-every-micro-step · never force-push · census-first wire-second ·
live-probe before wire (homonym traps) · fail-loudly guards · evidence
citations in verdict ledgers · one canonicalizer at the bundle boundary
· enrichment seams drop row fields unless pinned E2E ·
membership-aware back-syncs on removal · newposts=feed (fail-open
list) / state=membership / export=gate.
"""
# insert the index before "# PART B"
out_lines = []
for l in kept:
    if l.startswith("# PART B"):
        out_lines.append(index.strip("\n"))
    out_lines.append(l)
new_skill = "\n".join(out_lines)
# collapse >2 consecutive blank lines
new_skill = re.sub(r"\n{3,}", "\n\n", new_skill)
SKILL.write_text(new_skill, encoding="utf-8")
print(f"SKILL.md now {len(new_skill)} chars "
      f"({len(new_skill)/1000:.0f}k)")
assert len(new_skill) < 31000, "still over the guideline"
