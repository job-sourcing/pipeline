// S20-T2 second batch — curated lists, Chinese-language sources, verticals
import ZAI from 'z-ai-web-dev-sdk'
import { writeFileSync } from 'node:fs'

const QUERIES = [
  { id: 'zh_list', q: '中资企业在美国 名单 科技公司 设立分支机构' },
  { id: 'zh_list2', q: '出海北美的中国公司 招聘 美国本地员工 名单' },
  { id: 'cgcc_members', q: 'cgccusa.org member directory Chinese companies United States' },
  { id: 'report_pdf', q: 'CGCC annual business survey report Chinese enterprises United States PDF 2024 2025' },
  { id: 'tech_list', q: 'Chinese tech companies Silicon Valley offices list ByteDance Alibaba Tencent Baidu' },
  { id: 'ai_list', q: 'Chinese AI companies US offices labs hiring 2025' },
  { id: 'biotech', q: 'Chinese biotech pharmaceutical companies US operations hiring WuXi GenScript' },
  { id: 'ev2', q: 'Chinese EV battery companies US plants hiring Gotion CATL BYD' },
  { id: 'consumer', q: 'Chinese consumer brands US market Anker EcoFlow Roborock Dreame Insta360 US offices' },
  { id: 'gaming', q: 'Chinese gaming companies US studios miHoYo Tencent NetEase Perfect World' },
  { id: 'logistics', q: 'Chinese logistics shipping companies US offices COSCO SF Express' },
  { id: 'banking', q: 'Chinese banks United States branches ICBC Bank of China CMB full list' },
  { id: 'tiktok_ecom', q: 'TikTok Shop Temu Shein US hiring e-commerce operations' },
  { id: 'hardware', q: 'Chinese hardware robotics companies US hiring Unitree Dreewy UnitX' },
]

const zai = await ZAI.create()
const out = {}
for (const { id, q } of QUERIES) {
  try {
    const r = await zai.functions.invoke('web_search', { query: q, num: 8 })
    out[id] = (r || []).slice(0, 8).map(x => ({ t: x.name?.slice(0, 110), u: x.url, s: x.snippet?.slice(0, 200) }))
    console.log(`OK ${id}: ${out[id].length}`)
  } catch (e) {
    out[id] = { error: String(e).slice(0, 160) }
    console.log(`ERR ${id}`)
  }
}
writeFileSync('/tmp/s20_search2.json', JSON.stringify(out, null, 1))
console.log('saved')
