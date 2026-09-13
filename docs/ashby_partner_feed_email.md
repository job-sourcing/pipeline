# Ashby Dedicated Partner Job Feed — outreach email (READY TO SEND)

> Sprint 4's highest-leverage single integration (methodology §6.1 Layer 5):
> the only ATS offering a unified feed of all opted-in customers' postings
> in one JSON/XML schema, hourly push, ~3,000 tech/AI companies (OpenAI,
> Shopify, Anthropic, Notion, Vercel). Free — partnership negotiation, no
> public pricing. Lead time: 2–4 weeks to provision, so send EARLY.
>
> **Action: send from YOUR inbox to `integrations@ashbyhq.com`, then record
> the send date in the worklog.** Replace `[...]` placeholders first.

---

**To**: integrations@ashbyhq.com
**Subject**: Partner Job Feed access request — job-search research pipeline

Hi Ashby Integrations team,

I'd like to request access to the Dedicated Partner Job Feed for a job
market research project I'm running.

**What I'm building**: an open, trust-scored job-sourcing pipeline that
aggregates postings from ATS-direct APIs and free aggregator tiers
(Greenhouse, Lever, SmartRecruiters, Ashby boards, Adzuna, USAJobs, and
others), deduplicates them with fuzzy repost detection, and scores each
posting on data-quality signals. It's a personal research project at
current scale, not a commercial product.

**Why the Partner Feed**: I already consume Ashby's public per-tenant
Posting API (the `jobs.ashbyhq.com/{org}` board endpoint) for a curated
company list, and it works well — but the per-board discovery problem
(tenant slug hunting) is the bottleneck. A unified feed of opted-in
customers would replace slug-directory probing with hourly authoritative
data, which is exactly the layer my pipeline is weakest on.

**What I'd use it for**:
- Ingest postings into a research dataset with provenance and freshness
  tracking (hourly cadence is ideal).
- Cross-reference against aggregator sightings to study repost/ghost-job
  patterns (same opening listed across boards, stale pipelines).
- No redistribution, no scraping of Ashby-hosted pages beyond the feed
  itself, and happy to comply with any attribution or rate-limit terms.

**Technical details**:
- Ingestion: JSON or XML feed pull (I can consume either schema), run from
  a scheduled pipeline with backoff and Retry-After honoring.
- Volume expectation: research scale, well under any reasonable rate limit.
- I'm an individual researcher — happy to start in a sandbox tier if that's
  the standard first step.

Could you share the provisioning process and any partner terms? Happy to
fill out an application or hop on a call.

Thanks,
[YOUR NAME]
[EMAIL SIGNATURE — role/background, e.g. "Backend engineer, ex-[company]"]

---

## Follow-up tracker

| Date | Action | Result |
|------|--------|--------|
| (fill on send) | sent to integrations@ashbyhq.com | — |
| +1 week | polite follow-up if no reply | — |
| +2–4 weeks | provisioning / sandbox access | — |

## After access lands (the integration plan)

1. New adapter `jobsearch/sources/ashby_feed.py` on the feed schema
   (registered like the other Layer-1 sources; the existing
   `ashby.py` HTML-board adapter stays for non-opted-in tenants).
2. The feed becomes the highest rung of the dedup source-ladder
   (authoritative source wins merge identity — `dedup.py::_source_precedes`).
3. `ats_platform` tagging (already live via `ats_resolve.detect_platform`)
   keys the ghost-job §708 heuristic: feed sightings count as own-ATS
   evidence, which is exactly the signal it needs.
