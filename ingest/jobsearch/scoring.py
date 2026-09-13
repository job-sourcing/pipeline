"""Two-stage scoring pipeline (D8, exp 02 + exp 12 rubric).

Stage 1: TF-IDF cosine rank (free) over the whole pool.
Stage 2: LLM scoring of the RELATIVE top-N by rank — never an absolute
cutoff (exp 02 data: TF-IDF 5-12/100 while LLM 5-85; an absolute 30%
cutoff would have dropped every job the LLM ranked top).

TF-IDF implementation lifted from exp 02 score_jobs.mjs (validated).
"""
from __future__ import annotations

from typing import Iterable

from .config import Config
from .llm import LLMSeam
from .models import Job
from .storage import Store

_STOPWORDS = set("""
the a an and or but is are was were be been being to of in on at by for with
about against between into through during before after above below from up
down out off over under again further then once here there when where why how
all any both each few more most other some such no nor not only own same so
than too very s t can will just don should now we you our your their they
them this that these those i me my myself what which who whom as if because
while it its itself have has had having do does did doing would could should
""".split())


def tokenize(text: str) -> list[str]:
    import re
    return [t for t in (re.findall(r"[a-z][a-z+#.0-9]+", text.lower()))
            if len(t) >= 2 and t not in _STOPWORDS]


def tfidf_score(resume_text: str, job_text: str) -> float:
    """Cosine similarity of raw term-frequency vectors, 0-100 (exp 02 semantics)."""
    r_tokens, j_tokens = tokenize(resume_text), tokenize(job_text)
    if not r_tokens or not j_tokens:
        return 0.0
    r_freq: dict[str, int] = {}
    for t in r_tokens:
        r_freq[t] = r_freq.get(t, 0) + 1
    j_freq: dict[str, int] = {}
    for t in j_tokens:
        j_freq[t] = j_freq.get(t, 0) + 1
    dot = sum(c * j_freq.get(t, 0) for t, c in r_freq.items())
    mag_r = sum(c * c for c in r_freq.values()) ** 0.5
    mag_j = sum(c * c for c in j_freq.values()) ** 0.5
    if mag_r == 0 or mag_j == 0:
        return 0.0
    return round(dot / (mag_r * mag_j) * 100, 1)


def rank_by_tfidf(jobs: Iterable[Job], resume_text: str) -> list[tuple[Job, float]]:
    """Rank ALL jobs by TF-IDF (stage 1). Returns (job, score) sorted desc."""
    scored = [(job, tfidf_score(resume_text, job.job_text())) for job in jobs]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def score_pipeline(jobs: list[Job], resume_text: str, cfg: Config,
                   store: Store, seam: LLMSeam | None = None,
                   top_n: int | None = None) -> list[Job]:
    """Full pipeline: TF-IDF rank → LLM score top-N (relative) → persist.

    Mutates and returns the jobs with tfidf_score/llm_* fields set.
    Jobs outside top-N keep tfidf_score and llm_score=None.
    """
    seam = seam or LLMSeam(cfg, store)
    ranked = rank_by_tfidf(jobs, resume_text)
    n = top_n or cfg.llm_top_n

    for job, score in ranked:
        job.tfidf_score = score
        if job.id is not None:
            store.update_scores(job.id, tfidf=score)

    top = [job for job, _ in ranked[:n] if job.id is not None]
    if top:
        results = seam.score_jobs(top, resume_text)
        by_id = {r["id"]: r for r in results}
        for job in top:
            r = by_id.get(job.id)
            if r and r.get("ok"):
                job.llm_score = r["overall"]
                job.llm_reasoning = f"[{r['band']}] {r['reasoning']}"
                job.llm_matches = r.get("top_matches", [])
                job.llm_gaps = r.get("gaps", [])
                if r.get("sector"):
                    job.sector = r["sector"]
                store.update_scores(job.id, llm={
                    "score": r["overall"],
                    "reasoning": f"[{r['band']}] {r['reasoning']}",
                    "top_matches": r.get("top_matches", []),
                    "gaps": r.get("gaps", []),
                    "sector": r.get("sector"),
                })
    return jobs
