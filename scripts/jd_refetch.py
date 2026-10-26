#!/usr/bin/env python3
"""jd_refetch — the JD re-fetch lane (S41 #1, DESIGN-S41 v2 §1).

Closes the pending queue's EVIDENCE gap: 917 pending rows (no_evidence
721 + bank_review 185 + payer_review 11) of which ~854 have thin or
absent JD text. This script fetches FULL job descriptions for those
rows and writes them to `ingest/data/roles/jd_cache.jsonl` — keyed
**(source, url)** (S41-R1 F2: 89% of targets carry row-hash ids whose
basis embeds the mutable `posted`; every addressable row has a url).

The sweep's grading consults the cache (role_sweep.py `_jd_cache`
hook): a cached desc strictly-longer + overlap-passing upgrades the
row's snippet BEFORE sector_verdict, and the next daily sweep's
seed-envelope union re-runs the pending rows through grading — the
queue closes by VERDICT, continuously, with zero bundle surgery.

Selection (S41-R1 F4/F5): per-source fullness bars + a transport
allowlist — the lane never burns budget on provably-unfetchable rows:

  adzuna/dice/jooble/talent   ALL pending (the API cap IS the gap)
  indeed                     pending ∧ descLen < 200
  himalayas                  ALL pending (URLs post-F8-fix)
  findwork/gj                pending ∧ descLen < 300
  careerjet                  NOT from Azure (API+site 403 — recorded
                             as hk-leg-needed, skipped)
  wellfound/li/board         selection-time exclusions (no desc
                             exists / JS-walled); board-employer rows
                             (JobLeads US class) excluded via the N9 flag

Quality gates (S41-R1 F3): store-time only —
  * HTML→text strip (bs4 when importable, regex fallback)
  * min length 200ch, cap 12,000ch
  * junk-sweep (cookie-consent/nav-chrome signatures)
  * fetch status recorded per row (403 is an answer, not a retry
    signal — one polite retry on network errors only)

Outputs:
  ingest/data/roles/jd_cache.jsonl    the cache (append-or-update,
                                      union-merge by (source,url),
                                      newest fetched_at wins)
  ingest/data/roles/jd_refetch_report.md   per-source×status counters
  ingest/data/roles/jd_refetch_state.json  leg state (targets left,
                                      pending-set snapshot for the
                                      attrition ledger, caps)

Leg semantics (the DRAIN pattern): `--budget-seconds N` (default 480 =
8min) OR `--max-rows N` (default 150) — whichever binds FIRST wins
(S41-R1 F8: one knob binds). Exit 0 with `JD_REFETCH=pending|complete`
emitted via $GITHUB_ENV for the lane's retrigger step. Self-healing:
targets re-read from the live bundle at every leg start.
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# [S47] the firecrawl fleet (rotation-aware scrape) is shared with the
# sweep engine — one loader, one key list, org lanes get it via the
# INGEST_ENV-materialized ingest/.env
from role_sweep import _fc_keys, _fc_scrape  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "ingest/data/ui/roles-pm.json"
CACHE = REPO / "ingest/data/roles/jd_cache.jsonl"
STATE = REPO / "ingest/data/roles/jd_refetch_state.json"
REPORT = REPO / "ingest/data/roles/jd_refetch_report.md"
_FAMILY = "pm"


def _set_family(family: str) -> None:
    """S42-R1 A1/A2 (DESIGN-S42 v2 §1.2): the refetch is family-
    parameterized. pm keeps its existing files verbatim (backward-
    compat with the org's live state); dmm gets twins. The CACHE stays
    SHARED — keyed (source,url), a dmm row and a pm row with the same
    url are the same JD, no family contamination.
    Usage: _set_family('dmm') must be called BEFORE any bundle/state
    access (main does it first thing; library callers that never call
    main keep the pm defaults)."""
    global BUNDLE, STATE, REPORT, _FAMILY
    _FAMILY = family
    if family == "dmm":
        BUNDLE = REPO / "ingest/data/ui/roles-dmm.json"
        STATE = (REPO / "ingest/data/roles/"
                 "jd_refetch_dmm_state.json")
        REPORT = (REPO / "ingest/data/roles/"
                  "jd_refetch_dmm_report.md")
    elif family == "pm":
        # rebind EXPLICITLY (idempotent + reversible): the dmm call
        # above mutated the globals — 'pm' must restore them, not
        # merely no-op (a library caller flipping families in one
        # process would silently keep the dmm paths).
        BUNDLE = REPO / "ingest/data/ui/roles-pm.json"
        STATE = REPO / "ingest/data/roles/jd_refetch_state.json"
        REPORT = REPO / "ingest/data/roles/jd_refetch_report.md"
    else:
        raise SystemExit(f"unknown family: {family!r} (pm, dmm)")

from jobsearch.rolefamilies import is_board_employer  # noqa: E402

# ── selection (S41-R1 F4/F5) ────────────────────────────────────────────────
# source → (max_fullness_ch, transport)  transport ∈ {direct, hk_only, skip}
# max_fullness: a pending row is selected when its descLen is BELOW the
# bar (the snippet is thinner than the source can actually provide).
# None = the list-API truncation class: every pending row BELOW the
# THICK_DESC_FLOOR targets (the at-cap rows — adzuna/dice 500ch snippets
# — DO benefit: the detail page carries the full 4,000ch JD).
#
# THICK_DESC_FLOOR (S42-1): a pending row whose desc is already ≥1,000ch
# "has the JD" — the text came from a cross-source merge, the cache
# consult, or a full-JD feed (himalayas RSS / indeed / gj 4,000ch rows).
# Re-fetching it cannot strictly improve the row (the consult's
# strictly-longer gate rejects the cached text anyway), so it is NEVER
# selected. Owner doctrine: "for existing roles that we already have JD
# fetched, refetching is not necessary — unless it is a new role we just
# discovered" (a new role with a full JD is also skipped: the row grades
# on the JD it has; a re-fetch of the same text adds no evidence).
THICK_DESC_FLOOR = 1000
SELECT_RULES: dict[str, tuple[int | None, str]] = {
    "adzuna": (None, "direct"),
    "dice": (None, "direct"),
    "jooble": (None, "direct"),
    "talent": (None, "direct"),
    "himalayas": (None, "direct"),
    "indeed": (200, "direct"),     # 4,000ch rows are already full
    "findwork": (300, "direct"),
    "gj": (300, "direct"),
    "careerjet": (None, "hk_only"),   # Azure 403 — the HK local leg fills it
    "glassdoor": (None, "fc_relay"),  # S47: jl= detail URLs, CF-walled
    # direct — the firecrawl relay (fc_relay transport) fetches them
    "wellfound": (None, "skip"),      # JS-walled
    "li": (None, "skip"),             # guest cards have no desc to fetch
    "board": (None, "skip"),          # Lever/Workday endpoints — v2 transport
    "ziprecruiter": (None, "skip"),   # S47: SERP rows carry co/ URLs —
    # not job details; there is nothing to refetch
}
PENDING_REASONS = ("no_sector_evidence", "bank_review", "payer_review")
MIN_FETCH_LEN = 200
MAX_FETCH_LEN = 12000

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": ("text/html,application/xhtml+xml,application/json;"
               "q=0.9,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# junk signatures — a capture with any of these is a chrome wall, not a JD
_JUNK_PATTERNS = (
    re.compile(r"enable javascript", re.I),
    re.compile(r"verify you are a human", re.I),
    re.compile(r"checking your browser", re.I),
    re.compile(r"access denied\s*[-–—]\s*(you|error)", re.I),
    re.compile(r"are you a robot", re.I),
    re.compile(r"request blocked", re.I),
)


def _html_to_text(payload: bytes) -> str:
    """HTML→text (S41-R1 F3: BUILD one — nothing existed). bs4 when
    importable (the jobspy extra brings it), regex fallback otherwise."""
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415
        soup = BeautifulSoup(payload, "html.parser")
        for tag in soup(["script", "style", "noscript", "nav",
                        "header", "footer", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(" ")
    except ImportError:
        text = payload.decode("utf-8", "replace")
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ",
                      text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_bundle() -> list[dict]:
    d = json.load(open(BUNDLE))
    return d.get("jobs") or []


def _select_targets(jobs: list[dict], transport_filter: str) -> list[dict]:
    """Pending rows ∧ per-source fullness bar ∧ transport allowlist.
    Board-employer rows are out (the JobLeads class — redirect shells).
    [S47] fc_relay is a SUPERSET transport: it fetches direct + hk_only
    rows (firecrawl = US egress + stealth; 1 credit/detail page) — the
    post-Oct-24 override the cache comments always promised."""
    # S47-R1 F2 fix: the fc_relay leg's _ok must ALSO include rows whose
    # own rule transport IS fc_relay (glassdoor jl= URLs) — the superset
    # set {direct, hk_only} silently made the glassdoor class unselectable.
    _ok = ({"direct", "hk_only", "fc_relay"}
           if transport_filter == "fc_relay" else {transport_filter})
    out = []
    for r in jobs:
        reason = r.get("sectorReason") or ""
        if not any(reason.startswith(p) for p in PENDING_REASONS):
            continue
        src = r.get("source") or ""
        rule = SELECT_RULES.get(src)
        if not rule:
            continue                    # unknown source — not our lane
        min_len, transport = rule
        if transport not in _ok:
            continue
        if transport_filter in ("direct", "fc_relay") and is_board_employer(
                r.get("company") or ""):
            continue
        # S42-1: the effective selection bar. Explicit per-source bars
        # stay; the truncation class (bar=None) selects below the thick
        # floor — at-cap rows target, already-full rows never do.
        bar = THICK_DESC_FLOOR if min_len is None else min_len
        if len(r.get("desc") or "") >= bar:
            continue   # already at/above the bar — the JD is fetched
        if not r.get("url"):
            continue
        out.append(r)
    return out


def _fetch_one(url: str) -> tuple[str, int]:
    """Polite single fetch. Returns (text_or_error, http_status).
    403/410 = answer (record, no retry); network errors get ONE retry."""
    # himalayas double-prefix unwrap (S41-R1 F8): bundle rows built
    # before the fetch-time fix carry
    # himalayas.app/jobs/https://himalayas.app/... — strip the prefix.
    m = re.match(r"^(https?://himalayas\.app/jobs/)(https?://.+)$", url)
    if m:
        url = m.group(2)
    for attempt in (1, 2):
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = resp.read(400_000)
                return _html_to_text(payload)[:MAX_FETCH_LEN], resp.status
        except urllib.error.HTTPError as e:
            return f"http-error:{e.code}", e.code
        except Exception as e:
            if attempt == 2:
                return f"error:{type(e).__name__}", 0
            time.sleep(1.2)
    return "error:unreachable", 0


def _fetch_one_fc(url: str) -> tuple[str, int]:
    """[S47] firecrawl-transport detail fetch — US egress + JS render
    (waitFor) + stealth: the override for rows the direct leg measured
    terminal-4xx (jooble 403s, careerjet CF) and thin-shell q:too_short
    captures. 1 credit/row, fleet-rotated. Returns (text, http_status)
    shaped exactly like _fetch_one so the same store-time gates run."""
    # himalayas double-prefix unwrap (shared with _fetch_one)
    m = re.match(r"^(https?://himalayas\.app/jobs/)(https?://.+)$", url)
    if m:
        url = m.group(2)
    keys = _fc_keys()
    if not keys:
        return "error:no-fc-key", 0
    d, st = _fc_scrape({"url": url, "formats": ["markdown"],
                        "onlyMainContent": True, "waitFor": 2500})
    if d is None:
        # fleet-level failure string: http### / fleet:* / no-key
        if st.startswith("http"):
            try:
                code = int(st.split("http")[1])
                return f"http-error:{code}", code
            except ValueError:
                pass
        return f"error:{st[:40]}", 0
    md = str((d.get("data") or {}).get("markdown") or "")
    text = re.sub(r"[ \t]+", " ", md).strip()[:MAX_FETCH_LEN]
    return text, 200


def _quality_reject(text: str) -> str:
    """Returns a rejection reason or '' (store-time gate)."""
    if not text or len(text) < MIN_FETCH_LEN:
        return "too_short"
    if text.startswith(("http-error:", "error:")):
        return text.split(":")[0]
    for pat in _JUNK_PATTERNS:
        if pat.search(text):
            return "junk_signature"
    if len(re.findall(r"[a-z]{3,}", text[:2000].lower())) < 30:
        return "low_text_density"
    return ""


def _load_cache_map() -> dict[str, dict]:
    m: dict[str, dict] = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = f"{d.get('source', '')}|{d.get('url', '')}"
            prev = m.get(k)
            if not prev or (d.get("fetched_at", "")
                            >= prev.get("fetched_at", "")):
                m[k] = d
    return m


def _write_cache(m: dict[str, dict]) -> None:
    """S42-R1 A3 (F15): ATOMIC full rewrite — a torn read during the
    mirror's union-merge silently dropped the newest entry. temp +
    os.replace is the same length-repair contract role_sweep uses."""
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for d in sorted(m.values(), key=lambda x: (x.get("source", ""),
                                                   x.get("url", ""))):
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    os.replace(tmp, CACHE)


def _snapshot_pending(jobs: list[dict]) -> set[str]:
    return {f"{r.get('source','')}|{r.get('url','')}" for r in jobs
            if (r.get("sectorReason") or "").startswith(PENDING_REASONS)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transport",
                    choices=["direct", "hk_only", "fc_relay"],
                    default="direct",
                    help="direct = the GHA lane (Azure); hk_only = the "
                         "careerjet class, run from the HK sandbox; "
                         "fc_relay = the S47 firecrawl transport "
                         "(superset: direct+hk_only rows, US egress, "
                         "1 credit/row)")
    ap.add_argument("--family", choices=["pm", "dmm"], default="pm",
                    help="which family bundle to refill (the cache is "
                         "shared, keyed (source,url))")
    ap.add_argument("--budget-seconds", type=int, default=480)
    ap.add_argument("--max-rows", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    _set_family(args.family)
    fam = args.family.upper()

    jobs = _load_bundle()
    targets = _select_targets(jobs, args.transport)
    cache = _load_cache_map()
    # skip rows already resolved — 'ok' (have the JD) AND terminal
    # failures (http-error:403/404/410/418 = an ANSWER, not a retry
    # signal; re-burning every leg on a 403 wall wastes the daily
    # budget — the lane-1 measurement: 130 jooble 403s re-fetched per
    # leg). Only 5xx/network errors (transient class) re-try.
    # [S47] TRANSPORT-AWARE override: a terminal verdict is terminal
    # ONLY for the transport that measured it — an fc_relay leg re-
    # attempts the direct legs' 403/q: verdicts (US egress + JS render
    # is a genuinely new chance), and direct legs never re-burn the
    # fc_relay verdicts. 'ok' from ANY transport stays resolved.
    def _resolved(key: str) -> bool:
        ent = cache.get(key, {})
        st = ent.get("status", "")
        if st == "ok":
            return True
        same_transport = (ent.get("transport", "direct")
                          == args.transport)
        if st.startswith("q:"):
            return same_transport or ent.get("transport", "") == ""
        if st.startswith("http-error:4"):
            return same_transport
        return False
    todo = [t for t in targets
            if not _resolved(f"{t['source']}|{t['url']}")]
    print(f"[jd_refetch] bundle={len(jobs)} pending-targets={len(targets)} "
          f"cached-ok={len(targets) - len(todo)} todo={len(todo)} "
          f"transport={args.transport}")

    stats: dict[str, int] = {}
    fetched = 0
    t0 = time.time()
    for r in todo:
        if (fetched >= args.max_rows
                or time.time() - t0 > args.budget_seconds):
            break
        url, src = r["url"], r["source"]
        if args.transport == "fc_relay":
            text, status = _fetch_one_fc(url)
        else:
            text, status = _fetch_one(url)
        rej = _quality_reject(text)
        key = f"{src}|{url}"
        if rej and not text.startswith(("http-error:", "error:")):
            # quality-rejected: keep the row in cache with status so we
            # do not refetch identical junk every leg; a future transport
            # (firecrawl post-Oct-24) overrides via newer fetched_at.
            cache[key] = {
                "source": src, "url": url, "id": r.get("id") or "",
                "status": f"q:{rej}", "desc": "", "desc_len": 0,
                "transport": args.transport, "fetched_at": _now(),
                "http": status,
            }
            stats[f"{src}:q:{rej}"] = stats.get(f"{src}:q:{rej}", 0) + 1
        elif text.startswith(("http-error:", "error:")):
            cache[key] = {
                "source": src, "url": url, "id": r.get("id") or "",
                "status": text, "desc": "", "desc_len": 0,
                "transport": args.transport, "fetched_at": _now(),
                "http": status,
            }
            stats[f"{src}:{text}"] = stats.get(f"{src}:{text}", 0) + 1
        else:
            cache[key] = {
                "source": src, "url": url, "id": r.get("id") or "",
                "status": "ok", "desc": text,
                "desc_len": len(text),
                "transport": args.transport, "fetched_at": _now(),
                "http": status,
            }
            stats[f"{src}:ok"] = stats.get(f"{src}:ok", 0) + 1
        fetched += 1
        time.sleep(1.5)              # politeness jitter (per-domain cap:
        # single-threaded serial fetch = the strictest cap there is)

    remaining = max(0, len(todo) - fetched)
    # S42 fix (found wiring the two-family lane): an EMPTY todo must
    # report complete — `fetched and remaining == 0` made a drained
    # backlog report pending, re-dispatching to the daily cap forever.
    done = remaining == 0
    if not args.dry_run:
        _write_cache(cache)
        # attrition ledger (S41-R1 F1): pending ids that vanished from
        # the bundle without a verdict — closes-by-disappearance must
        # be distinguishable from closes-by-evidence.
        prev_state = json.loads(STATE.read_text()) if STATE.exists() else {}
        prev_set = set(prev_state.get("pending_snapshot") or [])
        cur_set = _snapshot_pending(jobs)
        attrited = prev_set - cur_set if prev_set else []
        _write_state_and_report(cur_set, stats, fetched, remaining,
                                len(attrited) if prev_set else None,
                                len(targets), args.transport)
    result = "complete" if done else "pending"
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        # S42-R1 A1 (F1): PER-FAMILY env keys — a shared JD_REFETCH key
        # would make the retrigger predicate reflect only the LAST
        # step (dmm), stalling the pm backlog at 1 leg/day.
        with open(genv, "a") as f:
            f.write(f"JD_REFETCH_{fam}={result}\n"
                    f"JD_REFETCH_{fam}_REMAINING={remaining}\n")
    print(f"[jd_refetch family={args.family}] "
          f"fetched={fetched} remaining={remaining} → {result}")
    for k in sorted(stats):
        print(f"  {stats[k]:4}  {k}")
    return 0


def _write_state_and_report(cur_set: set[str], stats: dict[str, int],
                            fetched: int, remaining: int, attrited,
                            n_targets: int, transport: str) -> None:
    STATE.write_text(json.dumps({
        "last_leg": _now(), "transport": transport,
        "fetched": fetched, "remaining": remaining,
        "targets_seen": n_targets,
        "pending_snapshot": sorted(cur_set),
    }, ensure_ascii=False, indent=1))
    lines = [
        f"# JD-refetch ledger (family: {_FAMILY})",
        "",
        f"- last leg: **{_now()}** (transport: {transport})",
        f"- fetched this leg: {fetched} | remaining: {remaining} | "
        f"targets seen: {n_targets}",
    ]
    if attrited is not None:
        lines.append(f"- pending attrition since last leg: {attrited} "
                     "(vanished without a verdict — F1 ledger)")
    lines += ["", "## per-source status counters", ""]
    for k in sorted(stats):
        lines.append(f"- {k}: {stats[k]}")
    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
