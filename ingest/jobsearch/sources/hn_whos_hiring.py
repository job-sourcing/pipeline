"""Hacker News "Ask HN: Who is hiring?" monthly thread client (SRC-HN).

Hits the public HN Algolia API (JSON, no auth) — NOT HTML scraping. HN runs
this thread on the first weekday of each month around 15:00 UTC; the thread
typically accumulates 200-300 top-level comments (each is a job posting) and
tops out around ~1,000 postings across the month's edits.

The thread URL pattern is `https://news.ycombinator.com/item?id={thread_id}`.
The thread ID is the HN story's numeric `objectID`. To find it for the current
month we Algolia-search for stories matching the title pattern
`^Ask HN: Who is hiring? (Month Year)` and take the most recent by date. A
specific past month can be pinned via `Config.hn_thread_id`.

Two API calls per fetch (auto-discover mode):
  1. GET hn.algolia.com/api/v1/search_by_date?query=Who+is+hiring&tags=story
       → find the newest monthly thread's objectID.
  2. GET hn.algolia.com/api/v1/items/{thread_id}
       → the recursive comment tree; we only walk top-level children (the
         nested replies are off-topic / discussion, NOT job postings).

If `cfg.hn_thread_id` is pinned, only call #2 is made — 1 request total.

Failure isolation (D3): any HTTP error from Algolia raises RuntimeError so the
parent aggregator's SourceResult.error surfaces it. We NEVER crash the
pipeline: malformed top-level comments are individually skipped (parser
returns None), not raised.

Comment parsing (the tricky part — see SRC-HN parsing spec):
  • HTML-clean: unescape entities, `<p>`→`\n`, strip other tags, collapse WS.
  • Split first paragraph (the header) on `|`; fall back to `–`/`—`/`•`; if
    none, treat the whole paragraph as one segment.
  • Classify each segment into one bucket, in priority order:
      url (first https://…) → work_mode (REMOTE/REMOTELY/ONSITE/HYBRID) →
      salary (segment matches $/equity/benefits) → title (segment contains
      engineer/developer/manager/designer/...) → location (segment has commas
      or geographic words) → tags (segment has commas + tech tokens).
  • Company is always the first non-URL-only segment (per spec).
  • Description = all unclassified segments + everything after the first
    paragraph (the body). If no body and no leftover, keep the raw header so
    downstream scoring always has SOMETHING to match against.
  • If no title segment found, fall back to a regex scan of the body for the
    first "Engineer/Developer/..." phrase; last resort = "Multiple Roles".
"""
from __future__ import annotations

import html as html_mod
import re
import sys
from typing import Optional

from ..config import Config
from ..models import Job
from .base import fetch_json, detect_h1b, extract_email, normalize_date
from .arbeitnow import keyword_match

# ── Algolia endpoints ──────────────────────────────────────────────────────
# `search_by_date` returns hits sorted newest-first; we then filter on the
# title regex `^Ask HN: Who is hiring? (` to drop the unrelated "Who wants
# to be hired?" / "Who is dating?" / etc. threads that full-text-match the
# query. hitsPerPage=30 is plenty — the monthly thread is always within the
# last ~30 stories when sorted by date.
_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
_ITEMS_URL = "https://hn.algolia.com/api/v1/items"

_THREAD_TITLE_RE = re.compile(r"^Ask HN: Who is hiring\? \(")

# ── Parser regexes (module-level so tests can introspect) ─────────────────
_URL_RE = re.compile(r"https?://[^\s)<]+")
_WORK_MODE_RE = re.compile(
    r"\b(remote|remotely|onsite|on-site|hybrid)\b", re.I
)
_SALARY_RE = re.compile(
    r"(\$[\d,]+\s?k?|salary|compensation|\bk\s?\+|equity|benefits|/year|/yr)",
    re.I,
)
# Words that signal "this segment IS the job title". Order matters: longer
# keywords first so "Software Engineer" wins over "Engineer".
_TITLE_KEYWORDS = (
    "engineer", "developer", "manager", "designer", "scientist", "architect",
    "programmer", "analyst", "specialist", "lead", "director", "intern",
    "founder", "consultant", "researcher", "operator", "head of", "head",
    "evangelist", "advocate", "scout", "recruiter",
)
# Geographic keywords used to detect location segments + parentheticals.
# Case-sensitive on purpose — without re.I, the bare 'US' / 'UK' / 'EU'
# patterns would false-positive on lowercase 'us' (a common English word
# in body text like 'Join us at Acme'). Bare 'US' is intentionally NOT
# matched here; the parenthetical promotion logic (below) handles the
# lowercase 'us' / 'usa' / 'worldwide' / 'global' cases via startswith.
_GEO_RE = re.compile(
    r"\b(United States|United Kingdom|USA|UK|EU|Europe|Germany|India|"
    r"Canada|France|Japan|Singapore|Australia|Netherlands|Belgium|Spain|"
    r"Italy|Brazil|Mexico|Ireland|Sweden|Norway|Denmark|Finland|Poland|"
    r"Portugal|Switzerland|Austria|Worldwide|Global|London|Berlin|"
    r"Paris|Amsterdam|Toronto|Sydney|Tokyo)\b",
)
# Lowercase prefix tokens that mark a parenthetical (or residual text) as
# location info, even when the rest of _GEO_RE doesn't fire. 'us' / 'usa' /
# 'uk' / 'eu' / 'worldwide' / 'global' are all valid locations when they
# start the parenthetical, e.g. 'Remote (US only)', 'REMOTE (worldwide)'.
_LOC_PREFIXES = ("us", "usa", "uk", "eu", "worldwide", "global")


def fetch(keywords: str, location: str = "", num_results: int = 20,
          date_filter: Optional[int] = None, job_type: Optional[str] = None,
          cfg: Config | None = None) -> list[Job]:
    """Fetch + parse the most recent HN "Who is hiring?" thread.

    Returns up to `num_results` Job objects whose title+description+tags
    match `keywords`. One Job per top-level comment (nested replies are
    skipped — they're discussion, not postings).
    """
    cfg = cfg or Config()
    # Step 1 — resolve the thread ID (1 Algolia call when auto-discovering,
    # 0 calls when the override is set).
    thread_id = (cfg.hn_thread_id or "").strip()
    thread_created_at: Optional[str] = None
    if not thread_id:
        try:
            thread_id, thread_created_at = _discover_thread_id(cfg)
        except Exception as exc:  # noqa: BLE001 — D3 isolation
            raise RuntimeError(
                f"HN Who's Hiring: thread discovery failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # Step 2 — fetch the thread's comment tree (1 Algolia call).
    try:
        tree = fetch_json(f"{_ITEMS_URL}/{thread_id}", cfg=cfg)
    except Exception as exc:  # noqa: BLE001 — D3 isolation
        raise RuntimeError(
            f"HN Who's Hiring: thread fetch failed for id={thread_id}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # The story object itself carries created_at — use it as the post date for
    # every job (HN comments don't reliably have per-comment timestamps in the
    # Algolia payload). If discovery was skipped (override set), pull it from
    # the tree now.
    if not thread_created_at:
        thread_created_at = tree.get("created_at")

    date_posted = _month_from_created_at(thread_created_at)

    # Step 3 — walk top-level children only, parse, filter, cap.
    jobs: list[Job] = []
    for child in tree.get("children", []):
        if not isinstance(child, dict):
            continue
        comment_id = child.get("id")
        if comment_id is None:
            continue
        try:
            parsed = parse_comment(child.get("text") or "")
        except Exception as exc:  # noqa: BLE001 — skip malformed, don't crash
            print(f"[hn_whos_hiring] parse failure id={comment_id}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if not parsed or not parsed.get("company"):
            # Empty comment or parser couldn't extract a company — skip.
            continue

        # Keyword filter against title + tags + description + company (so a
        # search for "python" matches both "Senior Python Engineer" titles
        # and "Python, Go, Kubernetes" tag segments).
        match_text = " ".join([
            parsed.get("title", ""),
            parsed.get("company", ""),
            parsed.get("tags", ""),
            parsed.get("description", ""),
        ])
        if keywords and not keyword_match(match_text, keywords):
            continue

        # Location filter (case-insensitive substring against the parsed
        # location OR the full description — some postings bury location in
        # the body when no location segment was detectable).
        loc_filter = (location or "").strip().lower()
        if loc_filter and loc_filter not in ("remote",):
            if loc_filter not in parsed.get("location", "").lower() and \
                    loc_filter not in parsed.get("description", "").lower():
                continue
        # If user asked for "Remote" specifically, only keep remote-flagged
        # jobs (mirror the `remote_only` Source flag — see __init__.py).
        if loc_filter == "remote" and not parsed.get("remote"):
            continue

        link = f"https://news.ycombinator.com/item?id={comment_id}"
        desc_full = parsed.get("description", "") or ""
        jobs.append(Job(
            title=parsed.get("title", "") or "Multiple Roles",
            company=parsed.get("company", ""),
            description=desc_full,
            link=link,
            contact_email=extract_email(desc_full),
            source="HN Who's Hiring",
            location=parsed.get("location", ""),
            date_posted=date_posted,
            remote=bool(parsed.get("remote")),
            h1b_mention=detect_h1b(
                f"{parsed.get('title', '')} {parsed.get('tags', '')} "
                f"{desc_full}"
            ),
            salary_text=parsed.get("salary"),
        ))
        if len(jobs) >= num_results:
            break

    return jobs


# ── Discovery ────────────────────────────────────────────────────────────

def _discover_thread_id(cfg: Config) -> tuple[str, Optional[str]]:
    """Find the most recent monthly "Ask HN: Who is hiring?" thread's objectID.

    Uses Algolia `search_by_date` (newest-first sort) with `tags=story` and
    filters hits whose title starts with `Ask HN: Who is hiring? (`. Returns
    (objectID, created_at_iso) of the first match.
    """
    data = fetch_json(
        _SEARCH_URL,
        params={
            "query": "Who is hiring",
            "tags": "story",
            "hitsPerPage": 30,
        },
        cfg=cfg,
    )
    hits = data.get("hits", []) if isinstance(data, dict) else []
    for hit in hits:
        title = (hit.get("title") or "").strip()
        if _THREAD_TITLE_RE.match(title):
            return str(hit.get("objectID")), hit.get("created_at")
    raise RuntimeError(
        "HN Who's Hiring: no monthly thread found in the last 30 hits "
        "(Algolia search returned 0 matching titles)"
    )


# ── Parser ────────────────────────────────────────────────────────────────

def parse_comment(raw: str) -> Optional[dict]:
    """Parse one HN "Who is hiring?" top-level comment into structured fields.

    Returns None if the comment is empty / wholly unparseable. Returns a dict
    with keys: company, title, location, work_mode, remote, salary, tags,
    url, description. All fields are strings ("" when unset) except `remote`
    which is a bool.
    """
    if not raw or not raw.strip():
        return None

    # ── HTML clean: unescape entities, <p>→\n, strip other tags ─────────
    text = html_mod.unescape(raw)
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return None

    header = paragraphs[0]
    body = " ".join(paragraphs[1:])

    # ── Split header into segments ────────────────────────────────────
    # `|` is the dominant delimiter (223/242 in the Aug 2026 thread); fall
    # back to en/em-dash, bullet, or "no delimiter" (treat as one segment).
    if "|" in header:
        segments = [s.strip() for s in header.split("|") if s.strip()]
    elif any(d in header for d in ("–", "—", "•")):
        for d in ("–", "—", "•"):
            if d in header:
                segments = [s.strip() for s in header.split(d) if s.strip()]
                break
    else:
        segments = [header]

    # ── Classify each segment ─────────────────────────────────────────
    company = ""
    title = ""
    location = ""
    work_mode = ""
    salary = ""
    url = ""
    leftover: list[str] = []

    for i, seg in enumerate(segments):
        if not seg:
            continue
        # Pull URLs out of the segment — the residual text is what we
        # classify; the URL becomes the link hint (but the canonical link
        # is still the comment URL, set by the caller).
        url_m = _URL_RE.search(seg)
        if url_m and not url:
            url = url_m.group(0).rstrip(".,);")
        seg_clean = _URL_RE.sub("", seg).strip(" -(),.;:")
        if not seg_clean:
            continue

        # Company = first non-URL-only segment (per spec). Embedded URL is
        # already stripped from seg_clean above.
        if not company:
            company = seg_clean
            continue

        # Work mode (segment primarily about remote/onsite/hybrid). To
        # avoid swallowing a multi-content segment like "Senior Engineer
        # (Remote)" — which is really a title — only classify as work_mode
        # when the segment is short OR begins with the work-mode word.
        if not work_mode:
            wm = _WORK_MODE_RE.search(seg_clean)
            if wm and (
                re.match(r"^\s*(remote|remotely|onsite|on-site|hybrid)",
                         seg_clean, re.I)
                or len(seg_clean) <= 40
            ):
                work_mode = _normalize_work_mode(wm.group(1))
                # Extract location info from this work-mode segment. Two
                # sources (in priority order):
                #   1. A parenthetical qualifier (e.g. "Remote (US only)") —
                #      promoted to location if it looks like a real place.
                #   2. The residual text after stripping the work-mode word
                #      (e.g. "Remote US or Ontario, Canada" → "US or Ontario,
                #      Canada") — promoted if it looks like a place.
                # In both cases, parentheticals / residuals that themselves
                # contain a work-mode word (e.g. "(all remote)", "(3 days/week
                # onsite)") are rejected — they're work-mode qualifiers, not
                # places.
                if not location:
                    paren = re.search(r"\(([^)]*)\)", seg)
                    if paren and _looks_like_location(paren.group(1)):
                        location = paren.group(1).strip()
                if not location:
                    residual = _WORK_MODE_RE.sub("", seg_clean, count=1)
                    residual = residual.strip(" -(),.;:")
                    if residual and _looks_like_location(residual):
                        location = residual
                continue

        # Salary (segment mentions $/k/equity/benefits/comp).
        if not salary and _SALARY_RE.search(seg_clean):
            salary = seg_clean
            continue

        # Title (segment contains a role keyword like Engineer/Developer).
        if not title and _has_title_keyword(seg_clean):
            title = seg_clean
            continue

        # Location (segment has a comma OR matches geographic keywords).
        if not location and _looks_like_location(seg_clean):
            location = seg_clean
            continue

        # Anything else → leftover (becomes part of description).
        leftover.append(seg_clean)

    # ── Post-processing ───────────────────────────────────────────────
    # Company too long → no-pipe narrative case. Extract a short company
    # name from the first 1-2 capitalized words and push the residual into
    # the description. Without this, a no-pipe comment like "Sumble is the
    # newco from the founders of Kaggle. We are hiring..." would land with
    # company = the entire first sentence, which is useless for dedup /
    # display. After extraction: company = "Sumble", description carries the
    # full first sentence as context for keyword matching.
    if len(company) > 30:
        m = re.match(r"([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)", company)
        if m and len(m.group(1)) < len(company):
            rest = company[len(m.group(1)):].strip(" -,.;:")
            if rest:
                leftover.insert(0, rest)
            company = m.group(1).strip()

    # ── Fallbacks ──────────────────────────────────────────────────────
    # Title: scan body for first "Engineer/Developer/..." phrase; last
    # resort = "Multiple Roles" (the spec's HN posting convention).
    if not title:
        title = _scan_body_for_title(body) or "Multiple Roles"

    # Work mode: if not detected in segments, scan header+body once more.
    if not work_mode:
        wm = _WORK_MODE_RE.search(header + " " + body)
        if wm:
            work_mode = _normalize_work_mode(wm.group(1))

    # Location: if not detected, look for geo keywords in body (case-
    # sensitive via _GEO_RE, so lowercase 'us' in 'Join us at Acme' won't
    # false-positive). Only the first 300 chars of the body are scanned —
    # HN postings put location info near the top.
    if not location:
        m = _GEO_RE.search(body[:300])
        if m:
            location = m.group(0)

    remote = work_mode == "REMOTE"

    # Description = leftover segments + body. Always non-empty so the
    # downstream keyword filter + scoring have something to scan.
    desc_parts = list(leftover)
    if body:
        desc_parts.append(body)
    description = " | ".join(desc_parts).strip() if desc_parts else header

    return {
        "company": company,
        "title": title,
        "location": location,
        "work_mode": work_mode,
        "remote": remote,
        "salary": salary,
        "tags": "",  # we don't separately carve out a tags segment —
                     # leftover + tags roll up into description
        "url": url,
        "description": description,
    }


def _normalize_work_mode(token: str) -> str:
    """Map REMOTELY→REMOTE, ON-SITE→ONSITE; uppercase the rest."""
    t = token.strip().upper().replace("-", "")
    if t == "REMOTELY":
        return "REMOTE"
    return t  # REMOTE / ONSITE / HYBRID


def _looks_like_location(text: str) -> bool:
    """Heuristic: does this text look like a city/state/country?

    True if the text has a comma ("City, State" / "City, Country" pattern),
    contains a recognized geo keyword (case-sensitive via _GEO_RE), OR starts
    with a lowercase location-prefix token ("us", "usa", "worldwide", …).
    Rejects texts that themselves contain a work-mode word — those are
    work-mode qualifiers, not places (e.g. "all remote", "3 days/week
    onsite").
    """
    if not text or not text.strip():
        return False
    text = text.strip()
    text_lower = text.lower()
    # Reject parentheticals / residuals that are themselves about work mode.
    if re.search(r'\b(?:remote|remotely|onsite|on-site|hybrid)\b', text_lower):
        return False
    if ", " in text:
        return True   # "Antwerp, Belgium", "San Francisco, CA", …
    if _GEO_RE.search(text):
        return True   # "Germany", "USA", "London", …
    # Lowercase prefix tokens: "us", "usa", "uk", "eu", "worldwide", "global".
    # Use word-boundary regex so "us" matches "us" and "us only" but NOT
    # "usually" or "used".
    return bool(re.match(rf"^(?:{'|'.join(_LOC_PREFIXES)})\b", text_lower))


def _has_title_keyword(seg: str) -> bool:
    """True if the segment contains any role keyword (Engineer, Developer, ...).

    Case-insensitive whole-word match — "Manager" matches "Engineering
    Manager" but also "Manager of Engineering". Also accepts plural /
    gerund suffixes ("engineers", "engineering", "leads") so segments like
    "Hiring Engineering Leaders" correctly classify as title.
    """
    low = seg.lower()
    return any(re.search(rf"\b{re.escape(kw)}(?:s|ing)?\b", low)
               for kw in _TITLE_KEYWORDS)


def _scan_body_for_title(body: str) -> str:
    """Best-effort extract a title phrase from the body when no title segment
    was found in the header. Two-step scan:
      1. Find the first title keyword (case-insensitive, with optional
         plural/gerund suffix). Word boundaries so "engineering" → match
         (engineer + ing) but "reengineering" → no match.
      2. Expand backwards to capture 0-4 capitalized prefix words (e.g.
         "Senior Backend " in "Senior Backend Engineer") and forwards for
         0-2 capitalized suffix words. Prefix/suffix expansion is
         case-sensitive on purpose — we want "Senior Engineer" not
         "the engineer".
    Returns "" if no title keyword is found anywhere in the body.
    """
    if not body:
        return ""
    kws = sorted(_TITLE_KEYWORDS, key=len, reverse=True)
    pat = r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")(?:s|ing)?\b"
    m = re.search(pat, body, re.I)
    if not m:
        return ""
    start, end = m.start(), m.end()
    # Greedily grab up to 4 capitalized words immediately before the match.
    prefix_m = re.search(r"((?:[A-Z][A-Za-z]+\s+){0,4})\Z", body[:start])
    prefix = prefix_m.group(1) if prefix_m else ""
    # Greedily grab up to 2 capitalized words immediately after the match.
    suffix_m = re.match(r"((?:\s+[A-Z][A-Za-z]+){0,2})", body[end:])
    suffix = suffix_m.group(1) if suffix_m else ""
    return (prefix + body[start:end] + suffix).strip()[:120]


def _month_from_created_at(created_at: Optional[str]) -> Optional[str]:
    """Normalize the thread's `created_at` (Algolia ISO 8601) → YYYY-MM-01.

    HN postings don't carry reliable per-comment timestamps, so the thread's
    creation month is the best "date_posted" signal — every parsed job gets
    the same YYYY-MM-01 (the thread goes live on the 1st weekday of the
    month). normalize_date handles the ISO parsing; we then pin to the 1st.
    """
    if not created_at:
        return None
    d = normalize_date(str(created_at))
    if not d:
        return None
    return f"{d[:7]}-01"
