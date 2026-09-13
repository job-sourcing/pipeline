"""Categorization — Sprint 3 port of T2's classify-tier.mjs + skill-extract.mjs
plus a central work-mode detector (T2 does work-mode inline per provider; a
single heuristic over title/location/description is the package equivalent).

Provenance: career-ops-research (T2) classify-tier.mjs (2026-08-07 vintage —
leftmost-marker rule, associate + program-bridge guards) and skill-extract.mjs
(#1896 vocabulary: single source of truth for hard-skill recognition, exact
aliases only, no umbrella aliases, Go/SAFe as case-sensitive side passes).

Port notes:
- classify_tier keeps T2's exact semantics: POSITION decides (leftmost marker
  wins), weight only breaks ties at the same offset; guards (a) "Associate
  Director" and (b) "Intern Program Director" resolve senior BEFORE the loop.
- extract_skills keeps T2's alternation order (longest-first within a family:
  'React Native' before 'React', 'Lean Six Sigma' before 'Six Sigma') and the
  (?<!\\w)/(?!\\w) boundaries so C++/C#/.NET match standalone.
- Tier default is 'mid' (T2's documented unknown bucket).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

__all__ = [
    "classify_tier",
    "extract_skills",
    "canonicalize_skill",
    "detect_work_mode",
    "categorize",
]


# ── Seniority tier (classify-tier.mjs port) ─────────────────────────────────

def _clean_title(title: str) -> str:
    """Fold dotted acronyms so \\bA.I.\\b-style splits don't dodge matchers."""
    clean = re.sub(r"\bA\.I\.", "AI", title, flags=re.IGNORECASE)
    clean = re.sub(r"\bA\.I\b", "AI", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bA\.\s+I\b", "AI", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bI\.T\.", "IT", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bI\.T\b", "IT", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bI\.\s+T\b", "IT", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bi/o\b", "IO", clean, flags=re.IGNORECASE)
    return clean


# Compound graduate matcher: 'graduate' AND (program|scheme) — the level
# word is 'graduate', NOT the qualifier (a bare "Graduate Engineer" is mid).
# The SAME compiled object is referenced in _TIER_MATCHERS so classify_tier
# can recognise the compound matcher by identity (`is`) and apply both
# conditions before using its position.
_GRADUATE_COMPOUND = re.compile(r"\bgraduate\b.*\b(?:program|scheme)\b",
                                re.I | re.S)
_GRADUATE_LEVEL_WORD = re.compile(r"\bgraduate\b", re.I)

# (pattern, tier, weight) — weight survives only as the tie-break for two
# markers at the same offset (keeps 'mid-level' over 'mid', 'entry-level'
# over 'entry'). Ordering in the list is otherwise irrelevant: the LEFTMOST
# marker in the title decides (T2 #2852/#2009 lineage).
_TIER_MATCHERS: list[tuple[re.Pattern, str, int]] = [
    # Senior tier (weight 4)
    (re.compile(r"\bchief\b", re.I), "senior", 4),
    (re.compile(r"\bvp\b", re.I), "senior", 4),
    (re.compile(r"\bvice\s+president\b", re.I), "senior", 4),
    (re.compile(r"\bdirector\b", re.I), "senior", 4),
    (re.compile(r"\bprincipal\b", re.I), "senior", 4),
    (re.compile(r"\bstaff\b", re.I), "senior", 4),
    (re.compile(r"\blead\b", re.I), "senior", 4),
    (re.compile(r"\bsenior\b", re.I), "senior", 4),
    (re.compile(r"\bsr\b", re.I), "senior", 4),
    (re.compile(r"\bsr\.", re.I), "senior", 4),
    (re.compile(r"\bhead\s+of\b", re.I), "senior", 4),
    (re.compile(r"\b[a-z]{2,}[\s-](?:iii|iv|v)\b", re.I), "senior", 4),
    # Mid tier (weight 3)
    (re.compile(r"\bmid-level\b", re.I), "mid", 3),
    (re.compile(r"\bmid\b", re.I), "mid", 3),
    (re.compile(r"\b[a-z]{2,}[\s-](?:ii)\b", re.I), "mid", 3),
    (re.compile(r"\b(?:l4|l5)\b", re.I), "mid", 3),
    # Entry tier (weight 2)
    (re.compile(r"\bentry-level\b", re.I), "entry", 2),
    (re.compile(r"\bentry\b", re.I), "entry", 2),
    (re.compile(r"\bassociate\b", re.I), "entry", 2),
    (re.compile(r"\bjunior\b", re.I), "entry", 2),
    (re.compile(r"\b[a-z]{2,}[\s-](?:i)\b", re.I), "entry", 2),
    (re.compile(r"\b(?:l1|l2)\b", re.I), "entry", 2),
    # Intern tier (weight 1)
    (re.compile(r"\binternship\b", re.I), "intern", 1),
    (re.compile(r"\bintern\b", re.I), "intern", 1),
    (re.compile(r"\btrainee\b", re.I), "intern", 1),
    (re.compile(r"\bco-op\b", re.I), "intern", 1),
    (_GRADUATE_LEVEL_WORD, "intern", 1),
]

_ASSOCIATE = re.compile(r"\bassociate\b", re.I)
_SENIOR_AFTER_ASSOCIATE = re.compile(
    r"\b(?:director|vice\s+president|vp|principal|partner|chief|head\s+of)\b", re.I)

_PROGRAM_BRIDGE = re.compile(
    r"\b(?:intern(?:ship)?|trainee|co-op|graduate|junior|entry(?:-level)?)"
    r"\s+(?:program|scheme|talent|cohort)\b", re.I)
_SENIOR_WORD = re.compile(
    r"\b(?:chief|vp|vice\s+president|director|principal|staff|lead|senior|"
    r"sr\.?|head\s+of|partner)\b", re.I)


def classify_tier(title: Optional[str]) -> str:
    """Classify a job title into exactly one seniority tier.

    Tiers: 'intern' | 'entry' | 'mid' | 'senior'. Plain titles with no level
    marker default to 'mid' (T2's documented unknown bucket — configure
    skip-lists with that in mind).
    """
    if not isinstance(title, str):
        return "mid"
    clean = _clean_title(title)

    # Guard (a): "Associate Director/VP/Chief/Principal/Partner/Head-of"
    # resolves to senior — the prefix qualifies a senior band, it does not
    # demote it. Checked before the loop because `associate` at index 0 would
    # otherwise win the leftmost-marker rule.
    m = _ASSOCIATE.search(clean)
    if m and _SENIOR_AFTER_ASSOCIATE.search(clean, m.end()):
        return "senior"

    # Guard (b): [intern/entry marker] + [programme bridge noun] + [senior
    # role noun] → senior. "Intern Program Director" manages the programme.
    bm = _PROGRAM_BRIDGE.search(clean)
    if bm and _SENIOR_WORD.search(_PROGRAM_BRIDGE.sub(" ", clean).strip()):
        return "senior"

    best_tier: Optional[str] = None
    best_weight = 0
    best_index: Optional[int] = None
    for pattern, tier, weight in _TIER_MATCHERS:
        if pattern is _GRADUATE_LEVEL_WORD:  # compound matcher
            if not _GRADUATE_COMPOUND.search(clean):
                continue
            index = _GRADUATE_LEVEL_WORD.search(clean).start()
        else:
            m2 = pattern.search(clean)
            if not m2:
                continue
            index = m2.start()
        if (best_index is None or index < best_index
                or (index == best_index and weight > best_weight)):
            best_tier, best_weight, best_index = tier, weight, index

    return best_tier or "mid"


# ── Skill extraction (skill-extract.mjs port) ────────────────────────────────

# Alternation order is load-bearing: longest-first within a family so
# 'React Native' is preferred over 'React', 'Lean Six Sigma' over 'Six Sigma'.
_SKILL_TOKENS: list[str] = [
    # Languages
    "JavaScript", "TypeScript", "Python", "Ruby", "Java", "Golang", "Rust",
    "PHP", "Kotlin", "Swift", "Scala", "Elixir", r"C\+\+", "C#", r"\.NET", "SQL",
    # Frontend / frameworks
    "React Native", "React", "Angular", r"Vue\.?js", "Svelte", r"Next\.?js",
    "Django", "Flask", "FastAPI", "Rails", "Laravel", "Symfony", "Spring",
    r"Node\.?js", "NodeJS",
    # Data stores
    "MongoDB", "MySQL", "PostgreSQL", "Postgres", "Redis", "Elasticsearch",
    "Snowflake", "BigQuery", "Databricks", "DynamoDB", "Cassandra",
    # APIs / messaging
    "GraphQL", "gRPC", "Kafka", "RabbitMQ",
    # Cloud / infra
    "AWS", "GCP", "Azure", "Docker", "Kubernetes", "k8s", "Terraform",
    "Ansible", "Helm", "Jenkins", "GitHub Actions", "GitLab CI", "CI/CD",
    "Prometheus", "Grafana", "Datadog", "Supabase", "Inngest",
    # Data / ML / AI
    "PyTorch", "TensorFlow", "scikit-learn", "Pandas", "NumPy", "Spark",
    "Airflow", "dbt", "MLOps", "MLflow", "LangChain", "LlamaIndex",
    "Hugging Face", "RAG", r"LLMs?", "Prompt Engineering", r"Fine-?tuning",
    "Computer Vision", "NLP",
    # Analytics / enterprise
    "Tableau", "Power BI", "Looker", "Salesforce", "SAP",
    # Certifications / methodologies (T2 2026-08-07 addition) — both spellings
    # of every fused credential; spaced forms alias to the same display string.
    "PMI-ACP", "PMI ACP", "PgMP", "CAPM", "PMBOK", "PMP",
    "PRINCE2", "PRINCE 2",
    "Certified Scrum Product Owner", "Certified ScrumMaster",
    "Certified Scrum Master", "CSPO",
    "ITIL", "COBIT", "TOGAF",
    "Lean Six Sigma", "Lean Six-Sigma", "Six Sigma", "Six-Sigma",
    "CISSP", "CISM", "CIPP",
    # NOTE (kept from T2 after review #2603): 'CSM' is deliberately omitted —
    # in job-ad prose it far more often expands to Customer Success Manager.
    # 'SAFe' is also omitted (everyday word) — handled case-sensitively below.
]

_SKILL_PATTERN = re.compile(
    "(?<!\\w)(?:" + "|".join(_SKILL_TOKENS) + ")(?!\\w)", re.IGNORECASE)

# 'Go' is an everyday English word: only the exact standalone token "Go"
# counts (trailing hyphen disqualifies — "Go-to-market" is not the language).
_GO_PATTERN = re.compile(r"(?<!\w)Go(?![\w-])")
# 'SAFe' likewise: case-sensitive standalone token only.
_SAFE_PATTERN = re.compile(r"(?<!\w)SAFe(?!\w)")

# lowercase → canonical display casing (strip regex syntax from the tokens).
_SKILL_DISPLAY: dict[str, str] = {}
for _tok in _SKILL_TOKENS:
    _disp = _tok.replace("\\", "").replace("?", "")
    _SKILL_DISPLAY[_disp.lower()] = _disp

# Exact-alias canonicalization ONLY (lowercased match → display name). No
# umbrella aliases: "cloud" must never count as knowing AWS/GCP/Azure.
_SKILL_CANONICAL: dict[str, str] = {
    "k8s": "Kubernetes",
    "golang": "Go",
    "postgres": "PostgreSQL",
    "nodejs": "Node.js", "node.js": "Node.js",
    "vuejs": "Vue.js", "vue.js": "Vue.js",
    "nextjs": "Next.js", "next.js": "Next.js",
    "llm": "LLMs", "llms": "LLMs",
    "finetuning": "Fine-tuning", "fine-tuning": "Fine-tuning",
    "power bi": "Power BI",
    "github actions": "GitHub Actions",
    "gitlab ci": "GitLab CI",
    "ci/cd": "CI/CD",
    "hugging face": "Hugging Face",
    "react native": "React Native",
    "prompt engineering": "Prompt Engineering",
    "computer vision": "Computer Vision",
    "scikit-learn": "scikit-learn",
    "c++": "C++", "c#": "C#", ".net": ".NET",
    "nlp": "NLP", "rag": "RAG", "sql": "SQL", "aws": "AWS", "gcp": "GCP",
    "grpc": "gRPC", "dbt": "dbt", "mlops": "MLOps", "mlflow": "MLflow",
    # Certifications / methodologies
    "pmp": "PMP", "pmi-acp": "PMI-ACP", "pgmp": "PgMP", "capm": "CAPM",
    "pmbok": "PMBOK", "prince2": "PRINCE2", "cspo": "CSPO",
    "certified scrummaster": "Certified ScrumMaster",
    "itil": "ITIL", "cobit": "COBIT", "togaf": "TOGAF",
    "lean six sigma": "Lean Six Sigma", "six sigma": "Six Sigma",
    "cissp": "CISSP", "cism": "CISM", "cipp": "CIPP",
    "certified scrum master": "Certified ScrumMaster",
    "certified scrum product owner": "CSPO",
    "pmi acp": "PMI-ACP",
    "prince 2": "PRINCE2",
    "lean six-sigma": "Lean Six Sigma",
    "six-sigma": "Six Sigma",
}


def canonicalize_skill(token: str) -> str:
    """Canonical form of one raw token: 'k8s'→'Kubernetes'; unknown tokens
    pass through UNCHANGED (never title-cased — that manufactures keys like
    'Graphql' that miss the known-skills set)."""
    key = token.lower()
    return _SKILL_CANONICAL.get(key) or _SKILL_DISPLAY.get(key) or token


def extract_skills(text: Optional[str]) -> set[str]:
    """Set of canonical skill names present in a free-text blob."""
    if not text:
        return set()
    found: set[str] = set()
    for m in _SKILL_PATTERN.finditer(text):
        found.add(canonicalize_skill(m.group(0)))
    if _GO_PATTERN.search(text):
        found.add("Go")
    if _SAFE_PATTERN.search(text):
        found.add("SAFe")
    return found


# ── Work mode (central heuristic; T2 does this inline per provider) ──────────

_HYBRID = re.compile(r"\bhybrid\b", re.I)
_REMOTE = re.compile(r"\bremote(?:ly)?\b", re.I)
_ONSITE = re.compile(r"\bon(?:-|\s)?site\b|\bin(?:-|\s)?office\b|\bin\s+person\b", re.I)


def detect_work_mode(title: str = "", location: str = "",
                     description: str = "", remote_hint: bool = False) -> str:
    """'remote' | 'hybrid' | 'onsite' | 'unknown'.

    Precedence: explicit markers in title/location beat markers in the
    description (employers summarize the arrangement in the title; body text
    often mentions all three modes in boilerplate). Hybrid beats remote when
    both appear — "Hybrid (Remote-friendly)" is still a hybrid arrangement.
    The source's own remote flag is the last resort before unknown.
    """
    headline = f"{title or ''} {location or ''}"
    if _HYBRID.search(headline):
        return "hybrid"
    if _REMOTE.search(headline) and _ONSITE.search(headline):
        return "hybrid"
    if _REMOTE.search(headline):
        return "remote"
    if _ONSITE.search(headline):
        return "onsite"
    body = description or ""
    if _HYBRID.search(body):
        return "hybrid"
    if _REMOTE.search(body) and not _ONSITE.search(body):
        return "remote"
    if _ONSITE.search(body) and not _REMOTE.search(body):
        return "onsite"
    if remote_hint:
        return "remote"
    return "unknown"


def categorize(title: str = "", location: str = "", description: str = "",
               remote_hint: bool = False) -> dict:
    """All categorization axes for one job, in one call.

    Returns {'tier': str, 'skills': sorted list[str], 'work_mode': str} —
    ready to be spread onto a Job / a storage row.
    """
    return {
        "tier": classify_tier(title),
        "skills": sorted(extract_skills(
            f"{title}\n{description or ''}")),
        "work_mode": detect_work_mode(title, location, description, remote_hint),
    }
