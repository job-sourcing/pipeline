"""Company→ATS resolution — the Step-E finishing move.

Two capabilities:

1. detect_platform(link) — identify which ATS a job link points at and
   extract the tenant slug. Works TODAY on any stored job (no directory
   needed): boards.greenhouse.io/{slug}, jobs.lever.co/{slug},
   jobs.ashbyhq.com/{slug}, careers.smartrecruiters.com/{slug}, the
   subdomain-hosted boards (workable/personio/teamtailor/...), and Workday
   ({tenant}.wd{N}.myworkdayjobs.com).

2. AtsDirectory — resolve a COMPANY NAME to its live board(s) using the
   slug→ATS directory drained by scripts/build_ats_directory.py (the DB is
   gitignored data; resolve() returns [] when it is absent — flag, never
   drop). Lookup is by slug candidates (normalized company name) and by the
   company names the directory learned while probing.

Board-URL templates mirror the endpoints the adapters actually use, so a
resolved board can be fed straight into the ATS-direct adapters via their
*_ORGS knobs.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re

from .dedup import normalize_match_text

__all__ = ["detect_platform", "AtsDirectory", "Board", "board_url",
           "ATS_DIRECTORY_PATH", "ATS_SNAPSHOT_PATH"]

ATS_DIRECTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "ats_directory.db"
# Committed snapshot fallback (scripts/export_ats_directory.py) — lets a
# FRESH sandbox resolve companies before the DB is re-seeded + drained.
ATS_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "ats_directory_snapshot.json"


# ── Link → (platform, slug) ──────────────────────────────────────────────────

# Ordered: specific query/param shapes FIRST (the slug lives in a query
# param or a later path segment there), then the first-path-segment boards,
# then the subdomain-hosted ones. Patterns are deliberately narrow — a
# false platform tag poisons downstream ATS-sourcing decisions.
_PLATFORM_PATTERNS: list[tuple[str, re.Pattern]] = [
    # greenhouse embed widget: /embed/job_app?for={slug} (& maybe more params)
    ("greenhouse", re.compile(
        r"^https?://(?:boards|job-boards)\.greenhouse\.io/embed/job_app"
        r"\?(?:.*&)?for=([a-z0-9][a-z0-9_-]*)", re.I)),
    # workable apply-path shape: apply.workable.com/{slug}/j/{id}
    ("workable", re.compile(
        r"^https?://apply\.workable\.com/([a-z0-9][a-z0-9-]*)(?:/|$)", re.I)),
    ("greenhouse", re.compile(
        r"^https?://(?:boards|job-boards|boards-api)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9][a-z0-9_-]*)", re.I)),
    ("lever", re.compile(
        r"^https?://jobs\.(?:eu\.)?lever\.co/([a-z0-9][a-z0-9_-]*)", re.I)),
    ("ashby", re.compile(
        r"^https?://jobs\.ashbyhq\.com/([a-z0-9][a-z0-9_-]*)", re.I)),
    ("smartrecruiters", re.compile(
        r"^https?://(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9][A-Za-z0-9_-]*)", re.I)),
    # workable subdomain shape: {slug}.workable.com ('apply' excluded — that
    # host's slug is a path segment, matched above)
    ("workable", re.compile(
        r"^https?://(?!apply\.)([a-z0-9][a-z0-9-]*)\.workable\.com", re.I)),
    ("personio", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)", re.I)),
    ("workday", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.wd\d+\.myworkdayjobs\.com", re.I)),
    ("teamtailor", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.teamtailor\.com", re.I)),
    ("recruitee", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.recruitee\.com", re.I)),
    ("breezy", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.breezy\.hr", re.I)),
    ("bamboohr", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.bamboohr\.com/careers", re.I)),
    ("recruitcrm", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.recruitcrm\.io", re.I)),
    ("jazzhr", re.compile(
        r"^https?://([a-z0-9][a-z0-9-]*)\.applytojob\.com", re.I)),
]

# Canonical public board URL per platform (the URL a human opens; the
# adapters use their API endpoints, derived from the same slug).
_BOARD_URL: dict[str, str] = {
    "greenhouse": "https://boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
    "smartrecruiters": "https://careers.smartrecruiters.com/{slug}",
    "workable": "https://{slug}.workable.com",
    "personio": "https://{slug}.jobs.personio.com",
    "teamtailor": "https://{slug}.teamtailor.com",
    "recruitee": "https://{slug}.recruitee.com",
    "breezy": "https://{slug}.breezy.hr",
    "bamboohr": "https://{slug}.bamboohr.com/careers",
    "recruitcrm": "https://{slug}.recruitcrm.io",
    "jazzhr": "https://{slug}.applytojob.com",
    "workday": "https://{slug}.wd1.myworkdayjobs.com",  # tenant only; instance unknown
}


def detect_platform(link: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(platform, slug) for a job link, or (None, None) when not an ATS link.

    Only the well-supported path/subdomain shapes are recognised; anything
    else (aggregator permalinks, company careers pages) returns None — the
    caller treats that as 'no platform signal', never an error.
    """
    if not link:
        return (None, None)
    url = link.strip()
    for platform, pattern in _PLATFORM_PATTERNS:
        m = pattern.match(url)
        if m:
            return (platform, m.group(1).lower())
    return (None, None)


def board_url(platform: str, slug: str) -> Optional[str]:
    template = _BOARD_URL.get(platform)
    return template.format(slug=slug) if template else None


# ── Company name → slug candidates ───────────────────────────────────────────

# Legal-entity suffixes that NEVER appear in an ATS slug, stripped first
# ("Linear Labs, Inc." → "linear labs" → 'linear-labs'/'linearlabs').
_LEGAL_SUFFIX = re.compile(
    r"\b(?:inc|llc|ltd|llp|plc|gmbh|ag|sa|sas|srl|bv|oy|ab|as|pty|pvt|co|"
    r"corp|corporation|holdings)\b", re.I)

# Descriptor words companies DROP when registering their board slug
# ("Linear Labs" → 'linear'; "Palantir Technologies" → 'palantir').
_DESCRIPTOR_NOISE = re.compile(
    r"\b(?:group|technologies|technology|company|labs?|the|digital|global)\b",
    re.I)


def company_slug_candidates(company: str) -> list[str]:
    """Plausible ATS slugs for a company name, most-likely first.

    Two stripping levels (legal-only, then legal+descriptor), each yielding a
    dashed and a fused form, plus the first-token form. E.g.
    "Linear Labs, Inc." → ['linear-labs', 'linearlabs', 'linear'].
    Directory slugs are lowercase; numeric slugs (greenhouse 103644278)
    only ever come from the dataset, never from a name.
    """
    if not company or not company.strip():
        return []
    base = normalize_match_text(company)
    variants: list[str] = []
    for source_text in (
            _LEGAL_SUFFIX.sub(" ", base),              # linear labs
            _DESCRIPTOR_NOISE.sub(" ", _LEGAL_SUFFIX.sub(" ", base))):  # linear
        slug = re.sub(r"\s+", "-", source_text.strip())
        if slug:
            variants.append(slug)

    candidates: list[str] = []
    for slug in variants:
        if slug not in candidates:
            candidates.append(slug)
        fused = slug.replace("-", "")
        if fused and fused not in candidates:
            candidates.append(fused)
    # first-token form ("linear labs" often registers as just 'linear')
    for slug in list(variants):
        if "-" in slug:
            first = slug.split("-")[0]
            if first not in candidates and len(first) > 2:
                candidates.append(first)
    return [c for c in candidates if c and not c.isdigit()]


# ── Directory lookup ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Board:
    platform: str          # 'ashby' | 'greenhouse' | 'lever' | 'smartrecruiters'
    slug: str
    company: str           # canonical name the directory learned, may be ''
    job_count: Optional[int]
    board_url: str


class AtsDirectory:
    """Read-only company→board resolver over the drained directory.

    Two backends, in order of preference:
    1. `data/ats_directory.db` (produced by scripts/build_ats_directory.py,
       gitignored, includes pending/dead state + drain progress stats);
    2. `data/ats_directory_snapshot.json` (committed export — live boards
       only; instant cold start in a fresh sandbox).

    Neither present → resolve() returns [] (flag, never drop); a partially
    drained DB simply resolves fewer companies, and improves as the prober
    daemon advances.
    """

    def __init__(self, db_path: Path | str | None = None,
                 snapshot_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else ATS_DIRECTORY_PATH
        self.snapshot_path = (Path(snapshot_path) if snapshot_path
                              else ATS_SNAPSHOT_PATH)
        self._conn: Optional[sqlite3.Connection] = None
        self._slug_cache: dict[str, list[Board]] = {}
        # snapshot-mode indexes: slug -> boards, learned company name -> boards
        self._snap_by_slug: dict[str, list[Board]] = {}
        self._snap_by_name: dict[str, list[Board]] = {}
        if self.db_path.exists():
            try:
                self._conn = sqlite3.connect(
                    f"file:{self.db_path.resolve()}?mode=ro", uri=True,
                    timeout=5.0)
                self._conn.row_factory = sqlite3.Row
            except sqlite3.Error:
                self._conn = None
        if self._conn is None and self.snapshot_path.exists():
            self._load_snapshot()

    def _load_snapshot(self) -> None:
        import json
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for entry in data.get("boards", []):
            if not isinstance(entry, dict):
                continue
            try:
                b = Board(platform=entry["platform"], slug=entry["slug"],
                          company=entry.get("company") or "",
                          job_count=entry.get("job_count"),
                          board_url=entry.get("board_url") or "")
            except (KeyError, TypeError):
                continue
            self._snap_by_slug.setdefault(b.slug, []).append(b)
            if b.company:
                self._snap_by_name.setdefault(b.company.strip(), []).append(b)

    @property
    def mode(self) -> str:
        """'db' | 'snapshot' | 'none' (diagnostics + tests)."""
        if self._conn is not None:
            return "db"
        if self._snap_by_slug:
            return "snapshot"
        return "none"

    @property
    def available(self) -> bool:
        return self._conn is not None or bool(self._snap_by_slug)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        # snapshot mode has nothing to close, but keep the indexes so a
        # close()-then-resolve() misuse doesn't crash (it resolves [])
        self._snap_by_slug = {}
        self._snap_by_name = {}

    def __enter__(self) -> "AtsDirectory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def stats(self) -> dict[str, int]:
        """Pending/live/dead/error counts + total (for report wiring).

        Snapshot mode reports only the live-exported count (the snapshot
        carries no pending/dead state by design)."""
        if self._conn is not None:
            try:
                rows = self._conn.execute(
                    "SELECT ats, status, COUNT(*) n FROM directory "
                    "GROUP BY ats, status").fetchall()
            except sqlite3.Error:
                return {}
            out: dict[str, int] = {}
            for r in rows:
                out[f"{r['ats']}:{r['status']}"] = r["n"]
                out[r["status"]] = out.get(r["status"], 0) + r["n"]
                out["total"] = out.get("total", 0) + r["n"]
            return out
        if self._snap_by_slug:
            live = sum(len(v) for v in self._snap_by_slug.values())
            return {"live": live, "live_exported": live}
        return {}

    def _rows_to_boards(self, rows: list[sqlite3.Row]) -> list[Board]:
        boards = []
        for r in rows:
            platform = r["ats"]
            slug = r["slug"]
            boards.append(Board(
                platform=platform, slug=slug,
                company=r["company"] or "",
                job_count=r["job_count"],
                board_url=board_url(platform, slug) or "",
            ))
        return boards

    def _live_by_slug(self, slug: str) -> list[Board]:
        """LIVE boards with this exact slug (any platform)."""
        if self._conn is not None:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM directory WHERE slug = ? "
                    "AND status = 'live' ORDER BY job_count DESC",
                    (slug,)).fetchall()
            except sqlite3.Error:
                return []
            return self._rows_to_boards(rows)
        return sorted(self._snap_by_slug.get(slug, []),
                      key=lambda b: -(b.job_count or 0))

    def _live_by_company_name(self, name: str) -> list[Board]:
        """LIVE boards whose LEARNED company name equals `name` exactly."""
        if self._conn is not None:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM directory WHERE status = 'live' "
                    "AND company != '' AND company = ? "
                    "ORDER BY job_count DESC", (name,)).fetchall()
            except sqlite3.Error:
                return []
            return self._rows_to_boards(rows)
        return sorted(self._snap_by_name.get(name.strip(), []),
                      key=lambda b: -(b.job_count or 0))

    def resolve_slug(self, platform: str, slug: str) -> Optional[Board]:
        """Exact (ats, slug) lookup — live rows only."""
        if not slug or not self.available:
            return None
        key = f"{platform}|{slug}"
        if key in self._slug_cache:
            cached = self._slug_cache[key]
            return cached[0] if cached else None
        if self._conn is not None:
            try:
                rows = self._conn.execute(
                    "SELECT * FROM directory WHERE ats = ? AND slug = ? "
                    "AND status = 'live'", (platform, slug)).fetchall()
            except sqlite3.Error:
                return None
            boards = self._rows_to_boards(rows)
        else:
            boards = [b for b in self._snap_by_slug.get(slug, [])
                      if b.platform == platform]
        self._slug_cache[key] = boards
        return boards[0] if boards else None

    def resolve(self, company: str) -> list[Board]:
        """All LIVE boards for a company name (best candidates first).

        Two passes: (1) slug candidates from the normalized name, (2) the
        company-name column the prober learned (e.g. greenhouse's
        meta.company_name). slug matches rank first — they are exact
        structural hits; name matches are corroboration.
        """
        if not company or not self.available:
            return []
        found: dict[tuple[str, str], Board] = {}

        for slug in company_slug_candidates(company):
            for b in self._live_by_slug(slug):
                found.setdefault((b.platform, b.slug), b)

        if found:
            return sorted(found.values(),
                          key=lambda b: -(b.job_count or 0))

        # Name-corroboration pass (only when no slug hit — a slug hit is
        # already an exact structural match; name rows would only add noise).
        if normalize_match_text(company):
            for b in self._live_by_company_name(company.strip()):
                found.setdefault((b.platform, b.slug), b)
        return sorted(found.values(), key=lambda b: -(b.job_count or 0))
