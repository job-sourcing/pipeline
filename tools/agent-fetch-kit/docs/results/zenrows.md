# Zenrows v1 evaluation

_captured: 01:29:45 UTC_

## 1. Egress IP + credit baseline (ipinfo.io/json, plain proxy)

- HTTP 400 elapsed=919ms error=None
- request cost: `{"X-Request-Cost": "0", "X-Request-Id": "6a8e418a016b0f15e1ef1a49db85dc9c", "Zr-Final-Url": null, "Concurrency-Remaining": null}`
- egress IP: `None` city=None country=None org=None

## 2. Basic markdown (example.com)

- HTTP 200 elapsed=1052ms X-Request-Cost=0.001 error=None
- markdown head: `# Example Domain

This domain is for use in documentation examples without needing permission. Avoid use in operations.

[Learn more](https://iana.org/domains/example)`

## 3. JS rendering (quotes.toscrape.com/js/, js_render + wait)

- HTTP 400 elapsed=948ms X-Request-Cost=0 error=None
- markdown length=295 quote-markers=0
- markdown head: `{"code":"REQS001","detail":"Requests to this URL are forbidden. Contact support if this may be a problem, or try again with a different target URL.","instance":"/v1","status":400,"title":"Requests to this domain are forbidden (REQS001)","type":"https://docs.zenrows.com/api-error-codes#REQS001"}`

## 4. Cloudflare challenge — mode=auto (Adaptive Stealth) + premium proxy (US)

| target | http_status | request_cost | title/markers | elapsed_ms | error |
|---|---|---|---|---|---|
| https://www.scrapingcourse.com/cloudflare-challenge | 200 | 0.025 | title=`Cloudflare Challenge` markers=cloudflare | 11915 |  |
  - final_url=https://www.scrapingcourse.com/cloudflare-challenge
| https://nowsecure.nl | 200 | 0.025 | title=`` markers= | 4009 |  |
  - final_url=https://nowsecure.nl/
| https://www.reddit.com/r/technology.json | 200 | 0.025 | title=`` markers=reddit | 15215 |  |
  - final_url=https://www.reddit.com/r/technology.json
