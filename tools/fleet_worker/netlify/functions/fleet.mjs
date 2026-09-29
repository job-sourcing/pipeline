export default async (req) => {
  const started = Date.now()
  const cors = {
    'access-control-allow-origin': '*',
    'content-type': 'application/json'
  }
  if (req.method === 'OPTIONS') return new Response('', { status: 204, headers: cors })
  if (req.method === 'GET') {
    return Response.json({ ok: true, worker: 'js-fleet v1', ts: started }, { headers: cors })
  }
  let job = {}
  try { job = await req.json() } catch { return Response.json({ err: 'bad json' }, { status: 400, headers: cors }) }
  const out = { ok: true, ts: started, took_ms: 0 }
  try {
    if (job.op === 'fetch') {
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), (job.timeout_s || 10) * 1000)
      const r = await fetch(job.url, {
        signal: ctl.signal,
        method: job.method || 'GET',
        headers: job.headers || { 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', accept: 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8', 'accept-language': 'en-US,en;q=0.9' },
        redirect: 'follow'
      })
      clearTimeout(t)
      const ab = new ArrayBuffer(await r.arrayBuffer())
      const bytes = ab.byteLength
      let text = ''
      try { text = new TextDecoder().decode(ab).slice(0, (job.max_bytes || 120000)) } catch { }
      out.status = r.status
      out.final_url = r.url
      out.bytes = bytes
      out.body = text
    } else if (job.op === 'search') {
      // DDG html SERP scrape, run from this function's own US IP
      const q = encodeURIComponent(job.query || '')
      const ctl = new AbortController()
      const t = setTimeout(() => ctl.abort(), 12000)
      const r = await fetch('https://html.duckduckgo.com/html/?q=' + q, {
        signal: ctl.signal,
        headers: { 'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' }
      })
      clearTimeout(t)
      const html = await r.text()
      out.status = r.status
      const results = []
      const seen = new Set()
      for (const m of html.matchAll(/<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)<\/a>/gs)) {
        let u = m[1].startsWith('//') ? 'https:' + m[1] : m[1]
        const um = u.match(/[?&]uddg=([^&]+)/)
        if (um) { try { u = decodeURIComponent(um[1]) } catch { } }
        const name = m[2].replace(/<[^>]+>/g, '').trim()
        if (!/^https?:\/\//.test(u) || seen.has(u)) continue
        seen.add(u)
        results.push({ name: name.slice(0, 140), url: u })
        if (results.length >= (job.num || 8)) break
      }
      out.results = results
    } else if (job.op === 'ip') {
      const r = await fetch('https://api.ipify.org')
      out.ip = await r.text()
    } else {
      return Response.json({ err: 'unknown op ' + job.op }, { status: 400, headers: cors })
    }
  } catch (e) {
    out.ok = false
    out.err = String(e).slice(0, 200)
  }
  out.took_ms = Date.now() - started
  return Response.json(out, { headers: cors })
}
export const config = { path: '/fleet' }
