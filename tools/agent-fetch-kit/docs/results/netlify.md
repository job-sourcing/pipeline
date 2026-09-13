# Netlify edge scraper evaluation

_captured: 01:17:02 UTC_

## 0. Base URL + response shape

- base `${NETLIFY_SCRAPER_URL}` HTTP 200 elapsed=2385ms

- top-level keys: `['batch_id', 'status', 'processed', 'succeeded', 'failed', 'skipped', 'elapsed_ms', 'results']`

- result0 keys: `['index', 'ok', 'status', 'url', 'engine', 'method', 'size', 'content_type', 'elapsed_ms', 'blob_key', 'inline_body', 'inline_body_truncated', 'tls', 'redirected', 'final_url', 'puppeteer', 'error']`

- result0: status=200 engine=fetch size=559 content_type=text/html elapsed=65ms

## 1. fetch engine — tls.peet.ws/api/all (inline)

- HTTP 200 batch_elapsed=297ms; result0.status=200 size=7597

- result0.tls (Netlify fetch's own TLS): `null`

- target-seen JA3: `1808993db60a053eb8ce0eb1c51750d6`  JA4: `t13d5212h1_b262b3658495_8e6e362c5eac`

- target-seen akamai h2 fp: `None`

- target-seen IP: `3.143.247.159:56296`  UA: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36`

## 2. fetch engine — ipinfo.io/json (egress IP)

- egress IP: `3.143.247.159` city=Columbus country=US org=AS16509 Amazon.com, Inc.

## 3. chrome_impersonate engine (queue mode) — tls.peet.ws

- submit HTTP 202 elapsed=891ms batch_id=batch-1787707027714-kyya1o result0.error=None

- poll: HTTP 200 result0.ok=None status=None error=None tls=null

- result body[:200]=``

## 4. puppeteer engine (queue mode) — JS render + WAF

| target | submit_status | result_status | size | markers | elapsed_ms |

|---|---|---|---|---|---|

| https://quotes.toscrape.com/js/ | 202 | None | None | quote-blocks=0 (JS FAIL) | None |

| https://nowsecure.nl | 202 | None | None | title=`` markers= | None |

| https://bot.sannysoft.com/ | 202 | None | None | title=`` markers= | None |

## 5. Batch + blob mode end-to-end

- submit (3 jobs, blob) HTTP 200 elapsed=1063ms batch_id=batch-1787707181877-9ad2h2

- batch status: succeeded=3 failed=0 elapsed=360ms

- result[2] (ipinfo) blob retrieval: IP=3.143.247.159 city=Columbus org=AS16509 Amazon.com, Inc.

## 6. Summary

- `fetch` engine: inline sync ✓ — fast (~70-130ms/URL), AWS us-east-2 egress

- `chrome_impersonate`: requires `queue=true`; verify JA3 vs Chrome baseline above

- `puppeteer`: requires `queue=true`; provides JS rendering + headless Chrome on Netlify edge

- Geo: locked to Netlify region (us-east-2 observed) — NO region-pin like Supabase

- Batch + blob storage: ✓ — 50 sync / 500 queue job limits, blob API retrieval works

- Inline response includes `tls` field (the edge function's own TLS fingerprint)


## 7. Queue results (polled later)

See `docs/results/netlify/queue-results.md` for full table.

## 7. Queue limitation (important)

All 4 queue jobs (chrome_impersonate + 3× puppeteer) submitted at 01:20 remained
`"status":"pending"` after 12+ minutes (verified 01:33). The result blobs return 404
(not yet created). The Netlify queue worker is **not processing jobs** on this deploy.

**Implication:** only the `fetch` engine (inline sync, or blob sync without `queue=true`)
is usable on this deploy. The `chrome_impersonate` and `puppeteer` engines — which
the API requires `queue=true` for — are effectively unusable (jobs submit but never
complete). Treat Netlify as a **batch fetch-only edge** with AWS us-east-2 egress.
