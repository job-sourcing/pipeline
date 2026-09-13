"""JobSpy aggregator source — wraps the `python-jobspy` pip package
(https://github.com/curioustone/jobspy, PyPI: python-jobspy, import: jobspy)
which scrapes 7+ boards (Indeed, Glassdoor, Google Jobs, ZipRecruiter,
LinkedIn, Bayt, Naukri, BDJobs) behind one `scrape_jobs(site_name=[...])` API.

This single source is the biggest coverage uplift in the pipeline: Indeed +
Glassdoor + Google Jobs + ZipRecruiter together cover the majority of US job
postings. By default we enable only Indeed + Glassdoor + Google Jobs +
ZipRecruiter (LinkedIn + Bayt + Naukri are excluded to avoid overlap with our
LinkedIn Guest source and to keep the request volume polite; the user can
override via cfg.jobspy_sites).

Install footprint (measured in this container, python-jobspy 1.1.82):
  - python-jobspy          ~52 KB wheel
  - tls-client             ~41 MB   (bundled TLS impersonation binary — the
                                     heavy one; replaces curl-cffi role)
  - numpy 1.26.3           ~18 MB   (DOWNGRADES numpy 2.x — see warning)
  - regex 2024.11.6        ~800 KB   (DOWNGRADES regex 2026.x)
  - markdownify 0.13.1     ~10 KB   (DOWNGRADES markdownify 1.x)
  - pandas / pydantic / bs4 / requests already satisfied (no extra cost)
  NO selenium, NO chromium binary, NO torch — JobSpy uses tls-client (a
  bundled TLS impersonation library) instead of a headless browser, so it
  works in containers without Chrome installed (verified — see worklog
  SRC-JOBSPY-2 Step 1).

NOTE on the numpy downgrade: python-jobspy pins `NUMPY==1.26.3` (capitalised
on purpose). In shared envs that have rasterio / opencv-python-headless /
nlopt / exchange-calendars installed, those packages require numpy>=2 and
will be broken until they're reinstalled. This is a jobspy packaging smell
(the capitalised pin name is unusual) — the project's own pyproject lists
only requests + click so the project itself is unaffected, but downstream
container users should be aware. We do NOT touch pyproject.toml here (per
spec); the main agent should add `python-jobspy` (NOT `jobspy` — that's an
unrelated Redis package that won the namespace) to the optional-deps group.

NOTE on enum names: Site enum in 1.1.82 is LINKEDIN / INDEED / ZIP_RECRUITER
/ GLASSDOOR / GOOGLE / BAYT / NAUKRI / BDJOBS — note GOOGLE (not GOOGLE_JOBS)
and ZIP_RECRUITER (not ZIPRECRUITER). We accept both spellings in
cfg.jobspy_sites so users can copy-paste the spec's "google_jobs" /
"ziprecruiter" strings without surprise (see _SITE_ALIASES).
"""
from __future__ import annotations

import math
from typing import Optional

from ..config import Config
from ..models import Job
from .base import (
    detect_h1b, extract_email, normalize_date, salary_text,
)

# User-facing site names (cfg.jobspy_sites) → JobSpy Site enum member names.
# Both spellings accepted for each board so the spec defaults
# ["indeed","glassdoor","google_jobs","ziprecruiter"] work without surprises.
_SITE_ALIASES = {
    "indeed": "indeed",
    "glassdoor": "glassdoor",
    "google_jobs": "google",          # spec name → JobSpy enum
    "google": "google",
    "ziprecruiter": "zip_recruiter",  # spec name → JobSpy enum
    "zip_recruiter": "zip_recruiter",
    "ziprecruit": "zip_recruiter",
    "linkedin": "linkedin",
    "bayt": "bayt",
    "naukri": "naukri",
    "bdjobs": "bdjobs",
}


def _normalize_site(name: str) -> Optional[str]:
    """Map a user-facing site name to JobSpy's Site enum member name."""
    return _SITE_ALIASES.get((name or "").strip().lower())


def _clean(value) -> object:
    """Coerce pandas NaN → None. Passes everything else through.

    JobSpy's DataFrame returns None for missing object fields and float NaN
    for missing numeric fields. We normalise both to None so the downstream
    Job construction doesn't have to deal with NaN sentinel values. (pd.NA
    would also be a candidate but is not produced by jobspy's response
    parser — None/NaN are the only sentinels observed in 1.1.82 captures.)
    """
    if value is None:
        return None
    try:
        # Pandas NaN is a regular float — caught here.
        if isinstance(value, float) and math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def is_configured(cfg: Config) -> bool:
    """True iff the `python-jobspy` pip package is importable.

    JobSpy needs no API key (it scrapes public job-board search pages via
    tls-client impersonation), so "configured" simply means "installed".
    """
    try:
        import jobspy  # noqa: F401  — import side-effect only
        return True
    except ImportError:
        return False


def _scrape_jobs(*args, **kwargs):
    """Lazy seam around `jobspy.scrape_jobs`.

    Kept as a module-level callable (rather than `from jobspy import
    scrape_jobs` inline in fetch) so the unit tests can monkeypatch THIS name
    (jobsearch.sources.jobspy_source._scrape_jobs) without importing jobspy,
    matching the established mockable-seam pattern used by every other source
    (adzuna.fetch_json, jsearch.fetch_json, etc.).
    """
    from jobspy import scrape_jobs as _real
    return _real(*args, **kwargs)


def fetch(keywords: str, location: str = "Remote",
          num_results: int = 20, *, hours_old: Optional[int] = None,
          country_indeed: str = "USA", cfg=None) -> list[Job]:
    """Aggregate jobs across cfg.jobspy_sites via the `python-jobspy` package.

    Returns one Job per DataFrame row, with `source` set to
    `f"JobSpy.{SiteName}"` (e.g. "JobSpy.Indeed") so the dedup layer can
    distinguish cross-aggregator dupes (the same Indeed posting arriving via
    JobSpy vs. an Indeed direct fetch would otherwise collide).

    Raises RuntimeError when the package isn't installed or the scrape fails.
    """
    cfg = cfg or Config()
    if not is_configured(cfg):
        raise RuntimeError(
            "python-jobspy not installed. "
            "Run: pip install python-jobspy  (PyPI name has the `python-` "
            "prefix — bare `jobspy` is an unrelated Redis package)."
        )

    # Resolve site list. Default = Indeed + Glassdoor + Google Jobs +
    # ZipRecruiter (per spec; LinkedIn/Bayt/Naukri excluded to avoid overlap
    # with our LinkedIn Guest source and to keep request volume polite).
    # NB: a falsy-or check would conflate `None` (not set → use defaults)
    # with `[]` (explicitly set to "no sites" → return []); use `is None`.
    sites_raw = getattr(cfg, "jobspy_sites", None)
    if sites_raw is None:
        sites_raw = ["indeed", "glassdoor", "google_jobs", "ziprecruiter"]
    sites: list[str] = []
    for s in list(sites_raw):
        mapped = _normalize_site(s)
        if mapped and mapped not in sites:
            sites.append(mapped)
    if not sites:
        return []

    try:
        df = _scrape_jobs(
            site_name=sites,
            search_term=keywords,
            location=location,
            results_wanted=num_results,
            hours_old=hours_old if hours_old else None,
            country_indeed=country_indeed,
        )
    except Exception as exc:  # noqa: BLE001 — surface a uniform error type
        msg = f"JobPy scrape failed: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg[:300])

    if df is None or len(df) == 0:
        return []

    jobs: list[Job] = []
    for row in df.itertuples(index=False):
        # row is a namedtuple; missing-object fields show up as NaN/None.
        title = str(_clean(getattr(row, "title", None)) or "").strip()
        company = str(_clean(getattr(row, "company", None)) or "").strip()
        if not title or not company:
            continue

        site_raw = str(_clean(getattr(row, "site", None)) or "unknown").lower()
        site_label = site_raw.replace("_", " ").title() or "Unknown"
        source = f"JobSpy.{site_label}"

        link = str(_clean(getattr(row, "job_url", None)) or "").strip()
        if not link:
            # Fall back to job_url_direct if the primary URL is missing.
            link = str(_clean(getattr(row, "job_url_direct", None)) or "").strip()

        desc_raw = _clean(getattr(row, "description", None))
        desc = "" if desc_raw is None else str(desc_raw)

        loc_raw = _clean(getattr(row, "location", None))
        loc = "" if loc_raw is None else str(loc_raw).strip()

        # is_remote comes through as a Python bool (verified — see worklog).
        is_remote_flag = bool(_clean(getattr(row, "is_remote", False)) or False)
        # Heuristic: also flag remote if the location text mentions "remote".
        if not is_remote_flag and loc and "remote" in loc.lower():
            is_remote_flag = True

        date_val = _clean(getattr(row, "date_posted", None))
        date_str = normalize_date(date_val)

        # Salary: combine min/max + currency via base.salary_text; also store
        # the numeric bounds on the Job for downstream filtering.
        min_amt = _clean(getattr(row, "min_amount", None))
        max_amt = _clean(getattr(row, "max_amount", None))
        currency = str(_clean(getattr(row, "currency", None)) or "USD").strip() or "USD"
        sal_text = salary_text(min_amt, max_amt, currency)

        # Contact email: prefer the structured `emails` column from JobSpy's
        # DataFrame (P1-4 fix). JobSpy's scraper extracts recruiter emails
        # server-side and exposes them as a list/dict; if it's present and
        # non-empty, use it directly. Fall back to regex extraction from the
        # description when the structured field is absent (matches the prior
        # behaviour for rows where JobSpy didn't detect an email).
        contact_email: Optional[str] = None
        emails_val = _clean(getattr(row, "emails", None))
        if emails_val:
            # JobSpy returns emails as either a list of strings or a
            # comma-separated string. Normalize to a single string.
            if isinstance(emails_val, (list, tuple)):
                emails_list = [str(e).strip() for e in emails_val if e]
            else:
                emails_list = [s.strip() for s in str(emails_val).split(",") if s.strip()]
            # De-noise (drop noreply/notifications per extract_email convention).
            noise = ("noreply", "no-reply", "example", "donotreply",
                     "notifications@", "support@", "info@", "admin@")
            filtered = [e for e in emails_list if not any(n in e.lower() for n in noise)]
            if filtered:
                contact_email = filtered[0]
        if not contact_email and desc:
            contact_email = extract_email(desc)

        jobs.append(Job(
            title=title,
            company=company,
            description=desc,
            link=link,
            contact_email=contact_email,
            source=source,
            location=loc,
            date_posted=date_str,
            remote=is_remote_flag,
            h1b_mention=detect_h1b(f"{title} {desc}"),
            salary_text=sal_text,
            salary_min=float(min_amt) if isinstance(min_amt, (int, float)) else None,
            salary_max=float(max_amt) if isinstance(max_amt, (int, float)) else None,
            search_query=keywords,
        ))
        if len(jobs) >= num_results:
            break
    return jobs
