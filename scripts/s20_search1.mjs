// S20-T2 channel research — batched web searches for the expansion round
import ZAI from 'z-ai-web-dev-sdk'

const QUERIES = [
  // gov-data channels
  { id: 'dol_quarters', q: 'DOL LCA disclosure data FY2025 quarterly xlsx download foreign labor performance' },
  { id: 'lca_stats', q: 'H-1B LCA disclosure data number of distinct employers per year statistics' },
  { id: 'myvisajobs', q: 'MyVisaJobs employer search Chinese companies H1B sponsors list' },
  { id: 'h1bgrader', q: 'H1BGrader employer database search H1B sponsors company list' },
  // directory channels
  { id: 'cgcc', q: 'China General Chamber of Commerce USA member companies directory list' },
  { id: 'cgcc_report', q: 'CGCC USA annual report Chinese companies in the United States report' },
  { id: 'lists', q: 'list of Chinese companies with US offices operations hiring' },
  { id: 'wiki', q: 'Wikipedia list of Chinese companies category companies of China' },
  // ecommerce/DTC + news
  { id: 'dtc', q: 'Chinese DTC consumer brands United States Anker Temu Shein Roborock list' },
  { id: 'news', q: 'Chinese tech companies expanding US hiring offices 2025 2026' },
  { id: 'ecommerce_us', q: 'Chinese e-commerce companies US warehouses operations jobs' },
  { id: 'ev_auto', q: 'Chinese automakers US factories jobs BYD Chery GWM NIO Zeekr' },
]

const zai = await ZAI.create()
const out = {}
for (const { id, q } of QUERIES) {
  try {
    const r = await zai.functions.invoke('web_search', { query: q, num: 8 })
    out[id] = (r || []).slice(0, 8).map(x => ({ t: x.name?.slice(0, 110), u: x.url, s: x.snippet?.slice(0, 220) }))
    console.log(`OK ${id}: ${out[id].length} results`)
  } catch (e) {
    out[id] = { error: String(e).slice(0, 200) }
    console.log(`ERR ${id}: ${e}`)
  }
}
import { writeFileSync } from 'node:fs'; writeFileSync('/tmp/s20_search1.json', JSON.stringify(out, null, 1))
console.log('saved /tmp/s20_search1.json')
