// score_jobs.mjs — LLM job scoring (Week 1, step 6).
//
// Payload (stdin or --payload file):
//   { resume, prompt_template, prompt_version,
//     jobs: [{ id, title, company, description }] }
//
// Output envelope data:
//   { results: [ { id, ok: true, dims: {technical, experience, behavioral,
//                 career}, reasoning, top_matches, gaps }
//              | { id, ok: false, error, raw } ],
//     attempts: [{ id, attempts }] }
//
// Per-item isolation (D9): one job failing never kills the batch.
// Salvage parse + one in-script retry with a stricter instruction (D1.2);
// Python quarantines persistent failures. Sentinel scores are never emitted.
import { createClient, main } from "./lib/runner.mjs";
import { salvageParseJson } from "./lib/envelope.mjs";

const STRICT_RETRY_SUFFIX =
  "\n\nIMPORTANT: Your previous reply was not valid JSON. Reply with ONLY the raw JSON object — no prose, no markdown fences, no code blocks.";

function fillTemplate(template, vars) {
  return template.replace(/\{(\w+)\}/g, (m, key) =>
    key in vars ? String(vars[key]) : m);
}

function validateScore(obj) {
  if (typeof obj !== "object" || obj === null) return "not_an_object";
  const dims = ["technical", "experience", "behavioral", "career"];
  for (const d of dims) {
    const v = Number(obj[d]);
    if (!Number.isFinite(v) || v < 0 || v > 100) return `bad_dim_${d}`;
  }
  if (typeof obj.reasoning !== "string") return "bad_reasoning";
  // "sector" (prompt v3) is OPTIONAL on the wire: a missing/unknown sector
  // degrades to undefined — the scored dimensions stay valid, and the
  // Python layer treats a missing sector as "not inferred yet".
  return null; // valid
}

// Fixed taxonomy from the prompt — a sector outside it is not an error, it
// just isn't persisted (defends downstream filters from free-text drift).
const SECTOR_TAXONOMY = new Set([
  "software", "fintech", "healthtech", "ecommerce", "ai-ml", "devtools",
  "cybersecurity", "data-infra", "gaming", "education", "enterprise-saas",
  "consumer", "industrial", "climate-energy", "government", "consulting",
  "media", "logistics", "other",
]);

async function scoreOne(zai, payload, job, attempt) {
  const prompt = fillTemplate(payload.prompt_template, {
    resume: (payload.resume || "").slice(0, 4000),
    title: job.title,
    company: job.company,
    description: (job.description || "").slice(0, 3000),
  }) + (attempt > 0 ? STRICT_RETRY_SUFFIX : "");

  const completion = await zai.chat.completions.create({
    messages: [
      { role: "system", content: "You are an expert technical recruiter. Output only valid JSON." },
      { role: "user", content: prompt },
    ],
    thinking: { type: "disabled" },
  });
  const text = completion.choices?.[0]?.message?.content || "";
  const parsed = salvageParseJson(text);
  if (parsed.error) return { parseError: parsed.error, raw: text };
  const invalid = validateScore(parsed.value);
  if (invalid) return { parseError: invalid, raw: text };
  const v = parsed.value;
  const sector = typeof v.sector === "string"
    ? v.sector.trim().toLowerCase() : "";
  return {
    ok: true,
    dims: {
      technical: Number(v.technical),
      experience: Number(v.experience),
      behavioral: Number(v.behavioral),
      career: Number(v.career),
    },
    reasoning: v.reasoning,
    top_matches: Array.isArray(v.top_matches) ? v.top_matches.slice(0, 8) : [],
    gaps: Array.isArray(v.gaps) ? v.gaps.slice(0, 8) : [],
    ...(SECTOR_TAXONOMY.has(sector) ? { sector } : {}),
  };
}

// Transient/environmental SDK failures (429 rate limits, timeouts, resets)
// are NOT job-specific: propagate them so runner.mjs emits an llm_retryable
// envelope and the Python seam retries the batch with backoff (D8).
// Genuinely per-item errors (bad JSON, invalid dims) stay isolated below.
const RETRYABLE_RE = /429|rate|timeout|ECONNRESET|ETIMEDOUT|temporar/i;

main(async (payload) => {
  const zai = await createClient();
  const results = [];
  const attempts = [];
  for (const job of payload.jobs || []) {
    let out = null;
    let last = null;
    let used = 0;
    for (let attempt = 0; attempt <= 1; attempt++) {
      used = attempt + 1;
      try {
        out = await scoreOne(zai, payload, job, attempt);
      } catch (e) {
        const msg = String(e?.message ?? e);
        if (RETRYABLE_RE.test(msg)) throw e;
        out = { parseError: `sdk_error: ${msg}`, raw: "" };
      }
      if (out && out.ok) break;
      last = out;
    }
    attempts.push({ id: job.id, attempts: used });
    if (out && out.ok) {
      results.push({ id: job.id, ...out });
    } else {
      results.push({ id: job.id, ok: false, error: last?.parseError ?? "unknown", raw: (last?.raw ?? "").slice(0, 2000) });
    }
  }
  return { results, attempts };
});
