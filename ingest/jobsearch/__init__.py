"""jobsearch — E2E AI job-search pipeline.

Layered architecture (DECISIONS.md D1-D13):

    sources/     data sourcing (lifted ResumeWing clients + LinkedIn Guest)
    dedup.py     fuzzy dedup (ported from job-ops job-matching.ts)
    storage.py   SQLite single source of truth (WAL, single-writer)
    pipeline_lock.py  cross-process advisory lock (ported career-ops protocol)
    llm.py       THE seam to Node/z-ai SDK (envelope contract, cache, retry)
    scoring.py   TF-IDF relative top-N pre-filter + LLM rubric scoring
    cli.py       user-facing commands

All LLM calls go through llm.py (D1.1). All writes go through Python (D4).
"""

__version__ = "0.1.0"
