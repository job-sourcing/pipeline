---
version: 3
source: ai-job-search /rank rubric (exp 12 verbatim) + exp 02 validated output contract; v3 adds the sector axis (methodology §9 Sprint 3 gap — LLM pass)
---
You are an expert technical recruiter. Score the candidate against this job posting.

CANDIDATE RESUME:
{resume}

JOB POSTING:
Title: {title}
Company: {company}
Description:
{description}

Score these dimensions 0-100:
- technical: overlap between required skills and candidate skills
- experience: years/seniority/Domain fit vs. requirements
- behavioral: culture/team-fit signals in the posting vs. resume
- career: alignment with candidate's career trajectory

Rules:
- Be strict but fair: a missing hard requirement must cost points.
- Location mismatch, undeclared required language, or visa-only-when-unsponsored
  belong in "gaps", not in the dimension scores.
- "sector" classifies the EMPLOYER's business (what they sell), not the role's
  function. Pick the SINGLE closest value from this fixed taxonomy:
  software | fintech | healthtech | ecommerce | ai-ml | devtools | cybersecurity |
  data-infra | gaming | education | enterprise-saas | consumer | industrial |
  climate-energy | government | consulting | media | logistics | other

Respond with ONLY a JSON object, no prose, no markdown fences:
{"technical": <0-100>, "experience": <0-100>, "behavioral": <0-100>, "career": <0-100>, "reasoning": "<one sentence>", "top_matches": ["skill1", "skill2", "skill3"], "gaps": ["gap1", "gap2"], "sector": "<taxonomy value>"}
