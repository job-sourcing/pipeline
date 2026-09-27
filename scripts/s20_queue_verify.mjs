// S20-D queue verification — LLM batch-classify the candidate companies.
// Reads queue_candidates.jsonl, appends verdicts to queue_verdicts.jsonl
// (resumable: skips names already verdicted).
import ZAI from '../ingest/vendor/z-ai-web-dev-sdk/dist/index.js'
import { readFileSync, writeFileSync, existsSync, appendFileSync } from 'node:fs'

const DIR = '/home/z/job-sourcing-research/ingest/data/ats_seed/s20_census'
const CAND = `${DIR}/queue_candidates.jsonl`
const VER = `${DIR}/queue_verdicts.jsonl`

const SYSTEM = `You are a corporate-origin verification analyst. For each candidate company decide:

1. chinese_origin (bool): the company is founded in / originates from mainland China or Hong Kong, OR it is a notable global brand majority-owned by a Chinese group (e.g. Motorola Mobility under Lenovo, GE Appliances under Haier). It is FALSE for: foreign MNCs' China arms (Google China, Citibank China), non-Chinese-origin companies (Singapore/Brazil/US/UK...), and generic token collisions.

2. lca_valid (bool): the listed LCA employer is plausibly THIS company's own US entity — the brand token matches AND the sector is plausible (e.g. "GOTION ILLINOIS NEW ENERGY INC" for Gotion = valid; "JD LOGISTICS UNITED STATES" for JD.com = valid; but "PING IDENTITY CORPORATION" for Ping An = INVALID collision; "jdsports"-style collisions invalid). If unsure, false.

3. ats_valid (bool): the listed ATS board belongs to THIS company (same brand AND plausible sector), not a homonym board (JD.com vs JD Sports board = false; Shein's own greenhouse board = true). If no ATS board listed, false.

4. brand: the canonical modern brand name for job-search purposes (e.g. "TikTok" not "ByteDance Ltd."; "Temu" not "Whaleco").
5. sector: one of tech_ai, ecommerce, ev_auto, battery, biotech, pharma, gaming, consumer, hardware, robotics, logistics, finance, telecom, consulting_staffing, industrial, holding, media, travel, education, aviation, energy, real_estate, other.
6. note: one short clause of reasoning.

Reply with ONLY a JSON array, one object per input company, fields:
{name, chinese_origin, confidence (high|med|low), lca_valid, ats_valid, brand, sector, note}
No markdown fences.`

function parseVerdicts (text) {
  let t = text.trim()
  t = t.replace(/^```(json)?/i, '').replace(/```$/, '').trim()
  const cut = t.indexOf('[')
  if (cut > 0) t = t.slice(cut)
  return JSON.parse(t)
}

async function main () {
  const zai = await ZAI.create()
  const cands = readFileSync(CAND, 'utf8').split('\n').filter(Boolean).map(JSON.parse)
  const done = new Set()
  if (existsSync(VER)) {
    for (const l of readFileSync(VER, 'utf8').split('\n').filter(Boolean)) {
      try { done.add(JSON.parse(l).name) } catch {}
    }
  }
  const todo = cands.filter(c => !done.has(c.name))
  console.log(`cands=${cands.length} done=${done.size} todo=${todo.length}`)

  const BATCH = 10  // review #14: smaller batches — a truncation throw loses less, 429-wasteful
  for (let i = 0; i < todo.length; i += BATCH) {
    const batch = todo.slice(i, i + BATCH)
    const payload = batch.map(c => ({
      name: c.name,
      wiki_category: c.category || null,
      family: c.family.slice(0, 4),
      lca_employers: (c.lca_employers || []).slice(0, 3),
      lca_filings: c.lca?.filings || 0,
      ats_boards: (c.ats || []).slice(0, 2).map(a => `${a.platform}:${a.slug} (${a.company}, ${a.job_count} jobs)`)
    }))
    try {
      const r = await zai.chat.completions.create({
        messages: [
          { role: 'system', content: SYSTEM },
          { role: 'user', content: JSON.stringify(payload, null, 1) }
        ],
        temperature: 0,
        max_tokens: 4000
      })
      const verdicts = parseVerdicts(r.choices[0].message.content)
      const names = new Set(batch.map(c => c.name))
      let n = 0
      for (const v of verdicts) {
        if (!v.name || !names.has(v.name)) continue  // membership: LLM echoes outside the batch are dropped
        appendFileSync(VER, JSON.stringify(v) + '\n')
        n++
      }
      console.log(`batch ${i / BATCH + 1}: ${n}/${batch.length} verdicts appended`)
    } catch (e) {
      const is429 = String(e).includes('429')
      console.error(`batch ${i / BATCH + 1} FAILED${is429 ? ' (429 rate limit)' : ''}: ${String(e).slice(0, 160)}`)
      if (is429) {
        console.log('cooling down 90s...')
        await new Promise(r => setTimeout(r, 90000))
      }
    }
    await new Promise(r => setTimeout(r, 4000)) // inter-batch throttle
  }
  console.log('done ->', VER)
}

main().catch(e => { console.error(String(e)); process.exit(1) })
